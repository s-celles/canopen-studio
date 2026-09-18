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
