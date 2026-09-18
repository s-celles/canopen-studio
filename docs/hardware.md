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


### Network Impact & Security (Multicast vs. Broadcast)
It is common to wonder if injecting raw CAN bus traffic over an IP network will cause network congestion or "pollution". The risk is practically non-existent thanks to the choice of **UDP Multicast**:

- **Multicast is Smart (IGMP Snooping):** Unlike *Broadcast* messages (which are forced onto every device on the network), Multicast acts as an opt-in subscription. Modern network switches use IGMP to route these packets **only** to the specific computers that are currently running CANopen Studio and listening to that specific IP. Your printers, phones, and colleagues' computers will not receive this traffic.
- **Negligible Bandwidth:** A physical CAN bus loaded at 100% (500 kbit/s) generates less than 100 KB/s of network traffic. On a standard 1 Gigabit office network, this represents less than 0.1% of the available bandwidth.
- **Local Scope:** Multicast packets remain strictly on your local subnet (LAN). They are not routed out to the external internet.

### Reaching Machines on Another Subnet (Hop Limit)
Frames are carried as UDP datagrams on port **43113** with an IP hop limit (TTL) of **1** by default.
A hop limit of 1 means routers never forward the traffic: every participating machine must sit on the
**same network segment**. This is the safe default — it guarantees the bus cannot leak beyond your LAN.

To span several subnets, raise the hop limit to the number of routers the frames must cross:

Set the `CANOPEN_UDP_HOP_LIMIT` environment variable before launching the application:

```bash
# Allow the virtual bus to cross up to 4 routers
CANOPEN_UDP_HOP_LIMIT=4 uv run python can_gui.py
```

Or pass it per connection through the MCP / A2A `connect` tool, which overrides the variable:

```python
connect(interface="udp_multicast", channel="239.0.0.1", hop_limit=4)
```

Every machine must use the **same multicast address**; the hop limit only needs raising on the
*sending* side, but setting it everywhere keeps bidirectional traffic symmetric.

!!! warning "Routers must forward multicast"
    Raising the hop limit is necessary but not always sufficient: the intervening routers also need
    multicast routing (PIM) or an IGMP proxy enabled. On a plain office LAN without multicast routing,
    keep all machines on one segment.

## Bridging a Real CAN Bus onto the Network

A UDP multicast bus lets several machines share a *virtual* bus. The **bridge** goes one step
further: it mirrors the bus you are physically connected to — a drive, an inverter, a live
vehicle harness — onto a multicast group, so remote machines observe the real traffic as if
they were wired to the same bus.

One machine acts as the gateway: it holds the USB-CAN adapter and republishes everything it
captures. Every other machine simply connects to the same multicast address.

### From the GUI
Tick **Bridge → Net** in the connection toolbar and set the multicast address next to it, then
click **Connect**. The bridge is armed with the connection and torn down when you disconnect.

### From MCP / A2A

```python
bridge_start(channel="239.0.0.1")  # read-only mirroring
bridge_start(channel="239.0.0.1", hop_limit=4)  # across subnets
bridge_stop()
```

`get_status()` reports the bridge with its counters: `forwarded`, `injected`, `suppressed`,
`suppressed_inject` and `errors`.

### Direction of Traffic

By default the bridge is **read-only**: frames flow from the real bus to the network, never the
other way. Remote machines observe, they do not command.

Passing `allow_inject=True` opens the return path, replaying frames received from the network
onto the real bus:

```python
bridge_start(channel="239.0.0.1", allow_inject=True)
```

!!! danger "Injection writes to real hardware"
    With injection enabled, anyone on the multicast group can transmit on your physical CAN bus
    — which means commanding the connected device. On a drive this moves a motor. Enable it only
    on a trusted network and with the equipment in a safe state.

### Loop Protection
A multicast socket receives its own datagrams back. Without protection a bidirectional bridge
would re-inject every frame it just mirrored, flooding the real bus. The bridge therefore
remembers what it sends in each direction for a short window (250 ms by default) and discards
the reflection; the `suppressed` and `suppressed_inject` counters show this working.

Two further rules keep the topology sane:

- The bridge refuses to mirror a multicast group **onto itself**, which would loop immediately.
- Do not arm a bridge on two machines for the same pair of buses: each would relay the other's
  traffic back. One gateway per physical bus.

### Simulating a CANopen Node
CANopen Studio includes a built-in virtual node that generates telemetry (Heartbeats, SYNC pulses, CiA 402/SEVCON PDOs, and SDO responses).
- **In the GUI**: Simply check the **"Simulate"** checkbox next to the Channel selector before clicking Connect. This will inject the simulated traffic directly onto the active bus (whether it's a physical USB interface, SocketCAN, or a UDP Multicast IP).
- **In the CLI**: You can attach the simulator to any interface via the `--simulate` flag:
  ```bash
  # Launch a simulation node broadcasting over the office Wi-Fi
  uv run can-sniffer -I udp_multicast -c 224.0.0.1 --simulate
  ```
