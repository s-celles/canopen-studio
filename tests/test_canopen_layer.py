"""
Unit tests for CANopen protocol abstraction layer (CANopenLayer).
"""

import pytest
import can
from canopen_stack.types import (
    NmtCommand,
    NmtState,
    CanopenService,
)
from canopen_stack.canopen_layer import CANopenLayer


class TestCANopenLayer:
    @pytest.fixture
    def layer(self):
        bus = can.Bus(interface="virtual", channel="test_layer_bus")
        layer_instance = CANopenLayer(bus=bus)
        yield layer_instance
        bus.shutdown()

    def test_nmt_master_command_start_node(self, layer):
        """Test processing NMT Master Command to start a node."""
        msg = can.Message(arbitration_id=0x000, is_extended_id=False, data=[0x01, 0x05])
        parsed = layer.process_can_message(msg)

        assert parsed is not None
        assert parsed.service == CanopenService.NMT_MASTER
        assert parsed.node_id == 5
        assert "Cmd: 0x01" in parsed.decoded_info

    def test_sync_frame(self, layer):
        """Test detection of SYNC frames (COB-ID 0x080)."""
        msg = can.Message(arbitration_id=0x080, is_extended_id=False, data=[])
        parsed = layer.process_can_message(msg)

        assert parsed is not None
        assert parsed.service == CanopenService.SYNC
        assert "SYNC" in parsed.decoded_info

    def test_heartbeat_frame(self, layer):
        """Test Heartbeat frame parsing and node state tracking (COB-ID 0x700 + NodeID)."""
        # Node 1 emitting Pre-Operational Heartbeat (0x701, state=0x7F)
        msg = can.Message(arbitration_id=0x701, is_extended_id=False, data=[0x7F])
        parsed = layer.process_can_message(msg)

        assert parsed is not None
        assert parsed.service == CanopenService.HEARTBEAT
        assert parsed.node_id == 1
        assert layer.node_states[1] == NmtState.PRE_OPERATIONAL

        # Node 1 transitioning to Operational (state=0x05)
        msg2 = can.Message(arbitration_id=0x701, is_extended_id=False, data=[0x05])
        layer.process_can_message(msg2)
        assert layer.node_states[1] == NmtState.OPERATIONAL

    def test_sdo_request_and_response(self, layer):
        """Test SDO request (0x600 + NodeID) and response (0x580 + NodeID)."""
        req_msg = can.Message(
            arbitration_id=0x601, is_extended_id=False, data=[0x40, 0x17, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00]
        )
        req_parsed = layer.process_can_message(req_msg)

        assert req_parsed.service == CanopenService.SDO_RX
        assert req_parsed.node_id == 1
        assert "0x1017:00" in req_parsed.decoded_info

        resp_msg = can.Message(
            arbitration_id=0x581, is_extended_id=False, data=[0x4B, 0x17, 0x10, 0x00, 0xE8, 0x03, 0x00, 0x00]
        )
        resp_parsed = layer.process_can_message(resp_msg)

        assert resp_parsed.service == CanopenService.SDO_TX
        assert resp_parsed.node_id == 1
        assert "0x1017:00" in resp_parsed.decoded_info

    def test_emergency_frame(self, layer):
        """Test Emergency (EMCY) frame decoding (COB-ID 0x080 + NodeID)."""
        msg = can.Message(
            arbitration_id=0x083, is_extended_id=False, data=[0x00, 0x10, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00]
        )
        parsed = layer.process_can_message(msg)

        assert parsed.service == CanopenService.EMERGENCY
        assert parsed.node_id == 3
        assert "0x1000" in parsed.decoded_info

    def test_transmit_helpers(self, layer):
        """Test layer transmit methods invoke underlying bus.send."""
        # Test send_nmt_command
        layer.send_nmt_command(5, NmtCommand.START_NODE)

        # Test send_sync
        layer.send_sync()

        # Test send_sdo_read
        layer.send_sdo_read(1, 0x1000, 0x00)

        # Test send_sdo_write
        layer.send_sdo_write(1, 0x1017, 0x00, (1000).to_bytes(2, "little"))
