# CAN & CANopen Studio

[![CI](https://github.com/s-celles/canopen-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/s-celles/canopen-studio/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue.svg)](https://s-celles.github.io/canopen-studio/)
[![Release](https://img.shields.io/github/v/release/s-celles/canopen-studio)](https://github.com/s-celles/canopen-studio/releases)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

> **Universal CAN & CANopen protocol analyzer, real-time telemetry oscilloscope, and transmit station.**

📖 **Full Online Documentation**: **[https://s-celles.github.io/canopen-studio/](https://s-celles.github.io/canopen-studio/)**

---

## ✨ Features

- **Multi-Hardware Support**: SLCAN (Lawicel CANUSB, USBtin, CANable), PEAK PCAN, Kvaser, Vector XL, IXXAT, gs_usb, Linux SocketCAN, UDP multicast, and a hardware-free Virtual Simulator.
- **Protocol & Application Decoders**: Generic CiA 301 / CiA 402 drives, SEVCON Gen4 inverters, De Haardt kart transponders, J1939 29-bit, and Raw CAN.
- **Multi-Trace Oscilloscope**: Real-time simultaneous time-series plotting of all 8 payload bytes (B0..B7) or decoded physical values (RPM, torque, temps).
- **Network Node Monitor**: Live discovery of nodes, state tracking (Operational, Pre-Op, Stopped), and visual dials.
- **Full Transmission Console**: Generic NMT Master (Start/Stop/Reset), 50 Hz SYNC clock generator, and frame templates library.
- **SDO Explorer**: Expedited dictionary reader & writer for node inspection.
- **Native Rust Engine**: Frame parsing, CANopen classification, ISO-TP reassembly and the virtual simulator run as compiled code, shared by the Python studio, the native front end and the CLI.
- **Real Bus Network Bridge**: Mirror a physical CAN bus onto a UDP multicast group so remote machines observe the live traffic as if they were wired to it.
- **AI Agent Integration**: MCP and A2A servers expose the bus to an agent — read the trace, inspect nodes, transmit frames, drive the NMT state machine.
- **OBD-II Vehicle Diagnostics (SAE J1979)**: Live parameters, trouble codes and VIN over an ELM327 (USB, Bluetooth SPP, Wi-Fi) or straight over a native CAN adapter via ISO-TP. Supported PIDs are discovered from the vehicle, never assumed; declarative vehicle profiles with inheritance; read-only by default.
- **In-App Updater**: Automated background checks against GitHub Releases and 1-click upgrade.

---

## 🚀 Quick Install

### Option A: Standalone Application — No Python required
Download the latest build for your platform from **[GitHub Releases](https://github.com/s-celles/canopen-studio/releases)**:
- **`CANopen-Studio-vX.Y.Z-Windows-Setup.exe`** (setup wizard with desktop & start menu shortcuts)
- **`CANopen-Studio-Windows-x64-Portable.zip`** (portable executable)
- **`CANopen-Studio-macOS-x64-Portable.tar.gz`** / **`CANopen-Studio-Linux-x64-Portable.tar.gz`**

Released builds carry the compiled `canopen_core` engine; a Git checkout does not until you run `just rust-python`.

### Option B: 1-Click Local Installer (`install.bat`)
```bash
git clone https://github.com/s-celles/canopen-studio.git
cd canopen-studio
# Double-click install.bat (or run 'just install')
```

### Option C: Python CLI Tool (`uv tool` / `pip`)
```bash
uv tool install git+https://github.com/s-celles/canopen-studio.git
canopen-studio
```

---

## ⚡ Quick Start

```powershell
# One-time: dependencies, then the compiled Rust engine
just setup
just rust-python

# Launch the graphical studio (or `just rust-gui` for the native front end)
just gui

# Run CLI Sniffer on SLCAN at 500 kbps
just sniff

# Run without hardware (Virtual Simulation Mode)
just simulate

# Run tests and quality verification
just check          # Python: ruff + pytest
just rust-test      # cargo test --workspace
```

> `uv sync` alone does not build `canopen_core`: the Python backend is plain setuptools and
> the Rust workspace sits beside it. Skip `just rust-python` and about ninety tests fail on
> `ImportError: cannot import name 'canopen_core'` — a missing build step, not a broken tree.

---

## 📚 Documentation

Detailed documentation, architecture guides, decoder tutorials, and screenshots are hosted on GitHub Pages:
👉 **[https://s-celles.github.io/canopen-studio/](https://s-celles.github.io/canopen-studio/)**

| | |
|---|---|
| [Installation](https://s-celles.github.io/canopen-studio/installation/) | Installers, portable builds, source |
| [Quick Start](https://s-celles.github.io/canopen-studio/quickstart/) | First connection, with or without hardware |
| [Architecture](https://s-celles.github.io/canopen-studio/architecture/) | One engine, two front ends |
| [Native Engine](https://s-celles.github.io/canopen-studio/rust_core/) | What the Rust core accelerates, and how to build it |
| [Command-Line Tools](https://s-celles.github.io/canopen-studio/cli/) | `can-sniffer` and `canopen-cli` |
| [OBD-II Diagnostics](https://s-celles.github.io/canopen-studio/obd/) | Vehicle diagnostics over ELM327 or native CAN |
| [AI Integration](https://s-celles.github.io/canopen-studio/ai_integration/) | MCP and A2A |

---

## 🗺️ Roadmap & Performance Benchmarks

- **[Development Roadmap](ROADMAP.md)** ([online](https://s-celles.github.io/canopen-studio/roadmap/)) — Architectural evolution of the **Rust** engine, and integration of next-generation agentic AI protocols (**AG-UI**, **A2UI**, **Agent Control Protocol - ACP**).
- **[Performance Benchmarks](benchmarks.md)** ([online](https://s-celles.github.io/canopen-studio/benchmarks/)) — Empirical latency (CAN ping/pong RTT), bus jitter, and throughput measurements across Linux and macOS, in both languages.
- **[Changelog](CHANGELOG.md)** ([online](https://s-celles.github.io/canopen-studio/changelog/)) — What changed in each release.

---

## 🤝 Community & Security

- **[Code of Conduct](.github/CODE_OF_CONDUCT.md)** — Contributor Covenant 3.0.
- **[Security Policy](.github/SECURITY.md)** — report privately through
  [GitHub Security Advisories](https://github.com/s-celles/canopen-studio/security/advisories/new),
  never in a public issue.

> ⚠️ This software transmits on real CAN buses and can talk to vehicles. Read the safety
> notes in the security policy before testing against anything that moves.

For language models: the documentation is published as
[`llms.txt`](https://s-celles.github.io/canopen-studio/llms.txt) and
[`llms-full.txt`](https://s-celles.github.io/canopen-studio/llms-full.txt).

---

## 📄 License

This project is licensed under the **GNU General Public License v3.0** (`GPL-3.0-or-later`).
Copyright (C) 2026 Sébastien Celles.
