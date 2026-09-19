"""
Transport-neutral contract for vehicle diagnostic sessions.

This is deliberately *not* the CAN adapter abstraction of `canopen_studio.interfaces`.
That one yields raw frames: everything downstream of it — the CANopen layer, the trace,
the plotter, the bridge — consumes `can.Message` objects. An ELM327 cannot honour that
contract. It runs its own protocol autodetection and its own ISO-TP reassembly, and hands
back diagnostic *responses* as ASCII hexadecimal, never frames.

So diagnostics get their own abstraction. A `DiagnosticInterface` answers one question:
given a request payload, which ECUs replied and what did they say. Two implementations
sit behind it, an ELM327 over a serial port and a native CAN adapter speaking ISO 15765-2,
and callers cannot tell them apart.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

# A request that goes unanswered for this long is treated as unsupported rather than
# retried: J1979 ECUs answer in tens of milliseconds, and a scan walks hundreds of PIDs.
DEFAULT_TIMEOUT = 1.0

# Marker byte that opens a negative response, per ISO 14229-1.
NEGATIVE_RESPONSE = 0x7F

# A positive response echoes the requested mode with this offset added.
POSITIVE_RESPONSE_OFFSET = 0x40


class DiagnosticError(Exception):
    """Base class for every failure raised by the diagnostic layer."""


class NotConnectedError(DiagnosticError):
    """A request was issued before the interface was opened."""


class DiagnosticTimeout(DiagnosticError):
    """No ECU answered within the allotted time."""


class TransportError(DiagnosticError):
    """The underlying link failed: serial port, socket, or CAN bus."""


class ProtocolError(DiagnosticError):
    """A reply arrived but could not be interpreted as a diagnostic response."""


class NegativeResponse(DiagnosticError):
    """An ECU understood the request and refused it."""

    def __init__(self, source: int, rejected_service: int, response_code: int):
        self.source = source
        self.rejected_service = rejected_service
        self.response_code = response_code
        super().__init__(
            f"ECU 0x{source:X} refused service 0x{rejected_service:02X}: "
            f"{ResponseCode.describe(response_code)} (0x{response_code:02X})"
        )


class ResponseCode:
    """
    Negative response codes of ISO 14229-1, as far as J1979 uses them.

    Manufacturers add their own, so an unlisted code is described rather than rejected.
    """

    GENERAL_REJECT = 0x10
    SERVICE_NOT_SUPPORTED = 0x11
    SUBFUNCTION_NOT_SUPPORTED = 0x12
    INCORRECT_MESSAGE_LENGTH = 0x13
    BUSY_REPEAT_REQUEST = 0x21
    CONDITIONS_NOT_CORRECT = 0x22
    REQUEST_OUT_OF_RANGE = 0x31
    SECURITY_ACCESS_DENIED = 0x33
    INVALID_KEY = 0x35
    UPLOAD_DOWNLOAD_NOT_ACCEPTED = 0x70
    RESPONSE_PENDING = 0x78
    SERVICE_NOT_SUPPORTED_IN_SESSION = 0x7F

    _NAMES = {
        0x10: "General reject",
        0x11: "Service not supported",
        0x12: "Subfunction not supported",
        0x13: "Incorrect message length or invalid format",
        0x14: "Response too long",
        0x21: "Busy, repeat request",
        0x22: "Conditions not correct",
        0x24: "Request sequence error",
        0x31: "Request out of range",
        0x33: "Security access denied",
        0x35: "Invalid key",
        0x36: "Exceeded number of attempts",
        0x37: "Required time delay not expired",
        0x70: "Upload/download not accepted",
        0x72: "General programming failure",
        0x78: "Request correctly received, response pending",
        0x7E: "Subfunction not supported in active session",
        0x7F: "Service not supported in active session",
    }

    @classmethod
    def describe(cls, code: int) -> str:
        """Name a negative response code, falling back to its hexadecimal value."""
        return cls._NAMES.get(code, f"Unknown response code 0x{code:02X}")


@dataclass(frozen=True)
class DiagnosticResponse:
    """
    One ECU's answer to one diagnostic request.

    `data` is the complete reassembled response, starting with the service byte: the
    ISO-TP framing and the ELM327 ASCII have both been stripped by the time it gets here.
    `source` identifies the ECU — its CAN response identifier (0x7E8..0x7EF) for both
    backends, since the ELM327 reports it too once headers are enabled.
    """

    source: int
    data: bytes

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("a diagnostic response carries at least a service byte")

    @property
    def service_id(self) -> int:
        """The first byte: the echoed service, or 0x7F for a rejection."""
        return self.data[0]

    @property
    def payload(self) -> bytes:
        """Everything after the service byte, PID echo included."""
        return self.data[1:]

    @property
    def is_negative(self) -> bool:
        """Whether the ECU refused the request."""
        return self.service_id == NEGATIVE_RESPONSE and len(self.data) >= 3

    @property
    def request_mode(self) -> Optional[int]:
        """The mode this answers, or None when the reply is a rejection."""
        if self.is_negative:
            return None
        return self.service_id - POSITIVE_RESPONSE_OFFSET

    @property
    def rejected_service(self) -> Optional[int]:
        """For a rejection, the service that was refused."""
        return self.data[1] if self.is_negative else None

    @property
    def response_code(self) -> Optional[int]:
        """For a rejection, why it was refused."""
        return self.data[2] if self.is_negative else None

    def hex(self) -> str:
        """Render the response the way a trace line shows it."""
        return self.data.hex(" ").upper()

    def __str__(self) -> str:
        return f"0x{self.source:X}: {self.hex()}"


class DiagnosticInterface(ABC):
    """
    A diagnostic link to a vehicle, independent of how it is carried.

    Subclasses implement three hooks — `_open`, `_close` and `_request` — and inherit the
    session lifecycle, the request validation and the response filtering. The high-level
    J1979 operations (read a PID, read and clear DTCs, read the VIN) live in
    `canopen_studio.diag.j1979` and work unchanged over either implementation.

    Read-only is the default posture: this class never sends anything the caller did not
    ask for, and the services that write to an ECU go through
    `canopen_studio.diag.security` rather than through `request()`.
    """

    def __init__(self) -> None:
        self._open_flag = False
        self.default_timeout = DEFAULT_TIMEOUT
        self._j1979: Any = None

    # -- Identity ----------------------------------------------------------

    @property
    @abstractmethod
    def description(self) -> str:
        """Short human-readable description of the link, for logs and the status bar."""

    # -- Lifecycle ---------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """Whether a session is currently established."""
        return self._open_flag

    def open(self) -> None:
        """Establish the session. Opening an already-open interface does nothing."""
        if self._open_flag:
            return
        self._open()
        self._open_flag = True

    def close(self) -> None:
        """Release the link. Closing an already-closed interface does nothing."""
        if not self._open_flag:
            return
        try:
            self._close()
        finally:
            self._open_flag = False

    def __enter__(self) -> "DiagnosticInterface":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @abstractmethod
    def _open(self) -> None:
        """Bring the link up. Called once, only when the interface is closed."""

    @abstractmethod
    def _close(self) -> None:
        """Take the link down. Called once, only when the interface is open."""

    # -- Requests ----------------------------------------------------------

    @abstractmethod
    def _request(self, payload: bytes, timeout: float) -> List[DiagnosticResponse]:
        """
        Send one request and collect the replies.

        Returns an empty list when nothing answered: an unsupported PID is an ordinary
        outcome during a scan, not an error, so it must not raise.
        """

    def request(self, payload: bytes, timeout: Optional[float] = None) -> List[DiagnosticResponse]:
        """
        Send a raw request payload and return every reply, in arrival order.

        Args:
            payload: The service bytes, starting with the mode. No ISO-TP framing.
            timeout: Seconds to wait for replies, defaulting to `default_timeout`.
        """
        if not self._open_flag:
            raise NotConnectedError("the diagnostic interface is not open — call open() first")
        if not payload:
            raise ValueError("a diagnostic request carries at least a mode byte")
        return self._request(bytes(payload), self.default_timeout if timeout is None else timeout)

    def service(self, mode: int, *args: int, timeout: Optional[float] = None) -> List[DiagnosticResponse]:
        """
        Send a request built from a mode and its arguments.

        Args:
            mode: The service identifier, e.g. 0x01 for current data.
            args: Further request bytes, typically a PID.
            timeout: Seconds to wait for replies, defaulting to `default_timeout`.
        """
        for value in (mode, *args):
            if not 0 <= value <= 0xFF:
                raise ValueError(f"request byte {value} does not fit in a byte")
        return self.request(bytes((mode, *args)), timeout=timeout)

    def positive_responses(
        self,
        mode: int,
        *args: int,
        timeout: Optional[float] = None,
        raise_on_negative: bool = False,
    ) -> List[DiagnosticResponse]:
        """
        Send a request and keep only the replies that actually answer it.

        Rejections are dropped, and so are replies echoing a different mode: on a shared
        bus a late answer to an earlier request can still be in flight.

        Args:
            mode: The service identifier.
            args: Further request bytes, typically a PID.
            timeout: Seconds to wait for replies, defaulting to `default_timeout`.
            raise_on_negative: Raise `NegativeResponse` instead of dropping a rejection,
                for callers that need the reason rather than an empty list.
        """
        replies = self.service(mode, *args, timeout=timeout)
        expected = mode + POSITIVE_RESPONSE_OFFSET
        kept = []
        for reply in replies:
            if reply.is_negative:
                if raise_on_negative and reply.rejected_service == mode:
                    raise NegativeResponse(reply.source, mode, reply.response_code or 0)
                continue
            if reply.service_id == expected:
                kept.append(reply)
        return kept

    # -- High-level OBD-II operations --------------------------------------
    #
    # Thin delegators, so that both backends literally expose the same high-level API.
    # The work lives in `canopen_studio.diag.j1979`, which is written once against this
    # contract; the import is local because that package builds on this module.

    def j1979(self, table: Any = None) -> Any:
        """
        The J1979 client for this session, created on first use and then reused.

        Args:
            table: A `PidTable` to decode with, replacing the current one. Typically the
                table of a resolved vehicle profile.
        """
        from .j1979.client import J1979Client

        if self._j1979 is None:
            self._j1979 = J1979Client(self, table=table)
        elif table is not None:
            self._j1979.table = table
        return self._j1979

    def supported_pids(self, mode: int = 0x01, refresh: bool = False) -> Any:
        """Which PIDs of a mode the vehicle implements, discovered from its bitmasks."""
        return self.j1979().supported_pids(mode, refresh=refresh)

    def read_pid(self, pid: int, mode: int = 0x01, timeout: Optional[float] = None) -> Any:
        """Read one PID and decode every ECU's answer."""
        return self.j1979().read_pid(pid, mode, timeout=timeout)

    def read_dtcs(self, kind: str = "stored", timeout: Optional[float] = None) -> Any:
        """Read the diagnostic trouble codes of one kind."""
        return self.j1979().read_dtcs(kind, timeout=timeout)

    def read_vin(self, timeout: Optional[float] = None) -> Optional[str]:
        """Read the vehicle identification number, via mode 09 PID 02."""
        return self.j1979().read_vin(timeout=timeout)

    def identify(self, timeout: Optional[float] = None) -> Any:
        """Gather everything the vehicle will say about itself."""
        return self.j1979().identify(timeout=timeout)


def first_payload(replies: Iterable[DiagnosticResponse]) -> Optional[bytes]:
    """Return the payload of the first reply, or None when nothing answered."""
    for reply in replies:
        return reply.payload
    return None
