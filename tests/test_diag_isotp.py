"""
Unit tests for the ISO 15765-2 (ISO-TP) segmentation and reassembly layer.

The same reassembler serves both backends: the native CAN transport feeds it frame
payloads off the bus, and the ELM327 backend feeds it the frames it parses out of the
adapter's ASCII output once headers are enabled. Malformed and truncated input is
therefore tested as carefully as the happy path.
"""

import pytest

from canopen_studio.diag import ProtocolError
from canopen_studio.diag.isotp import (
    FLOW_STATUS_CONTINUE,
    FLOW_STATUS_OVERFLOW,
    FLOW_STATUS_WAIT,
    FUNCTIONAL_REQUEST_11BIT,
    FUNCTIONAL_REQUEST_29BIT,
    IsoTpReassembler,
    decode_stmin,
    encode_stmin,
    flow_control_frame,
    is_response_id,
    physical_request_id,
    segment,
)


class TestSegmentation:
    def test_short_payload_becomes_one_single_frame(self):
        """A mode 01 PID request fits in a single frame: PCI 0x02 then the two bytes."""
        frames = segment(bytes([0x01, 0x0C]))

        assert frames == [bytes([0x02, 0x01, 0x0C, 0, 0, 0, 0, 0])]

    def test_single_frames_are_padded_to_eight_bytes(self):
        assert all(len(f) == 8 for f in segment(b"\x01"))

    def test_padding_byte_is_configurable(self):
        """Some ECUs are picky about the filler; 0xAA and 0xCC are both seen in the wild."""
        frames = segment(bytes([0x01, 0x0C]), padding=0xAA)

        assert frames[0] == bytes([0x02, 0x01, 0x0C, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA])

    def test_seven_bytes_still_fit_a_single_frame(self):
        frames = segment(bytes(range(1, 8)))

        assert len(frames) == 1
        assert frames[0][0] == 0x07

    def test_eight_bytes_need_a_first_frame(self):
        frames = segment(bytes(range(1, 9)))

        assert len(frames) == 2
        assert frames[0][:2] == bytes([0x10, 0x08])

    def test_first_frame_carries_six_payload_bytes(self):
        frames = segment(bytes(range(1, 21)))

        assert frames[0] == bytes([0x10, 0x14, 1, 2, 3, 4, 5, 6])

    def test_consecutive_frames_are_numbered_from_one(self):
        frames = segment(bytes(range(1, 21)))

        assert frames[1][0] == 0x21
        assert frames[2][0] == 0x22

    def test_consecutive_frame_sequence_wraps_after_fifteen(self):
        """The sequence number is a nibble: it wraps 0x2F -> 0x20, not to 0x30."""
        frames = segment(bytes(200))

        sequences = [f[0] for f in frames[1:]]
        assert sequences[:17] == [0x21 + i for i in range(15)] + [0x20, 0x21]

    def test_last_consecutive_frame_is_padded(self):
        frames = segment(bytes(range(1, 10)), padding=0xAA)

        assert frames[-1] == bytes([0x21, 7, 8, 9, 0xAA, 0xAA, 0xAA, 0xAA])

    def test_empty_payload_is_refused(self):
        with pytest.raises(ValueError):
            segment(b"")

    def test_payload_beyond_the_classic_limit_is_refused(self):
        """A classic first frame declares its length in 12 bits."""
        with pytest.raises(ValueError):
            segment(bytes(4096))

    def test_largest_classic_payload_is_accepted(self):
        assert segment(bytes(4095))[0][:2] == bytes([0x1F, 0xFF])


class TestFlowControl:
    def test_default_flow_control_clears_the_sender_to_send_everything(self):
        """Block size 0 and STmin 0 ask for the whole message without further handshakes."""
        assert flow_control_frame() == bytes([0x30, 0x00, 0x00, 0, 0, 0, 0, 0])

    def test_flow_control_block_size_and_separation_time_are_configurable(self):
        assert flow_control_frame(block_size=8, stmin=0x14)[:3] == bytes([0x30, 0x08, 0x14])

    def test_flow_control_status_is_configurable(self):
        assert flow_control_frame(status=FLOW_STATUS_WAIT)[0] == 0x31
        assert flow_control_frame(status=FLOW_STATUS_OVERFLOW)[0] == 0x32

    def test_flow_control_padding_is_configurable(self):
        assert flow_control_frame(padding=0xAA)[3:] == bytes([0xAA] * 5)


class TestSeparationTimeEncoding:
    def test_milliseconds_encode_directly(self):
        assert encode_stmin(0.020) == 20
        assert decode_stmin(20) == pytest.approx(0.020)

    def test_zero_means_as_fast_as_possible(self):
        assert encode_stmin(0) == 0
        assert decode_stmin(0) == 0

    def test_sub_millisecond_values_use_the_microsecond_range(self):
        """0xF1..0xF9 encode 100..900 microseconds."""
        assert decode_stmin(0xF1) == pytest.approx(0.0001)
        assert decode_stmin(0xF9) == pytest.approx(0.0009)

    def test_reserved_encodings_fall_back_to_the_safe_maximum(self):
        """ISO 15765-2 says to treat a reserved value as 127 ms rather than guess."""
        assert decode_stmin(0x80) == pytest.approx(0.127)
        assert decode_stmin(0xFA) == pytest.approx(0.127)

    def test_long_separation_times_saturate_at_the_encodable_maximum(self):
        assert encode_stmin(10.0) == 0x7F


class TestAddressing:
    def test_functional_request_identifiers_are_the_standard_ones(self):
        assert FUNCTIONAL_REQUEST_11BIT == 0x7DF
        assert FUNCTIONAL_REQUEST_29BIT == 0x18DB33F1

    def test_physical_request_targets_an_ecu_by_index(self):
        assert physical_request_id(0) == 0x7E0
        assert physical_request_id(7) == 0x7E7

    def test_physical_request_index_is_bounded(self):
        with pytest.raises(ValueError):
            physical_request_id(8)

    def test_standard_response_identifiers_are_recognised(self):
        assert is_response_id(0x7E8) is True
        assert is_response_id(0x7EF) is True
        assert is_response_id(0x7E0) is False

    def test_extended_response_identifiers_are_recognised(self):
        assert is_response_id(0x18DAF110, extended=True) is True
        assert is_response_id(0x18DB33F1, extended=True) is False


class TestReassemblySingleFrame:
    def test_single_frame_completes_immediately(self):
        result = IsoTpReassembler().feed(0x7E8, bytes([0x04, 0x41, 0x0C, 0x1A, 0xF8, 0, 0, 0]))

        assert result.completed == bytes([0x41, 0x0C, 0x1A, 0xF8])

    def test_single_frame_needs_no_flow_control(self):
        result = IsoTpReassembler().feed(0x7E8, bytes([0x04, 0x41, 0x0C, 0x1A, 0xF8, 0, 0, 0]))

        assert result.flow_control_required is False

    def test_padding_beyond_the_declared_length_is_discarded(self):
        result = IsoTpReassembler().feed(0x7E8, bytes([0x02, 0x41, 0x0C, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA]))

        assert result.completed == bytes([0x41, 0x0C])

    def test_a_frame_shorter_than_its_declared_length_is_malformed(self):
        """A truncated single frame is the classic symptom of a lost byte on the link."""
        with pytest.raises(ProtocolError):
            IsoTpReassembler().feed(0x7E8, bytes([0x07, 0x41, 0x0C]))

    def test_an_all_zero_frame_is_ignored_as_padding(self):
        """Idle padding frames must not be mistaken for a zero-length message."""
        result = IsoTpReassembler().feed(0x7E8, bytes(8))

        assert result.completed is None

    def test_an_empty_frame_is_ignored(self):
        assert IsoTpReassembler().feed(0x7E8, b"").completed is None


class TestReassemblyMultiFrame:
    VIN_FRAMES = [
        bytes([0x10, 0x14, 0x49, 0x02, 0x01, 0x31, 0x44, 0x34]),
        bytes([0x21, 0x47, 0x50, 0x30, 0x30, 0x52, 0x35, 0x35]),
        bytes([0x22, 0x42, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36]),
    ]

    def test_first_frame_does_not_complete_a_message(self):
        result = IsoTpReassembler().feed(0x7E8, self.VIN_FRAMES[0])

        assert result.completed is None

    def test_first_frame_asks_the_caller_to_send_flow_control(self):
        """The native transport must answer a first frame, or the ECU stops sending."""
        result = IsoTpReassembler().feed(0x7E8, self.VIN_FRAMES[0])

        assert result.flow_control_required is True

    def test_consecutive_frames_complete_the_declared_length(self):
        reassembler = IsoTpReassembler()

        results = [reassembler.feed(0x7E8, f) for f in self.VIN_FRAMES]

        assert results[-1].completed == bytes(
            [
                0x49,
                0x02,
                0x01,
                0x31,
                0x44,
                0x34,
                0x47,
                0x50,
                0x30,
                0x30,
                0x52,
                0x35,
                0x35,
                0x42,
                0x31,
                0x32,
                0x33,
                0x34,
                0x35,
                0x36,
            ]
        )

    def test_trailing_padding_of_the_last_frame_is_dropped(self):
        reassembler = IsoTpReassembler()
        frames = [
            bytes([0x10, 0x09, 1, 2, 3, 4, 5, 6]),
            bytes([0x21, 7, 8, 9, 0xAA, 0xAA, 0xAA, 0xAA]),
        ]

        for frame in frames:
            result = reassembler.feed(0x7E8, frame)

        assert result.completed == bytes([1, 2, 3, 4, 5, 6, 7, 8, 9])

    def test_sequence_numbers_wrap_across_long_messages(self):
        payload = bytes((i % 251) + 1 for i in range(150))
        reassembler = IsoTpReassembler()

        for frame in segment(payload):
            result = reassembler.feed(0x7E8, frame)

        assert result.completed == payload

    def test_two_ecus_are_reassembled_independently(self):
        """A functional request is answered by several ECUs whose frames interleave."""
        reassembler = IsoTpReassembler()
        first = segment(bytes([0x49, 0x02, 0x01]) + b"ECU-ONE-VIN-000000"[:17])
        second = segment(bytes([0x49, 0x02, 0x01]) + b"ECU-TWO-VIN-000000"[:17])

        completed = {}
        for frame_a, frame_b in zip(first, second):
            result_a = reassembler.feed(0x7E8, frame_a)
            result_b = reassembler.feed(0x7E9, frame_b)
            if result_a.completed:
                completed[0x7E8] = result_a.completed
            if result_b.completed:
                completed[0x7E9] = result_b.completed

        assert completed[0x7E8][3:] == b"ECU-ONE-VIN-00000"
        assert completed[0x7E9][3:] == b"ECU-TWO-VIN-00000"


class TestReassemblyErrors:
    def test_out_of_order_consecutive_frame_is_reported(self):
        """A dropped frame must surface, not silently corrupt the decoded value."""
        reassembler = IsoTpReassembler()
        reassembler.feed(0x7E8, bytes([0x10, 0x14, 0x49, 0x02, 0x01, 0x31, 0x44, 0x34]))

        with pytest.raises(ProtocolError):
            reassembler.feed(0x7E8, bytes([0x23, 1, 2, 3, 4, 5, 6, 7]))

    def test_a_broken_transfer_is_forgotten(self):
        """After an error the source starts clean, so the next request is not poisoned."""
        reassembler = IsoTpReassembler()
        reassembler.feed(0x7E8, bytes([0x10, 0x14, 0x49, 0x02, 0x01, 0x31, 0x44, 0x34]))
        with pytest.raises(ProtocolError):
            reassembler.feed(0x7E8, bytes([0x23, 1, 2, 3, 4, 5, 6, 7]))

        result = reassembler.feed(0x7E8, bytes([0x04, 0x41, 0x0C, 0x1A, 0xF8, 0, 0, 0]))

        assert result.completed == bytes([0x41, 0x0C, 0x1A, 0xF8])

    def test_consecutive_frame_without_a_first_frame_is_ignored(self):
        """A leftover frame from an aborted transfer must not start a phantom message."""
        result = IsoTpReassembler().feed(0x7E8, bytes([0x21, 1, 2, 3, 4, 5, 6, 7]))

        assert result.completed is None

    def test_first_frame_shorter_than_eight_bytes_is_malformed(self):
        """Anything that short belongs in a single frame; the length field is corrupt."""
        with pytest.raises(ProtocolError):
            IsoTpReassembler().feed(0x7E8, bytes([0x10, 0x04, 1, 2, 3, 4, 5, 6]))

    def test_truncated_first_frame_is_malformed(self):
        with pytest.raises(ProtocolError):
            IsoTpReassembler().feed(0x7E8, bytes([0x10, 0x14, 0x49]))

    def test_flow_control_frames_are_ignored_by_the_receiver(self):
        """A flow control frame is addressed to the sender, not to us."""
        result = IsoTpReassembler().feed(0x7E8, bytes([0x30, 0x00, 0x00, 0, 0, 0, 0, 0]))

        assert result.completed is None
        assert result.flow_control_required is False

    def test_unknown_protocol_control_information_is_ignored(self):
        """PCI type 4..15 is not defined for classic CAN; ignore rather than crash."""
        assert IsoTpReassembler().feed(0x7E8, bytes([0x40, 1, 2, 3, 4, 5, 6, 7])).completed is None

    def test_reset_forgets_every_pending_transfer(self):
        reassembler = IsoTpReassembler()
        reassembler.feed(0x7E8, bytes([0x10, 0x14, 0x49, 0x02, 0x01, 0x31, 0x44, 0x34]))

        reassembler.reset()

        assert reassembler.feed(0x7E8, bytes([0x21, 1, 2, 3, 4, 5, 6, 7])).completed is None

    def test_pending_reports_transfers_still_in_flight(self):
        reassembler = IsoTpReassembler()

        assert reassembler.pending_sources() == []
        reassembler.feed(0x7E8, bytes([0x10, 0x14, 0x49, 0x02, 0x01, 0x31, 0x44, 0x34]))
        assert reassembler.pending_sources() == [0x7E8]


class TestFlowStatusConstants:
    def test_flow_status_values_follow_the_standard(self):
        assert (FLOW_STATUS_CONTINUE, FLOW_STATUS_WAIT, FLOW_STATUS_OVERFLOW) == (0, 1, 2)
