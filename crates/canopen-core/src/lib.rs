/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

pub mod canopen;
pub mod frame;
pub mod latency;
pub mod obd2;
pub mod pyo3_bindings;
pub mod ring_buffer;
pub mod sdo;
pub mod udp;

pub use canopen::{decode_canopen_frame, CanopenMessageInfo, CanopenService, NmtState};
pub use frame::{CanError, CanFrame};
pub use latency::{LatencyStats, LatencyTracker};
pub use obd2::{decode_obd2_mode01_frame, format_dtc_bytes, ObdDtc, ObdPidReading};
pub use ring_buffer::TraceRingBuffer;
pub use sdo::{
    build_sdo_abort, build_sdo_read, build_sdo_write, parse_sdo_frame, SdoAbortCode, SdoMessage,
};
pub use udp::{UdpBusError, UdpCanBus};

/// Return library version string.
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}
