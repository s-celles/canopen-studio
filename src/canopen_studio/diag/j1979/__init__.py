"""
SAE J1979 (OBD-II) application layer.

Modes 01 through 0A, supported-PID discovery by bitmask, ISO 15031-6 trouble codes and
vehicle information, all decoded from declarative definitions rather than hard-coded
tables, over either diagnostic interface.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from .client import J1979Client, VehicleIdentity
from .discovery import SupportedPids, decode_support_bitmask, discover_supported_pids
from .dtc import DTC_MODES, DtcError, TroubleCode, decode_dtc, decode_dtc_response, encode_dtc
from .formula import Formula, FormulaError
from .pids import (
    MODE_NAMES,
    PidDefinition,
    PidError,
    PidTable,
    PidValue,
    format_key,
    parse_key,
)
from .vin import VinError, VinInfo, extract_vin, is_valid_vin, parse_vin

__all__ = [
    "DTC_MODES",
    "MODE_NAMES",
    "DtcError",
    "J1979Client",
    "SupportedPids",
    "TroubleCode",
    "VehicleIdentity",
    "VinError",
    "VinInfo",
    "decode_dtc",
    "decode_dtc_response",
    "decode_support_bitmask",
    "discover_supported_pids",
    "encode_dtc",
    "extract_vin",
    "is_valid_vin",
    "parse_vin",
    "Formula",
    "FormulaError",
    "PidDefinition",
    "PidError",
    "PidTable",
    "PidValue",
    "format_key",
    "parse_key",
]
