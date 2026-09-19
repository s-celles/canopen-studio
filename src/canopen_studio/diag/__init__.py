"""
Vehicle diagnostics: OBD-II (SAE J1979) over an ELM327 or over a native CAN adapter.

Diagnostics deliberately sit beside the CAN stack rather than inside it. The CAN adapter
abstraction of `canopen_studio.interfaces` yields raw frames; a diagnostic session is
transactional, segmented by ISO 15765-2, and — with an ELM327 — not made of frames at all.
`DiagnosticInterface` is the boundary between the two worlds.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from .interface import (
    DEFAULT_TIMEOUT,
    NEGATIVE_RESPONSE,
    POSITIVE_RESPONSE_OFFSET,
    DiagnosticError,
    DiagnosticInterface,
    DiagnosticResponse,
    DiagnosticTimeout,
    NegativeResponse,
    NotConnectedError,
    ProtocolError,
    ResponseCode,
    TransportError,
    first_payload,
)

__all__ = [
    "DEFAULT_TIMEOUT",
    "NEGATIVE_RESPONSE",
    "POSITIVE_RESPONSE_OFFSET",
    "DiagnosticError",
    "DiagnosticInterface",
    "DiagnosticResponse",
    "DiagnosticTimeout",
    "NegativeResponse",
    "NotConnectedError",
    "ProtocolError",
    "ResponseCode",
    "TransportError",
    "first_payload",
]
