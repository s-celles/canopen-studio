"""
MCP server for CAN & CANopen Studio.

Exposes CAN bus operations as tools for Claude / MCP clients.
Runs as an SSE server on http://localhost:3001.

Standalone:  uv run canopen-mcp
Integrated:  started automatically by gui.py in a daemon thread.

Claude Code integration:
  claude mcp add --transport sse canopen-studio http://localhost:3001/sse
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any

import can
import uvicorn
from fastmcp import FastMCP

from canopen_studio import agent_security as _sec
from canopen_studio.diag import mcp_tools as _diag_tools
from canopen_studio.interfaces import open_can_bus, VirtualCanopenSimulator
from canopen_studio.stack import CANopenLayer, get_default_registry

if TYPE_CHECKING:
    pass

import os as _os

MCP_HOST = "localhost"
MCP_PORT = int(_os.environ.get("MCP_PORT", 3001))


def is_enabled() -> bool:
    """Whether the MCP server may start. On unless CANOPEN_STUDIO_MCP disables it."""
    return _sec.env_flag("CANOPEN_STUDIO_MCP", default=True)


mcp = FastMCP(
    "CANopen Studio",
    instructions=(
        "Tools to connect to a CAN bus, send and receive CAN/CANopen frames, "
        "and inspect network state. Call get_status() first to check connection. "
        "The obd_* tools drive a vehicle OBD-II session over an ELM327 or a native "
        "CAN adapter; they read only, and call obd_connect() first."
    ),
)

# OBD-II diagnostics extend this server rather than standing up a second one: one
# process, one port, one set of guards. The tools registered are read-only; writing to a
# vehicle goes through canopen_studio.diag.security, which refuses agents by design.
DIAGNOSTIC_TOOLS = _diag_tools.register(mcp)

# ---------------------------------------------------------------------------
# Shared state — used when running standalone (no GUI).
# When embedded in the GUI, tools delegate to the app reference instead.
# ---------------------------------------------------------------------------
_app_ref: Any = None  # set by canopen_studio.gui when running integrated
_standalone_bus: can.Bus | None = None
_standalone_sim_bus: can.Bus | None = None
_standalone_simulator: VirtualCanopenSimulator | None = None
_standalone_layer: CANopenLayer | None = None
_standalone_trace: list[dict] = []
_standalone_tx_count = 0
_standalone_running = False
_standalone_rx_thread: threading.Thread | None = None


def set_app(app: Any) -> None:
    """Called by canopen_studio.gui to register the GUI app as the state provider."""
    global _app_ref
    _app_ref = app
    # A native diagnostic session must borrow the bus the capture loop already owns,
    # rather than opening a second reader on the same adapter.
    _diag_tools.set_app(app)


def _is_connected() -> bool:
    if _app_ref is not None:
        return _app_ref.bus is not None
    return _standalone_bus is not None


def _get_bus() -> can.Bus | None:
    if _app_ref is not None:
        return _app_ref.bus
    return _standalone_bus


def _get_layer() -> CANopenLayer | None:
    if _app_ref is not None:
        return _app_ref.canopen_layer
    return _standalone_layer


def _get_trace(n: int) -> list[dict]:
    if _app_ref is not None:
        return _app_ref.get_trace_json(n)
    return _standalone_trace[-n:]


def _count_tx() -> None:
    """Count a transmitted frame so MCP sends show up in the same statistics as GUI sends."""
    global _standalone_tx_count
    if _app_ref is not None:
        _app_ref.stats["total_tx"] += 1
    else:
        _standalone_tx_count += 1


def _get_network() -> dict:
    if _app_ref is not None:
        return _app_ref.get_network_dict()
    return {"discovered_nodes": {}, "telemetry": {}, "stats": {}}


def _standalone_rx_loop() -> None:
    global _standalone_running
    while _standalone_running and _standalone_bus:
        try:
            msg = _standalone_bus.recv(timeout=0.08)
            if not msg:
                continue
            layer = _standalone_layer
            if layer:
                parsed = layer.process_can_message(msg)
                _standalone_trace.append(
                    {
                        "id": f"0x{msg.arbitration_id:03X}",
                        "type": "EXT" if msg.is_extended_id else "STD",
                        "dlc": msg.dlc,
                        "data": msg.data.hex(" ").upper(),
                        "decoded": parsed.decoded_info,
                    }
                )
                if len(_standalone_trace) > 500:
                    _standalone_trace.pop(0)
        except Exception:
            break


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_status() -> dict:
    """Return the current connection status and active interface details."""
    if _app_ref is not None:
        return _app_ref.get_status_dict()
    connected = _standalone_bus is not None
    return {
        "connected": connected,
        "mode": "standalone",
        "stats": {"total_tx": _standalone_tx_count},
        "message": "Connected (standalone MCP server)" if connected else "Not connected — call connect() first",
    }


@mcp.tool()
def connect(
    interface: str = "udp_multicast",
    channel: str = "239.0.0.1",
    bitrate: int = 0,
    simulate: bool = False,
    hop_limit: int | None = None,
) -> str:
    """Connect to a CAN bus.

    Args:
        interface: Interface type — udp_multicast, virtual, socketcan, slcan, pcan, kvaser, gs_usb.
        channel: Channel string (IP for udp_multicast, interface name for socketcan, port for slcan).
        bitrate: Bus bitrate in bps (0 for udp_multicast / virtual).
        simulate: Start the virtual CANopen simulator (nodes, SYNC, PDOs, heartbeats).
        hop_limit: udp_multicast only — IP hop limit (TTL). 1 (the default) keeps frames on
            the local network segment; raise it to reach machines on other subnets.
    """
    if _app_ref is not None:
        return _app_ref.connect_from_mcp(interface, channel, bitrate, simulate, hop_limit=hop_limit)

    global _standalone_bus, _standalone_sim_bus, _standalone_simulator
    global _standalone_layer, _standalone_running, _standalone_rx_thread

    if _standalone_bus:
        return "Already connected. Call disconnect() first."

    try:
        _standalone_bus = open_can_bus(interface, channel, bitrate, hop_limit=hop_limit)
        reg = get_default_registry()
        _standalone_layer = CANopenLayer(bus=_standalone_bus, registry=reg)

        if simulate:
            _standalone_sim_bus = open_can_bus(interface, channel, bitrate, hop_limit=hop_limit)
            _standalone_simulator = VirtualCanopenSimulator(_standalone_sim_bus)
            _standalone_simulator.start()

        _standalone_running = True
        _standalone_rx_thread = threading.Thread(target=_standalone_rx_loop, daemon=True)
        _standalone_rx_thread.start()
        sim_note = " + simulator" if simulate else ""
        return f"Connected to {interface} [{channel}]{sim_note}"
    except Exception as exc:
        return f"Connection failed: {exc}"


@mcp.tool()
def disconnect() -> str:
    """Disconnect from the CAN bus and stop the simulator if running."""
    if _app_ref is not None:
        return _app_ref.disconnect_from_mcp()

    global _standalone_bus, _standalone_sim_bus, _standalone_simulator
    global _standalone_layer, _standalone_running, _standalone_tx_count

    if not _standalone_bus:
        return "Not connected."

    _standalone_running = False
    if _standalone_simulator:
        _standalone_simulator.stop()
        _standalone_simulator = None
    if _standalone_sim_bus:
        try:
            _standalone_sim_bus.shutdown()
        except Exception:
            pass
        _standalone_sim_bus = None
    try:
        _standalone_bus.shutdown()
    except Exception:
        pass
    _standalone_bus = None
    _standalone_layer = None
    _standalone_trace.clear()
    _standalone_tx_count = 0
    return "Disconnected."


@mcp.tool()
def get_trace(n: int = 50) -> list[dict]:
    """Return the last N received CAN frames.

    Each frame has keys: id, type (STD/EXT), dlc, data (hex), decoded (CANopen description).
    """
    return _get_trace(n)


@mcp.tool()
def send_frame(can_id: int, data: list[int], extended: bool = False) -> str:
    """Send a raw CAN frame on the bus.

    Args:
        can_id: CAN arbitration ID (decimal or 0x-prefixed hex as int).
        data: Payload bytes as a list of integers 0-255 (max 8 bytes).
        extended: True for 29-bit extended frame, False for 11-bit standard.
    """
    bus = _get_bus()
    if not bus:
        return "Not connected — call connect() first."
    if len(data) > 8:
        return "Payload too long — maximum 8 bytes."
    try:
        msg = can.Message(arbitration_id=can_id, data=bytes(data), is_extended_id=extended)
        bus.send(msg)
        _count_tx()
        return f"Sent 0x{can_id:03X} [{' '.join(f'{b:02X}' for b in data)}]"
    except Exception as exc:
        return f"Send failed: {exc}"


@mcp.tool()
def send_nmt(command: str, node_id: int = 0) -> str:
    """Send a CANopen NMT command to a node.

    Args:
        command: One of start, stop, pre_operational, reset_node, reset_communication.
        node_id: Target node ID (0 = broadcast to all nodes).
    """
    cmd_map = {
        "start": 0x01,
        "stop": 0x02,
        "pre_operational": 0x80,
        "reset_node": 0x81,
        "reset_communication": 0x82,
    }
    bus = _get_bus()
    if not bus:
        return "Not connected — call connect() first."
    cmd_byte = cmd_map.get(command.lower())
    if cmd_byte is None:
        return f"Unknown command '{command}'. Valid: {', '.join(cmd_map)}"
    try:
        msg = can.Message(arbitration_id=0x000, is_extended_id=False, data=[cmd_byte, node_id])
        bus.send(msg)
        _count_tx()
        target = f"node {node_id}" if node_id else "all nodes"
        return f"NMT {command} sent to {target}"
    except Exception as exc:
        return f"Send failed: {exc}"


@mcp.tool()
def send_sync() -> str:
    """Send a CANopen SYNC frame (0x080, empty payload) to trigger synchronous PDO exchange."""
    bus = _get_bus()
    if not bus:
        return "Not connected — call connect() first."
    try:
        bus.send(can.Message(arbitration_id=0x080, is_extended_id=False, data=[]))
        _count_tx()
        return "SYNC sent (0x080)"
    except Exception as exc:
        return f"Send failed: {exc}"


@mcp.tool()
def bridge_start(
    channel: str = "239.0.0.1",
    hop_limit: int | None = None,
    allow_inject: bool = False,
) -> str:
    """Mirror the currently captured CAN bus onto a UDP multicast group.

    Lets remote machines observe a real device (a drive, an inverter) connected to this
    machine's CAN adapter, as if they were wired to the same bus.

    Args:
        channel: Multicast address the traffic is republished on.
        hop_limit: IP hop limit (TTL) of the mirrored datagrams. 1 (the default) keeps them
            on the local segment; raise it to reach other subnets.
        allow_inject: Also replay frames received from the network onto the real bus.
            This writes to real hardware and lets remote clients command the device, so it
            is off by default.
    """
    if _app_ref is None:
        return "Bridging requires the GUI — the standalone MCP server has no capture loop."
    return _app_ref.start_bridge(channel, hop_limit=hop_limit, allow_inject=allow_inject)


@mcp.tool()
def bridge_stop() -> str:
    """Stop mirroring the captured bus onto the network. The bus stays connected."""
    if _app_ref is None:
        return "Bridging requires the GUI — the standalone MCP server has no capture loop."
    return _app_ref.stop_bridge()


@mcp.tool()
def get_network_state() -> dict:
    """Return discovered CANopen nodes and live telemetry (RPM, torque, temperature)."""
    return _get_network()


@mcp.tool()
def sdo_read(node_id: int, index: int, subindex: int = 0) -> str:
    """Send a CANopen SDO expedited read request (fire-and-forget).

    The response will appear in get_trace() as a frame with id 0x5{node_id:02X}.

    Args:
        node_id: Target node ID (1-127).
        index: Object dictionary index (e.g. 0x1000 for Device Type).
        subindex: Object dictionary subindex (default 0).
    """
    layer = _get_layer()
    if not layer:
        return "Not connected — call connect() first."
    try:
        layer.send_sdo_read(node_id, index, subindex)
        _count_tx()
        return f"SDO read sent to node {node_id} — 0x{index:04X}:{subindex:02X}. Check get_trace() for response."
    except Exception as exc:
        return f"Send failed: {exc}"


# ---------------------------------------------------------------------------
# Entry point (standalone mode)
# ---------------------------------------------------------------------------


def build_app():
    """
    Build the guarded SSE application.

    fastmcp 4.0.5 accepts host_origin_protection but silently drops it on the SSE transport:
    HostOriginGuardMiddleware is never installed, with no warning, although the same call
    with transport="http" installs it. The project therefore mounts its own guard rather
    than trusting a setting that does nothing. Without it a web page the user visits can
    reach these tools, and they transmit on a CAN bus.
    """
    app = mcp.http_app(transport="sse")
    app.add_middleware(_sec.LocalOnlyMiddleware)
    return app


def start_in_thread(host: str = MCP_HOST, port: int = MCP_PORT) -> threading.Thread:
    """Start the MCP SSE server in a daemon thread (used by canopen_studio.gui)."""

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        uvicorn.run(build_app(), host=host, port=port, log_level="warning", loop="asyncio")

    t = threading.Thread(target=_run, daemon=True, name=f"mcp-server-{port}")
    t.start()
    return t


def main() -> None:
    """Standalone entry point: uv run canopen-mcp"""
    print(f"CANopen Studio MCP server starting on http://{MCP_HOST}:{MCP_PORT}")
    print(f"Claude Code: claude mcp add --transport sse canopen-studio http://{MCP_HOST}:{MCP_PORT}/sse")
    uvicorn.run(build_app(), host=MCP_HOST, port=MCP_PORT)


if __name__ == "__main__":
    main()
