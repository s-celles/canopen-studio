# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-09-19

### Security
- The A2A server no longer starts automatically. It is stateless and accepts `text/plain`, so a single POST from any web page the user visited reached it without a CORS preflight and could transmit on a connected CAN bus (`send`, `sync`, `nmt stop` were all confirmed to execute). It now requires `CANOPEN_STUDIO_A2A=1`.
- Both servers refuse requests carrying an `Origin` header, which blocks browser-originated requests while leaving command-line clients and MCP agents unaffected. `CANOPEN_STUDIO_ALLOWED_ORIGINS` allows specific origins when a front-end genuinely needs one.
- Both servers refuse requests whose `Host` header does not name the loopback interface, closing the DNS rebinding variant where the page becomes same-origin and sends no `Origin`.
- The MCP server can be kept from starting with `CANOPEN_STUDIO_MCP=0`, for shared machines.
- Documented the real exposure of each server, why their defaults differ, and what remains uncovered (no authentication: any local process can still reach a running server).

### Fixed
- Worked around fastmcp 4.0.5 silently ignoring `host_origin_protection` on the SSE transport, which left the protection absent while appearing enabled; the MCP server now mounts the project's own guard. See `upstream-bugs.md`.

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
