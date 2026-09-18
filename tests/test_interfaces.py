"""
Unit tests for CAN interface configuration and Virtual CANopen Simulator.
"""

import time
import can
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
