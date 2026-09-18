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
