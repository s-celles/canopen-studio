"""
What an ELM327 can actually do, as opposed to what it says it is.

Counterfeit ELM327 boards are the norm rather than the exception, and the usual tell is
that `ATI` reports a version the firmware does not live up to: a v1.4-era die flashed to
answer "ELM327 v2.1", missing the CAN receive filter and the flow control controls that
the claimed version is supposed to have.

So the version is read but never trusted. Each capability is probed by sending the
command and watching for the '?' that means "unknown command". Every probe below is
harmless and leaves the adapter in its default state — `ATCRA` with no argument clears
the filter, `ATAT1` selects the default timing mode, `ATFCSM0` the default flow control
mode, `ATCEA` with no argument turns extended addressing off, and `ATPPS` only reads.

The result is a capability set the session degrades against: without `ATCRA` it filters
responses in software instead of asking the adapter to do it, and so on. Nothing fails
because a board is a clone; it just runs the slower way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional, Tuple

from .protocol import ElmProtocol

# Probe name -> the AT command that tests for it. Each one is a no-op on a chip that
# implements it, so probing never changes how the adapter behaves afterwards.
PROBES: Dict[str, str] = {
    "describe_protocol_number": "ATDPN",
    "adaptive_timing": "ATAT1",
    "flow_control_control": "ATFCSM0",
    "programmable_parameters": "ATPPS",
    "can_receive_filter": "ATCRA",
    "can_extended_addressing": "ATCEA",
}

# The firmware version each capability first appeared in. A board claiming a later
# version while failing the probe is reporting a version it does not have.
INTRODUCED_IN: Dict[str, float] = {
    "describe_protocol_number": 1.0,
    "programmable_parameters": 1.1,
    "flow_control_control": 1.1,
    "adaptive_timing": 1.2,
    "can_receive_filter": 1.3,
    "can_extended_addressing": 1.4,
}

_VERSION_PATTERN = re.compile(r"v\s*(\d+)\.(\d+)", re.IGNORECASE)


def parse_version(banner: str) -> Optional[float]:
    """
    Pull the version number out of an ATI banner.

    Returns None when the banner carries no recognisable version, which is itself a
    strong hint that the board is not what it claims.
    """
    match = _VERSION_PATTERN.search(banner or "")
    if not match:
        return None
    return float(f"{match.group(1)}.{match.group(2)}")


@dataclass(frozen=True)
class ElmCapabilities:
    """What the adapter reported about itself, and what it actually answered to."""

    reported_banner: str = ""
    reported_version: Optional[float] = None
    device_description: str = ""
    supported: FrozenSet[str] = field(default_factory=frozenset)

    def has(self, capability: str) -> bool:
        """Whether a probed capability is present."""
        return capability in self.supported

    @property
    def missing_for_reported_version(self) -> Tuple[str, ...]:
        """Capabilities the reported version should have but the board does not."""
        if self.reported_version is None:
            return ()
        return tuple(
            sorted(
                name
                for name, introduced in INTRODUCED_IN.items()
                if self.reported_version >= introduced and name not in self.supported
            )
        )

    @property
    def effective_version(self) -> Optional[float]:
        """
        The highest version the board actually behaves like.

        A board missing a v1.3 command is treated as v1.2 whatever its banner says, so
        that decisions elsewhere are made on evidence rather than on a claim.
        """
        if self.reported_version is None:
            return None
        capped = self.reported_version
        for name, introduced in sorted(INTRODUCED_IN.items(), key=lambda item: item[1]):
            if name not in self.supported and introduced <= capped:
                capped = min(capped, round(introduced - 0.1, 1))
        return capped

    @property
    def is_probable_clone(self) -> bool:
        """Whether the firmware claims a version it does not live up to."""
        return bool(self.missing_for_reported_version)

    @property
    def notes(self) -> Tuple[str, ...]:
        """Human-readable findings, for the status bar and the MCP status tool."""
        notes = []
        if self.reported_version is None:
            notes.append(f"the adapter did not report a recognisable version ({self.reported_banner!r})")
        if self.is_probable_clone:
            missing = ", ".join(self.missing_for_reported_version)
            notes.append(
                f"reports v{self.reported_version:g} but does not implement {missing} — "
                f"treating it as v{self.effective_version:g}"
            )
        if not self.has("can_receive_filter"):
            notes.append("no CAN receive filter: responses are filtered in software instead")
        return tuple(notes)

    def __str__(self) -> str:
        version = f"v{self.reported_version:g}" if self.reported_version else "unknown version"
        suffix = " (probable clone)" if self.is_probable_clone else ""
        return f"ELM327 {version}{suffix}"


def probe_capabilities(protocol: ElmProtocol, timeout: Optional[float] = None) -> ElmCapabilities:
    """
    Ask an adapter what it is, then check each claim by trying the commands.

    Args:
        protocol: An open command dialogue, past the reset and with echo off.
        timeout: Seconds to allow each probe, defaulting to the protocol's own timeout.

    Returns:
        The capability set. A probe that answers anything other than '?' counts as
        supported: a status word means the command exists but the bus is unhappy, which
        says nothing about the firmware.
    """
    banner = protocol.send("ATI", timeout=timeout).value
    description = protocol.send("AT@1", timeout=timeout).value

    supported = set()
    for name, command in PROBES.items():
        reply = protocol.send(command, timeout=timeout)
        if not reply.is_unsupported:
            supported.add(name)

    return ElmCapabilities(
        reported_banner=banner,
        reported_version=parse_version(banner),
        device_description=description,
        supported=frozenset(supported),
    )
