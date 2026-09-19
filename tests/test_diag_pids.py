"""
Unit tests for PID definitions and the shipped SAE J1979 table.

The last class checks the bundled `j1979_base.yaml` itself: that it parses, that every
formula in it is valid, and that the parameters people actually read decode to the values
the standard's own worked examples give.
"""

from pathlib import Path

import pytest
import yaml

from canopen_studio.diag.j1979.pids import (
    MODE_CURRENT_DATA,
    MODE_NAMES,
    MODE_VEHICLE_INFO,
    PidDefinition,
    PidError,
    PidTable,
    format_key,
    parse_key,
)

BASE_PROFILE = Path(__file__).resolve().parents[1] / "src/canopen_studio/diag/profiles/data/j1979_base.yaml"


@pytest.fixture(scope="module")
def base_table():
    """The SAE J1979 table as it ships."""
    document = yaml.safe_load(BASE_PROFILE.read_text(encoding="utf-8"))
    return PidTable.from_mapping(document["pids"], origin="j1979_base")


def definition(key="01:0C", **entry):
    entry.setdefault("name", "test_pid")
    return PidDefinition.from_mapping(key, entry)


class TestKeys:
    def test_a_key_reads_as_two_hexadecimal_numbers(self):
        assert parse_key("01:0C") == (1, 12)

    def test_a_key_is_case_insensitive(self):
        assert parse_key("01:0c") == (1, 12)

    def test_surrounding_space_is_tolerated(self):
        assert parse_key(" 09:02 ") == (9, 2)

    def test_a_key_without_a_separator_is_refused(self):
        with pytest.raises(PidError):
            parse_key("010C")

    def test_a_non_hexadecimal_key_is_refused(self):
        with pytest.raises(PidError):
            parse_key("01:ZZ")

    def test_a_key_renders_the_way_a_file_writes_it(self):
        assert format_key(1, 12) == "01:0C"

    def test_a_key_round_trips(self):
        assert parse_key(format_key(9, 2)) == (9, 2)


class TestFormulaDecoding:
    def test_a_formula_pid_decodes_to_a_number(self):
        value = definition(formula="(256 * A + B) / 4", unit="rpm").decode(bytes([0x1A, 0xF8]))

        assert value.value == pytest.approx(1726.0)
        assert value.unit == "rpm"

    def test_the_kind_reflects_how_it_decodes(self):
        assert definition(formula="A").kind == "formula"

    def test_a_response_too_short_for_the_formula_is_reported_with_the_pid(self):
        with pytest.raises(PidError) as excinfo:
            definition(formula="(256 * A + B) / 4").decode(b"\x1a")

        assert "01:0C" in str(excinfo.value)
        assert "test_pid" in str(excinfo.value)

    def test_the_raw_bytes_are_kept_alongside_the_value(self):
        value = definition(formula="A").decode(b"\x1a\xf8")

        assert value.raw == b"\x1a\xf8"

    def test_the_answering_ecu_is_carried_through(self):
        value = definition(formula="A").decode(b"\x1a", source=0x7E8)

        assert value.source == 0x7E8


class TestEnumDecoding:
    def test_a_known_value_decodes_to_its_label(self):
        value = definition(values={2: "Closed loop"}).decode(b"\x02")

        assert value.value == "Closed loop"

    def test_an_unknown_value_is_named_rather_than_dropped(self):
        """Manufacturers use values the standard does not list."""
        value = definition(values={2: "Closed loop"}).decode(b"\x09")

        assert "0x09" in value.value

    def test_an_empty_response_is_reported(self):
        with pytest.raises(PidError):
            definition(values={2: "Closed loop"}).decode(b"")

    def test_the_kind_reflects_how_it_decodes(self):
        assert definition(values={0: "off"}).kind == "enum"


class TestBitDecoding:
    def test_named_flags_decode_from_their_bits(self):
        value = definition(bits={"A.0": "sensor_1", "A.1": "sensor_2"}).decode(b"\x01")

        assert value.value == {"sensor_1": True, "sensor_2": False}

    def test_a_flag_in_a_later_byte_is_addressed_by_its_letter(self):
        value = definition(bits={"B.7": "lamp"}).decode(b"\x00\x80")

        assert value.value == {"lamp": True}

    def test_a_response_too_short_for_a_flag_is_reported(self):
        with pytest.raises(PidError) as excinfo:
            definition(bits={"D.0": "late_flag"}).decode(b"\x01")

        assert "late_flag" in str(excinfo.value)

    def test_a_malformed_bit_reference_is_refused_at_definition_time(self):
        """A bad profile must fail when it loads, not on the car."""
        with pytest.raises(PidError):
            definition(bits={"A": "no_bit_index"})

    def test_a_bit_index_outside_a_byte_is_refused(self):
        with pytest.raises(PidError):
            definition(bits={"A.9": "impossible"})

    def test_the_kind_reflects_how_it_decodes(self):
        assert definition(bits={"A.0": "flag"}).kind == "bits"


class TestAsciiDecoding:
    def test_text_decodes_to_a_string(self):
        value = definition("09:02", ascii=True).decode(b"1D4GP00R55B123456")

        assert value.value == "1D4GP00R55B123456"

    def test_padding_bytes_are_dropped(self):
        """ECUs pad the field with NUL or with spaces, and some prefix it with zeros."""
        value = definition("09:02", ascii=True).decode(b"\x00\x00 1D4GP00R55B123456 \x00")

        assert value.value == "1D4GP00R55B123456"

    def test_the_kind_reflects_how_it_decodes(self):
        assert definition("09:02", ascii=True).kind == "ascii"


class TestRawDecoding:
    def test_a_definition_with_no_decoding_keeps_the_bytes(self):
        """A PID a file lists but cannot describe still contributes that it exists."""
        value = definition().decode(b"\xbe\x3e\xb8\x11")

        assert value.value == b"\xbe\x3e\xb8\x11"

    def test_the_kind_reflects_how_it_decodes(self):
        assert definition().kind == "raw"


class TestDefinitionValidation:
    def test_a_definition_without_a_name_is_refused(self):
        with pytest.raises(PidError):
            PidDefinition.from_mapping("01:0C", {"formula": "A"})

    def test_an_entry_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(PidError):
            PidDefinition.from_mapping("01:0C", "engine speed")

    def test_a_broken_formula_is_refused_with_its_pid(self):
        with pytest.raises(PidError) as excinfo:
            definition(formula="__import__('os')")

        assert "01:0C" in str(excinfo.value)

    def test_the_origin_is_recorded(self):
        entry = PidDefinition.from_mapping("01:0C", {"name": "rpm"}, origin="ford_focus")

        assert entry.origin == "ford_focus"

    def test_a_definition_renders_its_key_name_and_unit(self):
        assert str(definition(formula="A", unit="rpm")) == "01:0C test_pid [rpm]"


class TestPidValueRendering:
    def test_a_numeric_reading_renders_with_its_unit(self):
        value = definition(formula="A - 40", unit="°C").decode(b"\x7b")

        assert str(value) == "test_pid = 83 °C"

    def test_a_unitless_reading_renders_without_a_trailing_space(self):
        assert str(definition(formula="A").decode(b"\x05")) == "test_pid = 5"

    def test_raw_bytes_render_as_hexadecimal(self):
        assert str(definition().decode(b"\xbe\x3e")) == "test_pid = BE 3E"

    def test_a_reading_serialises_for_an_agent(self):
        payload = definition(formula="A - 40", unit="°C").decode(b"\x7b", source=0x7E8).as_dict()

        assert payload["pid"] == "01:0C"
        assert payload["value"] == 83
        assert payload["unit"] == "°C"
        assert payload["ecu"] == "0x7E8"

    def test_raw_bytes_serialise_as_hexadecimal(self):
        assert definition().decode(b"\xbe\x3e").as_dict()["value"] == "BE3E"

    def test_an_anonymous_reading_omits_the_ecu(self):
        assert "ecu" not in definition(formula="A").decode(b"\x01").as_dict()


class TestPidTable:
    def test_a_definition_is_found_by_mode_and_pid(self):
        table = PidTable.from_mapping({"01:0C": {"name": "rpm", "formula": "A"}})

        assert table.get(0x01, 0x0C).name == "rpm"

    def test_an_absent_pid_yields_none(self):
        assert PidTable().get(0x01, 0x0C) is None

    def test_a_definition_is_found_by_name(self):
        table = PidTable.from_mapping({"01:0C": {"name": "rpm", "formula": "A"}})

        assert table.by_name("rpm").pid == 0x0C

    def test_an_unknown_name_yields_none(self):
        assert PidTable().by_name("rpm") is None

    def test_definitions_of_one_mode_come_back_in_pid_order(self):
        table = PidTable.from_mapping({"01:0D": {"name": "speed"}, "01:0C": {"name": "rpm"}, "09:02": {"name": "vin"}})

        assert [d.pid for d in table.pids_for_mode(0x01)] == [0x0C, 0x0D]

    def test_the_modes_described_are_reported(self):
        table = PidTable.from_mapping({"01:0C": {"name": "rpm"}, "09:02": {"name": "vin"}})

        assert table.modes() == [0x01, 0x09]

    def test_a_table_counts_its_definitions(self):
        assert len(PidTable.from_mapping({"01:0C": {"name": "rpm"}})) == 1

    def test_a_table_iterates_in_mode_then_pid_order(self):
        table = PidTable.from_mapping({"09:02": {"name": "vin"}, "01:0C": {"name": "rpm"}})

        assert [d.key for d in table] == ["01:0C", "09:02"]

    def test_merging_lets_the_later_table_win(self):
        """This is what profile inheritance needs: a child redefining one PID."""
        base = PidTable.from_mapping({"01:0C": {"name": "rpm"}, "01:0D": {"name": "speed"}})
        child = PidTable.from_mapping({"01:0C": {"name": "engine_speed"}})

        merged = base.merged_with(child)

        assert merged.get(0x01, 0x0C).name == "engine_speed"
        assert merged.get(0x01, 0x0D).name == "speed"

    def test_merging_does_not_alter_either_table(self):
        base = PidTable.from_mapping({"01:0C": {"name": "rpm"}})
        child = PidTable.from_mapping({"01:0C": {"name": "engine_speed"}})

        base.merged_with(child)

        assert base.get(0x01, 0x0C).name == "rpm"


class TestShippedJ1979Table:
    """The bundled table is data, so it is verified like data."""

    def test_the_profile_file_parses(self, base_table):
        assert len(base_table) > 90

    def test_it_describes_both_legislated_modes(self, base_table):
        assert base_table.modes() == [MODE_CURRENT_DATA, MODE_VEHICLE_INFO]

    def test_every_mode_of_the_standard_is_named(self):
        assert len(MODE_NAMES) == 10
        assert MODE_NAMES[MODE_VEHICLE_INFO] == "Vehicle information"

    def test_every_definition_carries_a_unique_name(self, base_table):
        names = [d.name for d in base_table]

        assert len(names) == len(set(names))

    def test_the_supported_pid_bitmasks_are_present(self, base_table):
        """Discovery walks these, so a missing one silently truncates a scan."""
        for pid in (0x00, 0x20, 0x40, 0x60):
            assert base_table.get(MODE_CURRENT_DATA, pid) is not None

    @pytest.mark.parametrize(
        "pid, data, expected",
        [
            (0x04, [0xFF], 100.0),  # engine load, full scale
            (0x05, [0x7B], 83),  # coolant temperature, 123 - 40
            (0x0C, [0x1A, 0xF8], 1726.0),  # engine speed
            (0x0D, [0x64], 100),  # vehicle speed
            (0x0E, [0x80], 0.0),  # timing advance, 128/2 - 64
            (0x0F, [0x28], 0),  # intake air temperature, 40 - 40
            (0x10, [0x0F, 0xA0], 40.0),  # mass air flow, 4000 / 100
            (0x11, [0x80], pytest.approx(50.196, abs=1e-3)),  # throttle position
            (0x1F, [0x01, 0x2C], 300),  # run time, 300 seconds
            (0x2F, [0xFF], 100.0),  # fuel tank level
            (0x33, [0x65], 101),  # barometric pressure
            (0x3C, [0x0F, 0xA0], 360.0),  # catalyst temperature, 4000/10 - 40
            (0x42, [0x30, 0x39], 12.345),  # control module voltage
            (0x46, [0x32], 10),  # ambient air temperature, 50 - 40
            (0x5C, [0x69], 65),  # oil temperature, 105 - 40
            (0x5D, [0x69, 0x00], 0.0),  # fuel injection timing, 26880 baseline
            (0x5E, [0x00, 0xC8], 10.0),  # fuel rate, 200 / 20
        ],
    )
    def test_the_common_parameters_decode_to_the_standard_values(self, base_table, pid, data, expected):
        value = base_table.get(MODE_CURRENT_DATA, pid).decode(bytes(data))

        assert value.value == expected

    def test_a_signed_pressure_decodes_below_zero(self, base_table):
        value = base_table.get(MODE_CURRENT_DATA, 0x54).decode(bytes([0xFF, 0xFF]))

        assert value.value == -1

    def test_fuel_system_status_decodes_to_its_label(self, base_table):
        value = base_table.get(MODE_CURRENT_DATA, 0x03).decode(bytes([0x02, 0x00]))

        assert value.value == "Closed loop, using oxygen sensor feedback"

    def test_fuel_type_decodes_to_its_label(self, base_table):
        assert base_table.get(MODE_CURRENT_DATA, 0x51).decode(b"\x01").value == "Gasoline"

    def test_the_obd_standard_decodes_to_its_label(self, base_table):
        assert base_table.get(MODE_CURRENT_DATA, 0x1C).decode(b"\x06").value == "EOBD"

    def test_the_malfunction_lamp_flag_decodes(self, base_table):
        value = base_table.get(MODE_CURRENT_DATA, 0x01).decode(bytes([0x83, 0x07, 0xFF, 0x00]))

        assert value.value["malfunction_indicator_lamp"] is True

    def test_the_vin_is_declared_as_text(self, base_table):
        assert base_table.get(MODE_VEHICLE_INFO, 0x02).kind == "ascii"

    def test_the_ecu_name_is_declared_as_text(self, base_table):
        assert base_table.get(MODE_VEHICLE_INFO, 0x0A).kind == "ascii"

    def test_every_declared_length_covers_what_its_formula_reads(self, base_table):
        """A definition promising one byte but reading two would fail only on a car."""
        for entry in base_table:
            if entry.formula is not None and entry.length is not None:
                assert entry.formula.required_bytes <= entry.length, entry.key

    def test_every_temperature_is_given_in_degrees_celsius(self, base_table):
        for entry in base_table:
            if "temperature" in entry.name:
                assert entry.unit == "°C", entry.key
