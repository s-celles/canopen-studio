"""
Importing PID definitions from a DBC database, via cantools.

A word about what this can and cannot do, because the two formats do not describe the
same thing. A DBC describes signals broadcast on a bus: a fixed identifier, a fixed
layout, always present. A diagnostic PID is requested and answered, and its layout
depends on which PID was asked for. The two overlap in exactly one place — a DBC that
models an OBD-II response as a *multiplexed* message, where the multiplexer byte is the
PID echo and each multiplexed signal is one parameter.

So that is what is imported. Messages whose identifier falls in the diagnostic response
range and which carry a multiplexer are read as PID definitions; the multiplexer value
becomes the PID and each signal under it becomes a decoding formula.

Real OBD-II databases use DBC *extended* multiplexing, where a signal names the
multiplexer that selects it. That makes the file self-describing, so nothing about the
frame layout is assumed: the service comes from the multiplexer chain above the PID
signal, and the first data byte is the one after the PID echo wherever the database puts
it. A database that models the ISO-TP length byte and one that does not both import
correctly, which guessing a fixed offset would not achieve.

Everything else in the file is reported as skipped, with the reason. An ordinary
broadcast DBC produces an empty profile and a list saying why, rather than a profile full
of parameters that would be requested as PIDs and never answered.

cantools is an optional dependency, installed with `canopen-studio[dbc]`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Union

from ...j1979.pids import format_key
from ...isotp import ECU_COUNT_11BIT, RESPONSE_BASE_11BIT
from ..model import Profile, ProfileError
from .torque_csv import ImportReport, normalise_name

# The byte letters a formula addresses, A being the first byte after the PID echo.
BYTE_NAMES = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _require_cantools():
    """Import cantools, explaining how to get it rather than failing obscurely."""
    try:
        import cantools
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ProfileError(
            "importing a DBC needs cantools, which is an optional dependency. "
            "Install it with: uv pip install 'canopen-studio[dbc]'"
        ) from exc
    return cantools


def is_diagnostic_response_id(frame_id: int) -> bool:
    """Whether a DBC frame identifier is one an ECU answers diagnostics on."""
    if RESPONSE_BASE_11BIT <= frame_id < RESPONSE_BASE_11BIT + ECU_COUNT_11BIT:
        return True
    # 29-bit diagnostic responses, 0x18DAF1xx, with or without the priority bits set.
    return (frame_id >> 8) == 0x18DAF1


def signal_formula(start_byte: int, length_bits: int, scale: float, offset: float, is_signed: bool) -> Optional[str]:
    """
    Build a decoding formula for a byte-aligned big-endian signal.

    Returns None for a signal this cannot express. Bit-packed and little-endian layouts
    are refused rather than approximated: a formula that is nearly right would decode
    into a plausible wrong number, which is the failure mode this whole layer exists to
    avoid.
    """
    if length_bits % 8 or length_bits == 0:
        return None
    width = length_bits // 8
    if start_byte < 0 or start_byte + width > len(BYTE_NAMES):
        return None

    letters = BYTE_NAMES[start_byte : start_byte + width]
    terms = [
        f"{letter} * {256 ** (width - index - 1)}" if index < width - 1 else letter
        for index, letter in enumerate(letters)
    ]
    raw = " + ".join(terms)
    if width > 1:
        raw = f"({raw})"

    if is_signed:
        raw = f"signed({raw}, {length_bits})"

    expression = raw
    if scale != 1:
        expression = f"{expression} * {scale}"
    if offset:
        expression = f"{expression} + {offset}" if offset > 0 else f"{expression} - {abs(offset)}"
    return expression


def import_dbc(
    source: Union[str, Path],
    profile_id: str,
    name: Optional[str] = None,
    extends: Optional[str] = None,
    mode: int = 0x01,
) -> ImportReport:
    """
    Build a profile from the diagnostic messages of a DBC database.

    Args:
        source: The `.dbc` file.
        profile_id: Identifier for the profile this produces.
        name: Display name, defaulting to the identifier.
        extends: Profile to inherit from, defaulting to generic J1979.
        mode: The service the imported PIDs belong to, 0x01 by default.

    Returns:
        An `ImportReport` carrying the profile and everything that was skipped, with the
        reason for each. A DBC of ordinary broadcast messages imports nothing and says
        so, which is the honest outcome rather than an error.

    Raises:
        ProfileError: If cantools is not installed, or the file cannot be read.
    """
    cantools = _require_cantools()

    try:
        database = cantools.database.load_file(str(source))
    except FileNotFoundError as exc:
        raise ProfileError(f"cannot read {source}: {exc}") from exc
    except Exception as exc:
        raise ProfileError(f"{source} is not a database cantools can read: {exc}") from exc

    report = ImportReport()
    entries: Dict[str, Dict[str, Any]] = {}

    for message in getattr(database, "messages", []):
        if not is_diagnostic_response_id(message.frame_id):
            report.skipped.append(
                f"{message.name}: identifier 0x{message.frame_id:X} is not a diagnostic response, "
                "so its signals are broadcast rather than requested"
            )
            continue

        by_name = {signal.name: signal for signal in getattr(message, "signals", [])}
        data_signals = [signal for signal in by_name.values() if getattr(signal, "multiplexer_ids", None)]
        if not data_signals:
            report.skipped.append(f"{message.name}: no multiplexed signals, so it carries no PID layout")
            continue

        for signal in data_signals:
            if getattr(signal, "is_multiplexer", False):
                # A selector, not a parameter: this is the PID echo itself, which says
                # which signal applies rather than carrying a reading of its own.
                continue

            selector = by_name.get(getattr(signal, "multiplexer_signal", None) or "")
            if selector is None or not getattr(selector, "is_multiplexer", False):
                report.skipped.append(f"{message.name}.{signal.name}: names a multiplexer the database does not define")
                continue

            entry = _signal_entry(signal, selector, message, report)
            if entry is None:
                continue

            signal_mode = _mode_of(selector, by_name, default=mode)
            for pid_value in signal.multiplexer_ids:
                entries[format_key(signal_mode, int(pid_value))] = entry
                report.imported += 1

    document: Dict[str, Any] = {
        "id": profile_id,
        "name": name or profile_id,
        "description": f"Imported from the diagnostic messages of {Path(source).name}.",
        "pids": entries,
    }
    if extends is not None:
        document["extends"] = extends

    report.profile = Profile.from_mapping(document)
    return report


def _mode_of(selector, by_name: Dict[str, Any], default: int) -> int:
    """
    The service a PID selector belongs to, read from the multiplexer above it.

    An OBD-II database nests the multiplexing: the service selects which PID signal
    applies, and the PID signal selects which parameter applies. When a file does not
    model the service, the caller's default is used.
    """
    ids = getattr(selector, "multiplexer_ids", None)
    if ids:
        return int(ids[0])
    parent = by_name.get(getattr(selector, "multiplexer_signal", None) or "")
    if parent is not None and getattr(parent, "multiplexer_ids", None):
        return int(parent.multiplexer_ids[0])
    return default


def _signal_entry(signal, selector, message, report: ImportReport) -> Optional[Dict[str, Any]]:
    """Turn one multiplexed signal into a PID entry, or explain why it cannot be."""
    byte_order = getattr(signal, "byte_order", "big_endian")
    if byte_order != "big_endian":
        report.skipped.append(f"{message.name}.{signal.name}: little-endian layouts are not imported")
        return None

    # cantools counts big-endian start bits from the most significant bit of byte 0, so
    # the byte a signal begins in is its start bit divided by eight. The data a formula
    # addresses starts just after the PID echo, wherever the database places it.
    first_data_byte = (int(selector.start) // 8) + 1
    start_byte = (int(signal.start) // 8) - first_data_byte
    formula = signal_formula(
        start_byte=start_byte,
        length_bits=int(signal.length),
        scale=float(getattr(signal, "scale", 1) or 1),
        offset=float(getattr(signal, "offset", 0) or 0),
        is_signed=bool(getattr(signal, "is_signed", False)),
    )
    if formula is None:
        report.skipped.append(
            f"{message.name}.{signal.name}: {signal.length} bits at offset {signal.start} is not byte-aligned, "
            "and an approximate formula would decode to a plausible wrong number"
        )
        return None

    entry: Dict[str, Any] = {
        "name": normalise_name(signal.name, fallback=f"signal_{signal.start}"),
        "description": getattr(signal, "comment", None) or signal.name,
        "formula": formula,
        "bytes": int(signal.length) // 8,
    }
    unit = getattr(signal, "unit", None)
    if unit:
        entry["unit"] = str(unit)
    if getattr(signal, "minimum", None) is not None:
        entry["min"] = float(signal.minimum)
    if getattr(signal, "maximum", None) is not None:
        entry["max"] = float(signal.maximum)
    return entry
