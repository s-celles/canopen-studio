"""
Type definitions, enumerations, and data classes for the CANopen stack.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


class NmtState(Enum):
    BOOT_UP = 0x00
    STOPPED = 0x04
    OPERATIONAL = 0x05
    PRE_OPERATIONAL = 0x7F
    UNKNOWN = 0xFF

    @classmethod
    def from_byte(cls, value: int) -> "NmtState":
        for member in cls:
            if member.value == value:
                return member
        return cls.UNKNOWN

    def __str__(self) -> str:
        return self.name.replace("_", " ").title()


class NmtCommand(Enum):
    START_NODE = 0x01
    STOP_NODE = 0x02
    ENTER_PRE_OPERATIONAL = 0x80
    RESET_NODE = 0x81
    RESET_COMMUNICATION = 0x82

    def __str__(self) -> str:
        return self.name.replace("_", " ").title()


class CanopenService(Enum):
    NMT_MASTER = "NMT Master"
    SYNC = "SYNC"
    TIME_STAMP = "TIME STAMP"
    EMERGENCY = "EMCY"
    TPDO = "TPDO"
    RPDO = "RPDO"
    SDO_TX = "SDO Tx (Response)"
    SDO_RX = "SDO Rx (Request)"
    HEARTBEAT = "Heartbeat / Bootup"
    EXTENDED_J1939 = "Extended J1939"
    RAW_CAN = "Raw CAN"


@dataclass
class CanopenMessage:
    """Represents a message parsed at the CANopen layer."""

    arbitration_id: int
    is_extended: bool
    dlc: int
    data: bytes
    timestamp: float
    service: CanopenService
    node_id: Optional[int] = None
    pdo_number: Optional[int] = None
    decoded_info: str = ""
    signals: Dict[str, Any] = field(default_factory=dict)
