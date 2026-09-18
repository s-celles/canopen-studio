"""
CANopen Protocol Stack and Extensible Application Decoder Framework.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from .types import (
    CanopenMessage,
    CanopenService,
    NmtState,
    NmtCommand,
)
from .base_decoder import BaseDeviceDecoder
from .registry import (
    DecoderRegistry,
    register_decoder,
    get_default_registry,
)
from .canopen_layer import CANopenLayer

# Import decoders package to trigger auto-registration
from . import decoders

__all__ = [
    "CANopenLayer",
    "CanopenMessage",
    "CanopenService",
    "NmtState",
    "NmtCommand",
    "BaseDeviceDecoder",
    "DecoderRegistry",
    "register_decoder",
    "get_default_registry",
    "decoders",
]
