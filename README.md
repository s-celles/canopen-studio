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

- **Multi-Hardware Support**: SLCAN (Lawicel CANUSB, USBtin, CANable), PEAK PCAN, Kvaser, Vector XL, IXXAT, gs_usb, Linux SocketCAN, and Virtual Simulator.
- **Protocol & Application Decoders**: Generic CiA 301 / CiA 402 drives, SEVCON Gen4 inverters, De Haardt kart transponders, J1939 29-bit, and Raw CAN.
- **Multi-Trace Oscilloscope**: Real-time simultaneous time-series plotting of all 8 payload bytes (B0..B7) or decoded physical values (RPM, torque, temps).
- **Network Node Monitor**: Live discovery of nodes, state tracking (Operational, Pre-Op, Stopped), and visual dials.
- **Full Transmission Console**: Generic NMT Master (Start/Stop/Reset), 50 Hz SYNC clock generator, and frame templates library.
- **SDO Explorer**: Expedited dictionary reader & writer for node inspection.
- **OBD-II Vehicle Diagnostics (SAE J1979)**: Live parameters, trouble codes and VIN over an ELM327 (USB, Bluetooth SPP, Wi-Fi) or straight over a native CAN adapter via ISO-TP. Supported PIDs are discovered from the vehicle, never assumed; declarative vehicle profiles with inheritance; read-only by default.
- **In-App Updater**: Automated background checks against GitHub Releases and 1-click upgrade.

---

## 🚀 Quick Install

### Option A: Standalone Windows Installer (.exe) — No Python required
Download the latest setup wizard from **[GitHub Releases](https://github.com/s-celles/canopen-studio/releases)**:
- **`CANopen-Studio-v0.2.1-Windows-Setup.exe`** (Setup wizard with desktop & start menu shortcuts)
- **`CANopen-Studio-Windows-x64-Portable.zip`** (Standalone portable executable)

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
# Launch Graphical Studio
just gui

# Run CLI Sniffer on SLCAN at 500 kbps
just sniff

# Run without hardware (Virtual Simulation Mode)
just simulate

# Run tests and quality verification
just check
```

---

## 📚 Documentation

Detailed documentation, architecture guides, decoder tutorials, and screenshots are hosted on GitHub Pages:  
👉 **[https://s-celles.github.io/canopen-studio/](https://s-celles.github.io/canopen-studio/)**

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
