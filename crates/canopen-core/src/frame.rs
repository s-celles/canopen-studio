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
    #[error("Invalid CAN FD data length {0}: must be 0..=8, 12, 16, 20, 24, 32, 48 or 64")]
    InvalidFdLength(usize),
    #[error("{0} carries a CAN FD frame, which it cannot encode")]
    FdNotSupported(&'static str),
}

/// The payload length each 4-bit DLC code selects on a CAN FD frame
/// (ISO 11898-1). Codes 0..=8 mean their own value, as on classic CAN; the
/// seven codes above that are the only lengths FD offers beyond eight bytes,
/// which is why an FD payload cannot be any arbitrary size.
pub const FD_DLC_LENGTHS: [u8; 16] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64];

/// The largest payload a classic CAN 2.0A/B frame can carry.
pub const MAX_CLASSIC_PAYLOAD: usize = 8;

/// The largest payload a CAN FD frame can carry.
pub const MAX_FD_PAYLOAD: usize = 64;

/// The DLC code that names this exact length on an FD frame, if one does.
///
/// Returns `None` for a length FD cannot express — 9, 10, 11, 13 and so on.
/// Such a payload has to be padded up before it can go on the wire; see
/// [`fd_padded_length`].
pub fn fd_dlc_code(len: usize) -> Option<u8> {
    FD_DLC_LENGTHS
        .iter()
        .position(|&l| usize::from(l) == len)
        .map(|code| code as u8)
}

/// The payload length a 4-bit DLC code names on an FD frame.
///
/// Only the low nibble is read, so this cannot fail: every one of the sixteen
/// codes names a length.
pub fn fd_length_for_code(code: u8) -> u8 {
    FD_DLC_LENGTHS[usize::from(code & 0x0F)]
}

/// The smallest FD length that holds `len` bytes.
///
/// A payload of 9 bytes travels in a 12-byte frame padded with three bytes.
/// Returns `None` above 64, where nothing will hold it.
pub fn fd_padded_length(len: usize) -> Option<usize> {
    FD_DLC_LENGTHS
        .iter()
        .map(|&l| usize::from(l))
        .find(|&l| l >= len)
}

/// A frame encoded in the compact binary wire format.
///
/// Classic frames occupy 24 bytes, exactly as they did before CAN FD existed
/// here, so a peer built against the old format still reads them. An FD frame
/// needs its 64-byte payload and occupies 80; a peer that does not know the
/// format refuses it on length rather than decoding a truncated frame.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CompactBytes {
    buf: [u8; 80],
    len: u8,
}

impl CompactBytes {
    /// The encoded bytes, 24 for a classic frame and 80 for an FD one.
    #[inline]
    pub fn as_slice(&self) -> &[u8] {
        &self.buf[..usize::from(self.len)]
    }
}

impl std::ops::Deref for CompactBytes {
    type Target = [u8];

    #[inline]
    fn deref(&self) -> &[u8] {
        self.as_slice()
    }
}

impl AsRef<[u8]> for CompactBytes {
    #[inline]
    fn as_ref(&self) -> &[u8] {
        self.as_slice()
    }
}

/// A compact, stack-allocated CAN 2.0A/B or CAN FD frame with microsecond
/// timestamp. Designed for zero heap allocation and high-throughput
/// serialization.
///
/// The payload buffer holds 64 bytes so that one type carries both formats,
/// the way `python-can`'s `Message` does. That costs memory per frame — the
/// struct is 88 bytes rather than the 24 it was while classic-only — but it
/// keeps every consumer working on one type instead of an enum, and the
/// property the benchmarks actually rest on is unchanged: no allocation, no
/// garbage collector, `Copy` in and out of the ring buffer. The compact wire
/// format still spends 24 bytes on a classic frame, so network throughput for
/// classic traffic is exactly what it was.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct CanFrame {
    /// 11-bit standard or 29-bit extended CAN arbitration ID
    pub id: u32,
    /// Whether the frame uses 29-bit extended arbitration ID
    pub is_extended: bool,
    /// Remote Transmission Request (RTR) flag. CAN FD has no remote frame, so
    /// this and `is_fd` are never both set.
    pub is_remote: bool,
    /// Error frame flag
    pub is_error: bool,
    /// Whether this is a CAN FD frame (ISO 11898-1) rather than classic CAN
    pub is_fd: bool,
    /// Bit Rate Switch: the data phase ran at the faster bit rate. FD only.
    pub bitrate_switch: bool,
    /// Error State Indicator: the transmitter was error-passive. FD only.
    pub error_state_indicator: bool,
    /// Payload length in bytes — not the wire DLC code. 0..=8 on classic CAN,
    /// and one of the sixteen lengths in [`FD_DLC_LENGTHS`] on FD. Use
    /// [`CanFrame::dlc_code`] for the 4-bit value that goes on the wire.
    pub dlc: u8,
    /// Fixed 64-byte buffer covering both classic CAN and CAN FD payloads
    /// (no heap allocation)
    #[serde(with = "serde_bytes")]
    pub data: [u8; 64],
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
        if payload.len() > MAX_CLASSIC_PAYLOAD {
            return Err(CanError::InvalidDlc(payload.len()));
        }
        let mut data = [0u8; 64];
        data[..payload.len()].copy_from_slice(payload);
        Ok(Self {
            id,
            is_extended: id > 0x7FF,
            is_remote: false,
            is_error: false,
            is_fd: false,
            bitrate_switch: false,
            error_state_indicator: false,
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

    /// Create a CAN FD frame.
    ///
    /// The payload length must be one FD can name: 0..=8, 12, 16, 20, 24, 32,
    /// 48 or 64. Anything else is refused rather than padded silently, because
    /// the padding bytes reach the receiver as data and only the caller knows
    /// what they should be — [`fd_padded_length`] says how far to pad.
    pub fn new_fd(id: u32, payload: &[u8], timestamp_us: u64) -> Result<Self, CanError> {
        if fd_dlc_code(payload.len()).is_none() {
            return Err(CanError::InvalidFdLength(payload.len()));
        }
        let mut data = [0u8; 64];
        data[..payload.len()].copy_from_slice(payload);
        Ok(Self {
            id,
            is_extended: id > 0x7FF,
            is_remote: false,
            is_error: false,
            is_fd: true,
            bitrate_switch: false,
            error_state_indicator: false,
            dlc: payload.len() as u8,
            data,
            timestamp_us,
        })
    }

    /// Mark the data phase as having run at the faster bit rate.
    pub fn with_bitrate_switch(mut self, on: bool) -> Self {
        self.bitrate_switch = on;
        self
    }

    /// The 4-bit DLC code this frame puts on the wire.
    ///
    /// On FD that is the index into [`FD_DLC_LENGTHS`]; on classic CAN it is
    /// the length itself, capped at 8.
    pub fn dlc_code(&self) -> u8 {
        if self.is_fd {
            fd_dlc_code(usize::from(self.dlc)).unwrap_or(0)
        } else {
            self.dlc.min(MAX_CLASSIC_PAYLOAD as u8)
        }
    }

    /// The largest payload this frame's format allows.
    #[inline]
    pub fn max_payload(&self) -> usize {
        if self.is_fd {
            MAX_FD_PAYLOAD
        } else {
            MAX_CLASSIC_PAYLOAD
        }
    }

    /// Return a slice containing only the valid payload bytes (0..dlc).
    #[inline]
    pub fn payload(&self) -> &[u8] {
        let len = (self.dlc as usize).min(self.max_payload());
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
            is_fd: self.is_fd,
            bitrate_switch: self.bitrate_switch,
            error_state_indicator: self.error_state_indicator,
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
            is_fd: bool,
            #[serde(default)]
            bitrate_switch: bool,
            #[serde(default)]
            error_state_indicator: bool,
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

        // A peer may set is_fd on a frame of eight bytes or fewer, which is
        // legal FD; the cap follows the flag, not the length.
        let cap = if wire.is_fd {
            MAX_FD_PAYLOAD
        } else {
            MAX_CLASSIC_PAYLOAD
        };
        let mut data = [0u8; 64];
        let copy_len = wire.data.len().min(cap);
        data[..copy_len].copy_from_slice(&wire.data[..copy_len]);
        // python-can sends the length in `dlc`, but trust the payload it
        // actually carried when the two disagree.
        let dlc = wire.dlc.unwrap_or(wire.data.len() as u8).min(cap as u8);

        Ok(Self {
            id: wire.arbitration_id,
            is_extended: wire.is_extended_id,
            is_remote: wire.is_remote_frame,
            is_error: wire.is_error_frame,
            is_fd: wire.is_fd,
            bitrate_switch: wire.bitrate_switch,
            error_state_indicator: wire.error_state_indicator,
            dlc,
            data,
            timestamp_us,
        })
    }

    /// Encode into the compact binary wire format for ultra-high throughput
    /// Rust-to-Rust communication.
    ///
    /// A classic frame is the same 24 bytes it has always been, so a peer
    /// built before CAN FD existed here still reads it:
    /// - Bytes 0..4: CAN ID (u32 little-endian)
    /// - Byte 4: payload length
    /// - Byte 5: Flags (bit 0: extended, 1: remote, 2: error, 3: FD, 4: BRS, 5: ESI)
    /// - Bytes 6..8: Reserved (u16 0)
    /// - Bytes 8..16: Payload data (8 bytes)
    /// - Bytes 16..24: Timestamp µs (u64 little-endian)
    ///
    /// An FD frame sets bit 3 and occupies 80 bytes, the payload growing to
    /// 64: bytes 8..72 are data and 72..80 the timestamp. An old peer refuses
    /// it on length rather than reading a truncated frame.
    pub fn to_compact_bytes(&self) -> CompactBytes {
        let mut buf = [0u8; 80];
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
        if self.is_fd {
            flags |= 1 << 3;
        }
        if self.bitrate_switch {
            flags |= 1 << 4;
        }
        if self.error_state_indicator {
            flags |= 1 << 5;
        }
        buf[5] = flags;

        if self.is_fd {
            buf[8..72].copy_from_slice(&self.data);
            buf[72..80].copy_from_slice(&self.timestamp_us.to_le_bytes());
            CompactBytes { buf, len: 80 }
        } else {
            buf[8..16].copy_from_slice(&self.data[..8]);
            buf[16..24].copy_from_slice(&self.timestamp_us.to_le_bytes());
            CompactBytes { buf, len: 24 }
        }
    }

    /// Decode from the compact binary wire format, 24 bytes for a classic
    /// frame and 80 for an FD one.
    pub fn from_compact_bytes(bytes: &[u8]) -> Result<Self, CanError> {
        if bytes.len() < 24 {
            return Err(CanError::InvalidCompactSize(bytes.len()));
        }
        let flags = bytes[5];
        let is_fd = (flags & (1 << 3)) != 0;
        // An FD frame that arrives short is refused rather than zero-padded:
        // the missing bytes are payload, and inventing them would hand the
        // caller data no one transmitted.
        if is_fd && bytes.len() < 80 {
            return Err(CanError::InvalidCompactSize(bytes.len()));
        }

        let id = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        let cap = if is_fd {
            MAX_FD_PAYLOAD
        } else {
            MAX_CLASSIC_PAYLOAD
        };
        let dlc = bytes[4].min(cap as u8);

        let mut data = [0u8; 64];
        let (payload_end, ts_start) = if is_fd { (72, 72) } else { (16, 16) };
        data[..payload_end - 8].copy_from_slice(&bytes[8..payload_end]);
        let timestamp_us = u64::from_le_bytes([
            bytes[ts_start],
            bytes[ts_start + 1],
            bytes[ts_start + 2],
            bytes[ts_start + 3],
            bytes[ts_start + 4],
            bytes[ts_start + 5],
            bytes[ts_start + 6],
            bytes[ts_start + 7],
        ]);

        Ok(Self {
            id,
            is_extended: (flags & (1 << 0)) != 0,
            is_remote: (flags & (1 << 1)) != 0,
            is_error: (flags & (1 << 2)) != 0,
            is_fd,
            bitrate_switch: (flags & (1 << 4)) != 0,
            error_state_indicator: (flags & (1 << 5)) != 0,
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
        let mut marks = String::new();
        if self.is_fd {
            marks.push_str(" FD");
            if self.bitrate_switch {
                marks.push_str("/BRS");
            }
            if self.error_state_indicator {
                marks.push_str("/ESI");
            }
        }
        write!(
            f,
            "[{:12.6}] {} [{}]{} {}",
            (self.timestamp_us as f64) / 1_000_000.0,
            id_str,
            self.dlc,
            marks,
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
    fn every_fd_dlc_code_names_the_length_the_standard_gives_it() {
        // ISO 11898-1: codes 0..=8 are their own value, and the seven above
        // are the only lengths FD adds.
        let expected = [
            (9u8, 12u8),
            (10, 16),
            (11, 20),
            (12, 24),
            (13, 32),
            (14, 48),
            (15, 64),
        ];
        for (code, len) in expected {
            assert_eq!(fd_length_for_code(code), len, "code {code}");
            assert_eq!(fd_dlc_code(usize::from(len)), Some(code), "length {len}");
        }
        for code in 0..=8u8 {
            assert_eq!(fd_length_for_code(code), code);
        }
    }

    #[test]
    fn a_length_fd_cannot_name_has_no_code_and_pads_up_to_the_next_one() {
        for len in [9usize, 10, 11, 13, 17, 25, 33, 49] {
            assert_eq!(fd_dlc_code(len), None, "{len} is not an FD length");
        }
        assert_eq!(fd_padded_length(9), Some(12));
        assert_eq!(fd_padded_length(13), Some(16));
        assert_eq!(fd_padded_length(33), Some(48));
        assert_eq!(fd_padded_length(64), Some(64));
        assert_eq!(fd_padded_length(65), None);
    }

    #[test]
    fn an_fd_frame_carries_sixty_four_bytes() {
        let payload: Vec<u8> = (0..64u8).collect();
        let frame = CanFrame::new_fd(0x123, &payload, 1_000).unwrap();
        assert!(frame.is_fd);
        assert_eq!(frame.dlc, 64);
        assert_eq!(frame.dlc_code(), 15);
        assert_eq!(frame.payload(), &payload[..]);
        assert_eq!(frame.max_payload(), 64);
    }

    #[test]
    fn an_fd_frame_refuses_a_length_the_format_cannot_express() {
        // Nine bytes is the first length FD has no code for. Padding it is the
        // caller's call, because the pad bytes arrive as data.
        let err = CanFrame::new_fd(0x123, &[0u8; 9], 0).unwrap_err();
        assert!(matches!(err, CanError::InvalidFdLength(9)));
        assert!(CanFrame::new_fd(0x123, &[0u8; 12], 0).is_ok());
        assert!(matches!(
            CanFrame::new_fd(0x123, &[0u8; 65], 0).unwrap_err(),
            CanError::InvalidFdLength(65)
        ));
    }

    #[test]
    fn a_classic_frame_still_refuses_more_than_eight_bytes() {
        assert!(matches!(
            CanFrame::new(0x123, &[0u8; 9]).unwrap_err(),
            CanError::InvalidDlc(9)
        ));
    }

    #[test]
    fn fd_survives_the_python_can_wire_format() {
        // The encoder used to hardcode is_fd = false and the decoder dropped
        // the three FD fields, so a frame round-tripped through Python came
        // back classic.
        let payload: Vec<u8> = (0..48u8).collect();
        let frame = CanFrame::new_fd(0x18DAF110, &payload, 1_700_000_000_000_000)
            .unwrap()
            .with_bitrate_switch(true);

        let decoded =
            CanFrame::from_python_can_msgpack(&frame.to_python_can_msgpack().unwrap()).unwrap();

        assert!(decoded.is_fd);
        assert!(decoded.bitrate_switch);
        assert!(!decoded.error_state_indicator);
        assert_eq!(decoded.dlc, 48);
        assert_eq!(decoded.payload(), &payload[..]);
        assert_eq!(decoded.id, 0x18DAF110);
    }

    #[test]
    fn a_classic_frame_still_spends_only_twenty_four_bytes_on_the_wire() {
        // The whole point of keeping two lengths: classic traffic costs what
        // it always did, and an old peer still parses it.
        let frame = CanFrame::new_with_timestamp(0x180, &[1, 2, 3], 50_000).unwrap();
        assert_eq!(frame.to_compact_bytes().len(), 24);
        assert_eq!(frame.to_compact_bytes()[5] & (1 << 3), 0, "FD bit clear");
    }

    #[test]
    fn an_fd_frame_round_trips_through_the_compact_format() {
        let payload: Vec<u8> = (0..32u8).map(|b| b.wrapping_mul(7)).collect();
        let frame = CanFrame::new_fd(0x7DF, &payload, 123_456_789)
            .unwrap()
            .with_bitrate_switch(true);

        let compact = frame.to_compact_bytes();
        assert_eq!(compact.len(), 80);

        let decoded = CanFrame::from_compact_bytes(&compact).unwrap();
        assert!(decoded.is_fd);
        assert!(decoded.bitrate_switch);
        assert_eq!(decoded.dlc, 32);
        assert_eq!(decoded.payload(), &payload[..]);
        assert_eq!(decoded.timestamp_us, 123_456_789);
    }

    #[test]
    fn a_truncated_fd_frame_is_refused_rather_than_zero_padded() {
        let frame = CanFrame::new_fd(0x100, &[0xAA; 64], 1).unwrap();
        let compact = frame.to_compact_bytes();
        // What an old 24-byte-only peer would hand us.
        let err = CanFrame::from_compact_bytes(&compact[..24]).unwrap_err();
        assert!(matches!(err, CanError::InvalidCompactSize(24)));
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
