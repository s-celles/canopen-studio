/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * ISO-TP (ISO 15765-2) Multi-Frame Transport Layer Protocol.
 * Used for automotive diagnostics (SAE J1979 OBD-II & ISO 14229 UDS).
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use crate::frame::{CanError, CanFrame};
use serde::{Deserialize, Serialize};

#[derive(thiserror::Error, Debug, PartialEq, Eq)]
pub enum IsoTpError {
    #[error("Invalid Single Frame length {0}: must be 1..=7")]
    InvalidSingleFrameLength(usize),
    #[error("Invalid First Frame length {0}: must be >= 8 and <= 4095")]
    InvalidFirstFrameLength(usize),
    #[error("Unexpected Consecutive Frame sequence number: expected {expected}, got {received}")]
    SequenceNumberMismatch { expected: u8, received: u8 },
    #[error("Payload length exceeded buffer limit: {0}")]
    PayloadTooLarge(usize),
    #[error("Received frame with invalid ISO-TP PCI byte: 0x{0:02X}")]
    InvalidPci(u8),
    #[error("No active multi-frame reception in progress")]
    NoTransferInProgress,
    #[error("CAN error: {0}")]
    Can(String),
}

impl From<CanError> for IsoTpError {
    fn from(err: CanError) -> Self {
        IsoTpError::Can(err.to_string())
    }
}

/// ISO-TP Protocol Control Information (PCI) Frame Types.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum IsoTpFrameType {
    SingleFrame,
    FirstFrame,
    ConsecutiveFrame,
    FlowControl,
}

/// Flow Control Status values.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum FlowStatus {
    ClearToSend = 0,
    Wait = 1,
    Overflow = 2,
}

/// ISO-TP message reassembly state machine for receiving diagnostic transfers.
pub struct IsoTpReassembler {
    expected_rx_id: u32,
    tx_fc_id: u32,
    expected_total_len: usize,
    buffer: Vec<u8>,
    expected_sn: u8,
    in_progress: bool,
}

impl IsoTpReassembler {
    /// Create a reassembler listening on `expected_rx_id` (e.g. ECU response 0x7E8)
    /// and generating Flow Control frames to `tx_fc_id` (e.g. Tester 0x7E0).
    pub fn new(expected_rx_id: u32, tx_fc_id: u32) -> Self {
        Self {
            expected_rx_id,
            tx_fc_id,
            expected_total_len: 0,
            buffer: Vec::with_capacity(512),
            expected_sn: 1,
            in_progress: false,
        }
    }

    /// Reset internal reassembly state.
    pub fn reset(&mut self) {
        self.expected_total_len = 0;
        self.buffer.clear();
        self.expected_sn = 1;
        self.in_progress = false;
    }

    /// Whether a multi-frame transfer is currently in progress.
    pub fn is_transfer_in_progress(&self) -> bool {
        self.in_progress
    }

    /// Process an incoming CAN frame.
    /// Returns:
    /// - `Ok((Some(payload), None))` if a complete message was received (Single Frame or last Consecutive Frame).
    /// - `Ok((None, Some(fc_frame)))` if a First Frame was received and a Flow Control frame must be sent.
    /// - `Ok((None, None))` if a Consecutive Frame was received and more frames are expected.
    /// - `Err(IsoTpError)` on protocol error.
    pub fn process_frame(
        &mut self,
        frame: &CanFrame,
    ) -> Result<(Option<Vec<u8>>, Option<CanFrame>), IsoTpError> {
        if frame.id != self.expected_rx_id {
            return Ok((None, None));
        }

        let payload = frame.payload();
        if payload.is_empty() {
            return Err(IsoTpError::InvalidPci(0));
        }

        let pci_byte = payload[0];
        let pci_type = (pci_byte >> 4) & 0x0F;

        match pci_type {
            0 => {
                // Single Frame (SF)
                let len = (pci_byte & 0x0F) as usize;
                if len == 0 || len > 7 || len > (payload.len() - 1) {
                    return Err(IsoTpError::InvalidSingleFrameLength(len));
                }
                self.reset();
                let complete_msg = payload[1..1 + len].to_vec();
                Ok((Some(complete_msg), None))
            }
            1 => {
                // First Frame (FF)
                if payload.len() < 8 {
                    return Err(IsoTpError::InvalidPci(pci_byte));
                }
                let total_len = (((pci_byte & 0x0F) as usize) << 8) | (payload[1] as usize);
                if !(8..=4095).contains(&total_len) {
                    return Err(IsoTpError::InvalidFirstFrameLength(total_len));
                }

                self.reset();
                self.expected_total_len = total_len;
                self.expected_sn = 1;
                self.in_progress = true;

                // Copy initial chunk from bytes 2..8
                let initial_data = &payload[2..];
                self.buffer.extend_from_slice(initial_data);

                // Construct Flow Control (CTS: Clear to Send, Block Size = 0, STmin = 0 ms)
                let fc_payload = [0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00];
                let fc_frame = CanFrame::new(self.tx_fc_id, &fc_payload)?;

                Ok((None, Some(fc_frame)))
            }
            2 => {
                // Consecutive Frame (CF)
                if !self.in_progress {
                    return Err(IsoTpError::NoTransferInProgress);
                }

                let sn = pci_byte & 0x0F;
                if sn != self.expected_sn {
                    let expected = self.expected_sn;
                    self.reset();
                    return Err(IsoTpError::SequenceNumberMismatch {
                        expected,
                        received: sn,
                    });
                }

                // Sequence numbers wrap from 0xF to 0x0
                self.expected_sn = (self.expected_sn + 1) & 0x0F;

                let remaining = self.expected_total_len - self.buffer.len();
                let chunk_len = remaining.min(payload.len() - 1);
                self.buffer.extend_from_slice(&payload[1..1 + chunk_len]);

                if self.buffer.len() >= self.expected_total_len {
                    let result = self.buffer.clone();
                    self.reset();
                    Ok((Some(result), None))
                } else {
                    Ok((None, None))
                }
            }
            3 => {
                // Flow Control (FC)
                // Flow control received by tester when transmitting; ignored during reception
                Ok((None, None))
            }
            other => Err(IsoTpError::InvalidPci(other)),
        }
    }
}

/// Fragment a diagnostic message into one or more ISO-TP CAN frames.
pub fn fragment_isotp_message(tx_id: u32, data: &[u8]) -> Result<Vec<CanFrame>, IsoTpError> {
    if data.is_empty() {
        return Err(IsoTpError::InvalidSingleFrameLength(0));
    }

    if data.len() <= 7 {
        // Single Frame (SF): [0x00 | len, d0..dn, padding...]
        let mut payload = [0x00u8; 8];
        payload[0] = data.len() as u8;
        payload[1..1 + data.len()].copy_from_slice(data);
        let frame = CanFrame::new(tx_id, &payload)?;
        return Ok(vec![frame]);
    }

    if data.len() > 4095 {
        return Err(IsoTpError::PayloadTooLarge(data.len()));
    }

    // Multi-Frame: First Frame (FF) followed by Consecutive Frames (CF)
    let mut frames = Vec::new();

    // 1. First Frame (FF): [0x10 | (len >> 8), (len & 0xFF), d0..d5]
    let total_len = data.len();
    let mut ff_payload = [0x00u8; 8];
    ff_payload[0] = 0x10 | ((total_len >> 8) as u8 & 0x0F);
    ff_payload[1] = (total_len & 0xFF) as u8;
    ff_payload[2..8].copy_from_slice(&data[..6]);
    frames.push(CanFrame::new(tx_id, &ff_payload)?);

    // 2. Consecutive Frames (CF): [0x20 | (sn & 0x0F), d0..d6]
    let mut offset = 6;
    let mut sn = 1u8;

    while offset < total_len {
        let chunk_len = (total_len - offset).min(7);
        let mut cf_payload = [0x00u8; 8];
        cf_payload[0] = 0x20 | (sn & 0x0F);
        cf_payload[1..1 + chunk_len].copy_from_slice(&data[offset..offset + chunk_len]);
        frames.push(CanFrame::new(tx_id, &cf_payload)?);

        offset += chunk_len;
        sn = (sn + 1) & 0x0F;
    }

    Ok(frames)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_single_frame_roundtrip() {
        let original_data = vec![0x02, 0x01, 0x0C]; // Mode 01 PID 0C (RPM request)
        let frames = fragment_isotp_message(0x7DF, &original_data).unwrap();
        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0].id, 0x7DF);
        assert_eq!(
            frames[0].payload(),
            &[0x03, 0x02, 0x01, 0x0C, 0x00, 0x00, 0x00, 0x00]
        );

        let mut reassembler = IsoTpReassembler::new(0x7DF, 0x7E8);
        let (res, fc) = reassembler.process_frame(&frames[0]).unwrap();
        assert!(fc.is_none());
        assert_eq!(res, Some(original_data));
    }

    #[test]
    fn test_multi_frame_reassembly_vin() {
        // Simulated 17-character VIN response (20 bytes total with Mode/PID bytes):
        // Mode 09 PID 02: 49 02 01 + "1FA6P8CF4H5100000" (17 ascii bytes)
        let mut vin_payload = vec![0x49, 0x02, 0x01];
        vin_payload.extend_from_slice(b"1FA6P8CF4H5100000");
        assert_eq!(vin_payload.len(), 20);

        let frames = fragment_isotp_message(0x7E8, &vin_payload).unwrap();
        // 20 bytes: FF (6 bytes) + CF 1 (7 bytes) + CF 2 (7 bytes) = 3 frames
        assert_eq!(frames.len(), 3);

        let mut reassembler = IsoTpReassembler::new(0x7E8, 0x7E0);

        // Feed Frame 1: First Frame
        let (res1, fc1) = reassembler.process_frame(&frames[0]).unwrap();
        assert!(res1.is_none());
        assert!(fc1.is_some());
        let fc = fc1.unwrap();
        assert_eq!(fc.id, 0x7E0);
        assert_eq!(fc.payload()[0], 0x30); // Clear to Send (CTS)

        // Feed Frame 2: Consecutive Frame 1
        let (res2, fc2) = reassembler.process_frame(&frames[1]).unwrap();
        assert!(res2.is_none());
        assert!(fc2.is_none());

        // Feed Frame 3: Consecutive Frame 2 (final)
        let (res3, fc3) = reassembler.process_frame(&frames[2]).unwrap();
        assert!(fc3.is_none());
        assert_eq!(res3, Some(vin_payload));
    }

    #[test]
    fn test_sequence_number_error_detection() {
        let data = vec![0xAA; 30];
        let mut frames = fragment_isotp_message(0x7E8, &data).unwrap();

        let mut reassembler = IsoTpReassembler::new(0x7E8, 0x7E0);
        // Process FF
        let _ = reassembler.process_frame(&frames[0]).unwrap();

        // Corrupt sequence number in CF 1 from 0x21 to 0x25
        let mut corrupted_data = [0u8; 8];
        corrupted_data.copy_from_slice(frames[1].payload());
        corrupted_data[0] = 0x25;
        frames[1] = CanFrame::new(0x7E8, &corrupted_data).unwrap();

        let err = reassembler.process_frame(&frames[1]).unwrap_err();
        assert_eq!(
            err,
            IsoTpError::SequenceNumberMismatch {
                expected: 1,
                received: 5
            }
        );
    }
}
