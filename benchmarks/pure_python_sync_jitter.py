"""
Pure-Python SYNC Jitter Benchmark — reference baseline.

Uses only stdlib + python-can msgpack, NO Rust extension (canopen_core is not imported).
Simulates the pre-Phase-1B Python stack: time.sleep() for timing, raw socket for UDP.

Usage:
    uv run python benchmarks/pure_python_sync_jitter.py unicast   [--duration 30]
    uv run python benchmarks/pure_python_sync_jitter.py broadcast [--duration 30]
    uv run python benchmarks/pure_python_sync_jitter.py multicast [--duration 30]
"""

import argparse
import socket
import statistics
import threading
import time
import sys

try:
    import msgpack  # type: ignore
except ImportError:
    sys.exit("msgpack is required: uv run --with msgpack python ...")

# CAN IDs
SYNC_ID = 0x080


# Wire format: python-can udp_multicast msgpack layout
#   {"arbitration_id": int, "data": bytes, "timestamp": float,
#    "is_extended_id": bool, "is_remote_frame": bool, "is_error_frame": bool}
def _pack_frame(arb_id: int, data: bytes) -> bytes:
    return msgpack.packb(
        {
            "arbitration_id": arb_id,
            "data": data,
            "timestamp": time.time(),
            "is_extended_id": False,
            "is_remote_frame": False,
            "is_error_frame": False,
        },
        use_bin_type=True,
    )


def _make_socket(target: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

    ip = socket.inet_aton(target)
    is_mc = (ip[0] & 0xF0) == 0xE0  # 224.0.0.0/4

    if is_mc:
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_ADD_MEMBERSHIP,
            # IP_ADD_MEMBERSHIP takes the interface to join on; INADDR_ANY means "any".
            socket.inet_aton(target) + socket.inet_aton("0.0.0.0"),  # nosec B104
        )
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    # A bus listener has to accept frames from the whole network, which is the
    # point of the benchmark; loopback targets stay on loopback.
    bind_ip = "127.0.0.1" if target.startswith("127.") else "0.0.0.0"  # nosec B104
    sock.bind((bind_ip, port))
    return sock


class PurePythonSimulator(threading.Thread):
    """Sends SYNC frames at 50 Hz using time.sleep (pure Python, no Rust)."""

    def __init__(self, target: str, port: int, interval_s: float = 0.020):
        super().__init__(daemon=True)
        self.target = target
        self.port = port
        self.interval_s = interval_s
        self._stop = threading.Event()

    def run(self) -> None:
        sock = _make_socket(self.target, 9999 if self.target.startswith("127.") else 19750)
        payload = _pack_frame(SYNC_ID, b"")
        while not self._stop.is_set():
            sock.sendto(payload, (self.target, self.port))
            time.sleep(self.interval_s)
        sock.close()

    def stop(self) -> None:
        self._stop.set()


REPORT_EVERY = 50
NOMINAL_US = 20_000.0


def run_receiver(target: str, port: int, duration: float) -> dict:
    sock = _make_socket(target, port)
    sock.settimeout(0.1)

    intervals: list[float] = []
    last_ts: float | None = None
    count = 0
    deadline = time.monotonic() + duration

    print(f"==> Pure-Python SYNC jitter receiver | target={target}:{port} | {duration:.0f}s")

    while time.monotonic() < deadline:
        try:
            data, _ = sock.recvfrom(4096)
        except TimeoutError:
            continue
        except OSError:
            continue

        try:
            msg = msgpack.unpackb(data, raw=False)
            arb_id = msg.get("arbitration_id", -1)
        except Exception:
            continue

        if arb_id != SYNC_ID:
            continue

        ts = time.perf_counter()  # use local monotonic clock for interval measurement
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

    sock.close()

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


TRANSPORT_TARGETS = {
    "broadcast": "192.168.30.255",
    "unicast": "127.0.0.1",
    "multicast": "239.0.0.1",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Pure-Python SYNC jitter reference benchmark")
    parser.add_argument(
        "transport",
        nargs="?",
        default="unicast",
        choices=["broadcast", "unicast", "multicast"],
    )
    parser.add_argument("--target", default=None)
    parser.add_argument("--port", type=int, default=1754)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument(
        "--start-sim",
        action="store_true",
        help="Auto-start the pure-Python simulator in a background thread",
    )
    args = parser.parse_args()

    target = args.target or TRANSPORT_TARGETS[args.transport]

    sim = None
    if args.start_sim:
        sim = PurePythonSimulator(target, args.port)
        sim.start()
        time.sleep(0.3)

    try:
        stats = run_receiver(target, args.port, args.duration)
    finally:
        if sim:
            sim.stop()
            sim.join(timeout=2)

    print()
    print("=== Final results (pure Python — no Rust) ===")
    if stats["count"] == 0:
        print("No frames received. Is the simulator running?")
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
