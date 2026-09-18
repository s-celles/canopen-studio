# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- CAN <-> network bridge (`can_bridge.py`): mirrors the bus currently captured — typically a physical adapter connected to a real device — onto a UDP multicast group, so remote machines observe the live traffic as if they were wired to it. Available from the GUI toolbar (**Bridge → Net**) and from the MCP/A2A `bridge_start` / `bridge_stop` tools, with bridge counters reported by `get_status`.
- Optional injection direction (`allow_inject`) replaying network frames onto the real bus, off by default because it writes to real hardware.
- Symmetric loop protection in the bridge: frames sent in either direction are remembered for a short window so the multicast reflection is discarded instead of being relayed back; the bridge also refuses to mirror a multicast group onto itself.
- Configurable UDP multicast hop limit (TTL) via the `CANOPEN_UDP_HOP_LIMIT` environment variable or the `hop_limit` argument of `open_can_bus()` and the MCP/A2A `connect` tool, allowing the virtual CAN bus to reach machines on other subnets (python-can defaults to 1, confining frames to the local segment).
- MCP/A2A status now reports the active `channel` and `bitrate` alongside the interface, so remote clients can join the same bus.
- Unit tests for the MCP server tools (`tests/test_mcp_server.py`), the GUI status snapshot (`tests/test_gui_status.py`) and the UDP multicast bus configuration.

### Fixed
- Corrected the `claude mcp add` command shown in `can_mcp_server.py` (docstring and standalone startup message): the server name must precede the URL.
- `get_status` reported the interface selected in the combobox instead of the one actually connected, so a bus opened from MCP/A2A was misreported (e.g. `slcan` while running on `udp_multicast`).
- Frames sent through MCP/A2A (`send_frame`, `send_nmt`, `send_sync`, `sdo_read`) were not counted in the `total_tx` statistic, unlike frames sent from the GUI transmission console.
- Removed an unused `tkinter` import in `connect_from_mcp` flagged by Ruff (F401).

### Changed
- Applied `ruff format` to `can_gui.py`, `can_mcp_server.py` and `can_a2a_server.py`, which were failing the CI formatting check.

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
