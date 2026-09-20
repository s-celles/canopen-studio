"""
Unit tests for the ELM327 diagnostic interface.

Covers the initialisation dialogue, protocol detection and refusal, the attribution of
responses to ECUs, multi-frame reassembly from the adapter's printed frames, and the
error and truncation cases an adapter produces on a real car.
"""

import pytest
from elm327_fake import FakeElm327

from canopen_studio.diag import DiagnosticResponse, NotConnectedError, ProtocolError
from canopen_studio.diag.elm327.interface import (
    ElmDiagnosticInterface,
    UnsupportedElmProtocol,
    parse_frame_line,
)
from canopen_studio.diag.elm327.protocol import ElmBusError, ElmUnableToConnect

RPM = {"010C": {0x7E8: bytes([0x41, 0x0C, 0x1A, 0xF8])}}
VIN = {"0902": {0x7E8: bytes([0x49, 0x02, 0x01]) + b"1D4GP00R55B123456"}}


def make(ecus=None, **kwargs):
    """Open an interface on a fake adapter and hand both back."""
    adapter_kwargs = {k: kwargs.pop(k) for k in list(kwargs) if k in FAKE_KEYS}
    adapter = FakeElm327(ecus=ecus, **adapter_kwargs)
    iface = ElmDiagnosticInterface(adapter, **kwargs)
    iface.default_timeout = 1.0
    return iface, adapter


FAKE_KEYS = {"version", "unsupported", "status", "truncate", "device_description", "extended", "dpn", "voltage"}


class TestFrameLineParsing:
    def test_an_eleven_bit_line_splits_into_source_and_frame(self):
        assert parse_frame_line("7E8 04 41 0C 1A F8") == (0x7E8, bytes([0x04, 0x41, 0x0C, 0x1A, 0xF8]))

    def test_a_twenty_nine_bit_header_spans_four_tokens(self):
        source, frame = parse_frame_line("18 DA F1 10 04 41 0C 1A F8", extended=True)

        assert source == 0x18DAF110
        assert frame == bytes([0x04, 0x41, 0x0C, 0x1A, 0xF8])

    def test_a_bare_header_carries_no_frame(self):
        assert parse_frame_line("7E8") is None

    def test_an_empty_line_carries_no_frame(self):
        assert parse_frame_line("   ") is None

    def test_an_odd_number_of_hex_digits_is_malformed(self):
        """The classic shape of a reply cut off mid-byte."""
        with pytest.raises(ProtocolError):
            parse_frame_line("7E8 04 41 0C 1A F")

    def test_non_hexadecimal_output_is_malformed(self):
        with pytest.raises(ProtocolError):
            parse_frame_line("7E8 04 41 ZZ")

    def test_the_headers_off_format_is_rejected_with_an_explanation(self):
        """Without headers a line cannot be attributed to an ECU at all."""
        with pytest.raises(ProtocolError) as excinfo:
            parse_frame_line("0: 49 02 01 31 44 34")

        assert "ATH1" in str(excinfo.value)

    def test_an_unparsable_header_is_skipped_rather_than_raising(self):
        assert parse_frame_line("BUS 04 41 0C") is None


class TestInitialisation:
    def test_opening_resets_the_adapter(self):
        iface, adapter = make()

        iface.open()

        assert "ATZ" in adapter.commands

    def test_opening_turns_the_echo_off(self):
        iface, adapter = make()

        iface.open()

        assert adapter.echo is False

    def test_opening_turns_headers_on(self):
        """Without headers no response can be attributed to an ECU."""
        iface, adapter = make()

        iface.open()

        assert adapter.headers is True

    def test_opening_keeps_spaces_on(self):
        """Spaces are what make the header separable from the data."""
        iface, adapter = make()

        iface.open()

        assert adapter.spaces is True

    def test_opening_selects_the_requested_protocol(self):
        iface, adapter = make(protocol="6")

        iface.open()

        assert "ATSP6" in adapter.commands

    def test_autodetection_is_the_default(self):
        iface, adapter = make()

        iface.open()

        assert "ATSP0" in adapter.commands

    def test_capabilities_are_probed_while_opening(self):
        iface, _ = make(version="ELM327 v2.1")

        iface.open()

        assert iface.capabilities.reported_version == 2.1

    def test_a_clone_is_detected_while_opening(self):
        iface, _ = make(version="ELM327 v2.1", unsupported=["CRA"])

        iface.open()

        assert iface.capabilities.is_probable_clone is True

    def test_adaptive_timing_is_enabled_when_the_board_has_it(self):
        """Sent twice: once to probe for it, once to apply it after the probe passed."""
        iface, adapter = make()

        iface.open()

        assert adapter.commands.count("ATAT1") == 2

    def test_adaptive_timing_is_skipped_on_a_board_without_it(self):
        """Degrading quietly is the point: an old board still works, just slower."""
        iface, adapter = make(version="ELM327 v1.0", unsupported=["AT1"])

        iface.open()

        assert adapter.commands.count("ATAT1") == 1

    def test_the_active_protocol_is_recorded(self):
        iface, _ = make(dpn="A6")

        iface.open()

        assert iface.active_protocol == "6"

    def test_a_forced_protocol_number_without_the_auto_prefix_is_read(self):
        iface, _ = make(dpn="6")

        iface.open()

        assert iface.active_protocol == "6"

    def test_an_eleven_bit_protocol_is_not_extended(self):
        iface, _ = make(dpn="A6")

        iface.open()

        assert iface.extended_addressing is False

    def test_a_twenty_nine_bit_protocol_is_extended(self):
        iface, _ = make(dpn="A7")

        iface.open()

        assert iface.extended_addressing is True

    def test_a_non_can_protocol_is_refused_by_name(self):
        """K-line frames differently; mis-parsing it would invent plausible values."""
        iface, _ = make(dpn="A3")

        with pytest.raises(UnsupportedElmProtocol) as excinfo:
            iface.open()

        assert "ISO 9141-2" in str(excinfo.value)

    def test_the_description_names_the_adapter_and_the_protocol(self):
        iface, _ = make(version="ELM327 v1.5")
        iface.open()

        assert "ELM327 v1.5" in iface.description
        assert "ISO 15765-4 CAN" in iface.description

    def test_requests_before_opening_are_refused(self):
        iface, _ = make()

        with pytest.raises(NotConnectedError):
            iface.service(0x01, 0x0C)

    def test_closing_releases_the_transport(self):
        iface, adapter = make()
        iface.open()

        iface.close()

        assert adapter.is_open is False

    def test_closing_asks_the_adapter_to_drop_the_protocol(self):
        iface, adapter = make()
        iface.open()

        iface.close()

        assert "ATPC" in adapter.commands


class TestExchange:
    def test_a_request_is_sent_as_hexadecimal(self):
        iface, adapter = make(RPM)
        iface.open()

        iface.service(0x01, 0x0C)

        assert "010C" in adapter.commands

    def test_a_response_is_attributed_to_its_ecu(self):
        iface, _ = make(RPM)
        iface.open()

        replies = iface.service(0x01, 0x0C)

        assert replies == [DiagnosticResponse(source=0x7E8, data=bytes([0x41, 0x0C, 0x1A, 0xF8]))]

    def test_every_answering_ecu_is_reported(self):
        ecus = {"0100": {0x7E8: bytes([0x41, 0x00, 0xBE]), 0x7E9: bytes([0x41, 0x00, 0x80])}}
        iface, _ = make(ecus)
        iface.open()

        replies = iface.service(0x01, 0x00)

        assert sorted(r.source for r in replies) == [0x7E8, 0x7E9]

    def test_a_multi_frame_response_is_reassembled(self):
        iface, _ = make(VIN)
        iface.open()

        replies = iface.service(0x09, 0x02)

        assert replies[0].data[3:] == b"1D4GP00R55B123456"

    def test_no_data_means_an_empty_result_not_an_error(self):
        """A PID scan meets this constantly; raising would make a scan impossible."""
        iface, _ = make()
        iface.open()

        assert iface.service(0x01, 0x0C) == []

    def test_a_bus_error_is_raised(self):
        iface, _ = make(RPM, status="CAN ERROR")
        iface.open()

        with pytest.raises(ElmBusError):
            iface.service(0x01, 0x0C)

    def test_an_unreachable_bus_is_raised(self):
        iface, _ = make(RPM, status="UNABLE TO CONNECT")
        iface.open()

        with pytest.raises(ElmUnableToConnect):
            iface.service(0x01, 0x0C)

    def test_expected_responses_are_advertised_to_the_adapter(self):
        """Letting it stop early is the single biggest speed-up for a scan."""
        iface, adapter = make(RPM, expected_responses=1)
        iface.open()

        iface.service(0x01, 0x0C)

        assert "010C1" in adapter.commands

    def test_the_hint_is_omitted_when_the_ecu_count_is_unknown(self):
        iface, adapter = make(RPM)
        iface.open()

        iface.service(0x01, 0x0C)

        assert "010C" in adapter.commands


class TestMalformedReplies:
    def test_a_truncated_line_does_not_produce_a_response(self):
        iface, _ = make(RPM, truncate=9)
        iface.open()

        assert iface.service(0x01, 0x0C) == []

    def test_a_truncated_line_is_recorded_for_the_caller(self):
        iface, _ = make(RPM, truncate=9)
        iface.open()

        iface.service(0x01, 0x0C)

        assert iface.last_errors

    def test_a_transfer_cut_off_mid_flight_is_reported(self):
        """The first frame arrives, the rest never does: the VIN must not come back short."""

        class Cutting(FakeElm327):
            def _handle_obd(self, request_hex):
                self._line("7E8 10 14 49 02 01 31 44 34")

        adapter = Cutting()
        iface = ElmDiagnosticInterface(adapter)
        iface.default_timeout = 1.0
        iface.open()

        replies = iface.service(0x09, 0x02)

        assert replies == []
        assert any("cut off" in error for error in iface.last_errors)

    def test_errors_are_cleared_between_requests(self):
        iface, _ = make(RPM, truncate=9)
        iface.open()
        iface.service(0x01, 0x0C)

        iface.service(0x01, 0x0D)

        assert iface.last_errors == []

    def test_a_bad_line_does_not_hide_a_good_one(self):
        class Noisy(FakeElm327):
            def _handle_obd(self, request_hex):
                self._line("7E8 04 41 0C 1A F")
                self._line("7E9 04 41 0C 1A F8")

        adapter = Noisy()
        iface = ElmDiagnosticInterface(adapter)
        iface.default_timeout = 1.0
        iface.open()

        replies = iface.service(0x01, 0x0C)

        assert [r.source for r in replies] == [0x7E9]


class TestVoltage:
    def test_the_battery_voltage_is_read(self):
        iface, _ = make(voltage="12.6V")
        iface.open()

        assert iface.read_voltage() == pytest.approx(12.6)

    def test_an_unreadable_voltage_yields_none(self):
        iface, _ = make(voltage="ERROR")
        iface.open()

        assert iface.read_voltage() is None
