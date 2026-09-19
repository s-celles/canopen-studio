/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

pub mod canopen;
pub mod eds;
pub mod frame;
pub mod isotp;
pub mod latency;
pub mod nmt;
pub mod obd2;
pub mod pdo;
pub mod pyo3_bindings;
pub mod ring_buffer;
pub mod sdo;
pub mod udp;

pub use canopen::{decode_canopen_frame, CanopenMessageInfo, CanopenService, NmtState};
pub use eds::{AccessType, DataType, DeviceInfo, EdsError, EdsFile, FileInfo, ObjectEntry};
pub use frame::{CanError, CanFrame};
pub use isotp::{fragment_isotp_message, IsoTpError, IsoTpReassembler};
pub use latency::{LatencyStats, LatencyTracker};
pub use nmt::{MonitoredNode, NmtCommand, NmtMaster};
pub use obd2::{decode_obd2_mode01_frame, format_dtc_bytes, ObdDtc, ObdPidReading};
pub use pdo::{PdoMapping, SignalDefinition, SignalReading, SignalType};
pub use ring_buffer::TraceRingBuffer;
pub use sdo::{
    build_sdo_abort, build_sdo_read, build_sdo_write, parse_sdo_frame, SdoAbortCode, SdoMessage,
};
pub use udp::{UdpBusError, UdpCanBus};

/// Return library version string.
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}
