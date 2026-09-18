# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- One-click Windows installer script (`install.bat` and `scripts/install_windows.ps1`) to automatically configure the environment and create Desktop and Start Menu shortcuts.
- Standalone PyInstaller build script (`scripts/build_exe.py`) supporting both directory and single-file portable builds.
- Native Windows Setup installer script (`installer/CANopenStudio.iss`) for Inno Setup.
- Automated GitHub Actions release pipeline (`.github/workflows/release.yml`) producing downloadable `.exe` installer and portable `.zip` packages on tag releases.
- Custom vector-styled application icons (`assets/icon.png` and `assets/icon.ico`) featuring CAN differential signal waveforms and network topology.
- Package entry points in `pyproject.toml` (`canopen-studio` and `can-sniffer`) for global installation via `uv tool` or `pip`.
- Justfile automation recipes for `test`, `lint`, `format`, `install`, `generate-icon`, `build-exe`, and `build-portable`.
- Comprehensive pytest unit test suite (26 tests) covering CANopen layer protocol state transitions, application decoders (SEVCON, CiA 402, De Haardt, J1939), hardware interface abstraction, virtual simulator loop, and CLI argument parsing.
- Integrated code quality and style linting with Ruff (`ruff check`, `ruff format`) configured in `pyproject.toml`.

## [0.2.0] - 2026-09-18

### Added
- Generalized multi-interface hardware support: SLCAN (Lawicel, USBtin, CANable), PEAK PCAN, Kvaser, Vector, IXXAT, gs_usb, SocketCAN, and Virtual Simulator.
- Extensible protocol decoding architecture with generic CiA 301/402, SEVCON Gen4, De Haardt, J1939, and Raw CAN profiles.
- Real-time multi-byte payload oscilloscope plotter with simultaneous B0–B7 time-series tracing.
- Network monitor with automatic node discovery, state tracking, and live gauges.
- CANopen Transmission Console with Generic NMT Master, SYNC clock generator, and frame templates library.
- SDO expedited dictionary reader and writer.
- Built-in Virtual CANopen Simulator for hardware-free educational study and testing.
- Allowlist `.gitignore` policy and initial GitHub release.
