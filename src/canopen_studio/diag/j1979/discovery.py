"""
Finding out which PIDs a vehicle actually implements.

There is no list to look up. A vehicle declares its own capabilities through PIDs that
return a bitmask: PID 0x00 says which of 0x01..0x20 are supported, PID 0x20 which of
0x21..0x40, and so on in blocks of thirty-two. The last bit of each block says whether
the next block's PID exists at all, so the walk ends where the vehicle says it ends.

Nothing here is hard-coded, which matters twice over. A profile that guessed would claim
readings a car cannot give, and a car that supports a PID the shipped table does not
describe still shows up — as a PID with no decoding rather than as nothing.

Support is tracked per ECU. On a functional request the engine controller, the
transmission and the hybrid controller each answer with their own bitmask, and knowing
which one implements a PID is what lets a later read be addressed to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Mapping, Optional, Tuple

from ..interface import DiagnosticInterface

# The PIDs that carry a support bitmask, each covering the thirty-two that follow it.
SUPPORT_PIDS: Tuple[int, ...] = (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0)

# A bitmask covers this many PIDs, and occupies this many bytes.
BLOCK_SIZE = 0x20
BITMASK_BYTES = 4


def decode_support_bitmask(base_pid: int, data: bytes) -> List[int]:
    """
    Read a support bitmask into the list of PIDs it declares.

    The most significant bit of the first byte is the PID just after `base_pid`, and the
    least significant bit of the fourth byte is `base_pid + 0x20` — which is itself the
    next bitmask PID, so it doubles as "there is more to ask for".

    Args:
        base_pid: The PID that returned this bitmask, e.g. 0x00 or 0x20.
        data: The four bitmask bytes.
    """
    if len(data) < BITMASK_BYTES:
        raise ValueError(f"a support bitmask is {BITMASK_BYTES} bytes, got {len(data)}")

    value = int.from_bytes(data[:BITMASK_BYTES], "big")
    return [base_pid + 1 + index for index in range(BLOCK_SIZE) if value & (1 << (BLOCK_SIZE - 1 - index))]


@dataclass(frozen=True)
class SupportedPids:
    """Which PIDs of one mode each ECU implements."""

    mode: int
    by_ecu: Mapping[int, FrozenSet[int]]

    @property
    def all(self) -> FrozenSet[int]:
        """Every PID at least one ECU implements."""
        result: FrozenSet[int] = frozenset()
        for pids in self.by_ecu.values():
            result |= pids
        return result

    @property
    def ecus(self) -> Tuple[int, ...]:
        """The ECUs that answered, in address order."""
        return tuple(sorted(self.by_ecu))

    def supported_by(self, pid: int) -> Tuple[int, ...]:
        """The ECUs implementing one PID, in address order."""
        return tuple(sorted(ecu for ecu, pids in self.by_ecu.items() if pid in pids))

    def __contains__(self, pid: object) -> bool:
        return isinstance(pid, int) and pid in self.all

    def __len__(self) -> int:
        return len(self.all)

    def __iter__(self):
        return iter(sorted(self.all))

    def as_dict(self) -> Dict[str, object]:
        """A JSON-friendly rendering, for the MCP tools and the GUI."""
        return {
            "mode": f"{self.mode:02X}",
            "count": len(self.all),
            "pids": [f"{pid:02X}" for pid in sorted(self.all)],
            "by_ecu": {f"0x{ecu:X}": [f"{pid:02X}" for pid in sorted(pids)] for ecu, pids in self.by_ecu.items()},
        }


def discover_supported_pids(
    interface: DiagnosticInterface,
    mode: int = 0x01,
    timeout: Optional[float] = None,
    max_blocks: int = len(SUPPORT_PIDS),
) -> SupportedPids:
    """
    Walk a vehicle's support bitmasks and report what it implements.

    Args:
        interface: An open diagnostic session, either backend.
        mode: The service to enumerate. 0x01 for live data, 0x09 for vehicle information.
        timeout: Seconds to allow each request.
        max_blocks: How many bitmask blocks to walk at most, bounding the scan on a
            vehicle whose "there is more" bit is stuck on.

    Returns:
        The supported PIDs, per ECU. Empty when nothing answered, which is how an
        ignition that is off, or a mode the vehicle does not implement, presents.
    """
    found: Dict[int, set] = {}

    for base in SUPPORT_PIDS[:max_blocks]:
        replies = interface.positive_responses(mode, base, timeout=timeout)
        if not replies:
            # No ECU implements this bitmask PID, so there is nothing beyond it either.
            break

        continues = False
        for reply in replies:
            payload = reply.payload
            # The payload opens with the PID echo; a reply to a different PID is a late
            # answer to an earlier request and must not be folded in.
            if len(payload) < 1 + BITMASK_BYTES or payload[0] != base:
                continue
            try:
                pids = decode_support_bitmask(base, payload[1 : 1 + BITMASK_BYTES])
            except ValueError:
                continue

            bucket = found.setdefault(reply.source, set())
            bucket.add(base)
            bucket.update(pids)
            if base + BLOCK_SIZE in pids:
                continues = True

        if not continues:
            break

    return SupportedPids(mode=mode, by_ecu={ecu: frozenset(pids) for ecu, pids in found.items()})
