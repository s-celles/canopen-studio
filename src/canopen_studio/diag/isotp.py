"""
ISO 15765-2 (ISO-TP) segmentation and reassembly.

Diagnostic messages routinely exceed the eight bytes a CAN frame carries — a VIN alone is
twenty — so they are split into a first frame and a train of consecutive frames, with a
flow control frame from the receiver in between.

This module is deliberately free of `python-can` types: it works on frame payloads and
source identifiers only. That lets the same reassembler serve both backends. The native
transport feeds it `msg.data` off the bus; the ELM327 backend feeds it the frames it
parses out of the adapter's ASCII output, because with headers enabled an ELM327 prints
the very same ISO-TP frames instead of reassembling them itself.

Only classic CAN framing is implemented: an 8-byte frame and a 12-bit first-frame length.
The CAN FD escape sequence (FF_DL of 0 followed by a 32-bit length) is not used by J1979
and is rejected as malformed rather than silently mis-parsed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .interface import ProtocolError

# Protocol control information, held in the high nibble of the first payload byte.
PCI_SINGLE_FRAME = 0x0
PCI_FIRST_FRAME = 0x1
PCI_CONSECUTIVE_FRAME = 0x2
PCI_FLOW_CONTROL = 0x3

# Flow status carried in the low nibble of a flow control frame.
FLOW_STATUS_CONTINUE = 0
FLOW_STATUS_WAIT = 1
FLOW_STATUS_OVERFLOW = 2

# A classic CAN frame carries eight bytes, one of which is the single-frame PCI.
FRAME_SIZE = 8
MAX_SINGLE_FRAME_PAYLOAD = 7
FIRST_FRAME_PAYLOAD = 6
CONSECUTIVE_FRAME_PAYLOAD = 7

# A classic first frame declares its length in twelve bits.
MAX_CLASSIC_PAYLOAD = 0xFFF

# Standard OBD-II addressing of ISO 15765-4.
FUNCTIONAL_REQUEST_11BIT = 0x7DF
PHYSICAL_REQUEST_BASE_11BIT = 0x7E0
RESPONSE_BASE_11BIT = 0x7E8
ECU_COUNT_11BIT = 8

# 29-bit addressing embeds both ends in the identifier: 0x18DA<target><source>, with the
# external test equipment fixed at 0xF1. A request is therefore 0x18DA<ecu>F1 and its
# answer 0x18DAF1<ecu> — the two address bytes swap, they do not shift.
FUNCTIONAL_REQUEST_29BIT = 0x18DB33F1
PHYSICAL_REQUEST_BASE_29BIT = 0x18DA00F1
RESPONSE_PREFIX_29BIT = 0x18DAF1
TESTER_ADDRESS_29BIT = 0xF1

# Separation times of 0x80..0xF0 and 0xFA..0xFF are reserved. ISO 15765-2 says a receiver
# meeting one must fall back to the longest defined value rather than guess.
STMIN_RESERVED_FALLBACK = 0.127
MAX_STMIN_MILLISECONDS = 0x7F


def physical_request_id(ecu_address: int, extended: bool = False) -> int:
    """
    Build the request identifier addressing one ECU directly.

    Args:
        ecu_address: With 11-bit addressing, which of the eight legislated ECU slots to
            target, 0 through 7. With 29-bit addressing, the ECU's own address byte.
        extended: True for 29-bit addressing, False for the 11-bit default.
    """
    if extended:
        if not 0 <= ecu_address <= 0xFF:
            raise ValueError(f"ECU address 0x{ecu_address:X} does not fit in a byte")
        return PHYSICAL_REQUEST_BASE_29BIT | (ecu_address << 8)
    if not 0 <= ecu_address < ECU_COUNT_11BIT:
        raise ValueError(f"ECU index {ecu_address} is outside the standard range 0..7")
    return PHYSICAL_REQUEST_BASE_11BIT + ecu_address


def is_response_id(can_id: int, extended: bool = False) -> bool:
    """Whether an identifier is one an ECU answers a diagnostic request on."""
    if extended:
        return (can_id >> 8) == RESPONSE_PREFIX_29BIT
    return RESPONSE_BASE_11BIT <= can_id < RESPONSE_BASE_11BIT + ECU_COUNT_11BIT


def encode_stmin(seconds: float) -> int:
    """
    Encode a minimum separation time into its flow control byte.

    Sub-millisecond values are rounded up to one millisecond rather than pushed into the
    0xF1..0xF9 range: asking an ECU for microsecond spacing gains nothing here and is
    refused outright by some of them.
    """
    if seconds <= 0:
        return 0
    milliseconds = int(round(seconds * 1000))
    if milliseconds < 1:
        return 1
    return min(milliseconds, MAX_STMIN_MILLISECONDS)


def decode_stmin(value: int) -> float:
    """Decode a separation time byte into seconds, per ISO 15765-2."""
    if 0x00 <= value <= 0x7F:
        return value / 1000.0
    if 0xF1 <= value <= 0xF9:
        return (value - 0xF0) / 10000.0
    return STMIN_RESERVED_FALLBACK


def flow_control_frame(
    status: int = FLOW_STATUS_CONTINUE,
    block_size: int = 0,
    stmin: int = 0,
    padding: int = 0x00,
) -> bytes:
    """
    Build a flow control frame.

    The defaults clear the sender to transmit the whole message without further
    handshaking: a block size of 0 means "no more flow control" and an STmin of 0 means
    "as fast as you can". That is what every OBD-II tool asks for, and what keeps a PID
    scan quick.

    Args:
        status: FLOW_STATUS_CONTINUE, _WAIT or _OVERFLOW.
        block_size: Frames the sender may send before waiting again; 0 for all of them.
        stmin: Encoded minimum separation time between consecutive frames.
        padding: Filler byte for the unused tail of the frame.
    """
    head = bytes([(PCI_FLOW_CONTROL << 4) | (status & 0x0F), block_size & 0xFF, stmin & 0xFF])
    return head + bytes([padding & 0xFF] * (FRAME_SIZE - len(head)))


def segment(payload: bytes, padding: int = 0x00) -> List[bytes]:
    """
    Split a request payload into the ISO-TP frames that carry it.

    Args:
        payload: The diagnostic request, starting with the mode byte.
        padding: Filler byte for the unused tail of the last frame.

    Returns:
        The frame payloads, each exactly eight bytes, in transmission order. Flow control
        is the caller's business: this function does not wait for one between frames.
    """
    if not payload:
        raise ValueError("an ISO-TP message carries at least one byte")
    if len(payload) > MAX_CLASSIC_PAYLOAD:
        raise ValueError(f"payload of {len(payload)} bytes exceeds the classic ISO-TP limit of {MAX_CLASSIC_PAYLOAD}")

    filler = padding & 0xFF

    if len(payload) <= MAX_SINGLE_FRAME_PAYLOAD:
        frame = bytes([(PCI_SINGLE_FRAME << 4) | len(payload)]) + payload
        return [frame.ljust(FRAME_SIZE, bytes([filler]))]

    length = len(payload)
    head = bytes([(PCI_FIRST_FRAME << 4) | ((length >> 8) & 0x0F), length & 0xFF])
    frames = [head + payload[:FIRST_FRAME_PAYLOAD]]

    index = FIRST_FRAME_PAYLOAD
    sequence = 1
    while index < length:
        chunk = payload[index : index + CONSECUTIVE_FRAME_PAYLOAD]
        frame = bytes([(PCI_CONSECUTIVE_FRAME << 4) | (sequence & 0x0F)]) + chunk
        frames.append(frame.ljust(FRAME_SIZE, bytes([filler])))
        index += CONSECUTIVE_FRAME_PAYLOAD
        sequence = (sequence + 1) & 0x0F

    return frames


@dataclass(frozen=True)
class FeedResult:
    """What feeding one frame to the reassembler produced."""

    completed: Optional[bytes] = None
    flow_control_required: bool = False

    def __bool__(self) -> bool:
        return self.completed is not None


@dataclass
class _Transfer:
    """A multi-frame message being reassembled from one source."""

    expected_length: int
    buffer: bytearray = field(default_factory=bytearray)
    next_sequence: int = 1


_EMPTY = FeedResult()


class IsoTpReassembler:
    """
    Reassembles ISO-TP messages fed one frame at a time, per source identifier.
    Backed by native Rust `canopen_core` for high-performance multi-ECU parsing.
    """

    def __init__(self) -> None:
        try:
            from .. import canopen_core
            self._native = canopen_core.IsoTpReassembler()
        except ImportError:
            raise ImportError("canopen_core is required for ISO-TP reassembly")

    def reset(self) -> None:
        self._native.reset()
        
    def pending_sources(self) -> list[int]:
        return self._native.pending_sources()

    def feed(self, source: int, data: bytes):
        try:
            return self._native.feed(source, data)
        except ValueError as e:
            if "ProtocolError" in str(e):
                raise ProtocolError(str(e))
            raise

