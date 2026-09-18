# Hardware & Interfaces

CAN & CANopen Studio supports a wide catalog of commercial and open-source USB-to-CAN converters.

---

## Supported Interfaces Catalog

| Identifier | Interface Name | Driver / Library | Common Hardware | Default Channels |
| :--- | :--- | :--- | :--- | :--- |
| **`slcan`** | SLCAN ASCII Serial | `pyserial` / FTDI / CDC-ACM | Lawicel CANUSB, USBtin, CANable, makerbase | `COM3`, `COM4`, `/dev/ttyUSB0` |
| **`pcan`** | PEAK-System PCAN | PCAN-Basic DLL / Driver | PCAN-USB, PCAN-USB Pro, PCAN-PCI | `PCAN_USBBUS1`, `PCAN_USBBUS2` |
| **`kvaser`** | Kvaser CANlib | Kvaser CANlib SDK | Leaf Light, Memorator, USBcan | `0`, `1` |
| **`vector`** | Vector Informatik | Vector XL Driver Library | VN1610, VN1630, VN5610, CANcase | `0`, `1` |
| **`ixxat`** | IXXAT VCI | IXXAT VCI v4 DLL | USB-to-CAN V2, compact, automotive | `0`, `1` |
| **`gs_usb`** | gs_usb (Candlelight) | WinUSB / libusb | Candlelight, Geschwister Schneider | `0` |
| **`socketcan`** | Linux SocketCAN | Native Linux Kernel | Any Linux CAN interface | `can0`, `vcan0` |
| **`udp_multicast`** | UDP Multicast (CAN over IP) | Network UDP Sockets | Any Local IP Network (Wi-Fi/Ethernet) | `224.0.0.1`, `239.0.0.1` |
| **`virtual`** | Virtual Simulator | In-memory loopback | Hardware-free educational simulation | `virtual_bus` |

---

## Lawicel CANUSB / Lextronic T851 (FTDI FT232R)

The Lawicel CANUSB adapter uses an FTDI USB-to-UART chip (`VID_0403` and `PID_6001`).

### Automatic Port Detection
When selecting the **SLCAN** interface in CAN & CANopen Studio, the software automatically scans connected Windows COM ports and detects the Lawicel CANUSB converter without manual COM port selection.

### Standard SLCAN Commands
- `S6` -> Configure 500 kbit/s
- `O`  -> Open CAN channel
- `C`  -> Close CAN channel
- `t123411223344` -> Transmit 11-bit frame
- `T18EAFFFE300EE00` -> Transmit 29-bit extended frame

---

## Virtual Simulator & Network CAN over IP (UDP Multicast)

CAN & CANopen Studio features a powerful built-in **Virtual CANopen Simulator** and supports **UDP Multicast** for creating virtual CAN networks without any physical hardware.

### UDP Multicast (CAN over IP)
By selecting the **UDP Multicast** interface, you can route CAN frames over your existing local network (Wi-Fi or Ethernet).
- **Channel**: In UDP Multicast mode, the "Channel" is actually the **Multicast IP Address** (e.g., `224.0.0.1`). 
- **Virtual Bus**: All instances of CAN & CANopen Studio (or other Python-CAN clients) on the same local network that connect to the exact same Multicast IP Address will act as if they are physically wired to the same CAN bus.
- **Isolation**: You can create multiple isolated virtual buses simultaneously by simply using different Multicast IPs (e.g., `239.0.0.1` and `239.0.0.2`).

### Simulating a CANopen Node
CANopen Studio includes a built-in virtual node that generates telemetry (Heartbeats, SYNC pulses, CiA 402/SEVCON PDOs, and SDO responses).
- **In the GUI**: Simply check the **"Simulate"** checkbox next to the Channel selector before clicking Connect. This will inject the simulated traffic directly onto the active bus (whether it's a physical USB interface, SocketCAN, or a UDP Multicast IP).
- **In the CLI**: You can attach the simulator to any interface via the `--simulate` flag:
  ```bash
  # Launch a simulation node broadcasting over the office Wi-Fi
  uv run can-sniffer -I udp_multicast -c 224.0.0.1 --simulate
  ```
