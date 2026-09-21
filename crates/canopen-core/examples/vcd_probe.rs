/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

//! Write a small reconstructed waveform, to check it against sigrok.
//!
//! The unit tests prove the bits are self-consistent — the CRC checks to zero,
//! the stuffing holds. They cannot prove sigrok's decoder reads back the frames
//! we put in. This writes a file for that:
//!
//! ```text
//! cargo run -p canopen-core --example vcd_probe -- out.vcd
//! sigrok-cli -i out.vcd -I vcd -P can:nominal_bitrate=500000 -A can=fields
//! ```

use std::env;
use std::fs::File;

use canopen_core::bitstream::AckSlot;
use canopen_core::frame::CanFrame;
use canopen_core::vcd::{Timing, VcdOptions, VcdWriter};

fn main() -> std::io::Result<()> {
    let path = env::args()
        .nth(1)
        .unwrap_or_else(|| "probe.vcd".to_string());

    let base = 1_000_000u64;
    let frames = vec![
        CanFrame::new_with_timestamp(0x080, &[], base).unwrap(), // SYNC
        CanFrame::new_with_timestamp(0x701, &[0x05], base + 1_000).unwrap(), // heartbeat
        CanFrame::new_with_timestamp(0x181, &[0x10, 0x27, 0x00, 0x00], base + 2_000).unwrap(),
        CanFrame::new_with_timestamp(0x601, &[0x40, 0x00, 0x10, 0x00, 0, 0, 0, 0], base + 3_000)
            .unwrap(),
        // The all-zero frame, which forces stuff bits.
        CanFrame::new_with_timestamp(0x000, &[0x00, 0x00], base + 4_000).unwrap(),
    ];

    let options = VcdOptions {
        bitrate: 500_000,
        tick_ns: 100,
        timing: Timing::Timestamps,
        ack: AckSlot::Acknowledged,
    };

    let mut writer = VcdWriter::new(File::create(&path)?, options)?;
    for frame in &frames {
        writer.write_frame(frame)?;
    }
    let (written, displaced) = writer.finish()?;

    println!("wrote {written} frames to {path} ({displaced} displaced)");
    for frame in &frames {
        println!("  0x{:03X} dlc={}", frame.id, frame.dlc);
    }
    Ok(())
}
