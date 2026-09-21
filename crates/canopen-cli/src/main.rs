/*
 * CANopen Studio — High-Performance Core Engine CLI
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use canopen_core::{
    CanFrame, LatencyTracker, PcapNgWriter, UdpCanBus, decode_canopen_frame,
    decode_obd2_mode01_frame, simulator_ext::spawn_udp_simulator,
};
use clap::{Parser, Subcommand};
use std::time::{Duration, Instant};

#[derive(Parser, Debug)]
#[command(name = "canopen-cli")]
#[command(about = "High-performance CANopen & OBD-II sniffer, latency monitor, and benchmark tool", long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Sniff CAN frames on a UDP socket and print decoded telemetry in real-time
    Sniff {
        #[arg(short, long, default_value_t = 1750)]
        port: u16,
        #[arg(long, default_value = "127.0.0.1")]
        target: String,
        #[arg(long, default_value_t = 1750)]
        target_port: u16,
        #[arg(long)]
        compact: bool,
        /// Also write the frames as PCAP-NG, for Wireshark. Give a file to
        /// record to, or a named pipe to stream into a live capture.
        #[arg(long, value_name = "FILE|PIPE")]
        pcap: Option<String>,
    },
    /// Generate high-speed test CAN frames to benchmark throughput (frames/sec)
    BenchTx {
        #[arg(short, long, default_value_t = 1750)]
        port: u16,
        #[arg(long, default_value = "127.0.0.1")]
        target: String,
        #[arg(long, default_value_t = 1750)]
        target_port: u16,
        #[arg(short, long, default_value_t = 100_000)]
        count: u64,
        #[arg(long)]
        compact: bool,
    },
    /// Monitor frame intervals and measure jitter on periodic traffic (e.g. 50 Hz SYNC = 20,000 µs)
    Latency {
        #[arg(short, long, default_value_t = 1750)]
        port: u16,
        #[arg(long, default_value_t = 20_000.0)]
        nominal_us: f64,
        /// Only measure frames with this CAN ID (hex, e.g. 0x080 for SYNC). Measures all frames if omitted.
        #[arg(long)]
        filter_id: Option<String>,
        /// Multicast group to join (e.g. 239.0.0.1). Omit for broadcast/unicast.
        #[arg(long)]
        group: Option<String>,
        #[arg(long)]
        compact: bool,
    },
    /// Send N CAN ping frames (0x7E0) and measure RTT from echo (0x7E1)
    BenchPing {
        #[arg(short, long, default_value_t = 1750)]
        port: u16,
        #[arg(long, default_value = "127.0.0.1")]
        target: String,
        #[arg(long, default_value_t = 1750)]
        target_port: u16,
        /// Number of pings to send
        #[arg(short, long, default_value_t = 10)]
        count: u32,
        /// Interval between pings in milliseconds
        #[arg(short, long, default_value_t = 100)]
        interval_ms: u64,
        /// Per-ping timeout in milliseconds
        #[arg(long, default_value_t = 2000)]
        timeout_ms: u64,
        #[arg(long)]
        compact: bool,
    },
    /// Run the virtual CANopen simulator headlessly (SYNC 50 Hz, Heartbeat 1 Hz, TPDOs 25 Hz)
    Simulate {
        #[arg(short, long, default_value_t = 1750)]
        port: u16,
        #[arg(long, default_value = "127.0.0.1")]
        target: String,
        /// Duration in seconds (0 = run forever)
        #[arg(short, long, default_value_t = 0)]
        duration_secs: u64,
    },
    /// Auto-echo responder: reply to 0x7E0 ping frames with 0x7E1 (run on the remote machine)
    Echo {
        #[arg(short, long, default_value_t = 1750)]
        port: u16,
        #[arg(long, default_value = "127.0.0.1")]
        target: String,
        #[arg(long, default_value_t = 1750)]
        target_port: u16,
        #[arg(long)]
        compact: bool,
    },
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Sniff {
            port,
            target,
            target_port,
            compact,
            pcap,
        } => {
            println!(
                "==> Binding UDP CAN Bus on port {} (target: {}:{})...",
                port, target, target_port
            );
            let bus = UdpCanBus::new(port, &target, target_port, compact)?;

            // Opening a pipe blocks until Wireshark is on the other end, so
            // say what the silence means before it starts.
            let mut writer = match pcap.as_deref() {
                Some(path) => {
                    println!("==> Writing PCAP-NG to {path}");
                    if canopen_core::pcap::sink_waits_for_reader(path) {
                        println!("    (a pipe: this waits until a capture opens it)");
                    }
                    let sink = canopen_core::pcap::open_capture_sink(path)?;
                    let w = PcapNgWriter::new(sink, "canopen-cli")?;
                    println!("==> Capture open");
                    Some(w)
                }
                None => None,
            };

            println!("==> Listening for CAN frames (press Ctrl+C to exit)...");

            loop {
                match bus.recv() {
                    Ok((frame, src)) => {
                        let canopen_info = decode_canopen_frame(&frame);
                        let obd_info = decode_obd2_mode01_frame(&frame);

                        let extra = if let Some(obd) = obd_info {
                            format!(" | OBD-II: {} = {:.2} {}", obd.name, obd.value, obd.unit)
                        } else {
                            format!(" | {}", canopen_info.description)
                        };

                        println!("{} from {}{}", frame, src, extra);

                        if let Some(w) = writer.as_mut()
                            && let Err(e) = w.write_frame(&frame)
                        {
                            // A capture that has gone away is the normal way
                            // this ends, not a failure worth a loud loop.
                            eprintln!("==> Capture closed ({e}); continuing without it");
                            writer = None;
                        }
                    }
                    Err(e) => {
                        eprintln!("Receive error: {}", e);
                    }
                }
            }
        }
        Commands::BenchTx {
            port,
            target,
            target_port,
            count,
            compact,
        } => {
            println!(
                "==> Preparing to transmit {} frames to {}:{} (compact wire: {})...",
                count, target, target_port, compact
            );
            let bus = UdpCanBus::new(port, &target, target_port, compact)?;
            let frame = CanFrame::new(0x123, &[0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08])?;

            let start = Instant::now();
            for _ in 0..count {
                bus.send(&frame)?;
            }
            let elapsed = start.elapsed();
            let elapsed_sec = elapsed.as_secs_f64();
            let rate = (count as f64) / elapsed_sec;

            println!(
                "==> Transmitted {} frames in {:.4} seconds ({:.0} frames/sec)",
                count, elapsed_sec, rate
            );
        }
        Commands::Latency {
            port,
            nominal_us,
            filter_id,
            group,
            compact,
        } => {
            let filter: Option<u32> = filter_id.as_deref().map(|s| {
                let s = s.trim_start_matches("0x").trim_start_matches("0X");
                u32::from_str_radix(s, 16).expect("invalid hex CAN ID")
            });
            let bind_target = group.as_deref().unwrap_or("0.0.0.0");
            match filter {
                Some(id) => println!(
                    "==> Monitoring jitter on port {} — CAN ID 0x{:03X} only (nominal: {:.1} µs, group: {})...",
                    port, id, nominal_us, bind_target
                ),
                None => println!(
                    "==> Monitoring frame latency on port {} (nominal interval: {:.1} µs, all IDs, group: {})...",
                    port, nominal_us, bind_target
                ),
            }
            let bus = UdpCanBus::new(port, bind_target, port, compact)?;
            let tracker = LatencyTracker::new(Some(nominal_us));

            let mut count = 0u64;
            loop {
                match bus.recv() {
                    Ok((frame, _)) => {
                        if filter.is_none_or(|id| frame.id == id) {
                            count += 1;
                            tracker.record_frame_timestamp(frame.timestamp_us);
                            if count.is_multiple_of(50) {
                                let stats = tracker.stats();
                                println!(
                                    "Samples: {:6} | Min: {:7.1} µs | Max: {:7.1} µs | Avg: {:7.1} µs | Jitter: {:6.1} µs | StdDev: {:6.1} µs",
                                    stats.count,
                                    stats.min_us,
                                    stats.max_us,
                                    stats.avg_us,
                                    stats.jitter_us,
                                    stats.std_dev_us
                                );
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("Receive error: {}", e);
                    }
                }
            }
        }
        Commands::BenchPing {
            port,
            target,
            target_port,
            count,
            interval_ms,
            timeout_ms,
            compact,
        } => {
            const PING_ID: u32 = 0x7E0;
            const PONG_ID: u32 = 0x7E1;

            println!(
                "==> PING {}:{} — {} pings, interval {}ms, timeout {}ms",
                target, target_port, count, interval_ms, timeout_ms
            );
            let bus = UdpCanBus::new(port, &target, target_port, compact)?;
            bus.set_read_timeout(Some(Duration::from_millis(timeout_ms)))?;

            let mut rtt_samples: Vec<f64> = Vec::with_capacity(count as usize);
            let mut lost = 0u32;

            for seq in 1..=count {
                let now = Instant::now();
                let frac = now.elapsed().subsec_micros();
                let mut payload = [0u8; 8];
                payload[..4].copy_from_slice(&seq.to_le_bytes());
                payload[4..].copy_from_slice(&frac.to_le_bytes());

                let ping = CanFrame::new(PING_ID, &payload)?;
                let sent_at = Instant::now();
                bus.send(&ping)?;

                // Wait for matching 0x7E1 response
                let rtt = loop {
                    match bus.recv() {
                        Ok((frame, _)) => {
                            if frame.id == PONG_ID && frame.payload().len() >= 4 {
                                let resp_seq =
                                    u32::from_le_bytes(frame.payload()[..4].try_into().unwrap());
                                if resp_seq == seq {
                                    break Some(sent_at.elapsed().as_secs_f64() * 1000.0);
                                }
                            }
                            // Not our pong — keep waiting unless timed out
                            if sent_at.elapsed() >= Duration::from_millis(timeout_ms) {
                                break None;
                            }
                        }
                        Err(_) => break None, // timeout
                    }
                };

                match rtt {
                    Some(ms) => {
                        println!("seq={:3}  rtt={:.2} ms", seq, ms);
                        rtt_samples.push(ms);
                    }
                    None => {
                        println!("seq={:3}  timeout", seq);
                        lost += 1;
                    }
                }

                if seq < count {
                    std::thread::sleep(Duration::from_millis(interval_ms));
                }
            }

            println!();
            if !rtt_samples.is_empty() {
                let min = rtt_samples.iter().cloned().fold(f64::INFINITY, f64::min);
                let max = rtt_samples
                    .iter()
                    .cloned()
                    .fold(f64::NEG_INFINITY, f64::max);
                let avg = rtt_samples.iter().sum::<f64>() / rtt_samples.len() as f64;
                let variance = rtt_samples.iter().map(|x| (x - avg).powi(2)).sum::<f64>()
                    / rtt_samples.len() as f64;
                let stddev = variance.sqrt();
                println!("--- ping statistics ---");
                println!(
                    "{} sent, {} received, {} lost ({:.0}% loss)",
                    count,
                    rtt_samples.len(),
                    lost,
                    lost as f64 / count as f64 * 100.0
                );
                println!(
                    "rtt min/avg/max/stddev = {:.2}/{:.2}/{:.2}/{:.2} ms",
                    min, avg, max, stddev
                );
            } else {
                println!("--- ping statistics ---");
                println!("{} sent, 0 received, 100% loss", count);
            }
        }
        Commands::Simulate {
            port,
            target,
            duration_secs,
        } => {
            println!(
                "==> Rust virtual simulator → {}:{} (SYNC 50 Hz, HB 1 Hz, TPDOs 25 Hz)",
                target, port
            );
            spawn_udp_simulator(&target, port);
            if duration_secs == 0 {
                println!("==> Running indefinitely (Ctrl+C to stop)...");
                loop {
                    std::thread::sleep(Duration::from_secs(3600));
                }
            } else {
                println!("==> Running for {} seconds...", duration_secs);
                std::thread::sleep(Duration::from_secs(duration_secs));
                println!("==> Done.");
            }
        }
        Commands::Echo {
            port,
            target,
            target_port,
            compact,
        } => {
            const PING_ID: u32 = 0x7E0;
            const PONG_ID: u32 = 0x7E1;

            println!(
                "==> Echo responder on port {} (replying 0x{:03X} → 0x{:03X}, press Ctrl+C to stop)...",
                port, PING_ID, PONG_ID
            );
            let bus = UdpCanBus::new(port, &target, target_port, compact)?;

            loop {
                if let Ok((frame, src)) = bus.recv()
                    && frame.id == PING_ID
                {
                    let pong = CanFrame::new(PONG_ID, frame.payload())?;
                    bus.send(&pong)?;
                    let seq = if frame.payload().len() >= 4 {
                        u32::from_le_bytes(frame.payload()[..4].try_into().unwrap())
                    } else {
                        0
                    };
                    println!("echo seq={} from {}", seq, src);
                }
            }
        }
    }

    #[allow(unreachable_code)]
    Ok(())
}
