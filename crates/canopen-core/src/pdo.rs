/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * CANopen Process Data Object (PDO) Signal Mapping & Packing Engine (CiA 301 / CiA 402).
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use crate::frame::{CanError, CanFrame};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SignalType {
    Bool,
    Uint8,
    Int8,
    Uint16,
    Int16,
    Uint32,
    Int32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SignalDefinition {
    pub name: String,
    pub bit_start: u8,
    pub bit_length: u8,
    pub signal_type: SignalType,
    pub is_big_endian: bool,
    pub factor: f64,
    pub offset: f64,
    pub unit: String,
}

impl SignalDefinition {
    pub fn new(
        name: &str,
        bit_start: u8,
        bit_length: u8,
        signal_type: SignalType,
        factor: f64,
        offset: f64,
        unit: &str,
    ) -> Self {
        Self {
            name: name.to_string(),
            bit_start,
            bit_length,
            signal_type,
            is_big_endian: false,
            factor,
            offset,
            unit: unit.to_string(),
        }
    }

    /// Extract raw integer bits from an 8-byte CAN payload.
    pub fn extract_raw_bits(&self, payload: &[u8]) -> u64 {
        let mut raw: u64 = 0;
        let total_bits = (payload.len() * 8) as u8;

        for i in 0..self.bit_length {
            let bit_idx = self.bit_start + i;
            if bit_idx >= total_bits {
                break;
            }
            let byte_pos = (bit_idx / 8) as usize;
            let bit_in_byte = bit_idx % 8;
            let bit_val = (payload[byte_pos] >> bit_in_byte) & 0x01;
            raw |= (bit_val as u64) << i;
        }

        raw
    }

    /// Decode raw bits into a scaled engineering value.
    pub fn decode_value(&self, payload: &[u8]) -> f64 {
        let raw_bits = self.extract_raw_bits(payload);

        let unscaled = match self.signal_type {
            SignalType::Bool => {
                if raw_bits != 0 {
                    1.0
                } else {
                    0.0
                }
            }
            SignalType::Uint8 => (raw_bits & 0xFF) as f64,
            SignalType::Int8 => (raw_bits as u8 as i8) as f64,
            SignalType::Uint16 => (raw_bits & 0xFFFF) as f64,
            SignalType::Int16 => (raw_bits as u16 as i16) as f64,
            SignalType::Uint32 => (raw_bits & 0xFFFF_FFFF) as f64,
            SignalType::Int32 => (raw_bits as u32 as i32) as f64,
        };

        (unscaled * self.factor) + self.offset
    }

    /// Pack an engineering value into an 8-byte buffer at the defined bit position.
    pub fn pack_value(&self, value: f64, payload: &mut [u8; 8]) {
        let unscaled = (value - self.offset) / self.factor;

        let raw_bits: u64 = match self.signal_type {
            SignalType::Bool => {
                if unscaled != 0.0 {
                    1
                } else {
                    0
                }
            }
            SignalType::Uint8 => unscaled as u8 as u64,
            SignalType::Int8 => (unscaled as i8) as u8 as u64,
            SignalType::Uint16 => unscaled as u16 as u64,
            SignalType::Int16 => (unscaled as i16) as u16 as u64,
            SignalType::Uint32 => unscaled as u32 as u64,
            SignalType::Int32 => (unscaled as i32) as u32 as u64,
        };

        for i in 0..self.bit_length {
            let bit_idx = self.bit_start + i;
            if bit_idx >= 64 {
                break;
            }
            let byte_pos = (bit_idx / 8) as usize;
            let bit_in_byte = bit_idx % 8;
            let bit_val = ((raw_bits >> i) & 0x01) as u8;

            if bit_val == 1 {
                payload[byte_pos] |= 1 << bit_in_byte;
            } else {
                payload[byte_pos] &= !(1 << bit_in_byte);
            }
        }
    }
}

/// Decoded PDO Signal Reading.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SignalReading {
    pub name: String,
    pub value: f64,
    pub unit: String,
}

/// Pre-configured PDO Mapping for an arbitration ID.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PdoMapping {
    pub cob_id: u32,
    pub name: String,
    pub signals: Vec<SignalDefinition>,
}

impl PdoMapping {
    pub fn new(cob_id: u32, name: &str) -> Self {
        Self {
            cob_id,
            name: name.to_string(),
            signals: Vec::new(),
        }
    }

    pub fn add_signal(&mut self, signal: SignalDefinition) {
        self.signals.push(signal);
    }

    /// Decode all mapped signals from a received CAN frame.
    pub fn decode_frame(&self, frame: &CanFrame) -> Vec<SignalReading> {
        let payload = frame.payload();
        self.signals
            .iter()
            .map(|sig| SignalReading {
                name: sig.name.clone(),
                value: sig.decode_value(payload),
                unit: sig.unit.clone(),
            })
            .collect()
    }

    /// Pack a list of signal values into a new CAN frame.
    pub fn encode_frame(&self, values: &[(&str, f64)]) -> Result<CanFrame, CanError> {
        let mut data = [0u8; 8];
        for (name, val) in values {
            if let Some(sig) = self.signals.iter().find(|s| s.name == *name) {
                sig.pack_value(*val, &mut data);
            }
        }
        CanFrame::new(self.cob_id, &data)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_uint16_signal_decode_and_encode() {
        // Motor RPM: Uint16 at bit 0..16, factor 1.0, offset 0.0
        let rpm_sig = SignalDefinition::new("MotorRPM", 0, 16, SignalType::Uint16, 1.0, 0.0, "rpm");
        let mut payload = [0u8; 8];
        rpm_sig.pack_value(3200.0, &mut payload);

        assert_eq!(&payload[0..2], &(3200u16).to_le_bytes());
        let decoded = rpm_sig.decode_value(&payload);
        assert_eq!(decoded, 3200.0);
    }

    #[test]
    fn test_int16_negative_temperature() {
        // Inverter Temp: Int16 at bit 16..32, factor 0.1, offset 0.0
        let temp_sig =
            SignalDefinition::new("InverterTemp", 16, 16, SignalType::Int16, 0.1, 0.0, "°C");
        let mut payload = [0u8; 8];
        temp_sig.pack_value(-15.5, &mut payload); // -155 raw

        let decoded = temp_sig.decode_value(&payload);
        assert!((decoded - (-15.5)).abs() < 0.05);
    }

    #[test]
    fn test_pdo_mapping_full_frame() {
        // CiA 402 Velocity (32-bit signed at bit 0) + StatusWord (16-bit at bit 32)
        let mut mapping = PdoMapping::new(0x181, "CiA402_TPDO1");
        mapping.add_signal(SignalDefinition::new(
            "Velocity",
            0,
            32,
            SignalType::Int32,
            1.0,
            0.0,
            "rpm",
        ));
        mapping.add_signal(SignalDefinition::new(
            "StatusWord",
            32,
            16,
            SignalType::Uint16,
            1.0,
            0.0,
            "raw",
        ));

        let frame = mapping
            .encode_frame(&[("Velocity", -1250.0), ("StatusWord", 0x0237 as f64)])
            .unwrap();
        assert_eq!(frame.id, 0x181);

        let readings = mapping.decode_frame(&frame);
        assert_eq!(readings.len(), 2);
        assert_eq!(readings[0].name, "Velocity");
        assert_eq!(readings[0].value, -1250.0);
        assert_eq!(readings[1].name, "StatusWord");
        assert_eq!(readings[1].value, 0x0237 as f64);
    }
}
