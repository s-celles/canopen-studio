"""
Diagnostic trouble codes, per ISO 15031-6.

A code arrives as two bytes and is read as a letter and four digits. The top two bits
choose the system — P for powertrain, C for chassis, B for body, U for network — the next
two are the first digit, and the remaining twelve are three hexadecimal digits.

Three modes return codes in the same format: 03 for stored codes, 07 for codes pending
confirmation, and 0A for permanent codes, which are the ones a scan tool cannot clear.

The count byte is the awkward part. ISO 15031-5 has a CAN response open with the number
of codes, but the field is absent on enough real ECUs that assuming either way mis-reads
the other, and length parity alone does not settle it once an ECU pads the reply out to a
frame boundary. The count is accepted only when it accounts for the whole payload: the
codes it declares must fit, and whatever follows them must be padding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# The two top bits of a code select the system it belongs to.
SYSTEM_LETTERS = ("P", "C", "B", "U")

SYSTEM_NAMES: Dict[str, str] = {
    "P": "Powertrain",
    "C": "Chassis",
    "B": "Body",
    "U": "Network",
}

# The digit after the letter says who defined the code.
DEFINED_BY: Dict[int, str] = {
    0: "Generic (SAE/ISO)",
    1: "Manufacturer-specific",
    2: "Generic or manufacturer-specific, depending on the system",
    3: "Generic or manufacturer-specific, depending on the system",
}

# Modes returning trouble codes, and what the codes they return mean.
DTC_MODES: Dict[str, int] = {
    "stored": 0x03,
    "pending": 0x07,
    "permanent": 0x0A,
}

DTC_KIND_DESCRIPTIONS: Dict[str, str] = {
    "stored": "Confirmed fault, with the malfunction indicator lamp commanded on",
    "pending": "Fault seen once, awaiting confirmation on a second drive cycle",
    "permanent": "Confirmed fault that only the vehicle itself may clear",
}

# A pair of zero bytes is padding, never a code.
NO_CODE = 0x0000


class DtcError(ValueError):
    """A trouble code response could not be read."""


def decode_dtc(word: int) -> str:
    """
    Turn the two bytes of a trouble code into its printed form.

    Args:
        word: The sixteen-bit code, e.g. 0x0143.

    Returns:
        The code as written on a repair order, e.g. "P0143".
    """
    if not 0 <= word <= 0xFFFF:
        raise DtcError(f"0x{word:X} does not fit in a trouble code")
    letter = SYSTEM_LETTERS[(word >> 14) & 0x03]
    first = (word >> 12) & 0x03
    second = (word >> 8) & 0x0F
    return f"{letter}{first}{second:X}{word & 0xFF:02X}"


def encode_dtc(code: str) -> int:
    """
    Turn a printed trouble code back into its two bytes.

    The inverse of `decode_dtc`, used to look a code up in a description table and to
    check that decoding round-trips.
    """
    text = str(code).strip().upper()
    if len(text) != 5 or text[0] not in SYSTEM_LETTERS:
        raise DtcError(f"{code!r} is not a trouble code such as 'P0143'")
    try:
        digits = int(text[1:], 16)
    except ValueError:
        raise DtcError(f"{code!r} does not end in four hexadecimal digits") from None
    if not 0 <= digits <= 0x3FFF:
        raise DtcError(f"{code!r} has a first digit above 3, which no code uses")
    return (SYSTEM_LETTERS.index(text[0]) << 14) | digits


@dataclass(frozen=True)
class TroubleCode:
    """One fault an ECU is reporting."""

    code: str
    raw: int
    kind: str = "stored"
    source: Optional[int] = None
    description: str = ""

    @property
    def system(self) -> str:
        """Powertrain, Chassis, Body or Network."""
        return SYSTEM_NAMES.get(self.code[0], "Unknown")

    @property
    def defined_by(self) -> str:
        """Whether the standard or the manufacturer gives this code its meaning."""
        return DEFINED_BY.get(int(self.code[1]), "Unknown")

    @property
    def is_manufacturer_specific(self) -> bool:
        """
        Whether the meaning comes from the manufacturer rather than the standard.

        Only a first digit of 1 is manufacturer-specific outright. 2 and 3 depend on the
        system, so they are reported as ambiguous through `defined_by` rather than
        claimed either way here.
        """
        return self.code[1] == "1"

    def as_dict(self) -> Dict[str, object]:
        """A JSON-friendly rendering, for the MCP tools and the GUI."""
        payload: Dict[str, object] = {
            "code": self.code,
            "kind": self.kind,
            "system": self.system,
            "defined_by": self.defined_by,
            "raw": f"0x{self.raw:04X}",
        }
        if self.description:
            payload["description"] = self.description
        if self.source is not None:
            payload["ecu"] = f"0x{self.source:X}"
        return payload

    def __str__(self) -> str:
        suffix = f" — {self.description}" if self.description else ""
        return f"{self.code} ({self.system}){suffix}"


def parse_dtc_payload(payload: bytes) -> List[int]:
    """
    Read the codes out of the payload of a mode 03, 07 or 0A response.

    Args:
        payload: Everything after the response service byte.

    Returns:
        The codes, as sixteen-bit words, with padding dropped and order preserved.
    """
    data = bytes(payload)
    if not data:
        return []

    # Prefer the count interpretation, but only when it accounts for the whole payload:
    # the declared number of codes must fit, and whatever follows them must be padding.
    # Anything else means the leading byte was the first half of a code, and reading it
    # as a count would shift every code by one byte and print faults the vehicle never
    # reported.
    count = data[0]
    body = data[1:]
    if count * 2 <= len(body) and not any(body[count * 2 :]):
        data = body[: count * 2]

    words = [int.from_bytes(data[index : index + 2], "big") for index in range(0, len(data) - 1, 2)]
    return [word for word in words if word != NO_CODE]


def decode_dtc_response(
    payload: bytes,
    kind: str = "stored",
    source: Optional[int] = None,
    descriptions: Optional[Dict[str, str]] = None,
) -> List[TroubleCode]:
    """
    Turn one ECU's trouble code response into codes.

    Args:
        payload: Everything after the response service byte.
        kind: Which mode produced it — stored, pending or permanent.
        source: The ECU that answered.
        descriptions: Optional code-to-wording map, typically from a vehicle profile.
    """
    lookup = descriptions or {}
    codes = []
    for word in parse_dtc_payload(payload):
        code = decode_dtc(word)
        codes.append(
            TroubleCode(
                code=code,
                raw=word,
                kind=kind,
                source=source,
                description=lookup.get(code, ""),
            )
        )
    return codes


def summarise(codes: Iterable[TroubleCode]) -> Dict[str, object]:
    """
    Condense a set of codes into counts, for a status line or an agent's first look.

    Args:
        codes: The codes to summarise.
    """
    codes = list(codes)
    by_system: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    for code in codes:
        by_system[code.system] = by_system.get(code.system, 0) + 1
        by_kind[code.kind] = by_kind.get(code.kind, 0) + 1
    return {
        "total": len(codes),
        "by_kind": by_kind,
        "by_system": by_system,
        "codes": [code.code for code in codes],
    }


def sort_codes(codes: Sequence[TroubleCode]) -> Tuple[TroubleCode, ...]:
    """Order codes the way a scan tool lists them: by system, then numerically."""
    return tuple(sorted(codes, key=lambda code: (SYSTEM_LETTERS.index(code.code[0]), code.raw & 0x3FFF)))
