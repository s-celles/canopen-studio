"""
Emissions readiness: which on-board monitors have run, and which have not.

A vehicle does not test its emissions systems continuously. Each monitor runs when its
own conditions are met — a warm engine, a steady cruise, a fuel level in range — and
reports whether it has completed since the codes were last cleared (PID 01) or during
the current drive cycle (PID 41). An inspection reads exactly this: a lamp that is off
proves little if the monitors that would light it have not run yet.

The four bytes cannot be decoded by a flat bit table. Bytes C and D name different
monitors on a spark-ignition engine and on a compression-ignition one, and bit 3 of
byte B says which applies. Hence this module rather than an entry in the YAML profile.

Nothing here passes judgement on a particular inspection regime. Some allow one
incomplete monitor, some none, and the threshold can depend on the model year; the
result reports the facts and `all_complete` is the strict reading.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

# The two PIDs carrying monitor status, and what each covers.
PID_MONITOR_STATUS = 0x01
PID_MONITOR_THIS_CYCLE = 0x41
SCOPES = {PID_MONITOR_STATUS: "since_codes_cleared", PID_MONITOR_THIS_CYCLE: "this_drive_cycle"}

# Byte B: available in bits 0-2, incomplete in bits 4-6, the same on every engine.
COMMON_MONITORS: Tuple[Tuple[str, str], ...] = (
    ("misfire", "Misfire"),
    ("fuel_system", "Fuel system"),
    ("components", "Comprehensive components"),
)

# Bytes C (available) and D (incomplete), by bit, for each ignition type. None marks a
# bit the standard reserves.
SPARK_MONITORS: Tuple[Optional[Tuple[str, str]], ...] = (
    ("catalyst", "Catalyst"),
    ("heated_catalyst", "Heated catalyst"),
    ("evaporative_system", "Evaporative system"),
    ("secondary_air", "Secondary air system"),
    ("ac_refrigerant", "A/C refrigerant"),
    ("oxygen_sensor", "Oxygen sensor"),
    ("oxygen_sensor_heater", "Oxygen sensor heater"),
    ("egr_vvt", "EGR and/or VVT system"),
)
COMPRESSION_MONITORS: Tuple[Optional[Tuple[str, str]], ...] = (
    ("nmhc_catalyst", "NMHC catalyst"),
    ("nox_aftertreatment", "NOx/SCR aftertreatment"),
    None,
    ("boost_pressure", "Boost pressure"),
    None,
    ("exhaust_gas_sensor", "Exhaust gas sensor"),
    ("pm_filter", "PM filter"),
    ("egr_vvt", "EGR and/or VVT system"),
)


@dataclass(frozen=True)
class Monitor:
    """One on-board monitor: whether the vehicle has it, and whether it has run."""

    name: str
    label: str
    available: bool
    complete: bool

    @property
    def status(self) -> str:
        if not self.available:
            return "not_available"
        return "complete" if self.complete else "incomplete"


@dataclass
class Readiness:
    """
    Monitor status as one ECU, or several combined, reports it.

    Attributes:
        scope: `since_codes_cleared` (PID 01) or `this_drive_cycle` (PID 41).
        ignition: `spark` or `compression`.
        mil_on: Whether the malfunction indicator lamp is commanded on. None for PID 41,
            whose first byte is reserved.
        dtc_count: Confirmed emissions-related codes. None for PID 41.
        monitors: Every monitor the ignition type defines, available or not.
        sources: The ECUs this was read from.
    """

    scope: str
    ignition: str
    mil_on: Optional[bool]
    dtc_count: Optional[int]
    monitors: List[Monitor] = field(default_factory=list)
    sources: List[int] = field(default_factory=list)

    @property
    def available(self) -> List[Monitor]:
        return [m for m in self.monitors if m.available]

    @property
    def incomplete(self) -> List[Monitor]:
        return [m for m in self.monitors if m.available and not m.complete]

    @property
    def all_complete(self) -> bool:
        """The strict reading: lamp off, and every monitor the vehicle has has run."""
        return not self.mil_on and not self.incomplete

    def as_dict(self) -> Dict[str, object]:
        return {
            "scope": self.scope,
            "ignition": self.ignition,
            "mil_on": self.mil_on,
            "dtc_count": self.dtc_count,
            "all_complete": self.all_complete,
            "incomplete": [m.name for m in self.incomplete],
            "monitors": {m.name: m.status for m in self.monitors},
            "ecus": [f"0x{ecu:X}" for ecu in self.sources],
        }


def decode_monitor_status(
    data: bytes, pid: int = PID_MONITOR_STATUS, source: Optional[int] = None, compression: Optional[bool] = None
) -> Readiness:
    """
    Decode the four bytes of PID 01 or PID 41.

    Args:
        data: Bytes A to D, after the PID echo.
        pid: Which of the two PIDs they answer.
        source: The ECU that sent them.
        compression: Force the ignition type. By default it is read from bit B3; pass
            PID 01's answer when decoding PID 41, where some ECUs leave that bit clear.

    Raises:
        ValueError: If fewer than four bytes are given, or the PID is neither 01 nor 41.
    """
    if pid not in SCOPES:
        raise ValueError(f"PID 0x{pid:02X} does not carry monitor status")
    if len(data) < 4:
        raise ValueError(f"monitor status is 4 bytes, got {len(data)}")
    a, b, c, d = data[:4]

    if compression is None:
        compression = bool(b & 0x08)

    monitors = [
        Monitor(name, label, available=bool(b & (1 << bit)), complete=not b & (1 << (bit + 4)))
        for bit, (name, label) in enumerate(COMMON_MONITORS)
    ]
    table = COMPRESSION_MONITORS if compression else SPARK_MONITORS
    for bit, entry in enumerate(table):
        if entry is None:
            continue
        name, label = entry
        monitors.append(Monitor(name, label, available=bool(c & (1 << bit)), complete=not d & (1 << bit)))

    since_clear = pid == PID_MONITOR_STATUS
    return Readiness(
        scope=SCOPES[pid],
        ignition="compression" if compression else "spark",
        mil_on=bool(a & 0x80) if since_clear else None,
        dtc_count=(a & 0x7F) if since_clear else None,
        monitors=monitors,
        sources=[source] if source is not None else [],
    )


def combine(readings: Iterable[Readiness]) -> Optional[Readiness]:
    """
    Merge what several ECUs report into the vehicle's status.

    Several controllers can answer PID 01 — an engine and a transmission, or the units
    of a multi-controller powertrain. The lamp is on if any commands it; a monitor is available if
    any ECU implements it, and complete only if every ECU implementing it says so.

    Returns:
        The combined status, or None when there was nothing to combine.
    """
    readings = list(readings)
    if not readings:
        return None
    first = readings[0]

    merged: Dict[str, Tuple[str, bool, bool]] = {}
    order: List[str] = []
    for reading in readings:
        for monitor in reading.monitors:
            if monitor.name not in merged:
                merged[monitor.name] = (monitor.label, False, True)
                order.append(monitor.name)
            label, available, complete = merged[monitor.name]
            if monitor.available:
                available = True
                complete = complete and monitor.complete
            merged[monitor.name] = (label, available, complete)

    lamps = [r.mil_on for r in readings if r.mil_on is not None]
    counts = [r.dtc_count for r in readings if r.dtc_count is not None]
    return Readiness(
        scope=first.scope,
        ignition=first.ignition,
        mil_on=any(lamps) if lamps else None,
        dtc_count=sum(counts) if counts else None,
        monitors=[Monitor(name, *merged[name]) for name in order],
        sources=sorted({ecu for r in readings for ecu in r.sources}),
    )
