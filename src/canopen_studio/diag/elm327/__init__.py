"""
ELM327 adapter backend.

An ELM327 is not a transparent CAN bridge. It runs its own protocol autodetection and its
own ISO-TP handling, and answers in ASCII hexadecimal terminated by a '>' prompt. This
package wraps one behind the `DiagnosticInterface` contract so that callers cannot tell
it apart from a native CAN session.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from .capabilities import ElmCapabilities, probe_capabilities
from .interface import ElmDiagnosticInterface, UnsupportedElmProtocol
from .protocol import (
    ElmBufferFull,
    ElmBusError,
    ElmCommandNotSupported,
    ElmError,
    ElmNoData,
    ElmProtocol,
    ElmReply,
    ElmStopped,
    ElmUnableToConnect,
    clean_reply,
)
from .transport import (
    DEFAULT_BAUDRATE,
    DEFAULT_TCP_PORT,
    ElmTransport,
    SerialElmTransport,
    TcpElmTransport,
)

__all__ = [
    "DEFAULT_BAUDRATE",
    "ElmBufferFull",
    "ElmBusError",
    "ElmCapabilities",
    "ElmCommandNotSupported",
    "ElmDiagnosticInterface",
    "ElmError",
    "ElmNoData",
    "ElmProtocol",
    "ElmReply",
    "ElmStopped",
    "ElmUnableToConnect",
    "UnsupportedElmProtocol",
    "clean_reply",
    "probe_capabilities",
    "DEFAULT_TCP_PORT",
    "ElmTransport",
    "SerialElmTransport",
    "TcpElmTransport",
]
