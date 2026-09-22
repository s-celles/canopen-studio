# Command-Line Tools

Two command-line tools ship with the project, and they answer different questions.

| Tool | Language | Use it for |
|---|---|---|
| `can-sniffer` | Python | Watching a **real adapter** — SLCAN, PCAN, Kvaser, Vector, IXXAT, gs_usb, SocketCAN — with the full decoder stack, CSV capture and a console dashboard |
| `canopen-cli` | Rust | Anything over **UDP**: a headless simulator, latency and throughput measurement, a decoded firehose with no interpreter in the path |

---

## `can-sniffer` — the Python sniffer

Installed with the package (`uv tool install`, `pip install -e .`) or reachable in a
checkout through `uv run can-sniffer`.

```bash
# Sniff the default SLCAN adapter at 500 kbit/s
uv run can-sniffer -I slcan -b 500000

# No hardware: synthetic CANopen traffic for 10 seconds
uv run can-sniffer -I virtual --simulate -t 10
```

| Option | Meaning |
|---|---|
| `-I`, `--interface` | `slcan`, `pcan`, `kvaser`, `vector`, `ixxat`, `gs_usb`, `socketcan`, `virtual`, `udp_multicast` |
| `-c`, `--channel` (`-p`, `--port`) | `COM4`, `PCAN_USBBUS1`, `can0`, `0`, a multicast group … |
| `-b`, `--bitrate` | Bus bitrate in bit/s (default `500000`) |
| `--profile` | Decoder profile: `All / Auto`, `CiA 402 Generic Drive`, `SEVCON Gen4 Inverter`, `De Haardt Safety Transponder`, `J1939 Extended (29-bit)`, `Raw CAN (No Decoders)` |
| `-i`, `--id` | Show one CAN identifier only, in hex or decimal (`0x473`) |
| `--extended-only` / `--standard-only` | 29-bit frames only / 11-bit frames only |
| `--listen-only` | Passive mode: no ACK is transmitted on the bus |
| `-o`, `--log` | Append captured frames to a CSV file |
| `-n`, `--count` | Stop after N frames |
| `-t`, `--duration` | Stop after N seconds |
| `--dashboard` | Interactive console dashboard (RPM, temperatures, torque, node states) |
| `--simulate` | Start the virtual nodes and emit synthetic traffic |
| `--pcap` | Also write a PCAP-NG capture for Wireshark — a file, or a named pipe for a live capture |
| `--vcd` | Also write a reconstructed VCD waveform for sigrok and PulseView |
| `--vcd-timing` | `timestamps` (the adapter's gaps, to its own accuracy) or `packed` (frames back to back) |
| `--vcd-ack` | `acknowledged` or `unanswered` — the adapter never reports the ACK slot |
| `--vcd-tick-ns` | Waveform resolution in nanoseconds; 100 gives 20 samples per bit at 500 kbit/s |

The two exports run together — one capture, Wireshark for the protocol and PulseView for
the waveform:

```bash
uv run can-sniffer -I slcan --pcap capture.pcapng --vcd capture.vcd
```

!!! warning "The waveform is reconstructed, not measured"
    `--vcd` rebuilds the bit stream a decoded frame *would* have produced. Structure,
    stuffing and CRC are exact; the ACK slot is an assumption, errors and retransmissions
    are absent, and the timing carries the adapter's accuracy. The file says so in its own
    header. [Logic Analyzer & Signals](logic_analyzer.md) covers measuring the wire for
    real.

The `just` recipes wrap the common combinations:

```bash
just sniff                  # slcan @ 500 kbit/s
just sniff slcan 250000     # an interface and a bitrate, in that order
just simulate 10            # virtual bus, 10 seconds
just dashboard              # console dashboard
just filter 0x473           # one identifier
just record capture.csv     # capture to CSV
just sample 50              # stop after 50 frames
just listen-only            # no ACK on the bus
just extended               # 29-bit frames only
just standard               # 11-bit frames only
```

---

## `canopen-cli` — the native tool

Built from the Rust workspace, so it needs a [Rust toolchain](rust_core.md#building-it-from-a-checkout):

```bash
cargo build --release -p canopen-cli
cargo run --release --bin canopen-cli -- <command> [options]
```

Every subcommand speaks the same UDP transport. `--port` is the socket to bind (default
`1750`), `--target` / `--target-port` say where frames are sent, and `--compact` selects
the 24-byte binary encoding instead of the `python-can`-compatible MessagePack one. Two
processes on one machine must **not** bind the same port, or each receives the frames it
just sent.

`simulate` is the exception: it transmits only, from an ephemeral port, and its `--port`
is the **destination** — so a `sniff` bound to that port hears it.

### `sniff` — decoded monitoring

```bash
cargo run --release --bin canopen-cli -- sniff --port 1750
```

Prints every frame with its CANopen meaning, and its OBD-II Mode 01 reading when the
frame carries one. It takes the same two exports as the Python sniffer, spelled the same
way, so a capture reads identically whichever front end wrote it:

| Option | Meaning |
|---|---|
| `--pcap <FILE\|PIPE>` | PCAP-NG for Wireshark; a pipe blocks until a capture opens it |
| `--vcd <FILE>` | The reconstructed waveform |
| `--vcd-bitrate <BPS>` | Rate the waveform is drawn at (default `500000`) |
| `--vcd-timing`, `--vcd-ack`, `--vcd-tick-ns` | As in the Python sniffer |

`--vcd-bitrate` has no Python counterpart, and needs one here: the Python sniffer takes
the rate from the adapter it opened, a UDP bus has none, and sigrok's `can` decoder has to
be told the same figure or it reads nothing back.

### `simulate` — a headless virtual bus

```bash
just rust-simulate 1750 0     # destination port, duration in seconds (0 = forever)
```

SYNC at 50 Hz, heartbeats at 1 Hz, TPDOs at 25 Hz, and answers to NMT commands, SDO
uploads, CAN pings and OBD-II Mode 01. This is the traffic source for every measurement
below, and a way to exercise a decoder without any hardware.

### `latency` — SYNC jitter

```bash
# In another terminal, with a simulator already running
just rust-bench-latency 1750 0x080 20000
```

Measures the interval between successive frames against a nominal period — 50 Hz SYNC
is 20,000 µs — and reports the deviation. `--filter-id` restricts the measurement to one
identifier, `--group` joins a multicast group.

### `bench-ping` and `echo` — round-trip time

`bench-ping` sends CAN ping frames on `0x7E0` and times the replies on `0x7E1`; `echo`
is the responder to run on the far machine.

```bash
# On the remote machine, pointed back at the machine that will ping it
just rust-echo 192.168.1.10 1750

# On this one (192.168.1.10): 20 pings, one per second
just rust-bench-ping 192.168.1.42 1750 20 1000
```

It reports min, average, max, standard deviation and packet loss — the same protocol the
Python `LatencyTracker` uses, so the two are directly comparable.

### `bench-tx` — throughput

```bash
just rust-bench-tx 200000
```

Generates N frames as fast as the transport allows and reports frames per second.

---

## `canopen-extcap` — the Wireshark plugin

Wireshark can only capture CAN on Linux, SocketCAN being a kernel feature, so its
interface list has nothing to offer on macOS and Windows. Dropped into Wireshark's extcap
directory, the `canopen-extcap` binary puts the studio's buses in that list instead.

```bash
cargo build --release -p canopen-cli --bin canopen-extcap
```

It advertises the UDP transport and the bundled simulator only — the two the core can
genuinely open — rather than listing interfaces that cannot be captured from.
[Wireshark Integration](wireshark.md) has the installation path for each platform, the
`Decode As` settings for CANopen, and the display filters worth knowing.

---

Measured results for all of these, across transports and both languages, are in
[Performance Benchmarks](benchmarks.md).
