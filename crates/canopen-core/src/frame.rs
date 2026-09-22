/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use serde::{Deserialize, Serialize};
use std::fmt;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(thiserror::Error, Debug)]
pub enum CanError {
    #[error("Invalid CAN data length {0}: must be 0..=8 for classic CAN")]
    InvalidDlc(usize),
    #[error("Invalid CAN identifier {0:#X}: wider than the frame format allows")]
    InvalidId(u32),
    #[error("Failed to decode MessagePack CAN frame: {0}")]
    MsgpackDecodeError(String),
    #[error("Failed to encode MessagePack CAN frame: {0}")]
    MsgpackEncodeError(String),
    #[error("Invalid compact frame size: expected at least 16 bytes, got {0}")]
    InvalidCompactSize(usize),
}

/// A compact, stack-allocated CAN 2.0A/B frame with microsecond timestamp.
/// Designed for zero heap allocation and high-throughput serialization.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct CanFrame {
    /// 11-bit standard or 29-bit extended CAN arbitration ID
    pub id: u32,
    /// Whether the frame uses 29-bit extended arbitration ID
    pub is_extended: bool,
    /// Remote Transmission Request (RTR) flag
    pub is_remote: bool,
    /// Error frame flag
    pub is_error: bool,
    /// Data Length Code (0..=8 for classic CAN)
    pub dlc: u8,
    /// Fixed 8-byte buffer for classic CAN payload (no heap allocation)
    pub data: [u8; 8],
    /// Microsecond epoch timestamp (µs since Unix epoch)
    pub timestamp_us: u64,
}

impl CanFrame {
    /// Create a new standard CAN frame with the current system time.
    pub fn new(id: u32, payload: &[u8]) -> Result<Self, CanError> {
        let now_us = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_micros() as u64;
        Self::new_with_timestamp(id, payload, now_us)
    }

    /// Create a new standard CAN frame with an explicit microsecond timestamp.
    pub fn new_with_timestamp(
        id: u32,
        payload: &[u8],
        timestamp_us: u64,
    ) -> Result<Self, CanError> {
        if payload.len() > 8 {
            return Err(CanError::InvalidDlc(payload.len()));
        }
        let mut data = [0u8; 8];
        data[..payload.len()].copy_from_slice(payload);
        Ok(Self {
            id,
            is_extended: id > 0x7FF,
            is_remote: false,
            is_error: false,
            dlc: payload.len() as u8,
            data,
            timestamp_us,
        })
    }

    /// Create an extended 29-bit CAN frame.
    pub fn new_extended(id: u32, payload: &[u8], timestamp_us: u64) -> Result<Self, CanError> {
        let mut frame = Self::new_with_timestamp(id, payload, timestamp_us)?;
        frame.is_extended = true;
        Ok(frame)
    }

    /// Return a slice containing only the valid payload bytes (0..dlc).
    #[inline]
    pub fn payload(&self) -> &[u8] {
        let len = (self.dlc as usize).min(8);
        &self.data[..len]
    }

    /// Encode into the exact MessagePack format used by `python-can` UDP multicast/unicast.
    /// This enables drop-in interoperability with Python's `UdpBus`.
    pub fn to_python_can_msgpack(&self) -> Result<Vec<u8>, CanError> {
        #[derive(Serialize)]
        struct PythonCanWire<'a> {
            timestamp: f64,
            arbitration_id: u32,
            is_extended_id: bool,
            is_remote_frame: bool,
            is_error_frame: bool,
            channel: Option<&'a str>,
            dlc: u8,
            #[serde(with = "serde_bytes")]
            data: &'a [u8],
            is_fd: bool,
            bitrate_switch: bool,
            error_state_indicator: bool,
        }

        let wire = PythonCanWire {
            timestamp: (self.timestamp_us as f64) / 1_000_000.0,
            arbitration_id: self.id,
            is_extended_id: self.is_extended,
            is_remote_frame: self.is_remote,
            is_error_frame: self.is_error,
            channel: None,
            dlc: self.dlc,
            data: self.payload(),
            is_fd: false,
            bitrate_switch: false,
            error_state_indicator: false,
        };

        rmp_serde::to_vec_named(&wire).map_err(|e| CanError::MsgpackEncodeError(e.to_string()))
    }

    /// Decode from `python-can` MessagePack wire format.
    pub fn from_python_can_msgpack(bytes: &[u8]) -> Result<Self, CanError> {
        #[derive(Deserialize)]
        struct PythonCanWire {
            timestamp: Option<f64>,
            arbitration_id: u32,
            #[serde(default)]
            is_extended_id: bool,
            #[serde(default)]
            is_remote_frame: bool,
            #[serde(default)]
            is_error_frame: bool,
            #[serde(default)]
            dlc: Option<u8>,
            #[serde(with = "serde_bytes")]
            data: Vec<u8>,
        }

        let wire: PythonCanWire = rmp_serde::from_slice(bytes)
            .map_err(|e| CanError::MsgpackDecodeError(e.to_string()))?;

        let timestamp_us = wire
            .timestamp
            .map(|t| (t * 1_000_000.0).max(0.0) as u64)
            .unwrap_or_else(|| {
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_micros() as u64
            });

        let mut data = [0u8; 8];
        let dlc = wire.dlc.unwrap_or(wire.data.len() as u8);
        let copy_len = wire.data.len().min(8);
        data[..copy_len].copy_from_slice(&wire.data[..copy_len]);

        Ok(Self {
            id: wire.arbitration_id,
            is_extended: wire.is_extended_id,
            is_remote: wire.is_remote_frame,
            is_error: wire.is_error_frame,
            dlc: dlc.min(8),
            data,
            timestamp_us,
        })
    }

    /// Encode into a compact 24-byte binary wire format for ultra-high throughput Rust-to-Rust communication:
    /// - Bytes 0..4: CAN ID (u32 little-endian)
    /// - Byte 4: DLC (u8)
    /// - Byte 5: Flags (bit 0: extended, bit 1: remote, bit 2: error)
    /// - Bytes 6..8: Reserved (u16 0)
    /// - Bytes 8..16: Payload data (8 bytes)
    /// - Bytes 16..24: Timestamp µs (u64 little-endian)
    pub fn to_compact_bytes(&self) -> [u8; 24] {
        let mut buf = [0u8; 24];
        buf[0..4].copy_from_slice(&self.id.to_le_bytes());
        buf[4] = self.dlc;
        let mut flags = 0u8;
        if self.is_extended {
            flags |= 1 << 0;
        }
        if self.is_remote {
            flags |= 1 << 1;
        }
        if self.is_error {
            flags |= 1 << 2;
        }
        buf[5] = flags;
        buf[8..16].copy_from_slice(&self.data);
        buf[16..24].copy_from_slice(&self.timestamp_us.to_le_bytes());
        buf
    }

    /// Decode from compact 24-byte binary wire format.
    pub fn from_compact_bytes(bytes: &[u8]) -> Result<Self, CanError> {
        if bytes.len() < 24 {
            return Err(CanError::InvalidCompactSize(bytes.len()));
        }
        let id = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        let dlc = bytes[4].min(8);
        let flags = bytes[5];
        let mut data = [0u8; 8];
        data.copy_from_slice(&bytes[8..16]);
        let timestamp_us = u64::from_le_bytes([
            bytes[16], bytes[17], bytes[18], bytes[19], bytes[20], bytes[21], bytes[22], bytes[23],
        ]);

        Ok(Self {
            id,
            is_extended: (flags & (1 << 0)) != 0,
            is_remote: (flags & (1 << 1)) != 0,
            is_error: (flags & (1 << 2)) != 0,
            dlc,
            data,
            timestamp_us,
        })
    }
}

impl fmt::Display for CanFrame {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let id_str = if self.is_extended {
            format!("{:08X}", self.id)
        } else {
            format!("{:03X}", self.id)
        };
        let mut data_str = String::new();
        for b in self.payload() {
            if !data_str.is_empty() {
                data_str.push(' ');
            }
            data_str.push_str(&format!("{:02X}", b));
        }
        write!(
            f,
            "[{:12.6}] {} [{}] {}",
            (self.timestamp_us as f64) / 1_000_000.0,
            id_str,
            self.dlc,
            data_str
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_can_frame_creation() {
        let frame = CanFrame::new(0x123, &[0x11, 0x22, 0x33, 0x44]).unwrap();
        assert_eq!(frame.id, 0x123);
        assert_eq!(frame.dlc, 4);
        assert_eq!(frame.payload(), &[0x11, 0x22, 0x33, 0x44]);
        assert!(!frame.is_extended);
    }

    #[test]
    fn test_extended_frame() {
        let frame = CanFrame::new(0x18DAF110, &[0x02, 0x01, 0x0C]).unwrap();
        assert_eq!(frame.id, 0x18DAF110);
        assert!(frame.is_extended);
        assert_eq!(frame.dlc, 3);
    }

    #[test]
    fn test_msgpack_roundtrip_with_python_can() {
        let frame =
            CanFrame::new_with_timestamp(0x7DF, &[0x02, 0x01, 0x0D, 0x00], 1_700_000_000_123_456)
                .unwrap();
        let packed = frame.to_python_can_msgpack().unwrap();
        let decoded = CanFrame::from_python_can_msgpack(&packed).unwrap();

        assert_eq!(decoded.id, 0x7DF);
        assert_eq!(decoded.dlc, 4);
        assert_eq!(decoded.payload(), &[0x02, 0x01, 0x0D, 0x00]);
        assert_eq!(decoded.timestamp_us, 1_700_000_000_123_456);
    }

    #[test]
    fn test_compact_bytes_roundtrip() {
        let frame = CanFrame::new_with_timestamp(0x180, &[0x00], 50_000).unwrap();
        let compact = frame.to_compact_bytes();
        assert_eq!(compact.len(), 24);
        let decoded = CanFrame::from_compact_bytes(&compact).unwrap();
        assert_eq!(decoded.id, 0x180);
        assert_eq!(decoded.dlc, 1);
        assert_eq!(decoded.payload(), &[0x00]);
        assert_eq!(decoded.timestamp_us, 50_000);
    }
}
