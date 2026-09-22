# Native Engine & Front End

Since **0.5.0** the protocol work lives in a compiled Rust engine, `canopen-core`. The
graphical studio, the command-line tools and the native front end all drive the same
engine: frame parsing, CANopen classification, ISO-TP reassembly and the virtual
simulator run as native code rather than in the Python interpreter.

You do not have to think about any of this if you installed a **released build** — the
installers and portable archives ship the compiled extension. It matters when you run
from a Git checkout.

---

## What it replaces

| Python class | Native backing | Without the extension |
|---|---|---|
| `UdpBus` (`interfaces.py`) | `canopen_core.UdpCanBus`, `canopen_core.CanFrame` | Falls back to a stdlib socket and `python-can`'s MessagePack packing — correct, slower |
| `CanopenLayer` (`stack/canopen_layer.py`) | `canopen_core.decode_canopen_message` | Falls back to the pure-Python classifier — correct, slower |
| `VirtualCanopenSimulator` | A native OS thread, free of the GIL | Falls back to Python timing loops, with visibly worse SYNC jitter |
| `IsoTpReassembler` (`diag/isotp.py`) | `canopen_core.IsoTpReassembler` | **Raises `ImportError`.** OBD-II diagnostics do not work at all |

The first three degrade quietly, which is the intent: a missing extension costs speed,
not correctness. ISO-TP is the exception — it is native only, so the
[OBD-II tab](obd.md) fails on first use if the extension was never built.

---

## Building it from a checkout

`uv sync` does **not** build it. The Python build backend is plain setuptools and the
Rust workspace sits beside it, so the compiled module is a separate step:

```bash
just setup          # uv sync — Python dependencies
just rust-python    # cargo build + copy the extension into the package
```

`just rust-python` runs `scripts/build_extension.py`, which builds `canopen-core` with
the `python` feature in release mode and copies the result into the package as
`src/canopen_studio/canopen_core.pyd` (Windows) or `canopen_core.<abi>.so` (Linux,
macOS). It pins PyO3 to the interpreter in `.venv`, so the module always matches the
Python that will import it.

Prerequisites: a Rust toolchain from [rustup](https://rustup.rs/) (stable), plus the
platform's C linker — MSVC Build Tools on Windows, `cc` on Linux, Xcode command-line
tools on macOS.

!!! warning "About ninety failing tests that are not a broken tree"
    `uv run pytest` reports roughly ninety failures on
    `ImportError: cannot import name 'canopen_core'` when the extension has not been
    built. Run `just rust-python` first; the suite then passes.

---

## What is exposed to Python

```python
from canopen_studio import canopen_core
```

| Exported | Purpose |
|---|---|
| `CanFrame` | 11/29-bit identifier, payload, microsecond timestamp; MessagePack and compact binary conversion |
| `UdpCanBus` | The UDP transport, with `SO_REUSEADDR`, `SO_REUSEPORT` and `SO_BROADCAST` |
| `TraceRingBuffer` | Bounded trace storage with snapshotting and arbitration-ID/mask filtering |
| `LatencyTracker` | Periodic-interval jitter and RTT statistics |
| `IsoTpReassembler`, `FeedResult`, `fragment_isotp` | ISO 15765-2 reassembly and segmentation |
| `PdoMapping` | Bit-level signal packing and extraction, with scale, offset and units |
| `NmtMaster` | NMT command synthesis, node state tracking, heartbeat timeout detection |
| `EdsFile` | CiA 306 EDS parsing, and `PdoMapping` synthesis from mapping records |
| `VirtualCanopenSimulator` | The native simulator loop |
| `decode_canopen`, `decode_canopen_message`, `decode_obd2` | Frame classification and decoding |
| `build_sdo_read`, `build_sdo_write`, `build_sdo_abort`, `decode_sdo` | SDO request and response handling |

---

## The native front end

`crates/canopen-gui` is a second front end over the same engine, built with
[Slint](https://slint.dev). It is leaner than the Python studio — no matplotlib, no CSV
capture, no DBC import — and starts instantly.

```bash
just rust-gui                 # cargo run --release -p canopen-gui
cargo run -p canopen-gui      # debug build: starts in seconds rather than minutes
```

It runs its own simulator over a local UDP pair (user interface on port 1750, simulated
nodes on 1751) and starts both simulated nodes **pre-operational** — `Active Nodes: 0`
is correct until you send an NMT Start.

!!! tip "Linux and fontconfig"
    On NixOS, run it from the `nix-shell` provided by `shell.nix`; Slint needs
    fontconfig and the graphics libraries at runtime.

---

## Building and testing the workspace

```bash
just rust-build     # cargo build --workspace
just rust-test      # cargo test --workspace
```

Protocol behaviour is covered by integration tests that open a real socket pair —
`crates/canopen-core/tests/simulator_udp.rs` asserts an NMT Start reaching Operational,
an SDO upload returning the device type, and a ping echoing its sequence. That is
repeatable in a way that clicking through a window is not.

Continuous integration additionally enforces `cargo fmt --all --check` and
`cargo clippy --workspace --all-targets -- -D warnings`.

For what the engine actually bought in measured terms, see
[Performance Benchmarks](benchmarks.md).
