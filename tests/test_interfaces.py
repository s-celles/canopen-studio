"""
Unit tests for CAN interface configuration and Virtual CANopen Simulator.
"""

import time
import can
import can_interfaces
from can_interfaces import (
    SUPPORTED_INTERFACES,
    STANDARD_BITRATES,
    VirtualCanopenSimulator,
)


class TestInterfacesCatalog:
    def test_supported_interfaces_catalog(self):
        """Verify all essential market interfaces are registered in catalog."""
        expected = ["slcan", "pcan", "kvaser", "vector", "ixxat", "gs_usb", "socketcan", "virtual"]
        for iface in expected:
            assert iface in SUPPORTED_INTERFACES
            assert "name" in SUPPORTED_INTERFACES[iface]
            assert "backend" in SUPPORTED_INTERFACES[iface]
            assert "default_channels" in SUPPORTED_INTERFACES[iface]

    def test_standard_bitrates(self):
        """Verify standard CAN bitrates from 10 kbps to 1 Mbps are present."""
        assert 500000 in STANDARD_BITRATES
        assert 250000 in STANDARD_BITRATES
        assert 125000 in STANDARD_BITRATES
        assert 1000000 in STANDARD_BITRATES


class TestVirtualCanopenSimulator:
    def test_simulator_traffic_generation(self):
        """Test Virtual Simulator starts, emits heartbeats and TPDOs."""
        channel_name = "test_sim_traffic"
        bus = can.Bus(channel=channel_name, interface="virtual")
        sim = VirtualCanopenSimulator(channel_or_bus=channel_name)

        received_frames = []

        try:
            sim.start()
            # Collect frames for ~250ms
            start_time = time.time()
            while time.time() - start_time < 0.25:
                msg = bus.recv(timeout=0.05)
                if msg:
                    received_frames.append(msg)

            assert len(received_frames) > 0

            # Check that we received Node 1 Heartbeat (0x701) or SYNC (0x080) or TPDOs
            cob_ids = [m.arbitration_id for m in received_frames]
            assert (0x701 in cob_ids) or (0x080 in cob_ids) or (0x181 in cob_ids) or (0x148 in cob_ids)

        finally:
            sim.stop()
            bus.shutdown()

    def test_simulator_sdo_expedited_read(self):
        """Test Virtual Simulator responds to SDO read of Device Type (Index 0x1000)."""
        channel_name = "test_sim_sdo"
        bus = can.Bus(channel=channel_name, interface="virtual")
        sim = VirtualCanopenSimulator(channel_or_bus=channel_name)

        try:
            sim.start()
            # Send SDO Upload Request for Index 0x1000 Sub 0x00
            req = can.Message(
                arbitration_id=0x601,
                is_extended_id=False,
                data=[0x40, 0x00, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00],
            )
            bus.send(req)

            # Wait for response (TxSDO 0x581)
            resp = None
            for _ in range(15):
                msg = bus.recv(timeout=0.05)
                if msg and msg.arbitration_id == 0x581:
                    resp = msg
                    break

            assert resp is not None
            assert resp.data[1] == 0x00
            assert resp.data[2] == 0x10

        finally:
            sim.stop()
            bus.shutdown()


class TestUdpMulticastBus:
    """UDP multicast is the transport used to share a virtual CAN bus between machines."""

    def test_udp_multicast_in_catalog(self):
        """Verify the UDP multicast interface is registered with sane network defaults."""
        cfg = SUPPORTED_INTERFACES["udp_multicast"]
        assert cfg["backend"] == "udp_multicast"
        assert cfg["default_bitrate"] == 0
        assert "239.0.0.1" in cfg["default_channels"]

    def test_default_hop_limit_stays_on_local_segment(self, monkeypatch):
        """Without configuration the bus keeps python-can's link-local hop limit of 1."""
        captured = {}
        monkeypatch.setattr(can_interfaces.can, "Bus", lambda **kw: captured.update(kw))
        monkeypatch.delenv("CANOPEN_UDP_HOP_LIMIT", raising=False)

        can_interfaces.open_can_bus("udp_multicast", "239.0.0.1", 0)

        assert captured["hop_limit"] == 1

    def test_hop_limit_argument_crosses_routers(self, monkeypatch):
        """An explicit hop limit is forwarded so frames can reach other subnets."""
        captured = {}
        monkeypatch.setattr(can_interfaces.can, "Bus", lambda **kw: captured.update(kw))

        can_interfaces.open_can_bus("udp_multicast", "239.0.0.1", 0, hop_limit=8)

        assert captured["hop_limit"] == 8

    def test_hop_limit_from_environment(self, monkeypatch):
        """CANOPEN_UDP_HOP_LIMIT configures the hop limit without touching the code."""
        captured = {}
        monkeypatch.setattr(can_interfaces.can, "Bus", lambda **kw: captured.update(kw))
        monkeypatch.setenv("CANOPEN_UDP_HOP_LIMIT", "16")

        can_interfaces.open_can_bus("udp_multicast", "239.0.0.1", 0)

        assert captured["hop_limit"] == 16

    def test_explicit_argument_overrides_environment(self, monkeypatch):
        """An explicit argument wins over the environment variable."""
        captured = {}
        monkeypatch.setattr(can_interfaces.can, "Bus", lambda **kw: captured.update(kw))
        monkeypatch.setenv("CANOPEN_UDP_HOP_LIMIT", "16")

        can_interfaces.open_can_bus("udp_multicast", "239.0.0.1", 0, hop_limit=2)

        assert captured["hop_limit"] == 2

    def test_hop_limit_not_sent_to_other_backends(self, monkeypatch):
        """Serial and kernel backends must not receive a multicast-only keyword."""
        captured = {}
        monkeypatch.setattr(can_interfaces.can, "Bus", lambda **kw: captured.update(kw))

        can_interfaces.open_can_bus("slcan", "/dev/ttyUSB0", 500000, hop_limit=8)

        assert "hop_limit" not in captured
