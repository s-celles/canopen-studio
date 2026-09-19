"""
Vehicle identification numbers: reading one, and reading what it says.

Mode 09 PID 02 returns seventeen characters, preceded by a count of data items. The field
is longer than a CAN frame, so it always arrives segmented — which is why both backends
reassemble before anything here is asked to parse.

A VIN is also the first input to profile resolution. The first three characters identify
the manufacturer and the region, the tenth encodes the model year, and the eleventh the
assembly plant. None of that is a guess: it is ISO 3779, and the parts that are ambiguous
are reported as ambiguous rather than resolved silently.

The model year is the one genuinely ambiguous field. Its code repeats on a thirty-year
cycle, so 'A' is both 1980 and 2010. ISO leaves the disambiguation to context; the
convention this follows is the North American one, where a letter in position seven marks
a vehicle of 2010 or later. A caller that knows better can say so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

VIN_LENGTH = 17

# I, O and Q never appear in a VIN: they are too easily confused with 1 and 0.
FORBIDDEN_CHARACTERS = frozenset("IOQ")

# Model year codes, in order, starting at 1980 and repeating every thirty years.
YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"
YEAR_CYCLE = len(YEAR_CODES)
FIRST_YEAR = 1980

# The first character of the world manufacturer identifier gives the region.
REGIONS: Tuple[Tuple[str, str], ...] = (
    ("ABCDEFGH", "Africa"),
    ("JKLMNPR", "Asia"),
    ("STUVWXYZ", "Europe"),
    ("12345", "North America"),
    ("67", "Oceania"),
    ("89", "South America"),
)

# Transliteration used by the North American check digit. I, O and Q are absent by
# construction, and the letters share values with digits in a fixed pattern.
_TRANSLITERATION: Dict[str, int] = {
    **{str(digit): digit for digit in range(10)},
    **{letter: value for letter, value in zip("ABCDEFGH", range(1, 9))},
    **{letter: value for letter, value in zip("JKLMN", range(1, 6))},
    "P": 7,
    "R": 9,
    **{letter: value for letter, value in zip("STUVWXYZ", range(2, 10))},
}

_CHECK_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_CHECK_POSITION = 8


class VinError(ValueError):
    """A vehicle identification number is malformed."""


def clean_vin(text: str) -> str:
    """Strip padding and case from a VIN as an ECU returned it."""
    return "".join(str(text).split()).upper()


def is_valid_vin(text: str, strict: bool = False) -> bool:
    """
    Whether a string could be a vehicle identification number.

    Args:
        text: The candidate.
        strict: Also require the North American check digit to match. Off by default
            because vehicles built outside North America are not required to carry one
            and frequently do not.
    """
    vin = clean_vin(text)
    if len(vin) != VIN_LENGTH:
        return False
    if not vin.isalnum():
        return False
    if set(vin) & FORBIDDEN_CHARACTERS:
        return False
    if strict:
        try:
            return vin[_CHECK_POSITION] == check_digit(vin)
        except VinError:
            return False
    return True


def check_digit(text: str) -> str:
    """
    Compute the North American check digit, which belongs in position nine.

    Raises:
        VinError: If the input is not seventeen usable characters.
    """
    vin = clean_vin(text)
    if len(vin) != VIN_LENGTH:
        raise VinError(f"a VIN is {VIN_LENGTH} characters, got {len(vin)}")
    total = 0
    for character, weight in zip(vin, _CHECK_WEIGHTS):
        value = _TRANSLITERATION.get(character)
        if value is None:
            raise VinError(f"{character!r} cannot appear in a VIN")
        total += value * weight
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def decode_model_year(code: str, later_cycle: bool = False) -> Optional[int]:
    """
    Decode the model year character.

    Args:
        code: The tenth character of the VIN.
        later_cycle: Whether the vehicle belongs to the 2010-2039 cycle rather than
            1980-2009. The codes are identical, so this cannot be derived from the
            character alone.

    Returns:
        The model year, or None when the character is not a year code.
    """
    character = str(code).strip().upper()
    if character not in YEAR_CODES:
        return None
    offset = YEAR_CODES.index(character)
    return FIRST_YEAR + offset + (YEAR_CYCLE if later_cycle else 0)


def region_of(wmi: str) -> str:
    """The manufacturing region named by a world manufacturer identifier."""
    if not wmi:
        return "Unknown"
    first = wmi[0].upper()
    for characters, name in REGIONS:
        if first in characters:
            return name
    return "Unknown"


@dataclass(frozen=True)
class VinInfo:
    """What a vehicle identification number says about the vehicle."""

    vin: str
    wmi: str
    vds: str
    vis: str
    region: str
    model_year: Optional[int]
    model_year_code: str
    model_year_is_ambiguous: bool
    plant_code: str
    serial: str
    check_digit_valid: Optional[bool]

    @property
    def alternate_model_year(self) -> Optional[int]:
        """
        The other year the code could mean, thirty years away.

        Present whenever the year was inferred rather than told, so that a caller — or a
        profile resolver — can consider both instead of trusting one.
        """
        if self.model_year is None:
            return None
        if not self.model_year_is_ambiguous:
            return None
        other = (
            self.model_year + YEAR_CYCLE if self.model_year < FIRST_YEAR + YEAR_CYCLE else self.model_year - YEAR_CYCLE
        )
        return other

    def as_dict(self) -> Dict[str, object]:
        """A JSON-friendly rendering, for the MCP tools and the GUI."""
        payload: Dict[str, object] = {
            "vin": self.vin,
            "wmi": self.wmi,
            "region": self.region,
            "model_year": self.model_year,
            "plant_code": self.plant_code,
            "serial": self.serial,
        }
        if self.model_year_is_ambiguous and self.alternate_model_year:
            payload["model_year_alternative"] = self.alternate_model_year
        if self.check_digit_valid is not None:
            payload["check_digit_valid"] = self.check_digit_valid
        return payload

    def __str__(self) -> str:
        year = str(self.model_year) if self.model_year else "unknown year"
        return f"{self.vin} ({self.wmi}, {self.region}, {year})"


def parse_vin(text: str, model_year: Optional[int] = None) -> VinInfo:
    """
    Break a vehicle identification number into its parts.

    Args:
        text: The seventeen characters, as returned by mode 09 PID 02.
        model_year: The model year, when it is known from elsewhere. Given one, the
            ambiguity of the year code is resolved rather than inferred.

    Raises:
        VinError: If the input is not a usable VIN.
    """
    vin = clean_vin(text)
    if not is_valid_vin(vin):
        raise VinError(f"{text!r} is not a valid VIN: expected {VIN_LENGTH} characters without I, O or Q")

    year_code = vin[9]
    ambiguous = False
    if model_year is not None:
        decoded_year: Optional[int] = int(model_year)
    else:
        # Position seven holds a letter on vehicles of the 2010-2039 cycle and a digit on
        # the earlier one. It is a North American convention rather than a rule, so the
        # result is flagged as inferred.
        later_cycle = vin[6].isalpha()
        decoded_year = decode_model_year(year_code, later_cycle=later_cycle)
        ambiguous = decoded_year is not None

    try:
        valid_check: Optional[bool] = vin[_CHECK_POSITION] == check_digit(vin)
    except VinError:
        valid_check = None

    return VinInfo(
        vin=vin,
        wmi=vin[0:3],
        vds=vin[3:9],
        vis=vin[9:17],
        region=region_of(vin[0:3]),
        model_year=decoded_year,
        model_year_code=year_code,
        model_year_is_ambiguous=ambiguous,
        plant_code=vin[10],
        serial=vin[11:17],
        check_digit_valid=valid_check,
    )


def extract_vin(payload: bytes) -> Optional[str]:
    """
    Pull the VIN out of a mode 09 PID 02 response.

    The payload opens with the PID echo and a count of data items, and some ECUs pad the
    text with leading NUL bytes. Everything outside printable ASCII is dropped and the
    result is only returned if it is the right length, so a partial reassembly comes back
    as None rather than as a short VIN nobody notices.

    Args:
        payload: Everything after the 0x49 response byte.
    """
    text = "".join(chr(byte) for byte in payload if 0x20 <= byte < 0x7F).strip()
    # Drop the PID echo and the item count when they survived as printable characters,
    # by keeping only the trailing seventeen.
    if len(text) < VIN_LENGTH:
        return None
    candidate = text[-VIN_LENGTH:]
    return candidate if is_valid_vin(candidate) else None
