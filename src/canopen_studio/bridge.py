"""
CAN <-> network bridge.

Republishes the traffic of a bus onto a second bus, typically a physical CAN adapter
connected to a real device (a drive, an inverter) mirrored onto a UDP multicast group so
that remote machines observe the live bus as if they were wired to it.

The read direction (real bus -> network) is driven by the application capture loop, which
already owns the only reader on the source bus: it hands each decoded frame to forward().
The write direction (network -> real bus) needs its own reader thread and is opt-in,
because it puts frames on real hardware.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Tuple

import can

# How long an injected frame is remembered so the echo coming back from the real bus is
# not forwarded to the network again. Long enough to cover adapter round-trip, short
# enough not to swallow a genuine cyclic PDO (a 50 Hz PDO repeats every 20 ms).
DEFAULT_ECHO_WINDOW = 0.25

# Signature identifying a frame for echo suppression: id, extended flag, payload.
Signature = Tuple[int, bool, bytes]


def _signature(msg: can.Message) -> Signature:
    return (msg.arbitration_id, bool(msg.is_extended_id), bytes(msg.data))


def _copy(msg: can.Message) -> can.Message:
    """Rebuild a frame without backend-specific attributes of the originating bus."""
    return can.Message(
        arbitration_id=msg.arbitration_id,
        data=bytes(msg.data),
        is_extended_id=bool(msg.is_extended_id),
        is_remote_frame=bool(msg.is_remote_frame),
    )


class CanBridge:
    """Mirror a CAN bus onto a network bus, optionally in both directions."""

    def __init__(
        self,
        source_bus: Any,
        network_bus: Any,
        allow_inject: bool = False,
        echo_window: float = DEFAULT_ECHO_WINDOW,
        network_channel: str = "",
    ) -> None:
        """
        Args:
            source_bus: The captured bus, usually a physical CAN adapter. Never closed here.
            network_bus: The bus frames are mirrored to, usually udp_multicast. Closed on stop().
            allow_inject: Forward network frames onto source_bus. Off by default: this writes
                to real hardware and turns remote observers into remote commanders.
            echo_window: Seconds an injected frame is remembered for echo suppression.
            network_channel: Multicast address, kept for reporting only.
        """
        self.source_bus = source_bus
        self.network_bus = network_bus
        self.allow_inject = allow_inject
        self.echo_window = echo_window
        self.network_channel = network_channel
        self.running = False
        self.stats: Dict[str, int] = {
            "forwarded": 0,
            "injected": 0,
            "suppressed": 0,
            "suppressed_inject": 0,
            "errors": 0,
        }
        # Loop protection runs both ways: a multicast socket receives its own datagrams,
        # so each direction remembers what it just sent and discards the reflection.
        self._injected: Dict[Signature, float] = {}
        self._forwarded: Dict[Signature, float] = {}
        self._lock = threading.Lock()
        self._inject_thread: Optional[threading.Thread] = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Arm the bridge, starting the injection reader only when injection is allowed."""
        if self.running:
            return
        self.running = True
        if self.allow_inject:
            self._inject_thread = threading.Thread(target=self._inject_loop, daemon=True, name="can-bridge-inject")
            self._inject_thread.start()

    def stop(self) -> None:
        """Disarm the bridge and release the network bus. The source bus is left untouched."""
        if not self.running:
            return
        self.running = False
        if self._inject_thread:
            self._inject_thread.join(timeout=1.0)
            self._inject_thread = None
        try:
            self.network_bus.shutdown()
        except Exception:
            pass
        with self._lock:
            self._injected.clear()
            self._forwarded.clear()

    # -- read direction: real bus -> network -------------------------------

    def forward(self, msg: can.Message) -> bool:
        """
        Mirror one captured frame onto the network bus.

        Called from the application capture loop for every received frame, so it must never
        raise: a network failure must not stop the analyzer from decoding the real bus.

        Returns True when the frame was actually sent.
        """
        if not self.running:
            return False
        sig = _signature(msg)
        if self._consume_echo(self._injected, sig):
            self.stats["suppressed"] += 1
            return False
        # Only remember the frame when a return path exists, otherwise a cyclic PDO would
        # be throttled by its own history.
        if self.allow_inject:
            self._remember(self._forwarded, sig)
        try:
            self.network_bus.send(_copy(msg))
        except Exception:
            self.stats["errors"] += 1
            return False
        self.stats["forwarded"] += 1
        return True

    # -- write direction: network -> real bus ------------------------------

    def _inject_loop(self) -> None:
        """Replay frames coming from the network onto the real bus."""
        while self.running:
            try:
                msg = self.network_bus.recv(timeout=0.1)
            except Exception:
                if self.running:
                    self.stats["errors"] += 1
                continue
            if not msg or not self.running:
                continue
            sig = _signature(msg)
            if self._consume_echo(self._forwarded, sig):
                self.stats["suppressed_inject"] += 1
                continue
            # Remember the frame before sending it: the real bus may echo it back into the
            # capture loop before send() has even returned.
            self._remember(self._injected, sig)
            try:
                self.source_bus.send(_copy(msg))
            except Exception:
                self.stats["errors"] += 1
                continue
            self.stats["injected"] += 1

    # -- echo suppression --------------------------------------------------

    def _remember(self, pending: Dict[Signature, float], sig: Signature) -> None:
        with self._lock:
            pending[sig] = time.monotonic()

    def _consume_echo(self, pending: Dict[Signature, float], sig: Signature) -> bool:
        """Consume a pending echo for this frame, dropping entries older than the window."""
        now = time.monotonic()
        with self._lock:
            stamped = pending.pop(sig, None)
            if stamped is not None and now - stamped <= self.echo_window:
                return True
            if pending:
                expired = [k for k, t in pending.items() if now - t > self.echo_window]
                for k in expired:
                    del pending[k]
        return False

    # -- reporting ---------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Return a JSON-friendly snapshot for the MCP and A2A servers."""
        return {
            "running": self.running,
            "channel": self.network_channel,
            "allow_inject": self.allow_inject,
            "stats": dict(self.stats),
        }
