"""
Unit tests for the GUI status snapshot exposed to the MCP and A2A servers.

The methods under test are pure state readers, so they are exercised unbound on a
lightweight stand-in rather than on a real Tk window.
"""

import types
import pytest

pytest.importorskip("tkinter", reason="GUI module requires the tkinter bindings")

from can_gui import CanStudioApp  # noqa: E402


def make_app(**overrides):
    """Build a stand-in carrying only the attributes get_status_dict reads."""
    state = {
        "instance_name": "",
        "bus": None,
        "simulator": None,
        "active_interface": "",
        "active_channel": "",
        "active_bitrate": 0,
        "stats": {"total_rx": 0, "total_tx": 0},
    }
    state.update(overrides)
    return types.SimpleNamespace(**state)


class TestStatusReportsActiveConnection:
    def test_connected_interface_is_the_active_one(self):
        """The reported interface must be the bus in use, not the combobox selection."""
        app = make_app(bus=object(), active_interface="udp_multicast", active_channel="239.0.0.1")

        status = CanStudioApp.get_status_dict(app)

        assert status["connected"] is True
        assert status["interface"] == "udp_multicast"

    def test_active_channel_and_bitrate_are_reported(self):
        """Remote clients need the channel to join the same bus."""
        app = make_app(
            bus=object(),
            active_interface="udp_multicast",
            active_channel="239.0.0.1",
            active_bitrate=0,
        )

        status = CanStudioApp.get_status_dict(app)

        assert status["channel"] == "239.0.0.1"
        assert status["bitrate"] == 0

    def test_simulator_flag_follows_simulator_state(self):
        app = make_app(bus=object(), active_interface="virtual", simulator=object())

        assert CanStudioApp.get_status_dict(app)["simulate"] is True

    def test_disconnected_reports_no_active_interface(self):
        """Once disconnected, no stale interface is advertised."""
        app = make_app()

        status = CanStudioApp.get_status_dict(app)

        assert status["connected"] is False
        assert status["interface"] == "unknown"

    def test_instance_name_defaults_when_unnamed(self):
        assert CanStudioApp.get_status_dict(make_app())["instance"] == "default"
