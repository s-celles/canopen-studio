"""
CAN Hardware Interface Abstraction & Virtual CANopen Simulator.
Supports a wide variety of market USB-to-CAN adapters and virtual loopback for teaching.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

import os
import socket
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


def _detect_local_ip(target_ip: str = "8.8.8.8") -> Optional[str]:
    """Detect the active local network interface IP routed towards LAN/Internet."""
    for probe_dest in [target_ip, "1.1.1.1", "8.8.8.8"]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((probe_dest, 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return None


def is_multicast_ip(ip_str: str) -> bool:
    """Return True if ip_str is an IPv4 or IPv6 multicast address."""
    try:
        if ":" in ip_str:
            return ip_str.lower().startswith("ff")
        first = int(ip_str.split(".")[0])
        return 224 <= first <= 239
    except Exception:
        return False


class UdpBus(can.BusABC):
    """
    Standard UDP unicast/broadcast CAN bus backend.

    If the Rust `canopen_core` module is available, it uses the high-performance
    native backend `UdpCanBus` which parses frames at wire speed. Otherwise,
    it falls back to Python `socket` and `python-can`'s msgpack logic.
    """

    def __init__(
        self,
        channel: str = "127.0.0.1",
        port: int = 1750,
        receive_own_messages: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(channel=channel, **kwargs)
        self.dest_ip = channel
        self.port = port
        self.receive_own_messages = receive_own_messages
        self._native_bus = None
        self._native_core = None

        try:
            from . import canopen_core

            self._native_bus = canopen_core.UdpCanBus(
                bind_port=self.port, target_host=self.dest_ip, target_port=self.port, compact=False
            )
            self._native_core = canopen_core
        except ImportError:
            pass

        if self._native_bus is None:
            from can.interfaces.udp_multicast.utils import pack_message, unpack_message

            self._pack_message = pack_message
            self._unpack_message = unpack_message

            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._sock.bind(("", self.port))
            self._send_dest = (self.dest_ip, self.port)

    def send(self, msg: can.Message, timeout: Optional[float] = None) -> None:
        if self._native_bus is not None:
            # Convert can.Message to PyCanFrame
            frame = self._native_core.CanFrame(
                msg.arbitration_id,
                bytes(msg.data),
                int(msg.timestamp * 1_000_000) if msg.timestamp else None,
                msg.is_extended_id,
            )
            self._native_bus.send(frame)
        else:
            data = self._pack_message(msg)
            self._sock.sendto(data, self._send_dest)

    def _recv_internal(self, timeout: Optional[float]) -> tuple[Optional[can.Message], bool]:
        if self._native_bus is not None:
            t_ms = int(timeout * 1000) if timeout is not None else None
            res = self._native_bus.recv(t_ms)
            if res is not None:
                frame, _addr = res
                msg = can.Message(
                    timestamp=frame.timestamp_sec,
                    arbitration_id=frame.id,
                    is_extended_id=frame.is_extended,
                    data=frame.data,
                    is_remote_frame=frame.is_remote,
                    is_error_frame=frame.is_error,
                )
                return msg, False
            return None, False
        else:
            self._sock.settimeout(timeout)
            try:
                raw, addr = self._sock.recvfrom(4096)
                now = time.time()
                msg = self._unpack_message(raw, replace={"timestamp": now})
                return msg, False
            except (socket.timeout, TimeoutError):
                return None, False
            except OSError:
                return None, False

    def shutdown(self) -> None:
        super().shutdown()
        if self._native_bus is None:
            try:
                self._sock.close()
            except OSError:
                pass


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
        target_ip = "224.0.0.1"
        port = 43113
        if "CANOPEN_UDP_PORT" in os.environ:
            try:
                port = int(os.environ["CANOPEN_UDP_PORT"])
            except ValueError:
                pass

        if isinstance(chan, str) and ":" in chan:
            ip_part, port_part = chan.rsplit(":", 1)
            if port_part.isdigit():
                target_ip = ip_part.strip()
                port = int(port_part.strip())
        elif isinstance(chan, str) and chan:
            target_ip = chan.strip()

        # If target is unicast or broadcast (not in multicast 224.0.0.0/4), use UdpBus
        if not is_multicast_ip(target_ip):
            return UdpBus(channel=target_ip, port=port)

        kwargs["channel"] = target_ip
        kwargs["port"] = port

    bus = can.Bus(**kwargs)

    if backend == "udp_multicast":
        try:
            mcast = getattr(bus, "_multicast", None)
            sock = getattr(mcast, "_socket", None)
            if sock and getattr(mcast, "ip_version", 4) == 4:
                group = getattr(mcast, "group", "224.0.0.1")
                local_ip = os.environ.get("CANOPEN_UDP_IF") or _detect_local_ip(group)
                if local_ip and local_ip != "0.0.0.0" and not local_ip.startswith("127."):  # nosec B104 - comparison, not a bind
                    ip_bin = socket.inet_aton(local_ip)
                    # Bind outgoing multicast packets to this network interface (prevents Errno 65 on macOS)
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, ip_bin)
                    try:
                        group_bin = socket.inet_pton(socket.AF_INET, group)
                        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, group_bin + ip_bin)
                    except OSError:
                        pass
        except Exception:
            pass

    return bus


class VirtualCanopenSimulator:
    """
    Simulates real CANopen nodes on a virtual bus for learning and teaching without hardware.
    Now backed by Rust `canopen_core` for zero-overhead background timing and logic.
    """

    def __init__(self, channel_or_bus: Any = "virtual_bus"):
        self.sim_bus: Optional[can.Bus] = None
        self.channel = "virtual_bus"
        self._owns_bus = False
        self.running = False

        self._native_sim = None

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

        try:
            from . import canopen_core

            self._native_sim = canopen_core.VirtualCanopenSimulator()

            # Create bridging callbacks
            def py_send(frame):
                msg = can.Message(
                    timestamp=frame.timestamp_sec,
                    arbitration_id=frame.id,
                    is_extended_id=frame.is_extended,
                    data=frame.data,
                    is_remote_frame=frame.is_remote,
                    is_error_frame=frame.is_error,
                )
                if self.sim_bus is not None:
                    self.sim_bus.send(msg)

            def py_recv(timeout):
                if self.sim_bus is None:
                    return None
                msg = self.sim_bus.recv(timeout)
                if msg is not None:
                    return canopen_core.CanFrame(
                        msg.arbitration_id,
                        bytes(msg.data),
                        int(msg.timestamp * 1_000_000) if msg.timestamp else None,
                        msg.is_extended_id,
                    )
                return None

            self._native_sim.start(py_send, py_recv)

        except ImportError:
            # Fallback for when core is not available
            self.thread = threading.Thread(target=self._sim_loop, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False
        if self._native_sim is not None:
            self._native_sim.stop()
            self._native_sim = None

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
