"""
What may write to a vehicle, and what may not.

Reading a PID asks an ECU a question. Writing to one changes a car that people drive, and
some of those changes cannot be undone from a laptop. So everything in this package reads
by default, and anything that does not has to come through here.

Three independent gates, all of which must open:

A kill switch
    `CANOPEN_STUDIO_DIAG_WRITE` must be set. It is off by default, following the same
    pattern as `CANOPEN_STUDIO_A2A` in `canopen_studio.agent_security`: a capability that
    can damage something is opt-in at the process level, not at the call site.

A per-profile whitelist
    A manufacturer service is only allowed if the active vehicle profile names it. A
    profile is a reviewed data file, so allowing a write is a deliberate act recorded
    somewhere a person can read, rather than an argument someone passed once.

An explicit confirmation
    The caller must say `confirm=True` for this specific call. It cannot be defaulted on
    and it does not persist, so no wrapper can enable writes for everything downstream of
    it.

**The UDS write services have no request path in this release.** `WriteDataByIdentifier`
(0x2E), `RoutineControl` (0x31) and `InputOutputControlByIdentifier` (0x2F) are refused
here even with all three gates open, because nothing in this package can send them. The
gate exists so that adding one later is a matter of calling `check()` — and so that the
refusal is explicit rather than an accident of what happens not to be written yet.

Clearing trouble codes (mode 04) is implemented, because reading and clearing codes is
what a diagnostic tool is for. It still needs the kill switch and a confirmation: it
erases the readiness monitors, which a vehicle then needs a full drive cycle to rebuild,
and an emissions test taken before that fails.

None of these are exposed as MCP tools. An agent reads.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .. import agent_security as _sec
from .interface import DiagnosticError, DiagnosticInterface

# Setting this enables the write path at all. Off by default, like CANOPEN_STUDIO_A2A.
WRITE_ENABLED_ENV = "CANOPEN_STUDIO_DIAG_WRITE"

# UDS services that modify an ECU. Named here so a refusal can say which one it refused.
WRITE_SERVICES: Dict[int, str] = {
    0x2E: "WriteDataByIdentifier",
    0x31: "RoutineControl",
    0x2F: "InputOutputControlByIdentifier",
}

# J1979 services that change the vehicle's state without writing a parameter.
STATEFUL_SERVICES: Dict[int, str] = {
    0x04: "ClearDiagnosticInformation",
    0x08: "ControlOnBoardSystemTestOrComponent",
}

# Services this release can actually send. Everything else in the two tables above is
# refused by the gate, whatever the configuration says.
IMPLEMENTED_SERVICES = frozenset({0x04})

MODE_CLEAR_DTC = 0x04


class DiagnosticWriteRefused(DiagnosticError):
    """A write was refused. The message says which gate refused it and why."""


def service_name(service: int) -> str:
    """Name a service, for a refusal message."""
    return WRITE_SERVICES.get(service) or STATEFUL_SERVICES.get(service) or f"service 0x{service:02X}"


def writes_enabled() -> bool:
    """Whether the process-level kill switch has been opened."""
    return _sec.env_flag(WRITE_ENABLED_ENV, default=False)


@dataclass(frozen=True)
class WhitelistEntry:
    """One write a vehicle profile permits."""

    service: int
    identifier: Optional[int] = None
    description: str = ""

    def covers(self, service: int, identifier: Optional[int] = None) -> bool:
        """
        Whether this entry permits a call.

        An entry with no identifier covers the whole service, which is how a profile
        allows a routine family without listing every member.
        """
        if service != self.service:
            return False
        if self.identifier is None:
            return True
        return identifier == self.identifier

    @classmethod
    def from_mapping(cls, entry: Mapping[str, Any]) -> "WhitelistEntry":
        """
        Build an entry from a profile's `write_whitelist` section.

        Raises:
            DiagnosticWriteRefused: If the entry is malformed. A whitelist that cannot be
                read must not be treated as an empty one that silently allows nothing
                *and* hides a typo that was meant to allow something.
        """
        if not isinstance(entry, Mapping):
            raise DiagnosticWriteRefused(f"a write_whitelist entry must be a mapping, got {type(entry).__name__}")
        if "service" not in entry:
            raise DiagnosticWriteRefused("a write_whitelist entry needs a service")
        try:
            service = int(str(entry["service"]), 0)
            identifier = int(str(entry["identifier"]), 0) if entry.get("identifier") is not None else None
        except ValueError as exc:
            raise DiagnosticWriteRefused(f"a write_whitelist entry has a non-numeric field: {exc}") from exc
        return cls(service=service, identifier=identifier, description=str(entry.get("description") or ""))

    def __str__(self) -> str:
        target = f" 0x{self.identifier:04X}" if self.identifier is not None else ""
        suffix = f" — {self.description}" if self.description else ""
        return f"{service_name(self.service)}{target}{suffix}"


class WriteGate:
    """
    The three checks every write passes, or does not.

    Args:
        whitelist: What the active vehicle profile permits. A `ResolvedProfile`, a list
            of raw entries, or nothing at all — which permits nothing.
        enabled: Override the kill switch, for tests. Leave None to read the environment.
    """

    def __init__(
        self,
        whitelist: Any = None,
        enabled: Optional[bool] = None,
    ):
        self._entries: Tuple[WhitelistEntry, ...] = tuple(_coerce_whitelist(whitelist))
        self._enabled = enabled

    @property
    def entries(self) -> Tuple[WhitelistEntry, ...]:
        """What the active profile permits."""
        return self._entries

    @property
    def enabled(self) -> bool:
        """Whether the process-level kill switch is open."""
        return writes_enabled() if self._enabled is None else self._enabled

    def allows(self, service: int, identifier: Optional[int] = None) -> bool:
        """Whether a call would pass every gate bar the per-call confirmation."""
        try:
            self.check(service, identifier=identifier, confirm=True)
        except DiagnosticWriteRefused:
            return False
        return True

    def check(self, service: int, identifier: Optional[int] = None, confirm: bool = False) -> None:
        """
        Refuse a write, or return silently.

        Args:
            service: The service identifier, e.g. 0x2E.
            identifier: The data identifier or routine the call targets, when it has one.
            confirm: The caller's explicit confirmation for this one call.

        Raises:
            DiagnosticWriteRefused: With a message naming the gate that refused it.
        """
        name = service_name(service)

        if service not in IMPLEMENTED_SERVICES:
            raise DiagnosticWriteRefused(
                f"{name} is refused: this release has no request path for it. The gate is in place so that "
                f"adding one is a deliberate change rather than an accident."
            )

        if not self.enabled:
            raise DiagnosticWriteRefused(
                f"{name} is refused: writing to a vehicle is off by default. "
                f"Set {WRITE_ENABLED_ENV}=1 to enable it for this process."
            )

        if service in WRITE_SERVICES and not any(entry.covers(service, identifier) for entry in self._entries):
            target = f" 0x{identifier:04X}" if identifier is not None else ""
            raise DiagnosticWriteRefused(
                f"{name}{target} is refused: the active vehicle profile does not list it in write_whitelist. "
                f"Allowing it means editing that profile, which is a reviewed file."
            )

        if not confirm:
            raise DiagnosticWriteRefused(
                f"{name} is refused: it needs an explicit confirm=True for this call. "
                f"Confirmation is per call and never persists."
            )

    def describe(self) -> Dict[str, Any]:
        """A JSON-friendly summary of the current posture, for the GUI and for logs."""
        return {
            "enabled": self.enabled,
            "environment_variable": WRITE_ENABLED_ENV,
            "implemented_services": [service_name(service) for service in sorted(IMPLEMENTED_SERVICES)],
            "refused_services": [name for service, name in sorted(WRITE_SERVICES.items())],
            "whitelist": [str(entry) for entry in self._entries],
        }

    def __str__(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        return f"diagnostic writes {state}, {len(self._entries)} whitelisted entry(ies)"


def _coerce_whitelist(whitelist: Any) -> List[WhitelistEntry]:
    """Accept a profile, a list of entries, or nothing."""
    if whitelist is None:
        return []
    entries = getattr(whitelist, "write_whitelist", whitelist)
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Iterable):
        raise DiagnosticWriteRefused(f"a write whitelist must be a sequence, got {type(entries).__name__}")
    return [entry if isinstance(entry, WhitelistEntry) else WhitelistEntry.from_mapping(entry) for entry in entries]


def clear_trouble_codes(
    interface: DiagnosticInterface,
    gate: WriteGate,
    confirm: bool = False,
    timeout: Optional[float] = None,
) -> Sequence[int]:
    """
    Clear stored trouble codes and the values they carry, via mode 04.

    This is destructive in a way that is easy to underestimate. Alongside the codes it
    erases the freeze frame and the readiness monitors, which a vehicle needs a full
    drive cycle to rebuild — and an emissions test taken before that fails. Permanent
    codes (mode 0A) are not affected: only the vehicle itself may clear those.

    Args:
        interface: An open diagnostic session.
        gate: The write gate for the active profile.
        confirm: Explicit confirmation for this call. Without it, nothing is sent.
        timeout: Seconds to allow the request.

    Returns:
        The ECUs that acknowledged, in the order they answered.

    Raises:
        DiagnosticWriteRefused: If any gate refuses. Nothing is transmitted in that case.
    """
    gate.check(MODE_CLEAR_DTC, confirm=confirm)
    replies = interface.positive_responses(MODE_CLEAR_DTC, timeout=timeout)
    return [reply.source for reply in replies]
