"""
Unit tests for the OBD-II tab's logic, without a display.

Following `tests/test_gui_status.py`, the methods under test are exercised unbound on a
lightweight stand-in. Only the parts that make decisions are tested here — which session
an adapter choice builds, and how a refused clear is reported — since the widget layout
is not something an assertion can usefully check.
"""

import types

import pytest

pytest.importorskip("tkinter", reason="GUI module requires the tkinter bindings")

from canopen_studio.diag import WRITE_ENABLED_ENV  # noqa: E402
from canopen_studio.diag.native import QueueFrameSource  # noqa: E402
from canopen_studio.gui import CanStudioApp  # noqa: E402


def make_app(**overrides):
    """A stand-in carrying only the attributes the methods under test read."""
    state = {
        "bus": None,
        "diag_session": None,
        "diag_client": None,
        "diag_match": None,
        "diag_frame_source": None,
        "diag_busy": False,
        "diag_poller": None,
        "diag_identity": None,
        "link": None,
    }
    state.update(overrides)
    return types.SimpleNamespace(**state)


class TestFrameForwarding:
    def test_frames_reach_a_running_native_session(self):
        source = QueueFrameSource()
        app = make_app(diag_frame_source=source)
        message = object()

        CanStudioApp._forward_to_diagnostics(app, message)

        assert source.recv(0.01) is message

    def test_forwarding_is_harmless_with_no_session(self):
        CanStudioApp._forward_to_diagnostics(make_app(), object())


class TestSessionTeardown:
    def test_closing_with_no_session_is_harmless(self):
        app = make_app()

        CanStudioApp._obd_close_session(app)

        assert app.diag_session is None

    def test_closing_releases_the_session(self):
        closed = []
        app = make_app(diag_session=types.SimpleNamespace(close=lambda: closed.append(True)))

        CanStudioApp._obd_close_session(app)

        assert closed == [True]

    def test_closing_clears_every_reference(self):
        app = make_app(
            diag_session=types.SimpleNamespace(close=lambda: None),
            diag_client=object(),
            diag_match=object(),
            diag_frame_source=QueueFrameSource(),
        )

        CanStudioApp._obd_close_session(app)

        assert (app.diag_session, app.diag_client, app.diag_match, app.diag_frame_source) == (None, None, None, None)

    def test_a_session_that_fails_to_close_still_ends(self):
        """A yanked adapter must not leave the tab believing it is connected."""

        def explode():
            raise OSError("gone")

        app = make_app(diag_session=types.SimpleNamespace(close=explode))

        CanStudioApp._obd_close_session(app)

        assert app.diag_session is None

    def test_an_elm327_session_is_left_for_the_link_to_close(self):
        """On an ELM327 the session is the link itself; only disconnecting closes it."""
        closed = []
        elm = types.SimpleNamespace(close=lambda: closed.append(True))
        app = make_app(diag_session=elm, link=types.SimpleNamespace(elm=elm))

        CanStudioApp._obd_close_session(app)

        assert closed == []
        assert app.diag_session is None

    def test_a_native_session_is_closed_with_the_link_still_up(self):
        closed = []
        session = types.SimpleNamespace(close=lambda: closed.append(True))
        app = make_app(diag_session=session, link=types.SimpleNamespace(elm=None))

        CanStudioApp._obd_close_session(app)

        assert closed == [True]

    def test_the_capture_loop_stops_feeding_a_closed_session(self):
        app = make_app(
            diag_session=types.SimpleNamespace(close=lambda: None),
            diag_frame_source=QueueFrameSource(),
        )

        CanStudioApp._obd_close_session(app)
        CanStudioApp._forward_to_diagnostics(app, object())

        assert app.diag_frame_source is None


class TestWriteNotice:
    def test_the_tab_says_it_is_read_only_by_default(self, monkeypatch):
        monkeypatch.delenv(WRITE_ENABLED_ENV, raising=False)

        notice = CanStudioApp._obd_write_notice(make_app())

        assert "Read-only" in notice
        assert WRITE_ENABLED_ENV in notice

    def test_the_tab_warns_about_the_cost_when_writes_are_on(self, monkeypatch):
        monkeypatch.setenv(WRITE_ENABLED_ENV, "1")

        notice = CanStudioApp._obd_write_notice(make_app())

        assert "readiness monitors" in notice


class TestReadinessRows:
    def rows(self, data):
        from canopen_studio.diag.j1979.readiness import decode_monitor_status

        return CanStudioApp.readiness_rows(decode_monitor_status(data, source=0x7E8))

    def test_a_ready_vehicle_says_so(self):
        headline, _ = self.rows(bytes([0x00, 0x07, 0x65, 0x00]))

        assert headline == "All monitors complete, lamp off."

    def test_a_vehicle_not_ready_names_the_lamp_and_the_monitors_that_have_not_run(self):
        headline, _ = self.rows(bytes([0x81, 0x07, 0x65, 0x04]))

        assert "lamp ON" in headline
        assert "Evaporative system" in headline

    def test_every_monitor_gets_a_row(self):
        _, rows = self.rows(bytes([0x00, 0x07, 0x65, 0x04]))

        assert ("Evaporative system", "✘ incomplete") in rows
        assert ("Secondary air system", "— not available") in rows

    def test_no_answer_suggests_the_ignition(self):
        headline, rows = CanStudioApp.readiness_rows(None)

        assert "ignition" in headline
        assert rows == []


class TestFreezeFrameRows:
    def test_no_frame_is_reported_as_no_snapshot(self):
        headline, rows = CanStudioApp.freeze_frame_rows([])

        assert "No freeze frame" in headline
        assert rows == []

    def test_a_frame_names_its_code_and_lists_its_values(self):
        from canopen_studio.diag.j1979.client import FreezeFrame
        from canopen_studio.diag.j1979.pids import PidDefinition

        rpm = PidDefinition.from_mapping(
            "01:0C", {"name": "engine_speed", "formula": "(256 * A + B) / 4", "unit": "rpm", "bytes": 2}
        )
        frame = FreezeFrame(source=0x7E8, dtc="P0143", values=[rpm.decode(bytes([0x1A, 0xF8]), source=0x7E8)])

        headline, rows = CanStudioApp.freeze_frame_rows([frame])

        assert "P0143" in headline
        assert rows == [("0x7E8", "engine_speed", "1726.00", "rpm")]


class TestLiveStatus:
    def stats(self, **fields):
        from canopen_studio.diag.j1979.polling import PollStats

        return PollStats(**fields)

    def test_the_rate_and_the_refresh_interval_are_shown(self):
        text = CanStudioApp.live_status_text(self.stats(requests=10, elapsed=4.0, recent_cycle_seconds=2.0))

        assert text == "Live — 2.5 requests/s, each value refreshed every 2.0 s"

    def test_failures_and_the_recording_are_mentioned(self):
        text = CanStudioApp.live_status_text(self.stats(requests=4, elapsed=2.0, errors=1), recorded_rows=12)

        assert "1 failed" in text
        assert "12 row(s) recorded" in text


class TestValueFormatting:
    def test_a_number_is_shown_compactly(self):
        assert CanStudioApp.format_reading(1726.0) == "1726"

    def test_raw_bytes_are_shown_as_spaced_hex(self):
        assert CanStudioApp.format_reading(b"\x1a\xf8") == "1A F8"

    def test_anything_else_is_shown_as_text(self):
        assert CanStudioApp.format_reading("EOBD") == "EOBD"


class TestConnectionChain:
    """Two links to watch: studio to adapter, and adapter to vehicle or bus."""

    def test_nothing_connected_shows_both_segments_off(self):
        adapter, near, far = CanStudioApp.link_chain(None, "off", "unknown")

        assert near == ("Studio", "off", "not connected")
        assert far[1] == "off"

    def test_an_elm327_names_its_transport(self):
        _, near, _ = CanStudioApp.link_chain("elm327_ble", "ok", "unknown")

        assert near == ("Studio", "ok", "Bluetooth LE")

    def test_an_adapter_reached_but_a_vehicle_not_yet_queried(self):
        adapter, _, far = CanStudioApp.link_chain("elm327_serial", "ok", "unknown")

        assert adapter == "ELM327"
        assert far == ("Vehicle", "off", "not queried yet")

    def test_a_vehicle_that_does_not_answer_is_shown_apart_from_the_adapter(self):
        """The case a single 'Connected' hides: adapter fine, ignition off."""
        _, near, far = CanStudioApp.link_chain("elm327_ble", "ok", "error", "no answer — is the ignition on?")

        assert near[1] == "ok"
        assert far == ("Vehicle", "error", "no answer — is the ignition on?")

    def test_an_identified_vehicle_shows_what_answered(self):
        _, _, far = CanStudioApp.link_chain("elm327_tcp", "ok", "ok", CanStudioApp.vehicle_summary("6", 2))

        assert far == ("Vehicle", "ok", "protocol 6 · 2 ECUs")

    def test_a_link_still_opening_leaves_the_far_side_unknown(self):
        _, near, far = CanStudioApp.link_chain("elm327_ble", "connecting", "unknown")

        assert near[1] == "pending"
        assert far[1] == "off"

    def test_a_can_link_shows_whether_frames_arrive(self):
        adapter, _, waiting = CanStudioApp.link_chain("slcan", "ok", "unknown")
        _, _, flowing = CanStudioApp.link_chain("slcan", "ok", "ok")

        assert adapter == "slcan"
        assert waiting == ("CAN bus", "pending", "waiting for frames")
        assert flowing == ("CAN bus", "ok", "frames flowing")

    def test_one_ecu_is_not_plural(self):
        assert CanStudioApp.vehicle_summary(None, 1) == "1 ECU"
