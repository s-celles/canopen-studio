/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Drive telemetry aggregation: CiA 402 statusword decoding and SEVCON Gen4
 * TPDO extraction. Mirrors the Python decoders in
 * `canopen_studio.stack.decoders` so both front ends report the same values.
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use crate::frame::CanFrame;
use serde::{Deserialize, Serialize};

/// State of the CiA 402 drive state machine, as encoded in the statusword (0x6041).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Cia402State {
    NotReadyToSwitchOn,
    SwitchOnDisabled,
    ReadyToSwitchOn,
    SwitchedOn,
    OperationEnabled,
    QuickStopActive,
    FaultReactionActive,
    Fault,
    /// The statusword matched no defined state; carries the raw word.
    Unknown(u16),
}

impl Cia402State {
    /// Decode bits 0..3, 5 and 6 of the statusword into a drive state (CiA 402 §6.3).
    pub fn from_status_word(sw: u16) -> Self {
        let masked = sw & 0x006F;
        // The 0x004F group ignores bit 5 (quick stop), which is don't-care there.
        if masked & 0x004F == 0x0000 {
            return Cia402State::NotReadyToSwitchOn;
        }
        if masked & 0x004F == 0x0040 {
            return Cia402State::SwitchOnDisabled;
        }
        if masked == 0x0021 {
            return Cia402State::ReadyToSwitchOn;
        }
        if masked == 0x0023 {
            return Cia402State::SwitchedOn;
        }
        if masked == 0x0027 {
            return Cia402State::OperationEnabled;
        }
        if masked == 0x0007 {
            return Cia402State::QuickStopActive;
        }
        if masked & 0x004F == 0x000F {
            return Cia402State::FaultReactionActive;
        }
        if masked & 0x004F == 0x0008 {
            return Cia402State::Fault;
        }
        Cia402State::Unknown(sw)
    }

    pub fn label(&self) -> String {
        match self {
            Cia402State::NotReadyToSwitchOn => "Not Ready to Switch On".to_string(),
            Cia402State::SwitchOnDisabled => "Switch On Disabled".to_string(),
            Cia402State::ReadyToSwitchOn => "Ready to Switch On".to_string(),
            Cia402State::SwitchedOn => "Switched On".to_string(),
            Cia402State::OperationEnabled => "Operation Enabled".to_string(),
            Cia402State::QuickStopActive => "Quick Stop Active".to_string(),
            Cia402State::FaultReactionActive => "Fault Reaction Active".to_string(),
            Cia402State::Fault => "Fault".to_string(),
            Cia402State::Unknown(sw) => format!("Status 0x{sw:04X}"),
        }
    }
}

/// Latest drive telemetry gathered from the bus.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct DriveTelemetry {
    pub speed_rpm: i32,
    pub max_speed_rpm: i32,
    pub target_torque: i32,
    pub heatsink_temp_c: i32,
    pub motor_temp_raw: i32,
    pub status_word: Option<u16>,
    pub state: Option<Cia402State>,
}

impl DriveTelemetry {
    pub fn new() -> Self {
        Self::default()
    }

    /// Human-readable drive state, or "-" while no statusword has been seen.
    pub fn state_label(&self) -> String {
        match self.state {
            Some(s) => s.label(),
            None => "-".to_string(),
        }
    }

    /// Fold one frame into the telemetry. Returns true when a value changed.
    ///
    /// Recognised frames (SEVCON Gen4 profile plus generic CiA 402 TPDO1/TPDO2):
    /// * `0x473` — TPDO5: max speed (0x6080) then actual speed (0x606C)
    /// * `0x270` — TPDO4: target torque (0x6071) at bytes 5..7
    /// * `0x156` — TPDO3: motor temp at 2..4, heatsink temp (°C) at 6..8
    /// * `0x181..=0x1FF` — TPDO1: statusword, optionally velocity actual
    /// * `0x281..=0x2FF` — TPDO2: statusword, optionally velocity actual
    pub fn apply_frame(&mut self, frame: &CanFrame) -> bool {
        let data = frame.payload();
        let before = self.clone();

        match frame.id {
            0x473 if data.len() >= 8 => {
                self.max_speed_rpm = read_i32(&data[0..4]);
                self.speed_rpm = read_i32(&data[4..8]);
            }
            0x270 if data.len() >= 7 => {
                self.target_torque = read_i16(&data[5..7]) as i32;
            }
            0x156 if data.len() >= 8 => {
                self.motor_temp_raw = read_i16(&data[2..4]) as i32;
                self.heatsink_temp_c = read_i16(&data[6..8]) as i32;
            }
            id if is_cia402_status_pdo(id) && data.len() >= 2 => {
                let sw = u16::from_le_bytes([data[0], data[1]]);
                self.status_word = Some(sw);
                self.state = Some(Cia402State::from_status_word(sw));
                // TPDO1/TPDO2 commonly carry the velocity actual value next.
                // 0x473 is the authoritative speed source, so only fall back
                // here while it has not reported a maximum yet.
                if data.len() >= 6 && self.max_speed_rpm == 0 {
                    self.speed_rpm = read_i32(&data[2..6]);
                }
            }
            _ => return false,
        }

        *self != before
    }
}

/// True for the generic CiA 402 TPDO1 and TPDO2 COB-ID ranges.
fn is_cia402_status_pdo(id: u32) -> bool {
    (0x181..=0x1FF).contains(&id) || (0x281..=0x2FF).contains(&id)
}

fn read_i32(bytes: &[u8]) -> i32 {
    i32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]])
}

fn read_i16(bytes: &[u8]) -> i16 {
    i16::from_le_bytes([bytes[0], bytes[1]])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_statusword_maps_to_the_documented_drive_states() {
        let cases = [
            (0x0000u16, Cia402State::NotReadyToSwitchOn),
            (0x0040, Cia402State::SwitchOnDisabled),
            (0x0021, Cia402State::ReadyToSwitchOn),
            (0x0023, Cia402State::SwitchedOn),
            (0x0027, Cia402State::OperationEnabled),
            (0x0007, Cia402State::QuickStopActive),
            (0x000F, Cia402State::FaultReactionActive),
            (0x0008, Cia402State::Fault),
        ];
        for (sw, expected) in cases {
            assert_eq!(Cia402State::from_status_word(sw), expected, "sw=0x{sw:04X}");
        }
    }

    #[test]
    fn statusword_bits_outside_the_state_mask_are_ignored() {
        // Bits 4, 7..15 carry voltage/warning/manufacturer flags, not state.
        assert_eq!(
            Cia402State::from_status_word(0xFF27 & !0x0010),
            Cia402State::OperationEnabled
        );
        assert_eq!(
            Cia402State::from_status_word(0x1237),
            Cia402State::OperationEnabled
        );
    }

    #[test]
    fn an_undefined_statusword_reports_its_raw_value() {
        let state = Cia402State::from_status_word(0x0002);
        assert_eq!(state, Cia402State::Unknown(0x0002));
        assert_eq!(state.label(), "Status 0x0002");
    }

    #[test]
    fn tpdo5_carries_max_speed_then_actual_speed() {
        let mut t = DriveTelemetry::new();
        let mut payload = Vec::new();
        payload.extend_from_slice(&5000i32.to_le_bytes());
        payload.extend_from_slice(&1234i32.to_le_bytes());
        let frame = CanFrame::new(0x473, &payload).unwrap();

        assert!(t.apply_frame(&frame));
        assert_eq!(t.max_speed_rpm, 5000);
        assert_eq!(t.speed_rpm, 1234);
    }

    #[test]
    fn a_negative_speed_is_read_as_signed() {
        let mut t = DriveTelemetry::new();
        let mut payload = Vec::new();
        payload.extend_from_slice(&5000i32.to_le_bytes());
        payload.extend_from_slice(&(-750i32).to_le_bytes());
        t.apply_frame(&CanFrame::new(0x473, &payload).unwrap());
        assert_eq!(t.speed_rpm, -750);
    }

    #[test]
    fn tpdo4_carries_the_target_torque_at_bytes_five_and_six() {
        let mut t = DriveTelemetry::new();
        let mut payload = vec![3u8, 4, 0, 0, 0];
        payload.extend_from_slice(&(-120i16).to_le_bytes());
        payload.push(0);
        assert!(t.apply_frame(&CanFrame::new(0x270, &payload).unwrap()));
        assert_eq!(t.target_torque, -120);
    }

    #[test]
    fn tpdo3_carries_the_motor_and_heatsink_temperatures() {
        let mut t = DriveTelemetry::new();
        let mut payload = vec![0u8, 0, 10, 0, 50, 0];
        payload.extend_from_slice(&37i16.to_le_bytes());
        assert!(t.apply_frame(&CanFrame::new(0x156, &payload).unwrap()));
        assert_eq!(t.heatsink_temp_c, 37);
        assert_eq!(t.motor_temp_raw, 10);
    }

    #[test]
    fn tpdo1_reports_the_drive_state() {
        let mut t = DriveTelemetry::new();
        assert_eq!(t.state_label(), "-");
        assert!(t.apply_frame(&CanFrame::new(0x181, &0x0027u16.to_le_bytes()).unwrap()));
        assert_eq!(t.state, Some(Cia402State::OperationEnabled));
        assert_eq!(t.state_label(), "Operation Enabled");
    }

    #[test]
    fn tpdo5_speed_wins_over_the_tpdo2_velocity_fallback() {
        let mut t = DriveTelemetry::new();
        let mut tpdo5 = Vec::new();
        tpdo5.extend_from_slice(&5000i32.to_le_bytes());
        tpdo5.extend_from_slice(&1234i32.to_le_bytes());
        t.apply_frame(&CanFrame::new(0x473, &tpdo5).unwrap());

        let mut tpdo2 = Vec::new();
        tpdo2.extend_from_slice(&0x0027u16.to_le_bytes());
        tpdo2.extend_from_slice(&999i32.to_le_bytes());
        t.apply_frame(&CanFrame::new(0x281, &tpdo2).unwrap());

        assert_eq!(t.speed_rpm, 1234, "TPDO5 must stay authoritative");
        assert_eq!(t.state, Some(Cia402State::OperationEnabled));
    }

    #[test]
    fn a_drive_without_tpdo5_still_reports_a_speed() {
        let mut t = DriveTelemetry::new();
        let mut tpdo2 = Vec::new();
        tpdo2.extend_from_slice(&0x0027u16.to_le_bytes());
        tpdo2.extend_from_slice(&999i32.to_le_bytes());
        t.apply_frame(&CanFrame::new(0x281, &tpdo2).unwrap());
        assert_eq!(t.speed_rpm, 999);
    }

    #[test]
    fn unrelated_and_truncated_frames_change_nothing() {
        let mut t = DriveTelemetry::new();
        assert!(!t.apply_frame(&CanFrame::new(0x7FF, &[1, 2, 3]).unwrap()));
        // 0x473 needs 8 bytes, 0x270 needs 7, 0x156 needs 8.
        assert!(!t.apply_frame(&CanFrame::new(0x473, &[0, 1, 2, 3]).unwrap()));
        assert!(!t.apply_frame(&CanFrame::new(0x270, &[3, 4, 0]).unwrap()));
        assert!(!t.apply_frame(&CanFrame::new(0x156, &[0, 0, 10]).unwrap()));
        assert_eq!(t, DriveTelemetry::new());
    }

    #[test]
    fn a_repeated_frame_reports_no_change() {
        let mut t = DriveTelemetry::new();
        let mut payload = Vec::new();
        payload.extend_from_slice(&5000i32.to_le_bytes());
        payload.extend_from_slice(&1234i32.to_le_bytes());
        let frame = CanFrame::new(0x473, &payload).unwrap();
        assert!(t.apply_frame(&frame));
        assert!(!t.apply_frame(&frame), "identical frame must be a no-op");
    }
}
