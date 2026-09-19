"""
OBD-II diagnostic tools for the existing MCP server.

These extend `canopen_studio.mcp_server` rather than standing up a second server: one
process, one port, one set of guards. `register(mcp)` is called from there, and the tools
are plain module-level functions so they can be tested without a server at all — the same
arrangement the CAN tools already use.

**Every tool here reads.** There is deliberately no tool that clears trouble codes or
writes to an ECU. The write gate in `canopen_studio.diag.security` exists for a person at
a keyboard who has set an environment variable and confirmed a specific call; an agent
holding a tool schema is not that person. A model that decides to "reset the fault and
try again" would erase readiness monitors on somebody's car.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .elm327.interface import ElmDiagnosticInterface
from .elm327.transport import DEFAULT_BAUDRATE, DEFAULT_TCP_PORT, SerialElmTransport, TcpElmTransport
from .interface import DiagnosticError, DiagnosticInterface
from .j1979.client import J1979Client
from .j1979.dtc import DTC_MODES
from .j1979.pids import parse_key
from .native import NativeCanDiagnosticInterface, QueueFrameSource
from .profiles.library import ProfileLibrary, default_library
from .profiles.resolver import ProfileMatch, ProfileResolver
from .security import WriteGate

# The session these tools act on. One at a time: a vehicle has one diagnostic link.
_session: Optional[DiagnosticInterface] = None
_client: Optional[J1979Client] = None
_match: Optional[ProfileMatch] = None
_frame_source: Optional[QueueFrameSource] = None

# Set by canopen_studio.gui when the tools run inside the application, so a native
# session can borrow the bus the capture loop already owns.
_app_ref: Any = None


def set_app(app: Any) -> None:
    """Register the GUI app, so a native session reuses its bus instead of opening one."""
    global _app_ref
    _app_ref = app


def frame_source() -> Optional[QueueFrameSource]:
    """
    The queue a capture loop feeds, when a native session is running.

    The GUI's receive loop owns the only reader on the bus, so it hands frames here
    rather than letting a second reader steal them from the trace and the plotter.
    """
    return _frame_source


def _library() -> ProfileLibrary:
    return default_library()


def _require_session() -> J1979Client:
    if _client is None:
        raise DiagnosticError("no diagnostic session — call obd_connect() first")
    return _client


def _reset_state() -> None:
    global _session, _client, _match, _frame_source
    _session = None
    _client = None
    _match = None
    _frame_source = None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def obd_connect(
    transport: str = "elm327",
    port: str = "/dev/ttyUSB0",
    baudrate: int = DEFAULT_BAUDRATE,
    host: str = "192.168.0.10",
    tcp_port: int = DEFAULT_TCP_PORT,
    protocol: str = "0",
    interface: str = "socketcan",
    channel: str = "can0",
    bitrate: int = 500000,
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Open an OBD-II diagnostic session and identify the vehicle.

    Args:
        transport: How to reach the vehicle — "elm327" for a serial or Bluetooth SPP
            adapter, "elm327_tcp" for a Wi-Fi adapter or emulator, "native" to speak
            ISO-TP over one of the studio's own CAN adapters.
        port: Serial device for transport="elm327", e.g. /dev/ttyUSB0, /dev/rfcomm0, COM4.
        baudrate: Serial line rate. 38400 suits most adapters; try 9600 for an old board.
        host: Address for transport="elm327_tcp".
        tcp_port: TCP port for transport="elm327_tcp", 35000 by convention.
        protocol: ELM327 protocol number, or "0" to autodetect. Forcing the right one
            skips the search delay on the first request.
        interface: CAN adapter key for transport="native" — socketcan, slcan, pcan,
            kvaser, gs_usb, udp_multicast, virtual.
        channel: Channel for transport="native", e.g. can0 or /dev/ttyUSB0.
        bitrate: Bus bitrate for transport="native".
        profile: Vehicle profile to use, overriding automatic resolution. Omit to let
            the VIN and the supported-PID fingerprint decide.
    """
    global _session, _client, _match, _frame_source

    if _session is not None:
        return {"connected": True, "message": "Already connected — call obd_disconnect() first."}

    try:
        session, source = _build_session(
            transport, port, baudrate, host, tcp_port, protocol, interface, channel, bitrate
        )
        session.open()
    except Exception as exc:
        _reset_state()
        return {"connected": False, "error": str(exc)}

    _session = session
    _frame_source = source

    try:
        client = J1979Client(session)
        identity = client.identify()
        match = ProfileResolver(_library()).resolve(identity, manual=profile)
        client.table = match.profile.table
        client.dtc_descriptions = dict(match.profile.dtc_descriptions)
    except Exception as exc:
        session.close()
        _reset_state()
        return {"connected": False, "error": str(exc)}

    _client = client
    _match = match

    return {
        "connected": True,
        "link": session.description,
        "vehicle": identity.as_dict(),
        "profile": match.as_dict(),
        "writes": WriteGate(match.profile).describe(),
    }


def _build_session(transport, port, baudrate, host, tcp_port, protocol, interface, channel, bitrate):
    """Build the session a transport name asks for, without opening it yet."""
    kind = str(transport).strip().lower()

    if kind in ("elm327", "elm", "serial"):
        return ElmDiagnosticInterface(SerialElmTransport(port, baudrate=baudrate), protocol=protocol), None

    if kind in ("elm327_tcp", "tcp", "wifi"):
        return ElmDiagnosticInterface(TcpElmTransport(host, tcp_port), protocol=protocol), None

    if kind in ("native", "can", "isotp"):
        if _app_ref is not None and getattr(_app_ref, "bus", None) is not None:
            # Borrow the bus the application already owns, and take frames from its
            # capture loop rather than opening a second reader on the same adapter.
            source = QueueFrameSource()
            return NativeCanDiagnosticInterface(_app_ref.bus, source=source), source
        return NativeCanDiagnosticInterface.open_bus(interface, channel, bitrate), None

    raise DiagnosticError(f"unknown transport {transport!r}; expected elm327, elm327_tcp or native")


def obd_disconnect() -> str:
    """Close the OBD-II diagnostic session."""
    global _session
    if _session is None:
        return "No diagnostic session."
    try:
        _session.close()
    finally:
        _reset_state()
    return "Diagnostic session closed."


def obd_status() -> Dict[str, Any]:
    """Return the diagnostic session state, the active vehicle profile and the write posture."""
    if _session is None or _match is None:
        return {
            "connected": False,
            "message": "No diagnostic session — call obd_connect() first.",
            "profiles_available": len(_library()),
        }
    return {
        "connected": True,
        "link": _session.description,
        "profile": _match.as_dict(),
        "writes": WriteGate(_match.profile).describe(),
    }


def obd_list_supported_pids(mode: int = 1) -> Dict[str, Any]:
    """List the PIDs the vehicle says it implements, discovered from its own bitmasks.

    Nothing is assumed: the vehicle declares its capabilities through PIDs 0x00, 0x20,
    0x40 and so on, and the walk stops where the vehicle says it stops.

    Args:
        mode: The service to enumerate. 1 for live data, 9 for vehicle information.
    """
    client = _require_session()
    supported = client.supported_pids(mode)
    described = []
    for pid in supported:
        definition = client.describes(pid, mode)
        described.append(
            {
                "pid": f"{mode:02X}:{pid:02X}",
                "name": definition.name if definition else None,
                "description": definition.description if definition else "",
                "unit": definition.unit if definition else "",
                "ecus": [f"0x{ecu:X}" for ecu in supported.supported_by(pid)],
            }
        )
    return {"mode": f"{mode:02X}", "count": len(described), "pids": described}


def obd_read_pid(pid: str, mode: int = 1) -> Dict[str, Any]:
    """Read one PID and decode every ECU's answer through the active vehicle profile.

    Args:
        pid: The parameter identifier, as hexadecimal ("0C") or as a full key ("01:0C"),
            or the name a profile gives it ("engine_speed").
        mode: The service to ask, when the PID is given as a bare identifier.
    """
    client = _require_session()

    definition = client.table.by_name(str(pid).strip())
    if definition is not None:
        resolved_mode, resolved_pid = definition.mode, definition.pid
    else:
        try:
            resolved_mode, resolved_pid = _parse_pid(str(pid), mode)
        except ValueError as exc:
            return {"error": str(exc)}

    readings = client.read_pid(resolved_pid, resolved_mode)
    if not readings:
        return {
            "pid": f"{resolved_mode:02X}:{resolved_pid:02X}",
            "supported": False,
            "message": "No ECU answered, which is how an unsupported PID presents.",
        }
    return {
        "pid": f"{resolved_mode:02X}:{resolved_pid:02X}",
        "supported": True,
        "readings": [reading.as_dict() for reading in readings],
    }


def _parse_pid(text: str, mode: int) -> tuple:
    """Read a PID given as `0C`, `0x0C` or `01:0C`."""
    cleaned = text.strip()
    if ":" in cleaned:
        return parse_key(cleaned)
    try:
        return int(mode), int(cleaned, 16)
    except ValueError:
        raise ValueError(f"{text!r} is not a PID: expected '0C', '01:0C' or a name from the profile") from None


def obd_read_dtcs(kind: str = "stored") -> Dict[str, Any]:
    """Read diagnostic trouble codes.

    Args:
        kind: "stored" for confirmed faults with the lamp on, "pending" for ones awaiting
            a second drive cycle, "permanent" for the ones only the vehicle may clear,
            or "all" for every kind in one pass.
    """
    client = _require_session()
    wanted = str(kind).strip().lower()

    if wanted == "all":
        codes = client.read_all_dtcs()
    elif wanted in DTC_MODES:
        codes = client.read_dtcs(wanted)
    else:
        return {"error": f"unknown kind {kind!r}; expected all, {', '.join(DTC_MODES)}"}

    return {
        "kind": wanted,
        "count": len(codes),
        "codes": [code.as_dict() for code in codes],
        "note": "Clearing codes is not available to agents; it erases the readiness monitors.",
    }


def obd_read_vin() -> Dict[str, Any]:
    """Read the vehicle identification number, via mode 09 PID 02, and decode what it says."""
    client = _require_session()
    vin = client.read_vin()
    if not vin:
        return {"vin": None, "message": "No ECU returned a usable VIN."}

    from .j1979.vin import VinError, parse_vin

    payload: Dict[str, Any] = {"vin": vin}
    try:
        payload["details"] = parse_vin(vin).as_dict()
    except VinError as exc:
        payload["note"] = f"the VIN could not be decoded: {exc}"
    return payload


def obd_identify_vehicle() -> Dict[str, Any]:
    """Gather everything the vehicle says about itself, and which profile that resolves to.

    Returns the VIN, the answering ECUs and their names, the calibration identifiers, the
    OBD standard the vehicle claims, and the supported-PID fingerprint used to pick a
    profile when there is no VIN.
    """
    client = _require_session()
    identity = client.identify()
    match = ProfileResolver(_library()).resolve(identity)
    return {"vehicle": identity.as_dict(), "profile": match.as_dict()}


def obd_list_profiles() -> Dict[str, Any]:
    """List the vehicle profiles available, for choosing one by hand in obd_connect()."""
    library = _library()
    return {
        "count": len(library),
        "profiles": [profile.as_dict() for profile in library.resolved()],
        "errors": library.errors,
    }


# Every tool this module contributes. Read-only by construction: adding a write here
# would also need the gate in `security.py`, which refuses agents by design.
TOOLS = (
    obd_connect,
    obd_disconnect,
    obd_status,
    obd_list_supported_pids,
    obd_read_pid,
    obd_read_dtcs,
    obd_read_vin,
    obd_identify_vehicle,
    obd_list_profiles,
)


def register(mcp: Any) -> List[str]:
    """
    Add the diagnostic tools to the studio's existing MCP server.

    Args:
        mcp: The `FastMCP` instance `canopen_studio.mcp_server` already built.

    Returns:
        The names registered, so the caller can log or test them.
    """
    for tool in TOOLS:
        mcp.tool()(tool)
    return [tool.__name__ for tool in TOOLS]
