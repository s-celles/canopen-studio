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

FUNCTIONAL_REQUEST_29BIT = 0x18DB33F1
PHYSICAL_REQUEST_BASE_29BIT = 0x18DA00F1
RESPONSE_PREFIX_29BIT = 0x18DAF1

# Separation times of 0x80..0xF0 and 0xFA..0xFF are reserved. ISO 15765-2 says a receiver
# meeting one must fall back to the longest defined value rather than guess.
STMIN_RESERVED_FALLBACK = 0.127
MAX_STMIN_MILLISECONDS = 0x7F


def physical_request_id(ecu_index: int, extended: bool = False) -> int:
    """
    Build the request identifier addressing one ECU directly.

    Args:
        ecu_index: Which of the eight ECU addresses to target, 0 through 7.
        extended: True for 29-bit addressing, False for the 11-bit default.
    """
    if not 0 <= ecu_index < ECU_COUNT_11BIT:
        raise ValueError(f"ECU index {ecu_index} is outside the standard range 0..7")
    if extended:
        return PHYSICAL_REQUEST_BASE_29BIT | ((RESPONSE_BASE_11BIT + ecu_index) & 0xFF) << 8
    return PHYSICAL_REQUEST_BASE_11BIT + ecu_index


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

    Several ECUs answer a functional request at once and their frames interleave on the
    bus, so state is kept per source rather than globally.

    Malformed input raises `ProtocolError` instead of being papered over: a dropped
    consecutive frame would otherwise shift every byte of a VIN or a DTC list and produce
    a plausible-looking wrong answer. The offending transfer is discarded first, so the
    next request starts clean.
    """

    def __init__(self) -> None:
        self._transfers: Dict[int, _Transfer] = {}

    def reset(self) -> None:
        """Forget every transfer in flight, before starting an unrelated request."""
        self._transfers.clear()

    def pending_sources(self) -> List[int]:
        """Sources with a transfer still awaiting consecutive frames."""
        return sorted(self._transfers)

    def feed(self, source: int, data: bytes) -> FeedResult:
        """
        Feed one frame payload and report what it produced.

        Args:
            source: The identifier the frame arrived on, used to separate ECUs.
            data: The frame payload, up to eight bytes, padding included.

        Returns:
            A `FeedResult` whose `completed` holds a finished message, if the frame
            finished one, and whose `flow_control_required` is set when the sender is
            waiting for a flow control frame.
        """
        if not data:
            return _EMPTY

        pci_type = data[0] >> 4
        if pci_type == PCI_SINGLE_FRAME:
            return self._feed_single_frame(source, data)
        if pci_type == PCI_FIRST_FRAME:
            return self._feed_first_frame(source, data)
        if pci_type == PCI_CONSECUTIVE_FRAME:
            return self._feed_consecutive_frame(source, data)
        # Flow control is addressed to the sender, and types 4..15 are undefined for
        # classic CAN. Neither is ours to act on.
        return _EMPTY

    def _feed_single_frame(self, source: int, data: bytes) -> FeedResult:
        length = data[0] & 0x0F
        if length == 0:
            # An all-zero frame is idle padding, which several adapters emit between
            # requests. Treating it as a zero-length message would invent a response.
            return _EMPTY
        if length > MAX_SINGLE_FRAME_PAYLOAD:
            raise ProtocolError(f"single frame from 0x{source:X} declares {length} bytes, which cannot fit")
        if len(data) < 1 + length:
            raise ProtocolError(f"single frame from 0x{source:X} declares {length} bytes but carries {len(data) - 1}")
        self._transfers.pop(source, None)
        return FeedResult(completed=bytes(data[1 : 1 + length]))

    def _feed_first_frame(self, source: int, data: bytes) -> FeedResult:
        if len(data) < 2 + FIRST_FRAME_PAYLOAD:
            raise ProtocolError(f"first frame from 0x{source:X} is truncated to {len(data)} bytes")
        length = ((data[0] & 0x0F) << 8) | data[1]
        if length <= MAX_SINGLE_FRAME_PAYLOAD:
            raise ProtocolError(
                f"first frame from 0x{source:X} declares {length} bytes, which belongs in a single frame"
            )
        self._transfers[source] = _Transfer(
            expected_length=length,
            buffer=bytearray(data[2 : 2 + FIRST_FRAME_PAYLOAD]),
        )
        return FeedResult(flow_control_required=True)

    def _feed_consecutive_frame(self, source: int, data: bytes) -> FeedResult:
        transfer = self._transfers.get(source)
        if transfer is None:
            # A leftover frame from a transfer that was aborted or predates this session.
            return _EMPTY

        sequence = data[0] & 0x0F
        if sequence != transfer.next_sequence:
            del self._transfers[source]
            raise ProtocolError(
                f"consecutive frame from 0x{source:X} is out of order: "
                f"expected sequence {transfer.next_sequence}, got {sequence}"
            )

        transfer.buffer.extend(data[1:])
        transfer.next_sequence = (transfer.next_sequence + 1) & 0x0F

        if len(transfer.buffer) < transfer.expected_length:
            return _EMPTY

        del self._transfers[source]
        return FeedResult(completed=bytes(transfer.buffer[: transfer.expected_length]))
