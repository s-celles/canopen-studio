"""
Unit tests for emissions readiness decoding (PID 01 and PID 41).

Bytes C and D name different monitors on a spark-ignition and a compression-ignition
engine, which is the reason this is decoded in code rather than by a bit table.
"""

import pytest

from canopen_studio.diag.j1979.readiness import (
    PID_MONITOR_STATUS,
    PID_MONITOR_THIS_CYCLE,
    combine,
    decode_monitor_status,
)

# Lamp on with one code; misfire, fuel and components available, misfire incomplete;
# catalyst, evaporative, oxygen sensor and heater available, evaporative incomplete.
SPARK = bytes([0x81, 0x17, 0x65, 0x04])

# Compression ignition: NOx aftertreatment and PM filter available, PM filter incomplete.
DIESEL = bytes([0x00, 0x0F, 0x42, 0x40])


def status(readiness):
    return {m.name: m.status for m in readiness.monitors}


class TestSparkIgnition:
    def test_the_lamp_and_the_code_count_come_from_byte_a(self):
        readiness = decode_monitor_status(SPARK)

        assert readiness.mil_on is True
        assert readiness.dtc_count == 1

    def test_the_ignition_type_is_read_from_bit_b3(self):
        assert decode_monitor_status(SPARK).ignition == "spark"

    def test_the_common_monitors_report_availability_and_completion(self):
        monitors = status(decode_monitor_status(SPARK))

        assert monitors["misfire"] == "incomplete"
        assert monitors["fuel_system"] == "complete"
        assert monitors["components"] == "complete"

    def test_bytes_c_and_d_name_the_spark_monitors(self):
        monitors = status(decode_monitor_status(SPARK))

        assert monitors["catalyst"] == "complete"
        assert monitors["evaporative_system"] == "incomplete"
        assert monitors["oxygen_sensor"] == "complete"
        assert monitors["secondary_air"] == "not_available"

    def test_a_lit_lamp_or_an_incomplete_monitor_is_not_all_complete(self):
        readiness = decode_monitor_status(SPARK)

        assert [m.name for m in readiness.incomplete] == ["misfire", "evaporative_system"]
        assert readiness.all_complete is False

    def test_lamp_off_and_every_available_monitor_run_is_all_complete(self):
        assert decode_monitor_status(bytes([0x00, 0x07, 0x65, 0x00])).all_complete is True

    def test_an_unavailable_monitor_never_counts_as_incomplete(self):
        """Some ECUs set the incomplete bit of a monitor they do not have."""
        assert decode_monitor_status(bytes([0x00, 0x00, 0x00, 0xFF])).incomplete == []


class TestCompressionIgnition:
    def test_bit_b3_switches_to_the_diesel_monitors(self):
        readiness = decode_monitor_status(DIESEL)

        assert readiness.ignition == "compression"
        assert "catalyst" not in status(readiness)

    def test_bytes_c_and_d_name_the_diesel_monitors(self):
        monitors = status(decode_monitor_status(DIESEL))

        assert monitors["nox_aftertreatment"] == "complete"
        assert monitors["pm_filter"] == "incomplete"
        assert monitors["boost_pressure"] == "not_available"

    def test_reserved_bits_define_no_monitor(self):
        assert len(decode_monitor_status(DIESEL).monitors) == 3 + 6

    def test_the_ignition_type_can_be_forced(self):
        assert decode_monitor_status(SPARK, compression=True).ignition == "compression"


class TestThisDriveCycle:
    def test_pid_41_has_no_lamp_or_code_count(self):
        readiness = decode_monitor_status(SPARK, pid=PID_MONITOR_THIS_CYCLE)

        assert readiness.scope == "this_drive_cycle"
        assert readiness.mil_on is None
        assert readiness.dtc_count is None

    def test_pid_01_covers_the_time_since_codes_were_cleared(self):
        assert decode_monitor_status(SPARK, pid=PID_MONITOR_STATUS).scope == "since_codes_cleared"


class TestRefusals:
    def test_a_short_reply_is_refused(self):
        with pytest.raises(ValueError):
            decode_monitor_status(b"\x00\x07")

    def test_another_pid_is_refused(self):
        with pytest.raises(ValueError):
            decode_monitor_status(SPARK, pid=0x0C)


class TestCombining:
    def test_the_lamp_is_on_if_any_ecu_commands_it(self):
        combined = combine([decode_monitor_status(SPARK, source=0x7E8), decode_monitor_status(bytes(4), source=0x7E9)])

        assert combined.mil_on is True
        assert combined.sources == [0x7E8, 0x7E9]

    def test_a_monitor_is_available_if_any_ecu_implements_it(self):
        engine = decode_monitor_status(bytes([0, 0x07, 0x01, 0x00]), source=0x7E8)
        other = decode_monitor_status(bytes([0, 0x00, 0x04, 0x00]), source=0x7E9)

        monitors = status(combine([engine, other]))

        assert monitors["catalyst"] == "complete"
        assert monitors["evaporative_system"] == "complete"

    def test_a_monitor_is_complete_only_if_every_ecu_implementing_it_says_so(self):
        done = decode_monitor_status(bytes([0, 0x01, 0, 0]), source=0x7E8)
        pending = decode_monitor_status(bytes([0, 0x11, 0, 0]), source=0x7E9)

        assert status(combine([done, pending]))["misfire"] == "incomplete"

    def test_an_ecu_without_the_monitor_does_not_mark_it_incomplete(self):
        done = decode_monitor_status(bytes([0, 0x01, 0, 0]), source=0x7E8)
        absent = decode_monitor_status(bytes([0, 0x10, 0, 0]), source=0x7E9)

        assert status(combine([done, absent]))["misfire"] == "complete"

    def test_the_code_counts_add_up(self):
        combined = combine(
            [decode_monitor_status(bytes([0x82, 0, 0, 0])), decode_monitor_status(bytes([0x01, 0, 0, 0]))]
        )

        assert combined.dtc_count == 3

    def test_nothing_to_combine_is_none(self):
        assert combine([]) is None

    def test_the_result_serialises_for_an_agent(self):
        payload = decode_monitor_status(SPARK, source=0x7E8).as_dict()

        assert payload["incomplete"] == ["misfire", "evaporative_system"]
        assert payload["monitors"]["catalyst"] == "complete"
        assert payload["ecus"] == ["0x7E8"]
