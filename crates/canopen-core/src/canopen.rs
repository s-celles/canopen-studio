/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use crate::frame::CanFrame;
use serde::{Deserialize, Serialize};

/// Standard CANopen NMT Network States (CiA 301).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum NmtState {
    Bootup = 0x00,
    Stopped = 0x04,
    Operational = 0x05,
    PreOperational = 0x7F,
    Unknown = 0xFF,
}

impl From<u8> for NmtState {
    fn from(val: u8) -> Self {
        match val {
            0x00 => NmtState::Bootup,
            0x04 => NmtState::Stopped,
            0x05 => NmtState::Operational,
            0x7F => NmtState::PreOperational,
            _ => NmtState::Unknown,
        }
    }
}

/// CANopen Communication Object Types.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum CanopenService {
    NmtCommand,
    Sync,
    Time,
    Emergency { node_id: u8, err_code: u16, err_reg: u8 },
    Tpdo { pdo_num: u8, node_id: u8 },
    Rpdo { pdo_num: u8, node_id: u8 },
    Tsdo { node_id: u8 },
    Rsdo { node_id: u8 },
    Heartbeat { node_id: u8, state: NmtState },
    Other { id: u32 },
}

/// Decoded CANopen message metadata.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CanopenMessageInfo {
    pub service: CanopenService,
    pub description: String,
}

/// Inspect a CAN frame and decode its CANopen service and semantics.
pub fn decode_canopen_frame(frame: &CanFrame) -> CanopenMessageInfo {
    let id = frame.id;
    let payload = frame.payload();

    if id == 0x000 {
        let cmd = payload.first().copied().unwrap_or(0);
        let node = payload.get(1).copied().unwrap_or(0);
        let desc = match cmd {
            0x01 => format!("NMT Start Remote Node (-> Node {})", node),
            0x02 => format!("NMT Stop Remote Node (-> Node {})", node),
            0x80 => format!("NMT Enter Pre-Operational (-> Node {})", node),
            0x81 => format!("NMT Reset Node (-> Node {})", node),
            0x82 => format!("NMT Reset Communication (-> Node {})", node),
            _ => format!("NMT Unknown Command 0x{:02X} (-> Node {})", cmd, node),
        };
        return CanopenMessageInfo {
            service: CanopenService::NmtCommand,
            description: desc,
        };
    }

    if id == 0x080 {
        return CanopenMessageInfo {
            service: CanopenService::Sync,
            description: "CANopen SYNC Frame".to_string(),
        };
    }

    if id == 0x100 {
        return CanopenMessageInfo {
            service: CanopenService::Time,
            description: "CANopen TIME Stamp".to_string(),
        };
    }

    // Emergency: 0x081..=0x0FF
    if (0x081..=0x0FF).contains(&id) {
        let node_id = (id - 0x080) as u8;
        let err_code = if payload.len() >= 2 {
            u16::from_le_bytes([payload[0], payload[1]])
        } else {
            0
        };
        let err_reg = payload.get(2).copied().unwrap_or(0);
        return CanopenMessageInfo {
            service: CanopenService::Emergency {
                node_id,
                err_code,
                err_reg,
            },
            description: format!("EMCY Node {} (Code: 0x{:04X}, Reg: 0x{:02X})", node_id, err_code, err_reg),
        };
    }

    // TPDO1: 0x181..=0x1FF
    if (0x181..=0x1FF).contains(&id) {
        let node_id = (id - 0x180) as u8;
        return CanopenMessageInfo {
            service: CanopenService::Tpdo { pdo_num: 1, node_id },
            description: format!("TPDO1 Node {}", node_id),
        };
    }

    // RPDO1: 0x201..=0x27F
    if (0x201..=0x27F).contains(&id) {
        let node_id = (id - 0x200) as u8;
        return CanopenMessageInfo {
            service: CanopenService::Rpdo { pdo_num: 1, node_id },
            description: format!("RPDO1 Node {}", node_id),
        };
    }

    // TPDO2: 0x281..=0x2FF
    if (0x281..=0x2FF).contains(&id) {
        let node_id = (id - 0x280) as u8;
        return CanopenMessageInfo {
            service: CanopenService::Tpdo { pdo_num: 2, node_id },
            description: format!("TPDO2 Node {}", node_id),
        };
    }

    // RPDO2: 0x301..=0x37F
    if (0x301..=0x37F).contains(&id) {
        let node_id = (id - 0x300) as u8;
        return CanopenMessageInfo {
            service: CanopenService::Rpdo { pdo_num: 2, node_id },
            description: format!("RPDO2 Node {}", node_id),
        };
    }

    // TPDO3: 0x381..=0x3FF
    if (0x381..=0x3FF).contains(&id) {
        let node_id = (id - 0x380) as u8;
        return CanopenMessageInfo {
            service: CanopenService::Tpdo { pdo_num: 3, node_id },
            description: format!("TPDO3 Node {}", node_id),
        };
    }

    // RPDO3: 0x401..=0x47F
    if (0x401..=0x47F).contains(&id) {
        let node_id = (id - 0x400) as u8;
        return CanopenMessageInfo {
            service: CanopenService::Rpdo { pdo_num: 3, node_id },
            description: format!("RPDO3 Node {}", node_id),
        };
    }

    // TPDO4: 0x481..=0x4FF
    if (0x481..=0x4FF).contains(&id) {
        let node_id = (id - 0x480) as u8;
        return CanopenMessageInfo {
            service: CanopenService::Tpdo { pdo_num: 4, node_id },
            description: format!("TPDO4 Node {}", node_id),
        };
    }

    // RPDO4: 0x501..=0x57F
    if (0x501..=0x57F).contains(&id) {
        let node_id = (id - 0x500) as u8;
        return CanopenMessageInfo {
            service: CanopenService::Rpdo { pdo_num: 4, node_id },
            description: format!("RPDO4 Node {}", node_id),
        };
    }

    // TSDO (Transmit SDO, Server to Client): 0x581..=0x5FF
    if (0x581..=0x5FF).contains(&id) {
        let node_id = (id - 0x580) as u8;
        return CanopenMessageInfo {
            service: CanopenService::Tsdo { node_id },
            description: format!("TSDO Node {} (SDO Response)", node_id),
        };
    }

    // RSDO (Receive SDO, Client to Server): 0x601..=0x67F
    if (0x601..=0x67F).contains(&id) {
        let node_id = (id - 0x600) as u8;
        return CanopenMessageInfo {
            service: CanopenService::Rsdo { node_id },
            description: format!("RSDO Node {} (SDO Request)", node_id),
        };
    }

    // Heartbeat / Bootup: 0x701..=0x77F
    if (0x701..=0x77F).contains(&id) {
        let node_id = (id - 0x700) as u8;
        let raw_state = payload.first().copied().unwrap_or(0xFF);
        let state = NmtState::from(raw_state);
        let state_name = match state {
            NmtState::Bootup => "Bootup",
            NmtState::Stopped => "Stopped",
            NmtState::Operational => "Operational",
            NmtState::PreOperational => "Pre-Operational",
            NmtState::Unknown => "Unknown",
        };
        return CanopenMessageInfo {
            service: CanopenService::Heartbeat { node_id, state },
            description: format!("Heartbeat Node {} [{}]", node_id, state_name),
        };
    }

    CanopenMessageInfo {
        service: CanopenService::Other { id },
        description: format!("Raw CAN frame 0x{:03X}", id),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_decode_sync() {
        let frame = CanFrame::new(0x080, &[]).unwrap();
        let info = decode_canopen_frame(&frame);
        assert_eq!(info.service, CanopenService::Sync);
    }

    #[test]
    fn test_decode_heartbeat() {
        let frame = CanFrame::new(0x70A, &[0x05]).unwrap(); // Node 10, Operational
        let info = decode_canopen_frame(&frame);
        assert_eq!(
            info.service,
            CanopenService::Heartbeat {
                node_id: 10,
                state: NmtState::Operational
            }
        );
    }

    #[test]
    fn test_decode_tpdo1() {
        let frame = CanFrame::new(0x181, &[0x01, 0x02]).unwrap(); // Node 1
        let info = decode_canopen_frame(&frame);
        assert_eq!(info.service, CanopenService::Tpdo { pdo_num: 1, node_id: 1 });
    }
}
