/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

pub mod bitstream;
pub mod canopen;
pub mod eds;
pub mod frame;
pub mod isotp;
pub mod latency;
pub mod nmt;
pub mod obd2;
pub mod pcap;
pub mod pdo;
pub mod pyo3_bindings;
pub mod ring_buffer;
pub mod sdo;
pub mod simulator;
pub mod simulator_ext;
pub mod slcan;
pub mod telemetry;
pub mod udp;
pub mod vcd;

pub use bitstream::{AckSlot, crc15, frame_bits, stuff};
pub use canopen::{CanopenMessageInfo, CanopenService, NmtState, decode_canopen_frame};
pub use eds::{AccessType, DataType, DeviceInfo, EdsError, EdsFile, FileInfo, ObjectEntry};
pub use frame::{CanError, CanFrame};
pub use isotp::{IsoTpError, IsoTpReassembler, fragment_isotp_message};
pub use latency::{LatencyStats, LatencyTracker};
pub use nmt::{MonitoredNode, NmtCommand, NmtMaster};
pub use obd2::{ObdDtc, ObdPidReading, decode_obd2_mode01_frame, format_dtc_bytes};
pub use pcap::{LINKTYPE_CAN_SOCKETCAN, PcapNgWriter, encode_socketcan};
pub use pdo::{PdoMapping, SignalDefinition, SignalReading, SignalType};
pub use ring_buffer::TraceRingBuffer;
pub use sdo::{
    SdoAbortCode, SdoMessage, build_sdo_abort, build_sdo_read, build_sdo_write, parse_sdo_frame,
};
pub use telemetry::{Cia402State, DriveTelemetry};
pub use udp::{UdpBusError, UdpCanBus};
pub use vcd::{Timing, VcdOptions, VcdWriter};

/// Return library version string.
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}
pub mod isotp_manager;
