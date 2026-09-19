"""
Choosing which vehicle profile applies.

Four stages, tried in order, and the last one always succeeds:

VIN
    The strongest evidence. The first three characters name the manufacturer, the tenth
    encodes the model year. A profile claiming a manufacturer and a year range that both
    fit is a real match.

Supported-PID fingerprint
    For a vehicle that will not give up its VIN, or whose VIN no profile claims. Two cars
    of the same model and year implement the same PIDs, so the set a vehicle declares
    identifies it well enough to choose between candidates. A profile can also name PIDs
    a vehicle must *not* have, which is what separates two otherwise identical variants.

Manual selection
    Offered when neither automatic stage found anything. A profile chosen explicitly by
    the caller is honoured over all of this, because at that point the human knows more
    than the heuristics do.

Generic J1979
    The fallback. It describes only what the standard legislates, so it is correct on any
    compliant vehicle and never claims a manufacturer-specific meaning. Resolution cannot
    fail: a wrong-but-specific profile would decode a manufacturer PID into a
    plausible-looking wrong number, and that is worse than decoding fewer parameters.

Every match reports which stage produced it and why, so a surprising decoding can be
traced back to the decision that caused it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..j1979.client import VehicleIdentity
from ..j1979.vin import VinInfo, parse_vin, VinError
from .library import ProfileLibrary, default_library
from .model import BASE_PROFILE_ID, MatchRules, ProfileError, ResolvedProfile

# Scores awarded by the VIN stage, so that a profile matching more of the VIN wins.
SCORE_VIN_PREFIX = 4.0
SCORE_WMI = 3.0
SCORE_YEAR = 2.0
SCORE_ECU_NAME = 1.0

# Scores awarded by the fingerprint stage.
SCORE_REQUIRED_PID = 1.0
SCORE_FORBIDDEN_PID_ABSENT = 0.5

# Stage names, in the order they are tried.
STAGE_MANUAL = "manual"
STAGE_VIN = "vin"
STAGE_FINGERPRINT = "fingerprint"
STAGE_FALLBACK = "fallback"


@dataclass(frozen=True)
class ProfileMatch:
    """Which profile was chosen, by which stage, and on what evidence."""

    profile: ResolvedProfile
    stage: str
    score: float = 0.0
    reasons: Tuple[str, ...] = ()

    @property
    def is_fallback(self) -> bool:
        """Whether nothing matched and the generic profile was used."""
        return self.stage == STAGE_FALLBACK

    def as_dict(self) -> Dict[str, object]:
        """A JSON-friendly rendering, for the MCP tools and the GUI."""
        return {
            "profile": self.profile.as_dict(),
            "stage": self.stage,
            "score": round(self.score, 2),
            "reasons": list(self.reasons),
        }

    def __str__(self) -> str:
        detail = "; ".join(self.reasons) if self.reasons else "no specific profile matched"
        return f"{self.profile.name} (by {self.stage}: {detail})"


@dataclass
class _Candidate:
    """
    A profile under consideration, with the evidence gathered for it so far.

    The scoring functions build one before knowing which profile it belongs to, so the
    profile is filled in by the caller and is optional until then.
    """

    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    profile: Optional[ResolvedProfile] = None


def score_against_vin(rules: MatchRules, vin: str, info: Optional[VinInfo]) -> Optional[_Candidate]:
    """
    Score a profile's rules against a VIN, or return None when they contradict it.

    A contradiction is decisive: a profile claiming another manufacturer is not a weak
    match, it is the wrong profile.
    """
    score = 0.0
    reasons = []

    if rules.vin_prefixes:
        matched = [prefix for prefix in rules.vin_prefixes if vin.startswith(prefix)]
        if not matched:
            return None
        score += SCORE_VIN_PREFIX
        reasons.append(f"VIN begins with {matched[0]}")

    if rules.wmi:
        if vin[:3] not in rules.wmi:
            return None
        score += SCORE_WMI
        reasons.append(f"manufacturer {vin[:3]}")

    if rules.year_from is not None or rules.year_to is not None:
        years = _candidate_years(info)
        if not years:
            # The profile constrains the year and the VIN will not give one, so the rule
            # cannot be confirmed. Claiming the profile anyway would be a guess.
            return None
        fitting = [year for year in years if _year_fits(rules, year)]
        if not fitting:
            return None
        score += SCORE_YEAR
        reasons.append(f"model year {fitting[0]}")

    if score == 0.0:
        return None
    return _Candidate(score=score, reasons=reasons)


def _candidate_years(info: Optional[VinInfo]) -> Tuple[int, ...]:
    """
    The model years a VIN could mean.

    The year code repeats every thirty years, so an inferred year is offered alongside
    its alternative rather than trusted on its own.
    """
    if info is None or info.model_year is None:
        return ()
    if info.model_year_is_ambiguous and info.alternate_model_year:
        return (info.model_year, info.alternate_model_year)
    return (info.model_year,)


def _year_fits(rules: MatchRules, year: int) -> bool:
    if rules.year_from is not None and year < rules.year_from:
        return False
    if rules.year_to is not None and year > rules.year_to:
        return False
    return True


def score_against_fingerprint(rules: MatchRules, supported: frozenset) -> Optional[_Candidate]:
    """
    Score a profile's rules against the PIDs a vehicle declares.

    A required PID the vehicle lacks, or a forbidden one it has, rules the profile out.
    """
    if not rules.required_pids and not rules.forbidden_pids:
        return None

    score = 0.0
    reasons = []

    for _, pid in rules.required_pids:
        if pid not in supported:
            return None
    if rules.required_pids:
        score += SCORE_REQUIRED_PID * len(rules.required_pids)
        reasons.append(f"supports all {len(rules.required_pids)} required PID(s)")

    for _, pid in rules.forbidden_pids:
        if pid in supported:
            return None
    if rules.forbidden_pids:
        score += SCORE_FORBIDDEN_PID_ABSENT * len(rules.forbidden_pids)
        reasons.append(f"lacks all {len(rules.forbidden_pids)} excluded PID(s)")

    return _Candidate(score=score, reasons=reasons)


def score_ecu_names(rules: MatchRules, names: Sequence[str]) -> Optional[_Candidate]:
    """Score a profile's ECU name rule against the names the vehicle reported."""
    if not rules.ecu_name_contains:
        return None
    joined = " ".join(name.upper() for name in names)
    matched = [fragment for fragment in rules.ecu_name_contains if fragment in joined]
    if not matched:
        return None
    return _Candidate(
        score=SCORE_ECU_NAME * len(matched),
        reasons=[f"ECU name contains {matched[0]}"],
    )


def _as_match(candidate: _Candidate, stage: str) -> Optional[ProfileMatch]:
    """Turn a scored candidate into a match, once its profile has been attached."""
    if candidate.profile is None:
        return None
    return ProfileMatch(
        profile=candidate.profile,
        stage=stage,
        score=candidate.score,
        reasons=tuple(candidate.reasons),
    )


class ProfileResolver:
    """
    Picks the profile a session decodes against.

    Args:
        library: Where profiles come from. The bundled library by default.
    """

    def __init__(self, library: Optional[ProfileLibrary] = None):
        self.library = library if library is not None else default_library()

    def resolve(
        self,
        identity: Optional[VehicleIdentity] = None,
        manual: Optional[str] = None,
    ) -> ProfileMatch:
        """
        Choose a profile for a vehicle.

        Args:
            identity: What the vehicle said about itself, from `J1979Client.identify()`.
                Without one, only a manual choice or the fallback can apply.
            manual: A profile identifier chosen by the caller. Honoured over the
                automatic stages, because at that point the human knows more than the
                heuristics do.

        Returns:
            The match. Never None: an unmatched vehicle gets the generic J1979 profile,
            which is correct everywhere rather than specific and possibly wrong.
        """
        if manual:
            return self._manual(manual)

        if identity is not None:
            by_vin = self._by_vin(identity)
            if by_vin is not None:
                return by_vin

            by_fingerprint = self._by_fingerprint(identity)
            if by_fingerprint is not None:
                return by_fingerprint

        return self.fallback()

    def candidates(self, identity: VehicleIdentity) -> List[ProfileMatch]:
        """
        Every profile that fits, best first, for a caller offering a manual choice.

        This is what a "we could not tell, please pick" prompt is built from.
        """
        matches = []
        for stage, finder in ((STAGE_VIN, self._score_vin_stage), (STAGE_FINGERPRINT, self._score_fingerprint_stage)):
            for candidate in finder(identity):
                match = _as_match(candidate, stage)
                if match is not None:
                    matches.append(match)
        return sorted(matches, key=lambda match: match.score, reverse=True)

    def fallback(self) -> ProfileMatch:
        """The generic SAE J1979 profile, which is correct on any compliant vehicle."""
        return ProfileMatch(
            profile=self.library.base(),
            stage=STAGE_FALLBACK,
            score=0.0,
            reasons=("no specific profile matched; decoding only what SAE J1979 legislates",),
        )

    # -- Stages ------------------------------------------------------------

    def _manual(self, profile_id: str) -> ProfileMatch:
        try:
            profile = self.library.resolve(profile_id)
        except ProfileError as exc:
            raise ProfileError(f"cannot use the profile chosen by hand: {exc}") from exc
        return ProfileMatch(
            profile=profile,
            stage=STAGE_MANUAL,
            score=0.0,
            reasons=(f"chosen by hand as {profile_id!r}",),
        )

    def _by_vin(self, identity: VehicleIdentity) -> Optional[ProfileMatch]:
        best = self._best(self._score_vin_stage(identity))
        return _as_match(best, STAGE_VIN) if best is not None else None

    def _by_fingerprint(self, identity: VehicleIdentity) -> Optional[ProfileMatch]:
        best = self._best(self._score_fingerprint_stage(identity))
        return _as_match(best, STAGE_FINGERPRINT) if best is not None else None

    def _score_vin_stage(self, identity: VehicleIdentity) -> List[_Candidate]:
        if not identity.vin:
            return []

        info = identity.vin_info
        if info is None:
            try:
                info = parse_vin(identity.vin)
            except VinError:
                info = None

        results = []
        for profile in self._specific_profiles():
            scored = score_against_vin(profile.match, identity.vin.upper(), info)
            if scored is None:
                continue
            scored.profile = profile
            # An ECU name rule adds confidence to a VIN match but never creates one.
            extra = score_ecu_names(profile.match, list(identity.ecu_names.values()))
            if extra is not None:
                scored.score += extra.score
                scored.reasons.extend(extra.reasons)
            results.append(scored)
        return results

    def _score_fingerprint_stage(self, identity: VehicleIdentity) -> List[_Candidate]:
        fingerprint = identity.fingerprint
        if not fingerprint:
            return []

        results = []
        for profile in self._specific_profiles():
            if self._contradicts_vin(profile.match, identity):
                # The PIDs fit, but the profile claims a manufacturer this vehicle is
                # not. Matching on the PID set alone would pick the wrong car.
                continue
            scored = score_against_fingerprint(profile.match, fingerprint)
            if scored is None:
                continue
            scored.profile = profile
            extra = score_ecu_names(profile.match, list(identity.ecu_names.values()))
            if extra is not None:
                scored.score += extra.score
                scored.reasons.extend(extra.reasons)
            results.append(scored)
        return results

    @staticmethod
    def _contradicts_vin(rules: MatchRules, identity: VehicleIdentity) -> bool:
        """Whether a profile's VIN rules rule it out for a vehicle whose VIN is known."""
        if not identity.vin:
            return False
        vin = identity.vin.upper()
        if rules.wmi and vin[:3] not in rules.wmi:
            return True
        if rules.vin_prefixes and not any(vin.startswith(prefix) for prefix in rules.vin_prefixes):
            return True
        return False

    def _specific_profiles(self) -> List[ResolvedProfile]:
        """Every profile that could match automatically, excluding the generic fallback."""
        return [
            profile
            for profile in self.library.resolved()
            if profile.id != BASE_PROFILE_ID and not profile.match.is_empty
        ]

    @staticmethod
    def _best(candidates: Sequence[_Candidate]) -> Optional[_Candidate]:
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: candidate.score)
