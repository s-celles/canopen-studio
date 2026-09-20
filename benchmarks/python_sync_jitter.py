"""
Python SYNC Jitter Benchmark — receiver side only.
Uses canopen-cli simulate as the frame source (separate process, no port conflict).

Usage:
    uv run python benchmarks/python_sync_jitter.py unicast   [--port 1752] [--duration 30]
    uv run python benchmarks/python_sync_jitter.py multicast [--port 1752] [--duration 30]

Run the simulator in another terminal first (or pass --start-sim to launch it automatically):
    ./target/release/canopen-cli simulate --target 127.0.0.1  --port 1752  # unicast loopback
    ./target/release/canopen-cli simulate --target 239.0.0.1  --port 1752  # multicast
    ./target/release/canopen-cli simulate --target 192.168.30.255 --port 1752  # broadcast
"""

import argparse
import subprocess
import statistics
import sys
import time

sys.path.insert(0, "src")
from canopen_studio.interfaces import UdpBus

SYNC_ID = 0x080
NOMINAL_US = 20_000.0
REPORT_EVERY = 50

TRANSPORT_TARGETS = {
    "broadcast": "192.168.30.255",
    "unicast": "127.0.0.1",
    "multicast": "239.0.0.1",
}


def run_receiver(target: str, port: int, duration: float) -> dict:
    rx_bus = UdpBus(channel=target, port=port, receive_own_messages=True)

    intervals: list[float] = []
    last_ts: float | None = None
    count = 0
    deadline = time.monotonic() + duration

    print(f"==> Python SYNC jitter receiver | group/target={target}:{port} | {duration:.0f}s")

    while time.monotonic() < deadline:
        try:
            msg = rx_bus.recv(timeout=0.1)
        except OSError:
            continue
        if msg is None or msg.arbitration_id != SYNC_ID:
            continue
        ts = msg.timestamp
        if last_ts is not None:
            interval_us = (ts - last_ts) * 1e6
            if 1_000 < interval_us < 200_000:
                intervals.append(interval_us)
                count += 1
                if count % REPORT_EVERY == 0:
                    avg = statistics.mean(intervals)
                    std = statistics.stdev(intervals) if len(intervals) > 1 else 0.0
                    mn = min(intervals)
                    mx = max(intervals)
                    print(
                        f"Samples: {count:6d} | Min: {mn:8.1f} µs | Max: {mx:8.1f} µs"
                        f" | Avg: {avg:8.1f} µs | Jitter: {avg - NOMINAL_US:+7.1f} µs"
                        f" | StdDev: {std:7.1f} µs"
                    )
        last_ts = ts

    rx_bus.shutdown()

    if not intervals:
        return {"count": 0}

    avg = statistics.mean(intervals)
    std = statistics.stdev(intervals) if len(intervals) > 1 else 0.0
    return {
        "count": len(intervals),
        "min_us": min(intervals),
        "avg_us": avg,
        "max_us": max(intervals),
        "jitter_us": avg - NOMINAL_US,
        "std_dev_us": std,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Python SYNC jitter benchmark (receiver)")
    parser.add_argument(
        "transport",
        nargs="?",
        default="unicast",
        choices=["broadcast", "unicast", "multicast"],
    )
    parser.add_argument("--target", default=None, help="Override target/group IP")
    parser.add_argument("--port", type=int, default=1752)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument(
        "--start-sim",
        action="store_true",
        help="Auto-start canopen-cli simulate as a subprocess",
    )
    args = parser.parse_args()

    target = args.target or TRANSPORT_TARGETS[args.transport]

    sim_proc = None
    if args.start_sim:
        cli = "./target/release/canopen-cli"
        sim_proc = subprocess.Popen(
            [cli, "simulate", "--target", target, "--port", str(args.port), "--duration-secs", "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)

    try:
        stats = run_receiver(target, args.port, args.duration)
    finally:
        if sim_proc:
            sim_proc.terminate()
            sim_proc.wait()

    print()
    print("=== Final results ===")
    if stats["count"] == 0:
        print("No frames received. Is the simulator running?")
        print(f"  Run: ./target/release/canopen-cli simulate --target {target} --port {args.port}")
        sys.exit(1)

    print(f"  Transport   : {args.transport} ({target}:{args.port})")
    print(f"  Samples     : {stats['count']}")
    print(f"  Min interval: {stats['min_us']:.1f} µs")
    print(f"  Avg interval: {stats['avg_us']:.1f} µs")
    print(f"  Max interval: {stats['max_us']:.1f} µs")
    print(f"  Avg jitter  : {stats['jitter_us']:.1f} µs ({stats['jitter_us'] / 1000:.2f} ms)")
    print(f"  StdDev      : {stats['std_dev_us']:.1f} µs")


if __name__ == "__main__":
    main()
