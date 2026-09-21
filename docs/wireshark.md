# Wireshark Integration (PCAP-NG)

CAN & CANopen Studio writes captures in **PCAP-NG** with the
`LINKTYPE_CAN_SOCKETCAN` encapsulation, which Wireshark reads natively — the
same format a `libpcap` capture of a Linux SocketCAN interface produces. No
plugin, no conversion step.

Wireshark then brings its own dissectors: **CANopen**, **SAE J1939**,
**ISO 15765 (ISO-TP)**, DeviceNet, AUTOSAR NM, XCP. The studio ships none of
its own; it only hands over the frames in a shape Wireshark recognises.

---

## Writing a capture file

```powershell
# Everything the adapter sees, into a file
uv run can-sniffer -I slcan -b 500000 --pcap bus.pcapng

# Bounded capture: 10 seconds of a virtual bus
uv run can-sniffer -I virtual --simulate -t 10 --pcap sim.pcapng
```

Open `bus.pcapng` in Wireshark, or check it from the shell:

```powershell
& "C:\Program Files\Wireshark\capinfos.exe" bus.pcapng
& "C:\Program Files\Wireshark\tshark.exe" -r bus.pcapng
```

```text
File encapsulation:  SocketCAN
File timestamp precision:  microseconds (6)

1   0.000000    CAN 16 ID: 128 (0x080), Length: 0
2   0.001000    CAN 16 ID: 1793 (0x701), Length: 1
3   0.002000    CAN 16 ID: 385 (0x181), Length: 4
```

!!! note "The capture is never filtered"
    `--pcap` records every frame the adapter delivers, before the display
    filters (`--id`, `--extended-only`, …) are applied. Wireshark's own filters
    are better than ours, and a capture that silently omits frames is a trap for
    whoever reads it later.

---

## Live capture through a named pipe

Wireshark can read a capture as it is being written. **Wireshark connects to a
pipe; it never creates one** — so the studio is the server, and it goes first.

**Windows**

```powershell
# 1. The studio first: it creates the pipe, then waits for a reader
uv run can-sniffer -I slcan -b 500000 --pcap \\.\pipe\canopen-studio

# 2. Then Wireshark, in another terminal, connecting to that pipe
& "C:\Program Files\Wireshark\Wireshark.exe" -i \\.\pipe\canopen-studio -k
```

The sniffer prints `Waiting for Wireshark to connect …` and blocks there. That
is not a hang: nothing can be captured until something is listening.

**Linux / macOS**

Here the FIFO is made up front and the rendezvous happens on open, so either
order works:

```bash
mkfifo /tmp/canopen-studio
wireshark -i /tmp/canopen-studio -k &
uv run can-sniffer -I socketcan --channel can0 --pcap /tmp/canopen-studio
```

If Wireshark is closed mid-session the sniffer reports `PCAP-NG capture stopped`
and keeps sniffing — a viewer going away is not a reason to lose the session.

---

## Decoding frames as CANopen

Wireshark dissects the CAN layer on its own, but the payload above it is
ambiguous: the same 11-bit ID space is CANopen on one bus and something
proprietary on the next. So the CANopen dissector has to be selected.

**In the GUI**: *Analyze → Decode As…*, add an entry for the `CAN next level
dissector` field, choose **CANopen**.

**From the shell**:

```powershell
& "C:\Program Files\Wireshark\tshark.exe" -r bus.pcapng -d can.subdissector=canopen -V
```

```text
CANopen
    COB-ID: 0x00000601
        [Function code: 0xc]
        [Node-ID: 0x01]
    Type: Default-SDO (rx)
        SDO command byte: 0x40, Client command specifier: Initiate upload request
            010. .... = Client command specifier: Initiate upload request (2)
        OD main-index: Device type (0x1000)
        OD sub-index: 0x00
```

Use `j1939` instead for 29-bit commercial-vehicle traffic, or `iso15765` for
OBD-II and UDS diagnostics carried over ISO-TP.

---

## Useful display filters

| Filter | Shows |
| :--- | :--- |
| `can.id == 0x080` | SYNC objects |
| `can.id >= 0x701 && can.id <= 0x77f` | heartbeats |
| `can.id >= 0x581 && can.id <= 0x5ff` | SDO responses |
| `can.flags.rtr == 1` | remote requests |
| `can.flags.err == 1` | error frames |
| `canopen.sdo.abortcode` | SDO aborts, once decoded as CANopen |

---

## From your own code

The writer lives in the Rust core and is exposed to Python, so any script can
produce a capture:

```python
from canopen_studio import canopen_core

with canopen_core.PcapNgWriter("bus.pcapng", "slcan COM4") as capture:
    capture.write_frame(0x080, b"")  # SYNC
    capture.write_frame(0x701, bytes([0x05]))  # heartbeat
    capture.write_frame(0x18EAFFFE, bytes([0xEE]), None, True)  # 29-bit
    capture.write_frame(0x123, b"", None, False, True, False, 8)  # RTR, DLC 8
```

`write_frame(id, data, timestamp_us, is_extended, is_remote, is_error, dlc)` —
everything after `id` is optional. The timestamp is microseconds since the Unix
epoch; left out, the current time is used. `dlc` only matters for a remote
frame, which requests a length it does not carry.

In Rust:

```rust
use canopen_core::{CanFrame, PcapNgWriter};

let mut capture = PcapNgWriter::new(File::create("bus.pcapng")?, "can0")?;
capture.write_frame(&CanFrame::new(0x181, &[0x10, 0x27])?)?;
```

Every frame is flushed as it is written, which is what makes the pipe work.

---

## Related

- [Logic Analyzer & Signal Analysis](logic_analyzer.md) — watching the same bus
  one layer below, and why that catches what an adapter cannot report.
- [Hardware & Interfaces](hardware.md) — the adapters that feed the capture.
