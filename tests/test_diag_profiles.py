"""
Unit tests for vehicle profiles: the format, inheritance, and the resolution cascade.

The rule that matters most is the last one: resolution never fails. A wrong-but-specific
profile would decode a manufacturer PID into a plausible-looking wrong number, which is
worse than decoding fewer parameters, so anything unmatched falls back to generic J1979.
"""

import textwrap

import pytest

from canopen_studio.diag.j1979.client import VehicleIdentity
from canopen_studio.diag.j1979.vin import parse_vin
from canopen_studio.diag.profiles.library import BUNDLED_DIRECTORY, ProfileLibrary, load_profile_file
from canopen_studio.diag.profiles.model import BASE_PROFILE_ID, MatchRules, Profile, ProfileError
from canopen_studio.diag.profiles.resolver import (
    STAGE_FALLBACK,
    STAGE_FINGERPRINT,
    STAGE_MANUAL,
    STAGE_VIN,
    ProfileResolver,
)

HONDA_VIN = "1HGBH41JXMN109186"


def write_profile(directory, name, body):
    """Write a profile file into a temporary directory."""
    path = directory / f"{name}.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


@pytest.fixture
def library(tmp_path):
    """A library over the bundled profiles plus a temporary directory."""
    return ProfileLibrary(directories=[tmp_path]).load(), tmp_path


class TestProfileFormat:
    def test_a_profile_needs_an_id(self):
        with pytest.raises(ProfileError):
            Profile.from_mapping({"name": "Nameless"})

    def test_a_profile_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(ProfileError):
            Profile.from_mapping(["not", "a", "mapping"])

    def test_the_name_defaults_to_the_id(self):
        assert Profile.from_mapping({"id": "thing"}).name == "thing"

    def test_a_profile_extends_the_base_by_default(self):
        """Every profile builds on J1979, so the legislated behaviour is always there."""
        assert Profile.from_mapping({"id": "thing"}).extends == BASE_PROFILE_ID

    def test_the_base_profile_extends_nothing(self):
        assert Profile.from_mapping({"id": BASE_PROFILE_ID}).extends is None

    def test_an_explicit_parent_is_kept(self):
        assert Profile.from_mapping({"id": "child", "extends": "parent"}).extends == "parent"

    def test_pids_become_a_table(self):
        profile = Profile.from_mapping({"id": "thing", "pids": {"01:0C": {"name": "rpm", "formula": "A"}}})

        assert profile.table.get(0x01, 0x0C).name == "rpm"

    def test_trouble_code_wordings_are_upper_cased(self):
        profile = Profile.from_mapping({"id": "thing", "dtcs": {"p0143": "Sensor fault"}})

        assert profile.dtc_descriptions == {"P0143": "Sensor fault"}

    def test_a_write_whitelist_must_be_a_list(self):
        with pytest.raises(ProfileError):
            Profile.from_mapping({"id": "thing", "write_whitelist": {"service": "0x2E"}})


class TestMatchRules:
    def test_no_rules_means_no_automatic_match(self):
        assert MatchRules.from_mapping(None).is_empty is True

    def test_a_single_manufacturer_can_be_written_as_a_string(self):
        assert MatchRules.from_mapping({"wmi": "1HG"}).wmi == ("1HG",)

    def test_manufacturers_are_upper_cased(self):
        assert MatchRules.from_mapping({"wmi": ["1hg"]}).wmi == ("1HG",)

    def test_year_bounds_are_read(self):
        rules = MatchRules.from_mapping({"years": {"from": 2015, "to": 2020}})

        assert (rules.year_from, rules.year_to) == (2015, 2020)

    def test_pid_rules_are_parsed_into_keys(self):
        rules = MatchRules.from_mapping({"required_pids": ["01:5B"], "forbidden_pids": ["01:5E"]})

        assert rules.required_pids == ((0x01, 0x5B),)
        assert rules.forbidden_pids == ((0x01, 0x5E),)

    def test_a_malformed_pid_rule_is_refused_when_the_profile_loads(self):
        with pytest.raises(ProfileError):
            MatchRules.from_mapping({"required_pids": ["nonsense"]})

    def test_a_match_section_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(ProfileError):
            MatchRules.from_mapping(["1HG"])

    def test_rules_with_any_constraint_are_not_empty(self):
        assert MatchRules.from_mapping({"wmi": ["1HG"]}).is_empty is False


class TestBundledProfiles:
    def test_the_base_profile_loads(self):
        assert ProfileLibrary().load().get(BASE_PROFILE_ID) is not None

    def test_every_bundled_file_parses(self):
        library = ProfileLibrary().load()

        assert library.errors == []

    def test_every_bundled_profile_resolves(self):
        library = ProfileLibrary().load()

        library.resolved()

        assert library.errors == []

    def test_the_base_profile_is_reachable_by_name(self):
        assert ProfileLibrary().load().base().id == BASE_PROFILE_ID

    def test_the_bundled_directory_is_inside_the_package(self):
        assert BUNDLED_DIRECTORY.is_dir()

    def test_the_example_profile_cannot_match_a_real_vehicle(self):
        """It is documentation, so its rules sit under a WMI nobody uses."""
        example = ProfileLibrary().load().get("example_make")

        assert example.match.wmi == ("ZZZ",)


class TestLoading:
    def test_a_local_profile_is_loaded_alongside_the_bundled_ones(self, library):
        loaded, directory = library
        write_profile(directory, "local", "id: local\nname: Local\n")

        loaded.load()

        assert "local" in loaded
        assert BASE_PROFILE_ID in loaded

    def test_a_local_profile_shadows_a_bundled_one_of_the_same_id(self, library):
        loaded, directory = library
        write_profile(directory, "override", f"id: {BASE_PROFILE_ID}\nname: My J1979\n")

        loaded.load()

        assert loaded.get(BASE_PROFILE_ID).name == "My J1979"

    def test_a_broken_profile_is_recorded_and_skipped(self, library):
        """One bad local file must not take the generic fallback down with it."""
        loaded, directory = library
        write_profile(directory, "broken", "id: broken\npids:\n  bad-key: {name: x}\n")

        loaded.load()

        assert loaded.errors
        assert loaded.base().id == BASE_PROFILE_ID

    def test_invalid_yaml_is_recorded_rather_than_raised(self, library):
        loaded, directory = library
        (directory / "bad.yaml").write_text("id: [unclosed\n", encoding="utf-8")

        loaded.load()

        assert any("not valid YAML" in error for error in loaded.errors)

    def test_an_empty_file_is_recorded(self, library):
        loaded, directory = library
        (directory / "empty.yaml").write_text("", encoding="utf-8")

        loaded.load()

        assert any("empty" in error for error in loaded.errors)

    def test_a_missing_file_is_reported(self, tmp_path):
        with pytest.raises(ProfileError):
            load_profile_file(tmp_path / "nope.yaml")

    def test_a_missing_directory_is_ignored(self, tmp_path):
        loaded = ProfileLibrary(directories=[tmp_path / "nothing-here"]).load()

        assert loaded.base().id == BASE_PROFILE_ID

    def test_a_profile_can_be_added_in_memory(self):
        loaded = ProfileLibrary().load()

        loaded.add(Profile.from_mapping({"id": "imported", "pids": {"01:A0": {"name": "custom", "formula": "A"}}}))

        assert loaded.resolve("imported").table.get(0x01, 0xA0).name == "custom"


class TestInheritance:
    def test_a_chain_runs_base_first(self, library):
        loaded, directory = library
        write_profile(directory, "make", "id: make\nextends: j1979_base\n")
        write_profile(directory, "model", "id: model\nextends: make\n")
        loaded.load()

        assert [profile.id for profile in loaded.chain("model")] == [BASE_PROFILE_ID, "make", "model"]

    def test_inherited_pids_are_available_to_the_child(self, library):
        loaded, directory = library
        write_profile(directory, "model", "id: model\nextends: j1979_base\n")
        loaded.load()

        assert loaded.resolve("model").table.get(0x01, 0x0C).name == "engine_speed"

    def test_a_child_redefinition_wins(self, library):
        loaded, directory = library
        write_profile(
            directory,
            "model",
            """
            id: model
            extends: j1979_base
            pids:
              "01:0C": {name: crank_speed, formula: "A"}
            """,
        )
        loaded.load()

        assert loaded.resolve("model").table.get(0x01, 0x0C).name == "crank_speed"

    def test_the_parent_is_unaffected_by_the_child(self, library):
        loaded, directory = library
        write_profile(
            directory,
            "model",
            """
            id: model
            extends: j1979_base
            pids:
              "01:0C": {name: crank_speed, formula: "A"}
            """,
        )
        loaded.load()
        loaded.resolve("model")

        assert loaded.resolve(BASE_PROFILE_ID).table.get(0x01, 0x0C).name == "engine_speed"

    def test_three_layers_merge_in_order(self, library):
        loaded, directory = library
        write_profile(directory, "make", 'id: make\npids:\n  "01:A0": {name: from_make, formula: "A"}\n')
        write_profile(
            directory,
            "model",
            """
            id: model
            extends: make
            pids:
              "01:A1": {name: from_model, formula: "A"}
              "01:A0": {name: overridden, formula: "A"}
            """,
        )
        loaded.load()

        resolved = loaded.resolve("model")

        assert resolved.table.get(0x01, 0xA0).name == "overridden"
        assert resolved.table.get(0x01, 0xA1).name == "from_model"
        assert resolved.table.get(0x01, 0x0C).name == "engine_speed"

    def test_trouble_code_wordings_accumulate_down_the_chain(self, library):
        loaded, directory = library
        write_profile(directory, "make", "id: make\ndtcs:\n  P1234: Make fault\n")
        write_profile(directory, "model", "id: model\nextends: make\ndtcs:\n  P1235: Model fault\n")
        loaded.load()

        descriptions = loaded.resolve("model").dtc_descriptions

        assert descriptions["P1234"] == "Make fault"
        assert descriptions["P1235"] == "Model fault"

    def test_the_lineage_is_recorded(self, library):
        loaded, directory = library
        write_profile(directory, "model", "id: model\nextends: j1979_base\n")
        loaded.load()

        assert loaded.resolve("model").lineage == (BASE_PROFILE_ID, "model")

    def test_an_unknown_profile_is_reported(self):
        with pytest.raises(ProfileError) as excinfo:
            ProfileLibrary().load().resolve("nonexistent")

        assert "nonexistent" in str(excinfo.value)

    def test_extending_something_unknown_is_reported(self, library):
        loaded, directory = library
        write_profile(directory, "orphan", "id: orphan\nextends: missing_parent\n")
        loaded.load()

        with pytest.raises(ProfileError) as excinfo:
            loaded.resolve("orphan")

        assert "missing_parent" in str(excinfo.value)

    def test_an_inheritance_loop_is_reported(self, library):
        loaded, directory = library
        write_profile(directory, "a", "id: a\nextends: b\n")
        write_profile(directory, "b", "id: b\nextends: a\n")
        loaded.load()

        with pytest.raises(ProfileError) as excinfo:
            loaded.resolve("a")

        assert "loop" in str(excinfo.value)

    def test_a_resolved_profile_serialises_for_an_agent(self, library):
        loaded, _ = library

        payload = loaded.base().as_dict()

        assert payload["id"] == BASE_PROFILE_ID
        assert payload["generic"] is True
        assert payload["pid_count"] > 90


class TestResolutionCascade:
    @pytest.fixture
    def resolver(self, tmp_path):
        write_profile(
            tmp_path,
            "acme_2015",
            """
            id: acme_2015
            name: Acme 2015-2020
            match:
              wmi: [1HG]
              years: {from: 1985, to: 1995}
            pids:
              "01:A0": {name: acme_signal, formula: "A"}
            """,
        )
        write_profile(
            tmp_path,
            "by_fingerprint",
            """
            id: by_fingerprint
            name: Identified by supported PIDs
            match:
              required_pids: ["01:5B"]
              forbidden_pids: ["01:5E"]
            """,
        )
        return ProfileResolver(ProfileLibrary(directories=[tmp_path]).load())

    def test_a_vin_selects_a_profile(self, resolver):
        identity = VehicleIdentity(vin=HONDA_VIN, vin_info=parse_vin(HONDA_VIN))

        match = resolver.resolve(identity)

        assert match.profile.id == "acme_2015"
        assert match.stage == STAGE_VIN

    def test_the_match_explains_itself(self, resolver):
        identity = VehicleIdentity(vin=HONDA_VIN, vin_info=parse_vin(HONDA_VIN))

        match = resolver.resolve(identity)

        assert any("1HG" in reason for reason in match.reasons)

    def test_a_vin_from_another_manufacturer_does_not_match(self, resolver):
        other = "WVWZZZ1JZ3W386752"
        identity = VehicleIdentity(vin=other, vin_info=parse_vin(other))

        assert resolver.resolve(identity).is_fallback is True

    def test_a_vin_outside_the_year_range_does_not_match(self, resolver):
        """The year is part of the claim, so a 2021 car is not this profile's car."""
        identity = VehicleIdentity(vin=HONDA_VIN, vin_info=parse_vin(HONDA_VIN, model_year=2021))

        assert resolver.resolve(identity).is_fallback is True

    def test_an_ambiguous_year_matches_if_either_candidate_fits(self, resolver):
        """The year code repeats every thirty years, so both readings are considered."""
        newer = HONDA_VIN[:6] + "J" + HONDA_VIN[7:]
        identity = VehicleIdentity(vin=newer, vin_info=parse_vin(newer))

        assert resolver.resolve(identity).profile.id == "acme_2015"

    def test_a_profile_claiming_another_manufacturer_loses_on_fingerprint_too(self, resolver):
        """The PID set may fit, but the VIN says this is not that manufacturer's car."""
        other = "WVWZZZ1JZ3W386752"
        identity = VehicleIdentity(
            vin=other,
            vin_info=parse_vin(other),
            supported_pids=_supported({0x5B}),
        )

        assert resolver.resolve(identity).profile.id == "by_fingerprint"

    def test_the_fingerprint_stage_runs_when_there_is_no_vin(self, resolver):
        identity = VehicleIdentity(supported_pids=_supported({0x5B}))

        match = resolver.resolve(identity)

        assert match.profile.id == "by_fingerprint"
        assert match.stage == STAGE_FINGERPRINT

    def test_a_forbidden_pid_rules_a_profile_out(self, resolver):
        """This is what separates two otherwise identical variants."""
        identity = VehicleIdentity(supported_pids=_supported({0x5B, 0x5E}))

        assert resolver.resolve(identity).is_fallback is True

    def test_a_missing_required_pid_rules_a_profile_out(self, resolver):
        identity = VehicleIdentity(supported_pids=_supported({0x0C}))

        assert resolver.resolve(identity).is_fallback is True

    def test_a_manual_choice_wins_over_the_automatic_stages(self, resolver):
        identity = VehicleIdentity(vin=HONDA_VIN, vin_info=parse_vin(HONDA_VIN))

        match = resolver.resolve(identity, manual="by_fingerprint")

        assert match.profile.id == "by_fingerprint"
        assert match.stage == STAGE_MANUAL

    def test_a_manual_choice_of_an_unknown_profile_is_refused(self, resolver):
        with pytest.raises(ProfileError):
            resolver.resolve(manual="nonexistent")

    def test_a_vehicle_that_says_nothing_gets_the_generic_profile(self, resolver):
        match = resolver.resolve(VehicleIdentity())

        assert match.profile.id == BASE_PROFILE_ID
        assert match.stage == STAGE_FALLBACK

    def test_resolution_without_an_identity_still_succeeds(self, resolver):
        assert resolver.resolve().profile.id == BASE_PROFILE_ID

    def test_the_fallback_still_decodes_the_legislated_parameters(self, resolver):
        """The point of falling back: fewer parameters, all of them correct."""
        match = resolver.resolve(VehicleIdentity())

        assert match.profile.table.get(0x01, 0x0C).name == "engine_speed"

    def test_the_fallback_explains_why(self, resolver):
        assert any("no specific profile" in reason for reason in resolver.resolve().reasons)

    def test_candidates_are_offered_for_a_manual_choice(self, resolver):
        """What a "we could not tell, please pick" prompt is built from."""
        identity = VehicleIdentity(vin=HONDA_VIN, vin_info=parse_vin(HONDA_VIN), supported_pids=_supported({0x5B}))

        candidates = resolver.candidates(identity)

        assert {match.profile.id for match in candidates} == {"acme_2015", "by_fingerprint"}

    def test_candidates_come_back_best_first(self, resolver):
        identity = VehicleIdentity(vin=HONDA_VIN, vin_info=parse_vin(HONDA_VIN), supported_pids=_supported({0x5B}))

        candidates = resolver.candidates(identity)

        assert candidates[0].score >= candidates[-1].score

    def test_a_resolved_profile_brings_its_own_pids(self, resolver):
        identity = VehicleIdentity(vin=HONDA_VIN, vin_info=parse_vin(HONDA_VIN))

        match = resolver.resolve(identity)

        assert match.profile.table.get(0x01, 0xA0).name == "acme_signal"
        assert match.profile.table.get(0x01, 0x0C).name == "engine_speed"

    def test_a_match_serialises_for_an_agent(self, resolver):
        payload = resolver.resolve(VehicleIdentity()).as_dict()

        assert payload["stage"] == STAGE_FALLBACK
        assert payload["profile"]["generic"] is True

    def test_a_match_renders_a_sentence(self, resolver):
        identity = VehicleIdentity(vin=HONDA_VIN, vin_info=parse_vin(HONDA_VIN))

        assert "by vin" in str(resolver.resolve(identity))


class TestAmbiguousModelYear:
    def test_both_candidate_years_are_considered(self, tmp_path):
        """The year code repeats every thirty years; a profile may claim either cycle."""
        write_profile(
            tmp_path,
            "later_cycle",
            """
            id: later_cycle
            match:
              wmi: [1HG]
              years: {from: 2015, to: 2025}
            """,
        )
        resolver = ProfileResolver(ProfileLibrary(directories=[tmp_path]).load())
        identity = VehicleIdentity(vin=HONDA_VIN, vin_info=parse_vin(HONDA_VIN))

        assert resolver.resolve(identity).profile.id == "later_cycle"

    def test_a_year_known_from_elsewhere_removes_the_ambiguity(self, tmp_path):
        write_profile(
            tmp_path,
            "later_cycle",
            """
            id: later_cycle
            match:
              wmi: [1HG]
              years: {from: 2015, to: 2025}
            """,
        )
        resolver = ProfileResolver(ProfileLibrary(directories=[tmp_path]).load())
        identity = VehicleIdentity(vin=HONDA_VIN, vin_info=parse_vin(HONDA_VIN, model_year=1991))

        assert resolver.resolve(identity).is_fallback is True


def _supported(pids):
    """A supported-PID set as discovery would report it."""
    from canopen_studio.diag.j1979.discovery import SupportedPids

    return SupportedPids(mode=0x01, by_ecu={0x7E8: frozenset(pids)})
