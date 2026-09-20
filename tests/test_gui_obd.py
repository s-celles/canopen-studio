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

from canopen_studio.diag import DiagnosticError, WRITE_ENABLED_ENV  # noqa: E402
from canopen_studio.diag.elm327.interface import ElmDiagnosticInterface  # noqa: E402
from canopen_studio.diag.elm327.transport import (  # noqa: E402
    DEFAULT_BAUDRATE,
    DEFAULT_TCP_PORT,
    SerialElmTransport,
    TcpElmTransport,
)
from canopen_studio.diag.native import NativeCanDiagnosticInterface, QueueFrameSource  # noqa: E402
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
    }
    state.update(overrides)
    return types.SimpleNamespace(**state)


def build(app, kind, port="/dev/ttyUSB0", rate="", protocol="0"):
    return CanStudioApp.build_diagnostic_session(app, kind, port, rate, protocol)


class TestSerialAdapter:
    def test_a_serial_choice_builds_an_elm327_session(self):
        session, source = build(make_app(), "elm327", port="/dev/ttyUSB0")

        assert isinstance(session, ElmDiagnosticInterface)
        assert isinstance(session.transport, SerialElmTransport)
        assert source is None

    def test_the_port_is_carried_through(self):
        session, _ = build(make_app(), "elm327", port="/dev/rfcomm0")

        assert session.transport.port == "/dev/rfcomm0"

    def test_a_blank_rate_falls_back_to_the_usual_one(self):
        session, _ = build(make_app(), "elm327", rate="")

        assert session.transport.baudrate == DEFAULT_BAUDRATE

    def test_a_given_rate_is_used(self):
        session, _ = build(make_app(), "elm327", rate="9600")

        assert session.transport.baudrate == 9600

    def test_a_non_numeric_rate_is_refused(self):
        with pytest.raises(ValueError):
            build(make_app(), "elm327", rate="fast")

    def test_the_protocol_choice_is_carried_through(self):
        session, _ = build(make_app(), "elm327", protocol="6")

        assert session.protocol_setting == "6"


class TestTcpAdapter:
    def test_a_wifi_choice_builds_a_tcp_session(self):
        session, source = build(make_app(), "elm327_tcp", port="192.168.0.10")

        assert isinstance(session.transport, TcpElmTransport)
        assert source is None

    def test_the_host_is_carried_through(self):
        session, _ = build(make_app(), "elm327_tcp", port="10.0.0.5")

        assert session.transport.host == "10.0.0.5"

    def test_a_blank_port_falls_back_to_the_convention(self):
        session, _ = build(make_app(), "elm327_tcp", rate="")

        assert session.transport.port == DEFAULT_TCP_PORT

    def test_a_given_port_is_used(self):
        session, _ = build(make_app(), "elm327_tcp", rate="35001")

        assert session.transport.port == 35001


class TestNativeAdapter:
    def test_a_native_choice_borrows_the_connected_bus(self):
        bus = object()
        session, source = build(make_app(bus=bus), "native")

        assert isinstance(session, NativeCanDiagnosticInterface)
        assert session.bus is bus

    def test_the_borrowed_bus_is_not_owned(self):
        """Closing the diagnostic session must not shut down the studio's bus."""
        session, _ = build(make_app(bus=object()), "native")

        assert session.owns_bus is False

    def test_a_native_session_takes_frames_from_a_queue(self):
        """The capture loop owns the only reader, so it feeds the session instead."""
        _, source = build(make_app(bus=object()), "native")

        assert isinstance(source, QueueFrameSource)

    def test_a_native_choice_without_a_bus_explains_itself(self):
        with pytest.raises(DiagnosticError) as excinfo:
            build(make_app(bus=None), "native")

        assert "connect one first" in str(excinfo.value)

    def test_an_unknown_adapter_is_refused(self):
        with pytest.raises(DiagnosticError):
            build(make_app(), "smoke signals")


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
