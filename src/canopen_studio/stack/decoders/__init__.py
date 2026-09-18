"""
Application decoders package. Automatically registers active decoders.
"""

from .sevcon_gen4 import SevconGen4Decoder
from .de_haardt import DeHaardtTransponderDecoder
from .j1939_extended import J1939ExtendedDecoder
from .cia402_generic import CiA402GenericDecoder

__all__ = [
    "SevconGen4Decoder",
    "DeHaardtTransponderDecoder",
    "J1939ExtendedDecoder",
    "CiA402GenericDecoder",
]
