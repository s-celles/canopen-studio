"""
Unit tests for the latency and jitter tracker module.
"""

import os
import time
import can
import pytest

from canopen_studio.latency import (
    DEFAULT_PING_REQ_ID,
    DEFAULT_PING_RESP_ID,
    DEFAULT_SYNC_ID,
    LatencyTracker,
)
from canopen_studio.interfaces import open_can_bus


class FakeBus:
    """Mock CAN bus for unit testing."""

    def __init__(self):
        self.sent = []

    def send(self, msg, timeout=None):
        self.sent.append(msg)


def test_latency_tracker_initial_state():
    tracker = LatencyTracker()
    stats = tracker.get_stats()
    assert stats["pings_sent"] == 0
    assert stats["pings_received"] == 0
    assert stats["rtt_last_ms"] is None
    assert stats["sync_jitter_last_ms"] is None
    assert stats["auto_echo"] is True


def test_send_ping():
    tracker = LatencyTracker()
    bus = FakeBus()
    seq = tracker.send_ping(bus)

    assert seq == 1
    assert tracker.pings_sent == 1
    assert len(bus.sent) == 1
    msg = bus.sent[0]
    assert msg.arbitration_id == DEFAULT_PING_REQ_ID
    assert len(msg.data) == 8
    assert int.from_bytes(msg.data[:4], "little") == 1


def test_auto_echo_responder():
    tracker = LatencyTracker(auto_echo=True)
    bus = FakeBus()

    req = can.Message(
        arbitration_id=DEFAULT_PING_REQ_ID,
        data=b"\x05\x00\x00\x00\x11\x22\x33\x44",
        is_extended_id=False,
    )
    result = tracker.process_message(req, bus)
    assert result is None
    assert len(bus.sent) == 1
    resp = bus.sent[0]
    assert resp.arbitration_id == DEFAULT_PING_RESP_ID
    assert resp.data == req.data


def test_auto_echo_disabled():
    tracker = LatencyTracker(auto_echo=False)
    bus = FakeBus()

    req = can.Message(
        arbitration_id=DEFAULT_PING_REQ_ID,
        data=b"\x05\x00\x00\x00\x11\x22\x33\x44",
        is_extended_id=False,
    )
    tracker.process_message(req, bus)
    assert len(bus.sent) == 0


def test_rtt_calculation():
    tracker = LatencyTracker()
    bus = FakeBus()
    seq = tracker.send_ping(bus)

    sent_msg = bus.sent[0]
    time.sleep(0.015)  # simulate ~15ms round trip

    resp = can.Message(
        arbitration_id=DEFAULT_PING_RESP_ID,
        data=sent_msg.data,
        is_extended_id=False,
    )
    rtt = tracker.process_message(resp, bus)
    assert rtt is not None
    assert rtt >= 10.0  # at least 10ms
    assert tracker.pings_received == 1

    stats = tracker.get_stats()
    assert stats["rtt_last_ms"] == pytest.approx(rtt, abs=0.1)
    assert stats["rtt_min_ms"] == stats["rtt_last_ms"]
    assert stats["rtt_max_ms"] == stats["rtt_last_ms"]
    assert stats["rtt_avg_ms"] == stats["rtt_last_ms"]
    assert stats["loss_rate_pct"] == 0.0


def test_sync_jitter_calculation():
    tracker = LatencyTracker(sync_nominal_ms=20.0)
    bus = FakeBus()

    sync1 = can.Message(arbitration_id=DEFAULT_SYNC_ID, data=b"", is_extended_id=False)
    tracker.process_message(sync1, bus)
    assert tracker.sync_interval_last_ms is None

    time.sleep(0.025)  # simulate 25ms interval (jitter ~5ms)
    sync2 = can.Message(arbitration_id=DEFAULT_SYNC_ID, data=b"", is_extended_id=False)
    tracker.process_message(sync2, bus)

    assert tracker.sync_interval_last_ms is not None
    assert tracker.sync_interval_last_ms >= 20.0
    assert tracker.sync_jitter_last_ms is not None
    assert tracker.sync_count == 2


def test_reset_metrics():
    tracker = LatencyTracker()
    bus = FakeBus()
    tracker.send_ping(bus)
    tracker.reset()

    stats = tracker.get_stats()
    assert stats["pings_sent"] == 0
    assert stats["pings_received"] == 0
    assert stats["rtt_last_ms"] is None


def test_interfaces_udp_multicast_port_parsing(monkeypatch):
    """Verify that open_can_bus supports IP:PORT and CANOPEN_UDP_PORT."""
    # Test IP:PORT in channel
    bus = open_can_bus("udp_multicast", "224.0.0.1:1750", 0)
    try:
        assert bus._multicast.port == 1750
        assert bus._multicast.group == "224.0.0.1"
    finally:
        bus.shutdown()

    # Test CANOPEN_UDP_PORT environment variable
    monkeypatch.setenv("CANOPEN_UDP_PORT", "1752")
    bus2 = open_can_bus("udp_multicast", "239.0.0.1", 0)
    try:
        assert bus2._multicast.port == 1752
        assert bus2._multicast.group == "239.0.0.1"
    finally:
        bus2.shutdown()


def test_mcp_latency_tools():
    from canopen_studio import mcp_server

    # Standalone mode: returns informative error
    mcp_server.set_app(None)
    stats = mcp_server.get_latency_stats()
    assert "error" in stats

    res = mcp_server.ping_bus()
    assert "error" in res


def test_auto_echo_ignores_own_ping():
    """Verify that auto-echo ignores our own ping request when it loops back."""
    tracker = LatencyTracker()
    bus = FakeBus()
    seq = tracker.send_ping(bus)
    assert len(bus.sent) == 1
    ping_req = bus.sent[0]

    # Process own ping request (as if looped back by socket)
    tracker.process_message(ping_req, bus)

    # Should NOT have sent an echo (bus.sent should still be 1)
    assert len(bus.sent) == 1
    # Ping should still be pending
    assert seq in tracker._pending_pings


def test_udp_bus_unicast_broadcast():
    """Verify open_can_bus creates UdpBus for unicast/broadcast addresses."""
    from canopen_studio.interfaces import UdpBus
    bus = open_can_bus("udp_multicast", "127.0.0.1:19999", 0)
    try:
        assert isinstance(bus, UdpBus)
        assert bus.dest_ip == "127.0.0.1"
        assert bus.port == 19999
        bus.send(can.Message(arbitration_id=0x123, data=[4, 5, 6]))
        rx = bus.recv(timeout=0.1)
        assert rx is not None
        assert rx.arbitration_id == 0x123
        assert list(rx.data) == [4, 5, 6]
    finally:
        bus.shutdown()

