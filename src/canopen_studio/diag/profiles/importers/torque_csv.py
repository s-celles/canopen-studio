"""
Importing custom PID definitions from a Torque Pro CSV export.

Torque is the Android OBD-II application whose custom-PID files people actually have, so
importing one is the quickest route from "somebody worked this PID out" to a profile that
decodes it here.

The formats line up almost exactly. Torque's equations address data bytes as A, B, C…,
which is the notation `canopen_studio.diag.j1979.formula` already interprets, so an
equation transfers verbatim. Only one construct needs translating: Torque writes bit
extraction as `{A:3}`, which becomes `bit(A, 3)`.

Nothing imported is trusted. Every equation goes through the formula interpreter, which
refuses anything that is not arithmetic, and a row whose equation will not parse is
reported rather than silently dropped — an imported PID that decodes to nonsense is worse
than one that is missing.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from ...j1979.formula import Formula, FormulaError
from ...j1979.pids import PidError, format_key
from ..model import Profile, ProfileError

# Column headings Torque writes, and the aliases seen in files people share. Matching is
# case-insensitive and ignores spaces and underscores.
COLUMN_ALIASES: Dict[str, Sequence[str]] = {
    "name": ("name", "longname", "fullname"),
    "short_name": ("shortname", "short"),
    "mode_and_pid": ("modeandpid", "pid", "obd2pid", "modepid"),
    "equation": ("equation", "formula"),
    "minimum": ("minvalue", "min", "minimum"),
    "maximum": ("maxvalue", "max", "maximum"),
    "unit": ("units", "unit"),
    "header": ("header", "canheader", "obdheader"),
}

# Torque writes bit extraction as {A:3}; the formula interpreter spells it bit(A, 3).
_BIT_PATTERN = re.compile(r"\{\s*([A-Z])\s*:\s*(\d+)\s*\}")

# A name that is not usable as an identifier becomes one, so that readings can be
# addressed by name and plotted.
_NON_IDENTIFIER = re.compile(r"[^0-9a-z]+")


@dataclass
class ImportReport:
    """What an import produced, and what it could not use."""

    profile: Optional[Profile] = None
    imported: int = 0
    skipped: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether anything at all was imported."""
        return self.imported > 0

    def as_dict(self) -> Dict[str, Any]:
        """A JSON-friendly rendering, for the MCP tools and the GUI."""
        return {
            "profile": self.profile.id if self.profile else None,
            "imported": self.imported,
            "skipped": list(self.skipped),
        }

    def __str__(self) -> str:
        suffix = f", {len(self.skipped)} skipped" if self.skipped else ""
        return f"{self.imported} PID(s) imported{suffix}"


def translate_equation(equation: str) -> str:
    """
    Turn a Torque equation into one the formula interpreter accepts.

    Only the bit-extraction syntax differs; everything else is already common notation.
    """
    text = str(equation).strip()
    return _BIT_PATTERN.sub(r"bit(\1, \2)", text)


def normalise_name(name: str, fallback: str) -> str:
    """Turn a display name into a stable snake_case key."""
    slug = _NON_IDENTIFIER.sub("_", str(name).strip().lower()).strip("_")
    if not slug or slug[0].isdigit():
        slug = f"pid_{slug}" if slug else fallback
    return slug


def parse_mode_and_pid(value: str) -> tuple:
    """
    Read Torque's combined mode-and-PID field, e.g. `0105` or `221E1B`.

    Returns:
        The mode and the PID. A PID wider than one byte — which manufacturer modes such
        as 0x22 use — is kept whole, because it addresses one parameter either way.
    """
    text = str(value).strip().upper().replace("0X", "")
    if len(text) < 4 or not all(character in "0123456789ABCDEF" for character in text):
        raise PidError(f"{value!r} is not a mode and PID such as '0105'")
    return int(text[:2], 16), int(text[2:], 16)


def _column_map(fieldnames: Optional[Sequence[str]]) -> Dict[str, str]:
    """Map our field names to the headings this particular file uses."""
    if not fieldnames:
        return {}
    lookup = {}
    for heading in fieldnames:
        key = re.sub(r"[\s_]+", "", str(heading or "")).lower()
        for field_name, aliases in COLUMN_ALIASES.items():
            if key in aliases and field_name not in lookup:
                lookup[field_name] = heading
    return lookup


def import_torque_csv(
    source: Union[str, Path, io.TextIOBase],
    profile_id: str,
    name: Optional[str] = None,
    extends: Optional[str] = None,
) -> ImportReport:
    """
    Build a profile from a Torque Pro custom-PID CSV.

    Args:
        source: The CSV file, by path or as an open text stream.
        profile_id: Identifier for the profile this produces.
        name: Display name, defaulting to the identifier.
        extends: Profile to inherit from, defaulting to generic J1979 so that the
            legislated parameters remain available alongside the imported ones.

    Returns:
        An `ImportReport` carrying the profile and the rows that could not be used.

    Raises:
        ProfileError: If the file cannot be read, or has no recognisable columns.
    """
    if isinstance(source, (str, Path)):
        try:
            text = Path(source).read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ProfileError(f"cannot read {source}: {exc}") from exc
        stream: io.TextIOBase = io.StringIO(text)
    else:
        stream = source

    reader = csv.DictReader(stream)
    columns = _column_map(reader.fieldnames)
    if "mode_and_pid" not in columns or "equation" not in columns:
        raise ProfileError(
            "this does not look like a Torque custom-PID export: no mode/PID and equation columns "
            f"(found {', '.join(reader.fieldnames or ['nothing'])})"
        )

    report = ImportReport()
    entries: Dict[str, Dict[str, Any]] = {}

    for number, row in enumerate(reader, start=2):
        raw_pid = (row.get(columns["mode_and_pid"]) or "").strip()
        if not raw_pid:
            continue

        try:
            mode, pid = parse_mode_and_pid(raw_pid)
        except PidError as exc:
            report.skipped.append(f"row {number}: {exc}")
            continue

        equation = translate_equation(row.get(columns["equation"]) or "")
        if not equation:
            report.skipped.append(f"row {number}: no equation for {raw_pid}")
            continue
        try:
            Formula(equation)
        except FormulaError as exc:
            # A PID that decodes to nonsense is worse than one that is missing.
            report.skipped.append(f"row {number}: {exc}")
            continue

        display = (row.get(columns.get("name", "")) or "").strip()
        short = (row.get(columns.get("short_name", "")) or "").strip()
        key = format_key(mode, pid)
        entries[key] = {
            "name": normalise_name(short or display, fallback=f"pid_{mode:02x}_{pid:02x}"),
            "description": display or short,
            "unit": (row.get(columns.get("unit", "")) or "").strip(),
            "formula": equation,
            **_optional_bound(row, columns, "minimum", "min"),
            **_optional_bound(row, columns, "maximum", "max"),
        }
        report.imported += 1

    document: Dict[str, Any] = {
        "id": profile_id,
        "name": name or profile_id,
        "description": "Imported from a Torque Pro custom-PID export.",
        "pids": entries,
    }
    if extends is not None:
        document["extends"] = extends

    report.profile = Profile.from_mapping(document)
    return report


def _optional_bound(row: Dict[str, str], columns: Dict[str, str], field_name: str, key: str) -> Dict[str, float]:
    """Read a min or max column, ignoring the blanks and placeholders these files carry."""
    heading = columns.get(field_name)
    if not heading:
        return {}
    raw = (row.get(heading) or "").strip()
    if not raw:
        return {}
    try:
        return {key: float(raw)}
    except ValueError:
        return {}
