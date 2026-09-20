# CANopen Studio — Performance Benchmarks & Baseline (Python Reference)

This document records the baseline performance metrics, Round-Trip Time (CAN Ping / Pong latency), and cyclic frame jitter of CANopen Studio implemented in **Python 3.13**, and their Rust equivalents.

These measurements serve as the reference baseline to quantify performance improvements achieved through future optimizations or full/partial rewrites in compiled systems languages (**Rust**, **C++**, **Go**). See the [Development Roadmap](ROADMAP.md) for architectural plans.

---

## Glossary of Benchmark Types

> This section explains what each benchmark measures, why it matters, and how to interpret the numbers.

### RTT Benchmark (Round-Trip Time / CAN Ping)
**What it measures:** the total time for a frame to travel from sender to responder and back. Sender transmits a `0x7E0` frame with a sequence number; the responder immediately echoes it as `0x7E1`; the sender records the elapsed time.

**Why it matters:** RTT is the end-to-end latency budget available to a control loop. An RTT of 75 ms means a closed-loop controller can only react at ~13 Hz, regardless of bus nominal speed.

**What affects it:** network medium (Wi-Fi vs Ethernet vs CAN bus), OS scheduler latency at the responder, serialization overhead (msgpack vs binary), GIL contention in Python.

**How to reproduce:**
```bash
# On responder machine:
just rust-echo target=<sender-ip>
# On sender machine:
just rust-bench-ping target=<responder-ip> count=20
```

### SYNC Jitter Benchmark (Cyclic Frame Interval Stability)
**What it measures:** the deviation between consecutive SYNC frame arrivals and the nominal 20 ms period (50 Hz). `jitter = |actual_interval − 20,000 µs|`. A perfect simulator would deliver every SYNC exactly 20,000 µs after the previous one.

**Why it matters:** in a real CANopen network, all nodes synchronise their PDO transmissions to the SYNC pulse. High jitter desynchronises the network, corrupts time-stamped measurements, and degrades motor control quality.

**What affects it:** OS scheduler granularity (`time.sleep` in Python has ~2–5 ms resolution), GIL hold time, CPU load. A native Rust thread with `std::thread::sleep` still depends on OS scheduler quanta but eliminates GIL contention.

**How to reproduce:**
```bash
# Terminal 1 — start the headless simulator:
just rust-simulate
# Terminal 2 — measure SYNC jitter:
just rust-bench-latency
```

### TX Throughput Benchmark
**What it measures:** the maximum number of CAN frames per second a single sender can transmit continuously over UDP (no acknowledgement, no receiver required).

**Why it matters:** establishes the upper bound of the software stack — hardware bus limits apply on top of this. A Python loop at 3,000 fps is the GIL ceiling; Rust at 300,000 fps shows the UDP socket is the real bottleneck.

**How to reproduce:**
```bash
just rust-bench-tx count=200000
```

---

## 1. Test Environment & Network Topology

Measurements were performed in real-world conditions between two physical machines communicating over a local 802.11 wireless network (Wi-Fi):

| Parameter | Node 1 (Transmitter & Simulator) | Node 2 (Receiver & Auto-Echo) |
| :--- | :--- | :--- |
| **Hardware** | Laptop (MAINPADIX) | Apple MacBook |
| **Operating System** | Linux NixOS 24.11 (Linux Kernel 6.x) | macOS Darwin (Sonoma/Sequoia) |
| **Runtime Environment** | Python 3.13.13 (`uv` / `nix-shell`) | Python 3.13.5 (Anaconda `.venv`) |
| **GUI Framework** | Tkinter / Tcl-Tk 8.6 | Tkinter / Cocoa Native |
| **Local IP Address** | `192.168.30.32` | `192.168.30.31` |
| **Application Mode** | `simulate=True` (Virtual Simulator active) | `simulate=False` (Passive capture + Auto-Echo) |
| **Physical Link** | Wi-Fi 802.11 (Subnet `192.168.30.0/24`) | Wi-Fi 802.11 (Subnet `192.168.30.0/24`) |
| **Transport Backend** | `UdpBus` (Broadcast UDP `192.168.30.255:1750`) | `UdpBus` (Broadcast UDP `192.168.30.255:1750`) |

### Transport Layer & Serialization
* **Wire Frame Format**: `can.Message` dictionaries serialized using **MessagePack (msgpack)** (aligned with *python-can*'s `udp_multicast` layout).
* **Network Channel**: Port `1750` via Subnet Broadcast (`192.168.30.255:1750`) with `SO_REUSEADDR`, `SO_REUSEPORT`, and `SO_BROADCAST` socket options enabled.
* **Rationale for UDP Broadcast vs Multicast**: Bypasses the macOS Cocoa GUI sandbox restriction, where Cocoa event loops block UDP multicast (`224.0.0.0/4`) for applications lacking Apple's `com.apple.developer.networking.multicast` entitlement (*`OSError: [Errno 65] No route to host`*).

---

## 2. Bus Load & Traffic Profile

The Linux machine generated a dense, continuous simulated CANopen stream:
* **CANopen SYNC (0x080)**: Periodic clock at **50 Hz** (nominal period $T = 20.0\text{ ms}$).
* **Node 1 Heartbeat (0x701)**: 100 ms period (NMT Operational).
* **Node 2 Heartbeat (0x702)**: 100 ms period (NMT Pre-Operational).
* **CiA 402 TPDO1 (0x181)**: Dynamic motor velocity.
* **CiA 402 TPDO2 (0x281)**: Motor position and electrical current.
* **SEVCON Gen4 TPDOs (0x148, 0x156, 0x270, 0x473)**: Inverter telemetry (RPM, torque, heatsink and motor temperatures).

**Observed throughput:**
* **Linux TX/RX**: **~159 frames/second**
* **Mac RX**: **~152 frames/second**

---

## 3. Latency Measurement Protocol (CAN Ping / Pong)

Round-Trip Time (RTT) is measured directly at the CAN application layer via the [`LatencyTracker`](src/canopen_studio/latency.py) module:
1. **Ping Request Frame (`0x7E0`)**: Contains an incrementing 32-bit sequence number and high-resolution timestamp fraction (`time.perf_counter()`).
2. **Auto-Echo Responder (`0x7E1`)**: Any listening node receiving `0x7E0` immediately replies with ID `0x7E1` echoing identical payload (frames originating from the same node looped back locally are automatically ignored).
3. **RTT Calculation**: Computed immediately upon receiving `0x7E1` using a monotonic clock differential, requiring zero NTP clock synchronization between machines.

---

## 4. Benchmark Results

### 4.1. Network Round-Trip Time (RTT Ping)

#### Mac ➔ Linux (5 consecutive pings across Wi-Fi)
| Ping # | Sequence ID | Measured RTT |
| :---: | :---: | :---: |
| 1 | `seq=1` | 75.00 ms |
| 2 | `seq=2` | 91.38 ms |
| 3 | `seq=3` | 24.85 ms |
| 4 | `seq=4` | 95.14 ms |
| 5 | `seq=5` | 95.73 ms |

* **Minimum RTT**: **24.85 ms**
* **Average RTT**: **76.42 ms**
* **Maximum RTT**: **95.73 ms**
* **Packet Loss**: **0.0%** (5/5 received)

#### Linux ➔ Mac (5 consecutive pings across Wi-Fi)
| Ping # | Sequence ID | Measured RTT |
| :---: | :---: | :---: |
| 1 | `seq=1` | 101.45 ms |
| 2 | `seq=2` | 95.43 ms |
| 3 | `seq=3` | 91.66 ms |
| 4 | `seq=4` | 18.15 ms |
| 5 | `seq=5` | 64.90 ms |

* **Minimum RTT**: **18.15 ms**
* **Average RTT**: **74.32 ms**
* **Maximum RTT**: **101.45 ms**
* **Packet Loss**: **0.0%** (5/5 received)

#### Overall Bidirectional Summary
* **Absolute Minimum**: **18.15 ms**
* **Overall Average RTT**: **~75.37 ms**
* **Absolute Maximum**: **101.45 ms**
* **Packet Loss Rate**: **0.0%** (10/10)

---

### 4.2. Cyclic Frame Jitter (SYNC 0x080 @ 50 Hz / 20.0 ms)

Jitter measures the deviation $|T_{\text{actual}} - T_{\text{nominal}}|$ between consecutive synchronization pulses:

| Metric | Linux (Local Simulator Jitter) | Mac (Network Wi-Fi Jitter) |
| :--- | :---: | :---: |
| **Last SYNC Interval** | 31.51 ms | 0.83 ms *(burst packet delivery)* |
| **Last Jitter ($|T - 20\text{ms}|$)** | 11.51 ms | 19.17 ms |
| **Average Jitter** | **2.60 ms** | **23.92 ms** |
| **Analyzed SYNC Frames** | 479 frames | 455 frames |

> [!NOTE]
> The **~24 ms** average network jitter measured on the remote node is characteristic of 802.11 Wi-Fi behavior: packet aggregation, power-saving sleep cycles, and DTIM (*Delivery Traffic Indication Message*) beacon buffering for broadcast/multicast traffic.

---

## 5. Rust RTT Ping Benchmark (`canopen-cli bench-ping`)

Measured on the same two physical machines as the Python baseline (§4), using `canopen-cli bench-ping` (sender) and `canopen-cli echo` (auto-responder). Same protocol: CAN ID `0x7E0` ping → `0x7E1` echo, 8-byte payload with sequence number and µs fraction. Transport: UDP unicast on Wi-Fi 802.11 (`192.168.30.0/24`).

```bash
# On MacBook (responder):
./target/release/canopen-cli echo --target 192.168.30.32 --target-port 1750

# On MAINPADIX (sender):
cargo run --release --bin canopen-cli -- bench-ping \
  --target 192.168.30.31 --target-port 1750 --count 20 --interval-ms 500
```

### 5.1 Linux (MAINPADIX) → macOS (MacBook) — 20 pings

| Ping # | Measured RTT |
| :---: | :---: |
| 1 | 6.26 ms |
| 2 | 7.13 ms |
| 3 | 10.00 ms |
| 4 | 6.56 ms |
| 5 | 7.70 ms |
| 6 | 62.07 ms |
| 7 | 5.92 ms |
| 8 | 56.87 ms |
| 9 | 8.92 ms |
| 10 | 6.29 ms |
| 11 | 6.07 ms |
| 12 | 26.21 ms |
| 13 | 8.34 ms |
| 14 | 14.95 ms |
| 15 | 8.97 ms |
| 16 | 176.39 ms |
| 17 | 55.07 ms |
| 18 | 19.82 ms |
| 19 | 7.76 ms |
| 20 | 17.40 ms |

* **Minimum RTT**: **5.92 ms**
* **Average RTT**: **25.94 ms**
* **Maximum RTT**: **176.39 ms**
* **Std Dev**: **38.78 ms**
* **Packet Loss**: **0.0%** (20/20)

### 5.2 macOS (MacBook) → Linux (MAINPADIX) — 20 pings

| Ping # | Measured RTT |
| :---: | :---: |
| 1 | 6.55 ms |
| 2 | 6.15 ms |
| 3 | 83.84 ms |
| 4 | 8.92 ms |
| 5 | 101.63 ms |
| 6 | 107.60 ms |
| 7 | 12.61 ms |
| 8 | 69.82 ms |
| 9 | 29.04 ms |
| 10 | 110.79 ms |
| 11 | 17.28 ms |
| 12 | 93.40 ms |
| 13 | 9.79 ms |
| 14 | 111.40 ms |
| 15 | 114.38 ms |
| 16 | 107.80 ms |
| 17 | 105.15 ms |
| 18 | 113.72 ms |
| 19 | 108.63 ms |
| 20 | 114.64 ms |

* **Minimum RTT**: **6.15 ms**
* **Average RTT**: **71.66 ms**
* **Maximum RTT**: **114.64 ms**
* **Std Dev**: **44.56 ms**
* **Packet Loss**: **0.0%** (20/20)

### 5.3 Comparison: Rust vs Python RTT (same network, same machines)

| Metric | Python 3.13 (Wi-Fi broadcast) | Rust 1.80+ (Wi-Fi unicast) | Delta |
| :--- | :---: | :---: | :---: |
| **Min RTT Linux→Mac** | 24.85 ms | **5.92 ms** | **−18.93 ms (−76%)** |
| **Avg RTT Linux→Mac** | 76.42 ms | **25.94 ms** | **−50.48 ms (−66%)** |
| **Max RTT Linux→Mac** | 95.73 ms | 176.39 ms* | — |
| **Min RTT Mac→Linux** | 18.15 ms | **6.15 ms** | **−12.00 ms (−66%)** |
| **Avg RTT Mac→Linux** | 74.32 ms | **71.66 ms** | −2.66 ms |
| **Max RTT Mac→Linux** | 101.45 ms | 114.64 ms* | — |
| **Packet Loss** | 0.0% | **0.0%** | — |

> [!NOTE]
> \* The higher max RTTs in the Rust run reflect Wi-Fi burst jitter (802.11 power-save / DTIM beacons), which is non-deterministic and varies between runs independently of the language runtime. The key gain is in **minimum and average RTT**: the Rust native stack eliminates Python's GIL overhead, `time.perf_counter()` scheduling latency, and msgpack serialization round-trips — reducing best-case latency by ~70%.

---

## 6. SYNC Jitter Benchmark — Full Implementation × Transport Matrix

All campaigns source from MAINPADIX (NixOS, Linux 6.x), nominal 20,000 µs (50 Hz SYNC).
Three implementations are benchmarked to provide a fair comparison:

| Stack | Simulator | Receiver clock source |
| :--- | :--- | :--- |
| **Pure Python** | `time.sleep(0.020)` + stdlib socket (no Rust) | `time.perf_counter()` at receive |
| **Python (Rust-backed)** | Rust `canopen_core.VirtualCanopenSimulator` via `UdpBus` | Sender timestamp in frame |
| **Rust CLI** | `canopen-cli simulate` (Rust `thread::sleep`) | Sender timestamp in frame |

> [!NOTE]
> **Measurement methodology.** Pure-Python receiver uses `time.perf_counter()` at the moment `recvfrom()` returns — this is a receiver-side inter-arrival measurement. Python (Rust-backed) and Rust CLI both use the sender timestamp embedded in the msgpack frame payload — this is a sender-side interval measurement. For cross-machine tests, the receiver-side metric is what a real CANopen node experiences; for local loopback, both metrics converge.

```bash
# Reproduce:
uv run --with msgpack python benchmarks/pure_python_sync_jitter.py [unicast|multicast|broadcast] --start-sim
uv run python benchmarks/python_sync_jitter.py [unicast|multicast] --start-sim
just rust-simulate && just rust-bench-latency
```

### 6.1 Local — MAINPADIX loopback (same machine, all transports)

| Implementation | Transport | Samples | Min µs | Avg µs | Max µs | **Avg jitter** | StdDev µs |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Pure Python** | Unicast 127.0.0.1 | 1,483 | 19,973 | 20,231 | 22,403 | **+231 µs** | 99 |
| **Pure Python** | Multicast 239.0.0.1 | 1,478 | 19,677 | 20,302 | 25,368 | **+302 µs** | 235 |
| **Pure Python** | Broadcast 192.168.30.255 | 1,477 | 19,231 | 20,312 | 25,521 | **+312 µs** | 305 |
| **Python (Rust-backed)** | Unicast 127.0.0.1 | 1,453 | 20,301 | 20,648 | 22,945 | **+648 µs** | 158 |
| **Python (Rust-backed)** | Multicast 239.0.0.1 | 1,447 | 20,297 | 20,729 | 24,097 | **+729 µs** | 224 |
| **Python (Rust-backed)** | Broadcast 192.168.30.255 | 1,544 | 20,294 | 20,722 | 23,777 | **+722 µs** | 202 |
| **Rust CLI** | Unicast 127.0.0.1 | ~1,350 | 20,389 | 20,737 | 23,716 | **+737 µs** | 222 |
| **Rust CLI** | Multicast 239.0.0.1 | ~1,450 | 20,325 | 20,758 | 22,286 | **+758 µs** | 220 |
| **Rust CLI** | Broadcast 192.168.30.255 | ~1,499 | 20,306 | 20,724 | 21,667 | **+724 µs** | 173 |

> The pure-Python numbers appear lower because the receiver measures `time.perf_counter()` at packet arrival — it captures how evenly packets arrive. Python (Rust-backed) and Rust CLI embed the sender's timestamp in the frame; those numbers reflect the simulator thread's `sleep` overshoot. For local loopback all three transports give the same result within noise (~720–760 µs for Rust-backed/Rust CLI), confirming that broadcast adds no extra latency on a loopback path. `thread::sleep` in Rust overshoots more (~737 µs) than Python's `time.sleep` (~231 µs) on this kernel.

### 6.2 Network — MacBook receives via Wi-Fi 802.11

Simulator on MAINPADIX; receiver on MacBook. Rows ordered per transport block: Pure Python simulator first, then Rust CLI, then Python GUI. Within each simulator block, receivers ordered: Pure Python → Rust CLI → Python (Rust-backed).

**Rust CLI** and **Python (Rust-backed)** receivers use sender timestamps embedded in frames (sender-side metric). **Pure Python** receivers use `time.perf_counter()` at packet arrival (receiver-side metric).

| Transport | Simulator | Receiver | Samples | Min µs | Avg µs | Max µs | **Avg jitter** | StdDev µs |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Broadcast 192.168.30.255 | Pure Python | **Pure Python** †† | 1,665 | 2,600 | 20,438 | 143,164 | **+438 µs** | 3,774 |
| Broadcast 192.168.30.255 | Pure Python | **Python (Rust-backed)** | 1,683 | 20,102 | 20,786 | 60,693 | **+786 µs** | 3,392 |
| Broadcast 192.168.30.255 | Rust CLI | **Pure Python** †† | 1,528 | 1,197 | 22,914 | 164,203 | **+2,914 µs** | 11,541 |
| Broadcast 192.168.30.255 | Rust CLI | **Rust CLI** | 1,549 | 20,275 | 22,331 | 103,697 | **+2,331 µs** | 6,749 |
| Broadcast 192.168.30.255 | Rust CLI | **Python (Rust-backed)** | 1,570 | 20,314 | 22,277 | 62,252 | **+2,277 µs** | 5,598 |
| Broadcast 192.168.30.255 | Python (GUI) | **Pure Python** (original) ‡ | 455 | — | — | ~120,000 | **+23,920 µs** ‡ | — |
| Unicast 192.168.30.31 | Pure Python | **Pure Python** †† | 1,609 | 5,042 | 20,224 | 25,325 | **+224 µs** | 830 |
| Unicast 192.168.30.31 | Pure Python | **Python (Rust-backed)** | 1,727 | 20,102 | 20,258 | 22,630 | **+258 µs** | 127 |
| Unicast 192.168.30.31 | Rust CLI | **Rust CLI** | 1,649 | 20,287 | 20,643 | 23,039 | **+643 µs** | 150 |
| Unicast 192.168.30.31 | Rust CLI | **Python (Rust-backed)** | 1,552 | 20,304 | 20,609 | 23,949 | **+609 µs** | 193 |
| Multicast 239.0.0.1 | Pure Python | **Pure Python** †† | 1,569 | 2,483 | 20,608 | 138,476 | **+608 µs** | 6,007 |
| Multicast 239.0.0.1 | Pure Python | **Python (Rust-backed)** | 1,725 | 20,105 | 20,289 | 21,821 | **+289 µs** | 90 |
| Multicast 239.0.0.1 | Rust CLI | **Rust CLI** | 699 | 20,108 | 20,718 | 21,978 | **+718 µs** | 183 |
| Multicast 239.0.0.1 | Rust CLI | **Python (Rust-backed)** | 1,833 | 20,250 | 20,682 | 23,478 | **+682 µs** | 197 |

†† Receiver-side `time.perf_counter()` measurement. DTIM bursts appear as short intervals (<1 ms, filtered) followed by a compensating long gap: avg jitter looks low but StdDev and max reveal the burst pattern. Rust CLI sim broadcast shows higher variance (StdDev 11,541 µs) because the AP buffers more frames per DTIM period at 76fps total frame rate vs 50fps for the Pure Python sim.

> [!NOTE]
> **Rust CLI sim → Pure Python receiver (unicast, multicast): not measured.** The Rust CLI simulator sends ~76fps aggregate (SYNC + HB + TPDOs). At that rate, the Mac Wi-Fi PSM buffers even unicast/multicast frames into bursts, producing intra-burst intervals of ~1 ms and inter-burst gaps of >200 ms — both outside the valid measurement window of the pure Python receiver. The sender-side metric (Python (Rust-backed) receiver) is not affected and remains reliable.

> [!NOTE]
> **Pure Python sim → Rust CLI receiver**: not measured — equivalent to Pure Python sim → Python (Rust-backed) receiver; both extract the same sender timestamp from the msgpack frame.

‡ Measured in §4.2 with the original Python GUI simulator (pre-Phase 1B). Reflects both DTIM penalty AND Python GIL-bound simulator timing variability.

### 6.3 Key Findings

| Finding | Evidence |
| :--- | :--- |
| **Pure Python loopback is ~3× better than Rust-backed** locally | Methodology difference: receiver clock vs sender clock |
| **Python (Rust-backed) ≈ Rust CLI** for all transports | Broadcast: 2,277 vs 2,331 µs; unicast: 609 vs 643 µs; multicast: 682 vs 718 µs — same Rust `UdpCanBus` core |
| **Pure Python sim is more precise than Rust CLI sim** (sender-side) | Unicast: +258 µs vs +609 µs; multicast: +289 vs +682 µs — pure Python sim only sends SYNC (50fps, minimal inter-sleep work), Rust CLI sim also computes HB + TPDOs between sleeps |
| **Multicast is the cleanest transport** (sender-side, pure Python sim) | StdDev **89 µs** — the lowest of all measurements; no DTIM + low simulator overhead |
| **Broadcast ×3–4× worse on Wi-Fi (sender-side)** | Rust broadcast: +2,331 µs vs Rust unicast +643 µs — DTIM beacon buffering |
| **DTIM visible in receiver-side extremes, not averages** | Pure Python sim broadcast: receiver-side avg +438 µs but sender-side +786 µs; max 143 ms |
| **Rust CLI sim broadcast has higher DTIM variance than Pure Python sim** | StdDev 6,749–11,541 µs vs 3,392 µs — more frames buffered per DTIM period at 76fps |
| **Unicast ≈ Multicast (IGMP-subscribed) on Rust/Python-backed** | 258–718 µs range; router treats IGMP multicast like unicast |
| **Original Python broadcast 23,920 µs = two problems** | DTIM penalty (~10×) + Python GIL-bound simulator (~10×) = ~100× total vs Rust unicast (643 µs) |
| **Wi-Fi unicast/multicast adds zero measurable latency** | Local 648–758 µs vs Wi-Fi 609–718 µs — within noise |

> [!NOTE]
> **Broadcast vs unicast/multicast on Wi-Fi.** Wi-Fi APs buffer broadcast frames until the next DTIM beacon (typically 100–300 ms) before delivering to sleeping clients. IGMP-subscribed multicast and unicast bypass this and are delivered immediately. Switching from broadcast to unicast or multicast (with `join_multicast_v4`) **eliminates ~70% of SYNC jitter on Wi-Fi links**, independently of the language stack.

> [!NOTE]
> The `+600–750 µs` bias on Rust/Python-backed tests is the simulator's `thread::sleep` overshoot (sender-side). Pure Python's lower apparent bias (~230 µs) reflects `time.sleep()` overshoot on the same kernel, measured at the receiver, which is a slightly different metric.

---

## 7. Rust Engine (`canopen-core`) Throughput Benchmark

With the Phase 1A native engine implementation in Rust, high-throughput transmission was benchmarked using `canopen-cli bench-tx`:

```bash
# High-speed compact binary transmission (200,000 frames)
cargo run --release --bin canopen-cli -- bench-tx --count 200000 --compact
```

### Empirical Comparison: Python 3.13 vs Rust 1.80+

| Metric | Python 3.13 Baseline | Rust `canopen-core` (Release) | Performance Multiplier |
| :--- | :---: | :---: | :---: |
| **Max Transmission Throughput** | ~155 fps (GUI active)<br>~3,000 fps (raw Python loop) | **298,526 frames/second** | **~100× vs raw Python**<br>**~1,900× vs GUI loop** |
| **Per-Frame Heap Allocation** | ~3 heap objects (`can.Message`, dict, bytes) | **0 heap allocations** (stack `[u8; 24]`) | **Zero-Copy** |
| **GIL Contention** | High (serialized on GIL) | **None** (unrestricted native threads) | **Full multi-core concurrency** |
| **Ring Buffer Storage** | Python `collections.deque` | `TraceRingBuffer` (pre-allocated array) | **Zero dynamic resizing** |
| **Periodic Timer Jitter** | ~2.6 ms (`time.sleep`) | < 50 µs target | **Sub-millisecond real-time** |

---

## 8. Architectural Bottlenecks & Targets for Future Rewrites

This baseline highlights key areas where a compiled language implementation (e.g. **Rust**, **C++**, **Go**) will deliver substantial gains:

### 1. Zero-Copy Frame Parsing vs Dynamic Msgpack Serialization
* **Current Python State**: Each frame undergoes dictionary allocation, type conversion, and `msgpack` serialization/deserialization.
* **Target Architecture**: Fixed 16-byte binary frames (32-bit CAN ID, 8-bit DLC, 8-bit flags, 64-bit nanosecond timestamp, 8-byte payload) allowing **zero-copy in-place parsing** with zero heap allocations.

### 2. Concurrency & GIL Elimination
* **Current Python State**: The Global Interpreter Lock (GIL) serializes execution across the CAN reader thread (`_rx_loop`), Tkinter event loop, and async FastMCP server.
* **Target Architecture**: Lock-free actor channels (e.g., Rust `crossbeam-channel` or `tokio::mpsc`), scaling throughput to **> 500,000 frames/second** without UI stutter.

### 3. Microsecond-Precision Scheduling
* **Current Python State**: Simulator timing uses `time.sleep()`, constrained by standard OS scheduler quanta (~2.6 ms baseline jitter).
* **Target Architecture**: High-resolution kernel timers (`timerfd` on Linux, monotonic dispatch timers on macOS) to reduce simulator jitter to **< 50 µs**.

### 4. Native Low-Level Networking
* Unified native network stack handling UDP Unicast, Multicast, and Subnet Broadcast with explicit interface binding (`IP_BOUND_IF` on macOS, `SO_BINDTODEVICE` on Linux) to guarantee sandbox and firewall compatibility out of the box.

---

## 9. How to Reproduce

```bash
# 1. Start the transmitter on Linux (with virtual simulator active)
uv run canopen-studio

# 2. Start the receiver on the remote machine (Mac)
ssh user@mac-ip "cd /path/to/canopen-studio && uv run canopen-studio"

# 3. Execute the automated MCP benchmark orchestrator
uv run python scratch/measure_ping.py
```
