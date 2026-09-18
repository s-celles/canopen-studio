"""
CAN Hardware Interface Abstraction & Virtual CANopen Simulator.
Supports a wide variety of market USB-to-CAN adapters and virtual loopback for teaching.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

import os
import time
import threading
from typing import Dict, List, Optional, Any
import serial.tools.list_ports
import can

# python-can sends UDP multicast with a hop limit of 1, which keeps frames on the local
# network segment. A higher value lets them cross routers to reach other subnets.
DEFAULT_UDP_HOP_LIMIT = 1

# Market CAN Converters catalog
SUPPORTED_INTERFACES = {
    "slcan": {
        "name": "SLCAN (Lawicel CANUSB, USBtin, CANable slcan)",
        "backend": "slcan",
        "has_ports": True,
        "default_channels": ["COM4", "COM3", "COM1", "/dev/ttyUSB0"],
        "default_bitrate": 500000,
        "needs_serial_baud": True,
        "description": "Standard ASCII SLCAN over serial/USB (FTDI, CDC-ACM)",
    },
    "pcan": {
        "name": "PEAK-System PCAN (PCAN-USB, PCAN-PCI)",
        "backend": "pcan",
        "has_ports": False,
        "default_channels": ["PCAN_USBBUS1", "PCAN_USBBUS2", "PCAN_PCIBUS1", "PCAN_ISABUS1"],
        "default_bitrate": 500000,
        "needs_serial_baud": False,
        "description": "Industry-standard PEAK-System CAN interfaces (PCAN-Basic driver)",
    },
    "kvaser": {
        "name": "Kvaser (Leaf Light, Memorator, USBcan)",
        "backend": "kvaser",
        "has_ports": False,
        "default_channels": ["0", "1", "2"],
        "default_bitrate": 500000,
        "needs_serial_baud": False,
        "description": "Kvaser CANlib hardware interfaces",
    },
    "vector": {
        "name": "Vector (VN1610, VN1630, VN5610, CANcase)",
        "backend": "vector",
        "has_ports": False,
        "default_channels": ["0", "1"],
        "default_bitrate": 500000,
        "needs_serial_baud": False,
        "description": "Vector XL Driver Library interfaces",
    },
    "ixxat": {
        "name": "IXXAT (USB-to-CAN V2, compact)",
        "backend": "ixxat",
        "has_ports": False,
        "default_channels": ["0", "1"],
        "default_bitrate": 500000,
        "needs_serial_baud": False,
        "description": "HMS IXXAT VCI driver interfaces",
    },
    "gs_usb": {
        "name": "Candlelight / gs_usb (CANable, Candlelight)",
        "backend": "gs_usb",
        "has_ports": False,
        "default_channels": ["0", "1"],
        "default_bitrate": 500000,
        "needs_serial_baud": False,
        "description": "High-performance native USB CAN firmware (WinUSB / libusb)",
    },
    "socketcan": {
        "name": "SocketCAN (Linux can0, vcan0)",
        "backend": "socketcan",
        "has_ports": False,
        "default_channels": ["can0", "can1", "vcan0"],
        "default_bitrate": 500000,
        "needs_serial_baud": False,
        "description": "Linux kernel native SocketCAN network interface",
    },
    "udp_multicast": {
        "name": "UDP Multicast (Network CAN over IP)",
        "backend": "udp_multicast",
        "has_ports": False,
        "default_channels": ["224.0.0.1", "239.0.0.1"],
        "default_bitrate": 0,
        "needs_serial_baud": False,
        "description": "Virtual CAN bus over local IP network via UDP multicast",
    },
    "virtual": {
        "name": "Virtual Simulator (Demo & Teaching loopback)",
        "backend": "virtual",
        "has_ports": False,
        "default_channels": ["virtual_bus"],
        "default_bitrate": 500000,
        "needs_serial_baud": False,
        "description": "Hardware-free virtual bus with simulated CANopen nodes & telemetry",
    },
}

STANDARD_BITRATES = [
    10000,
    20000,
    50000,
    100000,
    125000,
    250000,
    500000,
    800000,
    1000000,
]


def list_com_ports() -> List[str]:
    """Scan and list all available system serial COM ports."""
    return [p.device for p in serial.tools.list_ports.comports()]


def find_canusb_port() -> Optional[str]:
    """Find FTDI CANUSB adapter (VID 0403 / PID 6001)."""
    for port in serial.tools.list_ports.comports():
        if port.vid == 0x0403 and port.pid == 0x6001:
            return port.device
    return None


def resolve_udp_hop_limit(hop_limit: Optional[int] = None) -> int:
    """
    Resolve the UDP multicast hop limit: explicit argument, else environment, else default.

    A hop limit of 1 confines frames to the local segment; raise it to reach other subnets.
    """
    if hop_limit is not None:
        return int(hop_limit)
    try:
        return int(os.environ.get("CANOPEN_UDP_HOP_LIMIT", DEFAULT_UDP_HOP_LIMIT))
    except ValueError:
        return DEFAULT_UDP_HOP_LIMIT


def open_can_bus(
    interface_key: str,
    channel: str,
    bitrate: int,
    hop_limit: Optional[int] = None,
) -> can.Bus:
    """
    Instantiate a python-can Bus object for any selected market interface.

    Args:
        interface_key: Key of the interface in SUPPORTED_INTERFACES.
        channel: Channel string (serial port, IP multicast group, kernel interface...).
        bitrate: Bus bitrate in bps (ignored by the virtual backend).
        hop_limit: UDP multicast hop limit (TTL). Ignored by every other backend.
            Defaults to the CANOPEN_UDP_HOP_LIMIT environment variable, else 1.
    """
    cfg = SUPPORTED_INTERFACES.get(interface_key, SUPPORTED_INTERFACES["slcan"])
    backend = cfg["backend"]

    # Parse channel (numeric for kvaser/vector/ixxat, string for slcan/pcan/socketcan)
    chan = channel.strip()
    if backend in ("kvaser", "vector", "ixxat", "gs_usb") and chan.isdigit():
        chan = int(chan)

    kwargs: Dict[str, Any] = {
        "interface": backend,
        "channel": chan,
    }

    if backend != "virtual":
        kwargs["bitrate"] = bitrate

    if cfg["needs_serial_baud"]:
        kwargs["tty_baudrate"] = 115200

    if backend == "vector":
        kwargs["app_name"] = "CANopenStudio"

    if backend == "udp_multicast":
        kwargs["hop_limit"] = resolve_udp_hop_limit(hop_limit)

    return can.Bus(**kwargs)


class VirtualCanopenSimulator:
    """
    Simulates real CANopen nodes on a virtual bus for learning and teaching without hardware.
    Emits:
    - Node 1 Heartbeat (0x701) and Node 2 Heartbeat (0x702)
    - CANopen SYNC pulse (0x080) at 50 Hz
    - CiA 402 TPDO1 (0x181) and TPDO2 (0x281) with dynamic motor speed
    - SEVCON Gen4 TPDOs (0x148, 0x156, 0x270, 0x473) with simulated RPM, torque, and temps
    - Answers SDO read requests (0x601 -> 0x581) for Device Type (0x1000) and Device Name (0x1008)
    - Reacts to NMT master commands (0x000)
    """

    def __init__(self, channel_or_bus: Any = "virtual_bus"):
        self.sim_bus: Optional[can.Bus] = None
        self.channel = "virtual_bus"
        self._owns_bus = False
        self.running = False

        if hasattr(channel_or_bus, "send") and hasattr(channel_or_bus, "recv"):
            self.sim_bus = channel_or_bus
        elif isinstance(channel_or_bus, str):
            self.channel = channel_or_bus

    def start(self):
        if self.running:
            return
        if self.sim_bus is None:
            try:
                self.sim_bus = can.Bus(interface="virtual", channel=self.channel)
                self._owns_bus = True
            except Exception:
                return
        self.running = True
        self.thread = threading.Thread(target=self._sim_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.sim_bus and self._owns_bus:
            try:
                self.sim_bus.shutdown()
            except Exception:
                pass
            self.sim_bus = None

    def _sim_loop(self):
        last_hb = 0.0
        last_sync = 0.0
        last_pdo = 0.0
        angle = 0.0

        while self.running:
            now = time.time()

            # Check for incoming SDO or NMT frames on virtual bus
            try:
                if self.sim_bus:
                    rx_msg = self.sim_bus.recv(timeout=0.01)
                    if rx_msg:
                        self._handle_rx(rx_msg)
            except Exception:
                pass

            # 1. SYNC frame at 50 Hz (every 20 ms)
            if now - last_sync >= 0.020:
                try:
                    if self.sim_bus:
                        self.sim_bus.send(can.Message(arbitration_id=0x080, is_extended_id=False, data=[]))
                except Exception:
                    pass
                last_sync = now

            # 2. Heartbeat at 1 Hz
            if now - last_hb >= 1.0:
                try:
                    if self.sim_bus:
                        # Node 1 Heartbeat
                        self.sim_bus.send(
                            can.Message(arbitration_id=0x701, is_extended_id=False, data=[self.nmt_state_node1])
                        )
                        # Node 2 Heartbeat
                        self.sim_bus.send(
                            can.Message(arbitration_id=0x702, is_extended_id=False, data=[self.nmt_state_node2])
                        )
                except Exception:
                    pass
                last_hb = now

            # 3. TPDOs at 25 Hz (every 40 ms)
            if now - last_pdo >= 0.040:
                import math

                angle += 0.08
                # Sine wave oscillating between 400 and 3200 RPM
                self.sim_rpm = int(1800 + 1400 * math.sin(angle))
                self.sim_temp = 32 + int(8 * math.sin(angle * 0.3))
                torque_val = int(80 + 70 * math.sin(angle))

                try:
                    if self.sim_bus:
                        # Standard CiA 402 TPDO1 (0x181): Statusword 0x0027 (Operation Enabled)
                        sw_bytes = (0x0027).to_bytes(2, "little")
                        self.sim_bus.send(can.Message(arbitration_id=0x181, is_extended_id=False, data=sw_bytes))

                        # Standard CiA 402 TPDO2 (0x281): Statusword + Velocity Actual Value
                        vel_bytes = self.sim_rpm.to_bytes(4, "little", signed=True)
                        self.sim_bus.send(
                            can.Message(arbitration_id=0x281, is_extended_id=False, data=sw_bytes + vel_bytes)
                        )

                        # SEVCON TPDO5 (0x473): Max Speed (5000 RPM) + Actual Speed
                        max_rpm_bytes = (5000).to_bytes(4, "little", signed=True)
                        self.sim_bus.send(
                            can.Message(arbitration_id=0x473, is_extended_id=False, data=max_rpm_bytes + vel_bytes)
                        )

                        # SEVCON TPDO4 (0x270): Mode 3 + Target Torque
                        trq_bytes = torque_val.to_bytes(2, "little", signed=True)
                        self.sim_bus.send(
                            can.Message(
                                arbitration_id=0x270,
                                is_extended_id=False,
                                data=bytes([3, 4, 0, 0, 0]) + trq_bytes + bytes([0]),
                            )
                        )

                        # SEVCON TPDO3 (0x156): Temps & Voltages (Heatsink temp at bytes 6..7)
                        hs_bytes = self.sim_temp.to_bytes(2, "little", signed=True)
                        self.sim_bus.send(
                            can.Message(
                                arbitration_id=0x156, is_extended_id=False, data=bytes([0, 0, 10, 0, 50, 0]) + hs_bytes
                            )
                        )

                except Exception:
                    pass
                last_pdo = now

    def _handle_rx(self, msg: can.Message):
        """Respond to NMT commands and SDO queries."""
        # NMT Command (0x000)
        if msg.arbitration_id == 0x000 and len(msg.data) >= 2:
            cmd, target = msg.data[0], msg.data[1]
            if target in (0, 1):
                if cmd == 0x01:
                    self.nmt_state_node1 = 0x05  # Operational
                elif cmd == 0x02:
                    self.nmt_state_node1 = 0x04  # Stopped
                elif cmd == 0x80:
                    self.nmt_state_node1 = 0x7F  # Pre-Operational
                elif cmd == 0x81:
                    self.nmt_state_node1 = 0x00  # Bootup

        # SDO Request to Node 1 (0x601)
        elif msg.arbitration_id == 0x601 and len(msg.data) >= 4:
            cs = msg.data[0]
            idx = msg.data[1] | (msg.data[2] << 8)
            sub = msg.data[3]

            if cs == 0x40:  # Initiate Upload (Read)
                resp_payload = [0, 0, 0, 0]
                resp_cs = 0x42

                if idx == 0x1000 and sub == 0:  # Device Type: CiA 402 Servo Drive
                    resp_payload = list((0x00020192).to_bytes(4, "little"))
                    resp_cs = 0x43
                elif idx == 0x1008 and sub == 0:  # Device Name
                    name_bytes = b"Virtual Drive"
                    resp_payload = list(name_bytes[:4])
                    resp_cs = 0x43
                elif idx == 0x606C and sub == 0:  # Actual Velocity
                    resp_payload = list(self.sim_rpm.to_bytes(4, "little", signed=True))
                    resp_cs = 0x43

                reply = [resp_cs, idx & 0xFF, (idx >> 8) & 0xFF, sub] + resp_payload
                try:
                    if self.sim_bus:
                        self.sim_bus.send(can.Message(arbitration_id=0x581, is_extended_id=False, data=reply))
                except Exception:
                    pass
