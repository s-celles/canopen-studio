# Watching the Wire: Logic Analyzer & Signal Analysis

CAN & CANopen Studio decodes **frames**. A logic analyzer shows you the **bits**
those frames are made of — the stuff bits, the CRC, the ACK slot, the timing.
The two views answer different questions, and together they let you check one
against the other.

!!! info "What this page is for"
    Building a bench where one CAN bus is observed by two instruments at once:
    the studio (through a USB-CAN adapter) and PulseView (through a logic
    analyzer and a transceiver). Nothing here is required to use the studio.

---

## Why a second instrument

A USB-CAN adapter hands you frames its controller has already accepted. That is
exactly what you want most of the time, and exactly what hides the interesting
failures:

| Question | The adapter can answer | A logic analyzer can answer |
| :--- | :---: | :---: |
| What was transmitted? | yes | yes |
| Was my frame acknowledged? | no | **yes** |
| Why is this node error-passive? | no | **yes** |
| Is the bit timing off? | no | **yes** |
| Did the adapter drop frames? | no | **yes** |
| What does the payload mean? | **yes** | no |

The last two rows are the reason to run both. An SLCAN adapter such as the
Lawicel CANUSB reports frames as ASCII over a serial link — around twenty
characters per frame — and a busy 500 kbit/s bus produces roughly 3800 full
frames per second. When the adapter cannot keep up it simply stops reporting
some of them, silently. The analyzer, which sees the differential pair itself,
is the independent witness that tells you how much you lost.

---

## Bill of materials

| Part | Role | Notes |
| :--- | :--- | :--- |
| USB-CAN adapter | Participates in the bus, feeds the studio | Lawicel CANUSB, USBtin, CANable — see [Hardware & Interfaces](hardware.md) |
| **CAN transceiver board** | Turns the differential pair into a logic signal | SN65HVD230 (3.3 V), MCP2551 (5 V) |
| **USB logic analyzer** | Samples that logic signal | Any FX2-based 8-channel 24 MHz unit |
| Jumper wires | | |

A transceiver is **not** a CAN controller. It has no arbitration, no CRC, no
acknowledgement — it only converts voltages. That is all this montage needs,
because the decoding happens in software. It also means a USB-to-serial
converter (FTDI, CP2102) cannot replace an adapter: a UART has no idea what a
CAN frame is.

---

## Wiring

The transceiver sits on the bus as a listener, in parallel with everything else.
The CANUSB uses the standard CiA 303-1 DB9 pinout:

```text
CANUSB DB9 ─┬─ pin 7  CAN_H ─┬──> [SN65HVD230] ──RXD──> [analyzer D0] ──> PulseView
            ├─ pin 2  CAN_L ─┤
            └─ pin 3  GND  ──┴──> common ground (transceiver + analyzer)
```

- Only **RXD** is probed. Nothing is transmitted through the transceiver, so TXD
  stays unconnected — tie it to the transceiver's idle level per its datasheet
  if you prefer, but leave it out of the bus.
- The FX2 input threshold sits near 1.4 V, so it reads 3.3 V logic without any
  level shifting.
- **Ground must be common** between the bus, the transceiver and the analyzer.
  Without it the analyzer samples noise.

!!! warning "Check the termination before you connect"
    A CAN bus is terminated by **two** 120 Ω resistors, one at each end, which
    read as 60 Ω across CAN_H and CAN_L on an idle bus. Many small transceiver
    boards carry a third 120 Ω soldered on. Adding it drops the bus to 40 Ω and
    degrades signalling **for every node**, not just yours. Measure first; if
    your board has one and the bus is already terminated, desolder it.

---

## PulseView setup

1. **Install the WinUSB driver.** On Windows an FX2 analyzer has no usable
   native driver: run **Zadig**, select the device, assign **WinUSB**. Skipping
   this is the reason PulseView shows no device at all.
2. **Pick the driver**: *fx2lafw*. Analyzers flashed with sigrok's own USB ID
   (`1d50:608c`, "sigrok FX2 LA (8ch)") are recognised immediately. Generic
   clones enumerate as `0925:3881` (Saleae clone) or `04b4:8613` (bare Cypress
   board) and work just as well.
3. **Sample rate**: **12 MHz** for a 500 kbit/s bus — 24 samples per bit, which
   is plenty. These analyzers have no capture memory and stream everything over
   USB, so the top of their range is where samples start being dropped. Reserve
   24 MHz for 1 Mbit/s, on as few channels as possible.
4. **Add the decoder**: *CAN*, channel `CAN RX` = D0, bitrate 500000, sample
   point 75 %.

---

## The acknowledgement trap

A transmitted CAN frame must be acknowledged by **another node**. Put your
adapter alone on a bus and every frame you send is retransmitted forever, until
the controller goes error-passive and then bus-off. It looks like a broken
adapter; it is a bus with nobody home.

So a first bench needs a real counterpart:

- **A vehicle's OBD-II port** is the easiest. Pin 6 is CAN_H, pin 14 is CAN_L,
  500 kbit/s on virtually every car built since 2008. Listening changes
  nothing, and the studio's [OBD-II diagnostics](obd.md) speak to it directly.
- **A second adapter**, or any CANopen node on the bench.
- **Listen-only mode** (`--listen-only`) avoids the problem by never
  transmitting — but then you cannot test transmission.

Meanwhile the logic analyzer needs no counterpart at all: it watches whatever
traffic exists, and its decoder shows the ACK slot, so you can see with your own
eyes whether anybody answered.

---

## Reading one bus with both instruments

```powershell
# Terminal 1 — frames, decoded, plus a capture for Wireshark
uv run can-sniffer -I slcan -b 500000 --pcap bus.pcapng

# PulseView — bits, on the same wire, at the same moment
```

Compare the two afterwards. Every frame PulseView decoded should appear in the
capture; anything missing was dropped between the transceiver and the studio,
which is a measurement worth keeping. See [Wireshark Integration](wireshark.md)
for what to do with the capture.

---

## What sigrok cannot do here

PulseView displays **sampled signals**, not frames handed to it by another
program. There is no way to stream the studio's decoded traffic into it without
writing a `libsigrok` driver in C, and that is not worth doing: Wireshark
already covers live frame analysis.

A future `bitstream` module in `canopen-core` will be able to *reconstruct* a
waveform from a decoded frame — SOF, arbitration, stuffing, CRC-15, ACK, EOF —
and export it as VCD for PulseView. That is useful for teaching, because the
stuff bits become visible. It is worth being blunt about what such a file is: a
**plausible reconstruction, not a measurement**. Only a real probe on the real
pair tells you what actually happened on the wire.
