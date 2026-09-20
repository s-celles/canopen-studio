---
name: canopen-studio-dev
description: Work on the CAN & CANopen Studio repository itself — the Python Tkinter studio, the Rust canopen-core engine, the Slint front end and the CLI. Covers the repository map, the lint and test gates, the pytest failure baseline, launching and driving either GUI, and the traps that cost real time (GUIs that exit silently under a tool sandbox, the Windows justfile shell, the locked .exe on rebuild, unreliable UI click automation). Use when editing src/canopen_studio/**, crates/**, the justfile or the CI workflow, or when asked to run, screenshot or verify either front end. For reference on the CANopen protocol itself, read crates/canopen-gui/src/reference.txt instead.
---

# Developing CAN & CANopen Studio

A CAN bus analyser and transmit station. One protocol engine in Rust, two
front ends: a Tkinter studio in Python and a Slint one in Rust.

## Repository map

| Path | Contents |
|---|---|
| `src/canopen_studio/gui.py` | The Python studio: eight tabs, the reference implementation of every feature |
| `src/canopen_studio/interfaces.py` | Bus backends: SLCAN, socketcan, UDP, and `VirtualCanopenSimulator` |
| `src/canopen_studio/stack/decoders/` | Device profiles. `sevcon_gen4.py` and `cia402_generic.py` define what the telemetry means |
| `src/canopen_studio/diag/` | OBD-II: ELM327 adapters, ISO-TP, J1979 PIDs, vehicle profiles |
| `src/canopen_studio/{mcp,a2a}_server.py` | Agent-facing servers. A2A is opt-in (`CANOPEN_STUDIO_A2A=1`) |
| `crates/canopen-core/src/frame.rs` | `CanFrame`, the fixed 8-byte no-allocation payload, msgpack and compact wire formats |
| `crates/canopen-core/src/{sdo,nmt,pdo,canopen}.rs` | CiA 301 services |
| `crates/canopen-core/src/telemetry.rs` | Drive telemetry and the CiA 402 statusword. **Mirrors the Python decoders** |
| `crates/canopen-core/src/{obd2,isotp}.rs` | OBD-II Mode 01 and ISO 15765-4 |
| `crates/canopen-core/src/{simulator,simulator_ext}.rs` | The virtual bus: SYNC 50 Hz, heartbeats 1 Hz, TPDOs 25 Hz, and answers to NMT, SDO, ping and OBD-II |
| `crates/canopen-core/src/udp.rs` | `UdpCanBus`, the transport both front ends share |
| `crates/canopen-core/src/pyo3_bindings.rs` | The Python extension surface |
| `crates/canopen-gui/src/main.rs` | Rust front end: RX thread, tab wiring, input parsing |
| `crates/canopen-gui/ui/main.slint` | Its layout |
| `crates/canopen-cli/src/main.rs` | sniff, simulate, echo, bench-tx, bench-ping, latency |

## The two front ends must agree

The Python studio is the reference. When you change decoding on one side,
change the other and say so. `telemetry.rs` exists to mirror
`stack/decoders/sevcon_gen4.py` and `cia402_generic.py`; a divergence there is
a bug even when both sides pass their own tests.

Vendor COB-IDs are not the predefined connection set. SEVCON calls 0x473
"TPDO5", but under CiA 301 that is RPDO3 of node 115. Read vendor IDs against
the device profile, not the standard layout.

## Gates

```bash
uv run ruff format --check . && uv run ruff check .
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

Clippy runs with `-D warnings` in CI, so a lint is a build failure, not a note.

## Baselines

- **`cargo test --workspace` passes completely.** Any failure is yours.
- **`uv run pytest` gives 1024 passed, 1 skipped, nothing failing** — but
  only after `just rust-python` has built the compiled extension. `uv sync`
  does not build it: the backend is plain setuptools and the Rust workspace
  sits beside it. Skip that step and about ninety tests fail on
  `ImportError: cannot import name 'canopen_core'`, which looks like a broken
  tree and is not one.
- Anything red is therefore yours. Both suites are expected to be green.

## Running it

See [references/launching.md](references/launching.md) — read it before the
first attempt. The GUIs exit with status 0 and no message when they cannot
reach the interactive desktop, which looks exactly like a crash and is not one.

## What to put where

Protocol logic goes in `canopen-core`, where it is unit-testable, not in the
GUI event loop. The pattern the existing tabs follow: a callback parses input
and transmits; the RX thread decodes what comes back and pushes it to the UI.
`ModelRc` is not `Send`, so build Slint models inside `upgrade_in_event_loop`,
never on the RX thread.

Name tests as sentences describing the behaviour —
`an_nmt_start_moves_the_simulated_node_to_operational`, not `test_nmt`.
