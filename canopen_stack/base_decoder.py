"""
Base abstract class for application-specific CANopen device decoders.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from .types import CanopenMessage, NmtState


class BaseDeviceDecoder(ABC):
    """
    Abstract extension interface for decoding application-specific devices
    (e.g., SEVCON Gen4 inverter, De Haardt transponder, BMS, telemetry unit).
    """

    @property
    @abstractmethod
    def device_name(self) -> str:
        """Name of the device or application profile."""
        pass

    @property
    def supported_node_ids(self) -> List[int]:
        """List of default CANopen node IDs associated with this device."""
        return []

    @property
    def custom_cob_ids(self) -> Dict[int, str]:
        """
        Map of custom non-standard COB-IDs used by this device to their functional description.
        Example: {0x148: 'TPDO1', 0x473: 'TPDO5'}
        """
        return {}

    def can_decode(self, message: CanopenMessage) -> bool:
        """
        Determines whether this decoder should process the given CANopen message.
        By default, matches supported_node_ids or custom_cob_ids.
        """
        if message.arbitration_id in self.custom_cob_ids:
            return True
        if message.node_id is not None and message.node_id in self.supported_node_ids:
            return True
        return False

    @abstractmethod
    def decode(self, message: CanopenMessage) -> Optional[Dict[str, Any]]:
        """
        Decode raw payload bytes into high-level named physical signals.
        Must populate message.decoded_info and return a dict of extracted signals.

        :param message: The CANopen message to decode.
        :return: Dictionary of signal names to extracted values (or None).
        """
        pass

    def on_nmt_state_change(self, node_id: int, state: NmtState) -> None:
        """Optional callback invoked when a node changes its NMT state."""
        pass
