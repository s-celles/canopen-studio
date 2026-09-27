"""
Continuous polling of live-data PIDs, and recording what comes back.

OBD-II has no subscription: a value is only ever the answer to a request. Watching a
parameter change therefore means asking for it again and again, and an ELM327 answers
one request at a time — six to eleven per second measured from a clone over Bluetooth
LE, more over USB or a native CAN adapter. Every PID on screen shares that budget, so the refresh interval of
each one is the time a whole cycle takes, and grows with the number polled.

That rate is a property of the adapter and the vehicle, not something to promise. The
poller measures what it actually achieves and reports it, and every recorded sample
carries its own timestamp, so whatever is computed from a recording later — a
consumption, an acceleration time — can state the sampling it rests on.

The poller runs its own thread and owns the session while it runs: the client is not
safe to share, so callers must not issue other requests until it has stopped.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from __future__ import annotations

import collections
import csv
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

from ..interface import DiagnosticError, TransportError
from .pids import MODE_CURRENT_DATA, PidValue

# How many recent cycles the reported rate is averaged over: long enough to smooth one
# slow answer, short enough to follow a change such as more PIDs being added.
RATE_WINDOW = 10


@dataclass(frozen=True)
class Sample:
    """One reading, stamped with when it arrived."""

    elapsed: float
    timestamp: float
    reading: PidValue

    @property
    def pid(self) -> int:
        return self.reading.pid

    @property
    def source(self) -> Optional[int]:
        return self.reading.source


@dataclass
class PollStats:
    """What the poller has achieved so far."""

    cycles: int = 0
    requests: int = 0
    answered: int = 0
    errors: int = 0
    elapsed: float = 0.0
    recent_cycle_seconds: Optional[float] = None
    recent_requests_per_second: Optional[float] = None

    @property
    def requests_per_second(self) -> float:
        """
        The rate the link sustains now, over the last few cycles.

        Not the average since the start: the first request after connecting can carry
        the adapter's protocol search, seconds long, and would understate the rate for
        as long as the run lasts.
        """
        if self.recent_requests_per_second is not None:
            return self.recent_requests_per_second
        return self.average_requests_per_second

    @property
    def average_requests_per_second(self) -> float:
        return self.requests / self.elapsed if self.elapsed > 0 else 0.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "cycles": self.cycles,
            "requests": self.requests,
            "answered": self.answered,
            "errors": self.errors,
            "elapsed_s": round(self.elapsed, 3),
            "requests_per_second": round(self.requests_per_second, 2),
            "average_requests_per_second": round(self.average_requests_per_second, 2),
            "refresh_interval_s": None if self.recent_cycle_seconds is None else round(self.recent_cycle_seconds, 3),
        }


class PidPoller:
    """
    Ask a fixed set of mode 01 PIDs over and over, as fast as the link allows.

    Args:
        client: An open `J1979Client`.
        pids: The PIDs to poll, in the order to ask them.
        on_cycle: Called from the poller's thread after each cycle with that cycle's
            samples and the running statistics. A GUI must marshal it to its own thread.
        recorder: Where to write every sample, if anywhere.
        interval: Minimum seconds between the starts of two cycles, 0 for flat out.
        clock: Monotonic time source, replaceable in tests.
        wallclock: Epoch time source for the timestamps, replaceable in tests.
    """

    def __init__(
        self,
        client,
        pids: Sequence[int],
        on_cycle: Optional[Callable[[List[Sample], PollStats], None]] = None,
        recorder: Optional["CsvRecorder"] = None,
        interval: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        wallclock: Callable[[], float] = time.time,
    ):
        if not pids:
            raise ValueError("nothing to poll: give at least one PID")
        self.client = client
        self.pids = list(pids)
        self.on_cycle = on_cycle
        self.recorder = recorder
        self.interval = max(interval, 0.0)
        self.clock = clock
        self.wallclock = wallclock
        self.stats = PollStats()
        self.latest: Dict[Tuple[int, Optional[int]], Sample] = {}
        # Set when the link was lost: the poller stops and says why.
        self.error: Optional[Exception] = None
        self._started: Optional[float] = None
        self._cycle_times: Deque[Tuple[float, int]] = collections.deque(maxlen=RATE_WINDOW)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- One cycle ---------------------------------------------------------

    def poll_cycle(self) -> List[Sample]:
        """
        Ask every PID once and return what came back.

        A PID that fails is counted and skipped, since one bad answer should not end a
        recording. A lost link is different: it is raised, and ends the run.
        """
        if self._started is None:
            self._started = self.clock()
        cycle_start = self.clock()
        requests_before = self.stats.requests
        samples: List[Sample] = []

        for pid in self.pids:
            if self._stop.is_set():
                break
            self.stats.requests += 1
            try:
                readings = self.client.read_pid(pid, MODE_CURRENT_DATA)
            except TransportError:
                raise
            except DiagnosticError:
                self.stats.errors += 1
                continue
            if readings:
                self.stats.answered += 1
            arrived = self.clock()
            stamp = self.wallclock()
            for reading in readings:
                sample = Sample(elapsed=arrived - self._started, timestamp=stamp, reading=reading)
                samples.append(sample)
                self.latest[(reading.pid, reading.source)] = sample

        now = self.clock()
        self._cycle_times.append((now - cycle_start, self.stats.requests - requests_before))
        self.stats.cycles += 1
        self.stats.elapsed = now - self._started
        window = sum(seconds for seconds, _ in self._cycle_times)
        self.stats.recent_cycle_seconds = window / len(self._cycle_times)
        if window > 0:
            self.stats.recent_requests_per_second = sum(count for _, count in self._cycle_times) / window

        if self.recorder is not None and samples:
            self.recorder.write(samples)
        if self.on_cycle is not None:
            self.on_cycle(samples, self.stats)
        return samples

    # -- Running -----------------------------------------------------------

    def run(self, max_cycles: Optional[int] = None, duration: Optional[float] = None) -> PollStats:
        """
        Poll until stopped, `max_cycles` cycles have run, or `duration` seconds passed.

        Blocking; `start()` runs it on a thread instead. A lost link is kept in `error`
        rather than raised, so a background run ends cleanly and says why.
        """
        began = self.clock()
        while not self._stop.is_set():
            cycle_start = self.clock()
            try:
                self.poll_cycle()
            except TransportError as exc:
                self.error = exc
                break
            if max_cycles is not None and self.stats.cycles >= max_cycles:
                break
            if duration is not None and self.clock() - began >= duration:
                break
            if self.interval:
                self._stop.wait(max(0.0, self.interval - (self.clock() - cycle_start)))
        return self.stats

    def start(self) -> None:
        """Poll on a background thread until `stop()`."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="obd-poller", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """
        Ask the poller to stop and wait for it, so the session is free afterwards.

        The request in flight is allowed to finish: cutting an ELM327 off mid-answer
        leaves its reply in the buffer to confuse the next command.
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        if self.recorder is not None:
            self.recorder.close()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


class CsvRecorder:
    """
    Write samples to a CSV file, one row per reading.

    The long layout — one row per value rather than one column per PID — holds several
    ECUs answering the same PID, and PIDs sampled at different instants, without
    inventing values for the gaps.

    Args:
        path: The file to create. An existing file is replaced.
    """

    HEADER = ("timestamp", "elapsed_s", "ecu", "pid", "name", "value", "unit")

    def __init__(self, path):
        self.path = Path(path)
        self._file = open(self.path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(self.HEADER)
        self.rows = 0

    def write(self, samples: Iterable[Sample]) -> None:
        if self._file.closed:
            return
        for sample in samples:
            reading = sample.reading
            self._writer.writerow(
                (
                    datetime.fromtimestamp(sample.timestamp, timezone.utc).isoformat(timespec="milliseconds"),
                    f"{sample.elapsed:.3f}",
                    "" if reading.source is None else f"0x{reading.source:X}",
                    reading.definition.key,
                    reading.name,
                    format_value(reading.value),
                    reading.unit or "",
                )
            )
            self.rows += 1
        # A recording is most valuable when something went wrong; lose one cycle at most.
        self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> "CsvRecorder":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def format_value(value) -> str:
    """Render a decoded value for a CSV cell: numbers plainly, bit fields as JSON."""
    if isinstance(value, (bytes, bytearray)):
        return value.hex().upper()
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value)


def summarise(samples: Iterable[Sample]) -> Dict[str, Dict[str, object]]:
    """
    Condense samples per parameter: count, last value and, for numbers, min/max/mean.

    Keyed by `name@ecu`, so two ECUs answering the same PID stay apart.
    """
    groups: Dict[str, List[Sample]] = collections.defaultdict(list)
    for sample in samples:
        ecu = "" if sample.source is None else f"@0x{sample.source:X}"
        groups[f"{sample.reading.name}{ecu}"].append(sample)

    summary: Dict[str, Dict[str, object]] = {}
    for key, group in groups.items():
        values = [s.reading.value for s in group]
        entry: Dict[str, object] = {
            "count": len(group),
            "unit": group[-1].reading.unit,
            "last": format_value(values[-1]) if not isinstance(values[-1], (int, float)) else values[-1],
        }
        numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if numbers:
            entry.update(min=min(numbers), max=max(numbers), mean=sum(numbers) / len(numbers))
        summary[key] = entry
    return summary
