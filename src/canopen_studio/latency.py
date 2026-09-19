"""
CAN bus latency and jitter measurement module.

Provides Round-Trip Time (RTT) measurement via CAN Ping / Echo (0x7E0 / 0x7E1)
and real-time inter-frame jitter monitoring on cyclic frames (SYNC 0x080, Heartbeat).

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Any, Deque, Dict, Optional, Tuple

import can

# Default diagnostic IDs for CAN ping
DEFAULT_PING_REQ_ID = 0x7E0
DEFAULT_PING_RESP_ID = 0x7E1
DEFAULT_SYNC_ID = 0x080
DEFAULT_SYNC_NOMINAL_MS = 20.0  # 50 Hz nominal


class LatencyTracker:
    """
    Measures and tracks bus latency (RTT) and jitter on periodic CAN messages.

    - Active Ping: sends request frames (0x7E0) with sequence numbers and measures
      response time (0x7E1) without requiring clock synchronization between machines.
    - Auto-Echo: can act as a responder on the bus, immediately echoing 0x7E0 as 0x7E1.
    - Jitter Monitoring: measures interval stability on SYNC frames (nominal 20 ms).
    """

    def __init__(
        self,
        ping_req_id: int = DEFAULT_PING_REQ_ID,
        ping_resp_id: int = DEFAULT_PING_RESP_ID,
        sync_id: int = DEFAULT_SYNC_ID,
        sync_nominal_ms: float = DEFAULT_SYNC_NOMINAL_MS,
        auto_echo: bool = True,
        ping_timeout: float = 2.0,
    ) -> None:
        self.ping_req_id = ping_req_id
        self.ping_resp_id = ping_resp_id
        self.sync_id = sync_id
        self.sync_nominal_ms = sync_nominal_ms
        self.auto_echo = auto_echo
        self.ping_timeout = ping_timeout

        self._lock = threading.Lock()
        self._seq = 0
        self._pending_pings: Dict[int, float] = {}  # seq -> sent_perf_counter

        # RTT statistics
        self.pings_sent = 0
        self.pings_received = 0
        self.rtt_last_ms: Optional[float] = None
        self.rtt_min_ms: Optional[float] = None
        self.rtt_max_ms: Optional[float] = None
        self.rtt_history: Deque[float] = collections.deque(maxlen=100)

        # SYNC / Jitter statistics
        self.last_sync_time: Optional[float] = None
        self.sync_interval_last_ms: Optional[float] = None
        self.sync_jitter_last_ms: Optional[float] = None
        self.sync_jitter_history: Deque[float] = collections.deque(maxlen=100)
        self.sync_count = 0

    def reset(self) -> None:
        """Reset all collected metrics."""
        with self._lock:
            self._pending_pings.clear()
            self.pings_sent = 0
            self.pings_received = 0
            self.rtt_last_ms = None
            self.rtt_min_ms = None
            self.rtt_max_ms = None
            self.rtt_history.clear()
            self.last_sync_time = None
            self.sync_interval_last_ms = None
            self.sync_jitter_last_ms = None
            self.sync_jitter_history.clear()
            self.sync_count = 0

    def send_ping(self, bus: can.Bus) -> Optional[int]:
        """
        Send a CAN ping request frame (0x7E0).

        Returns:
            The sequence number of the sent ping, or None on transmission failure.
        """
        now = time.perf_counter()
        with self._lock:
            self._clean_expired(now)
            self._seq = (self._seq + 1) & 0xFFFFFFFF
            seq = self._seq
            # Payload: 4 bytes sequence + 4 bytes fraction of timestamp
            frac = int((now % 1.0) * 1_000_000) & 0xFFFFFFFF
            payload = seq.to_bytes(4, "little") + frac.to_bytes(4, "little")
            self._pending_pings[seq] = now
            self.pings_sent += 1

        msg = can.Message(
            arbitration_id=self.ping_req_id,
            data=payload,
            is_extended_id=False,
        )
        try:
            bus.send(msg)
            return seq
        except Exception as exc:
            with self._lock:
                self._pending_pings.pop(seq, None)
                self.pings_sent = max(0, self.pings_sent - 1)
            cause = getattr(exc, "__cause__", None)
            self.last_error = f"{exc} (cause: {cause!r})"
            return None

    def process_message(self, msg: can.Message, bus: Optional[can.Bus] = None) -> Optional[float]:
        """
        Inspect an incoming CAN frame to measure latency or reply to pings.

        Args:
            msg: The received CAN message.
            bus: The active CAN bus (required for auto-echo responder).

        Returns:
            The RTT in ms if this frame was an answer to our pending ping, else None.
        """
        now = time.perf_counter()
        cid = msg.arbitration_id

        # 1. Auto-echo responder: reply immediately to 0x7E0 with 0x7E1
        # Ignore our own pings that loop back on multicast/virtual interfaces
        if cid == self.ping_req_id and self.auto_echo and bus is not None and not msg.is_remote_frame:
            if len(msg.data) >= 4:
                seq = int.from_bytes(msg.data[:4], "little")
                with self._lock:
                    if seq in self._pending_pings:
                        return None  # Own ping looping back — do not echo to self

            resp = can.Message(
                arbitration_id=self.ping_resp_id,
                data=bytes(msg.data),
                is_extended_id=bool(msg.is_extended_id),
            )
            try:
                bus.send(resp)
            except Exception:
                pass
            return None

        # 2. Ping response handling: match 0x7E1 against our pending pings
        if cid == self.ping_resp_id and len(msg.data) >= 4 and not msg.is_remote_frame:
            seq = int.from_bytes(msg.data[:4], "little")
            with self._lock:
                sent_time = self._pending_pings.pop(seq, None)
                if sent_time is not None:
                    rtt = (now - sent_time) * 1000.0
                    self.pings_received += 1
                    self.rtt_last_ms = rtt
                    self.rtt_history.append(rtt)
                    self.rtt_min_ms = rtt if self.rtt_min_ms is None else min(self.rtt_min_ms, rtt)
                    self.rtt_max_ms = rtt if self.rtt_max_ms is None else max(self.rtt_max_ms, rtt)
                    return rtt

        # 3. Jitter monitoring on periodic frames (SYNC 0x080)
        if cid == self.sync_id and not msg.is_remote_frame:
            with self._lock:
                self.sync_count += 1
                if self.last_sync_time is not None:
                    interval_ms = (now - self.last_sync_time) * 1000.0
                    jitter_ms = abs(interval_ms - self.sync_nominal_ms)
                    self.sync_interval_last_ms = interval_ms
                    self.sync_jitter_last_ms = jitter_ms
                    self.sync_jitter_history.append(jitter_ms)
                self.last_sync_time = now

        return None

    def _clean_expired(self, now: float) -> None:
        """Purge pending pings that exceeded timeout."""
        expired = [s for s, t in self._pending_pings.items() if (now - t) > self.ping_timeout]
        for s in expired:
            del self._pending_pings[s]

    def get_stats(self) -> Dict[str, Any]:
        """Return a structured dictionary with latency and jitter metrics."""
        with self._lock:
            self._clean_expired(time.perf_counter())
            rtt_avg = (sum(self.rtt_history) / len(self.rtt_history)) if self.rtt_history else None
            jitter_avg = (sum(self.sync_jitter_history) / len(self.sync_jitter_history)) if self.sync_jitter_history else None
            lost = max(0, self.pings_sent - self.pings_received - len(self._pending_pings))
            loss_pct = (lost / self.pings_sent * 100.0) if self.pings_sent > 0 else 0.0

            return {
                "rtt_last_ms": round(self.rtt_last_ms, 2) if self.rtt_last_ms is not None else None,
                "rtt_avg_ms": round(rtt_avg, 2) if rtt_avg is not None else None,
                "rtt_min_ms": round(self.rtt_min_ms, 2) if self.rtt_min_ms is not None else None,
                "rtt_max_ms": round(self.rtt_max_ms, 2) if self.rtt_max_ms is not None else None,
                "pings_sent": self.pings_sent,
                "pings_received": self.pings_received,
                "pings_lost": lost,
                "loss_rate_pct": round(loss_pct, 1),
                "sync_interval_last_ms": round(self.sync_interval_last_ms, 2) if self.sync_interval_last_ms is not None else None,
                "sync_jitter_last_ms": round(self.sync_jitter_last_ms, 2) if self.sync_jitter_last_ms is not None else None,
                "sync_jitter_avg_ms": round(jitter_avg, 2) if jitter_avg is not None else None,
                "sync_count": self.sync_count,
                "auto_echo": self.auto_echo,
            }
