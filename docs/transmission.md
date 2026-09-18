# Transmission Console

CAN & CANopen Studio includes a comprehensive transmission station for injecting frames and testing network reactions.

---

## 1. Network Management (NMT) Master

Control any node on the CANopen network (Node 1 to 127) or broadcast to all nodes (`Node 0`):

- **Start Node** (`0x01`): Transitions node into **Operational** state (enables PDO transmission and drive output).
- **Stop Node** (`0x02`): Transitions node into **Stopped** state (disables PDOs, only NMT commands accepted).
- **Enter Pre-Operational** (`0x80`): Transitions node into **Pre-Operational** (enables SDO configuration, disables PDOs).
- **Reset Node** (`0x81`): Hardware reset and reboot of target device.
- **Reset Communication** (`0x82`): Resets communication parameters to factory defaults.

---

## 2. Periodic Generators

- **High-Precision SYNC Generator**: Emits CANopen SYNC pulses (COB-ID `0x080`) at a user-defined frequency (default: 50 Hz / 20 ms).
- **Heartbeat Generator**: Emits NMT Master heartbeat frames to keep slave nodes in active communication.

---

## 3. Arbitrary Frame Transmitter

Send custom CAN frames with precise parameters:

- **Identifier**: Standard 11-bit or Extended 29-bit identifier (in hex or decimal).
- **Frame Type**: Data frame or Remote Transmission Request (`RTR`).
- **Payload**: Raw hex bytes separated by spaces (e.g. `01 02 03 AA FF`).
- **Timing**:
  - **Single Shot**: Transmit once upon click.
  - **Periodic Transmission**: Repeated transmission at specified interval in milliseconds (e.g. every 10 ms).

---

## 4. Frame Templates Library

Pre-loaded templates for instant testing:

- `CiA 402 Drive - Ready to Switch On` (Controlword `0x0006`)
- `CiA 402 Drive - Switch On` (Controlword `0x0007`)
- `CiA 402 Drive - Enable Operation` (Controlword `0x000F`)
- `CiA 402 Drive - Target Velocity (1000 RPM)` (RPDO2 Velocity command)
- `SEVCON Gen4 - Throttle Request` (RPDO1 Torque command)
- `J1939 - Address Claim Request` (PGN Request)
- `Raw CAN Ping`
