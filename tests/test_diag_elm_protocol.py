"""
Unit tests for the ELM327 command dialogue.

Most of these drive `clean_reply()` directly with captured adapter output, because that
is where the awkward cases live: echo still on, autodetection chatter glued to the data,
status words in place of a reply, and replies cut off mid-transfer.
"""

import pytest
from elm327_fake import FakeElm327

from canopen_studio.diag import DiagnosticTimeout
from canopen_studio.diag.elm327.protocol import (
    ElmBufferFull,
    ElmBusError,
    ElmCommandNotSupported,
    ElmNoData,
    ElmProtocol,
    ElmStopped,
    ElmUnableToConnect,
    clean_reply,
)


class TestCleaning:
    def test_a_plain_reply_yields_its_data_line(self):
        reply = clean_reply("0100", "41 00 BE 3E B8 11\r\r>")

        assert reply.lines == ("41 00 BE 3E B8 11",)
        assert reply.ok is True

    def test_the_prompt_is_not_part_of_the_data(self):
        assert ">" not in clean_reply("ATI", "ELM327 v1.5\r\r>").value

    def test_the_echo_of_the_command_is_dropped(self):
        """Until ATE0 lands the adapter repeats the command back."""
        reply = clean_reply("0100", "0100\r41 00 BE 3E B8 11\r\r>")

        assert reply.lines == ("41 00 BE 3E B8 11",)

    def test_an_echo_written_with_spaces_is_still_recognised(self):
        reply = clean_reply("01 00", "01 00\r41 00 BE 3E B8 11\r\r>")

        assert reply.lines == ("41 00 BE 3E B8 11",)

    def test_a_data_line_equal_to_the_command_is_kept_when_it_follows_data(self):
        """Only the leading echo is dropped, never a later line that happens to match."""
        reply = clean_reply("0100", "41 00 BE 3E B8 11\r0100\r\r>")

        assert reply.lines == ("41 00 BE 3E B8 11", "0100")

    def test_searching_chatter_is_dropped(self):
        reply = clean_reply("0100", "SEARCHING...\r41 00 BE 3E B8 11\r\r>")

        assert reply.lines == ("41 00 BE 3E B8 11",)

    def test_searching_glued_to_the_data_is_dropped(self):
        """Some adapters omit the carriage return after the chatter."""
        reply = clean_reply("0100", "SEARCHING...41 00 BE 3E B8 11\r\r>")

        assert reply.lines == ("41 00 BE 3E B8 11",)

    def test_repeated_searching_chatter_is_dropped(self):
        reply = clean_reply("0100", "SEARCHING...SEARCHING...41 00 BE\r\r>")

        assert reply.lines == ("41 00 BE",)

    def test_bus_initialisation_chatter_is_dropped(self):
        reply = clean_reply("0100", "BUS INIT: OK\r41 00 BE 3E B8 11\r\r>")

        assert reply.lines == ("41 00 BE 3E B8 11",)

    def test_power_management_notices_are_dropped(self):
        reply = clean_reply("0100", "LP ALERT\r41 00 BE\r\r>")

        assert reply.lines == ("41 00 BE",)

    def test_linefeeds_are_handled_as_well_as_bare_carriage_returns(self):
        reply = clean_reply("0100", "41 00 BE\r\n41 20 80\r\n\r\n>")

        assert reply.lines == ("41 00 BE", "41 20 80")

    def test_several_lines_are_kept_in_order(self):
        """A multi-frame reply arrives one ISO-TP frame per line."""
        raw = "7E8 10 14 49 02 01 31 44 34\r7E8 21 47 50 30 30 52 35 35\r\r>"

        assert clean_reply("0902", raw).lines == (
            "7E8 10 14 49 02 01 31 44 34",
            "7E8 21 47 50 30 30 52 35 35",
        )

    def test_an_empty_reply_carries_no_lines(self):
        assert clean_reply("ATE0", "\r\r>").lines == ()


class TestStatusWords:
    @pytest.mark.parametrize(
        "raw, status",
        [
            ("NO DATA\r\r>", "NO DATA"),
            ("CAN ERROR\r\r>", "CAN ERROR"),
            ("UNABLE TO CONNECT\r\r>", "UNABLE TO CONNECT"),
            ("BUFFER FULL\r\r>", "BUFFER FULL"),
            ("STOPPED\r\r>", "STOPPED"),
            ("?\r\r>", "?"),
            ("BUS BUSY\r\r>", "BUS BUSY"),
            ("FB ERROR\r\r>", "FB ERROR"),
            ("DATA ERROR\r\r>", "DATA ERROR"),
        ],
    )
    def test_each_status_word_is_recognised(self, raw, status):
        assert clean_reply("0100", raw).status == status

    def test_a_status_word_is_not_reported_as_data(self):
        assert clean_reply("0100", "NO DATA\r\r>").lines == ()

    def test_searching_followed_by_a_failure_reports_the_failure(self):
        """The common shape when the ignition is off."""
        reply = clean_reply("0100", "SEARCHING...\rUNABLE TO CONNECT\r\r>")

        assert reply.status == "UNABLE TO CONNECT"

    def test_a_failure_glued_to_the_chatter_is_still_recognised(self):
        reply = clean_reply("0100", "SEARCHING...UNABLE TO CONNECT\r\r>")

        assert reply.status == "UNABLE TO CONNECT"

    def test_a_bus_initialisation_failure_is_a_status_not_chatter(self):
        """The status keeps the adapter's own wording; classification happens on raising."""
        reply = clean_reply("0100", "BUS INIT: ERROR\r\r>")

        assert reply.status == "BUS INIT: ERROR"
        with pytest.raises(ElmUnableToConnect):
            reply.raise_for_status()

    def test_an_internal_fault_code_is_reported(self):
        assert clean_reply("0100", "ERR94\r\r>").status == "ERR94"

    def test_ok_is_data_not_a_status(self):
        """AT commands acknowledge with OK; treating it as a status would break setup."""
        reply = clean_reply("ATE0", "OK\r\r>")

        assert reply.status is None
        assert reply.value == "OK"

    def test_a_reply_with_a_status_is_not_ok(self):
        assert clean_reply("0100", "NO DATA\r\r>").ok is False

    def test_no_data_has_its_own_flag(self):
        assert clean_reply("0100", "NO DATA\r\r>").no_data is True

    def test_an_unknown_command_has_its_own_flag(self):
        assert clean_reply("ATCRA7E8", "?\r\r>").is_unsupported is True


class TestRaiseForStatus:
    @pytest.mark.parametrize(
        "raw, exception",
        [
            ("NO DATA\r\r>", ElmNoData),
            ("CAN ERROR\r\r>", ElmBusError),
            ("UNABLE TO CONNECT\r\r>", ElmUnableToConnect),
            ("BUFFER FULL\r\r>", ElmBufferFull),
            ("STOPPED\r\r>", ElmStopped),
            ("?\r\r>", ElmCommandNotSupported),
        ],
    )
    def test_each_status_raises_its_own_exception(self, raw, exception):
        with pytest.raises(exception):
            clean_reply("0100", raw).raise_for_status()

    def test_a_good_reply_passes_through(self):
        reply = clean_reply("0100", "41 00 BE\r\r>")

        assert reply.raise_for_status() is reply

    def test_the_exception_names_the_command_that_caused_it(self):
        with pytest.raises(ElmNoData) as excinfo:
            clean_reply("0100", "NO DATA\r\r>").raise_for_status()

        assert "0100" in str(excinfo.value)


class TestProtocolExchange:
    def test_a_command_is_terminated_with_a_carriage_return(self):
        adapter = FakeElm327()
        adapter.open()

        ElmProtocol(adapter).send("ATI")

        assert adapter.commands == ["ATI"]

    def test_the_version_banner_is_returned(self):
        adapter = FakeElm327(version="ELM327 v2.1")
        adapter.open()

        assert ElmProtocol(adapter).send("ATI").value == "ELM327 v2.1"

    def test_the_echo_of_the_first_command_is_removed(self):
        """The fake powers up with echo on, exactly as a real chip does."""
        adapter = FakeElm327()
        adapter.open()

        assert ElmProtocol(adapter).send("ATI").lines == ("ELM327 v1.5",)

    def test_turning_the_echo_off_is_acknowledged(self):
        adapter = FakeElm327()
        adapter.open()

        assert ElmProtocol(adapter).send("ATE0").value == "OK"

    def test_an_unsupported_command_reports_the_question_mark(self):
        adapter = FakeElm327(unsupported=["CRA7E8"])
        adapter.open()

        assert ElmProtocol(adapter).send("ATCRA7E8").is_unsupported is True

    def test_an_obd_request_returns_its_framed_reply(self):
        adapter = FakeElm327(ecus={"010C": {0x7E8: bytes([0x41, 0x0C, 0x1A, 0xF8])}})
        adapter.open()
        protocol = ElmProtocol(adapter)
        protocol.send("ATH1")

        reply = protocol.send("010C")

        assert reply.lines == ("7E8 04 41 0C 1A F8",)

    def test_an_unknown_request_answers_no_data(self):
        adapter = FakeElm327()
        adapter.open()

        assert ElmProtocol(adapter).send("010C").no_data is True

    def test_a_scripted_status_is_reported(self):
        adapter = FakeElm327(status="CAN ERROR")
        adapter.open()

        assert ElmProtocol(adapter).send("010C").status == "CAN ERROR"

    def test_a_silent_adapter_times_out(self):
        """A dead link must fail with its own error rather than hang the caller."""

        class Silent(FakeElm327):
            def write(self, data):
                pass

        adapter = Silent()
        adapter.open()

        with pytest.raises(DiagnosticTimeout):
            ElmProtocol(adapter, timeout=0.05).send("ATI")

    def test_the_timeout_message_names_the_command(self):
        class Silent(FakeElm327):
            def write(self, data):
                pass

        adapter = Silent()
        adapter.open()

        with pytest.raises(DiagnosticTimeout) as excinfo:
            ElmProtocol(adapter, timeout=0.05).send("0100")

        assert "0100" in str(excinfo.value)

    def test_a_reply_without_a_prompt_times_out_rather_than_being_parsed(self):
        """Half a reply is worse than none: it would decode to a plausible wrong value."""

        class Truncating(FakeElm327):
            def _prompt(self):
                pass

        adapter = Truncating(ecus={"010C": {0x7E8: bytes([0x41, 0x0C, 0x1A, 0xF8])}})
        adapter.open()

        with pytest.raises(DiagnosticTimeout):
            ElmProtocol(adapter, timeout=0.05).send("010C")

    def test_the_raw_text_of_the_last_exchange_is_kept_for_diagnosis(self):
        adapter = FakeElm327()
        adapter.open()
        protocol = ElmProtocol(adapter)

        protocol.send("ATI")

        assert "ELM327 v1.5" in protocol.last_raw

    def test_an_explicit_timeout_overrides_the_default(self):
        adapter = FakeElm327()
        adapter.open()

        assert ElmProtocol(adapter, timeout=0.01).send("ATI", timeout=1.0).value == "ELM327 v1.5"
