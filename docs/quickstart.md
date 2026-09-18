# Quick Start Guide

Get started with **CAN & CANopen Studio** in less than 2 minutes.

---

## 1. Launching with Hardware

1. Connect your USB-to-CAN converter (e.g. Lawicel CANUSB, PEAK PCAN-USB, CANable).
2. Start the graphical studio:
   ```bash
   just gui
   # or via installed shortcut or executable
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

# Sniff at 250 kbps
just sniff 250000

# Run in virtual simulation mode
just simulate

# Launch real-time console dashboard (RPM, Temperatures, Status)
just dashboard

# Filter for a specific CAN ID
just filter 0x473

# Record frames to a CSV file
just record capture_log.csv
```
