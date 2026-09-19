"""
PID definitions and the values they decode to.

A definition says how to turn the bytes after a PID echo into something meaningful, and
comes from a data file rather than from code. Five shapes cover J1979 and everything a
manufacturer profile has needed so far:

formula
    Arithmetic over the data bytes. The common case.
enum
    A lookup on the first byte: fuel system status, fuel type, OBD standard.
bits
    Named flags, addressed as `A.0` — byte letter, then bit index from the least
    significant. Oxygen sensors present, auxiliary input status.
ascii
    Text: the VIN, a calibration identifier, an ECU name.
raw
    Bytes kept as they came, for the PIDs whose meaning is a structure of its own.

A definition that declares none of these decodes to raw bytes rather than failing, so an
imported file listing a PID it cannot describe still contributes the fact that the PID
exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from .formula import Formula, FormulaError

# Data bytes are addressed by letter, matching the formula notation.
BYTE_NAMES = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# Modes defined by SAE J1979.
MODE_CURRENT_DATA = 0x01
MODE_FREEZE_FRAME = 0x02
MODE_STORED_DTC = 0x03
MODE_CLEAR_DTC = 0x04
MODE_O2_MONITORING = 0x05
MODE_MONITOR_RESULTS = 0x06
MODE_PENDING_DTC = 0x07
MODE_CONTROL = 0x08
MODE_VEHICLE_INFO = 0x09
MODE_PERMANENT_DTC = 0x0A

MODE_NAMES = {
    MODE_CURRENT_DATA: "Current data",
    MODE_FREEZE_FRAME: "Freeze frame data",
    MODE_STORED_DTC: "Stored diagnostic trouble codes",
    MODE_CLEAR_DTC: "Clear trouble codes and stored values",
    MODE_O2_MONITORING: "Oxygen sensor monitoring test results",
    MODE_MONITOR_RESULTS: "On-board monitoring test results",
    MODE_PENDING_DTC: "Pending diagnostic trouble codes",
    MODE_CONTROL: "Control of on-board system or component",
    MODE_VEHICLE_INFO: "Vehicle information",
    MODE_PERMANENT_DTC: "Permanent diagnostic trouble codes",
}


class PidError(ValueError):
    """A PID definition is malformed, or could not decode a response."""


def parse_key(key: str) -> Tuple[int, int]:
    """
    Read a `mode:pid` key such as `01:0C` into its two numbers.

    Both halves are hexadecimal, which is how every OBD-II reference writes them.
    """
    text = str(key).strip()
    if ":" not in text:
        raise PidError(f"{key!r} is not a mode:pid key, e.g. '01:0C'")
    mode_text, pid_text = text.split(":", 1)
    try:
        return int(mode_text, 16), int(pid_text, 16)
    except ValueError:
        raise PidError(f"{key!r} is not a pair of hexadecimal numbers") from None


def format_key(mode: int, pid: int) -> str:
    """Render a mode and PID the way a definition file writes them."""
    return f"{mode:02X}:{pid:02X}"


def _parse_bit_reference(reference: str) -> Tuple[int, int]:
    """Read a bit reference such as `A.0` into a byte index and a bit index."""
    text = str(reference).strip().upper()
    if "." not in text:
        raise PidError(f"{reference!r} is not a bit reference, e.g. 'A.0'")
    letter, index = text.split(".", 1)
    if letter not in BYTE_NAMES or len(letter) != 1:
        raise PidError(f"{reference!r} does not start with a data byte letter")
    try:
        bit_index = int(index)
    except ValueError:
        raise PidError(f"{reference!r} does not end with a bit number") from None
    if not 0 <= bit_index <= 7:
        raise PidError(f"{reference!r} refers to bit {bit_index}, outside 0..7")
    return BYTE_NAMES.index(letter), bit_index


@dataclass(frozen=True)
class PidValue:
    """One decoded reading, with enough context to be rendered or serialised."""

    definition: "PidDefinition"
    raw: bytes
    value: Any
    source: Optional[int] = None

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def unit(self) -> str:
        return self.definition.unit

    @property
    def mode(self) -> int:
        return self.definition.mode

    @property
    def pid(self) -> int:
        return self.definition.pid

    def as_dict(self) -> Dict[str, Any]:
        """A JSON-friendly rendering, for the MCP tools and the GUI."""
        payload: Dict[str, Any] = {
            "pid": self.definition.key,
            "name": self.name,
            "description": self.definition.description,
            "value": self.value if not isinstance(self.value, (bytes, bytearray)) else self.value.hex().upper(),
            "unit": self.unit,
            "raw": self.raw.hex(" ").upper(),
        }
        if self.source is not None:
            payload["ecu"] = f"0x{self.source:X}"
        return payload

    def __str__(self) -> str:
        if isinstance(self.value, (bytes, bytearray)):
            rendered = self.value.hex(" ").upper()
        elif isinstance(self.value, float):
            rendered = f"{self.value:g}"
        else:
            rendered = str(self.value)
        return f"{self.name} = {rendered}{' ' + self.unit if self.unit else ''}"


@dataclass(frozen=True)
class PidDefinition:
    """
    How to decode one PID.

    Args:
        mode: The service the PID belongs to, e.g. 0x01.
        pid: The parameter identifier within that mode.
        name: A stable snake_case key, used by the MCP tools and the plotter.
        description: The wording of the standard, for display.
        unit: Physical unit of the decoded value, empty when it has none.
        length: Expected number of data bytes, when the standard fixes one.
        formula: Arithmetic over the data bytes.
        values: Lookup on the first data byte, for an enumerated PID.
        bits: Named flags, keyed by a `A.0` style reference.
        ascii: Whether the data is text.
        minimum, maximum: Range of the decoded value, for plotting and for sanity checks.
        origin: Which profile contributed this definition, for diagnosis.
    """

    mode: int
    pid: int
    name: str
    description: str = ""
    unit: str = ""
    length: Optional[int] = None
    formula: Optional[Formula] = None
    values: Optional[Mapping[int, str]] = None
    bits: Optional[Mapping[str, str]] = None
    ascii: bool = False
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    origin: str = ""

    @property
    def key(self) -> str:
        """The `mode:pid` key this definition is filed under."""
        return format_key(self.mode, self.pid)

    @property
    def kind(self) -> str:
        """Which of the five decoding shapes this definition uses."""
        if self.ascii:
            return "ascii"
        if self.values is not None:
            return "enum"
        if self.bits is not None:
            return "bits"
        if self.formula is not None:
            return "formula"
        return "raw"

    def decode(self, data: bytes, source: Optional[int] = None) -> PidValue:
        """
        Decode the bytes that followed this PID's echo.

        Args:
            data: The data bytes, A being the first of them.
            source: The ECU that answered, carried through to the value.

        Raises:
            PidError: If the response is too short for the declared decoding.
        """
        data = bytes(data)
        kind = self.kind

        if kind == "ascii":
            value: Any = self._decode_ascii(data)
        elif kind == "enum":
            value = self._decode_enum(data)
        elif kind == "bits":
            value = self._decode_bits(data)
        elif kind == "formula":
            try:
                value = self.formula(data)  # type: ignore[misc]
            except FormulaError as exc:
                raise PidError(f"{self.key} ({self.name}): {exc}") from exc
        else:
            value = data

        return PidValue(definition=self, raw=data, value=value, source=source)

    @staticmethod
    def _decode_ascii(data: bytes) -> str:
        """
        Read text out of a response.

        ECUs pad with NUL or with 0x20, and some prefix the field with zero bytes before
        the text begins, so anything outside printable ASCII is dropped rather than
        rendered as an escape.
        """
        return "".join(chr(byte) for byte in data if 0x20 <= byte < 0x7F).strip()

    def _decode_enum(self, data: bytes) -> str:
        if not data:
            raise PidError(f"{self.key} ({self.name}): an enumerated PID needs at least one byte")
        assert self.values is not None
        return self.values.get(data[0], f"Unknown value 0x{data[0]:02X}")

    def _decode_bits(self, data: bytes) -> Dict[str, bool]:
        assert self.bits is not None
        flags = {}
        for reference, label in self.bits.items():
            byte_index, bit_index = _parse_bit_reference(reference)
            if byte_index >= len(data):
                raise PidError(
                    f"{self.key} ({self.name}): flag {label!r} needs data byte "
                    f"{BYTE_NAMES[byte_index]} but the response carried {len(data)}"
                )
            flags[label] = bool((data[byte_index] >> bit_index) & 1)
        return flags

    @classmethod
    def from_mapping(cls, key: str, entry: Mapping[str, Any], origin: str = "") -> "PidDefinition":
        """
        Build a definition from one entry of a profile file.

        Args:
            key: The `mode:pid` key the entry is filed under.
            entry: The entry's fields.
            origin: Which profile the entry came from, for diagnosis.
        """
        if not isinstance(entry, Mapping):
            raise PidError(f"{key}: a PID entry must be a mapping, got {type(entry).__name__}")

        mode, pid = parse_key(key)
        name = str(entry.get("name") or "").strip()
        if not name:
            raise PidError(f"{key}: a PID entry needs a name")

        formula_text = entry.get("formula")
        try:
            formula = Formula(formula_text) if formula_text else None
        except FormulaError as exc:
            raise PidError(f"{key} ({name}): {exc}") from exc

        values = entry.get("values")
        if values is not None:
            values = {int(k): str(v) for k, v in dict(values).items()}

        bits = entry.get("bits")
        if bits is not None:
            bits = {str(k): str(v) for k, v in dict(bits).items()}
            for reference in bits:
                _parse_bit_reference(reference)

        return cls(
            mode=mode,
            pid=pid,
            name=name,
            description=str(entry.get("description") or ""),
            unit=str(entry.get("unit") or ""),
            length=int(entry["bytes"]) if entry.get("bytes") is not None else None,
            formula=formula,
            values=values,
            bits=bits,
            ascii=bool(entry.get("ascii", False)),
            minimum=float(entry["min"]) if entry.get("min") is not None else None,
            maximum=float(entry["max"]) if entry.get("max") is not None else None,
            origin=origin,
        )

    def with_origin(self, origin: str) -> "PidDefinition":
        """A copy attributed to a different profile."""
        return replace(self, origin=origin)

    def __str__(self) -> str:
        unit = f" [{self.unit}]" if self.unit else ""
        return f"{self.key} {self.name}{unit}"


@dataclass
class PidTable:
    """
    A set of PID definitions, keyed by mode and PID.

    Profiles are layered by merging tables: a table merged over another replaces the
    entries it redefines and keeps the rest, which is exactly what profile inheritance
    needs.
    """

    definitions: Dict[Tuple[int, int], PidDefinition] = field(default_factory=dict)

    def add(self, definition: PidDefinition) -> None:
        """Insert a definition, replacing any it shadows."""
        self.definitions[(definition.mode, definition.pid)] = definition

    def get(self, mode: int, pid: int) -> Optional[PidDefinition]:
        """The definition for one PID, or None when the table does not describe it."""
        return self.definitions.get((mode, pid))

    def by_name(self, name: str) -> Optional[PidDefinition]:
        """The definition carrying a given name, or None."""
        for definition in self.definitions.values():
            if definition.name == name:
                return definition
        return None

    def pids_for_mode(self, mode: int) -> List[PidDefinition]:
        """Every definition of one mode, in PID order."""
        return sorted(
            (d for d in self.definitions.values() if d.mode == mode),
            key=lambda d: d.pid,
        )

    def modes(self) -> List[int]:
        """The modes this table describes, in order."""
        return sorted({definition.mode for definition in self.definitions.values()})

    def merged_with(self, other: "PidTable") -> "PidTable":
        """A new table with `other` layered on top, its entries winning on conflict."""
        merged = dict(self.definitions)
        merged.update(other.definitions)
        return PidTable(merged)

    @classmethod
    def from_mapping(cls, entries: Mapping[str, Any], origin: str = "") -> "PidTable":
        """Build a table from the `pids` section of a profile file."""
        table = cls()
        for key, entry in dict(entries or {}).items():
            table.add(PidDefinition.from_mapping(key, entry, origin=origin))
        return table

    def __contains__(self, key: object) -> bool:
        return tuple(key) in self.definitions if isinstance(key, Sequence) else False

    def __iter__(self) -> Iterator[PidDefinition]:
        return iter(sorted(self.definitions.values(), key=lambda d: (d.mode, d.pid)))

    def __len__(self) -> int:
        return len(self.definitions)
