# Architecture

CAN & CANopen Studio is **one protocol engine behind two front ends**, plus a set of
agent-facing servers. Knowing which piece does what explains most of the behaviour you
will see — including why the graphical studio and the native front end decode a frame
identically, and why some features are noticeably faster than others.

---

## The shape of the project

```
                  crates/canopen-core   (Rust: frames, CiA 301/402, OBD-II, transport)
                            |
            +---------------+----------------+
            |               |                |
      pyo3_bindings    canopen-gui      canopen-cli
            |            (Slint)        (sniff, simulate, bench)
    src/canopen_studio
      (Tkinter studio,
       MCP / A2A servers)
```

| Path | Contents |
|---|---|
| `src/canopen_studio/` | The Python package: the Tkinter studio (`gui.py`), the command-line sniffer, the MCP and A2A servers, OBD-II diagnostics (`diag/`) and the protocol decoders (`stack/`) |
| `crates/canopen-core/` | The Rust engine: frames, SDO, NMT, PDO, EDS, ISO-TP, OBD-II, telemetry, UDP transport, the virtual simulator, and the PyO3 bindings |
| `crates/canopen-cli/` | The Rust command-line tool: sniff, simulate, echo, benchmarks |
| `crates/canopen-gui/` | The native front end, built with [Slint](https://slint.dev) |

---

## The Rust engine

`canopen-core` holds everything that can be tested without a window. It is compiled into
the Python package as the `canopen_core` extension module, linked into the native front
end, and driven directly by the CLI — so all three agree on what a frame means by
construction rather than by convention.

| Module | Responsibility |
|---|---|
| `frame.rs` | `CanFrame`: an 11/29-bit identifier and a fixed `[u8; 8]`, so decoding allocates nothing. Converts to the `python-can` MessagePack wire format and to a 24-byte compact one |
| `canopen.rs` | COB-ID classification, `NmtState`, `CanopenService` |
| `nmt.rs` | `NmtMaster`: builds commands, consumes heartbeats, tracks nodes and their timeouts |
| `sdo.rs` | Expedited and segmented SDO transfers, abort code decoding |
| `pdo.rs` | Signal definitions, bit extraction, PDO mappings |
| `eds.rs` | EDS (CiA 306) parsing; can synthesise a `PdoMapping` from a mapping object |
| `telemetry.rs` | `DriveTelemetry` and `Cia402State`: folds SEVCON TPDOs and CiA 402 statuswords into one snapshot |
| `obd2.rs` | Mode 01 requests, responses and PID decoding |
| `isotp.rs`, `isotp_manager.rs` | ISO 15765-2 segmentation and reassembly |
| `latency.rs` | `LatencyTracker` and the CAN ping helpers on `0x7E0` / `0x7E1` |
| `ring_buffer.rs` | Bounded trace storage |
| `udp.rs` | `UdpCanBus`, the transport shared by both front ends and the CLI |
| `simulator.rs`, `simulator_ext.rs` | The virtual device loop — periodic traffic, and answers to NMT, SDO, ping and OBD-II — and its UDP wiring |

See [Native Engine & Front End](rust_core.md) for how it is built and what it accelerates.

---

## The Python studio

`gui.py` is the reference implementation and the widest surface: the eight tabs described
in the [gallery](gallery.md), matplotlib plotting, CSV capture, DBC import and every
hardware adapter. `interfaces.py` abstracts the bus, so the same interface drives SLCAN
hardware, SocketCAN, a UDP multicast group or the in-process simulator.

Device knowledge lives in `stack/decoders/`, registered through `stack/registry.py`. A
decoder maps COB-IDs to named signals — see [Protocol Stack & Decoders](decoders.md).

!!! note "The two front ends must agree"
    `crates/canopen-core/src/telemetry.rs` mirrors
    `src/canopen_studio/stack/decoders/sevcon_gen4.py` and `cia402_generic.py`. A change
    to how a frame is decoded on one side is a change to both.

---

## The native front end

`crates/canopen-gui` is a Slint application over the same engine. It runs its own
simulator across a local UDP pair, and the two ends must use **different** ports —
sharing one socket makes each end receive the frames it just sent:

| End | Port |
|---|---|
| User interface | 1750 |
| Simulated nodes | 1751 |

The simulator starts both nodes **pre-operational**, so `Active Nodes: 0` on the
dashboard is correct until you send an NMT Start.

---

## Agent-facing servers

`mcp_server.py` exposes the bus over MCP and starts with the studio. `a2a_server.py` is
**opt-in** behind `CANOPEN_STUDIO_A2A=1` and rejects browser-originated requests. Both
are documented, with the reasoning behind their differing defaults, in
[AI Integration](ai_integration.md).
