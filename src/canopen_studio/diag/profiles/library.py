"""
Loading vehicle profiles and resolving what they inherit from.

Profiles come from the bundled `data/` directory and from any directory the user points
at, so that a profile written or imported locally sits alongside the shipped ones without
modifying the package.

A profile file is read with `yaml.safe_load`, never `yaml.load`: the format is data, and
the only thing in it that resembles code is a decoding formula, which the formula
interpreter refuses to do anything but arithmetic with.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence

import yaml

from .model import BASE_PROFILE_ID, Profile, ProfileError, ResolvedProfile, merge_chain

# Where the profiles that ship with the package live.
BUNDLED_DIRECTORY = Path(__file__).resolve().parent / "data"

# Inheritance chains are bounded, so a file that extends itself through a long loop is
# caught even if the cycle detection below were ever to miss it.
MAX_DEPTH = 16


def load_profile_file(path: Path) -> Profile:
    """
    Read one profile file.

    Args:
        path: The YAML file.

    Raises:
        ProfileError: If the file cannot be read or is not a valid profile.
    """
    try:
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ProfileError(f"{path} is not valid YAML: {exc}") from exc

    if document is None:
        raise ProfileError(f"{path} is empty")
    return Profile.from_mapping(document, path=Path(path))


class ProfileLibrary:
    """
    The profiles available to a session, and the inheritance between them.

    Args:
        directories: Where to look, in order. The bundled directory is searched first
            unless `include_bundled` is off, so a local profile can extend a shipped one.
        include_bundled: Whether to load the profiles that ship with the package.
    """

    def __init__(
        self,
        directories: Optional[Sequence[Path]] = None,
        include_bundled: bool = True,
    ):
        self.directories: List[Path] = []
        if include_bundled:
            self.directories.append(BUNDLED_DIRECTORY)
        self.directories.extend(Path(directory) for directory in (directories or ()))

        self._profiles: Dict[str, Profile] = {}
        self._errors: List[str] = []
        self._resolved: Dict[str, ResolvedProfile] = {}

    # -- Loading -----------------------------------------------------------

    def load(self) -> "ProfileLibrary":
        """
        Read every profile in the configured directories.

        A file that fails to parse is recorded in `errors` and skipped rather than
        aborting the load: one bad local profile must not take the generic J1979
        fallback down with it.
        """
        self._profiles.clear()
        self._errors.clear()
        self._resolved.clear()

        for directory in self.directories:
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*.yaml")):
                try:
                    profile = load_profile_file(path)
                except ProfileError as exc:
                    self._errors.append(str(exc))
                    continue
                # A later directory shadows an earlier one, so a local profile can
                # replace a bundled one by reusing its id.
                self._profiles[profile.id] = profile

        return self

    def add(self, profile: Profile) -> None:
        """Register a profile built in memory, such as one from an importer."""
        self._profiles[profile.id] = profile
        self._resolved.pop(profile.id, None)

    @property
    def errors(self) -> List[str]:
        """Files that could not be loaded, and why."""
        return list(self._errors)

    # -- Access ------------------------------------------------------------

    def get(self, profile_id: str) -> Optional[Profile]:
        """One profile as written, without its inheritance applied."""
        return self._profiles.get(profile_id)

    def ids(self) -> List[str]:
        """Every profile identifier, in order."""
        return sorted(self._profiles)

    def __contains__(self, profile_id: object) -> bool:
        return profile_id in self._profiles

    def __iter__(self) -> Iterator[Profile]:
        return iter(self._profiles[key] for key in sorted(self._profiles))

    def __len__(self) -> int:
        return len(self._profiles)

    # -- Resolution --------------------------------------------------------

    def chain(self, profile_id: str) -> List[Profile]:
        """
        The inheritance chain of a profile, base first.

        Raises:
            ProfileError: If the profile is unknown, extends something unknown, or
                extends itself through a cycle.
        """
        chain: List[Profile] = []
        seen: List[str] = []
        current: Optional[str] = profile_id

        while current is not None:
            if current in seen:
                loop = " -> ".join([*seen, current])
                raise ProfileError(f"profile inheritance loops: {loop}")
            profile = self._profiles.get(current)
            if profile is None:
                if not seen:
                    raise ProfileError(f"no profile named {current!r}")
                raise ProfileError(f"profile {seen[-1]!r} extends {current!r}, which does not exist")
            seen.append(current)
            chain.append(profile)
            if len(chain) > MAX_DEPTH:
                raise ProfileError(f"profile {profile_id!r} inherits more than {MAX_DEPTH} levels deep")
            current = profile.extends

        chain.reverse()
        return chain

    def resolve(self, profile_id: str) -> ResolvedProfile:
        """
        Merge a profile with everything it inherits from.

        The result is cached, because resolution is pure and a session asks for the same
        profile repeatedly.
        """
        if profile_id not in self._resolved:
            self._resolved[profile_id] = merge_chain(self.chain(profile_id))
        return self._resolved[profile_id]

    def base(self) -> ResolvedProfile:
        """
        The generic SAE J1979 profile, which must always work.

        Raises:
            ProfileError: If it is missing, which means the package data did not install.
        """
        if BASE_PROFILE_ID not in self._profiles:
            raise ProfileError(
                f"the {BASE_PROFILE_ID!r} profile is missing — the package data did not install correctly"
            )
        return self.resolve(BASE_PROFILE_ID)

    def resolved(self) -> List[ResolvedProfile]:
        """Every profile, merged, skipping any whose inheritance cannot be resolved."""
        results = []
        for profile_id in self.ids():
            try:
                results.append(self.resolve(profile_id))
            except ProfileError as exc:
                self._errors.append(str(exc))
        return results


_default_library: Optional[ProfileLibrary] = None


def default_library() -> ProfileLibrary:
    """The bundled profiles, loaded once and shared."""
    global _default_library
    if _default_library is None:
        _default_library = ProfileLibrary().load()
    return _default_library
