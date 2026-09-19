/*
 * CANopen Studio — High-Performance Core Engine CLI
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use canopen_core::{
    decode_canopen_frame, decode_obd2_mode01_frame, CanFrame, LatencyTracker, UdpCanBus,
};
use clap::{Parser, Subcommand};
use std::time::Instant;

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
        #[arg(long)]
        compact: bool,
    },
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Sniff { port, target, target_port, compact } => {
            println!("==> Binding UDP CAN Bus on port {} (target: {}:{})...", port, target, target_port);
            let bus = UdpCanBus::new(port, &target, target_port, compact)?;
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
                    }
                    Err(e) => {
                        eprintln!("Receive error: {}", e);
                    }
                }
            }
        }
        Commands::BenchTx { port, target, target_port, count, compact } => {
            println!("==> Preparing to transmit {} frames to {}:{} (compact wire: {})...", count, target, target_port, compact);
            let bus = UdpCanBus::new(port, &target, target_port, compact)?;
            let frame = CanFrame::new(0x123, &[0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08])?;

            let start = Instant::now();
            for _ in 0..count {
                bus.send(&frame)?;
            }
            let elapsed = start.elapsed();
            let elapsed_sec = elapsed.as_secs_f64();
            let rate = (count as f64) / elapsed_sec;

            println!("==> Transmitted {} frames in {:.4} seconds ({:.0} frames/sec)", count, elapsed_sec, rate);
        }
        Commands::Latency { port, nominal_us, compact } => {
            println!("==> Monitoring frame latency on port {} (nominal interval: {:.1} µs)...", port, nominal_us);
            let bus = UdpCanBus::new(port, "127.0.0.1", port, compact)?;
            let tracker = LatencyTracker::new(Some(nominal_us));

            let mut count = 0u64;
            loop {
                match bus.recv() {
                    Ok((frame, _)) => {
                        count += 1;
                        tracker.record_frame_timestamp(frame.timestamp_us);
                        if count % 50 == 0 {
                            let stats = tracker.stats();
                            println!(
                                "Samples: {:6} | Min: {:7.1} µs | Max: {:7.1} µs | Avg: {:7.1} µs | Jitter: {:6.1} µs | StdDev: {:6.1} µs",
                                stats.count, stats.min_us, stats.max_us, stats.avg_us, stats.jitter_us, stats.std_dev_us
                            );
                        }
                    }
                    Err(e) => {
                        eprintln!("Receive error: {}", e);
                    }
                }
            }
        }
    }

    #[allow(unreachable_code)]
    Ok(())
}
