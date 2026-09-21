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

## Reconstructed waveforms (`--vcd`)

PulseView displays **sampled signals**, not frames handed to it by another
program. But a frame determines the bits it was made of, so the studio can
rebuild the waveform and write it as VCD, which PulseView reads natively:

```powershell
# Capture from the adapter and rebuild the waveform
uv run can-sniffer -I slcan -b 500000 -t 10 --vcd bus.vcd

# Read it back without opening the GUI
sigrok-cli -i bus.vcd -I vcd -P can:nominal_bitrate=500000 -A can=fields
```

In PulseView: **Open** the `.vcd`, then add the **CAN** decoder, set its channel
to `CAN_RX_RECONSTRUCTED` and `nominal_bitrate` to your bus rate.

!!! danger "This is a reconstruction, not a measurement"
    The frame structure, the bit stuffing and the CRC-15 are **exact** — they
    follow from the frame. Nothing else does:

    - the **ACK slot** is drawn by assumption; the adapter never reported
      whether anybody answered;
    - **errors, retransmissions and lost arbitration** are absent, because the
      controller resolved them before the frame ever reached the studio;
    - **frames the adapter dropped** are missing, with nothing to mark the gap;
    - the **timing between frames** comes from software receive timestamps —
      tens of microseconds out, against a bit that lasts two at 500 kbit/s.

    The warning travels with the file, not just in this page: the channel is
    named `CAN_RX_RECONSTRUCTED`, so PulseView shows it beside the trace, and
    the VCD header spells the rest out in full.

    For anything diagnostic, probe the real pair with the analyzer as described
    above. Use the reconstruction to *teach* the frame format — where the stuff
    bits land, how the CRC is framed — not to judge a bus.

### Knobs

| Flag | Default | What it does |
| :--- | :--- | :--- |
| `--vcd PATH` | — | Write the waveform |
| `--vcd-timing` | `timestamps` | `timestamps` keeps the adapter's gaps, to its own accuracy. `packed` puts frames back to back: the time axis loses all meaning, the bits become easy to read |
| `--vcd-ack` | `acknowledged` | How to draw the ACK slot the adapter never reported |
| `--vcd-tick-ns` | `100` | Time resolution. 100 ns is 20 samples per bit at 500 kbit/s; the decoder needs far fewer |

The bit rate comes from `-b`, and the `can` decoder must be told the same one.

When two frames carry the same timestamp — common, since the adapter's clock is
coarser than a frame is long — the second is shifted later so the wire never
carries two at once. The sniffer counts those and says how many when it exits,
rather than quietly producing an impossible waveform.

### Comparing the two

Reconstructing what the adapter reported, next to the probe's recording of the
same bus, is the interesting exercise: every frame the analyzer saw should
appear in the reconstruction. Whatever is missing was dropped between the
transceiver and the studio — and the ACK slots, which the reconstruction
guesses and the probe actually measured, are worth a look too.

---

## What still needs the real probe

Live streaming into PulseView is not possible: it has no way to take a feed from
another program without a `libsigrok` driver written in C. Nor is anything
below the frame — a node going error-passive, an arbitration collision, a bus
with the wrong termination — visible in a reconstruction, because the adapter
never reported it. That is what the transceiver and analyzer above are for.
