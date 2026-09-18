"""
MCP server for CAN & CANopen Studio.

Exposes CAN bus operations as tools for Claude / MCP clients.
Runs as an SSE server on http://localhost:3001.

Standalone:  uv run canopen-mcp
Integrated:  started automatically by can_gui.py in a daemon thread.

Claude Code integration:
  claude mcp add --transport sse http://localhost:3001/sse canopen-studio
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any

import can
from fastmcp import FastMCP

from can_interfaces import open_can_bus, VirtualCanopenSimulator
from canopen_stack import CANopenLayer, get_default_registry

if TYPE_CHECKING:
    pass

import os as _os

MCP_HOST = "localhost"
MCP_PORT = int(_os.environ.get("MCP_PORT", 3001))

mcp = FastMCP(
    "CANopen Studio",
    instructions=(
        "Tools to connect to a CAN bus, send and receive CAN/CANopen frames, "
        "and inspect network state. Call get_status() first to check connection."
    ),
)

# ---------------------------------------------------------------------------
# Shared state — used when running standalone (no GUI).
# When embedded in the GUI, tools delegate to the app reference instead.
# ---------------------------------------------------------------------------
_app_ref: Any = None  # set by can_gui when running integrated
_standalone_bus: can.Bus | None = None
_standalone_sim_bus: can.Bus | None = None
_standalone_simulator: VirtualCanopenSimulator | None = None
_standalone_layer: CANopenLayer | None = None
_standalone_trace: list[dict] = []
_standalone_running = False
_standalone_rx_thread: threading.Thread | None = None


def set_app(app: Any) -> None:
    """Called by can_gui to register the GUI app as the state provider."""
    global _app_ref
    _app_ref = app


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
        "message": "Connected (standalone MCP server)" if connected else "Not connected — call connect() first",
    }


@mcp.tool()
def connect(
    interface: str = "udp_multicast",
    channel: str = "239.0.0.1",
    bitrate: int = 0,
    simulate: bool = False,
) -> str:
    """Connect to a CAN bus.

    Args:
        interface: Interface type — udp_multicast, virtual, socketcan, slcan, pcan, kvaser, gs_usb.
        channel: Channel string (IP for udp_multicast, interface name for socketcan, port for slcan).
        bitrate: Bus bitrate in bps (0 for udp_multicast / virtual).
        simulate: Start the virtual CANopen simulator (nodes, SYNC, PDOs, heartbeats).
    """
    if _app_ref is not None:
        return _app_ref.connect_from_mcp(interface, channel, bitrate, simulate)

    global _standalone_bus, _standalone_sim_bus, _standalone_simulator
    global _standalone_layer, _standalone_running, _standalone_rx_thread

    if _standalone_bus:
        return "Already connected. Call disconnect() first."

    try:
        _standalone_bus = open_can_bus(interface, channel, bitrate)
        reg = get_default_registry()
        _standalone_layer = CANopenLayer(bus=_standalone_bus, registry=reg)

        if simulate:
            _standalone_sim_bus = open_can_bus(interface, channel, bitrate)
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
    global _standalone_layer, _standalone_running

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
        return "SYNC sent (0x080)"
    except Exception as exc:
        return f"Send failed: {exc}"


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
        return f"SDO read sent to node {node_id} — 0x{index:04X}:{subindex:02X}. Check get_trace() for response."
    except Exception as exc:
        return f"Send failed: {exc}"


# ---------------------------------------------------------------------------
# Entry point (standalone mode)
# ---------------------------------------------------------------------------


def start_in_thread(host: str = MCP_HOST, port: int = MCP_PORT) -> threading.Thread:
    """Start the MCP SSE server in a daemon thread (used by can_gui)."""

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        mcp.run(transport="sse", host=host, port=port, log_level="warning")

    t = threading.Thread(target=_run, daemon=True, name=f"mcp-server-{port}")
    t.start()
    return t


def main() -> None:
    """Standalone entry point: uv run canopen-mcp"""
    print(f"CANopen Studio MCP server starting on http://{MCP_HOST}:{MCP_PORT}")
    print("Claude Code: claude mcp add --transport sse "
          f"http://{MCP_HOST}:{MCP_PORT}/sse canopen-studio")
    mcp.run(transport="sse", host=MCP_HOST, port=MCP_PORT)


if __name__ == "__main__":
    main()
