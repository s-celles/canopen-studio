# SDO Object Dictionary & NMT Management

CAN & CANopen Studio includes a full-featured Service Data Object (SDO) communication console for querying and modifying parameters in target device Object Dictionaries.

---

## Service Data Objects (SDO)

CANopen uses SDO for client/server peer-to-peer communication between the master (CANopen Studio) and target nodes.

- **Client-to-Server (SDO Rx)**: COB-ID `0x600 + NodeID`
- **Server-to-Client (SDO Tx)**: COB-ID `0x580 + NodeID`

---

## Reading Object Dictionary Entries (SDO Upload)

1. Navigate to the **SDO Object Dictionary** tab.
2. Select target **Node ID** (e.g. `1`).
3. Enter the 16-bit **Index** in hex (e.g. `0x1000` for Device Type).
4. Enter the 8-bit **Sub-index** in hex (e.g. `0x00`).
5. Click **Read (SDO Upload)**.
6. The application displays:
   - Raw bytes returned by the device.
   - Decoded integer / string interpretation.
   - Status: Success or SDO Abort code.

---

## Common CANopen Standard Dictionary Indices

| Index | Sub-index | Object Description | Typical Format |
| :--- | :--- | :--- | :--- |
| `0x1000` | `0x00` | Device Type | `UNSIGNED32` (CiA profile number) |
| `0x1001` | `0x00` | Error Register | `UNSIGNED8` bitmask |
| `0x1008` | `0x00` | Manufacturer Device Name | `VISIBLE_STRING` |
| `0x1009` | `0x00` | Manufacturer Hardware Version | `VISIBLE_STRING` |
| `0x100A` | `0x00` | Manufacturer Software Version | `VISIBLE_STRING` |
| `0x1017` | `0x00` | Producer Heartbeat Time | `UNSIGNED16` (milliseconds) |
| `0x1018` | `0x01` | Identity Object: Vendor ID | `UNSIGNED32` |
| `0x1018` | `0x02` | Identity Object: Product Code | `UNSIGNED32` |
| `0x6040` | `0x00` | CiA 402: Controlword | `UNSIGNED16` |
| `0x6041` | `0x00` | CiA 402: Statusword | `UNSIGNED16` |
| `0x6060` | `0x00` | CiA 402: Modes of Operation | `INTEGER8` |
| `0x606C` | `0x00` | CiA 402: Velocity Actual Value | `INTEGER32` (RPM or units/s) |
| `0x607A` | `0x00` | CiA 402: Target Position | `INTEGER32` |

---

## Writing Object Dictionary Entries (SDO Download)

1. Specify Node ID, Index, Sub-index.
2. Enter the new value (decimal or hex).
3. Select the data length (1, 2, or 4 bytes).
4. Click **Write (SDO Download)**.
5. The device validates the value and returns a confirmation acknowledgment.

---

## Network Management (NMT) & Heartbeat Monitoring

CANopen uses Network Management (COB-ID `0x000`) for commanding nodes between operational states, and Heartbeat messages (`0x700 + NodeID`) for presence and liveness verification.

### Supported NMT Master Commands (CiA 301)
- **Start Remote Node (`0x01`)**: Transitions the node into `Operational` state. PDO communication is active.
- **Stop Remote Node (`0x02`)**: Transitions the node into `Stopped` state.
- **Enter Pre-Operational (`0x80`)**: Enables SDO configuration while pausing PDO transmission.
- **Reset Node (`0x81`)**: Re-initializes device application and communication parameters.
- **Reset Communication (`0x82`)**: Re-initializes device communication stack only.

### High-Performance Native NMT Engine
When `canopen_core` is compiled, NMT master command synthesis, state machine tracking, and microsecond-level heartbeat timeout detection are handled in zero-allocation native Rust (`canopen_core.NmtMaster`).

---

## Electronic Data Sheets (EDS) & Object Dictionary Engine (CiA 306)

The compiled native core includes a CiA 306 Electronic Data Sheet parser (`canopen_core.EdsFile`):

- **Device & File Metadata**: Reads `[FileInfo]` and `[DeviceInfo]` (Vendor Name, Vendor ID, Product Code, Revision Number).
- **Object Dictionary Instantiation**: Parses index entries (`[1000]`) and subindex entries (`[1018sub1]`) with data types (`UNSIGNED32`, `INTEGER16`, `BOOLEAN`, etc.), access permissions (`ro`, `rw`, `const`), and default values.
- **Automated PDO Mapping Synthesis**: Given a TPDO/RPDO mapping index (e.g. `0x1A00` for TPDO1 or `0x1600` for RPDO1), automatically builds a bit-accurate `PdoMapping` structure with resolved signal names, bit offsets, and engineering types:

```python
from canopen_studio import canopen_core

eds = canopen_core.EdsFile.load_file("motor_controller.eds")
tpdo1 = eds.create_pdo_mapping(cob_id=0x181, mapping_index=0x1A00)
signals = tpdo1.decode_frame(frame)
```


