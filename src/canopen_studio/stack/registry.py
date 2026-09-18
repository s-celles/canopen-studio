"""
Plugin registry and extension manager for application-specific CANopen decoders.
"""

from typing import List, Type, Optional
from .base_decoder import BaseDeviceDecoder
from .types import CanopenMessage


class DecoderRegistry:
    """Registry managing active application decoders."""

    def __init__(self):
        self._decoders: List[BaseDeviceDecoder] = []
        self.active_profile: str = "All / Auto"

    def register(self, decoder: BaseDeviceDecoder) -> None:
        """Register an instantiated device decoder."""
        if decoder not in self._decoders:
            self._decoders.append(decoder)

    def unregister(self, decoder: BaseDeviceDecoder) -> None:
        if decoder in self._decoders:
            self._decoders.remove(decoder)

    @property
    def decoders(self) -> List[BaseDeviceDecoder]:
        return list(self._decoders)

    def lookup(self, cob_id: int) -> Optional[BaseDeviceDecoder]:
        """Find a decoder that has registered a custom description for this COB-ID."""
        for decoder in self._decoders:
            if cob_id in decoder.custom_cob_ids:
                return decoder
        return None

    def dispatch_and_decode(self, message: CanopenMessage) -> CanopenMessage:
        """
        Passes a CANopen message through matching application decoders.
        Populates message.signals and message.decoded_info.
        """
        if self.active_profile == "Raw CAN (No Decoders)":
            return message

        for decoder in self._decoders:
            if self.active_profile != "All / Auto" and decoder.device_name != self.active_profile:
                continue
            if decoder.can_decode(message):
                extracted = decoder.decode(message)
                if extracted:
                    message.signals.update(extracted)

        return message


# Global default registry
_default_registry = DecoderRegistry()


def get_default_registry() -> DecoderRegistry:
    return _default_registry


def register_decoder(decoder_cls: Type[BaseDeviceDecoder]):
    """Decorator to automatically instantiate and register an application decoder."""
    instance = decoder_cls()
    _default_registry.register(instance)
    return decoder_cls
