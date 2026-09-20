"""
Unit tests for the Torque CSV and DBC profile importers.

Both share one rule: a definition that would decode to a plausible wrong number is
skipped with a reason rather than imported. An imported PID that lies is worse than one
that is missing, because nothing downstream can tell the difference.

Fixtures are written into tmp_path rather than committed, since the repository's
gitignore excludes CSV files.
"""

import io
import textwrap

import pytest

from canopen_studio.diag.j1979.pids import PidError
from canopen_studio.diag.profiles.importers.torque_csv import (
    ImportReport,
    import_torque_csv,
    normalise_name,
    parse_mode_and_pid,
    translate_equation,
)
from canopen_studio.diag.profiles.model import BASE_PROFILE_ID, ProfileError

TORQUE_CSV = """\
Name,ShortName,ModeAndPID,Equation,Min Value,Max Value,Units,Header
Engine Coolant Temp,CoolantTemp,0105,A-40,-40,215,°C,7E0
Engine RPM,RPM,010C,((A*256)+B)/4,0,16383,rpm,7E0
Battery Temperature,BattTemp,2101,(A*256+B)/10-40,-40,120,°C,7E0
"""


def csv_stream(text=TORQUE_CSV):
    return io.StringIO(textwrap.dedent(text))


class TestEquationTranslation:
    def test_an_ordinary_equation_transfers_verbatim(self):
        """Torque already addresses bytes as A, B, C, which is our notation too."""
        assert translate_equation("((A*256)+B)/4") == "((A*256)+B)/4"

    def test_bit_extraction_is_translated(self):
        assert translate_equation("{A:3}") == "bit(A, 3)"

    def test_bit_extraction_inside_a_larger_equation_is_translated(self):
        assert translate_equation("{B:0}*100") == "bit(B, 0)*100"

    def test_spacing_inside_the_bit_syntax_is_tolerated(self):
        assert translate_equation("{ A : 3 }") == "bit(A, 3)"

    def test_surrounding_space_is_stripped(self):
        assert translate_equation("  A-40  ") == "A-40"


class TestModeAndPidParsing:
    def test_a_standard_mode_and_pid_is_read(self):
        assert parse_mode_and_pid("0105") == (0x01, 0x05)

    def test_a_manufacturer_mode_is_read(self):
        assert parse_mode_and_pid("2101") == (0x21, 0x01)

    def test_a_wide_pid_is_kept_whole(self):
        """Mode 0x22 addresses parameters with a two-byte identifier."""
        assert parse_mode_and_pid("221E1B") == (0x22, 0x1E1B)

    def test_lowercase_is_accepted(self):
        assert parse_mode_and_pid("010c") == (0x01, 0x0C)

    def test_a_hex_prefix_is_tolerated(self):
        assert parse_mode_and_pid("0x0105") == (0x01, 0x05)

    def test_something_too_short_is_refused(self):
        with pytest.raises(PidError):
            parse_mode_and_pid("01")

    def test_something_not_hexadecimal_is_refused(self):
        with pytest.raises(PidError):
            parse_mode_and_pid("01ZZ")


class TestNameNormalisation:
    def test_a_display_name_becomes_a_key(self):
        assert normalise_name("Engine Coolant Temp", "fallback") == "engine_coolant_temp"

    def test_punctuation_is_replaced(self):
        assert normalise_name("O2 Sensor #1 (B1)", "fallback") == "o2_sensor_1_b1"

    def test_a_name_starting_with_a_digit_is_prefixed(self):
        assert normalise_name("2nd Gear", "fallback").startswith("pid_")

    def test_an_empty_name_falls_back(self):
        assert normalise_name("   ", "pid_01_0c") == "pid_01_0c"


class TestTorqueImport:
    def test_every_usable_row_is_imported(self):
        report = import_torque_csv(csv_stream(), "my_car")

        assert report.imported == 3
        assert report.ok is True

    def test_the_profile_carries_the_pids(self):
        profile = import_torque_csv(csv_stream(), "my_car").profile

        assert profile.table.get(0x01, 0x05).name == "coolanttemp"
        assert profile.table.get(0x01, 0x0C) is not None
        assert profile.table.get(0x21, 0x01) is not None

    def test_an_imported_pid_decodes(self):
        profile = import_torque_csv(csv_stream(), "my_car").profile

        assert profile.table.get(0x01, 0x0C).decode(bytes([0x1A, 0xF8])).value == pytest.approx(1726.0)

    def test_units_and_bounds_are_carried_over(self):
        definition = import_torque_csv(csv_stream(), "my_car").profile.table.get(0x01, 0x05)

        assert definition.unit == "°C"
        assert (definition.minimum, definition.maximum) == (-40, 215)

    def test_the_display_name_becomes_the_description(self):
        definition = import_torque_csv(csv_stream(), "my_car").profile.table.get(0x01, 0x05)

        assert definition.description == "Engine Coolant Temp"

    def test_the_profile_inherits_from_j1979_by_default(self):
        """So the legislated parameters stay available alongside the imported ones."""
        assert import_torque_csv(csv_stream(), "my_car").profile.extends == BASE_PROFILE_ID

    def test_the_parent_can_be_chosen(self):
        report = import_torque_csv(csv_stream(), "my_car", extends="example_make")

        assert report.profile.extends == "example_make"

    def test_the_display_name_can_be_set(self):
        assert import_torque_csv(csv_stream(), "my_car", name="My Car").profile.name == "My Car"

    def test_a_file_can_be_imported_by_path(self, tmp_path):
        path = tmp_path / "torque.csv"
        path.write_text(TORQUE_CSV, encoding="utf-8")

        assert import_torque_csv(path, "my_car").imported == 3

    def test_a_byte_order_mark_is_tolerated(self, tmp_path):
        """Files exported on Windows carry one, and it would corrupt the first heading."""
        path = tmp_path / "torque.csv"
        path.write_text("﻿" + TORQUE_CSV, encoding="utf-8")

        assert import_torque_csv(path, "my_car").imported == 3

    def test_a_missing_file_is_reported(self, tmp_path):
        with pytest.raises(ProfileError):
            import_torque_csv(tmp_path / "nope.csv", "my_car")


class TestTorqueImportRobustness:
    def test_a_file_without_the_expected_columns_is_refused(self):
        with pytest.raises(ProfileError) as excinfo:
            import_torque_csv(csv_stream("Alpha,Beta\n1,2\n"), "my_car")

        assert "Torque" in str(excinfo.value)

    def test_alternative_column_headings_are_recognised(self):
        report = import_torque_csv(csv_stream("Name,PID,Formula,Units\nSpeed,010D,A,km/h\n"), "my_car")

        assert report.imported == 1

    def test_a_row_with_an_unparsable_equation_is_skipped_with_a_reason(self):
        """An imported PID that decodes to nonsense is worse than a missing one."""
        text = TORQUE_CSV + "Evil,Evil,0199,__import__('os'),0,1,,7E0\n"

        report = import_torque_csv(csv_stream(text), "my_car")

        assert report.imported == 3
        assert any("row 5" in message for message in report.skipped)

    def test_a_row_with_a_bad_pid_is_skipped_with_a_reason(self):
        text = TORQUE_CSV + "Broken,Broken,ZZ,A,0,1,,7E0\n"

        report = import_torque_csv(csv_stream(text), "my_car")

        assert any("ZZ" in message for message in report.skipped)

    def test_a_row_with_no_equation_is_skipped(self):
        text = TORQUE_CSV + "Empty,Empty,0188,,0,1,,7E0\n"

        report = import_torque_csv(csv_stream(text), "my_car")

        assert any("no equation" in message for message in report.skipped)

    def test_a_blank_row_is_ignored_silently(self):
        report = import_torque_csv(csv_stream(TORQUE_CSV + ",,,,,,,\n"), "my_car")

        assert report.imported == 3
        assert len(report.skipped) == 0

    def test_unparsable_bounds_are_dropped_rather_than_failing_the_row(self):
        text = "Name,ShortName,ModeAndPID,Equation,Min Value,Max Value,Units,Header\nX,X,010D,A,n/a,n/a,km/h,7E0\n"

        definition = import_torque_csv(csv_stream(text), "my_car").profile.table.get(0x01, 0x0D)

        assert definition.minimum is None

    def test_an_empty_file_imports_nothing(self):
        report = import_torque_csv(csv_stream("Name,ModeAndPID,Equation\n"), "my_car")

        assert report.ok is False

    def test_a_report_renders_a_summary(self):
        report = import_torque_csv(csv_stream(TORQUE_CSV + "Broken,Broken,ZZ,A,,,,\n"), "my_car")

        assert str(report) == "3 PID(s) imported, 1 skipped"

    def test_a_report_serialises_for_an_agent(self):
        payload = import_torque_csv(csv_stream(), "my_car").as_dict()

        assert payload["profile"] == "my_car"
        assert payload["imported"] == 3


class TestImportReport:
    def test_an_empty_report_is_not_ok(self):
        assert ImportReport().ok is False


cantools = pytest.importorskip("cantools", reason="the DBC importer needs the optional cantools dependency")

from canopen_studio.diag.profiles.importers.dbc import (  # noqa: E402
    import_dbc,
    is_diagnostic_response_id,
    signal_formula,
)

# A DBC modelling an OBD-II response as a multiplexed message, which is the one shape
# the two formats genuinely share. The nesting is the real one: the service selects
# which PID signal applies, and the PID selects which parameter applies. Byte 0 is the
# ISO-TP length, so the data begins at byte 3 — which the importer derives rather than
# assumes.
OBD_DBC = """\
VERSION ""

NS_ :

BS_:

BU_: Tester

BO_ 2024 OBD2: 8 Vector__XXX
 SG_ S1_PID_0C_EngineRPM m12 : 31|16@0+ (0.25,0) [0|16383.75] "rpm" Vector__XXX
 SG_ S1_PID_05_CoolantTemp m5 : 31|8@0+ (1,-40) [-40|215] "degC" Vector__XXX
 SG_ ParameterID_Service01 m1M : 23|8@0+ (1,0) [0|255] "" Vector__XXX
 SG_ Service M : 15|8@0+ (1,0) [0|255] "" Vector__XXX
 SG_ Length : 7|8@0+ (1,0) [0|255] "" Vector__XXX

SG_MUL_VAL_ 2024 S1_PID_0C_EngineRPM ParameterID_Service01 12-12;
SG_MUL_VAL_ 2024 S1_PID_05_CoolantTemp ParameterID_Service01 5-5;
SG_MUL_VAL_ 2024 ParameterID_Service01 Service 1-1;
"""

# An ordinary broadcast database, which describes signals nobody can request as a PID.
BROADCAST_DBC = """\
VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 256 WheelSpeeds: 8 ECU
 SG_ FrontLeft : 7|16@0+ (0.01,0) [0|655] "km/h" Vector__XXX

"""


def write_dbc(tmp_path, text, name="test.dbc"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


class TestDiagnosticIdentifiers:
    def test_the_standard_response_range_is_recognised(self):
        assert is_diagnostic_response_id(0x7E8) is True
        assert is_diagnostic_response_id(0x7EF) is True

    def test_a_request_identifier_is_not_a_response(self):
        assert is_diagnostic_response_id(0x7E0) is False

    def test_an_ordinary_broadcast_identifier_is_not_a_response(self):
        assert is_diagnostic_response_id(0x100) is False

    def test_the_extended_response_range_is_recognised(self):
        assert is_diagnostic_response_id(0x18DAF110) is True


class TestSignalFormula:
    def test_a_single_byte_signal_reads_one_letter(self):
        assert signal_formula(0, 8, scale=1, offset=0, is_signed=False) == "A"

    def test_an_offset_is_applied(self):
        assert signal_formula(0, 8, scale=1, offset=-40, is_signed=False) == "A - 40"

    def test_a_scale_is_applied(self):
        assert signal_formula(0, 8, scale=0.5, offset=0, is_signed=False) == "A * 0.5"

    def test_a_two_byte_signal_is_assembled_big_endian(self):
        assert signal_formula(0, 16, scale=1, offset=0, is_signed=False) == "(A * 256 + B)"

    def test_a_signed_signal_is_reinterpreted(self):
        assert signal_formula(0, 16, scale=1, offset=0, is_signed=True) == "signed((A * 256 + B), 16)"

    def test_a_later_start_byte_shifts_the_letters(self):
        assert signal_formula(2, 8, scale=1, offset=0, is_signed=False) == "C"

    def test_a_bit_packed_signal_is_refused(self):
        """An approximate formula would decode to a plausible wrong number."""
        assert signal_formula(0, 12, scale=1, offset=0, is_signed=False) is None

    def test_a_zero_length_signal_is_refused(self):
        assert signal_formula(0, 0, scale=1, offset=0, is_signed=False) is None

    def test_a_signal_before_the_data_bytes_is_refused(self):
        assert signal_formula(-1, 8, scale=1, offset=0, is_signed=False) is None


class TestDbcImport:
    def test_multiplexed_diagnostic_signals_become_pids(self, tmp_path):
        report = import_dbc(write_dbc(tmp_path, OBD_DBC), "from_dbc")

        assert report.imported == 2

    def test_the_multiplexer_value_becomes_the_pid(self, tmp_path):
        profile = import_dbc(write_dbc(tmp_path, OBD_DBC), "from_dbc").profile

        assert profile.table.get(0x01, 0x0C) is not None
        assert profile.table.get(0x01, 0x05) is not None

    def test_an_imported_pid_decodes(self, tmp_path):
        profile = import_dbc(write_dbc(tmp_path, OBD_DBC), "from_dbc").profile

        assert profile.table.get(0x01, 0x0C).decode(bytes([0x1A, 0xF8])).value == pytest.approx(1726.0)

    def test_an_offset_signal_decodes(self, tmp_path):
        profile = import_dbc(write_dbc(tmp_path, OBD_DBC), "from_dbc").profile

        assert profile.table.get(0x01, 0x05).decode(bytes([0x7B])).value == 83

    def test_the_unit_is_carried_over(self, tmp_path):
        profile = import_dbc(write_dbc(tmp_path, OBD_DBC), "from_dbc").profile

        assert profile.table.get(0x01, 0x0C).unit == "rpm"

    def test_the_service_comes_from_the_multiplexer_chain(self, tmp_path):
        """The database says which service its PIDs belong to; nothing is assumed."""
        profile = import_dbc(write_dbc(tmp_path, OBD_DBC), "from_dbc", mode=0x99).profile

        assert profile.table.get(0x01, 0x0C) is not None
        assert profile.table.get(0x99, 0x0C) is None

    def test_the_first_data_byte_is_derived_from_the_pid_echo(self, tmp_path):
        """This database models the ISO-TP length byte; one that does not also works."""
        profile = import_dbc(write_dbc(tmp_path, OBD_DBC), "from_dbc").profile

        assert profile.table.get(0x01, 0x0C).formula.variables == frozenset({"A", "B"})

    def test_the_profile_inherits_from_j1979_by_default(self, tmp_path):
        report = import_dbc(write_dbc(tmp_path, OBD_DBC), "from_dbc")

        assert report.profile.extends == BASE_PROFILE_ID

    def test_a_broadcast_database_imports_nothing_and_says_why(self, tmp_path):
        """The honest outcome: a DBC of broadcast signals has no PIDs in it."""
        report = import_dbc(write_dbc(tmp_path, BROADCAST_DBC), "from_dbc")

        assert report.imported == 0
        assert any("broadcast rather than requested" in message for message in report.skipped)

    def test_a_missing_file_is_reported(self, tmp_path):
        with pytest.raises(ProfileError):
            import_dbc(tmp_path / "nope.dbc", "from_dbc")

    def test_a_file_that_is_not_a_database_is_reported(self, tmp_path):
        path = tmp_path / "junk.dbc"
        path.write_text("this is not a DBC at all", encoding="utf-8")

        with pytest.raises(ProfileError):
            import_dbc(path, "from_dbc")
