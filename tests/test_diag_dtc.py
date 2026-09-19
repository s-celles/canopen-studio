"""
Unit tests for ISO 15031-6 diagnostic trouble codes.

The count byte is the case that matters: ISO 15031-5 has a CAN response open with the
number of codes, but real ECUs disagree about whether it is there, and guessing wrong
shifts every code by one byte and prints faults the vehicle never reported.
"""

import pytest

from canopen_studio.diag.j1979.dtc import (
    DTC_MODES,
    DtcError,
    TroubleCode,
    decode_dtc,
    decode_dtc_response,
    encode_dtc,
    parse_dtc_payload,
    sort_codes,
    summarise,
)


class TestCodeDecoding:
    @pytest.mark.parametrize(
        "word, code",
        [
            (0x0143, "P0143"),
            (0x0000, "P0000"),
            (0x0101, "P0101"),
            (0x4321, "C0321"),
            (0x8123, "B0123"),
            (0xC456, "U0456"),
            (0x1234, "P1234"),
            (0x2345, "P2345"),
            (0x30FF, "P30FF"),
        ],
    )
    def test_a_word_decodes_to_its_printed_form(self, word, code):
        assert decode_dtc(word) == code

    def test_the_top_two_bits_choose_the_system(self):
        assert decode_dtc(0x0000)[0] == "P"
        assert decode_dtc(0x4000)[0] == "C"
        assert decode_dtc(0x8000)[0] == "B"
        assert decode_dtc(0xC000)[0] == "U"

    def test_the_last_three_digits_are_hexadecimal(self):
        assert decode_dtc(0x0ABC) == "P0ABC"

    def test_a_word_outside_sixteen_bits_is_refused(self):
        with pytest.raises(DtcError):
            decode_dtc(0x10000)


class TestCodeEncoding:
    @pytest.mark.parametrize("code", ["P0143", "C0321", "B0123", "U0456", "P30FF"])
    def test_decoding_round_trips(self, code):
        assert decode_dtc(encode_dtc(code)) == code

    def test_a_lowercase_code_is_accepted(self):
        assert encode_dtc("p0143") == 0x0143

    def test_surrounding_space_is_tolerated(self):
        assert encode_dtc(" P0143 ") == 0x0143

    def test_a_code_of_the_wrong_length_is_refused(self):
        with pytest.raises(DtcError):
            encode_dtc("P143")

    def test_an_unknown_system_letter_is_refused(self):
        with pytest.raises(DtcError):
            encode_dtc("X0143")

    def test_a_non_hexadecimal_code_is_refused(self):
        with pytest.raises(DtcError):
            encode_dtc("P01ZZ")

    def test_a_first_digit_above_three_is_refused(self):
        """Only two bits hold it, so P4xxx cannot exist."""
        with pytest.raises(DtcError):
            encode_dtc("P4123")


class TestPayloadParsing:
    def test_a_payload_with_a_count_byte_is_read(self):
        """43 02 01 43 01 96 — two codes, announced."""
        assert parse_dtc_payload(bytes([0x02, 0x01, 0x43, 0x01, 0x96])) == [0x0143, 0x0196]

    def test_a_payload_without_a_count_byte_is_read(self):
        assert parse_dtc_payload(bytes([0x01, 0x43, 0x01, 0x96])) == [0x0143, 0x0196]

    def test_a_count_of_zero_means_no_faults(self):
        assert parse_dtc_payload(bytes([0x00])) == []

    def test_an_empty_payload_means_no_faults(self):
        assert parse_dtc_payload(b"") == []

    def test_padding_pairs_are_dropped(self):
        """ECUs pad the response out to a frame boundary with zero words."""
        assert parse_dtc_payload(bytes([0x01, 0x01, 0x43, 0x00, 0x00])) == [0x0143]

    def test_a_count_of_zero_followed_by_padding_means_no_faults(self):
        assert parse_dtc_payload(bytes([0x00, 0x00, 0x00, 0x00])) == []

    def test_a_trailing_half_code_is_dropped(self):
        """A reply cut off mid-code must not invent a fault from one byte."""
        assert parse_dtc_payload(bytes([0x01, 0x43, 0x01, 0x96, 0x01])) == [0x0143, 0x0196]

    def test_a_count_followed_by_padding_is_honoured(self):
        """ECUs pad to a frame boundary, which makes the payload length even again."""
        assert parse_dtc_payload(bytes([0x02, 0x01, 0x43, 0x01, 0x96, 0x00, 0x00])) == [0x0143, 0x0196]

    def test_a_leading_byte_that_cannot_be_a_count_is_read_as_a_code(self):
        """Here 0x01 opens P0143; treating it as a count would report C0301 instead."""
        assert parse_dtc_payload(bytes([0x01, 0x43, 0x01, 0x96])) == [0x0143, 0x0196]

    def test_order_is_preserved(self):
        assert parse_dtc_payload(bytes([0x03, 0x01, 0x96, 0x01, 0x43, 0xC1, 0x00])) == [0x0196, 0x0143, 0xC100]


class TestResponseDecoding:
    def test_a_response_decodes_to_trouble_codes(self):
        codes = decode_dtc_response(bytes([0x02, 0x01, 0x43, 0x01, 0x96]))

        assert [code.code for code in codes] == ["P0143", "P0196"]

    def test_the_kind_is_carried_through(self):
        codes = decode_dtc_response(bytes([0x01, 0x01, 0x43]), kind="pending")

        assert codes[0].kind == "pending"

    def test_the_answering_ecu_is_carried_through(self):
        codes = decode_dtc_response(bytes([0x01, 0x01, 0x43]), source=0x7E8)

        assert codes[0].source == 0x7E8

    def test_a_description_is_attached_when_a_profile_supplies_one(self):
        codes = decode_dtc_response(bytes([0x01, 0x01, 0x43]), descriptions={"P0143": "O2 sensor low voltage"})

        assert codes[0].description == "O2 sensor low voltage"

    def test_a_code_without_a_description_keeps_an_empty_one(self):
        assert decode_dtc_response(bytes([0x01, 0x01, 0x43]))[0].description == ""

    def test_a_clean_vehicle_returns_no_codes(self):
        assert decode_dtc_response(bytes([0x00])) == []


class TestTroubleCodeValue:
    def test_the_system_is_named(self):
        assert TroubleCode(code="P0143", raw=0x0143).system == "Powertrain"
        assert TroubleCode(code="C0321", raw=0x4321).system == "Chassis"
        assert TroubleCode(code="B0123", raw=0x8123).system == "Body"
        assert TroubleCode(code="U0456", raw=0xC456).system == "Network"

    def test_a_generic_code_says_so(self):
        assert "Generic" in TroubleCode(code="P0143", raw=0x0143).defined_by

    def test_a_manufacturer_code_is_flagged(self):
        assert TroubleCode(code="P1143", raw=0x1143).is_manufacturer_specific is True

    def test_a_generic_code_is_not_flagged(self):
        assert TroubleCode(code="P0143", raw=0x0143).is_manufacturer_specific is False

    def test_an_ambiguous_first_digit_is_not_claimed_either_way(self):
        """P2xxx and P3xxx depend on the system, so neither answer is honest."""
        code = TroubleCode(code="P2143", raw=0x2143)

        assert code.is_manufacturer_specific is False
        assert "depending on the system" in code.defined_by

    def test_a_code_renders_with_its_system(self):
        assert str(TroubleCode(code="P0143", raw=0x0143)) == "P0143 (Powertrain)"

    def test_a_described_code_renders_its_description(self):
        code = TroubleCode(code="P0143", raw=0x0143, description="O2 sensor low voltage")

        assert str(code) == "P0143 (Powertrain) — O2 sensor low voltage"

    def test_a_code_serialises_for_an_agent(self):
        payload = TroubleCode(code="P0143", raw=0x0143, kind="stored", source=0x7E8).as_dict()

        assert payload["code"] == "P0143"
        assert payload["system"] == "Powertrain"
        assert payload["ecu"] == "0x7E8"
        assert payload["raw"] == "0x0143"

    def test_an_undescribed_code_omits_the_description(self):
        assert "description" not in TroubleCode(code="P0143", raw=0x0143).as_dict()


class TestModes:
    def test_the_three_reading_modes_are_the_standard_ones(self):
        assert DTC_MODES == {"stored": 0x03, "pending": 0x07, "permanent": 0x0A}


class TestSummary:
    def test_codes_are_counted_by_kind_and_system(self):
        codes = [
            TroubleCode(code="P0143", raw=0x0143, kind="stored"),
            TroubleCode(code="C0321", raw=0x4321, kind="stored"),
            TroubleCode(code="P0196", raw=0x0196, kind="pending"),
        ]

        summary = summarise(codes)

        assert summary["total"] == 3
        assert summary["by_kind"] == {"stored": 2, "pending": 1}
        assert summary["by_system"] == {"Powertrain": 2, "Chassis": 1}

    def test_an_empty_set_summarises_to_zero(self):
        assert summarise([])["total"] == 0

    def test_codes_sort_by_system_then_number(self):
        codes = [
            TroubleCode(code="U0456", raw=0xC456),
            TroubleCode(code="P0196", raw=0x0196),
            TroubleCode(code="P0143", raw=0x0143),
        ]

        assert [code.code for code in sort_codes(codes)] == ["P0143", "P0196", "U0456"]
