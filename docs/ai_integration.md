# AI Integration (MCP & A2A)

CAN & CANopen Studio exposes its bus to AI agents through two protocol servers.

**MCP starts with the GUI. A2A does not** — it is opt-in, for the reasons in
[Why A2A is opt-in](#why-a2a-is-opt-in).

Since 0.6.1 both servers are an optional `servers` extra rather than a hard dependency:
they are the heavy half of an installation — 61 MB compressed against 33 MB without them —
and a bench does not need an agent. Where they are absent the studio imports them inside a
`try`/`except ImportError`, runs with `_SERVERS_AVAILABLE = False`, and starts neither.

A development checkout has them anyway: they are in the `dev` group, so `uv sync` installs
them and their tests keep running in CI. An installed copy needs the extra —
`pip install canopen-studio[servers]`, or `uv sync --extra servers`.

```bash
uv run canopen-studio                          # MCP only
CANOPEN_STUDIO_A2A=1 uv run canopen-studio     # MCP + A2A
CANOPEN_STUDIO_MCP=0 uv run canopen-studio     # neither
```

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
| `get_latency_stats()` | Current RTT (min/avg/max) and SYNC jitter statistics |
| `ping_bus(timeout)` | Send a CAN ping (`0x7E0`) and measure the round-trip time |
| `bridge_start(channel, hop_limit, allow_inject)` | Mirror the captured bus onto a multicast group |
| `bridge_stop()` | Stop mirroring, leaving the bus connected |

Ten further `obd_*` tools sit on the same server — one process, one port, one set of
guards — for vehicle diagnostics. Nine of them read; `obd_clear_dtcs` can change the
vehicle and passes a gate of its own. They are documented with the rest of the
diagnostics in [OBD-II Vehicle Diagnostics](obd.md#mcp-tool-reference).

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
| `CANOPEN_STUDIO_MCP` | on | Set to `0` to keep the MCP server from starting |
| `CANOPEN_STUDIO_A2A` | off | Set to `1` to start the A2A server |
| `CANOPEN_STUDIO_ALLOWED_ORIGINS` | *(empty)* | Comma-separated `Origin` values to accept |

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

These servers transmit on a CAN bus. A request reaching one of them can command real equipment,
so binding to `localhost` is necessary but not sufficient.

### The web page problem

Listening on loopback keeps other machines out. It does not keep a *web page* out: any site the
user visits can POST to `http://localhost:...` from their browser. The attacker cannot read the
reply — the browser blocks that — but the side effect has already happened.

Both servers guard against this with two header checks:

- **Origin** — a browser always attaches it to a cross-origin POST and a page cannot forge it,
  while command-line clients and MCP agents send none. Any request carrying an `Origin` is
  refused with 403. Set `CANOPEN_STUDIO_ALLOWED_ORIGINS` to a comma-separated list if you have a
  front-end that genuinely needs one.
- **Host** — a DNS rebinding attack points an attacker-controlled name at `127.0.0.1`, after
  which the page is same-origin and may send no `Origin` at all. The `Host` header still names
  the attacker's domain, so a non-loopback `Host` is refused.

### Why A2A is opt-in

The two servers are not equally exposed, which is why they have different defaults.

| | MCP | A2A |
|---|---|---|
| Accepts `text/plain` | no | yes |
| CORS preflight required | yes | no |
| Session required | yes, a 128-bit id read from the SSE stream | none |
| Reachable from a web page | no | **yes, before the guard** |

MCP has two independent barriers of its own: it rejects anything but `application/json`, which
forces a preflight the browser cannot complete, and it needs a session id that only the SSE
stream hands out — a stream a cross-origin page cannot read. A2A has neither: it is stateless
and accepts a simple request, so a single POST from any page reached the bus.

That gap is why A2A stays off unless you ask for it, while MCP starts with the application.

### What is still not covered

There is no authentication. Any process running as any user on this machine can reach either
server while it is up — the header checks stop browsers, not local programs. On a shared machine,
run with `CANOPEN_STUDIO_MCP=0` when you are not using an agent.

Exposing either server beyond loopback — a reverse proxy, an SSH tunnel — hands bus control to
whoever reaches the port, and the bridge's injection path has the same reach.
