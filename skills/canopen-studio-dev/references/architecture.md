# Architecture

One protocol engine, two front ends, several agent-facing servers.

```
                    crates/canopen-core  (Rust: frames, CiA 301/402, OBD-II, transport)
                              |
              +---------------+----------------+
              |               |                |
        pyo3_bindings    canopen-gui      canopen-cli
              |            (Slint)        (sniff, bench)
      src/canopen_studio
        (Tkinter studio,
         MCP / A2A servers)
```

## The Rust engine

`canopen-core` holds everything that can be tested without a window.

| Module | Responsibility |
|---|---|
| `frame.rs` | `CanFrame`: an 11/29-bit id and a fixed `[u8; 8]`, so decoding allocates nothing. Converts to the python-can msgpack wire format and a 24-byte compact one |
| `canopen.rs` | COB-ID classification, `NmtState`, `CanopenService` |
| `nmt.rs` | `NmtMaster`: builds commands, consumes heartbeats, tracks nodes and their timeouts |
| `sdo.rs` | Expedited and segmented SDO, abort codes |
| `pdo.rs` | Signal definitions, bit extraction, PDO mappings |
| `eds.rs` | EDS parsing; can synthesise a `PdoMapping` from a mapping object |
| `telemetry.rs` | `DriveTelemetry` and `Cia402State`. Folds SEVCON TPDOs and CiA 402 statuswords into one snapshot |
| `obd2.rs` | Mode 01 requests, responses and PID decoding; `SUPPORTED_MODE01_PIDS` is the contract between requester and responder |
| `isotp.rs`, `isotp_manager.rs` | ISO 15765-2 segmentation and reassembly |
| `latency.rs` | `LatencyTracker` (interior mutability, so an `Arc` is enough to share it) and the CAN Ping helpers on 0x7E0/0x7E1 |
| `ring_buffer.rs` | Bounded trace storage |
| `udp.rs` | `UdpCanBus`, the transport shared by both front ends and the CLI |
| `simulator.rs` | The virtual device loop: periodic traffic, and answers to NMT, SDO, ping and OBD-II |
| `simulator_ext.rs` | Its UDP wiring. `spawn_udp_simulator_bound` can receive; `spawn_udp_simulator` transmits only |

## The Rust front end

`crates/canopen-gui/src/main.rs` is one file with a clear shape:

1. `main` builds the window, starts the simulator, opens the shared
   `Arc<UdpCanBus>`, then calls one `wire_*_tab` per interactive tab.
2. Each `wire_*_tab` installs callbacks that parse user input and transmit.
   Parsing lives in free functions (`parse_can_id`, `parse_index`,
   `parse_payload`) so it is unit-testable without a window.
3. One RX thread owns the receive loop. It folds each frame into the
   telemetry, the NMT map, the SDO log, the OBD readings and the latency
   trackers, then pushes a snapshot to the UI.

**`ModelRc` is not `Send`.** Cross the thread boundary with plain data
(`Vec<[String; 4]>`) and build the Slint model inside
`upgrade_in_event_loop`. `node_rows_to_model` and `obd_rows_to_model` exist
for exactly that.

## The Python studio

`gui.py` is the reference implementation and the widest surface: eight tabs,
matplotlib plotting, CSV capture, DBC import, hardware adapters.
`interfaces.py` abstracts the bus so the same UI drives SLCAN hardware,
socketcan, a UDP group or the in-process simulator.

Device knowledge lives in `stack/decoders/`, registered through
`stack/registry.py`. A decoder maps COB-IDs to named signals; that naming is
what `telemetry.rs` has to match.

## Agent-facing servers

`mcp_server.py` exposes the bus over MCP and starts with the studio.
`a2a_server.py` is **opt-in** behind `CANOPEN_STUDIO_A2A=1` and rejects
browser-originated requests — see the commit that introduced it before
loosening anything there.
