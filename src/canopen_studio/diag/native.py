"""
Diagnostic sessions over the CAN adapters the studio already supports.

This backend implements ISO 15765-2 on top of `canopen_studio.interfaces`, so every
adapter in the catalog — SLCAN, PCAN, Kvaser, Vector, IXXAT, gs_usb, SocketCAN, UDP
multicast, the virtual simulator — can drive an OBD-II session without an ELM327 in the
way. The trade-off against an ELM327 is that protocol autodetection is not done for us:
the bitrate and the addressing have to be right.

The frame source is injectable for a reason. When the studio is connected, its capture
loop owns the only reader on the bus, exactly as `canopen_studio.bridge` documents for
the mirroring direction. A second `recv()` from here would steal frames from the trace
and the plotter, so the GUI hands frames over through a `QueueFrameSource` instead.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from __future__ import annotations

import queue
import time
from typing import List, Optional, Protocol

import can

from .interface import DEFAULT_TIMEOUT, DiagnosticInterface, DiagnosticResponse, ProtocolError, ResponseCode
from .isotp import (
    FUNCTIONAL_REQUEST_11BIT,
    PHYSICAL_REQUEST_BASE_29BIT,
    RESPONSE_BASE_11BIT,
    ECU_COUNT_11BIT,
    IsoTpReassembler,
    decode_stmin,
    flow_control_frame,
    is_response_id,
    segment,
)

# How long to keep listening after the first complete answer, so that the other ECUs
# answering a functional request are not cut off. Kept short because a PID scan walks
# hundreds of requests and pays this on every one of them.
DEFAULT_SETTLE_TIME = 0.05

# Longest single wait on the frame source, so a request stays responsive to its deadline.
POLL_INTERVAL = 0.02

# How long to wait for the receiver to clear us to send the rest of a segmented request.
DEFAULT_FLOW_CONTROL_TIMEOUT = 0.5

# An ECU may ask for more time with this negative response code, as often as it likes.
# Each one buys another full timeout, up to a bound that keeps a scan from hanging.
MAX_RESPONSE_PENDING = 10


class FrameSource(Protocol):
    """Where a diagnostic session gets its frames from."""

    def recv(self, timeout: float) -> Optional[can.Message]:
        """Return the next frame, or None once `timeout` seconds have passed."""


class BusFrameSource:
    """Reads frames straight off the bus, for a session that owns the only reader."""

    def __init__(self, bus: can.BusABC):
        self._bus = bus

    def recv(self, timeout: float) -> Optional[can.Message]:
        return self._bus.recv(timeout=timeout)

    def clear(self) -> None:
        """Drop whatever is already buffered, so a request starts on a quiet bus."""
        while self._bus.recv(timeout=0) is not None:
            pass


class QueueFrameSource:
    """
    Receives frames pushed by an application capture loop.

    The loop that already owns the bus calls `feed()` for each frame it reads; the
    diagnostic session consumes them here. The queue is bounded and drops its oldest
    frame when full, so a session nobody is reading cannot grow without bound behind a
    running capture loop.
    """

    def __init__(self, maxsize: int = 512):
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)

    def feed(self, msg: can.Message) -> None:
        """Hand one frame to the session. Never blocks the capture loop."""
        while True:
            try:
                self._queue.put_nowait(msg)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:  # pragma: no cover - another consumer drained it
                    return

    def recv(self, timeout: float) -> Optional[can.Message]:
        try:
            return self._queue.get(timeout=timeout) if timeout > 0 else self._queue.get_nowait()
        except queue.Empty:
            return None

    def clear(self) -> None:
        """Drop frames left over from an earlier request."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return


def response_to_request_id(response_id: int, extended: bool = False) -> int:
    """
    Map the identifier an ECU answered on back to its physical request identifier.

    Flow control has to reach that one ECU. Sending it to the functional address instead
    would ask every ECU on the bus to resume, which is not what the standard describes
    and which some gateways reject outright.
    """
    if extended:
        # 0x18DAF1<ecu> answered us; the request back to it is 0x18DA<ecu>F1.
        ecu_address = response_id & 0xFF
        return PHYSICAL_REQUEST_BASE_29BIT | (ecu_address << 8)
    return response_id - ECU_COUNT_11BIT


class NativeCanDiagnosticInterface(DiagnosticInterface):
    """
    A diagnostic session carried by ISO-TP over a native CAN adapter.

    Args:
        bus: An open `python-can` bus. It is borrowed, not owned, unless `owns_bus` is
            set: the studio's own bus must survive the end of a diagnostic session.
        source: Where to read frames from. Defaults to reading the bus directly, which
            is right for a standalone session but wrong when a capture loop already
            owns the only reader — pass a `QueueFrameSource` then.
        tx_id: Request identifier. The functional address by default, so that every ECU
            answers; use `isotp.physical_request_id()` to address one of them.
        extended: True for 29-bit addressing.
        padding: Filler byte for the unused tail of a frame.
        settle_time: How long to keep listening after the first complete answer, to
            catch the other ECUs answering a functional request.
        flow_control_timeout: How long to wait for an ECU to clear us to send the rest
            of a segmented request before sending it anyway.
    """

    def __init__(
        self,
        bus: can.BusABC,
        source: Optional[FrameSource] = None,
        tx_id: int = FUNCTIONAL_REQUEST_11BIT,
        extended: bool = False,
        padding: int = 0x00,
        settle_time: float = DEFAULT_SETTLE_TIME,
        flow_control_timeout: float = DEFAULT_FLOW_CONTROL_TIMEOUT,
        owns_bus: bool = False,
    ):
        super().__init__()
        self.bus = bus
        self.source: FrameSource = source if source is not None else BusFrameSource(bus)
        self.tx_id = tx_id
        self.extended = extended
        self.padding = padding
        self.settle_time = settle_time
        self.flow_control_timeout = flow_control_timeout
        self.owns_bus = owns_bus
        self.default_timeout = DEFAULT_TIMEOUT
        self.last_errors: List[str] = []
        self._reassembler = IsoTpReassembler()

    @classmethod
    def open_bus(
        cls,
        interface: str,
        channel: str,
        bitrate: int = 500000,
        **kwargs,
    ) -> "NativeCanDiagnosticInterface":
        """Open a bus from the studio's adapter catalog and own it for this session."""
        from ..interfaces import open_can_bus

        bus = open_can_bus(interface, channel, bitrate)
        return cls(bus, owns_bus=True, **kwargs)

    @property
    def description(self) -> str:
        width = "29-bit" if self.extended else "11-bit"
        return f"Native CAN ISO-TP, {width} request 0x{self.tx_id:X}"

    def _open(self) -> None:
        self._reassembler.reset()

    def _close(self) -> None:
        self._reassembler.reset()
        if self.owns_bus:
            try:
                self.bus.shutdown()
            except Exception:
                # A bus that is already gone is not a reason to fail closing a session.
                pass

    # -- Exchange ----------------------------------------------------------

    def _request(self, payload: bytes, timeout: float) -> List[DiagnosticResponse]:
        self.last_errors = []
        self._reassembler.reset()
        clear = getattr(self.source, "clear", None)
        if callable(clear):
            clear()

        self._send_request(payload)
        return self._collect(timeout)

    def _send_request(self, payload: bytes) -> None:
        """
        Put the request on the bus.

        A J1979 request always fits one frame, but a gated UDS write may not, so the
        multi-frame case is handled: the first frame goes out, the addressed ECU answers
        with flow control, and the consecutive frames follow at the spacing it asked for.
        """
        frames = segment(payload, padding=self.padding)
        self._send_frame(self.tx_id, frames[0])
        if len(frames) == 1:
            return

        separation = self._await_flow_control(self.flow_control_timeout)
        for frame in frames[1:]:
            if separation:
                time.sleep(separation)
            self._send_frame(self.tx_id, frame)

    def _await_flow_control(self, timeout: float) -> float:
        """Wait for the receiver to clear us to send, and return the spacing it asked for."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.source.recv(min(POLL_INTERVAL, max(deadline - time.monotonic(), 0)))
            if msg is None:
                continue
            data = bytes(msg.data)
            if data and data[0] >> 4 == 0x3 and len(data) >= 3:
                return decode_stmin(data[2])
        # Nothing came back. Send anyway rather than abandon: some gateways stay silent
        # and accept the consecutive frames regardless.
        return 0.0

    def _send_frame(self, can_id: int, data: bytes) -> None:
        self.bus.send(can.Message(arbitration_id=can_id, is_extended_id=self.extended, data=data))

    def _collect(self, timeout: float) -> List[DiagnosticResponse]:
        """Gather replies until the deadline, or shortly after the last one arrived."""
        responses: List[DiagnosticResponse] = []
        deadline = time.monotonic() + timeout
        settle_deadline: Optional[float] = None
        pending_seen = 0

        while True:
            now = time.monotonic()
            if now >= deadline or (settle_deadline is not None and now >= settle_deadline):
                return responses

            remaining = min(deadline - now, POLL_INTERVAL)
            msg = self.source.recv(max(remaining, 0.0))
            if msg is None:
                continue

            if msg.is_extended_id != self.extended or not is_response_id(msg.arbitration_id, self.extended):
                continue

            completed = self._feed(msg)
            if completed is None:
                continue

            reply = DiagnosticResponse(source=msg.arbitration_id, data=completed)
            if self._is_response_pending(reply):
                # The ECU is still working. Give it another full window rather than
                # reporting its "wait" as the answer.
                pending_seen += 1
                if pending_seen <= MAX_RESPONSE_PENDING:
                    deadline = time.monotonic() + timeout
                    settle_deadline = None
                continue

            responses.append(reply)
            settle_deadline = time.monotonic() + self.settle_time

    def _feed(self, msg: can.Message) -> Optional[bytes]:
        """Feed one frame to the reassembler, answering flow control where it is due."""
        try:
            result = self._reassembler.feed(msg.arbitration_id, bytes(msg.data))
        except ProtocolError as exc:
            # One ECU emitting garbage must not cost the answers of the others, so the
            # failure is recorded for the caller and the scan carries on. Name the
            # source: with several ECUs answering, an unattributed error is useless.
            self.last_errors.append(f"0x{msg.arbitration_id:03X}: {exc}")
            return None

        if result.flow_control_required:
            self._send_frame(
                response_to_request_id(msg.arbitration_id, self.extended),
                flow_control_frame(padding=self.padding),
            )
        return result.completed

    @staticmethod
    def _is_response_pending(reply: DiagnosticResponse) -> bool:
        return reply.is_negative and reply.response_code == ResponseCode.RESPONSE_PENDING


def standard_response_ids() -> range:
    """The identifiers an 11-bit OBD-II session listens on."""
    return range(RESPONSE_BASE_11BIT, RESPONSE_BASE_11BIT + ECU_COUNT_11BIT)
