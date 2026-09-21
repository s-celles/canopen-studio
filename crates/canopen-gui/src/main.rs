use canopen_core::canopen::NmtState;
use canopen_core::frame::CanFrame;
use canopen_core::latency::{
    build_ping_request, build_ping_response, ping_response_sequence, LatencyStats, LatencyTracker,
    PING_REQUEST_ID,
};
use canopen_core::nmt::{MonitoredNode, NmtCommand, NmtMaster};
use canopen_core::obd2::{
    build_obd2_mode01_request, decode_obd2_mode01_frame, SUPPORTED_MODE01_PIDS,
};
use canopen_core::sdo::{build_sdo_read, build_sdo_write, parse_sdo_frame, SdoMessage};
use canopen_core::simulator_ext::spawn_udp_simulator_bound;
use canopen_core::telemetry::DriveTelemetry;
use canopen_core::udp::UdpCanBus;
use slint::{ModelRc, SharedString, StandardListViewItem, VecModel, Weak};
use std::collections::{BTreeMap, BTreeSet, HashMap, VecDeque};
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

slint::include_modules!();

/// Local UDP ports for the built-in virtual bus. The two ends must differ,
/// otherwise each would receive the frames it just sent.
const UI_PORT: u16 = 1750;
const SIM_PORT: u16 = 1751;

/// SYNC is produced at 50 Hz, so 20 ms is the interval jitter is measured against.
const SYNC_COB_ID: u32 = 0x080;
const SYNC_NOMINAL_US: f64 = 20_000.0;

/// A ping still unanswered after this long is written off as lost.
const PING_TIMEOUT: Duration = Duration::from_secs(2);

/// Parse a CAN identifier written as "0x601", "601" or "  0X601  ".
fn parse_can_id(text: &str) -> Result<u32, String> {
    let t = text.trim();
    if t.is_empty() {
        return Err("CAN ID is empty".to_string());
    }
    let stripped = t
        .strip_prefix("0x")
        .or_else(|| t.strip_prefix("0X"))
        .unwrap_or(t);
    let id = u32::from_str_radix(stripped, 16).map_err(|_| format!("'{t}' is not a hex CAN ID"))?;
    if id > 0x1FFF_FFFF {
        return Err(format!("CAN ID 0x{id:X} exceeds the 29-bit range"));
    }
    Ok(id)
}

/// Parse a payload written as "40 00 10", "400010" or "40,00,10" into at most 8 bytes.
fn parse_payload(text: &str) -> Result<Vec<u8>, String> {
    let compact: String = text
        .chars()
        .filter(|c| !c.is_whitespace() && *c != ',' && *c != '-')
        .collect();
    if compact.is_empty() {
        return Ok(Vec::new());
    }
    if !compact.len().is_multiple_of(2) {
        return Err(format!(
            "payload has {} hex digits; an even count is required",
            compact.len()
        ));
    }
    if compact.len() > 16 {
        return Err(format!(
            "payload is {} bytes; classic CAN allows at most 8",
            compact.len() / 2
        ));
    }
    (0..compact.len())
        .step_by(2)
        .map(|i| {
            u8::from_str_radix(&compact[i..i + 2], 16)
                .map_err(|_| format!("'{}' is not a hex byte", &compact[i..i + 2]))
        })
        .collect()
}

/// Parse an NMT target node: 0 broadcasts to every node, 1..=127 targets one.
fn parse_node_id(text: &str) -> Result<u8, String> {
    let t = text.trim();
    if t.is_empty() {
        return Err("target node is empty".to_string());
    }
    let id: u16 = t
        .parse()
        .map_err(|_| format!("'{t}' is not a decimal node ID"))?;
    if id > 127 {
        return Err(format!("node ID {id} is outside the 0..=127 range"));
    }
    Ok(id as u8)
}

fn nmt_command_from_byte(byte: i32) -> Option<NmtCommand> {
    match byte {
        0x01 => Some(NmtCommand::StartRemoteNode),
        0x02 => Some(NmtCommand::StopRemoteNode),
        0x80 => Some(NmtCommand::EnterPreOperational),
        0x81 => Some(NmtCommand::ResetNode),
        0x82 => Some(NmtCommand::ResetCommunication),
        _ => None,
    }
}

fn epoch_us() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_micros() as u64
}

/// Map a COB-ID to the node it belongs to and the CANopen service it carries,
/// so the dashboard can list what each discovered node is actually doing.
fn classify_service(id: u32) -> Option<(u8, &'static str)> {
    let node = |base: u32| -> Option<u8> {
        let n = id - base;
        (1..=127).contains(&n).then_some(n as u8)
    };
    match id {
        0x701..=0x77F => node(0x700).map(|n| (n, "HEARTBEAT")),
        0x181..=0x1FF | 0x281..=0x2FF | 0x381..=0x3FF | 0x481..=0x4FF => {
            let base = id & 0xF80;
            node(base).map(|n| (n, "TPDO"))
        }
        0x201..=0x27F | 0x301..=0x37F | 0x401..=0x47F | 0x501..=0x57F => {
            let base = id & 0xF80;
            node(base).map(|n| (n, "RPDO"))
        }
        0x581..=0x5FF => node(0x580).map(|n| (n, "SDO")),
        0x601..=0x67F => node(0x600).map(|n| (n, "SDO")),
        0x081..=0x0FF => node(0x080).map(|n| (n, "EMCY")),
        _ => None,
    }
}

/// One row of the "Discovered CANopen Network Nodes" table, as plain strings.
/// Kept `Send` so the RX thread can hand it to the UI thread; the Slint model
/// itself is `Rc`-based and must be built inside the event loop.
type NodeRow = [String; 4];

fn build_node_rows(
    nodes: &[MonitoredNode],
    services: &HashMap<u8, BTreeSet<&'static str>>,
    now_us: u64,
) -> Vec<NodeRow> {
    let mut sorted: Vec<&MonitoredNode> = nodes.iter().collect();
    sorted.sort_by_key(|n| n.node_id);

    sorted
        .iter()
        .map(|n| {
            [
                format!("Node {}", n.node_id),
                nmt_state_label(n.state),
                format_age(now_us, n.last_seen_us, n.is_timed_out),
                services
                    .get(&n.node_id)
                    .map(|s| s.iter().copied().collect::<Vec<_>>().join(", "))
                    .unwrap_or_default(),
            ]
        })
        .collect()
}

/// Convert the rows into the nested Slint model the table expects.
/// Must run on the UI thread: `ModelRc` is not `Send`.
fn node_rows_to_model(rows: Vec<NodeRow>) -> ModelRc<ModelRc<StandardListViewItem>> {
    let models: Vec<ModelRc<StandardListViewItem>> = rows
        .into_iter()
        .map(|row| {
            let cells: Vec<StandardListViewItem> = row
                .iter()
                .map(|t| StandardListViewItem::from(SharedString::from(t.as_str())))
                .collect();
            ModelRc::from(Rc::new(VecModel::from(cells)))
        })
        .collect();
    ModelRc::from(Rc::new(VecModel::from(models)))
}

fn nmt_state_label(state: NmtState) -> String {
    match state {
        NmtState::Bootup => "Boot-up",
        NmtState::Stopped => "Stopped",
        NmtState::Operational => "Operational",
        NmtState::PreOperational => "Pre-Operational",
        NmtState::Unknown => "Unknown",
    }
    .to_string()
}

/// Render the time since the node was last heard from, as the Python GUI does.
fn format_age(now_us: u64, last_seen_us: u64, timed_out: bool) -> String {
    if timed_out {
        return "timed out".to_string();
    }
    let age_s = now_us.saturating_sub(last_seen_us) as f64 / 1_000_000.0;
    format!("{age_s:.1}s ago")
}

/// One row of the SDO explorer log: object, outcome, decoded value, raw bytes.
type SdoRow = [String; 4];

/// Shared between the UI callbacks (which clear it) and the RX thread (which
/// appends responses as they arrive).
type SdoLog = Arc<Mutex<VecDeque<SdoRow>>>;

const SDO_LOG_CAPACITY: usize = 100;

/// Parse an object index written as "0x1000", "1000" or "  0X606c  ".
fn parse_index(text: &str) -> Result<u16, String> {
    let t = text.trim();
    if t.is_empty() {
        return Err("index is empty".to_string());
    }
    let stripped = t
        .strip_prefix("0x")
        .or_else(|| t.strip_prefix("0X"))
        .unwrap_or(t);
    u16::from_str_radix(stripped, 16).map_err(|_| format!("'{t}' is not a 16-bit hex index"))
}

/// Parse a sub-index, accepting decimal or an explicit 0x form.
fn parse_subindex(text: &str) -> Result<u8, String> {
    let t = text.trim();
    if t.is_empty() {
        return Err("sub-index is empty".to_string());
    }
    let parsed = match t.strip_prefix("0x").or_else(|| t.strip_prefix("0X")) {
        Some(hex) => u16::from_str_radix(hex, 16),
        None => t.parse::<u16>(),
    };
    match parsed {
        Ok(v) if v <= 255 => Ok(v as u8),
        Ok(v) => Err(format!("sub-index {v} does not fit in one byte")),
        Err(_) => Err(format!("'{t}' is not a sub-index")),
    }
}

/// An SDO node ID addresses a server, so 0 (broadcast) is not valid here.
fn parse_sdo_node_id(text: &str) -> Result<u8, String> {
    let id = parse_node_id(text)?;
    if id == 0 {
        return Err("node 0 is the broadcast address; SDO needs 1..=127".to_string());
    }
    Ok(id)
}

/// The width and signedness an SDO download writes, taken from the label the
/// type chooser shows. Matching on the leading token keeps the mapping
/// readable and survives the parenthetical being reworded.
struct SdoWriteType {
    width: usize,
    signed: bool,
}

fn sdo_write_type(label: &str) -> Result<SdoWriteType, String> {
    let token = label.split_whitespace().next().unwrap_or("");
    let (width, signed) = match token {
        "uint8" => (1, false),
        "uint16" => (2, false),
        "uint32" => (4, false),
        "int16" => (2, true),
        "int32" => (4, true),
        _ => return Err(format!("'{label}' is not a known data type")),
    };
    Ok(SdoWriteType { width, signed })
}

/// Encode the value an engineer typed into the little-endian bytes of an
/// expedited download.
///
/// Decimal by default, hex behind an explicit `0x`, as the sub-index field
/// reads them: an object value is as often 1500 as it is 0x1F, and silently
/// reading a bare `10` as sixteen would write the wrong number.
fn encode_sdo_value(text: &str, ty: &SdoWriteType) -> Result<Vec<u8>, String> {
    let t = text.trim();
    if t.is_empty() {
        return Err("value is empty".to_string());
    }
    let (negative, digits) = match t.strip_prefix('-') {
        Some(rest) => (true, rest.trim_start()),
        None => (false, t),
    };
    let magnitude = match digits
        .strip_prefix("0x")
        .or_else(|| digits.strip_prefix("0X"))
    {
        Some(hex) => i64::from_str_radix(hex, 16),
        None => digits.parse::<i64>(),
    }
    .map_err(|_| format!("'{t}' is not a number"))?;
    let value = if negative { -magnitude } else { magnitude };

    let bits = ty.width * 8;
    let (low, high) = if ty.signed {
        (-(1_i64 << (bits - 1)), (1_i64 << (bits - 1)) - 1)
    } else {
        (0, (1_i64 << bits) - 1)
    };
    if value < low || value > high {
        return Err(format!(
            "{value} does not fit in {} bytes ({low}..={high})",
            ty.width
        ));
    }
    Ok(value.to_le_bytes()[..ty.width].to_vec())
}

fn hex_bytes(data: &[u8]) -> String {
    data.iter()
        .map(|b| format!("{b:02X}"))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Render an expedited SDO payload the several ways an engineer wants to see it:
/// as an unsigned word, as a signed word, and as text when it looks like text.
fn describe_sdo_value(data: &[u8]) -> String {
    if data.is_empty() {
        return "(no data)".to_string();
    }
    let mut padded = [0u8; 4];
    padded[..data.len().min(4)].copy_from_slice(&data[..data.len().min(4)]);
    let unsigned = u32::from_le_bytes(padded);
    let signed = i32::from_le_bytes(padded);

    let mut parts = vec![format!("0x{unsigned:0width$X}", width = data.len() * 2)];
    parts.push(format!("u={unsigned}"));
    // Only worth showing when the two readings differ, i.e. the top bit is set.
    if signed < 0 {
        parts.push(format!("i={signed}"));
    }
    if data.iter().all(|b| (0x20..0x7F).contains(b)) {
        parts.push(format!("\"{}\"", String::from_utf8_lossy(data)));
    }
    parts.join("  ")
}

/// Turn an SDO response into an explorer row, or None if the frame is a
/// request rather than a server answer.
fn sdo_response_row(msg: &SdoMessage) -> Option<SdoRow> {
    match msg {
        SdoMessage::ExpeditedUploadResponse {
            node_id,
            index,
            subindex,
            data,
        } => Some([
            format!("{index:#06X}:{subindex:02X}"),
            format!("OK (node {node_id})"),
            describe_sdo_value(data),
            hex_bytes(data),
        ]),
        SdoMessage::InitiateUploadResponse {
            node_id,
            index,
            subindex,
            size,
        } => Some([
            format!("{index:#06X}:{subindex:02X}"),
            format!("Segmented (node {node_id})"),
            match size {
                Some(n) => format!("{n} bytes to follow"),
                None => "size unknown".to_string(),
            },
            String::new(),
        ]),
        SdoMessage::DownloadResponse {
            node_id,
            index,
            subindex,
        } => Some([
            format!("{index:#06X}:{subindex:02X}"),
            format!("Written (node {node_id})"),
            // The server confirms the write without echoing the value, so the
            // row says what happened rather than inventing one.
            "download accepted".to_string(),
            String::new(),
        ]),
        SdoMessage::Abort {
            node_id,
            index,
            subindex,
            code,
        } => Some([
            format!("{index:#06X}:{subindex:02X}"),
            format!("Abort (node {node_id})"),
            code.description().to_string(),
            format!("{:#010X}", code.code()),
        ]),
        _ => None,
    }
}

/// Shared state for the Hardware Diagnostics tab: in-flight pings, counters,
/// the round-trip tracker and the auto-echo switch the RX thread reads.
struct PingState {
    pending: Mutex<HashMap<u32, Instant>>,
    next_seq: AtomicU32,
    sent: AtomicU32,
    answered: AtomicU32,
    auto_echo: AtomicBool,
    rtt: LatencyTracker,
    /// The aggregate stats carry no "most recent" sample, so keep it here.
    last_rtt_us: Mutex<Option<f64>>,
}

impl PingState {
    fn new() -> Self {
        Self {
            pending: Mutex::new(HashMap::new()),
            next_seq: AtomicU32::new(1),
            sent: AtomicU32::new(0),
            answered: AtomicU32::new(0),
            auto_echo: AtomicBool::new(false),
            // RTT has no nominal interval: every sample is its own measurement.
            rtt: LatencyTracker::new(None),
            last_rtt_us: Mutex::new(None),
        }
    }

    fn reset(&self) {
        if let Ok(mut pending) = self.pending.lock() {
            pending.clear();
        }
        self.sent.store(0, Ordering::Relaxed);
        self.answered.store(0, Ordering::Relaxed);
        self.rtt.reset();
        if let Ok(mut last) = self.last_rtt_us.lock() {
            *last = None;
        }
    }

    /// Forget pings that have been outstanding too long, so the map cannot grow
    /// without bound on a bus where nothing answers.
    fn drop_stale(&self, older_than: Duration) {
        if let Ok(mut pending) = self.pending.lock() {
            pending.retain(|_, sent_at| sent_at.elapsed() < older_than);
        }
    }

    /// Match a ping response against an outstanding request and record its RTT.
    fn record_response(&self, seq: u32) -> Option<f64> {
        let sent_at = self.pending.lock().ok()?.remove(&seq)?;
        let rtt_us = sent_at.elapsed().as_secs_f64() * 1_000_000.0;
        self.rtt.record_sample_us(rtt_us);
        self.answered.fetch_add(1, Ordering::Relaxed);
        if let Ok(mut last) = self.last_rtt_us.lock() {
            *last = Some(rtt_us);
        }
        Some(rtt_us)
    }

    fn loss_text(&self) -> String {
        let sent = self.sent.load(Ordering::Relaxed);
        let answered = self.answered.load(Ordering::Relaxed);
        if sent == 0 {
            return "Sent 0, answered 0".to_string();
        }
        let loss = 100.0 * (sent.saturating_sub(answered)) as f64 / sent as f64;
        format!("Sent {sent}, answered {answered}  |  loss {loss:.1}%")
    }
}

/// Render round-trip statistics in milliseconds.
fn format_rtt(stats: &LatencyStats, last_us: Option<f64>) -> String {
    if stats.count == 0 {
        return "No ping answered yet.".to_string();
    }
    let last = match last_us {
        Some(us) => format!("{:.3}", us / 1000.0),
        None => "--".to_string(),
    };
    format!(
        "RTT last {last} ms  |  avg {:.3}  min {:.3}  max {:.3}  |  std dev {:.3} ms  ({} samples)",
        stats.avg_us / 1000.0,
        stats.min_us / 1000.0,
        stats.max_us / 1000.0,
        stats.std_dev_us / 1000.0,
        stats.count
    )
}

/// Render periodic-frame statistics, naming the deviation from nominal.
fn format_sync(stats: &LatencyStats) -> String {
    if stats.count == 0 {
        return "No SYNC frame seen yet.".to_string();
    }
    let nominal = match stats.nominal_us {
        Some(n) => format!("nominal {:.1} ms", n / 1000.0),
        None => "no nominal".to_string(),
    };
    format!(
        "Interval avg {:.3} ms ({nominal})  |  min {:.3}  max {:.3}  |  jitter ±{:.3} ms  std dev {:.3} ms  ({} intervals)",
        stats.avg_us / 1000.0,
        stats.min_us / 1000.0,
        stats.max_us / 1000.0,
        stats.jitter_us / 1000.0,
        stats.std_dev_us / 1000.0,
        stats.count
    )
}

/// One row of the OBD-II table: PID, parameter name, value, unit.
type ObdRow = [String; 4];

/// Latest reading per PID, ordered by PID so the table does not jump around.
type ObdReadings = Arc<Mutex<BTreeMap<u8, ObdRow>>>;

/// Format a decoded reading for the table. Values are shown to one decimal,
/// except RPM and raw counts where a fraction would be noise.
fn obd_row(reading: &canopen_core::obd2::ObdPidReading) -> ObdRow {
    let value = if reading.value.fract().abs() < 0.05 {
        format!("{:.0}", reading.value)
    } else {
        format!("{:.1}", reading.value)
    };
    [
        format!("{:02X}", reading.pid),
        reading.name.clone(),
        value,
        reading.unit.clone(),
    ]
}

fn obd_rows_to_model(rows: Vec<ObdRow>) -> ModelRc<ModelRc<StandardListViewItem>> {
    let models: Vec<ModelRc<StandardListViewItem>> = rows
        .into_iter()
        .map(|row| {
            let cells: Vec<StandardListViewItem> = row
                .iter()
                .map(|t| StandardListViewItem::from(SharedString::from(t.as_str())))
                .collect();
            ModelRc::from(Rc::new(VecModel::from(cells)))
        })
        .collect();
    ModelRc::from(Rc::new(VecModel::from(models)))
}

/// Request every Mode 01 PID the decoder understands. Returns how many
/// requests went out, or the first transmission error.
fn request_all_obd_pids(bus: &UdpCanBus, total_tx: &AtomicI32) -> Result<usize, String> {
    let mut sent = 0;
    for &pid in SUPPORTED_MODE01_PIDS {
        let frame = build_obd2_mode01_request(pid)
            .map_err(|e| format!("cannot build the request for PID {pid:#04X}: {e:?}"))?;
        bus.send(&frame)
            .map_err(|e| format!("transmission failed on PID {pid:#04X}: {e:?}"))?;
        sent += 1;
        total_tx.fetch_add(1, Ordering::Relaxed);
    }
    Ok(sent)
}

fn main() -> Result<(), slint::PlatformError> {
    let ui = MainWindow::new()?;
    let ui_handle: Weak<MainWindow> = ui.as_weak();

    // The simulator and the UI each own one end of a local UDP pair, so what
    // the Transmit tab sends actually reaches the simulated nodes and their
    // replies come back.
    spawn_udp_simulator_bound(SIM_PORT, "127.0.0.1", UI_PORT);

    // The UI listens on UI_PORT and transmits to the simulator; the RX thread
    // and the Transmit tab share one bus.
    let bus = match UdpCanBus::new(UI_PORT, "127.0.0.1", SIM_PORT, false) {
        Ok(b) => Arc::new(b),
        Err(e) => {
            eprintln!("Failed to bind UdpCanBus: {e:?}");
            return Err(slint::PlatformError::Other(format!(
                "cannot bind the UDP CAN bus on port {UI_PORT}: {e:?}"
            )));
        }
    };
    let _ = bus.set_read_timeout(Some(Duration::from_millis(5)));

    let total_tx = Arc::new(AtomicI32::new(0));
    let sdo_log: SdoLog = Arc::new(Mutex::new(VecDeque::with_capacity(SDO_LOG_CAPACITY)));
    wire_transmit_tab(&ui, Arc::clone(&bus), Arc::clone(&total_tx));
    wire_sdo_tab(
        &ui,
        Arc::clone(&bus),
        Arc::clone(&total_tx),
        Arc::clone(&sdo_log),
    );

    ui.set_app_version(env!("CARGO_PKG_VERSION").into());

    // Quick reference sheet, kept beside the source so it can be edited as text.
    ui.set_reference_text(include_str!("reference.txt").into());

    let ping_state = Arc::new(PingState::new());
    let sync_tracker = Arc::new(LatencyTracker::new(Some(SYNC_NOMINAL_US)));
    ui.set_link_info(
        format!(
            "Virtual UDP bus on 127.0.0.1 - UI listens on {UI_PORT}, simulated nodes on {SIM_PORT}.\n\
             Wire format: python-can msgpack.\n\
             Simulated traffic: SYNC 50 Hz, heartbeats 1 Hz, TPDOs 25 Hz."
        )
        .into(),
    );
    let obd_readings: ObdReadings = Arc::new(Mutex::new(BTreeMap::new()));
    let _obd_timer = wire_obd_tab(
        &ui,
        Arc::clone(&bus),
        Arc::clone(&total_tx),
        Arc::clone(&obd_readings),
    );
    let _ping_timer = wire_hardware_tab(
        &ui,
        Arc::clone(&bus),
        Arc::clone(&total_tx),
        Arc::clone(&ping_state),
        Arc::clone(&sync_tracker),
    );

    let rx_bus = Arc::clone(&bus);
    let rx_sdo_log = Arc::clone(&sdo_log);
    let rx_ping = Arc::clone(&ping_state);
    let rx_sync = Arc::clone(&sync_tracker);
    let rx_obd = Arc::clone(&obd_readings);
    thread::spawn(move || {
        let bus = rx_bus;
        let sdo_log = rx_sdo_log;
        let ping_state = rx_ping;
        let sync_tracker = rx_sync;
        let obd_readings = rx_obd;

        let mut trace_count = 0;
        let mut rpm_history: VecDeque<i32> = VecDeque::with_capacity(800);
        let mut trace_lines: VecDeque<String> = VecDeque::with_capacity(50);
        let mut telemetry = DriveTelemetry::new();
        let mut nmt = NmtMaster::new();
        let mut services: HashMap<u8, BTreeSet<&'static str>> = HashMap::new();

        let start_time = std::time::Instant::now();
        let mut rate_window_start = start_time;
        let mut rate_window_count = 0u32;
        let mut bus_rate = 0u32;

        loop {
            // A recv error is a read timeout on an idle bus; just poll again.
            if let Ok((frame, _addr)) = bus.recv() {
                trace_count += 1;
                    println!("RX: id={:x} len={}", frame.id, frame.payload().len());
                println!("RX: id={:x} len={}", frame.id, frame.payload().len());
                println!("RX: id={:x} len={}", frame.id, frame.payload().len());
                rate_window_count += 1;
                let id = frame.id;
                let data = frame.payload();

                let mut updated = telemetry.apply_frame(&frame);
                if id == 0x473 && data.len() >= 8 {
                    rpm_history.push_back(telemetry.speed_rpm);
                    if rpm_history.len() > 800 {
                        rpm_history.pop_front();
                    }
                    updated = true;
                }

                // SYNC intervals measure how steadily the bus is being driven.
                if id == SYNC_COB_ID {
                    sync_tracker.record_frame_timestamp(frame.timestamp_us);
                }

                // Answer pings when acting as a responder, and match incoming
                // answers to our own outstanding requests.
                if id == PING_REQUEST_ID && ping_state.auto_echo.load(Ordering::Relaxed) {
                    if let Some(pong) = build_ping_response(&frame) {
                        let _ = bus.send(&pong);
                    }
                }
                if let Some(seq) = ping_response_sequence(&frame) {
                    if ping_state.record_response(seq).is_some() {
                        updated = true;
                    }
                }

                // OBD-II Mode 01 responses feed the diagnostics table.
                let mut obd_rows: Option<Vec<ObdRow>> = None;
                if let Some(reading) = decode_obd2_mode01_frame(&frame) {
                    if let Ok(mut readings) = obd_readings.lock() {
                        readings.insert(reading.pid, obd_row(&reading));
                        obd_rows = Some(readings.values().cloned().collect());
                    }
                    updated = true;
                }

                // SDO server responses feed the Object Dictionary explorer.
                let mut sdo_rows: Option<Vec<SdoRow>> = None;
                let mut sdo_summary: Option<String> = None;
                if (0x581..=0x5FF).contains(&id) {
                    if let Some(row) = parse_sdo_frame(&frame).as_ref().and_then(sdo_response_row) {
                        sdo_summary = Some(format!("{} -> {}", row[1], row[2]));
                        if let Ok(mut log) = sdo_log.lock() {
                            log.push_front(row);
                            log.truncate(SDO_LOG_CAPACITY);
                            sdo_rows = Some(log.iter().cloned().collect());
                        }
                        updated = true;
                    }
                }

                // Heartbeats drive node discovery; other COB-IDs are
                // recorded as services the node is seen to use.
                if nmt.process_frame(&frame).is_some() {
                    updated = true;
                }
                if let Some((node_id, service)) = classify_service(id) {
                    if services.entry(node_id).or_default().insert(service) {
                        updated = true;
                    }
                }

                // Refresh the bus rate once per second.
                let window = rate_window_start.elapsed();
                if window.as_secs_f32() >= 1.0 {
                    bus_rate = (rate_window_count as f32 / window.as_secs_f32()) as u32;
                    rate_window_count = 0;
                    rate_window_start = std::time::Instant::now();
                    nmt.check_timeouts(epoch_us());
                    ping_state.drop_stale(PING_TIMEOUT);
                    // Refresh once per second regardless, so the bus rate
                    // and the "last seen" ages keep ticking on a quiet bus.
                    updated = true;
                }

                // Format Trace Line
                let elapsed = start_time.elapsed().as_secs_f32();
                let hex_data: Vec<String> = data.iter().map(|b| format!("{:02X}", b)).collect();
                let line = format!(
                    "{:>8.3} | ID: 0x{:03X} | DLC: {} | Data: {}",
                    elapsed,
                    id,
                    data.len(),
                    hex_data.join(" ")
                );
                trace_lines.push_front(line);
                if trace_lines.len() > 50 {
                    trace_lines.pop_back();
                }

                if updated || trace_count % 10 == 0 {
                    let snapshot = telemetry.clone();
                    let nodes = nmt.all_nodes();
                    let node_rows = build_node_rows(&nodes, &services, epoch_us());
                    let active_nodes = nodes
                        .iter()
                        .filter(|n| n.state == NmtState::Operational && !n.is_timed_out)
                        .count() as i32;
                    let rate_text = format!("{bus_rate} msgs/s");
                    let sync_text = format_sync(&sync_tracker.stats());
                    let rtt_text = format_rtt(
                        &ping_state.rtt.stats(),
                        ping_state.last_rtt_us.lock().ok().and_then(|l| *l),
                    );
                    let loss_text = ping_state.loss_text();
                    let total_trace = trace_count;
                    let trace_text = trace_lines
                        .iter()
                        .cloned()
                        .collect::<Vec<String>>()
                        .join("\n");

                    let mut path = String::with_capacity(rpm_history.len() * 15);
                    let w = 800.0;
                    let h = 300.0;
                    let max_rpm = 4000.0;

                    let len = rpm_history.len();
                    for (i, &val) in rpm_history.iter().enumerate() {
                        let points_from_right = (len - 1 - i) as f32;
                        let x = w - (points_from_right / 800.0) * w;
                        let v = if val < 0 {
                            0.0
                        } else if val as f32 > max_rpm {
                            max_rpm
                        } else {
                            val as f32
                        };
                        let y = h - ((v / max_rpm) * h);
                        if i == 0 {
                            path.push_str(&format!("M {} {} ", x, y));
                        } else {
                            path.push_str(&format!("L {} {} ", x, y));
                        }
                    }

                    let _ = ui_handle.upgrade_in_event_loop(move |ui| {
                        ui.set_rpm(snapshot.speed_rpm);
                        ui.set_max_speed_rpm(snapshot.max_speed_rpm);
                        ui.set_torque(snapshot.target_torque);
                        ui.set_temp_inv(snapshot.heatsink_temp_c);
                        ui.set_temp_motor(snapshot.motor_temp_raw);
                        ui.set_drive_state(snapshot.state_label().into());
                        ui.set_bus_rate(rate_text.into());
                        ui.set_active_nodes(active_nodes);
                        ui.set_nodes_model(node_rows_to_model(node_rows));
                        ui.set_total_rx(total_trace);
                        ui.set_trace_text(trace_text.into());
                        if let Some(rows) = sdo_rows {
                            ui.set_sdo_log_model(sdo_rows_to_model(rows));
                        }
                        if let Some(rows) = obd_rows {
                            let count = rows.len();
                            ui.set_obd_rows_model(obd_rows_to_model(rows));
                            ui.set_obd_status_is_error(false);
                            ui.set_obd_status(format!("{count} parameter(s) answered.").into());
                        }
                        ui.set_sync_stats(sync_text.into());
                        ui.set_rtt_stats(rtt_text.into());
                        ui.set_loss_stats(loss_text.into());
                        if let Some(summary) = sdo_summary {
                            ui.set_sdo_status_is_error(summary.starts_with("Abort"));
                            ui.set_sdo_status(summary.into());
                        }
                        if !path.is_empty() {
                            ui.set_plot_path_b0(path.into());
                        }
                    });
                }
            }
        }
    });

    ui.run()
}

/// Wire the Transmit & Control tab: manual frame transmission and NMT commands.
fn wire_transmit_tab(ui: &MainWindow, bus: Arc<UdpCanBus>, total_tx: Arc<AtomicI32>) {
    let report = |ui: &MainWindow, total_tx: &AtomicI32, outcome: Result<String, String>| {
        match outcome {
            Ok(msg) => {
                let sent = total_tx.fetch_add(1, Ordering::Relaxed) + 1;
                ui.set_total_tx(sent);
                ui.set_tx_status_is_error(false);
                ui.set_tx_status(msg.into());
            }
            Err(msg) => {
                ui.set_tx_status_is_error(true);
                ui.set_tx_status(format!("Error: {msg}").into());
            }
        };
    };

    {
        let bus = Arc::clone(&bus);
        let total_tx = Arc::clone(&total_tx);
        let ui_handle = ui.as_weak();
        ui.on_send_frame(move |id_text, data_text| {
            let Some(ui) = ui_handle.upgrade() else {
                return;
            };
            let outcome = (|| {
                let id = parse_can_id(&id_text)?;
                let payload = parse_payload(&data_text)?;
                let frame = CanFrame::new(id, &payload)
                    .map_err(|e| format!("cannot build the frame: {e:?}"))?;
                bus.send(&frame)
                    .map_err(|e| format!("transmission failed: {e:?}"))?;
                let hex: Vec<String> = payload.iter().map(|b| format!("{b:02X}")).collect();
                Ok(format!(
                    "Sent ID 0x{:03X}  DLC {}  [{}]",
                    id,
                    payload.len(),
                    hex.join(" ")
                ))
            })();
            report(&ui, &total_tx, outcome);
        });
    }

    {
        let bus = Arc::clone(&bus);
        let total_tx = Arc::clone(&total_tx);
        let ui_handle = ui.as_weak();
        ui.on_send_nmt(move |command_byte, node_text| {
            let Some(ui) = ui_handle.upgrade() else {
                return;
            };
            let outcome = (|| {
                let command = nmt_command_from_byte(command_byte)
                    .ok_or_else(|| format!("unknown NMT command 0x{command_byte:02X}"))?;
                let node = parse_node_id(&node_text)?;
                let frame = NmtMaster::build_nmt_command(command, node)
                    .map_err(|e| format!("cannot build the NMT frame: {e:?}"))?;
                bus.send(&frame)
                    .map_err(|e| format!("transmission failed: {e:?}"))?;
                let target = if node == 0 {
                    "all nodes".to_string()
                } else {
                    format!("node {node}")
                };
                Ok(format!(
                    "Sent NMT {command:?} (0x{:02X}) to {target}",
                    command.to_byte()
                ))
            })();
            report(&ui, &total_tx, outcome);
        });
    }
}

/// Convert SDO log rows into the Slint table model. UI thread only.
fn sdo_rows_to_model(rows: Vec<SdoRow>) -> ModelRc<ModelRc<StandardListViewItem>> {
    let models: Vec<ModelRc<StandardListViewItem>> = rows
        .into_iter()
        .map(|row| {
            let cells: Vec<StandardListViewItem> = row
                .iter()
                .map(|t| StandardListViewItem::from(SharedString::from(t.as_str())))
                .collect();
            ModelRc::from(Rc::new(VecModel::from(cells)))
        })
        .collect();
    ModelRc::from(Rc::new(VecModel::from(models)))
}

/// Wire the SDO Object Dictionary tab. Requests go out here; the answers are
/// decoded by the RX thread, which owns the log and pushes it back to the UI.
fn wire_sdo_tab(ui: &MainWindow, bus: Arc<UdpCanBus>, total_tx: Arc<AtomicI32>, sdo_log: SdoLog) {
    {
        let ui_handle = ui.as_weak();
        let bus = Arc::clone(&bus);
        let total_tx = Arc::clone(&total_tx);
        ui.on_sdo_read(move |node_text, index_text, sub_text| {
            let Some(ui) = ui_handle.upgrade() else {
                return;
            };
            let outcome: Result<String, String> = (|| {
                let node = parse_sdo_node_id(&node_text)?;
                let index = parse_index(&index_text)?;
                let subindex = parse_subindex(&sub_text)?;
                let frame = build_sdo_read(node, index, subindex)
                    .map_err(|e| format!("cannot build the SDO request: {e:?}"))?;
                bus.send(&frame)
                    .map_err(|e| format!("transmission failed: {e:?}"))?;
                total_tx.fetch_add(1, Ordering::Relaxed);
                Ok(format!(
                    "Requested {index:#06X}:{subindex:02X} from node {node}, waiting for 0x{:03X}...",
                    0x580 + node as u32
                ))
            })();
            ui.set_total_tx(total_tx.load(Ordering::Relaxed));
            match outcome {
                Ok(msg) => {
                    ui.set_sdo_status_is_error(false);
                    ui.set_sdo_status(msg.into());
                }
                Err(msg) => {
                    ui.set_sdo_status_is_error(true);
                    ui.set_sdo_status(format!("Error: {msg}").into());
                }
            }
        });
    }

    {
        let ui_handle = ui.as_weak();
        let bus = Arc::clone(&bus);
        let total_tx = Arc::clone(&total_tx);
        ui.on_sdo_write(
            move |node_text, index_text, sub_text, value_text, type_text| {
                let Some(ui) = ui_handle.upgrade() else {
                    return;
                };
                let outcome: Result<String, String> = (|| {
                    let node = parse_sdo_node_id(&node_text)?;
                    let index = parse_index(&index_text)?;
                    let subindex = parse_subindex(&sub_text)?;
                    let ty = sdo_write_type(&type_text)?;
                    let data = encode_sdo_value(&value_text, &ty)?;
                    let frame = build_sdo_write(node, index, subindex, &data)
                        .map_err(|e| format!("cannot build the SDO download: {e:?}"))?;
                    bus.send(&frame)
                        .map_err(|e| format!("transmission failed: {e:?}"))?;
                    total_tx.fetch_add(1, Ordering::Relaxed);
                    Ok(format!(
                        "Wrote {index:#06X}:{subindex:02X} = {} to node {node}, waiting for 0x{:03X}...",
                        hex_bytes(&data),
                        0x580 + node as u32
                    ))
                })();
                ui.set_total_tx(total_tx.load(Ordering::Relaxed));
                match outcome {
                    Ok(msg) => {
                        ui.set_sdo_status_is_error(false);
                        ui.set_sdo_status(msg.into());
                    }
                    Err(msg) => {
                        ui.set_sdo_status_is_error(true);
                        ui.set_sdo_status(format!("Error: {msg}").into());
                    }
                }
            },
        );
    }

    {
        let ui_handle = ui.as_weak();
        ui.on_sdo_clear(move || {
            if let Ok(mut log) = sdo_log.lock() {
                log.clear();
            }
            if let Some(ui) = ui_handle.upgrade() {
                ui.set_sdo_log_model(sdo_rows_to_model(Vec::new()));
                ui.set_sdo_status_is_error(false);
                ui.set_sdo_status(SharedString::new());
            }
        });
    }
}

/// Send one ping and record it as outstanding. Returns the sequence used.
fn send_ping(bus: &UdpCanBus, state: &PingState, total_tx: &AtomicI32) -> Result<u32, String> {
    let seq = state.next_seq.fetch_add(1, Ordering::Relaxed);
    let frame = build_ping_request(seq, 0).map_err(|e| format!("cannot build the ping: {e:?}"))?;
    // Record before sending: a fast responder may answer before send() returns.
    if let Ok(mut pending) = state.pending.lock() {
        pending.insert(seq, Instant::now());
    }
    bus.send(&frame).map_err(|e| {
        if let Ok(mut pending) = state.pending.lock() {
            pending.remove(&seq);
        }
        format!("transmission failed: {e:?}")
    })?;
    state.sent.fetch_add(1, Ordering::Relaxed);
    total_tx.fetch_add(1, Ordering::Relaxed);
    Ok(seq)
}

/// Wire the Hardware Diagnostics tab. Returns the periodic-ping timer, which
/// the caller must keep alive for the toggle to keep firing. `slint::Timer` is
/// not `Clone`, so it is shared through an `Rc`; everything here runs on the
/// UI thread.
fn wire_hardware_tab(
    ui: &MainWindow,
    bus: Arc<UdpCanBus>,
    total_tx: Arc<AtomicI32>,
    state: Arc<PingState>,
    sync_tracker: Arc<LatencyTracker>,
) -> Rc<slint::Timer> {
    {
        let bus = Arc::clone(&bus);
        let state = Arc::clone(&state);
        let total_tx = Arc::clone(&total_tx);
        let ui_handle = ui.as_weak();
        ui.on_ping_once(move || {
            let Some(ui) = ui_handle.upgrade() else {
                return;
            };
            match send_ping(&bus, &state, &total_tx) {
                Ok(_) => ui.set_loss_stats(state.loss_text().into()),
                Err(e) => ui.set_loss_stats(format!("Error: {e}").into()),
            }
            ui.set_total_tx(total_tx.load(Ordering::Relaxed));
        });
    }

    {
        let state = Arc::clone(&state);
        ui.on_auto_echo_changed(move |on| {
            state.auto_echo.store(on, Ordering::Relaxed);
        });
    }

    {
        let state = Arc::clone(&state);
        let sync_tracker = Arc::clone(&sync_tracker);
        let ui_handle = ui.as_weak();
        ui.on_reset_latency(move || {
            state.reset();
            sync_tracker.reset();
            if let Some(ui) = ui_handle.upgrade() {
                ui.set_rtt_stats(format_rtt(&state.rtt.stats(), None).into());
                ui.set_sync_stats(format_sync(&sync_tracker.stats()).into());
                ui.set_loss_stats(state.loss_text().into());
            }
        });
    }

    let timer = Rc::new(slint::Timer::default());
    {
        let bus = Arc::clone(&bus);
        let state = Arc::clone(&state);
        let total_tx = Arc::clone(&total_tx);
        let ui_handle = ui.as_weak();
        let timer_handle = Rc::clone(&timer);
        ui.on_periodic_ping_changed(move |on| {
            if !on {
                timer_handle.stop();
                return;
            }
            let bus = Arc::clone(&bus);
            let state = Arc::clone(&state);
            let total_tx = Arc::clone(&total_tx);
            let ui_handle = ui_handle.clone();
            timer_handle.start(
                slint::TimerMode::Repeated,
                Duration::from_secs(1),
                move || {
                    let _ = send_ping(&bus, &state, &total_tx);
                    if let Some(ui) = ui_handle.upgrade() {
                        ui.set_total_tx(total_tx.load(Ordering::Relaxed));
                    }
                },
            );
        });
    }
    timer
}

/// Wire the OBD-II tab. Returns the polling timer, which the caller must keep
/// alive for the toggle to keep firing.
fn wire_obd_tab(
    ui: &MainWindow,
    bus: Arc<UdpCanBus>,
    total_tx: Arc<AtomicI32>,
    readings: ObdReadings,
) -> Rc<slint::Timer> {
    let announce = |ui: &MainWindow, total_tx: &AtomicI32, outcome: Result<usize, String>| {
        ui.set_total_tx(total_tx.load(Ordering::Relaxed));
        match outcome {
            Ok(n) => {
                ui.set_obd_status_is_error(false);
                ui.set_obd_status(format!("Requested {n} parameter(s), waiting...").into());
            }
            Err(e) => {
                ui.set_obd_status_is_error(true);
                ui.set_obd_status(format!("Error: {e}").into());
            }
        }
    };

    {
        let bus = Arc::clone(&bus);
        let total_tx = Arc::clone(&total_tx);
        let ui_handle = ui.as_weak();
        ui.on_obd_read_all(move || {
            if let Some(ui) = ui_handle.upgrade() {
                announce(&ui, &total_tx, request_all_obd_pids(&bus, &total_tx));
            }
        });
    }

    {
        let readings = Arc::clone(&readings);
        let ui_handle = ui.as_weak();
        ui.on_obd_clear(move || {
            if let Ok(mut r) = readings.lock() {
                r.clear();
            }
            if let Some(ui) = ui_handle.upgrade() {
                ui.set_obd_rows_model(obd_rows_to_model(Vec::new()));
                ui.set_obd_status_is_error(false);
                ui.set_obd_status("No request sent yet.".into());
            }
        });
    }

    let timer = Rc::new(slint::Timer::default());
    {
        let bus = Arc::clone(&bus);
        let total_tx = Arc::clone(&total_tx);
        let ui_handle = ui.as_weak();
        let timer_handle = Rc::clone(&timer);
        ui.on_obd_polling_changed(move |on| {
            if !on {
                timer_handle.stop();
                return;
            }
            let bus = Arc::clone(&bus);
            let total_tx = Arc::clone(&total_tx);
            let ui_handle = ui_handle.clone();
            timer_handle.start(
                slint::TimerMode::Repeated,
                Duration::from_secs(1),
                move || {
                    let _ = request_all_obd_pids(&bus, &total_tx);
                    if let Some(ui) = ui_handle.upgrade() {
                        ui.set_total_tx(total_tx.load(Ordering::Relaxed));
                    }
                },
            );
        });
    }
    timer
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn can_ids_are_read_with_or_without_the_hex_prefix() {
        assert_eq!(parse_can_id("0x601"), Ok(0x601));
        assert_eq!(parse_can_id("0X601"), Ok(0x601));
        assert_eq!(parse_can_id("601"), Ok(0x601));
        assert_eq!(parse_can_id("  7ff  "), Ok(0x7FF));
    }

    #[test]
    fn a_can_id_beyond_29_bits_is_refused() {
        assert!(parse_can_id("20000000").is_err());
        assert_eq!(parse_can_id("1FFFFFFF"), Ok(0x1FFF_FFFF));
    }

    #[test]
    fn a_malformed_can_id_is_refused() {
        assert!(parse_can_id("").is_err());
        assert!(parse_can_id("zzz").is_err());
        assert!(parse_can_id("0x").is_err());
    }

    #[test]
    fn payloads_accept_spaces_commas_and_dashes() {
        let expected = vec![0x40, 0x00, 0x10];
        assert_eq!(parse_payload("40 00 10"), Ok(expected.clone()));
        assert_eq!(parse_payload("400010"), Ok(expected.clone()));
        assert_eq!(parse_payload("40,00,10"), Ok(expected.clone()));
        assert_eq!(parse_payload("40-00-10"), Ok(expected));
    }

    #[test]
    fn an_empty_payload_is_a_valid_zero_length_frame() {
        assert_eq!(parse_payload(""), Ok(Vec::new()));
        assert_eq!(parse_payload("   "), Ok(Vec::new()));
    }

    #[test]
    fn a_payload_with_an_odd_digit_count_is_refused() {
        let err = parse_payload("40 0").unwrap_err();
        assert!(err.contains("even count"), "{err}");
    }

    #[test]
    fn a_payload_longer_than_eight_bytes_is_refused() {
        let err = parse_payload("00 11 22 33 44 55 66 77 88").unwrap_err();
        assert!(err.contains("at most 8"), "{err}");
        assert!(parse_payload("00 11 22 33 44 55 66 77").is_ok());
    }

    #[test]
    fn a_payload_with_a_non_hex_byte_is_refused() {
        assert!(parse_payload("4Z").is_err());
    }

    #[test]
    fn node_zero_is_the_broadcast_target() {
        assert_eq!(parse_node_id("0"), Ok(0));
        assert_eq!(parse_node_id("127"), Ok(127));
        assert_eq!(parse_node_id(" 5 "), Ok(5));
    }

    #[test]
    fn a_node_id_beyond_127_is_refused() {
        assert!(parse_node_id("128").is_err());
        assert!(parse_node_id("-1").is_err());
        assert!(parse_node_id("abc").is_err());
    }

    #[test]
    fn every_nmt_button_maps_to_its_command_specifier() {
        assert_eq!(
            nmt_command_from_byte(0x01),
            Some(NmtCommand::StartRemoteNode)
        );
        assert_eq!(
            nmt_command_from_byte(0x02),
            Some(NmtCommand::StopRemoteNode)
        );
        assert_eq!(
            nmt_command_from_byte(0x80),
            Some(NmtCommand::EnterPreOperational)
        );
        assert_eq!(nmt_command_from_byte(0x81), Some(NmtCommand::ResetNode));
        assert_eq!(
            nmt_command_from_byte(0x82),
            Some(NmtCommand::ResetCommunication)
        );
        assert_eq!(nmt_command_from_byte(0x7F), None);
    }

    #[test]
    fn an_nmt_frame_carries_the_command_and_the_target_on_cob_id_zero() {
        let frame = NmtMaster::build_nmt_command(NmtCommand::StartRemoteNode, 5).unwrap();
        assert_eq!(frame.id, 0x000);
        assert_eq!(frame.payload(), &[0x01, 5]);
    }
    #[test]
    fn heartbeats_and_pdos_are_attributed_to_their_node() {
        assert_eq!(classify_service(0x701), Some((1, "HEARTBEAT")));
        assert_eq!(classify_service(0x705), Some((5, "HEARTBEAT")));
        assert_eq!(classify_service(0x181), Some((1, "TPDO")));
        assert_eq!(classify_service(0x281), Some((1, "TPDO")));
        assert_eq!(classify_service(0x201), Some((1, "RPDO")));
        assert_eq!(classify_service(0x401), Some((1, "RPDO")));
        assert_eq!(classify_service(0x481), Some((1, "TPDO")));
        assert_eq!(classify_service(0x581), Some((1, "SDO")));
        assert_eq!(classify_service(0x605), Some((5, "SDO")));
        assert_eq!(classify_service(0x081), Some((1, "EMCY")));
    }

    #[test]
    fn vendor_cob_ids_are_read_against_the_standard_layout() {
        // SEVCON calls 0x473 "TPDO5", but that is a vendor remap: under the
        // CiA 301 predefined connection set 0x473 is RPDO3 of node 115.
        // The services column reports the standard reading, and the phantom
        // node is never listed because no heartbeat ever announces it.
        assert_eq!(classify_service(0x473), Some((115, "RPDO")));
        assert_eq!(classify_service(0x270), Some((112, "RPDO")));
        assert_eq!(classify_service(0x156), None);
    }

    #[test]
    fn broadcast_and_unassigned_cob_ids_belong_to_no_node() {
        assert_eq!(classify_service(0x000), None); // NMT command
        assert_eq!(classify_service(0x080), None); // SYNC
        assert_eq!(classify_service(0x700), None); // node 0 is not a node
        assert_eq!(classify_service(0x7FF), None);
    }

    #[test]
    fn node_rows_are_sorted_and_carry_state_age_and_services() {
        let now = 10_000_000u64;
        let nodes = vec![
            MonitoredNode {
                node_id: 2,
                state: NmtState::PreOperational,
                last_seen_us: now - 1_500_000,
                heartbeat_interval_us: None,
                is_timed_out: false,
            },
            MonitoredNode {
                node_id: 1,
                state: NmtState::Operational,
                last_seen_us: now,
                heartbeat_interval_us: None,
                is_timed_out: false,
            },
        ];
        let mut services: HashMap<u8, BTreeSet<&'static str>> = HashMap::new();
        services.entry(1).or_default().insert("HEARTBEAT");
        services.entry(1).or_default().insert("TPDO");

        let rows = build_node_rows(&nodes, &services, now);
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0][0], "Node 1", "rows must be ordered by node ID");
        assert_eq!(rows[0][1], "Operational");
        assert_eq!(rows[0][2], "0.0s ago");
        assert_eq!(rows[0][3], "HEARTBEAT, TPDO");
        assert_eq!(rows[1][0], "Node 2");
        assert_eq!(rows[1][1], "Pre-Operational");
        assert_eq!(rows[1][2], "1.5s ago");
        assert_eq!(rows[1][3], "", "a node with no seen service lists none");
    }

    #[test]
    fn a_timed_out_node_says_so_instead_of_an_age() {
        let node = MonitoredNode {
            node_id: 3,
            state: NmtState::Operational,
            last_seen_us: 0,
            heartbeat_interval_us: Some(1_000_000),
            is_timed_out: true,
        };
        let rows = build_node_rows(&[node], &HashMap::new(), 9_000_000);
        assert_eq!(rows[0][2], "timed out");
    }

    #[test]
    fn a_clock_running_backwards_does_not_panic_on_the_age() {
        assert_eq!(format_age(0, 5_000_000, false), "0.0s ago");
    }
    #[test]
    fn object_indices_are_read_with_or_without_the_hex_prefix() {
        assert_eq!(parse_index("0x1000"), Ok(0x1000));
        assert_eq!(parse_index("606C"), Ok(0x606C));
        assert_eq!(parse_index(" 0X606c "), Ok(0x606C));
        assert_eq!(parse_index("FFFF"), Ok(0xFFFF));
    }

    #[test]
    fn an_index_that_does_not_fit_in_sixteen_bits_is_refused() {
        assert!(parse_index("10000").is_err());
        assert!(parse_index("").is_err());
        assert!(parse_index("zz").is_err());
    }

    #[test]
    fn sub_indices_accept_decimal_and_hex() {
        assert_eq!(parse_subindex("0"), Ok(0));
        assert_eq!(parse_subindex("10"), Ok(10), "bare digits are decimal");
        assert_eq!(parse_subindex("0x10"), Ok(0x10));
        assert_eq!(parse_subindex("255"), Ok(255));
    }

    #[test]
    fn a_sub_index_beyond_one_byte_is_refused() {
        assert!(parse_subindex("256").is_err());
        assert!(parse_subindex("").is_err());
    }

    #[test]
    fn sdo_cannot_be_addressed_to_the_broadcast_node() {
        assert!(parse_sdo_node_id("0").is_err());
        assert_eq!(parse_sdo_node_id("1"), Ok(1));
        assert_eq!(parse_sdo_node_id("127"), Ok(127));
    }

    #[test]
    fn a_value_is_shown_as_hex_unsigned_and_text_when_it_is_text() {
        let v = describe_sdo_value(b"Virt");
        assert!(v.contains("0x74726956"), "{v}");
        assert!(v.contains("\"Virt\""), "{v}");
    }

    #[test]
    fn a_negative_value_is_shown_signed_as_well_as_unsigned() {
        let v = describe_sdo_value(&(-750i32).to_le_bytes());
        assert!(v.contains("i=-750"), "{v}");
        assert!(v.contains("u=4294966546"), "{v}");
    }

    #[test]
    fn a_short_value_is_not_padded_in_the_hex_rendering() {
        let v = describe_sdo_value(&[0x2A]);
        assert!(v.starts_with("0x2A"), "{v}");
        assert!(v.contains("u=42"), "{v}");
    }

    #[test]
    fn an_empty_payload_is_reported_rather_than_rendered() {
        assert_eq!(describe_sdo_value(&[]), "(no data)");
    }

    #[test]
    fn an_expedited_upload_response_becomes_an_explorer_row() {
        let msg = SdoMessage::ExpeditedUploadResponse {
            node_id: 1,
            index: 0x1000,
            subindex: 0,
            data: 0x0002_0192u32.to_le_bytes().to_vec(),
        };
        let row = sdo_response_row(&msg).expect("a response must produce a row");
        assert_eq!(row[0], "0x1000:00");
        assert_eq!(row[1], "OK (node 1)");
        assert!(row[2].contains("u=131474"), "{}", row[2]);
        assert_eq!(row[3], "92 01 02 00");
    }

    #[test]
    fn an_abort_becomes_a_row_that_names_the_reason() {
        let frame = canopen_core::sdo::build_sdo_abort(
            5,
            0x1000,
            1,
            canopen_core::SdoAbortCode::ObjectDoesNotExist,
        )
        .unwrap();
        let msg = parse_sdo_frame(&frame).expect("abort must parse");
        let row = sdo_response_row(&msg).expect("an abort must produce a row");
        assert_eq!(row[0], "0x1000:01");
        assert!(row[1].starts_with("Abort"), "{}", row[1]);
        assert_eq!(row[3], "0x06020000");
    }

    #[test]
    fn a_request_frame_is_not_logged_as_a_response() {
        let request = build_sdo_read(1, 0x1000, 0).unwrap();
        let msg = parse_sdo_frame(&request).expect("request must parse");
        assert!(
            sdo_response_row(&msg).is_none(),
            "only server answers belong in the log"
        );
    }

    #[test]
    fn every_write_type_maps_to_its_width_and_sign() {
        for (label, width, signed) in [
            ("uint8 (1 byte)", 1, false),
            ("uint16 (2 bytes)", 2, false),
            ("uint32 (4 bytes)", 4, false),
            ("int16 (2 bytes)", 2, true),
            ("int32 (4 bytes)", 4, true),
        ] {
            let ty = sdo_write_type(label).expect("the chooser only offers known types");
            assert_eq!((ty.width, ty.signed), (width, signed), "{label}");
        }
    }

    #[test]
    fn an_unknown_write_type_is_refused() {
        assert!(sdo_write_type("float32 (4 bytes)").is_err());
        assert!(sdo_write_type("").is_err());
    }

    #[test]
    fn a_write_value_is_decimal_unless_it_is_prefixed_with_0x() {
        let u16_ = sdo_write_type("uint16 (2 bytes)").unwrap();
        assert_eq!(encode_sdo_value("1500", &u16_).unwrap(), vec![0xDC, 0x05]);
        assert_eq!(encode_sdo_value("0x05DC", &u16_).unwrap(), vec![0xDC, 0x05]);
        // A bare 10 is ten, not sixteen.
        assert_eq!(encode_sdo_value("10", &u16_).unwrap(), vec![0x0A, 0x00]);
    }

    #[test]
    fn a_value_is_encoded_little_endian_at_the_width_of_its_type() {
        let u8_ = sdo_write_type("uint8 (1 byte)").unwrap();
        let u32_ = sdo_write_type("uint32 (4 bytes)").unwrap();
        assert_eq!(encode_sdo_value("0x7F", &u8_).unwrap(), vec![0x7F]);
        assert_eq!(
            encode_sdo_value("0x00020192", &u32_).unwrap(),
            vec![0x92, 0x01, 0x02, 0x00]
        );
    }

    #[test]
    fn a_negative_value_is_encoded_in_twos_complement() {
        let i16_ = sdo_write_type("int16 (2 bytes)").unwrap();
        let i32_ = sdo_write_type("int32 (4 bytes)").unwrap();
        assert_eq!(encode_sdo_value("-1", &i16_).unwrap(), vec![0xFF, 0xFF]);
        assert_eq!(encode_sdo_value("-1500", &i16_).unwrap(), vec![0x24, 0xFA]);
        assert_eq!(
            encode_sdo_value("-2", &i32_).unwrap(),
            vec![0xFE, 0xFF, 0xFF, 0xFF]
        );
    }

    #[test]
    fn each_type_accepts_its_full_range_and_nothing_beyond() {
        let u8_ = sdo_write_type("uint8 (1 byte)").unwrap();
        assert_eq!(encode_sdo_value("255", &u8_).unwrap(), vec![0xFF]);
        assert!(encode_sdo_value("256", &u8_).is_err());
        assert!(
            encode_sdo_value("-1", &u8_).is_err(),
            "unsigned has no sign"
        );

        let i16_ = sdo_write_type("int16 (2 bytes)").unwrap();
        assert_eq!(encode_sdo_value("32767", &i16_).unwrap(), vec![0xFF, 0x7F]);
        assert_eq!(encode_sdo_value("-32768", &i16_).unwrap(), vec![0x00, 0x80]);
        assert!(encode_sdo_value("32768", &i16_).is_err());
        // 0xFFFF is 65535, which is out of range for a signed 16-bit object.
        assert!(encode_sdo_value("0xFFFF", &i16_).is_err());

        let u32_ = sdo_write_type("uint32 (4 bytes)").unwrap();
        assert_eq!(
            encode_sdo_value("4294967295", &u32_).unwrap(),
            vec![0xFF, 0xFF, 0xFF, 0xFF]
        );
        assert!(encode_sdo_value("4294967296", &u32_).is_err());
    }

    #[test]
    fn an_empty_or_malformed_write_value_is_refused() {
        let u16_ = sdo_write_type("uint16 (2 bytes)").unwrap();
        assert!(encode_sdo_value("", &u16_).is_err());
        assert!(encode_sdo_value("   ", &u16_).is_err());
        assert!(encode_sdo_value("twelve", &u16_).is_err());
        assert!(encode_sdo_value("0xZZ", &u16_).is_err());
    }

    #[test]
    fn an_encoded_value_becomes_a_download_frame_the_server_can_read() {
        let ty = sdo_write_type("uint16 (2 bytes)").unwrap();
        let data = encode_sdo_value("0x000F", &ty).unwrap();
        let frame = build_sdo_write(1, 0x6040, 0, &data).unwrap();

        assert_eq!(frame.id, 0x601, "SDO client request to node 1");
        match parse_sdo_frame(&frame).expect("the download must parse") {
            SdoMessage::ExpeditedDownloadRequest {
                node_id,
                index,
                subindex,
                data,
            } => {
                assert_eq!((node_id, index, subindex), (1, 0x6040, 0));
                assert_eq!(data, vec![0x0F, 0x00]);
            }
            other => panic!("expected an expedited download, got {other:?}"),
        }
    }

    #[test]
    fn a_download_response_is_logged_as_a_write_that_was_accepted() {
        let confirmation = CanFrame::new(0x581, &[0x60, 0x40, 0x60, 0x00, 0, 0, 0, 0]).unwrap();
        let msg = parse_sdo_frame(&confirmation).expect("confirmation must parse");
        let row = sdo_response_row(&msg).expect("a write confirmation belongs in the log");
        assert_eq!(row[0], "0x6040:00");
        assert_eq!(row[1], "Written (node 1)");
    }

    #[test]
    fn a_fresh_ping_state_reports_nothing_sent() {
        let state = PingState::new();
        assert_eq!(state.loss_text(), "Sent 0, answered 0");
        assert_eq!(
            format_rtt(&state.rtt.stats(), None),
            "No ping answered yet."
        );
    }

    #[test]
    fn an_answered_ping_is_recorded_and_clears_the_pending_entry() {
        let state = PingState::new();
        state.pending.lock().unwrap().insert(7, Instant::now());
        state.sent.fetch_add(1, Ordering::Relaxed);

        let rtt = state.record_response(7);
        assert!(rtt.is_some(), "a matching sequence must be recorded");
        assert_eq!(state.answered.load(Ordering::Relaxed), 1);
        assert!(state.pending.lock().unwrap().is_empty());
        assert_eq!(state.rtt.stats().count, 1);
    }

    #[test]
    fn an_unmatched_sequence_is_ignored() {
        let state = PingState::new();
        state.pending.lock().unwrap().insert(7, Instant::now());
        assert!(
            state.record_response(8).is_none(),
            "a foreign sequence must not count as our answer"
        );
        assert_eq!(state.answered.load(Ordering::Relaxed), 0);
        assert_eq!(state.pending.lock().unwrap().len(), 1);
    }

    #[test]
    fn the_same_response_cannot_be_counted_twice() {
        let state = PingState::new();
        state.pending.lock().unwrap().insert(7, Instant::now());
        assert!(state.record_response(7).is_some());
        assert!(
            state.record_response(7).is_none(),
            "a duplicate response must be dropped"
        );
        assert_eq!(state.answered.load(Ordering::Relaxed), 1);
    }

    #[test]
    fn loss_is_the_share_of_pings_left_unanswered() {
        let state = PingState::new();
        state.sent.store(4, Ordering::Relaxed);
        state.answered.store(3, Ordering::Relaxed);
        assert_eq!(state.loss_text(), "Sent 4, answered 3  |  loss 25.0%");
    }

    #[test]
    fn stale_pings_are_dropped_so_the_pending_map_stays_bounded() {
        let state = PingState::new();
        state
            .pending
            .lock()
            .unwrap()
            .insert(1, Instant::now() - Duration::from_secs(10));
        state.pending.lock().unwrap().insert(2, Instant::now());

        state.drop_stale(Duration::from_secs(2));
        let pending = state.pending.lock().unwrap();
        assert!(!pending.contains_key(&1), "the stale ping must be dropped");
        assert!(pending.contains_key(&2), "the fresh ping must be kept");
    }

    #[test]
    fn resetting_clears_every_counter_and_sample() {
        let state = PingState::new();
        state.pending.lock().unwrap().insert(7, Instant::now());
        state.sent.fetch_add(1, Ordering::Relaxed);
        state.record_response(7);

        state.reset();
        assert_eq!(state.loss_text(), "Sent 0, answered 0");
        assert_eq!(state.rtt.stats().count, 0);
        assert!(state.pending.lock().unwrap().is_empty());
        assert!(state.last_rtt_us.lock().unwrap().is_none());
    }

    #[test]
    fn sync_statistics_name_the_nominal_interval_they_are_judged_against() {
        let tracker = LatencyTracker::new(Some(SYNC_NOMINAL_US));
        assert_eq!(format_sync(&tracker.stats()), "No SYNC frame seen yet.");

        // Three intervals: 20.0, 20.5 and 19.8 ms.
        for t in [0u64, 20_000, 40_500, 60_300] {
            tracker.record_frame_timestamp(t);
        }
        let text = format_sync(&tracker.stats());
        assert!(text.contains("nominal 20.0 ms"), "{text}");
        assert!(text.contains("3 intervals"), "{text}");
        assert!(text.contains("jitter"), "{text}");
    }

    #[test]
    fn the_most_recent_round_trip_is_reported_next_to_the_average() {
        let tracker = LatencyTracker::new(None);
        tracker.record_sample_us(1_000.0);
        tracker.record_sample_us(3_000.0);
        let text = format_rtt(&tracker.stats(), Some(3_000.0));
        assert!(text.contains("RTT last 3.000 ms"), "{text}");
        assert!(text.contains("avg 2.000"), "{text}");
        assert!(text.contains("2 samples"), "{text}");
    }
    #[test]
    fn an_obd_row_names_the_pid_in_hex_and_carries_its_unit() {
        let response =
            canopen_core::obd2::build_obd2_mode01_response(0x0C, &7000u16.to_be_bytes()).unwrap();
        let reading = decode_obd2_mode01_frame(&response).unwrap();
        let row = obd_row(&reading);
        assert_eq!(row[0], "0C");
        assert_eq!(row[1], "Engine RPM");
        assert_eq!(row[2], "1750", "a whole value is shown without a fraction");
        assert_eq!(row[3], "rpm");
    }

    #[test]
    fn a_fractional_obd_value_keeps_one_decimal() {
        // Engine load is A * 100 / 255, so 100 -> 39.2 %.
        let response = canopen_core::obd2::build_obd2_mode01_response(0x04, &[100]).unwrap();
        let reading = decode_obd2_mode01_frame(&response).unwrap();
        let row = obd_row(&reading);
        assert_eq!(row[2], "39.2");
        assert_eq!(row[3], "%");
    }

    #[test]
    fn readings_are_keyed_by_pid_so_a_refresh_replaces_rather_than_appends() {
        let mut readings: BTreeMap<u8, ObdRow> = BTreeMap::new();
        for value in [7000u16, 8000] {
            let response =
                canopen_core::obd2::build_obd2_mode01_response(0x0C, &value.to_be_bytes()).unwrap();
            let reading = decode_obd2_mode01_frame(&response).unwrap();
            readings.insert(reading.pid, obd_row(&reading));
        }
        assert_eq!(readings.len(), 1, "the same PID must not be listed twice");
        assert_eq!(readings[&0x0C][2], "2000", "the latest value wins");
    }

    #[test]
    fn rows_come_out_ordered_by_pid() {
        let mut readings: BTreeMap<u8, ObdRow> = BTreeMap::new();
        for pid in [0x42u8, 0x04, 0x0C] {
            let response =
                canopen_core::obd2::build_obd2_mode01_response(pid, &[0x40, 0x00]).unwrap();
            let reading = decode_obd2_mode01_frame(&response).unwrap();
            readings.insert(reading.pid, obd_row(&reading));
        }
        let order: Vec<String> = readings.values().map(|r| r[0].clone()).collect();
        assert_eq!(order, vec!["04", "0C", "42"]);
    }
}
