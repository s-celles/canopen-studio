# Protocol Stack & Application Decoders

CAN & CANopen Studio features an extensible application decoder architecture.

---

## Supported Decoder Profiles

### 1. Generic CANopen (CiA 301 / CiA 402)
- **CiA 301 (Base Communications)**:
  - Network Management (`NMT`, COB-ID `0x000`)
  - Synchronization (`SYNC`, COB-ID `0x080`)
  - Emergency frames (`EMCY`, COB-ID `0x081`..`0x0FF`)
  - Heartbeat & Boot-up (COB-ID `0x701`..`0x77F`)
  - SDO Request (`0x600 + NodeID`) and SDO Response (`0x580 + NodeID`)
- **CiA 402 (Drives & Motion Control)**:
  - Statusword (`0x6041`) bitfield decoding: *Not Ready to Switch On*, *Switch On Disabled*, *Ready to Switch On*, *Switched On*, *Operation Enabled*, *Quick Stop*, *Fault*
  - Controlword (`0x6040`) bitfield decoding
  - Target & Actual Velocity (`0x606C`) in RPM

### 2. SEVCON Gen4 AC/PMSM Inverter
- **TPDO1** (`0x148`): Inverter status vectors (v1 to v4)
- **TPDO2** (`0x441`): Motor status monitor (m1 to m4)
- **TPDO3** (`0x156`): Heatsink temperature (°C), raw motor temperature, auxiliary voltage
- **TPDO4** (`0x270`): Control mode, speed limit steps, target torque (`0x6071`)
- **TPDO5** (`0x473`): Actual motor speed (`0x606C`) and maximum speed (`0x6080`) in RPM

### 3. De Haardt Track Safety Transponder
- Speed mode decoding:
  - `0`: Emergency Stop (Red Flag)
  - `1`: Pit Lane Speed (Very Slow)
  - `2`: Slow Speed (Yellow Flag)
  - `3`: Normal Speed (Green Flag)
  - `4`: Boost / Fast Mode

### 4. J1939 Extended (29-bit)
- Extracts Priority (3 bits), Parameter Group Number (PGN, 18 bits), Source Address (8 bits), and Destination Address.

---

## Creating a Custom Decoder

To create a new decoder, simply create a class inheriting from `BaseDeviceDecoder` decorated with `@register_decoder`:

```python
from canopen_stack import BaseDeviceDecoder, CanopenMessage, register_decoder


@register_decoder
class MyCustomBmsDecoder(BaseDeviceDecoder):
    @property
    def device_name(self) -> str:
        return "Custom BMS Battery Monitor"

    @property
    def custom_cob_ids(self):
        return {
            0x190: "BMS Status & Voltage",
            0x290: "BMS Cell Temperatures",
        }

    def can_decode(self, message: CanopenMessage) -> bool:
        return message.arbitration_id in (0x190, 0x290)

    def decode(self, message: CanopenMessage):
        cid = message.arbitration_id
        data = message.data
        signals = {}

        if cid == 0x190 and len(data) >= 4:
            voltage = int.from_bytes(data[0:2], "little") * 0.1
            current = int.from_bytes(data[2:4], "little", signed=True) * 0.1
            signals["pack_voltage_v"] = voltage
            signals["pack_current_a"] = current
            message.decoded_info = f"BMS -> Voltage: {voltage:.1f}V | Current: {current:.1f}A"

        return signals
```
The application will automatically discover, load, and display your decoder in the user interface!
