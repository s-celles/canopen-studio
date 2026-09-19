/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Electronic Data Sheet (EDS) & Object Dictionary Engine (CiA 306).
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fmt;

/// CiA 301 / 306 Access Type for Object Dictionary parameters.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AccessType {
    ReadOnly,
    WriteOnly,
    ReadWrite,
    Constant,
    Unknown,
}

impl std::str::FromStr for AccessType {
    type Err = std::convert::Infallible;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Ok(match s.trim().to_lowercase().as_str() {
            "ro" | "readonly" => AccessType::ReadOnly,
            "wo" | "writeonly" => AccessType::WriteOnly,
            "rw" | "readwrite" => AccessType::ReadWrite,
            "const" | "constant" => AccessType::Constant,
            _ => AccessType::Unknown,
        })
    }
}

impl AccessType {
    pub fn as_str(&self) -> &'static str {
        match self {
            AccessType::ReadOnly => "ro",
            AccessType::WriteOnly => "wo",
            AccessType::ReadWrite => "rw",
            AccessType::Constant => "const",
            AccessType::Unknown => "unknown",
        }
    }
}

/// Standard CANopen data types (CiA 301).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DataType {
    Boolean,       // 0x0001
    Integer8,      // 0x0002
    Integer16,     // 0x0003
    Integer32,     // 0x0004
    Unsigned8,     // 0x0005
    Unsigned16,    // 0x0006
    Unsigned32,    // 0x0007
    Real32,        // 0x0008
    VisibleString, // 0x0009
    OctetString,   // 0x000A
    Other(u16),
}

impl DataType {
    pub fn from_u16(code: u16) -> Self {
        match code {
            0x0001 => DataType::Boolean,
            0x0002 => DataType::Integer8,
            0x0003 => DataType::Integer16,
            0x0004 => DataType::Integer32,
            0x0005 => DataType::Unsigned8,
            0x0006 => DataType::Unsigned16,
            0x0007 => DataType::Unsigned32,
            0x0008 => DataType::Real32,
            0x0009 => DataType::VisibleString,
            0x000A => DataType::OctetString,
            other => DataType::Other(other),
        }
    }

    pub fn bit_length(&self) -> Option<usize> {
        match self {
            DataType::Boolean => Some(1),
            DataType::Integer8 | DataType::Unsigned8 => Some(8),
            DataType::Integer16 | DataType::Unsigned16 => Some(16),
            DataType::Integer32 | DataType::Unsigned32 | DataType::Real32 => Some(32),
            _ => None,
        }
    }

    pub fn type_name(&self) -> &'static str {
        match self {
            DataType::Boolean => "BOOLEAN",
            DataType::Integer8 => "INTEGER8",
            DataType::Integer16 => "INTEGER16",
            DataType::Integer32 => "INTEGER32",
            DataType::Unsigned8 => "UNSIGNED8",
            DataType::Unsigned16 => "UNSIGNED16",
            DataType::Unsigned32 => "UNSIGNED32",
            DataType::Real32 => "REAL32",
            DataType::VisibleString => "VISIBLE_STRING",
            DataType::OctetString => "OCTET_STRING",
            DataType::Other(_) => "USER_DEFINED",
        }
    }
}

/// An individual Object Dictionary entry (index, subindex, metadata).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ObjectEntry {
    pub index: u16,
    pub subindex: u8,
    pub name: String,
    pub object_type: u8,
    pub data_type: DataType,
    pub access: AccessType,
    pub default_value: Option<String>,
    pub pdo_mapping: bool,
    pub low_limit: Option<String>,
    pub high_limit: Option<String>,
}

/// Metadata extracted from the `[FileInfo]` section of an EDS file.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileInfo {
    pub file_name: String,
    pub file_version: String,
    pub description: String,
    pub eds_version: String,
    pub created_by: String,
}

/// Metadata extracted from the `[DeviceInfo]` section of an EDS file.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeviceInfo {
    pub vendor_name: String,
    pub vendor_number: Option<u32>,
    pub product_name: String,
    pub product_number: Option<u32>,
    pub revision_number: Option<u32>,
    pub order_code: String,
}

/// Error encountered while parsing an EDS file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EdsError {
    InvalidFormat(String),
    ParseIntError(String),
}

impl fmt::Display for EdsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            EdsError::InvalidFormat(msg) => write!(f, "EDS invalid format: {}", msg),
            EdsError::ParseIntError(msg) => write!(f, "EDS integer parse error: {}", msg),
        }
    }
}

impl std::error::Error for EdsError {}

/// Parsed CiA 306 Electronic Data Sheet (EDS) Object Dictionary.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct EdsFile {
    pub file_info: FileInfo,
    pub device_info: DeviceInfo,
    pub entries: BTreeMap<(u16, u8), ObjectEntry>,
}

impl std::str::FromStr for EdsFile {
    type Err = EdsError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Self::parse(s)
    }
}

impl EdsFile {
    /// Parse an EDS file from text content.
    pub fn parse(content: &str) -> Result<Self, EdsError> {
        let mut eds = EdsFile::default();

        let mut current_section = String::new();
        let mut section_key_values: BTreeMap<String, String> = BTreeMap::new();

        let flush_section = |section: &str,
                             kv: &BTreeMap<String, String>,
                             target: &mut EdsFile|
         -> Result<(), EdsError> {
            if section.is_empty() {
                return Ok(());
            }

            let sec_lower = section.to_lowercase();
            if sec_lower == "fileinfo" {
                target.file_info.file_name = kv.get("filename").cloned().unwrap_or_default();
                target.file_info.file_version = kv.get("fileversion").cloned().unwrap_or_default();
                target.file_info.description = kv.get("description").cloned().unwrap_or_default();
                target.file_info.eds_version = kv.get("edsversion").cloned().unwrap_or_default();
                target.file_info.created_by = kv.get("createdby").cloned().unwrap_or_default();
                return Ok(());
            }

            if sec_lower == "deviceinfo" {
                target.device_info.vendor_name = kv.get("vendorname").cloned().unwrap_or_default();
                target.device_info.product_name =
                    kv.get("productname").cloned().unwrap_or_default();
                target.device_info.order_code = kv.get("ordercode").cloned().unwrap_or_default();

                if let Some(v) = kv.get("vendornumber") {
                    target.device_info.vendor_number = parse_int_auto(v).ok();
                }
                if let Some(v) = kv.get("productnumber") {
                    target.device_info.product_number = parse_int_auto(v).ok();
                }
                if let Some(v) = kv.get("revisionnumber") {
                    target.device_info.revision_number = parse_int_auto(v).ok();
                }
                return Ok(());
            }

            // Check if section is an object definition, e.g. [1000] or [1018sub1]
            if let Some((index, subindex)) = parse_section_index_subindex(section) {
                let name = kv
                    .get("parametername")
                    .cloned()
                    .unwrap_or_else(|| format!("Object 0x{:04X}:{:02X}", index, subindex));
                let object_type = kv
                    .get("objecttype")
                    .and_then(|s| parse_int_auto(s).ok())
                    .unwrap_or(7) as u8;

                let data_type_code = kv
                    .get("datatype")
                    .and_then(|s| parse_int_auto(s).ok())
                    .unwrap_or(0x0007) as u16;
                let data_type = DataType::from_u16(data_type_code);

                let access = kv
                    .get("accesstype")
                    .and_then(|s| s.parse::<AccessType>().ok())
                    .unwrap_or(AccessType::Unknown);

                let default_value = kv.get("defaultvalue").cloned();
                let pdo_mapping = kv
                    .get("pdomapping")
                    .map(|s| s.trim() == "1" || s.trim().to_lowercase() == "true")
                    .unwrap_or(false);

                let low_limit = kv.get("lowlimit").cloned();
                let high_limit = kv.get("highlimit").cloned();

                let entry = ObjectEntry {
                    index,
                    subindex,
                    name,
                    object_type,
                    data_type,
                    access,
                    default_value,
                    pdo_mapping,
                    low_limit,
                    high_limit,
                };
                target.entries.insert((index, subindex), entry);
            }

            Ok(())
        };

        for raw_line in content.lines() {
            let line = raw_line.trim();
            // Skip comments and empty lines
            if line.is_empty() || line.starts_with(';') || line.starts_with('#') {
                continue;
            }

            if line.starts_with('[') && line.ends_with(']') {
                flush_section(&current_section, &section_key_values, &mut eds)?;
                current_section = line[1..line.len() - 1].trim().to_string();
                section_key_values.clear();
            } else if let Some((k, v)) = line.split_once('=') {
                let key_norm = k.trim().to_lowercase();
                let val = v.trim().to_string();
                section_key_values.insert(key_norm, val);
            }
        }

        // Flush trailing section
        flush_section(&current_section, &section_key_values, &mut eds)?;

        Ok(eds)
    }

    /// Retrieve an entry by (index, subindex).
    pub fn get(&self, index: u16, subindex: u8) -> Option<&ObjectEntry> {
        self.entries.get(&(index, subindex))
    }

    /// Retrieve all entries for a specific index (subindex 0..=255).
    pub fn get_index_subindices(&self, index: u16) -> Vec<&ObjectEntry> {
        self.entries
            .range((index, 0)..=(index, u8::MAX))
            .map(|(_, entry)| entry)
            .collect()
    }

    /// Search entries by parameter name substring (case-insensitive).
    pub fn find_by_name(&self, query: &str) -> Vec<&ObjectEntry> {
        let q = query.to_lowercase();
        self.entries
            .values()
            .filter(|e| e.name.to_lowercase().contains(&q))
            .collect()
    }

    /// Return all objects marked as PDO mappable (`PDOMapping = 1`).
    pub fn pdo_mappable(&self) -> Vec<&ObjectEntry> {
        self.entries.values().filter(|e| e.pdo_mapping).collect()
    }

    /// Automatically build a `PdoMapping` using the PDO mapping object at `mapping_index`
    /// (e.g. `0x1A00` for TPDO1 mapping, `0x1600` for RPDO1 mapping).
    /// Resolves target object names and data types directly from the dictionary.
    pub fn create_pdo_mapping(&self, cob_id: u32, mapping_index: u16) -> crate::pdo::PdoMapping {
        let name = format!("PDO_0x{:04X}", mapping_index);
        let mut mapping = crate::pdo::PdoMapping::new(cob_id, &name);
        let sub_entries = self.get_index_subindices(mapping_index);
        let mut current_bit_offset: u8 = 0;

        for entry in sub_entries {
            if entry.subindex == 0 {
                continue;
            }
            if let Some(ref val_str) = entry.default_value {
                let record = parse_int_auto(val_str).unwrap_or(0);
                if record == 0 {
                    continue;
                }
                let target_index = (record >> 16) as u16;
                let target_sub = ((record >> 8) & 0xFF) as u8;
                let bit_len = (record & 0xFF) as u8;

                let (sig_name, sig_type) =
                    if let Some(target_obj) = self.get(target_index, target_sub) {
                        let st = match target_obj.data_type {
                            DataType::Boolean => crate::pdo::SignalType::Bool,
                            DataType::Integer8 => crate::pdo::SignalType::Int8,
                            DataType::Integer16 => crate::pdo::SignalType::Int16,
                            DataType::Integer32 => crate::pdo::SignalType::Int32,
                            DataType::Unsigned8 => crate::pdo::SignalType::Uint8,
                            DataType::Unsigned16 => crate::pdo::SignalType::Uint16,
                            DataType::Unsigned32 => crate::pdo::SignalType::Uint32,
                            _ => crate::pdo::SignalType::Uint32,
                        };
                        (target_obj.name.clone(), st)
                    } else {
                        (
                            format!("Obj_{:04X}_{:02X}", target_index, target_sub),
                            crate::pdo::SignalType::Uint32,
                        )
                    };

                let sig = crate::pdo::SignalDefinition::new(
                    &sig_name,
                    current_bit_offset,
                    bit_len,
                    sig_type,
                    1.0,
                    0.0,
                    "",
                );
                mapping.add_signal(sig);
                current_bit_offset += bit_len;
            }
        }
        mapping
    }

    /// Total number of object entries in the dictionary.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Return true if the dictionary contains no entries.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

/// Parse string as hex (`0x1000` or `1000`) or decimal (`4096`).
fn parse_int_auto(s: &str) -> Result<u32, EdsError> {
    let trimmed = s.trim();
    if let Some(hex_str) = trimmed
        .strip_prefix("0x")
        .or_else(|| trimmed.strip_prefix("0X"))
    {
        u32::from_str_radix(hex_str, 16)
            .map_err(|e| EdsError::ParseIntError(format!("Invalid hex {}: {}", s, e)))
    } else if let Ok(val) = u32::from_str_radix(trimmed, 16) {
        // Many EDS files use bare hex without 0x prefix for 4-digit indices
        if trimmed.len() == 4 || trimmed.chars().any(|c| c.is_ascii_alphabetic()) {
            Ok(val)
        } else {
            trimmed
                .parse::<u32>()
                .map_err(|e| EdsError::ParseIntError(format!("Invalid int {}: {}", s, e)))
        }
    } else {
        trimmed
            .parse::<u32>()
            .map_err(|e| EdsError::ParseIntError(format!("Invalid int {}: {}", s, e)))
    }
}

/// Parse section name like `1000`, `0x1000`, `1018sub1`, `0x1018sub02`.
fn parse_section_index_subindex(section: &str) -> Option<(u16, u8)> {
    let s = section.trim();
    let lower = s.to_lowercase();

    if let Some(pos) = lower.find("sub") {
        let idx_part = &s[..pos];
        let sub_part = &s[pos + 3..];

        let idx_str = idx_part.strip_prefix("0x").unwrap_or(idx_part);
        let index = u16::from_str_radix(idx_str, 16).ok()?;

        let sub_str = sub_part.strip_prefix("0x").unwrap_or(sub_part);
        // Sub-index is usually hex or decimal, parse as hex if possible
        let subindex = u8::from_str_radix(sub_str, 16)
            .or_else(|_| sub_str.parse::<u8>())
            .ok()?;

        Some((index, subindex))
    } else {
        let idx_str = s
            .strip_prefix("0x")
            .or_else(|| s.strip_prefix("0X"))
            .unwrap_or(s);
        let index = u16::from_str_radix(idx_str, 16).ok()?;
        Some((index, 0))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE_EDS: &str = r#"
; CANopen Electronic Data Sheet Sample
[FileInfo]
FileName=Sevcon_Gen4.eds
FileVersion=2
FileRevision=1
EDSVersion=4.0
Description=SEVCON Gen4 AC Motor Controller
CreatedBy=CANopen Studio

[DeviceInfo]
VendorName=SEVCON
VendorNumber=0x0000001A
ProductName=Gen4 Size 4
ProductNumber=0x00001234
RevisionNumber=0x00010000
OrderCode=GEN4-AC-48V

[1000]
ParameterName=Device Type
ObjectType=7
DataType=0x0007
AccessType=ro
DefaultValue=0x00020192
PDOMapping=0

[1001]
ParameterName=Error Register
ObjectType=7
DataType=0x0005
AccessType=ro
DefaultValue=0
PDOMapping=1

[1008]
ParameterName=Manufacturer Device Name
ObjectType=7
DataType=0x0009
AccessType=const
DefaultValue=Gen4-Size4
PDOMapping=0

[1018]
ParameterName=Identity Object
ObjectType=9
SubNumber=4

[1018sub1]
ParameterName=Vendor ID
ObjectType=7
DataType=0x0007
AccessType=ro
DefaultValue=0x0000001A
PDOMapping=0

[1018sub2]
ParameterName=Product Code
ObjectType=7
DataType=0x0007
AccessType=ro
DefaultValue=0x00001234
PDOMapping=0

[6040]
ParameterName=CiA 402 Controlword
ObjectType=7
DataType=0x0006
AccessType=rw
DefaultValue=0x0000
PDOMapping=1

[6041]
ParameterName=CiA 402 Statusword
ObjectType=7
DataType=0x0006
AccessType=ro
DefaultValue=0x0000
PDOMapping=1

[1A00]
ParameterName=TPDO1 Mapping
ObjectType=9
SubNumber=2

[1A00sub1]
ParameterName=TPDO1 Mapping Entry 1
ObjectType=7
DataType=0x0007
AccessType=rw
DefaultValue=0x60410010
PDOMapping=0

[1A00sub2]
ParameterName=TPDO1 Mapping Entry 2
ObjectType=7
DataType=0x0007
AccessType=rw
DefaultValue=0x10010008
PDOMapping=0
"#;

    #[test]
    fn test_parse_file_info_and_device_info() {
        let eds = EdsFile::parse(SAMPLE_EDS).unwrap();
        assert_eq!(eds.file_info.file_name, "Sevcon_Gen4.eds");
        assert_eq!(eds.file_info.eds_version, "4.0");
        assert_eq!(eds.device_info.vendor_name, "SEVCON");
        assert_eq!(eds.device_info.vendor_number, Some(0x1A));
        assert_eq!(eds.device_info.product_name, "Gen4 Size 4");
    }

    #[test]
    fn test_parse_objects() {
        let eds = EdsFile::parse(SAMPLE_EDS).unwrap();

        // 0x1000: Device Type
        let obj1000 = eds.get(0x1000, 0).unwrap();
        assert_eq!(obj1000.name, "Device Type");
        assert_eq!(obj1000.data_type, DataType::Unsigned32);
        assert_eq!(obj1000.access, AccessType::ReadOnly);
        assert_eq!(obj1000.default_value.as_deref(), Some("0x00020192"));
        assert!(!obj1000.pdo_mapping);

        // 0x1001: Error Register (PDO mappable)
        let obj1001 = eds.get(0x1001, 0).unwrap();
        assert_eq!(obj1001.name, "Error Register");
        assert_eq!(obj1001.data_type, DataType::Unsigned8);
        assert!(obj1001.pdo_mapping);

        // 0x1018sub1: Vendor ID
        let obj1018_1 = eds.get(0x1018, 1).unwrap();
        assert_eq!(obj1018_1.name, "Vendor ID");
        assert_eq!(obj1018_1.data_type, DataType::Unsigned32);

        // 0x1018sub2: Product Code
        let obj1018_2 = eds.get(0x1018, 2).unwrap();
        assert_eq!(obj1018_2.name, "Product Code");
    }

    #[test]
    fn test_pdo_mappable_and_search() {
        let eds = EdsFile::parse(SAMPLE_EDS).unwrap();

        let mappable = eds.pdo_mappable();
        // 1001, 6040, 6041
        assert_eq!(mappable.len(), 3);

        let statuswords = eds.find_by_name("statusword");
        assert_eq!(statuswords.len(), 1);
        assert_eq!(statuswords[0].index, 0x6041);
        assert_eq!(statuswords[0].subindex, 0);

        let subs = eds.get_index_subindices(0x1018);
        // subindex 0 ([1018]), subindex 1 ([1018sub1]), subindex 2 ([1018sub2])
        assert_eq!(subs.len(), 3);
    }

    #[test]
    fn test_data_type_bit_lengths() {
        assert_eq!(DataType::Boolean.bit_length(), Some(1));
        assert_eq!(DataType::Integer8.bit_length(), Some(8));
        assert_eq!(DataType::Unsigned16.bit_length(), Some(16));
        assert_eq!(DataType::Unsigned32.bit_length(), Some(32));
    }

    #[test]
    fn test_create_pdo_mapping_from_eds() {
        let eds = EdsFile::parse(SAMPLE_EDS).unwrap();
        let pdo = eds.create_pdo_mapping(0x181, 0x1A00);
        assert_eq!(pdo.cob_id, 0x181);
        assert_eq!(pdo.signals.len(), 2);
        assert_eq!(pdo.signals[0].name, "CiA 402 Statusword");
        assert_eq!(pdo.signals[0].bit_length, 16);
        assert_eq!(pdo.signals[1].name, "Error Register");
        assert_eq!(pdo.signals[1].bit_length, 8);

        let frame =
            crate::CanFrame::new(0x181, &[0x37, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]).unwrap();
        let decoded = pdo.decode_frame(&frame);
        assert_eq!(decoded.len(), 2);
        assert_eq!(decoded[0].name, "CiA 402 Statusword");
        assert_eq!(decoded[0].value, 0x0237 as f64);
        assert_eq!(decoded[1].name, "Error Register");
        assert_eq!(decoded[1].value, 0.0);
    }
}
