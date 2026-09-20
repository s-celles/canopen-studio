"""
Vehicle profiles: declarative PID and trouble code definitions, with inheritance.

A profile is a data file. The generic SAE J1979 profile describes what every compliant
car has; a make adds what that manufacturer does; a model and year add what that car does
specifically. Each layer states only its own difference, and resolution merges them
base-first so the most specific definition wins.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from .library import BUNDLED_DIRECTORY, ProfileLibrary, default_library, load_profile_file
from .model import (
    BASE_PROFILE_ID,
    MatchRules,
    Profile,
    ProfileError,
    ResolvedProfile,
    merge_chain,
)
from .resolver import (
    STAGE_FALLBACK,
    STAGE_FINGERPRINT,
    STAGE_MANUAL,
    STAGE_VIN,
    ProfileMatch,
    ProfileResolver,
)

__all__ = [
    "BASE_PROFILE_ID",
    "BUNDLED_DIRECTORY",
    "STAGE_FALLBACK",
    "STAGE_FINGERPRINT",
    "STAGE_MANUAL",
    "STAGE_VIN",
    "MatchRules",
    "Profile",
    "ProfileError",
    "ProfileLibrary",
    "ProfileMatch",
    "ProfileResolver",
    "ResolvedProfile",
    "default_library",
    "load_profile_file",
    "merge_chain",
]
