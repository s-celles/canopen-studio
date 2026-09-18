"""
Unit tests for the bridge wiring inside the GUI application.

The methods under test are exercised unbound on a lightweight stand-in, so no Tk window
and no display are required.
"""

import types
import can
import pytest

pytest.importorskip("tkinter", reason="GUI module requires the tkinter bindings")

from canopen_studio.gui import CanStudioApp  # noqa: E402


class RecordingBridge:
    def __init__(self):
        self.forwarded = []
        self.stopped = False

    def forward(self, msg):
        self.forwarded.append(msg)
        return True

    def stop(self):
        self.stopped = True

    def get_status(self):
        return {"running": not self.stopped, "channel": "239.0.0.1"}


def make_app(**overrides):
    state = {
        "instance_name": "",
        "bus": None,
        "simulator": None,
        "bridge": None,
        "active_interface": "",
        "active_channel": "",
        "active_bitrate": 0,
        "stats": {"total_rx": 0, "total_tx": 0},
    }
    state.update(overrides)
    return types.SimpleNamespace(**state)


class TestBridgeInStatus:
    def test_no_bridge_key_when_inactive(self):
        assert CanStudioApp.get_status_dict(make_app())["bridge"] is None

    def test_bridge_status_is_exposed(self):
        app = make_app(bus=object(), active_interface="pcan", bridge=RecordingBridge())

        bridge_status = CanStudioApp.get_status_dict(app)["bridge"]

        assert bridge_status["running"] is True
        assert bridge_status["channel"] == "239.0.0.1"


class TestBridgeForwarding:
    """Every captured frame must be handed to the bridge, and only when one is armed."""

    def test_frame_is_forwarded_when_a_bridge_is_armed(self):
        bridge = RecordingBridge()
        app = make_app(bridge=bridge)
        msg = can.Message(arbitration_id=0x181, data=b"\x27\x00", is_extended_id=False)

        CanStudioApp._forward_to_bridge(app, msg)

        assert bridge.forwarded == [msg]

    def test_no_bridge_is_a_no_op(self):
        app = make_app()

        CanStudioApp._forward_to_bridge(app, can.Message(arbitration_id=0x181))

    def test_bridge_failure_never_breaks_the_capture_loop(self):
        """A broken bridge must not stop the analyzer from decoding the real bus."""

        def boom(msg):
            raise OSError("bridge down")

        app = make_app(bridge=types.SimpleNamespace(forward=boom))

        CanStudioApp._forward_to_bridge(app, can.Message(arbitration_id=0x181))


class FakeNetworkBus:
    def __init__(self):
        self.sent = []

    def send(self, msg, timeout=None):
        self.sent.append(msg)

    def recv(self, timeout=None):
        return None

    def shutdown(self):
        pass


@pytest.fixture
def opened(monkeypatch):
    """Capture the arguments the bridge uses to open its network bus."""
    calls = []

    def fake_open(interface, channel, bitrate, hop_limit=None):
        calls.append({"interface": interface, "channel": channel, "hop_limit": hop_limit})
        return FakeNetworkBus()

    monkeypatch.setattr("canopen_studio.gui.open_can_bus", fake_open)
    return calls


class TestStartBridge:
    def test_bridge_opens_a_multicast_bus_and_arms(self, opened):
        app = make_app(bus=object(), active_interface="pcan")

        result = CanStudioApp.start_bridge(app, "239.0.0.1")

        assert opened[0]["interface"] == "udp_multicast"
        assert opened[0]["channel"] == "239.0.0.1"
        assert app.bridge.running is True
        assert "239.0.0.1" in result

    def test_hop_limit_is_forwarded(self, opened):
        app = make_app(bus=object(), active_interface="pcan")

        CanStudioApp.start_bridge(app, "239.0.0.1", hop_limit=4)

        assert opened[0]["hop_limit"] == 4

    def test_injection_stays_off_unless_requested(self, opened):
        app = make_app(bus=object(), active_interface="pcan")

        CanStudioApp.start_bridge(app, "239.0.0.1")

        assert app.bridge.allow_inject is False

    def test_injection_can_be_enabled(self, opened):
        app = make_app(bus=object(), active_interface="pcan")

        CanStudioApp.start_bridge(app, "239.0.0.1", allow_inject=True)

        assert app.bridge.allow_inject is True

    def test_refuses_when_not_connected(self, opened):
        app = make_app()

        result = CanStudioApp.start_bridge(app, "239.0.0.1")

        assert "not connected" in result.lower()
        assert app.bridge is None

    def test_refuses_a_second_bridge(self, opened):
        app = make_app(bus=object(), active_interface="pcan")
        CanStudioApp.start_bridge(app, "239.0.0.1")
        first = app.bridge

        result = CanStudioApp.start_bridge(app, "239.0.0.2")

        assert "already" in result.lower()
        assert app.bridge is first

    def test_refuses_to_mirror_a_multicast_bus_onto_itself(self, opened):
        """Capturing and mirroring the same group would loop frames forever."""
        app = make_app(bus=object(), active_interface="udp_multicast", active_channel="239.0.0.1")

        result = CanStudioApp.start_bridge(app, "239.0.0.1")

        assert "itself" in result.lower()
        assert app.bridge is None

    def test_allows_mirroring_to_a_different_group(self, opened):
        app = make_app(bus=object(), active_interface="udp_multicast", active_channel="239.0.0.1")

        CanStudioApp.start_bridge(app, "239.0.0.2")

        assert app.bridge is not None


class TestStopBridge:
    def test_stop_disarms_and_clears(self, opened):
        app = make_app(bus=object(), active_interface="pcan")
        CanStudioApp.start_bridge(app, "239.0.0.1")

        result = CanStudioApp.stop_bridge(app)

        assert app.bridge is None
        assert "stopped" in result.lower()

    def test_stop_without_a_bridge_is_reported(self, opened):
        assert "no bridge" in CanStudioApp.stop_bridge(make_app()).lower()
