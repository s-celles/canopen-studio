"""
What a vehicle profile is, and how one inherits from another.

A profile is a data file: it names itself, optionally extends another, contributes PID
definitions and trouble code wordings, and says what a vehicle must look like for it to
apply. Nothing in it is executable — the only expressions it may contain are decoding
formulas, which `canopen_studio.diag.j1979.formula` interprets rather than runs.

Inheritance is the reason the format exists. The generic J1979 profile describes what
every compliant car has; a make adds what that manufacturer does across its range; a model
and year add what that car does specifically. Each layer states only its own difference,
and resolution merges them base-first so the most specific definition wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..j1979.pids import PidError, PidTable, parse_key

# The profile every other one ultimately extends, and the fallback that must always work.
BASE_PROFILE_ID = "j1979_base"


class ProfileError(ValueError):
    """A profile is malformed, or its inheritance cannot be resolved."""


@dataclass(frozen=True)
class MatchRules:
    """
    What a vehicle must look like for a profile to apply.

    Every rule is optional, and a profile with no rules at all never matches
    automatically — it can still be chosen by hand, which is how a profile under
    development is used before its rules are written.

    Args:
        wmi: World manufacturer identifiers this profile covers, e.g. ["1HG", "JHM"].
        vin_prefixes: Longer VIN prefixes, for a profile narrower than a whole
            manufacturer.
        year_from, year_to: Inclusive model year bounds.
        required_pids: `mode:pid` keys the vehicle must support.
        forbidden_pids: `mode:pid` keys the vehicle must not support, which is what
            separates two otherwise identical variants.
        ecu_name_contains: Text that must appear in an ECU name from mode 09 PID 0A.
    """

    wmi: Tuple[str, ...] = ()
    vin_prefixes: Tuple[str, ...] = ()
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    required_pids: Tuple[Tuple[int, int], ...] = ()
    forbidden_pids: Tuple[Tuple[int, int], ...] = ()
    ecu_name_contains: Tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Whether the profile states no rules, and so never matches automatically."""
        return not any(
            (
                self.wmi,
                self.vin_prefixes,
                self.year_from is not None,
                self.year_to is not None,
                self.required_pids,
                self.forbidden_pids,
                self.ecu_name_contains,
            )
        )

    @classmethod
    def from_mapping(cls, entry: Optional[Mapping[str, Any]]) -> "MatchRules":
        """Build rules from the `match` section of a profile file."""
        if not entry:
            return cls()
        if not isinstance(entry, Mapping):
            raise ProfileError(f"a match section must be a mapping, got {type(entry).__name__}")

        def as_tuple(key: str) -> Tuple[str, ...]:
            value = entry.get(key) or ()
            if isinstance(value, str):
                value = [value]
            return tuple(str(item).strip().upper() for item in value if str(item).strip())

        def as_pids(key: str) -> Tuple[Tuple[int, int], ...]:
            value = entry.get(key) or ()
            if isinstance(value, str):
                value = [value]
            try:
                return tuple(parse_key(item) for item in value)
            except PidError as exc:
                raise ProfileError(f"the {key} rule is malformed: {exc}") from exc

        years = entry.get("years") or {}
        if not isinstance(years, Mapping):
            raise ProfileError("a years rule must be a mapping with 'from' and/or 'to'")

        return cls(
            wmi=as_tuple("wmi"),
            vin_prefixes=as_tuple("vin_prefixes"),
            year_from=int(years["from"]) if years.get("from") is not None else None,
            year_to=int(years["to"]) if years.get("to") is not None else None,
            required_pids=as_pids("required_pids"),
            forbidden_pids=as_pids("forbidden_pids"),
            ecu_name_contains=as_tuple("ecu_name_contains"),
        )


@dataclass(frozen=True)
class Profile:
    """One profile file, before inheritance is applied."""

    id: str
    name: str
    description: str = ""
    extends: Optional[str] = None
    table: PidTable = field(default_factory=PidTable)
    dtc_descriptions: Mapping[str, str] = field(default_factory=dict)
    match: MatchRules = field(default_factory=MatchRules)
    write_whitelist: Tuple[Mapping[str, Any], ...] = ()
    path: Optional[Path] = None

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any], path: Optional[Path] = None) -> "Profile":
        """
        Build a profile from a parsed file.

        Args:
            document: The parsed YAML.
            path: Where it came from, for error messages.
        """
        where = f" in {path}" if path else ""
        if not isinstance(document, Mapping):
            raise ProfileError(f"a profile must be a mapping{where}, got {type(document).__name__}")

        profile_id = str(document.get("id") or "").strip()
        if not profile_id:
            raise ProfileError(f"a profile needs an id{where}")

        extends = document.get("extends")
        # Only the base profile may have no parent; everything else builds on something,
        # which is what guarantees the generic J1979 behaviour is always present.
        if extends is None and profile_id != BASE_PROFILE_ID:
            extends = BASE_PROFILE_ID

        whitelist = document.get("write_whitelist") or ()
        if isinstance(whitelist, Mapping):
            raise ProfileError(f"write_whitelist must be a list of entries{where}")

        try:
            table = PidTable.from_mapping(document.get("pids") or {}, origin=profile_id)
        except PidError as exc:
            raise ProfileError(f"profile {profile_id!r}{where} has a bad PID entry: {exc}") from exc

        return cls(
            id=profile_id,
            name=str(document.get("name") or profile_id),
            description=str(document.get("description") or "").strip(),
            extends=str(extends) if extends else None,
            table=table,
            dtc_descriptions={str(k).upper(): str(v) for k, v in dict(document.get("dtcs") or {}).items()},
            match=MatchRules.from_mapping(document.get("match")),
            write_whitelist=tuple(dict(entry) for entry in whitelist),
            path=path,
        )

    def __str__(self) -> str:
        return f"{self.id} ({self.name})"


@dataclass(frozen=True)
class ResolvedProfile:
    """
    A profile with its whole inheritance chain merged in.

    This is what a session actually decodes against. `lineage` records the chain that
    produced it, base first, so that a surprising reading can be traced to the layer that
    defined it.
    """

    id: str
    name: str
    description: str
    table: PidTable
    dtc_descriptions: Mapping[str, str]
    match: MatchRules
    write_whitelist: Tuple[Mapping[str, Any], ...]
    lineage: Tuple[str, ...]

    @property
    def is_generic(self) -> bool:
        """Whether this is the generic J1979 fallback rather than a specific profile."""
        return self.id == BASE_PROFILE_ID

    def describes(self, key: str) -> bool:
        """Whether the merged table describes a `mode:pid` key."""
        mode, pid = parse_key(key)
        return self.table.get(mode, pid) is not None

    def as_dict(self) -> Dict[str, Any]:
        """A JSON-friendly rendering, for the MCP tools and the GUI."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "lineage": list(self.lineage),
            "pid_count": len(self.table),
            "dtc_description_count": len(self.dtc_descriptions),
            "generic": self.is_generic,
        }

    def __str__(self) -> str:
        chain = " -> ".join(self.lineage)
        return f"{self.name} [{chain}]"


def merge_chain(chain: Sequence[Profile]) -> ResolvedProfile:
    """
    Merge an inheritance chain into the profile a session decodes against.

    Args:
        chain: The profiles, base first and most specific last.

    Returns:
        The merged profile, carrying the identity of the last one in the chain.
    """
    if not chain:
        raise ProfileError("an inheritance chain needs at least one profile")

    table = PidTable()
    descriptions: Dict[str, str] = {}
    whitelist: List[Mapping[str, Any]] = []

    for profile in chain:
        table = table.merged_with(profile.table)
        descriptions.update(profile.dtc_descriptions)
        whitelist.extend(profile.write_whitelist)

    leaf = chain[-1]
    return ResolvedProfile(
        id=leaf.id,
        name=leaf.name,
        description=leaf.description,
        table=table,
        dtc_descriptions=descriptions,
        match=leaf.match,
        write_whitelist=tuple(whitelist),
        lineage=tuple(profile.id for profile in chain),
    )
