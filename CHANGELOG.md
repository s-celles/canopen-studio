# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-09-21

### Added
- **Reconstructed CAN waveforms for sigrok/PulseView (`crates/canopen-core/src/bitstream.rs`, `vcd.rs`)**: rebuilds the bit stream a decoded frame would have produced — SOF, arbitration, bit stuffing, CRC-15, delimiters, ACK slot, EOF — and writes it as VCD, which PulseView reads natively. Exposed to Python as `canopen_core.VcdWriter` and driven with `can-sniffer --vcd <path>`, with `--vcd-timing` (adapter timestamps or frames packed back to back), `--vcd-ack` and `--vcd-tick-ns`. Verified against sigrok-cli 0.7.2: 668 frames captured from the simulator decode back with the right identifiers and no CRC warnings.
  **The waveform is a reconstruction, not a measurement**, and says so where it cannot be missed: the channel is named `CAN_RX_RECONSTRUCTED`, the VCD header spells out that the ACK slot is an assumption, that errors and retransmissions are absent, that dropped frames leave no trace, and that the timing carries adapter accuracy. Frames sharing a timestamp are shifted apart so the wire never carries two at once, and the sniffer reports how many had to move.
- **PCAP-NG capture for Wireshark (`crates/canopen-core/src/pcap.rs`)**: frames are written with the `LINKTYPE_CAN_SOCKETCAN` encapsulation Wireshark reads natively, so its CANopen, J1939 and ISO 15765 dissectors apply without a plugin. Exposed to Python as `canopen_core.PcapNgWriter` and driven from the sniffer with `can-sniffer --pcap <path>`. The path may be a file or a named pipe (`\\.\pipe\canopen-studio`, or a FIFO), which gives a live capture: the studio creates the pipe and waits, then Wireshark connects to it (`wireshark -i <pipe> -k`) — Wireshark connects to pipes, it never creates them. The capture records every frame received, before the display filters, so it never silently omits traffic.
- **Hardware documentation for signal-level analysis** (`docs/logic_analyzer.md`): probing the bus with a CAN transceiver and an FX2 logic analyzer alongside the adapter — bill of materials, DB9 wiring, the termination trap, PulseView settings, and why a lone adapter on a bus never gets its frames acknowledged.
- **Wireshark integration guide** (`docs/wireshark.md`): capture files, live pipes, `Decode As` for CANopen, and useful display filters.

## [0.5.0] - 2026-09-21

### Added
- **Rust High-Performance Core Engine (`crates/canopen-core`)** (Phase 1A):
  - Compact stack-allocated `CanFrame` (24 bytes) supporting zero heap allocations, microsecond timestamps, and dual serialization (MessagePack `python-can` interop and raw compact binary).
  - High-throughput `TraceRingBuffer` with snapshotting and filtering by arbitration ID/mask.
  - Sub-microsecond `LatencyTracker` measuring periodic frame intervals and clock jitter (e.g. 50 Hz SYNC = 20,000 µs nominal) and RTT statistics.
  - Cross-platform `UdpCanBus` with `SO_REUSEADDR`, `SO_REUSEPORT`, and `SO_BROADCAST`.
  - CANopen Service Data Object (SDO) client & server protocol engine (CiA 301): expedited upload (read), expedited download (write), segmented block transfers, and abort code decoding.
  - **Process Data Object (PDO) Signal Mapping & Packing Engine**: Bit-level packing and extraction for arbitrary integer and boolean signals with scale factor, offset, and physical engineering units (`PdoMapping`, `SignalDefinition`).
  - **ISO-TP (ISO 15765-2) Multi-Frame Transport Engine**: Single Frame (SF), First Frame (FF), Consecutive Frame (CF) reassembly, and automatic Flow Control (FC) frame emission for diagnostic messages up to 4095 bytes.
  - Native CANopen service decoders (NMT, SYNC, TIME, EMCY, TPDO1..4, RPDO1..4, TSDO, RSDO, Heartbeat) and OBD-II SAE J1979 Mode 01 PID and DTC decoders.
  - **CANopen Network Management (NMT) Master & Heartbeat Consumer Engine (CiA 301)**: Command generation (`StartRemoteNode`, `StopRemoteNode`, `EnterPreOperational`, `ResetNode`, `ResetCommunication`), real-time node state tracking, and heartbeat timeout detection (`NmtMaster`, `MonitoredNode`).
  - **Electronic Data Sheet (EDS) & Object Dictionary Engine (CiA 306)**: Parser for standard CANopen `.eds` files (`[FileInfo]`, `[DeviceInfo]`, objects `[1000]`, subindices `[1018sub1]`, data types, access rights, default values), with automatic `PdoMapping` generation directly from TPDO/RPDO mapping records (`0x1A00`..`0x1A03`, `0x1600`..`0x1603`).
  - **PyO3 Python Bindings (`canopen_core`)**: Exposes the compiled Rust core directly to Python (`from canopen_studio import canopen_core`), tested via `tests/test_rust_core.py` (including `IsoTpReassembler`, `fragment_isotp`, `PdoMapping`, `NmtMaster`, and `EdsFile`).
  - **Phase 1A Native Integration**: The Python `UdpBus` interface now natively instantiates and delegates to the compiled Rust `canopen_core.UdpCanBus` and `canopen_core.CanFrame` objects, achieving wire-speed frame parsing directly in the backend and eliminating `python-can`'s MessagePack overhead.
  - **Phase 1B Global Parsing**: The `CanopenLayer` class in Python now natively offloads protocol classification (SDO, PDO, NMT, Heartbeat) and bitwise decoding to `canopen_core.decode_canopen_message` in Rust.
  - **Phase 1B Virtual Simulator**: The `VirtualCanopenSimulator` math generation and background timing loops (SYNC, Heartbeat, PDO sine waves) have been entirely ported to a native Rust OS thread, eliminating the Python GIL overhead.
  - **Phase 1C ISO-TP Engine**: The `IsoTpReassembler` used for OBD-II vehicle diagnostics is now fully rewritten in Rust. It tracks multiplexed concurrent diagnostic responses directly in native code, drastically speeding up VIN extraction and PID scanning.
- **Rust CLI Tool (`crates/canopen-cli`)**:
  - `sniff`: Real-time decoded CANopen and OBD-II network monitoring over UDP.
  - `bench-tx`: High-speed packet generator achieving ~300,000 frames/second.
  - `latency`: Real-time jitter and latency statistics display.
  - `bench-ping`: RTT benchmark — sends N CAN ping frames (0x7E0) and measures round-trip time from echo replies (0x7E1), reporting min/avg/max/stddev and packet loss. Same protocol as the Python `LatencyTracker` baseline.
  - `echo`: Auto-echo responder — listens for 0x7E0 frames and immediately replies with 0x7E1 echoing the identical payload. Run on the remote machine to act as the ping target.
- **Justfile Recipes**: `rust-build`, `rust-test`, `rust-python`, `rust-bench-tx`, `rust-bench-ping`, `rust-echo`, and `rust-gui`.
- **Full SYNC Jitter benchmark matrix** — transport × language (broadcast / unicast / multicast, local + Wi-Fi):
  - Local loopback — Rust 737 µs, Python 648 µs (Rust-backed `UdpBus`).
  - Wi-Fi broadcast — Rust 2,331 µs, Python (Rust-backed) 2,277 µs (vs old Python 23,920 µs with pure-Python simulator).
  - Wi-Fi unicast — Rust 643 µs, Python 609 µs.
  - Wi-Fi multicast (IGMP `join_multicast_v4`) — Rust 718 µs, Python 682 µs.
  - Key finding: Python ≈ Rust for all transports (same Rust `UdpCanBus` core); broadcast 3–4× worse due to Wi-Fi DTIM buffering.
  - `simulate`, `--filter-id`, `--group` added to `canopen-cli`; `rust-simulate`, `rust-bench-latency` added to justfile.
  - Python benchmark scripts added: `benchmarks/python_sync_jitter.py` (Rust-backed UdpBus) and `benchmarks/pure_python_sync_jitter.py` (stdlib socket + `time.sleep`, no Rust).
- Pure-Python loopback baseline: unicast 231 µs, multicast 302 µs, broadcast 312 µs (`time.perf_counter` receiver-side).
- Pure-Python cross-Wi-Fi benchmark (receiver-side `time.perf_counter`, pure stdlib — no Rust):
  - Unicast 192.168.30.31: **+224 µs avg jitter**, StdDev 830 µs — best single metric (no DTIM, receiver-side timing).
  - Broadcast 192.168.30.255: **+438 µs avg jitter**, StdDev 3,774 µs, max 143 ms — DTIM bursts visible in extremes.
  - Multicast 239.0.0.1: **+608 µs avg jitter**, StdDev 6,007 µs — experienced DTIM burst event; AP IGMP snooping inconsistent.
- Pure Python sim → Python (Rust-backed) receiver cross-Wi-Fi (sender-side timestamps):
  - Unicast: **+258 µs avg jitter**, StdDev **127 µs** — pure Python sim is more precise than Rust CLI sim (+609 µs) because it only sends SYNC (50fps, no HB/TPDO math between sleeps).
  - Multicast: **+289 µs avg jitter**, StdDev **89 µs** — lowest StdDev of all measurements.
  - Broadcast: **+786 µs avg jitter**, StdDev 3,392 µs — DTIM visible, but less severe than Rust CLI sim broadcast (76fps vs 50fps).
- Rust CLI sim → Pure Python receiver broadcast cross-Wi-Fi: **+2,914 µs avg jitter**, StdDev 11,541 µs (receiver-side, DTIM visible).
- §6.2 table reordered: Pure Python sim rows first, then Rust CLI, then Python GUI; receivers in order Pure Python → Rust CLI → Python (Rust-backed) within each simulator block.
- `.gitignore` updated to whitelist `benchmarks/` directory.
- **fix(cli)**: `latency` now binds to `0.0.0.0` so it can receive frames from remote simulators over the network (was hardcoded to `127.0.0.1`).
- **Benchmarks documentation** updated with a pedagogical **Glossary of Benchmark Types** section explaining RTT, SYNC jitter, and TX throughput benchmarks.
- **Rust RTT Ping Benchmark** (MAINPADIX NixOS ↔ MacBook, Wi-Fi 802.11, 20 pings each direction):
  - Linux→Mac: min **5.92 ms**, avg **25.94 ms**, 0% loss (vs Python: min 24.85 ms, avg 76.42 ms — **−76% min, −66% avg**).
  - Mac→Linux: min **6.15 ms**, avg **71.66 ms**, 0% loss (vs Python: min 18.15 ms, avg 74.32 ms — **−66% min**).
  - Gain attributed to elimination of Python GIL overhead, `time.perf_counter()` scheduling latency, and msgpack serialization round-trips. Remaining high-end outliers are Wi-Fi 802.11 burst jitter (DTIM beacons, power-save cycles), independent of the language runtime.
- **OBD-II vehicle diagnostics (SAE J1979)**, in two independently testable parts: an adapter backend and an application layer.
- `DiagnosticInterface`, a transport-neutral abstraction deliberately separate from the CAN adapter catalog. An ELM327 is not a transparent bridge — it runs its own protocol autodetection and ISO-TP handling and answers in ASCII hexadecimal — so it cannot honour the contract that `open_can_bus()` consumers (the CANopen layer, the trace, the plotter, the bridge) all depend on.
- `ElmDiagnosticInterface`: ELM327 over USB, classic Bluetooth SPP (via a bound `rfcomm` or `COMx` port) and TCP. Bluetooth Low Energy is documented as unsupported, because BLE adapters expose a vendor-specific GATT service rather than a serial port.
- `NativeCanDiagnosticInterface`: ISO 15765-2 over every adapter already in the catalog — SLCAN, PCAN, Kvaser, Vector, IXXAT, gs_usb, SocketCAN, UDP multicast and the virtual bus — with no ELM327 in the way.
- Runtime ELM327 capability probing. Counterfeit boards commonly report a version their firmware does not live up to, so the version is read but never trusted: each capability is probed by sending the command and watching for `?`, the effective version is capped at the evidence, and the session degrades instead of failing.
- Robust ELM327 reply parsing: framing on the `>` prompt, echo removal, `SEARCHING...` / `BUS INIT` / power-alert chatter (including when an adapter glues it to the data), and explicit handling of `NO DATA`, `CAN ERROR`, `UNABLE TO CONNECT`, `BUFFER FULL`, `STOPPED` and `?`. A status word is reported rather than raised, since `NO DATA` is the ordinary answer to an unsupported PID.
- SAE J1979 modes 01 through 0A, supported-PID discovery from the vehicle's own bitmasks (`0x00`, `0x20`, `0x40` …) tracked per ECU, ISO 15031-6 trouble codes with the P/C/B/U prefixes, and VIN reading via mode 09 PID 02 with ISO 3779 decoding.
- Declarative vehicle profiles with inheritance (generic J1979 → make → model and year) and a four-stage resolution cascade: VIN, supported-PID fingerprint, manual choice, then generic J1979. Resolution never fails, because a wrong-but-specific profile would decode a manufacturer PID into a plausible-looking wrong number.
- The generic `j1979_base` profile as shipped data, covering mode 01 (`0x00`–`0x60`) and mode 09, verified by tests against the standard's own worked values.
- Importers for Torque Pro custom-PID CSV exports and for DBC databases (via the optional `cantools` extra, `canopen-studio[dbc]`). Both skip a definition they cannot express exactly, with a reason, rather than importing one that would decode to a plausible wrong number.
- Ten `obd_*` MCP tools on the **existing** server — one process, one port, one set of guards. Nine read; `obd_clear_dtcs` can change the vehicle and is gated (see below).
- An **🩺 OBD-II Diagnostics** GUI tab: adapter selection, vehicle identification, supported-parameter discovery and live reading, trouble codes, and a gated clear.
- Opt-in integration tests against Ircama's ELM327-emulator (`just test-emulator`), plus an in-repository ELM327 fake that carries the everyday suite with nothing installed.
- `just` recipes: `test-emulator`, `emulator`, `import-torque`, `profiles`.

### Security
- Diagnostic writes are off by default and pass three independent gates: the `CANOPEN_STUDIO_DIAG_WRITE` environment flag (following the `CANOPEN_STUDIO_A2A` pattern), a per-profile `write_whitelist`, and an explicit per-call confirmation that never persists. A refused write transmits nothing.
- The UDS write services `0x2E`, `0x31` and `0x2F` have **no request path** in this release and are refused even with every gate open. The gate exists so that adding one is a deliberate change rather than an accident.
- Clearing trouble codes (mode 04) is implemented but gated, and the GUI states what it costs: it erases the readiness monitors, which need a full drive cycle to rebuild and without which an emissions test fails.
- A write requested through MCP passes a **fourth** gate, `CANOPEN_STUDIO_MCP_DIAG_WRITE`, on top of the three above. It is deliberately separate from `CANOPEN_STUDIO_DIAG_WRITE`: enabling writes so that a person can clear codes from the GUI must not, by itself, hand that capability to whatever model is connected to the server. With only one of the two set, the call transmits nothing and the refusal names the missing one.
- When agent writes are enabled, the capability is stated at the server's console on startup, in the tool's own description, in the result of every trouble-code read, and in `obd_status()`. Nobody should discover it by watching a model use it.
- The UDS write services stay unavailable through MCP, because they stay unimplemented everywhere.
- Decoding formulas from profiles and imported files are **interpreted, never executed**: parsed to a syntax tree, whitelisted node by node, and evaluated by walking that tree. Nothing is compiled and `eval` is never called, so a profile cannot reach the filesystem, the network or the interpreter. Profile files are read with `yaml.safe_load`, and exponents are bounded.

### Documentation
- `SECURITY.md`, with private disclosure through GitHub Security Advisories, the components whose security surface is real (the local agent servers, the diagnostic write gate, the formula interpreter, file loading, the updater), what is *not* a vulnerability but a documented property, and the physical-safety notes that matter when a bug can move something.
- `CODE_OF_CONDUCT.md`: Contributor Covenant 3.0, with the reporting and enforcement sections filled in. Both live in `.github/`, where GitHub picks them up.
- The documentation build now publishes `llms.txt` and `llms-full.txt` (via `mkdocs-llmstxt`), so a language model can read the documentation without scraping the rendered HTML.

### Changed
- `pyyaml` is now a dependency, for the vehicle profile format. `cantools` is an optional `[dbc]` extra rather than a base dependency, since the generic profile and every hand-written one work without it.
- The MCP server's instructions mention the `obd_*` tools and state that they read only.

### Notes
- Only the CAN protocols of ISO 15765-4 are parsed. A session that autodetects K-line (ISO 9141-2, ISO 14230-4) or J1850 says which protocol it found and stops, rather than mis-parsing a differently framed reply.
- `ELM327-emulator` is intentionally **not** a development dependency: it is licensed CC-BY-NC-SA-4.0, which is non-commercial and non-OSI, and adding it to the default dev group of a GPL project would impose that on everyone running the suite. It runs as a separate process, so nothing is redistributed, and the tests that need it skip with an instruction when it is absent.

## [0.4.0] - 2026-09-19

### Security
- The A2A server no longer starts automatically. It is stateless and accepts `text/plain`, so a single POST from any web page the user visited reached it without a CORS preflight and could transmit on a connected CAN bus (`send`, `sync`, `nmt stop` were all confirmed to execute). It now requires `CANOPEN_STUDIO_A2A=1`.
- Both servers refuse requests carrying an `Origin` header, which blocks browser-originated requests while leaving command-line clients and MCP agents unaffected. `CANOPEN_STUDIO_ALLOWED_ORIGINS` allows specific origins when a front-end genuinely needs one.
- Both servers refuse requests whose `Host` header does not name the loopback interface, closing the DNS rebinding variant where the page becomes same-origin and sends no `Origin`.
- The MCP server can be kept from starting with `CANOPEN_STUDIO_MCP=0`, for shared machines.
- Documented the real exposure of each server, why their defaults differ, and what remains uncovered (no authentication: any local process can still reach a running server).

### Fixed
- Worked around fastmcp 4.0.5 silently ignoring `host_origin_protection` on the SSE transport, which left the protection absent while appearing enabled: the guard middleware is never installed although the setting is accepted, so the MCP server builds and guards its SSE application itself.

## [0.3.0] - 2026-09-18 [WITHDRAWN]

Withdrawn on 2026-09-19: this release shipped the A2A exposure described above. Its content is
included in 0.4.0.


### Added
- CAN <-> network bridge (`can_bridge.py`): mirrors the bus currently captured — typically a physical adapter connected to a real device — onto a UDP multicast group, so remote machines observe the live traffic as if they were wired to it. Available from the GUI toolbar (**Bridge → Net**) and from the MCP/A2A `bridge_start` / `bridge_stop` tools, with bridge counters reported by `get_status`.
- Optional injection direction (`allow_inject`) replaying network frames onto the real bus, off by default because it writes to real hardware.
- Symmetric loop protection in the bridge: frames sent in either direction are remembered for a short window so the multicast reflection is discarded instead of being relayed back; the bridge also refuses to mirror a multicast group onto itself.
- Configurable UDP multicast hop limit (TTL) via the `CANOPEN_UDP_HOP_LIMIT` environment variable or the `hop_limit` argument of `open_can_bus()` and the MCP/A2A `connect` tool, allowing the virtual CAN bus to reach machines on other subnets (python-can defaults to 1, confining frames to the local segment).
- MCP/A2A status now reports the active `channel` and `bitrate` alongside the interface, so remote clients can join the same bus.
- Unit tests for the MCP server tools (`tests/test_mcp_server.py`), the GUI status snapshot (`tests/test_gui_status.py`) and the UDP multicast bus configuration.
- MCP server (`can_mcp_server.py`) and A2A server (`can_a2a_server.py`) started automatically by the GUI in daemon threads, exposing the bus to AI agents: connection management, trace and network state reading, raw frame transmission, NMT commands, SYNC generation and SDO reads.
- Multi-instance support: `CANOPEN_STUDIO_INSTANCE` names an instance in the window title and status reports, while `MCP_PORT` and `A2A_PORT` let several instances run side by side.
- UDP Multicast interface (CAN over IP) with an attachable simulator, turning any local network into a shared virtual CAN bus.
- NixOS support: `shell.nix`, automatic `LD_LIBRARY_PATH` setup for `libstdc++` in `just gui`, and a venv setup handling tkinter and binary wheels.
- Static analysis and security scanning in CI (mypy, bandit, safety, pre-commit), and a release pipeline building standalone binaries for macOS and Linux alongside Windows.

### Fixed
- Corrected the `claude mcp add` command shown in `can_mcp_server.py` (docstring and standalone startup message): the server name must precede the URL.
- `get_status` reported the interface selected in the combobox instead of the one actually connected, so a bus opened from MCP/A2A was misreported (e.g. `slcan` while running on `udp_multicast`).
- Frames sent through MCP/A2A (`send_frame`, `send_nmt`, `send_sync`, `sdo_read`) were not counted in the `total_tx` statistic, unlike frames sent from the GUI transmission console.
- Removed an unused `tkinter` import in `connect_from_mcp` flagged by Ruff (F401).
- The virtual simulator now transmits on a separate `sim_bus` when running on a network interface: `udp_multicast` cannot receive its own messages, so simulated frames never reached the capture loop.
- Added the `msgpack` dependency required by the UDP Multicast backend, which failed to open without it.
- `VirtualCanopenSimulator.running` is initialised in `__init__`, avoiding an attribute error when stopping a simulator that was never started.
- Corrected `bandit` scanning the `.venv` directory in CI.
- The in-app git updater located the checkout from its own directory, which broke once the code moved under `src/`; it now walks up to find the repository root and reports clearly when running outside a checkout.

### Changed
- **Breaking (source layout)**: the Python code moved to a `src/canopen_studio/` package. Modules were renamed (`can_gui` -> `canopen_studio.gui`, `can_interfaces` -> `canopen_studio.interfaces`, `can_bridge` -> `canopen_studio.bridge`, `can_mcp_server` -> `canopen_studio.mcp_server`, `can_a2a_server` -> `canopen_studio.a2a_server`, `can_sniffer` -> `canopen_studio.sniffer`, `updater` -> `canopen_studio.updater`, `canopen_stack` -> `canopen_studio.stack`). Console entry points (`canopen-studio`, `can-sniffer`, `canopen-mcp`, `canopen-a2a`) are unchanged.
- The version is declared once in `src/canopen_studio/__init__.py`; `pyproject.toml` reads it as dynamic metadata, the modules import it, and the Windows installer receives it from the release workflow.
- Documented the AI integration (MCP and A2A) on a dedicated page, covering the tool reference, bridge control from an agent, multi-instance ports and the absence of authentication.
- Applied `ruff format` to `can_gui.py`, `can_mcp_server.py` and `can_a2a_server.py`, which were failing the CI formatting check.
- Made the `justfile` fully cross-platform.
- Documented UDP Multicast, the virtual simulator and the network impact of multicast versus broadcast.

## [0.2.1] - 2026-09-18

### Added
- Automated macOS installation script (`install.sh` and `scripts/install_macos.sh`) creating a native `.app` wrapper bundle on the user's Desktop.
- Automated Linux installation script (`scripts/install_linux.sh`) generating a standard `.desktop` application menu entry.
- Added Python best practices to `AGENTS.md` based on `cookiecutter-python-package` recommendations (pre-commit, mypy, bandit, safety).

## [0.2.0] - 2026-09-18
### Added
- Generalized multi-interface hardware support: SLCAN (Lawicel, USBtin, CANable), PEAK PCAN, Kvaser, Vector, IXXAT, gs_usb, SocketCAN, and Virtual Simulator.
- Extensible protocol decoding architecture with generic CiA 301/402, SEVCON Gen4, De Haardt, J1939, and Raw CAN profiles.
- Real-time multi-byte payload oscilloscope plotter with simultaneous B0–B7 time-series tracing.
- Network monitor with automatic node discovery, state tracking, and live gauges.
- CANopen Transmission Console with Generic NMT Master, SYNC clock generator, and frame templates library.
- SDO expedited dictionary reader and writer.
- Built-in Virtual CANopen Simulator for hardware-free educational study and testing.
- One-click Windows installer script (`install.bat` and `scripts/install_windows.ps1`) to automatically configure the environment and create Desktop and Start Menu shortcuts.
- Standalone PyInstaller build script (`scripts/build_exe.py`) supporting both directory and single-file portable builds.
- Native Windows Setup installer script (`installer/CANopenStudio.iss`) for Inno Setup.
- Automated GitHub Actions release pipeline (`.github/workflows/release.yml`) producing downloadable `.exe` installer and portable `.zip` packages on tag releases.
- Custom vector-styled application icons (`assets/icon.png` and `assets/icon.ico`) featuring CAN differential signal waveforms and network topology.
- Package entry points in `pyproject.toml` (`canopen-studio` and `can-sniffer`) for global installation via `uv tool` or `pip`.
- Justfile automation recipes for `test`, `lint`, `format`, `check`, `install`, `generate-icon`, `build-exe`, `build-portable`, `doc-build`, and `doc-serve`.
- Comprehensive pytest unit test suite (34 tests) covering CANopen layer protocol state transitions, application decoders (SEVCON, CiA 402, De Haardt, J1939), hardware interface abstraction, virtual simulator loop, CLI argument parsing, and updater routines.
- Integrated code quality and style linting with Ruff (`ruff check`, `ruff format`) configured in `pyproject.toml`.
- Continuous Integration workflow (`.github/workflows/ci.yml`) testing across Python 3.10, 3.11, 3.12, 3.13 on both Ubuntu and Windows runners, with Ruff linting and formatting verification.
- Automated dependency update configuration with Dependabot (`.github/dependabot.yml`) for both GitHub Actions and Python dependencies.
- In-app update mechanism (`updater.py`) with non-blocking background release checks against GitHub API, update notifications in status bar, interactive "Check for Updates" dialog with release notes, one-click Windows installer download & execution, and Git `git pull && uv sync` automated updates for developer installations.
- Comprehensive documentation website built with Material for MkDocs (`mkdocs.yml`, `docs/`) with instant search, dark/light themes, hardware setup guides, protocol specifications, and automated deployment to GitHub Pages (`.github/workflows/docs.yml`).
- Minimalist `README.md` with CI/Docs/Release badges, feature highlights, and direct documentation links.
- Allowlist `.gitignore` policy and initial GitHub release.
