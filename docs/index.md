# CAN & CANopen Studio

<p align="center">
  <strong>Universal CAN & CANopen Protocol Analyzer, Telemetry Plotter, and Transmit Station</strong>
</p>

---

**CAN & CANopen Studio** is a modern, modular, cross-platform protocol engineering suite designed for testing, reverse-engineering, diagnosing, and interacting with CAN bus networks and CANopen automation devices.

Initially created for electric kart telemetry (SEVCON Gen4 inverter & De Haardt safety transponders), it has been completely generalized into a universal tool compatible with industry standard hardware adapters, CiA 301 / CiA 402 drives, J1939 commercial vehicles, and arbitrary CAN traffic.

---

## Key Highlights

- **Multi-Adapter Hardware Support**: Direct plug-and-play support for SLCAN (Lawicel CANUSB, USBtin, CANable), PEAK-System PCAN, Kvaser, Vector XL, IXXAT, gs_usb (Candlelight), Linux SocketCAN, and built-in Virtual simulation loop.
- **Protocol & Device Decoders**: Extensible decoding engine for generic CANopen (CiA 301 / CiA 402), SEVCON Gen4 inverters, De Haardt track safety transponders, 29-bit J1939 frames, and Raw CAN.
- **Multi-Trace Telemetry Oscilloscope**: Real-time simultaneous time-series plotting of all 8 payload bytes (B0 to B7) or decoded physical values (RPM, torque, temperatures, voltage).
- **Network Node Monitor & Gauges**: Live visual tracking of node NMT states (Operational, Pre-Operational, Stopped), message counters, and real-time RPM / torque dials.
- **Full Transmission Console**: Generic NMT Master controller, high-precision SYNC clock pulse generator (50 Hz), arbitrary frame transmitter, and pre-configured frame templates library.
- **SDO Object Dictionary Explorer**: Expedited dictionary reader and writer for inspecting and calibrating any CANopen node.
- **OBD-II Vehicle Diagnostics (SAE J1979)**: Live parameters, trouble codes and VIN over an ELM327 (USB, Bluetooth SPP, Wi-Fi) or straight over a native CAN adapter via ISO-TP. Supported PIDs are discovered from the vehicle rather than assumed, vehicle profiles inherit from a generic J1979 base, and everything is read-only by default.
- **Native Rust Engine**: Frame parsing, CANopen classification, ISO-TP reassembly and the virtual simulator run as compiled code shared by every front end — see the [measured results](benchmarks.md).
- **AI Agent Integration**: Built-in MCP and A2A servers let an AI agent read the live trace, inspect nodes and telemetry, transmit frames, drive the NMT state machine and control the network bridge.
- **Real Bus Network Bridge**: Mirror a physical CAN bus — a drive, an inverter, a live harness — onto a UDP multicast group so remote machines observe the real traffic as if they were wired to it.
- **Two Front Ends, One Engine**: The full-featured Python studio, and a lean native front end built with Slint, both driving the same protocol core.
- **Integrated In-App Updater**: Automated background checks against GitHub Releases, direct 1-click Windows installer upgrade, and Git update automation.

---

## Quick Navigation

**Getting started**

- [Installation Guide](installation.md) — standalone installer, 1-click script, or Python package.
- [Quick Start](quickstart.md) — connect hardware or launch the virtual simulator in seconds.
- [Screenshots Gallery](gallery.md) — a tour of the graphical studio.

**Using the bus**

- [Hardware & Interfaces](hardware.md) — supported USB-to-CAN converters, UDP multicast and the real-bus bridge.
- [Protocol Decoders](decoders.md) — how CAN packets and devices are decoded, and how to add your own.
- [Telemetry & Oscilloscope](telemetry.md) — multi-trace plotting and signal reverse engineering.
- [Transmission Console](transmission.md) — NMT master, SYNC generator and frame templates.
- [SDO & NMT Master](sdo_nmt.md) — reading and writing the object dictionary.
- [OBD-II Vehicle Diagnostics](obd.md) — live parameters, trouble codes and VIN from a vehicle.

**Under the hood**

- [Architecture](architecture.md) — one engine, two front ends, and where each piece lives.
- [Native Engine & Front End](rust_core.md) — what the Rust core accelerates, and how to build it.
- [Command-Line Tools](cli.md) — the Python sniffer and the native `canopen-cli`.
- [Performance Benchmarks](benchmarks.md) — measured latency, jitter and throughput.

**Automation and project**

- [AI Integration](ai_integration.md) — drive the bus from an AI agent over MCP or A2A.
- [In-App Updates](updates.md) — how the studio updates itself.
- [Roadmap](roadmap.md) and [Changelog](changelog.md).
