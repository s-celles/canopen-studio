# CAN & CANopen Studio - Universal Protocol Analyzer & Transmit Station

A general-purpose open-source station for studying, reverse-engineering, monitoring, and transmitting on **CAN** (Controller Area Network) and **CANopen** (CiA 301 / CiA 402) networks.

Managed with modern Python packaging: **`pyproject.toml`** (PEP 621), **`uv`**, and **`just`**.

---

## 1. Supported Market Hardware Interfaces & Virtual Simulator

The application supports multiple commercial CAN adapters via `python-can` drivers:

| Interface Key | Hardware & Vendor | Default Channels / Ports | Notes |
| :--- | :--- | :--- | :--- |
| **`slcan`** | **SLCAN / Lawicel CANUSB**, USBtin, CANable | `COM4`, `COM1..`, `/dev/ttyUSB0` | Auto-detects FTDI VID_0403 / PID_6001 |
| **`pcan`** | **PEAK-System PCAN-USB**, PCAN-PCI | `PCAN_USBBUS1`, `PCAN_USBBUS2` | Uses PCAN-Basic DLL / driver |
| **`kvaser`** | **Kvaser Leaf Light**, Memorator, USBcan | `0`, `1`, `2` | Uses Kvaser CANlib |
| **`vector`** | **Vector VN1610, VN1630, VN5610**, CANcase | `0`, `1` | Uses Vector XL Driver Library |
| **`ixxat`** | **HMS IXXAT USB-to-CAN V2**, compact | `0`, `1` | Uses IXXAT VCI driver |
| **`gs_usb`** | **Candlelight / CANable (gs_usb)** | `0`, `1` | Native USB CAN firmware (WinUSB) |
| **`socketcan`**| **Linux SocketCAN** | `can0`, `can1`, `vcan0` | Native Linux network interface |
| **`virtual`** | **Virtual Simulator** | `virtual_bus` | **Hardware-free teaching & testing loopback** |

> [!TIP]
> **No hardware?** Select the **Virtual Simulator** interface to run a built-in virtual CANopen drive emitting live heartbeats, 50 Hz SYNC pulses, dynamic motor velocity ramps, and reacting to NMT master commands and SDO queries!

---

## 2. Supported Device Profiles & Application Decoders

- **Generic CANopen (CiA 301 / CiA 402)**: Decodes standard NMT, SYNC, EMCY, TIME, TPDO1..4, RPDO1..4, SDO Tx/Rx, Heartbeat, and standard CiA 402 motor status words / velocity / position.
- **SEVCON Gen4 Motor Inverter**: Customized TPDOs (`0x148`, `0x156`, `0x270`, `0x441`, `0x473`) for electric karts (RPM, Target Torque, Heatsink Temp).
- **De Haardt Kart Safety Transponder**: Decodes speed regulation modes, slow flag, and emergency stop.
- **J1939 Extended (29-bit)**: Commercial vehicles, trucks, tractors (PGN, Priority, Source/Destination addresses).
- **Raw CAN**: Clean raw byte exploration without application interpretation.

---

## 3. Project Management with `just` and `uv`

The project uses a modern **`pyproject.toml`** (PEP 621) with **`uv`** and **`just`** task automation.

### Available `just` commands:

| Command | Action |
| :--- | :--- |
| `just` | Show list of available recipes |
| `just setup` | Sync dependencies and lockfile (`uv sync`) |
| `just sniff` | Run sniffer at 500 kbit/s |
| `just sniff 250000` | Run sniffer at 250 kbit/s |
| `just gui` | Start full application (Dashboard, Trace, Frame Transmitter, SDO) |
| `just can-explorer` | Launch Tbruno25/can-explorer (Real-time payload graphing tool) |
| `just extended` | Monitor 29-bit Extended frames only |
| `just standard` | Monitor 11-bit Standard frames only |
| `just filter 0x473` | Filter a specific CAN ID (e.g. RPM `0x473`) |
| `just record capture.csv` | Save frames to a CSV file |
| `just sample 50` | Capture 50 frames then stop |
| `just listen-only` | Passive listen-only mode (no ACK frames transmitted) |
| `just clean` | Remove temporary cache files and CSV logs |

---

## 4. Direct CLI Commands (via `uv run`)

You can also run commands directly with `uv run`:

```powershell
# Standard capture
uv run python can_sniffer.py

# Live dashboard
uv run python can_sniffer.py --dashboard

# 29-bit Extended frames only
uv run python can_sniffer.py --extended-only

# Capture 10 seconds to CSV
uv run python can_sniffer.py -t 10 -o capture.csv
```

---

## 5. CANopen Abstraction Layer & Decoder Extension Mechanism

The project includes an extensible architecture under [`canopen_stack`](file:///C:/Users/scelles/Downloads/Huard/CANopen_kart/canopen_stack):

```
┌──────────────────────────────────────────────────────────┐
│             Application Layer / GUI / CLI                │
└────────────────────────────▲─────────────────────────────┘
                             │ Dispatches decoded signals
┌────────────────────────────┴─────────────────────────────┐
│          Decoder Registry (Extension Mechanism)          │
│   ├── SEVCON Gen4 Decoder (RPM, Torque, Temps, etc.)     │
│   ├── De Haardt Transponder Decoder (Speed Modes, Safety)│
│   └── Custom Application Decoders (BMS, Sensors, etc.)   │
└────────────────────────────▲─────────────────────────────┘
                             │ Translates services (NMT, PDO, SDO)
┌────────────────────────────┴─────────────────────────────┐
│                 CANopenLayer (CiA 301)                   │
│   State Tracking (Node States), NMT/SYNC/SDO Dispatch    │
└────────────────────────────▲─────────────────────────────┘
                             │ Raw Frames (ID, DLC, Data)
┌────────────────────────────┴─────────────────────────────┐
│             CAN Bus Hardware Layer (python-can)          │
│              CANUSB Lextronic T851 (SLCAN)               │
└──────────────────────────────────────────────────────────┘
```

### How to add a new device decoder:

To add support for a new device on the bus (e.g. a Battery Management System or telemetry unit), simply create a file in `canopen_stack/decoders/my_device.py`:

```python
from canopen_stack import BaseDeviceDecoder, CanopenMessage, register_decoder

@register_decoder
class MyBmsDecoder(BaseDeviceDecoder):
    @property
    def device_name(self) -> str:
        return "BMS Battery Monitor"

    @property
    def supported_node_ids(self) -> list[int]:
        return [0x0A]  # Node ID 10

    def decode(self, message: CanopenMessage) -> dict:
        # Unpack raw CAN data into named physical signals
        if message.arbitration_id == 0x18A:  # TPDO1
            pack_voltage = int.from_bytes(message.data[0:2], "little") * 0.1
            current = int.from_bytes(message.data[2:4], "little", signed=True) * 0.1
            message.decoded_info = f"BMS -> Voltage: {pack_voltage:.1f}V, Current: {current:.1f}A"
            return {"battery_voltage_v": pack_voltage, "battery_current_a": current}
        return {}
```
Any registered decoder is automatically picked up by both the CLI sniffer and the GUI dashboard!

---

## 6. Graphical Application - CAN & CANopen Studio (`just gui`)

Run `just gui` to launch the multi-tab graphical suite:

### Tab 1: Network Monitor & Live Dashboard
Real-time node state discovery table (CiA 301), active services tracking, bus rate in msgs/s, and dynamic telemetry gauges (speed, torque, temperatures, status words).

![Network & Dashboard](docs/images/01_network_dashboard.png)

### Tab 2: 📈 Real-Time Reverse Engineering Plotter
Time-series plotter capable of tracking any CAN ID or decoded signal. Supports simultaneous 8-byte payload plotting (`B0` to `B7`), 16-bit / 32-bit little-endian words, adjustable rolling window, pause/resume, and interactive pan/zoom.

![Reverse Engineering Plotter](docs/images/02_reverse_plotter.png)

### Tab 3: 📋 Real-Time Message Trace
Scrolling message table with standard/extended frame classification, CAN ID filtering, and one-click CSV logging export.

![Real-time Trace](docs/images/03_realtime_trace.png)

### Tab 4: 🚀 Transmit & Bus Stimulation Console
Full transmission studio featuring:
- **CANopen NMT Master**: Start, Stop, Pre-Op, and Reset for any Node ID (`0` to `127`).
- **Periodic Producers**: 50 Hz SYNC clock generator and configurable Heartbeat producer.
- **Preset Templates**: 1-click loading of NMT commands, SYNC, CiA 402 drive state machine transitions, and J1939 frames.
- **Arbitrary Frame Transmitter**: 11-bit / 29-bit, RTR, arbitrary payload, and periodic timer.

![Transmit & Control](docs/images/04_transmit_control.png)

### Tab 5: 📖 Universal SDO Object Dictionary Client
Perform Expedited SDO Uploads (Read) and Downloads (Write) for any server Node ID. Decodes responses in hex, unsigned, signed, and ASCII representations. Includes quick-inquiry buttons for standard CiA 301 and CiA 402 indices.

![SDO Object Dictionary](docs/images/05_sdo_explorer.png)

### Tab 7: 📚 CANopen Educational Quick Reference
Built-in interactive cheat sheet covering standard COB-IDs, NMT commands, SDO protocol formats, and essential object dictionary indices.

![CANopen Reference Guide](docs/images/06_canopen_reference.png)

---

## 7. Author & License

- **Author**: **Sébastien Celles**
- **License**: [GNU General Public License v3.0 (GPL-3.0-or-later)](file:///C:/Users/scelles/Downloads/Huard/CANopen_kart/LICENSE)
- **Copyright**: © 2026 Sébastien Celles

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.



