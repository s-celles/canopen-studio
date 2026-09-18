"""
Unit tests for the MCP server tools, in both standalone and GUI-integrated modes.
"""

import pytest

import canopen_studio.mcp_server as mcp_server


class FakeBus:
    """Minimal python-can Bus stand-in recording everything that is sent."""

    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


class FakeApp:
    """Stands in for CanStudioApp when the MCP server runs integrated in the GUI."""

    def __init__(self):
        self.bus = FakeBus()
        self.canopen_layer = None
        self.stats = {"total_rx": 0, "total_tx": 0}

    def get_status_dict(self):
        return {"connected": True, "interface": "udp_multicast", "stats": dict(self.stats)}


@pytest.fixture
def app(monkeypatch):
    """Register a fake GUI app as the MCP state provider, then restore the previous one."""
    fake = FakeApp()
    monkeypatch.setattr(mcp_server, "_app_ref", fake)
    return fake


def _call(tool, **kwargs):
    """Invoke an MCP tool through its underlying Python function."""
    return getattr(tool, "fn", tool)(**kwargs)


class TestTransmitCounter:
    """Frames sent through MCP must be counted like frames sent from the GUI console."""

    def test_send_frame_increments_total_tx(self, app):
        _call(mcp_server.send_frame, can_id=0x123, data=[1, 2, 3])
        assert app.stats["total_tx"] == 1

    def test_send_sync_increments_total_tx(self, app):
        _call(mcp_server.send_sync)
        assert app.stats["total_tx"] == 1

    def test_send_nmt_increments_total_tx(self, app):
        _call(mcp_server.send_nmt, command="start", node_id=0)
        assert app.stats["total_tx"] == 1

    def test_counter_accumulates_across_sends(self, app):
        _call(mcp_server.send_frame, can_id=0x100, data=[0])
        _call(mcp_server.send_sync)
        _call(mcp_server.send_nmt, command="stop", node_id=3)
        assert app.stats["total_tx"] == 3

    def test_failed_send_does_not_count(self, app):
        """A rejected payload must not inflate the transmit counter."""
        result = _call(mcp_server.send_frame, can_id=0x100, data=[0] * 9)
        assert "too long" in result.lower()
        assert app.stats["total_tx"] == 0

    def test_standalone_mode_counts_without_app(self, monkeypatch):
        """Standalone mode keeps its own counter so get_status stays meaningful."""
        monkeypatch.setattr(mcp_server, "_app_ref", None)
        monkeypatch.setattr(mcp_server, "_standalone_bus", FakeBus())
        monkeypatch.setattr(mcp_server, "_standalone_tx_count", 0)

        _call(mcp_server.send_frame, can_id=0x080, data=[])

        assert _call(mcp_server.get_status)["stats"]["total_tx"] == 1


class TestConnectForwardsHopLimit:
    """The hop limit must reach the bus factory so remote subnets can be targeted."""

    def test_hop_limit_forwarded_to_the_app(self, app, monkeypatch):
        captured = {}
        app.connect_from_mcp = lambda *a, **kw: captured.update(kw) or "Connected"
        app.bus = None

        _call(
            mcp_server.connect,
            interface="udp_multicast",
            channel="239.0.0.1",
            bitrate=0,
            simulate=False,
            hop_limit=8,
        )

        assert captured["hop_limit"] == 8

    def test_hop_limit_defaults_to_none(self, app):
        """Omitting it leaves the environment or built-in default in charge."""
        captured = {}
        app.connect_from_mcp = lambda *a, **kw: captured.update(kw) or "Connected"

        _call(mcp_server.connect)

        assert captured["hop_limit"] is None


class TestStatusDelegation:
    def test_status_comes_from_the_app_when_integrated(self, app):
        assert _call(mcp_server.get_status)["interface"] == "udp_multicast"


class TestBridgeTools:
    """Bridging is exposed to MCP clients so a real bus can be shared on demand."""

    def test_bridge_start_delegates_to_the_app(self, app):
        captured = {}
        app.start_bridge = lambda channel, **kw: captured.update(kw, channel=channel) or "Bridging"

        _call(mcp_server.bridge_start, channel="239.0.0.5", hop_limit=4, allow_inject=True)

        assert captured == {"channel": "239.0.0.5", "hop_limit": 4, "allow_inject": True}

    def test_bridge_start_defaults_to_read_only(self, app):
        captured = {}
        app.start_bridge = lambda channel, **kw: captured.update(kw) or "Bridging"

        _call(mcp_server.bridge_start)

        assert captured["allow_inject"] is False

    def test_bridge_stop_delegates_to_the_app(self, app):
        app.stop_bridge = lambda: "Bridge stopped."

        assert _call(mcp_server.bridge_stop) == "Bridge stopped."

    def test_bridging_needs_the_gui(self, monkeypatch):
        """Standalone mode has no capture loop to feed the bridge."""
        monkeypatch.setattr(mcp_server, "_app_ref", None)

        assert "gui" in _call(mcp_server.bridge_start).lower()
