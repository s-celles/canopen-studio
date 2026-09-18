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
- **Integrated In-App Updater**: Automated background checks against GitHub Releases, direct 1-click Windows installer upgrade, and Git update automation.

---

## Quick Navigation

- [Installation Guide](installation.md) - Download standalone `.exe` setup, 1-click script, or Python CLI.
- [Quick Start](quickstart.md) - Connect hardware or launch the virtual simulator in seconds.
- [Hardware & Interfaces](hardware.md) - Supported USB-to-CAN converters and drivers.
- [Protocol Decoders](decoders.md) - How to decode custom CAN packets and devices.
- [Screenshots Gallery](gallery.md) - Tour the graphical studio interface.
