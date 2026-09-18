# AI Integration (MCP & A2A)

CAN & CANopen Studio exposes its bus to AI agents through two protocol servers. Both start
automatically in daemon threads when the GUI launches — there is nothing to configure to get
them running.

```
MCP server: http://localhost:3001/sse
A2A server: http://localhost:8765/.well-known/agent.json
```

An agent can then read the live trace, inspect discovered nodes and telemetry, transmit frames,
drive the NMT state machine, read the object dictionary, and mirror the bus onto the network.

!!! info "Two protocols, one application state"
    Both servers act on the same running GUI: what an agent connects or transmits shows up in the
    window, and what you do in the window is visible to the agent. When a server runs standalone
    (without the GUI) it keeps its own bus and trace instead.

---

## MCP (Model Context Protocol)

### Registering with Claude Code

```bash
claude mcp add --transport sse canopen-studio http://localhost:3001/sse
```

The server name comes before the URL. Start the GUI first, otherwise the registration has nothing
to connect to.

### Running standalone

Without the GUI, the MCP server owns its own bus and capture loop:

```bash
uv run canopen-mcp
```

Standalone mode has no GUI capture loop, so the bridge tools are unavailable there.

### Tool reference

| Tool | Purpose |
|---|---|
| `get_status()` | Connection state, active interface, channel, bitrate, statistics, bridge state |
| `connect(interface, channel, bitrate, simulate, hop_limit)` | Open a bus, optionally starting the virtual simulator |
| `disconnect()` | Close the bus and stop the simulator |
| `get_trace(n)` | The last N frames, with their decoded CANopen meaning |
| `get_network_state()` | Discovered nodes, NMT states and live telemetry |
| `send_frame(can_id, data, extended)` | Transmit an arbitrary frame |
| `send_nmt(command, node_id)` | `start`, `stop`, `pre_operational`, `reset_node`, `reset_communication` |
| `send_sync()` | One SYNC pulse (`0x080`) |
| `sdo_read(node_id, index, subindex)` | Expedited SDO read; the reply appears in `get_trace()` |
| `bridge_start(channel, hop_limit, allow_inject)` | Mirror the captured bus onto a multicast group |
| `bridge_stop()` | Stop mirroring, leaving the bus connected |

### A typical session

```python
get_status()  # check what is connected
connect(interface="udp_multicast", channel="239.0.0.1", simulate=True)
send_nmt(command="start", node_id=0)  # bring all nodes Operational
get_network_state()  # read back RPM, torque, temperatures
get_trace(n=20)  # inspect decoded frames
```

---

## A2A (Agent-to-Agent)

The A2A server publishes an agent card at `/.well-known/agent.json` and accepts free-text
messages over JSON-RPC, dispatching them to the same actions. It advertises three skills:

| Skill | Covers |
|---|---|
| `can-monitor` | Reading the trace and the network state |
| `can-control` | Connecting, sending frames, NMT commands and SYNC |
| `canopen-sdo` | Object dictionary access over SDO |

Messages are matched on intent, in English or French — `status`, `trace` / `trames`,
`network` / `réseau`, `connect udp_multicast 239.0.0.1 simulate`, `send 0x701 05`, `sync`,
`sdo read`, `disconnect`.

---

## Controlling the Bridge from an Agent

The [bridge](hardware.md#bridging-a-real-can-bus-onto-the-network) mirrors the bus you are
physically connected to onto a UDP multicast group, so remote machines observe a real device as
if they were wired to it. An agent arms and disarms it:

```python
connect(interface="pcan", channel="PCAN_USBBUS1", bitrate=500000)
bridge_start(channel="239.0.0.1")  # read-only mirroring
get_status()  # bridge counters
bridge_stop()
```

`get_status()` reports the bridge as a nested object:

```json
{
  "bridge": {
    "running": true,
    "channel": "239.0.0.1",
    "allow_inject": false,
    "stats": {"forwarded": 1022, "injected": 0, "suppressed": 0,
              "suppressed_inject": 0, "errors": 0}
  }
}
```

Passing `allow_inject=True` opens the return path, replaying network frames onto the real bus.

!!! danger "Injection writes to real hardware"
    With injection enabled, anyone on the multicast group can transmit on your physical CAN bus,
    which means commanding the connected device. On a drive this moves a motor. Enable it only on
    a trusted network and with the equipment in a safe state.

---

## Running Several Instances

Each instance needs its own ports. Three environment variables cover it:

| Variable | Default | Purpose |
|---|---|---|
| `CANOPEN_STUDIO_INSTANCE` | *(empty)* | Name shown in the window title and reported in `get_status()` |
| `MCP_PORT` | `3001` | MCP SSE port |
| `A2A_PORT` | `8765` | A2A HTTP port |

```bash
# Gateway: holds the CAN adapter and mirrors it onto the network
CANOPEN_STUDIO_INSTANCE=gateway uv run canopen-studio

# Observer: joins the same multicast group, on its own ports
CANOPEN_STUDIO_INSTANCE=receiver MCP_PORT=3002 A2A_PORT=8766 uv run canopen-studio
```

Register the second instance alongside the first:

```bash
claude mcp add --transport sse canopen-receiver http://localhost:3002/sse
```

---

## Security

Both servers bind to `localhost` and have **no authentication**. They are reachable only from the
machine running the application, which is deliberate: the tools transmit on a CAN bus, and on real
hardware that commands equipment.

Exposing them on a network interface — through a reverse proxy or an SSH tunnel — hands bus
control to whoever reaches the port. Put authentication in front of them if you do, and keep in
mind that the bridge's injection path has the same reach.
