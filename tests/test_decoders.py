"""
Unit tests for CANopen & vehicle protocol decoders (Sevcon Gen4, CiA 402, De Haardt, J1939).
"""

import pytest
from canopen_stack.registry import DecoderRegistry, get_default_registry
from canopen_stack.decoders.sevcon_gen4 import SevconGen4Decoder
from canopen_stack.decoders.cia402_generic import CiA402GenericDecoder
from canopen_stack.decoders.de_haardt import DeHaardtTransponderDecoder
from canopen_stack.decoders.j1939_extended import J1939ExtendedDecoder
from canopen_stack.types import CanopenMessage, CanopenService


def make_canopen_msg(cid: int, data: bytes, is_extended: bool = False) -> CanopenMessage:
    return CanopenMessage(
        arbitration_id=cid,
        is_extended=is_extended,
        dlc=len(data),
        data=bytes(data),
        timestamp=0.0,
        service=CanopenService.EXTENDED_J1939 if is_extended else CanopenService.TPDO,
    )


class TestDecoderRegistry:
    def test_default_registry_contains_standard_decoders(self):
        """Verify the global registry contains all built-in decoders."""
        registry = get_default_registry()
        names = [d.device_name for d in registry.decoders]

        assert "CiA 402 Generic Drive" in names
        assert "SEVCON Gen4 Inverter" in names
        assert "De Haardt Safety Transponder" in names
        assert "J1939 Extended (29-bit)" in names

    def test_custom_registration(self):
        """Test registering and retrieving a custom decoder in a clean registry."""
        reg = DecoderRegistry()
        decoder = SevconGen4Decoder()
        reg.register(decoder)

        assert len(reg.decoders) == 1
        assert reg.lookup(0x148) is decoder


class TestSevconGen4Decoder:
    @pytest.fixture
    def decoder(self):
        return SevconGen4Decoder()

    def test_sevcon_tpdo1_inverter_status(self, decoder):
        """Test decoding TPDO1 (0x148)."""
        msg = make_canopen_msg(0x148, bytes([10, 0, 20, 0, 30, 0, 40, 0]))
        assert decoder.can_decode(msg) is True
        signals = decoder.decode(msg)

        assert signals["inverter_status_v1"] == 10
        assert signals["inverter_status_v2"] == 20
        assert signals["inverter_status_v3"] == 30
        assert signals["inverter_status_v4"] == 40

    def test_sevcon_tpdo3_temperatures(self, decoder):
        """Test decoding TPDO3 (0x156) temperatures."""
        # Heatsink temp = 45 °C at bytes[6:8], Motor temp raw = 200 at bytes[2:4]
        data = bytearray(8)
        data[2:4] = (200).to_bytes(2, "little", signed=True)
        data[6:8] = (45).to_bytes(2, "little", signed=True)
        msg = make_canopen_msg(0x156, data)

        signals = decoder.decode(msg)
        assert signals["heatsink_temp_c"] == 45
        assert signals["motor_temp_raw"] == 200

    def test_sevcon_tpdo4_torque_and_speed_limit(self, decoder):
        """Test decoding TPDO4 (0x270) control mode, speed step and torque."""
        data = bytearray([0x03, 0x05, 0x00, 0x00, 0x00, 0x78, 0x00])  # torque = 120 (0x0078)
        msg = make_canopen_msg(0x270, data)

        signals = decoder.decode(msg)
        assert signals["control_mode"] == 3
        assert signals["speed_limit_step"] == 5
        assert signals["target_torque"] == 120

    def test_sevcon_tpdo5_actual_speed_rpm(self, decoder):
        """Test decoding TPDO5 (0x473) actual motor RPM."""
        data = bytearray(8)
        data[0:4] = (4500).to_bytes(4, "little", signed=True)  # max speed
        data[4:8] = (2450).to_bytes(4, "little", signed=True)  # actual speed
        msg = make_canopen_msg(0x473, data)

        signals = decoder.decode(msg)
        assert signals["max_speed_rpm"] == 4500
        assert signals["actual_speed_rpm"] == 2450

    def test_cannot_decode_unrelated_id(self, decoder):
        """Decoder should refuse non-matching IDs."""
        msg = make_canopen_msg(0x705, bytes([0x05]))
        assert decoder.can_decode(msg) is False


class TestCiA402GenericDecoder:
    @pytest.fixture
    def decoder(self):
        return CiA402GenericDecoder()

    def test_cia402_tpdo1_statusword_states(self, decoder):
        """Test parsing CiA 402 Statusword states."""
        # Statusword 0x0027 -> Operation Enabled
        msg1 = make_canopen_msg(0x181, (0x0027).to_bytes(2, "little"))
        signals1 = decoder.decode(msg1)
        assert signals1["cia402_state"] == "Operation Enabled"

        # Statusword 0x0040 -> Switch On Disabled
        msg2 = make_canopen_msg(0x181, (0x0040).to_bytes(2, "little"))
        signals2 = decoder.decode(msg2)
        assert signals2["cia402_state"] == "Switch On Disabled"

    def test_cia402_tpdo1_with_velocity(self, decoder):
        """Test parsing CiA 402 TPDO1 containing Statusword + Velocity."""
        data = (0x0027).to_bytes(2, "little") + (1850).to_bytes(4, "little", signed=True)
        msg = make_canopen_msg(0x181, data)

        signals = decoder.decode(msg)
        assert signals["velocity_actual"] == 1850

    def test_cia402_rpdo1_controlword(self, decoder):
        """Test parsing CiA 402 RPDO1 containing Controlword."""
        data = (0x000F).to_bytes(2, "little")
        msg = make_canopen_msg(0x201, data)

        signals = decoder.decode(msg)
        assert signals["control_word"] == 0x000F


class TestDeHaardtTransponderDecoder:
    @pytest.fixture
    def decoder(self):
        return DeHaardtTransponderDecoder()

    def test_speed_modes(self, decoder):
        """Test parsing safety speed modes emitted by De Haardt track transponder."""
        msg0 = make_canopen_msg(0x270, bytes([0, 10]))
        signals0 = decoder.decode(msg0)
        assert signals0["de_haardt_mode_name"] == "Emergency Stop"

        msg2 = make_canopen_msg(0x270, bytes([2, 50]))
        signals2 = decoder.decode(msg2)
        assert signals2["de_haardt_mode_name"] == "Slow Speed (Yellow Flag)"

        msg3 = make_canopen_msg(0x270, bytes([3, 100]))
        signals3 = decoder.decode(msg3)
        assert signals3["de_haardt_mode_name"] == "Normal Speed (Green Flag)"


class TestJ1939ExtendedDecoder:
    @pytest.fixture
    def decoder(self):
        return J1939ExtendedDecoder()

    def test_j1939_header_parsing(self, decoder):
        """Test extracting 29-bit CAN ID fields (Priority, PGN, Source Address)."""
        # CAN ID: 0x18FEEE00 (PGN 0xFEEE = 65262, Priority 6, SA 0x00)
        can_id = 0x18FEEE00
        msg = make_canopen_msg(can_id, bytes(8), is_extended=True)

        assert decoder.can_decode(msg) is True
        signals = decoder.decode(msg)

        assert signals["priority"] == 6
        assert signals["pgn"] == 0xFEEE
        assert signals["source_address"] == 0x00
