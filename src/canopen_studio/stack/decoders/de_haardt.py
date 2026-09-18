"""
Application-specific decoder extension for De Haardt Kart Safety Transponders (Xtra.CAN).
"""

from typing import Optional, Dict, Any
from ..base_decoder import BaseDeviceDecoder
from ..types import CanopenMessage
from ..registry import register_decoder


@register_decoder
class DeHaardtTransponderDecoder(BaseDeviceDecoder):
    """
    Decoder for De Haardt kart speed regulation and safety transponder.
    Interprets speed limits, pit-in/pit-out, and emergency stop states.
    """

    SPEED_MODES = {
        0: "Emergency Stop",
        1: "Pit Lane Speed (Very Slow)",
        2: "Slow Speed (Yellow Flag)",
        3: "Normal Speed (Green Flag)",
        4: "Boost / Fast Mode",
    }

    @property
    def device_name(self) -> str:
        return "De Haardt Safety Transponder"

    @property
    def custom_cob_ids(self) -> Dict[int, str]:
        return {
            0x270: "De Haardt Speed & Safety Command (RPDO1 to SEVCON)",
        }

    def decode(self, message: CanopenMessage) -> Optional[Dict[str, Any]]:
        cid = message.arbitration_id
        data = message.data
        signals: Dict[str, Any] = {}

        if cid == 0x270 and len(data) >= 2:
            mode_code = data[0]
            limit_val = data[1]
            mode_name = self.SPEED_MODES.get(mode_code, f"Mode {mode_code}")
            signals["de_haardt_mode_code"] = mode_code
            signals["de_haardt_mode_name"] = mode_name
            signals["de_haardt_limit_val"] = limit_val

            # Enhance existing message info with transponder interpretation
            message.decoded_info += f" | Transponder: {mode_name}"
            return signals

        return None
