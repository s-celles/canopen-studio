"""
Decoder extension for 29-bit Extended CAN frames (J1939 / ISO 11783).
"""

from typing import Optional, Dict, Any
from ..base_decoder import BaseDeviceDecoder
from ..types import CanopenMessage
from ..registry import register_decoder


@register_decoder
class J1939ExtendedDecoder(BaseDeviceDecoder):
    """
    Decodes 29-bit extended frames into J1939 Priority, PGN, Source Address,
    and Destination Address.
    """

    @property
    def device_name(self) -> str:
        return "J1939 Extended (29-bit)"

    def can_decode(self, message: CanopenMessage) -> bool:
        return message.is_extended

    def decode(self, message: CanopenMessage) -> Optional[Dict[str, Any]]:
        can_id = message.arbitration_id
        priority = (can_id >> 26) & 0x07
        dp = (can_id >> 24) & 0x01
        pf = (can_id >> 16) & 0xFF
        ps = (can_id >> 8) & 0xFF
        sa = can_id & 0xFF

        signals: Dict[str, Any] = {
            "priority": priority,
            "source_address": sa,
        }

        if pf < 240:
            da = ps
            pgn = (dp << 16) | (pf << 8)
            signals["destination_address"] = da
            signals["pgn"] = pgn
            message.decoded_info = f"[J1939] Prio:{priority} PGN:0x{pgn:04X} SA:0x{sa:02X} -> DA:0x{da:02X}"
        else:
            pgn = (dp << 16) | (pf << 8) | ps
            signals["pgn"] = pgn
            message.decoded_info = f"[J1939] Prio:{priority} PGN:0x{pgn:05X} SA:0x{sa:02X} (Broadcast)"

        return signals
