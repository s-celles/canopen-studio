"""
Unit tests for vehicle identification numbers.

The model year is the interesting part: its code repeats every thirty years, so 'A' is
both 1980 and 2010. The result has to say when it was inferred rather than told, because
profile resolution keys off it.
"""

import pytest

from canopen_studio.diag.j1979.vin import (
    VIN_LENGTH,
    VinError,
    check_digit,
    clean_vin,
    decode_model_year,
    extract_vin,
    is_valid_vin,
    parse_vin,
    region_of,
)

# A real-shaped VIN with a valid North American check digit.
SAMPLE = "1HGBH41JXMN109186"


class TestValidation:
    def test_a_well_formed_vin_is_accepted(self):
        assert is_valid_vin(SAMPLE) is True

    def test_a_short_vin_is_refused(self):
        assert is_valid_vin("1HGBH41JX") is False

    def test_a_long_vin_is_refused(self):
        assert is_valid_vin(SAMPLE + "0") is False

    @pytest.mark.parametrize("letter", "IOQ")
    def test_the_confusable_letters_are_refused(self, letter):
        """I, O and Q never appear: they are too easily read as 1 and 0."""
        assert is_valid_vin(SAMPLE[:5] + letter + SAMPLE[6:]) is False

    def test_punctuation_is_refused(self):
        assert is_valid_vin("1HGBH41JXMN10918-") is False

    def test_lowercase_is_accepted(self):
        assert is_valid_vin(SAMPLE.lower()) is True

    def test_surrounding_space_is_tolerated(self):
        assert is_valid_vin(f"  {SAMPLE}  ") is True

    def test_cleaning_strips_space_and_raises_case(self):
        assert clean_vin(" 1hgbh41jxmn109186 ") == SAMPLE

    def test_strict_validation_checks_the_check_digit(self):
        assert is_valid_vin(SAMPLE, strict=True) is True

    def test_strict_validation_rejects_a_bad_check_digit(self):
        broken = SAMPLE[:8] + "0" + SAMPLE[9:]

        assert is_valid_vin(broken, strict=True) is False

    def test_a_check_digit_is_not_required_by_default(self):
        """Vehicles built outside North America frequently carry none."""
        broken = SAMPLE[:8] + "0" + SAMPLE[9:]

        assert is_valid_vin(broken) is True


class TestCheckDigit:
    def test_the_check_digit_is_computed(self):
        assert check_digit(SAMPLE) == "X"

    def test_a_remainder_of_ten_prints_as_x(self):
        assert check_digit(SAMPLE) == "X"

    def test_a_vin_of_the_wrong_length_is_refused(self):
        with pytest.raises(VinError):
            check_digit("1HGBH41JX")

    def test_a_character_that_cannot_appear_is_refused(self):
        with pytest.raises(VinError):
            check_digit("1HGBH41JXMN10918-")


class TestModelYear:
    @pytest.mark.parametrize(
        "code, year",
        [("A", 1980), ("Y", 2000), ("1", 2001), ("9", 2009)],
    )
    def test_the_first_cycle_decodes(self, code, year):
        assert decode_model_year(code) == year

    @pytest.mark.parametrize(
        "code, year",
        [("A", 2010), ("Y", 2030), ("1", 2031), ("9", 2039)],
    )
    def test_the_second_cycle_decodes(self, code, year):
        assert decode_model_year(code, later_cycle=True) == year

    def test_the_skipped_letters_are_not_year_codes(self):
        for letter in "IOQUZ0":
            assert decode_model_year(letter) is None

    def test_a_lowercase_code_decodes(self):
        assert decode_model_year("m") == 1991


class TestRegions:
    @pytest.mark.parametrize(
        "wmi, region",
        [
            ("1HG", "North America"),
            ("JHM", "Asia"),
            ("WVW", "Europe"),
            ("AAV", "Africa"),
            ("6F4", "Oceania"),
            ("9BW", "South America"),
        ],
    )
    def test_the_region_follows_the_first_character(self, wmi, region):
        assert region_of(wmi) == region

    def test_an_empty_identifier_is_unknown(self):
        assert region_of("") == "Unknown"

    def test_an_unassigned_character_is_unknown(self):
        assert region_of("0AA") == "Unknown"


class TestParsing:
    def test_the_parts_are_split_at_the_standard_positions(self):
        info = parse_vin(SAMPLE)

        assert info.wmi == "1HG"
        assert info.vds == "BH41JX"
        assert info.vis == "MN109186"
        assert info.plant_code == "N"
        assert info.serial == "109186"

    def test_the_region_is_derived_from_the_identifier(self):
        assert parse_vin(SAMPLE).region == "North America"

    def test_the_model_year_is_inferred(self):
        """Position seven is a digit here, so the earlier cycle applies."""
        info = parse_vin(SAMPLE)

        assert info.model_year == 1991
        assert info.model_year_code == "M"

    def test_an_inferred_year_says_it_is_ambiguous(self):
        assert parse_vin(SAMPLE).model_year_is_ambiguous is True

    def test_an_inferred_year_offers_the_other_possibility(self):
        """Thirty years away, which a profile resolver must be able to consider."""
        assert parse_vin(SAMPLE).alternate_model_year == 2021

    def test_a_letter_in_position_seven_selects_the_later_cycle(self):
        later = SAMPLE[:6] + "J" + SAMPLE[7:]

        assert parse_vin(later).model_year == 2021

    def test_a_known_year_is_used_instead_of_being_inferred(self):
        info = parse_vin(SAMPLE, model_year=1991)

        assert info.model_year == 1991
        assert info.model_year_is_ambiguous is False
        assert info.alternate_model_year is None

    def test_the_check_digit_result_is_reported(self):
        assert parse_vin(SAMPLE).check_digit_valid is True

    def test_a_failing_check_digit_is_reported_rather_than_raised(self):
        broken = SAMPLE[:8] + "0" + SAMPLE[9:]

        assert parse_vin(broken).check_digit_valid is False

    def test_a_malformed_vin_is_refused(self):
        with pytest.raises(VinError):
            parse_vin("NOTAVIN")

    def test_the_error_says_what_was_expected(self):
        with pytest.raises(VinError) as excinfo:
            parse_vin("NOTAVIN")

        assert str(VIN_LENGTH) in str(excinfo.value)

    def test_it_renders_its_identity(self):
        assert str(parse_vin(SAMPLE)) == f"{SAMPLE} (1HG, North America, 1991)"

    def test_it_serialises_for_an_agent(self):
        payload = parse_vin(SAMPLE).as_dict()

        assert payload["vin"] == SAMPLE
        assert payload["wmi"] == "1HG"
        assert payload["model_year"] == 1991
        assert payload["model_year_alternative"] == 2021

    def test_a_known_year_omits_the_alternative(self):
        assert "model_year_alternative" not in parse_vin(SAMPLE, model_year=1991).as_dict()


class TestExtraction:
    def test_a_vin_is_pulled_out_of_a_mode_09_payload(self):
        """The payload opens with the PID echo and a count of data items."""
        payload = bytes([0x02, 0x01]) + SAMPLE.encode("ascii")

        assert extract_vin(payload) == SAMPLE

    def test_leading_padding_is_dropped(self):
        payload = bytes([0x02, 0x01, 0x00, 0x00, 0x00]) + SAMPLE.encode("ascii")

        assert extract_vin(payload) == SAMPLE

    def test_a_truncated_response_yields_nothing(self):
        """A partial reassembly must not come back as a short VIN nobody notices."""
        payload = bytes([0x02, 0x01]) + SAMPLE[:10].encode("ascii")

        assert extract_vin(payload) is None

    def test_an_empty_response_yields_nothing(self):
        assert extract_vin(b"") is None

    def test_text_that_is_not_a_vin_yields_nothing(self):
        payload = bytes([0x02, 0x01]) + b"NOT A VALID VIN!!"

        assert extract_vin(payload) is None
