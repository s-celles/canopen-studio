# CANopen Studio — Performance Benchmarks & Baseline (Python Reference)

This document records the baseline performance metrics, Round-Trip Time (CAN Ping / Pong latency), and cyclic frame jitter of CANopen Studio implemented in **Python 3.13**.

These measurements serve as the reference baseline to quantify performance improvements achieved through future optimizations or full/partial rewrites in compiled systems languages (**Rust**, **C++**, **Go**).

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

## 5. Architectural Bottlenecks & Targets for Future Rewrites

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

## 6. How to Reproduce

```bash
# 1. Start the transmitter on Linux (with virtual simulator active)
uv run canopen-studio

# 2. Start the receiver on the remote machine (Mac)
ssh user@mac-ip "cd /path/to/canopen-studio && uv run canopen-studio"

# 3. Execute the automated MCP benchmark orchestrator
uv run python scratch/measure_ping.py
```
