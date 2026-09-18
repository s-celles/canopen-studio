"""
A2A (Agent-to-Agent) server for CAN & CANopen Studio.

Implements the Google A2A protocol spec (JSON-RPC 2.0 over HTTP) so that
external AI agents can interact with the CAN bus through the studio.

Standalone:  uv run canopen-a2a
Integrated:  started automatically by can_gui.py in a daemon thread.

Protocol:
  GET  /.well-known/agent.json  — agent card (skills & capabilities)
  POST /                        — JSON-RPC 2.0 (message/send)
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from typing import TYPE_CHECKING, Any

import can
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from can_interfaces import open_can_bus, VirtualCanopenSimulator
from canopen_stack import CANopenLayer, get_default_registry

if TYPE_CHECKING:
    pass

import os as _os

A2A_HOST = "localhost"
A2A_PORT = int(_os.environ.get("A2A_PORT", 8765))

# ---------------------------------------------------------------------------
# Shared state — same pattern as can_mcp_server.py
# ---------------------------------------------------------------------------
_app_ref: Any = None
_standalone_bus: can.Bus | None = None
_standalone_sim_bus: can.Bus | None = None
_standalone_simulator: VirtualCanopenSimulator | None = None
_standalone_layer: CANopenLayer | None = None
_standalone_trace: list[dict] = []
_standalone_running = False


def set_app(app: Any) -> None:
    global _app_ref
    _app_ref = app


def _get_bus() -> can.Bus | None:
    return _app_ref.bus if _app_ref else _standalone_bus


def _get_layer() -> CANopenLayer | None:
    return _app_ref.canopen_layer if _app_ref else _standalone_layer


def _get_trace(n: int) -> list[dict]:
    if _app_ref:
        return _app_ref.get_trace_json(n)
    return _standalone_trace[-n:]


def _get_network() -> dict:
    if _app_ref:
        return _app_ref.get_network_dict()
    return {"discovered_nodes": {}, "telemetry": {}, "stats": {}}


def _is_connected() -> bool:
    return _get_bus() is not None


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
# Agent logic — maps natural-language intent to CAN actions
# ---------------------------------------------------------------------------

def _handle_message(text: str) -> str:
    """Dispatch a free-text message from an A2A client to the appropriate action."""
    text_lower = text.lower().strip()

    # status / connection check
    if any(k in text_lower for k in ("status", "connected", "état", "connection")):
        if _app_ref:
            s = _app_ref.get_status_dict()
        else:
            s = {"connected": _is_connected(), "mode": "standalone"}
        return json.dumps(s, indent=2)

    # trace / monitor
    if any(k in text_lower for k in ("trace", "frames", "trames", "monitor", "reçu", "received")):
        n = 20
        frames = _get_trace(n)
        if not frames:
            return "No frames received yet. Make sure the bus is connected and active."
        lines = [f"{f['id']}  {f['type']}  [{f['data']}]  {f.get('decoded', '')}" for f in frames]
        return f"Last {len(frames)} frames:\n" + "\n".join(lines)

    # network state
    if any(k in text_lower for k in ("network", "réseau", "nodes", "nœuds", "telemetry", "rpm", "torque")):
        net = _get_network()
        return json.dumps(net, indent=2, default=str)

    # connect
    if text_lower.startswith("connect"):
        parts = text_lower.split()
        interface = parts[1] if len(parts) > 1 else "udp_multicast"
        channel = parts[2] if len(parts) > 2 else "239.0.0.1"
        bitrate = int(parts[3]) if len(parts) > 3 else 0
        simulate = "simulate" in text_lower or "sim" in text_lower
        if _app_ref:
            return _app_ref.connect_from_mcp(interface, channel, bitrate, simulate)
        return _standalone_connect(interface, channel, bitrate, simulate)

    # disconnect
    if text_lower.startswith("disconnect"):
        if _app_ref:
            return _app_ref.disconnect_from_mcp()
        return _standalone_disconnect()

    # NMT commands
    for cmd in ("start", "stop", "pre_operational", "reset_node", "reset_communication"):
        if cmd in text_lower or cmd.replace("_", " ") in text_lower:
            node_id = 0
            for word in text_lower.split():
                if word.isdigit():
                    node_id = int(word)
                    break
            return _send_nmt(cmd, node_id)

    # SYNC
    if "sync" in text_lower:
        return _send_sync()

    # send frame  (e.g. "send 0x701 05" or "send frame 701 5")
    if "send" in text_lower:
        tokens = text_lower.replace(",", " ").split()
        hex_tokens = [t for t in tokens if t.startswith("0x") or (len(t) >= 2 and all(c in "0123456789abcdef" for c in t))]
        if hex_tokens:
            try:
                can_id = int(hex_tokens[0], 16)
                data = [int(b, 16) for b in hex_tokens[1:]]
                return _send_frame(can_id, data)
            except ValueError:
                pass

    # SDO read  (e.g. "sdo read node 1 index 0x1000")
    if "sdo" in text_lower and ("read" in text_lower or "lire" in text_lower):
        tokens = text_lower.split()
        node_id, index, subindex = 1, 0x1000, 0
        for i, t in enumerate(tokens):
            if t in ("node", "nœud") and i + 1 < len(tokens):
                node_id = int(tokens[i + 1])
            if t in ("index", "idx", "0x1000") and i + 1 < len(tokens):
                try:
                    index = int(tokens[i + 1], 16)
                except ValueError:
                    pass
            if t in ("sub", "subindex") and i + 1 < len(tokens):
                subindex = int(tokens[i + 1])
        layer = _get_layer()
        if not layer:
            return "Not connected."
        try:
            layer.send_sdo_read(node_id, index, subindex)
            return f"SDO read sent to node {node_id} — 0x{index:04X}:{subindex:02X}. Response will appear in trace."
        except Exception as exc:
            return f"SDO read failed: {exc}"

    return (
        "Available commands:\n"
        "  status — connection status\n"
        "  connect [interface] [channel] [bitrate] [simulate] — connect to bus\n"
        "  disconnect\n"
        "  trace — last received frames\n"
        "  network — discovered nodes & telemetry\n"
        "  send [0xID] [bytes...] — send CAN frame\n"
        "  start/stop/pre_operational/reset_node [node_id] — NMT commands\n"
        "  sync — send SYNC frame\n"
        "  sdo read node [N] index [0xXXXX] — SDO read request\n"
    )


def _send_nmt(command: str, node_id: int) -> str:
    cmd_map = {"start": 0x01, "stop": 0x02, "pre_operational": 0x80, "reset_node": 0x81, "reset_communication": 0x82}
    bus = _get_bus()
    if not bus:
        return "Not connected."
    cmd_byte = cmd_map.get(command, 0x01)
    bus.send(can.Message(arbitration_id=0x000, is_extended_id=False, data=[cmd_byte, node_id]))
    return f"NMT {command} sent to node {node_id if node_id else 'all'}"


def _send_sync() -> str:
    bus = _get_bus()
    if not bus:
        return "Not connected."
    bus.send(can.Message(arbitration_id=0x080, is_extended_id=False, data=[]))
    return "SYNC sent (0x080)"


def _send_frame(can_id: int, data: list[int]) -> str:
    bus = _get_bus()
    if not bus:
        return "Not connected."
    bus.send(can.Message(arbitration_id=can_id, data=bytes(data), is_extended_id=False))
    return f"Sent 0x{can_id:03X} [{' '.join(f'{b:02X}' for b in data)}]"


def _standalone_connect(interface: str, channel: str, bitrate: int, simulate: bool) -> str:
    global _standalone_bus, _standalone_sim_bus, _standalone_simulator
    global _standalone_layer, _standalone_running
    if _standalone_bus:
        return "Already connected."
    try:
        _standalone_bus = open_can_bus(interface, channel, bitrate)
        _standalone_layer = CANopenLayer(bus=_standalone_bus, registry=get_default_registry())
        if simulate:
            _standalone_sim_bus = open_can_bus(interface, channel, bitrate)
            _standalone_simulator = VirtualCanopenSimulator(_standalone_sim_bus)
            _standalone_simulator.start()
        _standalone_running = True
        threading.Thread(target=_standalone_rx_loop, daemon=True).start()
        return f"Connected to {interface} [{channel}]" + (" + simulator" if simulate else "")
    except Exception as exc:
        return f"Connection failed: {exc}"


def _standalone_disconnect() -> str:
    global _standalone_bus, _standalone_sim_bus, _standalone_simulator, _standalone_layer, _standalone_running
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


# ---------------------------------------------------------------------------
# FastAPI application — A2A JSON-RPC 2.0
# ---------------------------------------------------------------------------

app = FastAPI(title="CANopen Studio A2A Agent", docs_url=None, redoc_url=None)

AGENT_CARD = {
    "name": "CANopen Studio",
    "description": (
        "AI agent interface for the CAN & CANopen Studio. "
        "Connects to real or virtual CAN buses, sends/receives frames, "
        "monitors CANopen nodes and telemetry."
    ),
    "url": f"http://{A2A_HOST}:{A2A_PORT}",
    "version": "1.0.0",
    "capabilities": {
        "streaming": False,
        "pushNotifications": False,
        "stateTransitionHistory": False,
    },
    "skills": [
        {
            "id": "can-monitor",
            "name": "CAN Monitor",
            "description": "Read CAN trace and network state (discovered nodes, telemetry)",
            "tags": ["can", "canopen", "monitor", "trace"],
            "examples": ["trace", "network state", "show discovered nodes"],
        },
        {
            "id": "can-control",
            "name": "CAN Control",
            "description": "Connect to a bus, send CAN frames, NMT commands and SYNC",
            "tags": ["can", "canopen", "send", "nmt", "control"],
            "examples": [
                "connect udp_multicast 239.0.0.1 simulate",
                "send 0x701 05",
                "start node 1",
                "sync",
            ],
        },
        {
            "id": "canopen-sdo",
            "name": "CANopen SDO",
            "description": "Read and write CANopen object dictionary entries via SDO",
            "tags": ["canopen", "sdo", "object-dictionary"],
            "examples": ["sdo read node 1 index 0x1000", "sdo read node 2 index 0x1008"],
        },
    ],
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain"],
}


@app.get("/.well-known/agent.json")
async def agent_card() -> JSONResponse:
    return JSONResponse(AGENT_CARD)


@app.post("/")
async def jsonrpc_endpoint(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )

    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    if method == "message/send":
        message = params.get("message", {})
        parts = message.get("parts", [])
        text = " ".join(p.get("text", "") for p in parts if p.get("kind") == "text").strip()
        if not text:
            text = str(parts)

        result_text = _handle_message(text)

        task_id = str(uuid.uuid4())
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "id": task_id,
                    "contextId": params.get("contextId", task_id),
                    "status": {"state": "completed"},
                    "artifacts": [
                        {
                            "artifactId": str(uuid.uuid4()),
                            "parts": [{"kind": "text", "text": result_text}],
                        }
                    ],
                },
            }
        )

    if method == "tasks/get":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": "Tasks are ephemeral in this implementation"},
            }
        )

    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}},
        status_code=404,
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def start_in_thread(host: str = A2A_HOST, port: int = A2A_PORT) -> threading.Thread:
    """Start the A2A HTTP server in a daemon thread (used by can_gui)."""

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        uvicorn.run(app, host=host, port=port, log_level="warning", loop="asyncio")

    t = threading.Thread(target=_run, daemon=True, name=f"a2a-server-{port}")
    t.start()
    return t


def main() -> None:
    """Standalone entry point: uv run canopen-a2a"""
    print(f"CANopen Studio A2A server starting on http://{A2A_HOST}:{A2A_PORT}")
    print(f"Agent card: http://{A2A_HOST}:{A2A_PORT}/.well-known/agent.json")
    uvicorn.run(app, host=A2A_HOST, port=A2A_PORT)


if __name__ == "__main__":
    main()
