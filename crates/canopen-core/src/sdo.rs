/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * CANopen Service Data Object (SDO) Client & Server Protocol Engine (CiA 301).
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use crate::frame::{CanError, CanFrame};
use serde::{Deserialize, Serialize};

/// Standard SDO Abort Codes (CiA 301).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SdoAbortCode {
    ToggleBitNotAlternated,
    ProtocolTimedOut,
    CommandSpecifierInvalid,
    InvalidBlockSize,
    InvalidSequenceNumber,
    CrcError,
    OutOfMemory,
    UnsupportedAccess,
    ReadWriteOnlyObject,
    WriteReadOnlyObject,
    ObjectDoesNotExist,
    ObjectCannotBeMappedToPdo,
    PdoLengthExceeded,
    GeneralParameterIncompatibility,
    GeneralInternalIncompatibility,
    HardwareFault,
    DataTypeMismatch,
    DataTooLong,
    DataTooShort,
    SubIndexDoesNotExist,
    ValueOutOfRange,
    ValueTooHigh,
    ValueTooLow,
    MaxValLessThanMinVal,
    ResourceNotAvailable,
    GeneralError,
    DataCannotBeTransferred,
    DataCannotBeTransferredLocalControl,
    DataCannotBeTransferredDeviceState,
    ObjectDictionaryDynamicGenerationFails,
    NoDataAvailable,
    Unknown(u32),
}

impl SdoAbortCode {
    pub fn code(&self) -> u32 {
        match self {
            Self::ToggleBitNotAlternated => 0x0503_0000,
            Self::ProtocolTimedOut => 0x0504_0000,
            Self::CommandSpecifierInvalid => 0x0504_0001,
            Self::InvalidBlockSize => 0x0504_0002,
            Self::InvalidSequenceNumber => 0x0504_0003,
            Self::CrcError => 0x0504_0004,
            Self::OutOfMemory => 0x0504_0005,
            Self::UnsupportedAccess => 0x0601_0000,
            Self::ReadWriteOnlyObject => 0x0601_0001,
            Self::WriteReadOnlyObject => 0x0601_0002,
            Self::ObjectDoesNotExist => 0x0602_0000,
            Self::ObjectCannotBeMappedToPdo => 0x0604_0041,
            Self::PdoLengthExceeded => 0x0604_0042,
            Self::GeneralParameterIncompatibility => 0x0604_0043,
            Self::GeneralInternalIncompatibility => 0x0604_0047,
            Self::HardwareFault => 0x0606_0000,
            Self::DataTypeMismatch => 0x0607_0010,
            Self::DataTooLong => 0x0607_0012,
            Self::DataTooShort => 0x0607_0013,
            Self::SubIndexDoesNotExist => 0x0609_0011,
            Self::ValueOutOfRange => 0x0609_0030,
            Self::ValueTooHigh => 0x0609_0031,
            Self::ValueTooLow => 0x0609_0032,
            Self::MaxValLessThanMinVal => 0x0609_0036,
            Self::ResourceNotAvailable => 0x060A_0023,
            Self::GeneralError => 0x0800_0000,
            Self::DataCannotBeTransferred => 0x0800_0020,
            Self::DataCannotBeTransferredLocalControl => 0x0800_0021,
            Self::DataCannotBeTransferredDeviceState => 0x0800_0022,
            Self::ObjectDictionaryDynamicGenerationFails => 0x0800_0023,
            Self::NoDataAvailable => 0x0800_0024,
            Self::Unknown(val) => *val,
        }
    }
}

impl From<u32> for SdoAbortCode {
    fn from(val: u32) -> Self {
        match val {
            0x0503_0000 => SdoAbortCode::ToggleBitNotAlternated,
            0x0504_0000 => SdoAbortCode::ProtocolTimedOut,
            0x0504_0001 => SdoAbortCode::CommandSpecifierInvalid,
            0x0504_0002 => SdoAbortCode::InvalidBlockSize,
            0x0504_0003 => SdoAbortCode::InvalidSequenceNumber,
            0x0504_0004 => SdoAbortCode::CrcError,
            0x0504_0005 => SdoAbortCode::OutOfMemory,
            0x0601_0000 => SdoAbortCode::UnsupportedAccess,
            0x0601_0001 => SdoAbortCode::ReadWriteOnlyObject,
            0x0601_0002 => SdoAbortCode::WriteReadOnlyObject,
            0x0602_0000 => SdoAbortCode::ObjectDoesNotExist,
            0x0604_0041 => SdoAbortCode::ObjectCannotBeMappedToPdo,
            0x0604_0042 => SdoAbortCode::PdoLengthExceeded,
            0x0604_0043 => SdoAbortCode::GeneralParameterIncompatibility,
            0x0604_0047 => SdoAbortCode::GeneralInternalIncompatibility,
            0x0606_0000 => SdoAbortCode::HardwareFault,
            0x0607_0010 => SdoAbortCode::DataTypeMismatch,
            0x0607_0012 => SdoAbortCode::DataTooLong,
            0x0607_0013 => SdoAbortCode::DataTooShort,
            0x0609_0011 => SdoAbortCode::SubIndexDoesNotExist,
            0x0609_0030 => SdoAbortCode::ValueOutOfRange,
            0x0609_0031 => SdoAbortCode::ValueTooHigh,
            0x0609_0032 => SdoAbortCode::ValueTooLow,
            0x0609_0036 => SdoAbortCode::MaxValLessThanMinVal,
            0x060A_0023 => SdoAbortCode::ResourceNotAvailable,
            0x0800_0000 => SdoAbortCode::GeneralError,
            0x0800_0020 => SdoAbortCode::DataCannotBeTransferred,
            0x0800_0021 => SdoAbortCode::DataCannotBeTransferredLocalControl,
            0x0800_0022 => SdoAbortCode::DataCannotBeTransferredDeviceState,
            0x0800_0023 => SdoAbortCode::ObjectDictionaryDynamicGenerationFails,
            0x0800_0024 => SdoAbortCode::NoDataAvailable,
            other => SdoAbortCode::Unknown(other),
        }
    }
}

impl SdoAbortCode {
    pub fn description(&self) -> &'static str {
        match self {
            Self::ToggleBitNotAlternated => "Toggle bit not alternated",
            Self::ProtocolTimedOut => "SDO protocol timed out",
            Self::CommandSpecifierInvalid => "Client/server command specifier not valid or unknown",
            Self::InvalidBlockSize => "Invalid block size",
            Self::InvalidSequenceNumber => "Invalid sequence number",
            Self::CrcError => "CRC error",
            Self::OutOfMemory => "Out of memory",
            Self::UnsupportedAccess => "Unsupported access to an object",
            Self::ReadWriteOnlyObject => "Attempt to read a write-only object",
            Self::WriteReadOnlyObject => "Attempt to write a read-only object",
            Self::ObjectDoesNotExist => "Object does not exist in the object dictionary",
            Self::ObjectCannotBeMappedToPdo => "Object cannot be mapped to the PDO",
            Self::PdoLengthExceeded => "The number and length of the objects to be mapped would exceed PDO length",
            Self::GeneralParameterIncompatibility => "General parameter incompatibility",
            Self::GeneralInternalIncompatibility => "General internal incompatibility in the device",
            Self::HardwareFault => "Access failed due to an hardware error",
            Self::DataTypeMismatch => "Data type does not match, length of service parameter does not match",
            Self::DataTooLong => "Data type does not match, length of service parameter too long",
            Self::DataTooShort => "Data type does not match, length of service parameter too short",
            Self::SubIndexDoesNotExist => "Sub-index does not exist",
            Self::ValueOutOfRange => "Value range of parameter exceeded",
            Self::ValueTooHigh => "Value of parameter written too high",
            Self::ValueTooLow => "Value of parameter written too low",
            Self::MaxValLessThanMinVal => "Maximum value is less than minimum value",
            Self::ResourceNotAvailable => "Resource not available: SDO connection",
            Self::GeneralError => "General error",
            Self::DataCannotBeTransferred => "Data cannot be transferred or stored to the application",
            Self::DataCannotBeTransferredLocalControl => "Data cannot be transferred or stored to the application because of local control",
            Self::DataCannotBeTransferredDeviceState => "Data cannot be transferred or stored to the application because of the present device state",
            Self::ObjectDictionaryDynamicGenerationFails => "Object dictionary dynamic generation fails or no object dictionary is present",
            Self::NoDataAvailable => "No data available",
            Self::Unknown(_) => "Unknown SDO abort code",
        }
    }
}

/// SDO Message Types decoded from CAN traffic.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum SdoMessage {
    /// Client initiates upload (read): index, subindex
    UploadRequest {
        node_id: u8,
        index: u16,
        subindex: u8,
    },
    /// Server expedited upload response: index, subindex, data bytes
    ExpeditedUploadResponse {
        node_id: u8,
        index: u16,
        subindex: u8,
        data: Vec<u8>,
    },
    /// Client initiates expedited download (write): index, subindex, data bytes
    ExpeditedDownloadRequest {
        node_id: u8,
        index: u16,
        subindex: u8,
        data: Vec<u8>,
    },
    /// Server confirms download: index, subindex
    DownloadResponse {
        node_id: u8,
        index: u16,
        subindex: u8,
    },
    /// SDO Abort transfer: index, subindex, abort code
    Abort {
        node_id: u8,
        index: u16,
        subindex: u8,
        code: SdoAbortCode,
    },
    /// Other SDO transaction (segmented, block transfer)
    Other {
        node_id: u8,
        cs: u8,
        index: u16,
        subindex: u8,
    },
}

/// Build an SDO Initiate Upload Request frame (Read request).
pub fn build_sdo_read(node_id: u8, index: u16, subindex: u8) -> Result<CanFrame, CanError> {
    let cob_id = 0x600 + (node_id as u32);
    let idx_bytes = index.to_le_bytes();
    let payload = [
        0x40,
        idx_bytes[0],
        idx_bytes[1],
        subindex,
        0x00,
        0x00,
        0x00,
        0x00,
    ];
    CanFrame::new(cob_id, &payload)
}

/// Build an SDO Initiate Download Request frame (Write request). Supports 1..=4 bytes expedited.
pub fn build_sdo_write(
    node_id: u8,
    index: u16,
    subindex: u8,
    data: &[u8],
) -> Result<CanFrame, CanError> {
    if data.is_empty() || data.len() > 4 {
        return Err(CanError::InvalidDlc(data.len()));
    }
    let cob_id = 0x600 + (node_id as u32);
    let idx_bytes = index.to_le_bytes();
    let n = (4 - data.len()) as u8;
    // Command byte: 0x23 (expedited, size indicated: bits 2..3 = n, bit 1 = e, bit 0 = s)
    let cs = 0x23 | (n << 2);

    let mut payload = [0u8; 8];
    payload[0] = cs;
    payload[1] = idx_bytes[0];
    payload[2] = idx_bytes[1];
    payload[3] = subindex;
    payload[4..4 + data.len()].copy_from_slice(data);

    CanFrame::new(cob_id, &payload)
}

/// Build an SDO Abort frame.
pub fn build_sdo_abort(
    node_id: u8,
    index: u16,
    subindex: u8,
    code: SdoAbortCode,
) -> Result<CanFrame, CanError> {
    let cob_id = 0x580 + (node_id as u32);
    let idx_bytes = index.to_le_bytes();
    let code_bytes = code.code().to_le_bytes();

    let payload = [
        0x80,
        idx_bytes[0],
        idx_bytes[1],
        subindex,
        code_bytes[0],
        code_bytes[1],
        code_bytes[2],
        code_bytes[3],
    ];
    CanFrame::new(cob_id, &payload)
}

/// Decode a CAN frame into an SDO message if it matches SDO COB-IDs (0x581..0x5FF or 0x601..0x67F).
pub fn parse_sdo_frame(frame: &CanFrame) -> Option<SdoMessage> {
    let id = frame.id;
    let payload = frame.payload();
    if payload.len() < 4 {
        return None;
    }

    let cs = payload[0];
    let index = u16::from_le_bytes([payload[1], payload[2]]);
    let subindex = payload[3];

    // Client -> Server (0x601..=0x67F)
    if (0x601..=0x67F).contains(&id) {
        let node_id = (id - 0x600) as u8;
        if cs == 0x40 {
            return Some(SdoMessage::UploadRequest {
                node_id,
                index,
                subindex,
            });
        }
        if (cs & 0xE0) == 0x20 {
            // Initiate Download (Write)
            let is_expedited = (cs & 0x02) != 0;
            let size_indicated = (cs & 0x01) != 0;
            if is_expedited {
                let n = if size_indicated {
                    ((cs >> 2) & 0x03) as usize
                } else {
                    0
                };
                let data_len = 4 - n;
                let data_slice = if payload.len() >= 4 + data_len {
                    &payload[4..4 + data_len]
                } else {
                    &[]
                };
                return Some(SdoMessage::ExpeditedDownloadRequest {
                    node_id,
                    index,
                    subindex,
                    data: data_slice.to_vec(),
                });
            }
        }
        if cs == 0x80 && payload.len() >= 8 {
            let code_u32 = u32::from_le_bytes([payload[4], payload[5], payload[6], payload[7]]);
            return Some(SdoMessage::Abort {
                node_id,
                index,
                subindex,
                code: SdoAbortCode::from(code_u32),
            });
        }
        return Some(SdoMessage::Other {
            node_id,
            cs,
            index,
            subindex,
        });
    }

    // Server -> Client (0x581..=0x5FF)
    if (0x581..=0x5FF).contains(&id) {
        let node_id = (id - 0x580) as u8;
        if cs == 0x60 {
            return Some(SdoMessage::DownloadResponse {
                node_id,
                index,
                subindex,
            });
        }
        if (cs & 0xE0) == 0x40 {
            // Initiate Upload Response (Read reply)
            let is_expedited = (cs & 0x02) != 0;
            let size_indicated = (cs & 0x01) != 0;
            if is_expedited {
                let n = if size_indicated {
                    ((cs >> 2) & 0x03) as usize
                } else {
                    0
                };
                let data_len = 4 - n;
                let data_slice = if payload.len() >= 4 + data_len {
                    &payload[4..4 + data_len]
                } else {
                    &[]
                };
                return Some(SdoMessage::ExpeditedUploadResponse {
                    node_id,
                    index,
                    subindex,
                    data: data_slice.to_vec(),
                });
            }
        }
        if cs == 0x80 && payload.len() >= 8 {
            let code_u32 = u32::from_le_bytes([payload[4], payload[5], payload[6], payload[7]]);
            return Some(SdoMessage::Abort {
                node_id,
                index,
                subindex,
                code: SdoAbortCode::from(code_u32),
            });
        }
        return Some(SdoMessage::Other {
            node_id,
            cs,
            index,
            subindex,
        });
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_and_parse_sdo_read_request() {
        let frame = build_sdo_read(0x05, 0x1017, 0x00).unwrap();
        assert_eq!(frame.id, 0x605);
        assert_eq!(
            frame.payload(),
            &[0x40, 0x17, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00]
        );

        let msg = parse_sdo_frame(&frame).unwrap();
        assert_eq!(
            msg,
            SdoMessage::UploadRequest {
                node_id: 5,
                index: 0x1017,
                subindex: 0x00
            }
        );
    }

    #[test]
    fn test_build_and_parse_sdo_expedited_write() {
        // Write 2 bytes: 0x03E8 (1000 ms heartbeat producer time)
        let frame = build_sdo_write(0x05, 0x1017, 0x00, &[0xE8, 0x03]).unwrap();
        assert_eq!(frame.id, 0x605);
        // cs: 0x23 | (2 << 2) = 0x2B
        assert_eq!(frame.payload()[0], 0x2B);
        assert_eq!(&frame.payload()[4..6], &[0xE8, 0x03]);

        let msg = parse_sdo_frame(&frame).unwrap();
        assert_eq!(
            msg,
            SdoMessage::ExpeditedDownloadRequest {
                node_id: 5,
                index: 0x1017,
                subindex: 0x00,
                data: vec![0xE8, 0x03],
            }
        );
    }

    #[test]
    fn test_parse_sdo_upload_response() {
        // Server response for 4 bytes reading (e.g. Device Type = 0x00020192)
        // cs: 0x43 (expedited, 4 bytes, size indicated)
        let payload = [0x43, 0x00, 0x10, 0x00, 0x92, 0x01, 0x02, 0x00];
        let frame = CanFrame::new(0x585, &payload).unwrap();

        let msg = parse_sdo_frame(&frame).unwrap();
        assert_eq!(
            msg,
            SdoMessage::ExpeditedUploadResponse {
                node_id: 5,
                index: 0x1000,
                subindex: 0x00,
                data: vec![0x92, 0x01, 0x02, 0x00],
            }
        );
    }

    #[test]
    fn test_sdo_abort_decode() {
        let frame =
            build_sdo_abort(0x05, 0x1000, 0x01, SdoAbortCode::SubIndexDoesNotExist).unwrap();
        assert_eq!(frame.id, 0x585);
        assert_eq!(frame.payload()[0], 0x80);

        let msg = parse_sdo_frame(&frame).unwrap();
        assert_eq!(
            msg,
            SdoMessage::Abort {
                node_id: 5,
                index: 0x1000,
                subindex: 0x01,
                code: SdoAbortCode::SubIndexDoesNotExist,
            }
        );
        assert_eq!(
            SdoAbortCode::SubIndexDoesNotExist.description(),
            "Sub-index does not exist"
        );
    }
}
