use crate::frame::CanFrame;
use std::collections::BTreeMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Instant;

pub trait CanBusWrapper: Send + Sync {
    fn send(&self, frame: &CanFrame) -> bool;
    fn recv(&self, timeout_ms: u64) -> Option<CanFrame>;
}

pub struct SimulatorState {
    pub nmt_state_node1: u8,
    pub nmt_state_node2: u8,
    pub sim_rpm: i32,
    /// Objects a client has written over SDO, keyed by (index, sub-index).
    /// The simulated node has no real dictionary, so this is what gives a
    /// download somewhere to land and an upload something to read back.
    pub written_objects: BTreeMap<(u16, u8), Vec<u8>>,
    pub running: Arc<AtomicBool>,
}

impl Default for SimulatorState {
    fn default() -> Self {
        Self::new()
    }
}

impl SimulatorState {
    pub fn new() -> Self {
        Self {
            nmt_state_node1: 0x7F, // Pre-Operational
            nmt_state_node2: 0x7F,
            sim_rpm: 1800,
            written_objects: BTreeMap::new(),
            running: Arc::new(AtomicBool::new(true)),
        }
    }
}

/// Raw Mode 01 payload for the PIDs the simulated ECU supports.
/// Engine speed follows the simulated motor so the reading moves with the bus;
/// the rest are plausible constants. Returns `None` for an unsupported PID,
/// which the simulator answers with silence, as a real ECU would.
fn simulated_obd2_pid(pid: u8, sim_rpm: i32) -> Option<Vec<u8>> {
    match pid {
        // Calculated engine load, A * 100 / 255.
        0x04 => Some(vec![128]),
        // Coolant temperature, A - 40.
        0x05 => Some(vec![90 + 40]),
        // Engine speed, ((A * 256) + B) / 4 -> quarter revolutions.
        0x0C => {
            let quarters = (sim_rpm.max(0) as u32 * 4).min(u16::MAX as u32) as u16;
            Some(quarters.to_be_bytes().to_vec())
        }
        // Vehicle speed in km/h; derive something that tracks the motor.
        0x0D => Some(vec![(sim_rpm.max(0) / 40).min(255) as u8]),
        // Intake air temperature, A - 40.
        0x0F => Some(vec![25 + 40]),
        // Throttle position, A * 100 / 255.
        0x11 => Some(vec![64]),
        // Control module voltage, ((A * 256) + B) / 1000.
        0x42 => Some(13_800u16.to_be_bytes().to_vec()),
        _ => None,
    }
}

/// The objects the simulated node serves from its own live state. A client
/// may read them, but writing one is refused the way a real drive refuses it.
fn is_read_only(index: u16, subindex: u8) -> bool {
    matches!((index, subindex), (0x1000, 0) | (0x1008, 0) | (0x606C, 0))
}

/// The value carried by an expedited download request, or `None` if the
/// command specifier is not one (CiA 301: ccs 1, expedited bit set).
///
/// When the size is not indicated the request carries all four data bytes,
/// which is what the trailing slice falls back to.
fn expedited_download_payload(cs: u8, data: &[u8]) -> Option<&[u8]> {
    if (cs & 0xE0) != 0x20 || (cs & 0x02) == 0 {
        return None;
    }
    let n = if (cs & 0x01) != 0 {
        ((cs >> 2) & 0x03) as usize
    } else {
        0
    };
    data.get(4..4 + (4 - n))
}

pub fn run_simulator_loop(
    bus: Box<dyn CanBusWrapper>,
    state: Arc<std::sync::RwLock<SimulatorState>>,
) {
    let mut angle: f32 = 0.0;

    let mut last_hb = Instant::now();
    let mut last_sync = Instant::now();
    let mut last_pdo = Instant::now();

    while state.read().unwrap().running.load(Ordering::SeqCst) {
        // Polling RX via timeout
        if let Some(frame) = bus.recv(10) {
            let id = frame.id;
            let data = frame.payload();
            let mut s = state.write().unwrap();

            // NMT Command
            if id == 0x000 && data.len() >= 2 {
                let cmd = data[0];
                let target = data[1];
                if target == 0 || target == 1 {
                    if cmd == 0x01 {
                        s.nmt_state_node1 = 0x05;
                    } else if cmd == 0x02 {
                        s.nmt_state_node1 = 0x04;
                    } else if cmd == 0x80 {
                        s.nmt_state_node1 = 0x7F;
                    } else if cmd == 0x81 {
                        s.nmt_state_node1 = 0x00;
                    }
                }
            }
            // CAN Ping: echo the payload back so a client can measure the
            // round trip against the virtual bus.
            else if let Some(pong) = crate::latency::build_ping_response(&frame) {
                bus.send(&pong);
            }
            // OBD-II Mode 01: answer as an ECU would, so the diagnostics tab
            // has something to read on the virtual bus.
            else if let Some(pid) = crate::obd2::obd2_mode01_request_pid(&frame) {
                if let Some(data) = simulated_obd2_pid(pid, s.sim_rpm)
                    && let Ok(resp) = crate::obd2::build_obd2_mode01_response(pid, &data)
                {
                    bus.send(&resp);
                }
            }
            // SDO Request
            else if id == 0x601 && data.len() >= 4 {
                let cs = data[0];
                let idx = data[1] as u16 | ((data[2] as u16) << 8);
                let sub = data[3];
                if cs == 0x40 {
                    let mut resp_payload = vec![0, 0, 0, 0];
                    let mut resp_cs = 0x42;

                    if idx == 0x1000 && sub == 0 {
                        resp_payload = 0x00020192_u32.to_le_bytes().to_vec();
                        resp_cs = 0x43;
                    } else if idx == 0x1008 && sub == 0 {
                        resp_payload = b"Virt".to_vec(); // Virtual Drive
                        resp_cs = 0x43;
                    } else if idx == 0x606C && sub == 0 {
                        resp_payload = s.sim_rpm.to_le_bytes().to_vec();
                        resp_cs = 0x43;
                    } else if let Some(stored) = s.written_objects.get(&(idx, sub)) {
                        // An object written earlier reads back as it was
                        // stored, so a download can be confirmed by reading
                        // the object again.
                        resp_cs = 0x43 | (((4 - stored.len()) as u8) << 2);
                        resp_payload = stored.clone();
                    }

                    let mut reply = vec![resp_cs, (idx & 0xFF) as u8, (idx >> 8) as u8, sub];
                    reply.extend(resp_payload);
                    bus.send(&CanFrame::new(0x581, &reply).unwrap());
                } else if let Some(written) = expedited_download_payload(cs, data) {
                    // The simulated node serves a handful of objects from its
                    // own state; those are read-only, as they are on a real
                    // drive. Anything else it accepts as scratch, so the
                    // download path can be exercised end to end.
                    let reply = if is_read_only(idx, sub) {
                        crate::sdo::build_sdo_abort(
                            1,
                            idx,
                            sub,
                            crate::sdo::SdoAbortCode::WriteReadOnlyObject,
                        )
                    } else {
                        s.written_objects.insert((idx, sub), written.to_vec());
                        CanFrame::new(
                            0x581,
                            &[0x60, (idx & 0xFF) as u8, (idx >> 8) as u8, sub, 0, 0, 0, 0],
                        )
                    };
                    if let Ok(frame) = reply {
                        bus.send(&frame);
                    }
                }
            }
        }

        let now2 = Instant::now();

        // 1. SYNC frame at 50 Hz (20 ms)
        if now2.duration_since(last_sync).as_secs_f32() >= 0.020 {
            bus.send(&CanFrame::new(0x080, &[]).unwrap());
            last_sync = now2;
        }

        // 2. Heartbeat at 1 Hz
        if now2.duration_since(last_hb).as_secs_f32() >= 1.0 {
            let (n1, n2) = {
                let s = state.read().unwrap();
                (s.nmt_state_node1, s.nmt_state_node2)
            };
            bus.send(&CanFrame::new(0x701, &[n1]).unwrap());
            bus.send(&CanFrame::new(0x702, &[n2]).unwrap());
            last_hb = now2;
        }

        // 3. TPDOs at 25 Hz (40 ms)
        if now2.duration_since(last_pdo).as_secs_f32() >= 0.040 {
            angle += 0.08;
            let current_rpm = (1800.0 + 1400.0 * angle.sin()) as i32;
            {
                state.write().unwrap().sim_rpm = current_rpm;
            }
            let sim_temp = (32.0 + 8.0 * (angle * 0.3).sin()) as i32;
            let torque_val = (80.0 + 70.0 * angle.sin()) as i32;

            bus.send(&CanFrame::new(0x181, &0x0027_u16.to_le_bytes()).unwrap());

            let mut buf_281 = Vec::new();
            buf_281.extend_from_slice(&0x0027_u16.to_le_bytes());
            buf_281.extend_from_slice(&current_rpm.to_le_bytes());
            bus.send(&CanFrame::new(0x281, &buf_281).unwrap());

            let mut buf_473 = Vec::new();
            buf_473.extend_from_slice(&5000_i32.to_le_bytes());
            buf_473.extend_from_slice(&current_rpm.to_le_bytes());
            bus.send(&CanFrame::new(0x473, &buf_473).unwrap());

            let mut buf_270 = vec![3, 4, 0, 0, 0];
            buf_270.extend_from_slice(&(torque_val as i16).to_le_bytes());
            buf_270.push(0);
            bus.send(&CanFrame::new(0x270, &buf_270).unwrap());

            let mut buf_156 = vec![0, 0, 10, 0, 50, 0];
            buf_156.extend_from_slice(&(sim_temp as i16).to_le_bytes());
            bus.send(&CanFrame::new(0x156, &buf_156).unwrap());

            last_pdo = now2;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sdo::build_sdo_write;

    #[test]
    fn the_objects_served_from_live_state_are_read_only() {
        assert!(is_read_only(0x1000, 0), "device type");
        assert!(is_read_only(0x1008, 0), "device name");
        assert!(is_read_only(0x606C, 0), "velocity actual");
        assert!(!is_read_only(0x6040, 0), "controlword is writable scratch");
        assert!(!is_read_only(0x1000, 1), "only sub-index 0 is served");
    }

    #[test]
    fn an_expedited_download_of_each_width_yields_its_value() {
        for value in [
            vec![0x0F],
            vec![0x0F, 0x00],
            vec![0x0F, 0x00, 0x00],
            vec![0x0F, 0x00, 0x00, 0x00],
        ] {
            let frame = build_sdo_write(1, 0x6040, 0, &value).unwrap();
            let payload = frame.payload();
            assert_eq!(
                expedited_download_payload(payload[0], payload),
                Some(value.as_slice()),
                "download of {} byte(s)",
                value.len()
            );
        }
    }

    #[test]
    fn a_download_without_the_size_indicated_carries_all_four_bytes() {
        // ccs 1, expedited, size not indicated: cs = 0x22.
        let data = [0x22, 0x40, 0x60, 0x00, 0xDE, 0xAD, 0xBE, 0xEF];
        assert_eq!(
            expedited_download_payload(data[0], &data),
            Some(&[0xDE, 0xAD, 0xBE, 0xEF][..])
        );
    }

    #[test]
    fn an_upload_request_is_not_mistaken_for_a_download() {
        let frame = crate::sdo::build_sdo_read(1, 0x1000, 0).unwrap();
        let payload = frame.payload();
        assert_eq!(expedited_download_payload(payload[0], payload), None);
    }

    #[test]
    fn a_segmented_download_is_not_treated_as_expedited() {
        // ccs 1 with the expedited bit clear: the value follows in segments.
        let data = [0x21, 0x40, 0x60, 0x00, 0x08, 0x00, 0x00, 0x00];
        assert_eq!(expedited_download_payload(data[0], &data), None);
    }

    #[test]
    fn a_truncated_download_frame_yields_nothing_rather_than_panicking() {
        let data = [0x23, 0x40, 0x60, 0x00, 0x01];
        assert_eq!(expedited_download_payload(data[0], &data), None);
    }
}
