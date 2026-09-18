"""
Generic CANopen CiA 402 Device Profile Decoder (Drives and Motion Control).
Supports standard motion controllers, AC servo drives, and inverters.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from typing import Optional, Dict, Any, List
from ..base_decoder import BaseDeviceDecoder
from ..types import CanopenMessage
from ..registry import register_decoder


@register_decoder
class CiA402GenericDecoder(BaseDeviceDecoder):
    """
    Standard CiA 402 decoder for motion drives and inverters.
    Decodes standard Statusword (0x6041), Controlword (0x6040),
    Velocity Actual Value (0x606C), and Position Actual Value (0x6064).
    """

    # CiA 402 Drive State Machine bitmask
    DRIVE_STATES = {
        0x0000: "Not Ready to Switch On",
        0x0040: "Switch On Disabled",
        0x0021: "Ready to Switch On",
        0x0023: "Switched On",
        0x0027: "Operation Enabled",
        0x0007: "Quick Stop Active",
        0x000F: "Fault Reaction Active",
        0x0008: "Fault",
    }

    @property
    def device_name(self) -> str:
        return "CiA 402 Generic Drive"

    @property
    def supported_node_ids(self) -> List[int]:
        # Matches any node ID 1 to 127 when using standard CiA 402 PDOs
        return list(range(1, 128))

    def can_decode(self, message: CanopenMessage) -> bool:
        # Standard CiA 301 / 402 TPDO1 (0x181..0x1FF) or TPDO2 (0x281..0x2FF) or TPDO3 (0x381..0x3FF)
        cid = message.arbitration_id
        if 0x181 <= cid <= 0x1FF:
            return True
        if 0x281 <= cid <= 0x2FF:
            return True
        if 0x381 <= cid <= 0x3FF:
            return True
        if 0x201 <= cid <= 0x27F:
            return True
        return False

    def decode(self, message: CanopenMessage) -> Optional[Dict[str, Any]]:
        cid = message.arbitration_id
        data = message.data
        signals: Dict[str, Any] = {}

        # 1. TPDO1 (0x180 + NodeID) -> Typically Statusword (0x6041)
        if 0x181 <= cid <= 0x1FF and len(data) >= 2:
            node_id = cid - 0x180
            status_word = int.from_bytes(data[0:2], "little", signed=False)
            signals["status_word"] = status_word
            state_str = self._parse_status_word(status_word)
            signals["cia402_state"] = state_str

            if len(data) >= 6:
                # Often TPDO1 has Statusword (2B) + Velocity Actual Value (4B)
                velocity = int.from_bytes(data[2:6], "little", signed=True)
                signals["velocity_actual"] = velocity
                message.decoded_info = f"CiA 402 Node {node_id} TPDO1 -> State: {state_str} | Velocity: {velocity}"
            else:
                message.decoded_info = f"CiA 402 Node {node_id} TPDO1 -> Status: 0x{status_word:04X} ({state_str})"
            return signals

        # 2. RPDO1 (0x200 + NodeID) -> Typically Controlword (0x6040)
        if 0x201 <= cid <= 0x27F and len(data) >= 2:
            node_id = cid - 0x200
            ctrl_word = int.from_bytes(data[0:2], "little", signed=False)
            signals["control_word"] = ctrl_word
            message.decoded_info = f"CiA 402 Node {node_id} RPDO1 -> Controlword: 0x{ctrl_word:04X}"
            return signals

        # 3. TPDO2 (0x280 + NodeID) -> Typically Statusword + Position / Velocity
        if 0x281 <= cid <= 0x2FF and len(data) >= 6:
            node_id = cid - 0x280
            val1 = int.from_bytes(data[0:2], "little", signed=False)
            val2 = int.from_bytes(data[2:6], "little", signed=True)
            signals["tpdo2_val1"] = val1
            signals["tpdo2_val2"] = val2
            message.decoded_info = f"CiA 402 Node {node_id} TPDO2 -> 0x{val1:04X}, Value: {val2}"
            return signals

        return None

    def _parse_status_word(self, sw: int) -> str:
        """Evaluate bits 0..3, 5, 6 of Statusword to determine CiA 402 drive state."""
        # Mask bits: 0: Ready to switch on, 1: Switched on, 2: Operation enabled, 3: Fault, 5: Quick stop, 6: Switch on disabled
        masked = sw & 0x006F
        if (masked & 0x004F) == 0x0000:
            return "Not Ready to Switch On"
        if (masked & 0x004F) == 0x0040:
            return "Switch On Disabled"
        if (masked & 0x006F) == 0x0021:
            return "Ready to Switch On"
        if (masked & 0x006F) == 0x0023:
            return "Switched On"
        if (masked & 0x006F) == 0x0027:
            return "Operation Enabled"
        if (masked & 0x006F) == 0x0007:
            return "Quick Stop Active"
        if (masked & 0x004F) == 0x000F:
            return "Fault Reaction Active"
        if (masked & 0x004F) == 0x0008:
            return "Fault"
        return f"Status 0x{sw:04X}"
