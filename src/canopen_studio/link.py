"""
The one link the studio talks through, whatever sits at the other end.

There are two kinds of adapter, and they are not interchangeable. A CAN adapter — SLCAN,
PCAN, Kvaser, a UDP bus, the virtual simulator — hands the studio raw frames: the trace,
the CANopen stack and a native OBD-II session over ISO-TP all run on it. An ELM327 is
not a bus at all: it runs its own protocol detection and ISO-TP, and answers diagnostic
requests in ASCII. It can do OBD-II and nothing else.

Before this module the studio modelled that as two connections — a CAN bus in the top
bar, and a diagnostic adapter with a Connect button of its own — where "native CAN" in
the second silently depended on the first. Now there is one `Link`: the user picks the
physical adapter once, and each feature asks the link what it can do rather than
guessing from which button was pressed.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from __future__ import annotations

from typing import Any, Callable, Dict, FrozenSet, Optional, Tuple

from canopen_studio.diag.elm327.ble import BleElmTransport
from canopen_studio.diag.elm327.interface import ElmDiagnosticInterface
from canopen_studio.diag.elm327.transport import DEFAULT_BAUDRATE, DEFAULT_TCP_PORT, SerialElmTransport, TcpElmTransport
from canopen_studio.diag.interface import DiagnosticInterface
from canopen_studio.diag.native import NativeCanDiagnosticInterface, QueueFrameSource
from canopen_studio.interfaces import SUPPORTED_INTERFACES, VirtualCanopenSimulator, open_can_bus

# What a link can carry.
CAP_TRACE = "trace"
CAP_CANOPEN = "canopen"
CAP_OBD = "obd"

CAN_CAPABILITIES: FrozenSet[str] = frozenset({CAP_TRACE, CAP_CANOPEN, CAP_OBD})
ELM327_CAPABILITIES: FrozenSet[str] = frozenset({CAP_OBD})

# Shown in the channel field of a BLE link: leave it and the first adapter found is used.
BLE_FIRST_FOUND = "(first adapter found)"

# ELM327 adapters, described like the CAN interfaces so one chooser can list both.
ELM327_INTERFACES: Dict[str, Dict[str, Any]] = {
    "elm327_serial": {
        "name": "ELM327 — USB / Bluetooth SPP (OBD-II only)",
        "has_ports": True,
        "default_channels": ["/dev/ttyUSB0", "COM4", "/dev/rfcomm0"],
        "default_bitrate": 0,
        "description": "ELM327 on a serial port; append @9600 to the port for an older board.",
    },
    "elm327_tcp": {
        "name": "ELM327 — Wi-Fi / TCP (OBD-II only)",
        "has_ports": False,
        "default_channels": [f"192.168.0.10:{DEFAULT_TCP_PORT}"],
        "default_bitrate": 0,
        "description": "ELM327 Wi-Fi adapter, or the ELM327 emulator, as host:port.",
    },
    "elm327_ble": {
        "name": "ELM327 — Bluetooth LE (OBD-II only)",
        "has_ports": False,
        "default_channels": [BLE_FIRST_FOUND],
        "default_bitrate": 0,
        "description": "Bluetooth LE adapter, by address or name fragment; needs the ble extra.",
    },
}

# Every link a user can pick, CAN adapters first.
LINK_INTERFACES: Dict[str, Dict[str, Any]] = {**SUPPORTED_INTERFACES, **ELM327_INTERFACES}


class LinkError(Exception):
    """A link could not be opened, or cannot do what was asked of it."""


def is_elm327(interface: str) -> bool:
    """Whether `interface` names an ELM327 adapter rather than a CAN bus."""
    return interface in ELM327_INTERFACES


def link_capabilities(interface: str) -> FrozenSet[str]:
    """What a link of this kind can carry."""
    return ELM327_CAPABILITIES if is_elm327(interface) else CAN_CAPABILITIES


def build_elm_transport(interface: str, channel: str):
    """
    The byte transport an ELM327 link needs, from the channel as the user typed it.

    `elm327_serial` takes a port, optionally with a rate (`COM4@9600`); `elm327_tcp` a
    host with an optional port (`192.168.0.10:35000`); `elm327_ble` an address, a name
    fragment, or nothing for the first adapter found.
    """
    text = (channel or "").strip()
    if interface == "elm327_serial":
        port, _, rate = text.partition("@")
        if not port:
            raise LinkError("give the serial port the ELM327 is on, e.g. /dev/ttyUSB0 or COM4")
        try:
            baudrate = int(rate) if rate else DEFAULT_BAUDRATE
        except ValueError:
            raise LinkError(f"{rate!r} is not a baud rate") from None
        return SerialElmTransport(port, baudrate=baudrate)
    if interface == "elm327_tcp":
        host, _, port = text.rpartition(":") if ":" in text else (text, "", "")
        try:
            return TcpElmTransport(host or "192.168.0.10", int(port) if port else DEFAULT_TCP_PORT)
        except ValueError:
            raise LinkError(f"{port!r} is not a TCP port") from None
    if interface == "elm327_ble":
        return BleElmTransport(None if text in ("", BLE_FIRST_FOUND) else text)
    raise LinkError(f"{interface!r} is not an ELM327 adapter")


class Link:
    """
    One physical adapter, opened once and shared by every feature that can use it.

    Args:
        interface: A key of `LINK_INTERFACES`.
        channel: Port, device, address or group, as the adapter needs.
        bitrate: CAN bitrate; ignored by ELM327 adapters, which find their own.
        simulate: Run the virtual CANopen simulator on a CAN link.
        protocol: ELM327 protocol number, "0" to autodetect.
        hop_limit: UDP multicast only.
        opener: Opens a CAN bus; replaceable in tests.
    """

    def __init__(
        self,
        interface: str,
        channel: str,
        bitrate: int = 500000,
        simulate: bool = False,
        protocol: str = "0",
        hop_limit: Optional[int] = None,
        opener: Callable[..., Any] = open_can_bus,
    ):
        if interface not in LINK_INTERFACES:
            raise LinkError(f"unknown adapter {interface!r}")
        self.interface = interface
        self.channel = channel
        self.bitrate = bitrate
        self.simulate = simulate
        self.protocol = protocol
        self.hop_limit = hop_limit
        self._opener = opener

        self.bus: Any = None
        self.sim_bus: Any = None
        self.simulator: Optional[VirtualCanopenSimulator] = None
        # The ELM327 session, which is the link itself when the adapter is an ELM327.
        self.elm: Optional[ElmDiagnosticInterface] = None

    # -- What it is --------------------------------------------------------

    @property
    def kind(self) -> str:
        return "elm327" if is_elm327(self.interface) else "can"

    @property
    def capabilities(self) -> FrozenSet[str]:
        return link_capabilities(self.interface)

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    @property
    def is_open(self) -> bool:
        return self.bus is not None or (self.elm is not None and self.elm.is_open)

    @property
    def name(self) -> str:
        return LINK_INTERFACES[self.interface]["name"]

    @property
    def description(self) -> str:
        if self.elm is not None:
            return self.elm.description
        text = f"{self.interface} [{self.channel}]"
        if self.bitrate:
            text += f" @ {self.bitrate / 1000:g} kbps"
        if self.simulator is not None:
            text += " + simulator"
        return text

    def unavailable_reason(self, capability: str) -> Optional[str]:
        """Why this link cannot do `capability`, or None when it can."""
        if self.can(capability):
            return None
        what = {CAP_TRACE: "the frame trace", CAP_CANOPEN: "CANopen"}.get(capability, capability)
        return f"{what} needs raw CAN frames, and an ELM327 does not pass them on — connect a CAN adapter"

    # -- Lifecycle ---------------------------------------------------------

    def open(self) -> None:
        """
        Open the adapter. Blocking: an ELM327 resets and searches for the vehicle's
        protocol, which takes seconds, so a GUI must call this off its event thread.

        Raises:
            LinkError: With what went wrong. Nothing is left half open.
        """
        if self.is_open:
            return
        try:
            if self.kind == "elm327":
                self.elm = ElmDiagnosticInterface(build_elm_transport(self.interface, self.channel), self.protocol)
                self.elm.open()
            else:
                self._open_can()
        except LinkError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise LinkError(f"cannot open {self.name} on {self.channel!r}: {exc}") from exc

    def _open_can(self) -> None:
        self.bus = self._opener(self.interface, self.channel, self.bitrate, hop_limit=self.hop_limit)
        if self.interface == "virtual":
            # The virtual backend delivers a bus's frames to every other bus on the same
            # channel, so the simulator opens its own and the two see each other.
            self.simulator = VirtualCanopenSimulator(self.channel)
        elif self.simulate:
            # A UDP bus does not receive its own frames: the simulator needs a second
            # socket, so that its frames travel through the network stack to ours.
            self.sim_bus = self._opener(self.interface, self.channel, self.bitrate, hop_limit=self.hop_limit)
            self.simulator = VirtualCanopenSimulator(self.sim_bus)
        if self.simulator is not None:
            self.simulator.start()

    def close(self) -> None:
        """Release everything. Safe to call twice, or on a link that never opened."""
        elm, self.elm = self.elm, None
        if elm is not None:
            try:
                elm.close()
            except Exception:
                # An adapter already gone is not a reason to fail closing.
                pass
        simulator, self.simulator = self.simulator, None
        if simulator is not None:
            simulator.stop()
        for attribute in ("sim_bus", "bus"):
            bus = getattr(self, attribute)
            setattr(self, attribute, None)
            if bus is not None:
                try:
                    bus.shutdown()
                except Exception:
                    pass

    # -- Diagnostics -------------------------------------------------------

    def diagnostic_session(self) -> Tuple[DiagnosticInterface, Optional[QueueFrameSource]]:
        """
        The OBD-II session this link carries, opened.

        On an ELM327 that is the adapter's own session. On a CAN bus it is ISO-TP over
        the bus, borrowed rather than owned; the returned queue is where the caller's
        capture loop must feed received frames, since only one reader may own the bus.

        Raises:
            LinkError: If the link is not open.
        """
        if not self.is_open:
            raise LinkError("the link is not open — connect first")
        if self.elm is not None:
            return self.elm, None
        source = QueueFrameSource()
        session = NativeCanDiagnosticInterface(self.bus, source=source)
        session.open()
        return session, source
