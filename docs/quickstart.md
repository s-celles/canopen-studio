# Quick Start Guide

Get started with **CAN & CANopen Studio** in less than 2 minutes.

---

## 1. Launching with Hardware

1. Connect your USB-to-CAN converter (e.g. Lawicel CANUSB, PEAK PCAN-USB, CANable).
2. Start the graphical studio:
   ```bash
   just gui          # the Python studio
   just rust-gui     # or the native front end, same engine
   # or via the installed shortcut or the standalone executable
   ```
3. In the top toolbar:
   - Select your adapter under **Interface** (e.g., `SLCAN` or `PEAK-System PCAN`).
   - Select the target **Bitrate** (e.g., `500000` for 500 kbit/s).
   - Click **Connect**.
4. The network monitor immediately begins discovering nodes, decoding PDOs, and logging traffic!

---

## 2. No Hardware? Use the Built-in Virtual Simulator!

You can explore all features, test decoders, and learn CANopen without any hardware:

1. In the top toolbar, select **Interface**: `Virtual Simulator (In-Memory Loopback)`.
2. Click **Connect**.
3. The simulator launches in-memory synthetic CANopen traffic:
   - **Node 1 Heartbeat** (COB-ID `0x701`) & **Node 2 Heartbeat** (`0x702`)
   - **SYNC clock pulses** (COB-ID `0x080`) at 50 Hz
   - **CiA 402 TPDO1/2** motor speed oscillating between 400 and 3200 RPM
   - **SEVCON Gen4 TPDOs** with simulated torque and temperatures
   - Responds to NMT Master commands and SDO queries in real-time!

---

## 3. Command-Line Sniffer (Terminal Mode)

If you prefer working in a headless console or shell:

```bash
# Sniff traffic on default SLCAN adapter at 500 kbps
just sniff

# Sniff at 250 kbps — the recipe takes the interface first, then the bitrate
just sniff slcan 250000

# Run in virtual simulation mode for 10 seconds
just simulate 10

# Launch real-time console dashboard (RPM, Temperatures, Status)
just dashboard

# Filter for a specific CAN ID
just filter 0x473

# Record frames to a CSV file
just record capture_log.csv
```

Every option is listed in [Command-Line Tools](cli.md).

---

## 4. A Virtual Bus Without a Window

The native CLI runs the same simulated nodes headlessly over UDP, which gives the
command-line tools and the native front end a bus to work against with no hardware and no
window open:

```bash
just rust-simulate 1750 0     # transmit to port 1750, run until interrupted
```

Anything speaking the same UDP transport can then listen — `canopen-cli sniff --port 1750`
in another terminal, or the benchmark commands in [Command-Line Tools](cli.md).

To share a bus between **machines** rather than processes, use the UDP multicast
interface of the studio itself, described in [Hardware & Interfaces](hardware.md).

---

## 5. Diagnosing a Vehicle

Plug an ELM327 into the OBD-II socket, or wire a native CAN adapter to pins 6 and 14,
then open the **🩺 OBD-II Diagnostics** tab. The full procedure — adapter setup,
supported-PID discovery, trouble codes, VIN, and the safety gates around writing — is in
[OBD-II Vehicle Diagnostics](obd.md).
