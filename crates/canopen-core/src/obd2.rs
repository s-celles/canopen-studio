/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use crate::frame::CanFrame;
use serde::{Deserialize, Serialize};

/// Standard OBD-II Parameter ID (PID) representation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ObdPidReading {
    pub mode: u8,
    pub pid: u8,
    pub name: String,
    pub value: f64,
    pub unit: String,
}

/// Decoded Diagnostic Trouble Code (DTC).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ObdDtc {
    pub code: String,
    pub description: String,
}

/// Parse standard Mode 01 response frames (e.g. ECU 0x7E8..0x7EF).
/// Functional (broadcast) OBD-II request COB-ID for 11-bit ISO 15765-4.
pub const OBD2_REQUEST_ID: u32 = 0x7DF;

/// First of the eight physical OBD-II response COB-IDs (0x7E8..=0x7EF).
pub const OBD2_RESPONSE_ID: u32 = 0x7E8;

/// Mode 01 PIDs [`decode_obd2_mode01_frame`] understands, in PID order.
/// A requester and a responder can both work from this list so they agree.
pub const SUPPORTED_MODE01_PIDS: &[u8] = &[0x04, 0x05, 0x0C, 0x0D, 0x0F, 0x11, 0x42];

/// Build a Mode 01 request for `pid` as an ISO-TP single frame on
/// [`OBD2_REQUEST_ID`]. Unused bytes are padded with 0x55 as is conventional.
pub fn build_obd2_mode01_request(pid: u8) -> Result<CanFrame, crate::frame::CanError> {
    CanFrame::new(
        OBD2_REQUEST_ID,
        &[0x02, 0x01, pid, 0x55, 0x55, 0x55, 0x55, 0x55],
    )
}

/// Build a Mode 01 response carrying `data` for `pid`, as an ECU would.
/// `data` must be 1 or 2 bytes, matching what the PID defines.
pub fn build_obd2_mode01_response(
    pid: u8,
    data: &[u8],
) -> Result<CanFrame, crate::frame::CanError> {
    let mut payload = [0x55u8; 8];
    payload[0] = 2 + data.len() as u8;
    payload[1] = 0x41;
    payload[2] = pid;
    payload[3..3 + data.len()].copy_from_slice(data);
    CanFrame::new(OBD2_RESPONSE_ID, &payload)
}

/// Read the PID out of a Mode 01 request, or `None` if the frame is not one.
pub fn obd2_mode01_request_pid(frame: &CanFrame) -> Option<u8> {
    if frame.id != OBD2_REQUEST_ID {
        return None;
    }
    let payload = frame.payload();
    if payload.len() < 3 || payload[1] != 0x01 {
        return None;
    }
    Some(payload[2])
}

pub fn decode_obd2_mode01_frame(frame: &CanFrame) -> Option<ObdPidReading> {
    let id = frame.id;
    // Standard response IDs: 0x7E8..=0x7EF (11-bit) or 0x18DAF1xx (29-bit)
    if !(0x7E8..=0x7EF).contains(&id) && (id & 0xFFFF0000) != 0x18DA0000 {
        return None;
    }

    let payload = frame.payload();
    if payload.len() < 3 {
        return None;
    }

    // ISO-TP Single Frame: byte 0 = length, byte 1 = Mode (0x41 = Mode 01 response), byte 2 = PID
    let _pci_len = payload[0];
    let mode_resp = payload[1];
    if mode_resp != 0x41 {
        return None;
    }

    let pid = payload[2];
    let data = &payload[3..];

    match pid {
        0x04 => {
            // Calculated engine load (%)
            let a = data.first().copied()? as f64;
            Some(ObdPidReading {
                mode: 1,
                pid,
                name: "Calculated Engine Load".to_string(),
                value: (a * 100.0) / 255.0,
                unit: "%".to_string(),
            })
        }
        0x05 => {
            // Engine coolant temperature (°C)
            let a = data.first().copied()? as f64;
            Some(ObdPidReading {
                mode: 1,
                pid,
                name: "Engine Coolant Temperature".to_string(),
                value: a - 40.0,
                unit: "°C".to_string(),
            })
        }
        0x0C => {
            // Engine speed (RPM)
            if data.len() < 2 {
                return None;
            }
            let a = data[0] as f64;
            let b = data[1] as f64;
            Some(ObdPidReading {
                mode: 1,
                pid,
                name: "Engine RPM".to_string(),
                value: ((a * 256.0) + b) / 4.0,
                unit: "rpm".to_string(),
            })
        }
        0x0D => {
            // Vehicle speed (km/h)
            let a = data.first().copied()? as f64;
            Some(ObdPidReading {
                mode: 1,
                pid,
                name: "Vehicle Speed".to_string(),
                value: a,
                unit: "km/h".to_string(),
            })
        }
        0x0F => {
            // Intake air temperature (°C)
            let a = data.first().copied()? as f64;
            Some(ObdPidReading {
                mode: 1,
                pid,
                name: "Intake Air Temperature".to_string(),
                value: a - 40.0,
                unit: "°C".to_string(),
            })
        }
        0x11 => {
            // Throttle position (%)
            let a = data.first().copied()? as f64;
            Some(ObdPidReading {
                mode: 1,
                pid,
                name: "Throttle Position".to_string(),
                value: (a * 100.0) / 255.0,
                unit: "%".to_string(),
            })
        }
        0x42 => {
            // Control module voltage (V)
            if data.len() < 2 {
                return None;
            }
            let a = data[0] as f64;
            let b = data[1] as f64;
            Some(ObdPidReading {
                mode: 1,
                pid,
                name: "Control Module Voltage".to_string(),
                value: ((a * 256.0) + b) / 1000.0,
                unit: "V".to_string(),
            })
        }
        _ => None,
    }
}

/// Convert two raw DTC bytes into standard alphanumeric representation (e.g. `P0100`).
pub fn format_dtc_bytes(b1: u8, b2: u8) -> Option<String> {
    if b1 == 0 && b2 == 0 {
        return None;
    }
    let category = match (b1 >> 6) & 0x03 {
        0 => 'P', // Powertrain
        1 => 'C', // Chassis
        2 => 'B', // Body
        3 => 'U', // Network
        _ => unreachable!(),
    };
    let digit1 = (b1 >> 4) & 0x03;
    let digit2 = b1 & 0x0F;
    let digit3 = (b2 >> 4) & 0x0F;
    let digit4 = b2 & 0x0F;
    Some(format!(
        "{}{:X}{:X}{:X}{:X}",
        category, digit1, digit2, digit3, digit4
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_decode_obd2_rpm() {
        // Mode 01 PID 0C response: 2500 RPM -> (2500 * 4) = 10000 = 0x2710 -> A=0x27, B=0x10
        let frame = CanFrame::new(0x7E8, &[0x04, 0x41, 0x0C, 0x27, 0x10]).unwrap();
        let reading = decode_obd2_mode01_frame(&frame).unwrap();
        assert_eq!(reading.pid, 0x0C);
        assert_eq!(reading.value, 2500.0);
        assert_eq!(reading.unit, "rpm");
    }

    #[test]
    fn test_decode_obd2_speed() {
        let frame = CanFrame::new(0x7E8, &[0x03, 0x41, 0x0D, 85]).unwrap();
        let reading = decode_obd2_mode01_frame(&frame).unwrap();
        assert_eq!(reading.pid, 0x0D);
        assert_eq!(reading.value, 85.0);
        assert_eq!(reading.unit, "km/h");
    }

    #[test]
    fn test_format_dtc() {
        // P0100 -> b1 = 0x01, b2 = 0x00
        assert_eq!(format_dtc_bytes(0x01, 0x00), Some("P0100".to_string()));
        // C0300 -> Chassis = 01 in top 2 bits -> 0x43, 0x00
        assert_eq!(format_dtc_bytes(0x43, 0x00), Some("C0300".to_string()));
        // None for zero
        assert_eq!(format_dtc_bytes(0x00, 0x00), None);
    }
    #[test]
    fn a_mode01_request_is_an_iso_tp_single_frame_on_the_functional_id() {
        let frame = build_obd2_mode01_request(0x0C).unwrap();
        assert_eq!(frame.id, OBD2_REQUEST_ID);
        assert_eq!(&frame.payload()[..3], &[0x02, 0x01, 0x0C]);
        assert_eq!(&frame.payload()[3..], &[0x55; 5], "padded to eight bytes");
    }

    #[test]
    fn a_request_reveals_the_pid_it_asks_for() {
        assert_eq!(
            obd2_mode01_request_pid(&build_obd2_mode01_request(0x42).unwrap()),
            Some(0x42)
        );
    }

    #[test]
    fn a_response_is_not_mistaken_for_a_request() {
        let response = build_obd2_mode01_response(0x0C, &[0x1A, 0xF8]).unwrap();
        assert_eq!(obd2_mode01_request_pid(&response), None);
        // A Mode 03 request (read DTCs) is not Mode 01.
        let mode03 = CanFrame::new(OBD2_REQUEST_ID, &[0x01, 0x03, 0, 0, 0, 0, 0, 0]).unwrap();
        assert_eq!(obd2_mode01_request_pid(&mode03), None);
    }

    #[test]
    fn a_built_response_decodes_back_to_the_value_it_carries() {
        // Engine RPM is reported in quarter-revolutions: 1750 rpm -> 7000.
        let raw = 7000u16.to_be_bytes();
        let response = build_obd2_mode01_response(0x0C, &raw).unwrap();
        let reading = decode_obd2_mode01_frame(&response).expect("must decode");
        assert_eq!(reading.pid, 0x0C);
        assert_eq!(reading.name, "Engine RPM");
        assert!((reading.value - 1750.0).abs() < 0.01, "{}", reading.value);
    }

    #[test]
    fn every_advertised_pid_can_be_built_and_decoded() {
        for &pid in SUPPORTED_MODE01_PIDS {
            let response = build_obd2_mode01_response(pid, &[0x40, 0x00]).unwrap();
            let reading = decode_obd2_mode01_frame(&response)
                .unwrap_or_else(|| panic!("PID {pid:#04X} is advertised but does not decode"));
            assert_eq!(reading.pid, pid);
            assert!(!reading.name.is_empty());
        }
    }
}
