"""
Unit tests for continuous PID polling and CSV recording.

The poller runs against the same scripted interface as the J1979 client tests, with a
fake clock, so rates and timestamps are exact rather than timing-dependent.
"""

import csv

import pytest
from test_diag_j1979_client import TABLE, ScriptedInterface

from canopen_studio.diag import DiagnosticTimeout, TransportError
from canopen_studio.diag.j1979.client import J1979Client
from canopen_studio.diag.j1979.polling import CsvRecorder, PidPoller, format_value, summarise

RPM = {b"\x01\x0c": [(0x7E8, bytes([0x41, 0x0C, 0x1A, 0xF8]))]}
COOLANT = {b"\x01\x05": [(0x7E8, bytes([0x41, 0x05, 0x7B]))]}


class Clock:
    """Advances a fixed step every time it is read."""

    def __init__(self, step=0.25):
        self.now = 0.0
        self.step = step

    def __call__(self):
        self.now += self.step
        return self.now


class RaisingInterface(ScriptedInterface):
    """A scripted interface that raises for chosen requests."""

    def __init__(self, script, failures):
        super().__init__(script)
        self.failures = failures

    def _request(self, payload, timeout):
        if payload in self.failures:
            self.requests.append(payload)
            raise self.failures[payload]
        return super()._request(payload, timeout)


def poller(script=None, pids=(0x0C, 0x05), interface=None, **kwargs):
    client = J1979Client(interface or ScriptedInterface({**RPM, **COOLANT, **(script or {})}), table=TABLE)
    return PidPoller(client, pids, clock=kwargs.pop("clock", Clock()), wallclock=lambda: 1_700_000_000.0, **kwargs)


class TestOneCycle:
    def test_every_pid_is_asked_once_per_cycle(self):
        p = poller()

        samples = p.poll_cycle()

        assert [s.reading.name for s in samples] == ["engine_speed", "coolant_temperature"]
        assert p.client.interface.requests == [b"\x01\x0c", b"\x01\x05"]

    def test_the_latest_value_is_kept_per_pid_and_ecu(self):
        p = poller()
        p.poll_cycle()

        assert p.latest[(0x0C, 0x7E8)].reading.value == pytest.approx(1726.0)

    def test_every_answering_ecu_gives_a_sample(self):
        two = {b"\x01\x0c": [(0x7E8, bytes([0x41, 0x0C, 0x1A, 0xF8])), (0x7E9, bytes([0x41, 0x0C, 0x00, 0x00]))]}
        p = poller(two, pids=[0x0C])

        assert [s.source for s in p.poll_cycle()] == [0x7E8, 0x7E9]

    def test_samples_are_stamped_with_time_since_the_start(self):
        samples = poller().poll_cycle()

        assert samples[0].elapsed < samples[1].elapsed

    def test_a_pid_nobody_answers_is_asked_but_yields_nothing(self):
        p = poller(pids=[0x0C, 0x0D])

        samples = p.poll_cycle()

        assert len(samples) == 1
        assert p.stats.requests == 2
        assert p.stats.answered == 1

    def test_nothing_to_poll_is_refused(self):
        with pytest.raises(ValueError):
            PidPoller(object(), [])


class TestFailures:
    def test_a_failing_pid_is_counted_and_the_cycle_goes_on(self):
        interface = RaisingInterface({**RPM, **COOLANT}, {b"\x01\x0c": DiagnosticTimeout("slow")})
        p = poller(interface=interface)

        samples = p.poll_cycle()

        assert [s.reading.name for s in samples] == ["coolant_temperature"]
        assert p.stats.errors == 1

    def test_a_lost_link_ends_the_run_and_is_kept(self):
        interface = RaisingInterface({**RPM}, {b"\x01\x05": TransportError("adapter gone")})
        p = poller(interface=interface)

        p.run(max_cycles=5)

        assert isinstance(p.error, TransportError)
        assert p.stats.cycles == 0


class TestRate:
    def test_the_rate_is_measured_not_assumed(self):
        p = poller(clock=Clock(step=0.25))

        p.run(max_cycles=4)

        assert p.stats.cycles == 4
        assert p.stats.requests == 8
        assert p.stats.requests_per_second > 0
        assert p.stats.recent_cycle_seconds > 0

    def test_a_slow_first_request_does_not_understate_the_rate(self):
        """The first request can carry the adapter's protocol search, seconds long."""
        calls = [0]
        now = [0.0]

        def clock():
            # The fifth reading is taken as the first answer arrives, five seconds late.
            calls[0] += 1
            now[0] += 5.0 if calls[0] == 5 else 0.1
            return now[0]

        p = poller(pids=[0x0C], clock=clock)
        p.run(max_cycles=15)

        assert p.stats.requests_per_second > p.stats.average_requests_per_second

    def test_a_run_stops_after_its_duration(self):
        p = poller(clock=Clock(step=0.5))

        p.run(duration=3.0)

        assert 0 < p.stats.cycles < 10

    def test_the_statistics_serialise(self):
        p = poller()
        p.run(max_cycles=2)

        payload = p.stats.as_dict()

        assert payload["cycles"] == 2
        assert payload["refresh_interval_s"] is not None


class TestCallbacks:
    def test_each_cycle_is_reported_with_its_samples(self):
        seen = []
        p = poller(on_cycle=lambda samples, stats: seen.append((len(samples), stats.cycles)))

        p.run(max_cycles=3)

        assert seen == [(2, 1), (2, 2), (2, 3)]


class TestBackground:
    def test_a_background_run_stops_on_request(self):
        import time

        p = poller(clock=time.monotonic)
        p.start()
        time.sleep(0.05)

        p.stop()

        assert p.running is False
        assert p.stats.cycles > 0


class TestRecording:
    def test_every_sample_becomes_one_row(self, tmp_path):
        path = tmp_path / "log.csv"
        with CsvRecorder(path) as recorder:
            poller(recorder=recorder).run(max_cycles=3)

        rows = list(csv.DictReader(path.open()))

        assert len(rows) == 6
        assert rows[0]["name"] == "engine_speed"
        assert rows[0]["ecu"] == "0x7E8"
        assert rows[0]["pid"] == "01:0C"
        assert float(rows[0]["value"]) == pytest.approx(1726.0)
        assert rows[0]["unit"] == "rpm"

    def test_timestamps_are_utc_with_milliseconds(self, tmp_path):
        path = tmp_path / "log.csv"
        with CsvRecorder(path) as recorder:
            poller(recorder=recorder).run(max_cycles=1)

        first = next(csv.DictReader(path.open()))

        assert first["timestamp"] == "2023-11-14T22:13:20.000+00:00"

    def test_stopping_the_poller_closes_the_recording(self, tmp_path):
        recorder = CsvRecorder(tmp_path / "log.csv")
        p = poller(recorder=recorder)
        p.run(max_cycles=1)

        p.stop()

        assert recorder._file.closed


class TestFormatting:
    def test_a_bit_field_is_written_as_json(self):
        assert format_value({"b": True, "a": False}) == '{"a": false, "b": true}'

    def test_raw_bytes_are_written_as_hex(self):
        assert format_value(b"\x1a\xf8") == "1AF8"

    def test_a_flag_is_written_as_one_or_zero(self):
        assert format_value(True) == "1"


class TestSummary:
    def test_numbers_are_condensed_per_parameter_and_ecu(self):
        p = poller()
        samples = [s for _ in range(3) for s in p.poll_cycle()]

        summary = summarise(samples)

        speed = summary["engine_speed@0x7E8"]
        assert speed["count"] == 3
        assert speed["min"] == speed["max"] == pytest.approx(1726.0)
        assert speed["unit"] == "rpm"
