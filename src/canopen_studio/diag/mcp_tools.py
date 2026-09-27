"""
OBD-II diagnostic tools for the existing MCP server.

These extend `canopen_studio.mcp_server` rather than standing up a second server: one
process, one port, one set of guards. `register(mcp)` is called from there, and the tools
are plain module-level functions so they can be tested without a server at all — the same
arrangement the CAN tools already use.

**Every tool here reads, except one, and that one is off unless it is deliberately
turned on.** `obd_clear_dtcs` can clear diagnostic trouble codes, but only when
`CANOPEN_STUDIO_MCP_DIAG_WRITE` is set *in addition to* `CANOPEN_STUDIO_DIAG_WRITE`. The
two are separate on purpose: enabling writes so that a person can clear codes from the
GUI must not, by itself, hand that capability to whatever model is connected to the
server.

When it is enabled, the capability is stated everywhere it could matter — in the tool's
own description, in the result of every trouble-code read, in `obd_status()`, and on the
server's console at startup — because a model deciding to "reset the fault and try again"
would erase readiness monitors on somebody's car.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .elm327.interface import ElmDiagnosticInterface
from .elm327.ble import BleElmTransport
from .elm327.transport import DEFAULT_BAUDRATE, DEFAULT_TCP_PORT, SerialElmTransport, TcpElmTransport
from .interface import DiagnosticError, DiagnosticInterface
from .j1979.client import J1979Client, VehicleIdentity
from .j1979.dtc import DTC_MODES
from .j1979.pids import parse_key
from .j1979.polling import PidPoller, summarise as summarise_samples
from .native import NativeCanDiagnosticInterface, QueueFrameSource
from .profiles.library import ProfileLibrary, default_library, reload_default_library
from .profiles.model import ProfileError
from .profiles.resolver import ProfileMatch, ProfileResolver
from .security import WriteGate, agent_write_warning, agent_writes_enabled, clear_trouble_codes

# The session these tools act on. One at a time: a vehicle has one diagnostic link.
_session: Optional[DiagnosticInterface] = None
_client: Optional[J1979Client] = None
_match: Optional[ProfileMatch] = None
_frame_source: Optional[QueueFrameSource] = None
# What the vehicle said and which profile was asked for, kept so that reloading the
# profiles can resolve again without asking the vehicle a second time.
_identity: Optional[VehicleIdentity] = None
_manual_profile: Optional[str] = None

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


def _gate(agent: bool = False) -> WriteGate:
    """The write gate for the active profile, judged as an agent request when asked."""
    profile = _match.profile if _match is not None else None
    return WriteGate(profile, agent=agent)


def _reset_state() -> None:
    global _session, _client, _match, _frame_source, _identity, _manual_profile
    _session = None
    _client = None
    _match = None
    _frame_source = None
    _identity = None
    _manual_profile = None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def obd_connect(
    transport: str = "elm327",
    port: str = "/dev/ttyUSB0",
    baudrate: int = DEFAULT_BAUDRATE,
    host: str = "192.168.0.10",
    tcp_port: int = DEFAULT_TCP_PORT,
    ble_device: Optional[str] = None,
    protocol: str = "0",
    interface: str = "socketcan",
    channel: str = "can0",
    bitrate: int = 500000,
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Open an OBD-II diagnostic session and identify the vehicle.

    Args:
        transport: How to reach the vehicle — "elm327" for a serial or Bluetooth SPP
            adapter, "elm327_tcp" for a Wi-Fi adapter or emulator, "elm327_ble" for a
            Bluetooth Low Energy adapter (needs the `ble` extra), "native" to speak
            ISO-TP over one of the studio's own CAN adapters.
        port: Serial device for transport="elm327", e.g. /dev/ttyUSB0, /dev/rfcomm0, COM4.
        baudrate: Serial line rate. 38400 suits most adapters; try 9600 for an old board.
        host: Address for transport="elm327_tcp".
        tcp_port: TCP port for transport="elm327_tcp", 35000 by convention.
        ble_device: For transport="elm327_ble", the adapter's address or a fragment of
            its advertised name. Omit to take the first adapter that looks like one.
        protocol: ELM327 protocol number, or "0" to autodetect. Forcing the right one
            skips the search delay on the first request.
        interface: CAN adapter key for transport="native" — socketcan, slcan, pcan,
            kvaser, gs_usb, udp_multicast, virtual.
        channel: Channel for transport="native", e.g. can0 or /dev/ttyUSB0.
        bitrate: Bus bitrate for transport="native".
        profile: Vehicle profile to use, overriding automatic resolution. Omit to let
            the VIN and the supported-PID fingerprint decide.
    """
    global _session, _client, _match, _frame_source, _identity, _manual_profile

    if _session is not None:
        return {"connected": True, "message": "Already connected — call obd_disconnect() first."}

    try:
        session, source = _build_session(
            transport, port, baudrate, host, tcp_port, protocol, interface, channel, bitrate, ble_device
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
    _identity = identity
    _manual_profile = profile

    return {
        "connected": True,
        "link": session.description,
        "vehicle": identity.as_dict(),
        "profile": match.as_dict(),
        "writes": WriteGate(match.profile, agent=True).describe(),
    }


def _build_session(transport, port, baudrate, host, tcp_port, protocol, interface, channel, bitrate, ble_device=None):
    """Build the session a transport name asks for, without opening it yet."""
    kind = str(transport).strip().lower()

    if kind in ("elm327", "elm", "serial"):
        return ElmDiagnosticInterface(SerialElmTransport(port, baudrate=baudrate), protocol=protocol), None

    if kind in ("elm327_tcp", "tcp", "wifi"):
        return ElmDiagnosticInterface(TcpElmTransport(host, tcp_port), protocol=protocol), None

    if kind in ("elm327_ble", "ble", "bluetooth_le"):
        return ElmDiagnosticInterface(BleElmTransport(ble_device), protocol=protocol), None

    if kind in ("native", "can", "isotp"):
        if _app_ref is not None and getattr(_app_ref, "bus", None) is not None:
            # Borrow the bus the application already owns, and take frames from its
            # capture loop rather than opening a second reader on the same adapter.
            source = QueueFrameSource()
            return NativeCanDiagnosticInterface(_app_ref.bus, source=source), source
        return NativeCanDiagnosticInterface.open_bus(interface, channel, bitrate), None

    raise DiagnosticError(f"unknown transport {transport!r}; expected elm327, elm327_tcp, elm327_ble or native")


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
        "writes": _gate(agent=True).describe(),
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


# An agent's sampling window is bounded: the session is held for its whole length.
MAX_SAMPLE_SECONDS = 30.0


def obd_sample_pids(pids: List[str], duration_s: float = 5.0) -> Dict[str, Any]:
    """Poll live-data PIDs repeatedly for a while, and summarise how they moved.

    Where obd_read_pid takes one reading, this watches: every PID is asked over and over
    for `duration_s` seconds, and each comes back with its count, last value and, for
    numbers, min, max and mean. The measured request rate is reported too — an ELM327
    answers one request at a time, so the more PIDs, the longer each waits between
    readings, and `refresh_interval_s` says by how much.

    Args:
        pids: Mode 01 PIDs, each as "0C", "01:0C" or a profile name ("engine_speed").
        duration_s: How long to sample, at most 30 seconds.
    """
    client = _require_session()

    resolved = []
    for text in pids:
        definition = client.table.by_name(str(text).strip())
        if definition is not None:
            mode, pid = definition.mode, definition.pid
        else:
            try:
                mode, pid = _parse_pid(str(text), 1)
            except ValueError as exc:
                return {"error": str(exc)}
        if mode != 1:
            return {"error": f"{text!r} is mode {mode:02X}; only live data (mode 01) can be sampled"}
        resolved.append(pid)
    if not resolved:
        return {"error": "give at least one PID to sample"}

    duration = min(max(float(duration_s), 0.0), MAX_SAMPLE_SECONDS)
    samples = []
    poller = PidPoller(client, resolved, on_cycle=lambda cycle, _stats: samples.extend(cycle))
    stats = poller.run(duration=duration)

    result: Dict[str, Any] = {
        "duration_s": duration,
        "rate": stats.as_dict(),
        "parameters": summarise_samples(samples),
    }
    if poller.error is not None:
        result["error"] = f"the link was lost while sampling: {poller.error}"
    return result


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

    result = {
        "kind": wanted,
        "count": len(codes),
        "codes": [code.as_dict() for code in codes],
    }
    if agent_writes_enabled():
        result["clearing"] = agent_write_warning()
    else:
        result["clearing"] = (
            "Clearing codes is not available: it needs both CANOPEN_STUDIO_DIAG_WRITE=1 and "
            "CANOPEN_STUDIO_MCP_DIAG_WRITE=1. Clearing erases the readiness monitors."
        )
    return result


def obd_read_freeze_frame(frame: int = 0) -> Dict[str, Any]:
    """Read the freeze frame: the values each ECU captured when it stored a trouble code.

    It answers "what was the engine doing when the lamp came on" — speed, load, coolant
    temperature, fuel trims at that moment. An ECU with no stored code holds no frame,
    so an empty list on a healthy vehicle is the expected answer.

    Args:
        frame: The frame number. Frame 0 is the one every vehicle keeps.
    """
    client = _require_session()
    frames = client.read_freeze_frames(frame=int(frame))
    return {"frame": int(frame), "count": len(frames), "frames": [f.as_dict() for f in frames]}


def obd_read_readiness(this_cycle: bool = False) -> Dict[str, Any]:
    """Read emissions readiness: which on-board monitors have run and which have not.

    This is what a periodic inspection reads. `all_complete` is the strict reading —
    lamp off and every monitor the vehicle has run — and is not itself a pass or fail:
    some inspection regimes allow one or two incomplete monitors depending on the model
    year. Clearing trouble codes resets every monitor to incomplete.

    Args:
        this_cycle: Report the current drive cycle (PID 41) instead of the state since
            the codes were last cleared (PID 01).
    """
    client = _require_session()
    readiness = client.read_readiness(this_cycle=bool(this_cycle))
    if readiness is None:
        return {"error": "no ECU reported monitor status; is the ignition on?"}
    return readiness.as_dict()


def obd_clear_dtcs(confirm: bool = False) -> Dict[str, Any]:
    """DESTRUCTIVE. Clear stored diagnostic trouble codes on the connected vehicle.

    Do not call this to "try again" after a fault, to tidy up a reading, or on your own
    initiative. Ask the person first, every time.

    Alongside the trouble codes, mode 04 erases the freeze frame and the **readiness
    monitors**. The vehicle needs a full drive cycle — typically tens of kilometres of
    mixed driving — to rebuild those, and an emissions inspection taken before they are
    complete will fail. On a car due for a test, clearing codes can cost its owner the
    appointment. Permanent codes are not affected; only the vehicle can clear those.

    This tool is disabled unless the operator has set BOTH CANOPEN_STUDIO_DIAG_WRITE=1
    and CANOPEN_STUDIO_MCP_DIAG_WRITE=1 for the server process. When either is missing
    the call transmits nothing and explains which one is absent.

    Args:
        confirm: Must be True. Nothing is transmitted otherwise. This is a separate,
            explicit acknowledgement that the consequences above are intended.
    """
    client = _require_session()
    if _session is None:
        return {"cleared": False, "error": "no diagnostic session"}

    gate = _gate(agent=True)
    try:
        acknowledged = clear_trouble_codes(_session, gate, confirm=confirm)
    except DiagnosticError as exc:
        return {"cleared": False, "refused": True, "reason": str(exc)}

    # Read back, so the answer reflects the vehicle rather than the request.
    remaining = client.read_all_dtcs()
    return {
        "cleared": True,
        "acknowledged_by": [f"0x{ecu:X}" for ecu in acknowledged],
        "remaining_codes": [code.code for code in remaining],
        "consequence": (
            "The readiness monitors were erased with the codes. The vehicle needs a full "
            "drive cycle to rebuild them; an emissions test before then will fail."
        ),
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


def obd_reload_profiles() -> Dict[str, Any]:
    """Read the vehicle profiles from disk again, and apply them to the open session.

    For trying a profile just edited — a PID added or a formula corrected — without
    restarting the studio. The open session resolves its profile again from what the
    vehicle already said, without asking it anything. If the generic J1979 profile no
    longer loads, nothing is replaced and the previous profiles stay in use.
    """
    global _match
    try:
        library = reload_default_library()
    except ProfileError as exc:
        return {"reloaded": False, "error": str(exc), "note": "the previous profiles are still in use"}

    result: Dict[str, Any] = {"reloaded": True, "count": len(library), "errors": library.errors}
    if _client is not None and _identity is not None:
        match = ProfileResolver(library).resolve(_identity, manual=_manual_profile)
        _client.table = match.profile.table
        _client.dtc_descriptions = dict(match.profile.dtc_descriptions)
        _match = match
        result["profile"] = match.as_dict()
    return result


def obd_list_profiles() -> Dict[str, Any]:
    """List the vehicle profiles available, for choosing one by hand in obd_connect()."""
    library = _library()
    return {
        "count": len(library),
        "profiles": [profile.as_dict() for profile in library.resolved()],
        "errors": library.errors,
    }


# Every tool this module contributes.
READ_TOOLS = (
    obd_connect,
    obd_disconnect,
    obd_status,
    obd_list_supported_pids,
    obd_read_pid,
    obd_sample_pids,
    obd_read_dtcs,
    obd_read_freeze_frame,
    obd_read_readiness,
    obd_read_vin,
    obd_identify_vehicle,
    obd_list_profiles,
    obd_reload_profiles,
)

# Tools that can change the vehicle. Registered unconditionally so that the refusal is a
# clear message rather than a missing tool an agent cannot reason about, and gated at
# call time by `security.WriteGate`.
WRITE_TOOLS = (obd_clear_dtcs,)

TOOLS = READ_TOOLS + WRITE_TOOLS


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


def startup_notice() -> Optional[str]:
    """
    The line the server prints when an agent is able to write, or None when it cannot.

    Printed at startup rather than only on the first call, so that nobody discovers the
    capability by watching a model use it.
    """
    if agent_writes_enabled():
        return agent_write_warning()
    return None
