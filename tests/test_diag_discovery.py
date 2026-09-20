"""
Unit tests for supported-PID discovery.

A vehicle declares its own capabilities through bitmask PIDs, and the walk must end where
the vehicle says it ends rather than where a hard-coded list does.
"""

import pytest

from canopen_studio.diag import DiagnosticInterface, DiagnosticResponse
from canopen_studio.diag.j1979.discovery import (
    BLOCK_SIZE,
    SUPPORT_PIDS,
    SupportedPids,
    decode_support_bitmask,
    discover_supported_pids,
)


class ScriptedInterface(DiagnosticInterface):
    """Replays canned answers, keyed by the request bytes."""

    def __init__(self, script):
        super().__init__()
        self.script = {bytes(k): v for k, v in script.items()}
        self.requests = []
        self.open()

    @property
    def description(self):
        return "scripted"

    def _open(self):
        pass

    def _close(self):
        pass

    def _request(self, payload, timeout):
        self.requests.append(payload)
        return [DiagnosticResponse(source=source, data=data) for source, data in self.script.get(payload, [])]


def bitmask_reply(base, value, source=0x7E8):
    """A mode 01 answer carrying one support bitmask."""
    return (source, bytes([0x41, base]) + value.to_bytes(4, "big"))


class TestBitmaskDecoding:
    def test_the_top_bit_is_the_pid_just_after_the_base(self):
        assert decode_support_bitmask(0x00, (0x80000000).to_bytes(4, "big")) == [0x01]

    def test_the_bottom_bit_is_the_next_bitmask_pid(self):
        assert decode_support_bitmask(0x00, (0x00000001).to_bytes(4, "big")) == [0x20]

    def test_a_full_bitmask_declares_thirty_two_pids(self):
        assert len(decode_support_bitmask(0x00, b"\xff\xff\xff\xff")) == BLOCK_SIZE

    def test_an_empty_bitmask_declares_nothing(self):
        assert decode_support_bitmask(0x00, b"\x00\x00\x00\x00") == []

    def test_a_later_block_is_offset_by_its_base(self):
        assert decode_support_bitmask(0x20, (0x80000000).to_bytes(4, "big")) == [0x21]

    def test_a_real_bitmask_decodes_to_the_expected_pids(self):
        """BE 3E B8 11 is the answer a great many cars give to PID 0x00."""
        pids = decode_support_bitmask(0x00, bytes([0xBE, 0x3E, 0xB8, 0x11]))

        assert 0x0C in pids  # engine speed
        assert 0x0D in pids  # vehicle speed
        assert 0x20 in pids  # there is another block
        assert 0x02 not in pids

    def test_a_short_bitmask_is_refused(self):
        with pytest.raises(ValueError):
            decode_support_bitmask(0x00, b"\xbe\x3e")


class TestDiscovery:
    def test_a_single_block_is_walked(self):
        interface = ScriptedInterface({b"\x01\x00": [bitmask_reply(0x00, 0x80000000)]})

        supported = discover_supported_pids(interface)

        assert set(supported) == {0x00, 0x01}

    def test_the_bitmask_pid_itself_counts_as_supported(self):
        interface = ScriptedInterface({b"\x01\x00": [bitmask_reply(0x00, 0x80000000)]})

        assert 0x00 in discover_supported_pids(interface)

    def test_the_walk_continues_while_the_vehicle_says_there_is_more(self):
        interface = ScriptedInterface(
            {
                b"\x01\x00": [bitmask_reply(0x00, 0x80000001)],
                b"\x01\x20": [bitmask_reply(0x20, 0x40000000)],
            }
        )

        supported = discover_supported_pids(interface)

        assert {0x01, 0x20, 0x22} <= set(supported)

    def test_the_walk_stops_when_the_continuation_bit_is_clear(self):
        interface = ScriptedInterface({b"\x01\x00": [bitmask_reply(0x00, 0x80000000)]})

        discover_supported_pids(interface)

        assert interface.requests == [b"\x01\x00"]

    def test_the_walk_stops_when_nothing_answers(self):
        """An ignition that is off, or a mode the vehicle does not implement."""
        interface = ScriptedInterface({})

        supported = discover_supported_pids(interface)

        assert len(supported) == 0

    def test_the_walk_is_bounded_even_if_the_continuation_bit_is_stuck(self):
        """A vehicle insisting there is always more must not scan forever."""
        interface = ScriptedInterface({bytes([0x01, base]): [bitmask_reply(base, 0xFFFFFFFF)] for base in SUPPORT_PIDS})

        discover_supported_pids(interface)

        assert len(interface.requests) == len(SUPPORT_PIDS)

    def test_the_block_count_can_be_limited(self):
        interface = ScriptedInterface({bytes([0x01, base]): [bitmask_reply(base, 0xFFFFFFFF)] for base in SUPPORT_PIDS})

        discover_supported_pids(interface, max_blocks=2)

        assert len(interface.requests) == 2

    def test_mode_nine_is_enumerated_the_same_way(self):
        interface = ScriptedInterface({b"\x09\x00": [(0x7E8, bytes([0x49, 0x00, 0x40, 0x00, 0x00, 0x00]))]})

        supported = discover_supported_pids(interface, mode=0x09)

        assert supported.mode == 0x09
        assert 0x02 in supported

    def test_a_reply_echoing_a_different_pid_is_ignored(self):
        """A late answer to an earlier request must not be folded into the bitmask."""
        interface = ScriptedInterface({b"\x01\x00": [(0x7E8, bytes([0x41, 0x20, 0xFF, 0xFF, 0xFF, 0xFF]))]})

        assert len(discover_supported_pids(interface)) == 0

    def test_a_truncated_bitmask_is_ignored(self):
        interface = ScriptedInterface({b"\x01\x00": [(0x7E8, bytes([0x41, 0x00, 0xBE]))]})

        assert len(discover_supported_pids(interface)) == 0


class TestPerEcuSupport:
    def test_each_ecu_keeps_its_own_capabilities(self):
        interface = ScriptedInterface(
            {b"\x01\x00": [bitmask_reply(0x00, 0x80000000, 0x7E8), bitmask_reply(0x00, 0x40000000, 0x7E9)]}
        )

        supported = discover_supported_pids(interface)

        assert supported.by_ecu[0x7E8] == frozenset({0x00, 0x01})
        assert supported.by_ecu[0x7E9] == frozenset({0x00, 0x02})

    def test_the_union_is_what_the_vehicle_supports(self):
        interface = ScriptedInterface(
            {b"\x01\x00": [bitmask_reply(0x00, 0x80000000, 0x7E8), bitmask_reply(0x00, 0x40000000, 0x7E9)]}
        )

        assert set(discover_supported_pids(interface)) == {0x00, 0x01, 0x02}

    def test_the_ecu_implementing_a_pid_can_be_found(self):
        """Knowing which one answers is what lets a later read be addressed to it."""
        interface = ScriptedInterface(
            {b"\x01\x00": [bitmask_reply(0x00, 0x80000000, 0x7E8), bitmask_reply(0x00, 0x40000000, 0x7E9)]}
        )

        assert discover_supported_pids(interface).supported_by(0x02) == (0x7E9,)

    def test_the_answering_ecus_are_listed_in_order(self):
        interface = ScriptedInterface(
            {b"\x01\x00": [bitmask_reply(0x00, 0x80000000, 0x7E9), bitmask_reply(0x00, 0x40000000, 0x7E8)]}
        )

        assert discover_supported_pids(interface).ecus == (0x7E8, 0x7E9)


class TestSupportedPidsValue:
    def test_membership_is_tested_against_the_union(self):
        supported = SupportedPids(mode=0x01, by_ecu={0x7E8: frozenset({0x0C})})

        assert 0x0C in supported
        assert 0x0D not in supported

    def test_iteration_is_in_pid_order(self):
        supported = SupportedPids(mode=0x01, by_ecu={0x7E8: frozenset({0x0D, 0x0C})})

        assert list(supported) == [0x0C, 0x0D]

    def test_it_serialises_for_an_agent(self):
        supported = SupportedPids(mode=0x01, by_ecu={0x7E8: frozenset({0x0C})})

        payload = supported.as_dict()

        assert payload["mode"] == "01"
        assert payload["pids"] == ["0C"]
        assert payload["by_ecu"] == {"0x7E8": ["0C"]}

    def test_an_empty_result_is_falsy_by_length(self):
        assert len(SupportedPids(mode=0x01, by_ecu={})) == 0
