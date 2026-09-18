"""
CANopen Protocol Abstraction Layer.
Translates raw CAN frames into structured CANopen services and manages network state.
"""

import time
from typing import Optional, Dict, Callable, List, Any
import can

from .types import CanopenMessage, CanopenService, NmtState, NmtCommand
from .registry import DecoderRegistry, get_default_registry


class CANopenLayer:
    """
    CANopen protocol abstraction layer running on top of a python-can bus.
    Provides service classification, state tracking, and extension dispatching.
    """

    def __init__(self, bus: can.BusABC, registry: Optional[DecoderRegistry] = None):
        self.bus = bus
        self.registry = registry or get_default_registry()

        # Network node states: node_id -> NmtState
        self.node_states: Dict[int, NmtState] = {}
        self.last_seen: Dict[int, float] = {}

        # Known custom COB-IDs mapped to service/node
        self._custom_cob_map: Dict[int, Dict[str, Any]] = {}
        self._rebuild_custom_cob_map()

        # Callbacks
        self.on_message_received: List[Callable[[CanopenMessage], None]] = []

    def _rebuild_custom_cob_map(self):
        """Build an index of custom COB-IDs registered by loaded application decoders."""
        self._custom_cob_map.clear()
        for dec in self.registry.decoders:
            for cob_id, desc in dec.custom_cob_ids.items():
                self._custom_cob_map[cob_id] = {
                    "decoder": dec,
                    "desc": desc,
                }

    def process_can_message(self, msg: can.Message) -> CanopenMessage:
        """
        Ingests a raw can.Message, classifies the CANopen service,
        tracks network states, and dispatches to application decoders.
        """
        cid = msg.arbitration_id
        is_ext = msg.is_extended_id
        data = bytes(msg.data)
        now = msg.timestamp if msg.timestamp else time.time()

        service = CanopenService.RAW_CAN
        node_id: Optional[int] = None
        pdo_num: Optional[int] = None
        info = ""

        # 1. Check for Extended 29-bit identifier (J1939 / ISO 11783)
        if is_ext:
            service = CanopenService.EXTENDED_J1939
            node_id = cid & 0xFF

        # 2. Standard 11-bit CANopen Services (CiA 301)
        elif cid == 0x000:
            service = CanopenService.NMT_MASTER
            if len(data) >= 2:
                cmd_val, target_node = data[0], data[1]
                node_id = target_node
                info = f"NMT Master -> Cmd: 0x{cmd_val:02X} for Node: {target_node if target_node != 0 else 'All'}"
            else:
                info = "NMT Master Command"

        elif cid == 0x080:
            service = CanopenService.SYNC
            info = "SYNC (Clock pulse)"

        elif cid == 0x100:
            service = CanopenService.TIME_STAMP
            info = "TIME STAMP"

        elif 0x081 <= cid <= 0x0FF:
            service = CanopenService.EMERGENCY
            node_id = cid - 0x080
            err_code = (data[1] << 8) | data[0] if len(data) >= 2 else 0
            info = f"EMCY Node {node_id} -> Code: 0x{err_code:04X}"

        elif 0x700 <= cid <= 0x77F:
            service = CanopenService.HEARTBEAT
            node_id = cid - 0x700
            if data:
                state = NmtState.from_byte(data[0])
                self.node_states[node_id] = state
                self.last_seen[node_id] = now
                info = f"Heartbeat Node {node_id} -> State: {state}"
                for dec in self.registry.decoders:
                    dec.on_nmt_state_change(node_id, state)
            else:
                info = f"Node Guarding Node {node_id}"

        elif 0x180 <= cid <= 0x1FF:
            service = CanopenService.TPDO
            pdo_num = 1
            node_id = cid - 0x180
            info = f"TPDO1 Node {node_id}"

        elif 0x200 <= cid <= 0x27F:
            service = CanopenService.RPDO
            pdo_num = 1
            node_id = cid - 0x200
            info = f"RPDO1 Node {node_id}"

        elif 0x280 <= cid <= 0x2FF:
            service = CanopenService.TPDO
            pdo_num = 2
            node_id = cid - 0x280
            info = f"TPDO2 Node {node_id}"

        elif 0x300 <= cid <= 0x37F:
            service = CanopenService.RPDO
            pdo_num = 2
            node_id = cid - 0x300
            info = f"RPDO2 Node {node_id}"

        elif 0x380 <= cid <= 0x3FF:
            service = CanopenService.TPDO
            pdo_num = 3
            node_id = cid - 0x380
            info = f"TPDO3 Node {node_id}"

        elif 0x400 <= cid <= 0x47F:
            service = CanopenService.RPDO
            pdo_num = 3
            node_id = cid - 0x400
            info = f"RPDO3 Node {node_id}"

        elif 0x480 <= cid <= 0x4FF:
            service = CanopenService.TPDO
            pdo_num = 4
            node_id = cid - 0x480
            info = f"TPDO4 Node {node_id}"

        elif 0x500 <= cid <= 0x57F:
            service = CanopenService.RPDO
            pdo_num = 4
            node_id = cid - 0x500
            info = f"RPDO4 Node {node_id}"

        elif 0x580 <= cid <= 0x5FF:
            service = CanopenService.SDO_TX
            node_id = cid - 0x580
            if len(data) >= 4:
                idx = data[1] | (data[2] << 8)
                sub = data[3]
                info = f"SDO Response (Server->Client) Node {node_id} [0x{idx:04X}:{sub:02X}]"
            else:
                info = f"SDO Response Node {node_id}"

        elif 0x600 <= cid <= 0x67F:
            service = CanopenService.SDO_RX
            node_id = cid - 0x600
            if len(data) >= 4:
                idx = data[1] | (data[2] << 8)
                sub = data[3]
                info = f"SDO Request (Client->Server) Node {node_id} [0x{idx:04X}:{sub:02X}]"
            else:
                info = f"SDO Request Node {node_id}"

        # 3. Check for custom COB-IDs mapped by application profiles
        elif cid in self._custom_cob_map:
            mapping = self._custom_cob_map[cid]
            info = f"Custom: {mapping['desc']}"
            service = CanopenService.TPDO

        parsed_msg = CanopenMessage(
            arbitration_id=cid,
            is_extended=is_ext,
            dlc=msg.dlc,
            data=data,
            timestamp=now,
            service=service,
            node_id=node_id,
            pdo_number=pdo_num,
            decoded_info=info,
        )

        # 4. Dispatch through the application decoder registry
        self.registry.dispatch_and_decode(parsed_msg)

        # Notify any subscribed listeners
        for cb in self.on_message_received:
            cb(parsed_msg)

        return parsed_msg

    # High-level transmit methods
    def send_nmt_command(self, node_id: int, command: NmtCommand) -> None:
        """Send an NMT state transition command (COB-ID 0x000)."""
        msg = can.Message(arbitration_id=0x000, is_extended_id=False, data=[command.value, node_id])
        self.bus.send(msg)

    def send_sync(self) -> None:
        """Send a CANopen SYNC clock frame (COB-ID 0x080)."""
        msg = can.Message(arbitration_id=0x080, is_extended_id=False, data=[])
        self.bus.send(msg)

    def send_sdo_read(self, node_id: int, index: int, subindex: int) -> None:
        """Send an SDO Initiate Upload Request (read object dictionary)."""
        cob_id = 0x600 + node_id
        req = [0x40, index & 0xFF, (index >> 8) & 0xFF, subindex, 0, 0, 0, 0]
        self.bus.send(can.Message(arbitration_id=cob_id, is_extended_id=False, data=req))

    def send_sdo_write(self, node_id: int, index: int, subindex: int, data_bytes: bytes) -> None:
        """Send an SDO Expedited Download Request (write object dictionary)."""
        cob_id = 0x600 + node_id
        n = 4 - len(data_bytes)  # number of unused bytes
        cs = 0x23 | ((n & 0x03) << 2)  # expedited download request
        payload = [cs, index & 0xFF, (index >> 8) & 0xFF, subindex]
        payload.extend(data_bytes)
        while len(payload) < 8:
            payload.append(0)
        self.bus.send(can.Message(arbitration_id=cob_id, is_extended_id=False, data=payload))
