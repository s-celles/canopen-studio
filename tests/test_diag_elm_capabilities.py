"""
Unit tests for ELM327 version reporting and capability probing.

The case that matters is the clone: a board whose firmware answers "ELM327 v2.1" while
refusing commands that version is supposed to have. The probe has to catch that and
degrade, not trust the banner and then fail obscurely later.
"""

import pytest
from elm327_fake import FakeElm327

from canopen_studio.diag.elm327.capabilities import (
    INTRODUCED_IN,
    PROBES,
    ElmCapabilities,
    parse_version,
    probe_capabilities,
)
from canopen_studio.diag.elm327.protocol import ElmProtocol


def probe(version="ELM327 v1.5", unsupported=()):
    adapter = FakeElm327(version=version, unsupported=unsupported)
    adapter.open()
    protocol = ElmProtocol(adapter)
    protocol.send("ATE0")
    return probe_capabilities(protocol), adapter


class TestVersionParsing:
    @pytest.mark.parametrize(
        "banner, expected",
        [
            ("ELM327 v1.5", 1.5),
            ("ELM327 v2.1", 2.1),
            ("ELM327 v1.0", 1.0),
            ("elm327 V1.4", 1.4),
            ("ELM327 v 1.3", 1.3),
        ],
    )
    def test_a_version_is_read_from_the_banner(self, banner, expected):
        assert parse_version(banner) == expected

    def test_a_banner_without_a_version_yields_none(self):
        """Some clones answer with a product name and no version at all."""
        assert parse_version("OBDII Interface") is None

    def test_an_empty_banner_yields_none(self):
        assert parse_version("") is None

    def test_a_missing_banner_yields_none(self):
        assert parse_version(None) is None


class TestProbing:
    def test_every_probe_is_sent(self):
        _, adapter = probe()

        for command in PROBES.values():
            assert command in adapter.commands

    def test_a_full_featured_board_supports_everything(self):
        capabilities, _ = probe()

        assert set(capabilities.supported) == set(PROBES)

    def test_the_banner_is_recorded(self):
        capabilities, _ = probe(version="ELM327 v2.1")

        assert capabilities.reported_banner == "ELM327 v2.1"
        assert capabilities.reported_version == 2.1

    def test_the_device_description_is_recorded(self):
        capabilities, _ = probe()

        assert capabilities.device_description == "OBDII to RS232 Interpreter"

    def test_a_refused_command_is_not_counted_as_supported(self):
        capabilities, _ = probe(unsupported=["CRA"])

        assert capabilities.has("can_receive_filter") is False

    def test_the_other_capabilities_survive_one_refusal(self):
        capabilities, _ = probe(unsupported=["CRA"])

        assert capabilities.has("adaptive_timing") is True

    def test_probes_leave_the_adapter_in_its_default_state(self):
        """Every probe is a no-op, so identification never changes later behaviour."""
        _, adapter = probe()

        assert adapter.headers is False
        assert adapter.protocol == 0


class TestCloneDetection:
    def test_a_board_matching_its_banner_is_not_flagged(self):
        capabilities, _ = probe(version="ELM327 v2.1")

        assert capabilities.is_probable_clone is False

    def test_a_board_missing_a_command_of_its_claimed_version_is_flagged(self):
        capabilities, _ = probe(version="ELM327 v2.1", unsupported=["CRA"])

        assert capabilities.is_probable_clone is True

    def test_the_missing_capabilities_are_named(self):
        capabilities, _ = probe(version="ELM327 v2.1", unsupported=["CRA", "CEA"])

        assert capabilities.missing_for_reported_version == ("can_extended_addressing", "can_receive_filter")

    def test_an_old_board_missing_a_later_command_is_not_a_clone(self):
        """A genuine v1.2 chip has no CAN receive filter, and that is not a lie."""
        capabilities, _ = probe(version="ELM327 v1.2", unsupported=["CRA", "CEA"])

        assert capabilities.is_probable_clone is False

    def test_the_effective_version_is_capped_at_what_the_board_answers_to(self):
        capabilities, _ = probe(version="ELM327 v2.1", unsupported=["CRA", "CEA"])

        assert capabilities.effective_version == 1.2

    def test_a_board_that_keeps_its_promises_keeps_its_version(self):
        capabilities, _ = probe(version="ELM327 v1.4")

        assert capabilities.effective_version == 1.4

    def test_a_clone_explains_itself_in_its_notes(self):
        capabilities, _ = probe(version="ELM327 v2.1", unsupported=["CRA"])

        assert any("does not implement" in note for note in capabilities.notes)

    def test_a_missing_receive_filter_is_called_out(self):
        """The session filters in software instead, which is worth saying once."""
        capabilities, _ = probe(version="ELM327 v1.2", unsupported=["CRA", "CEA"])

        assert any("filtered in software" in note for note in capabilities.notes)

    def test_an_unrecognisable_banner_is_called_out(self):
        capabilities, _ = probe(version="OBDII Interface")

        assert capabilities.reported_version is None
        assert any("recognisable version" in note for note in capabilities.notes)

    def test_a_clean_board_has_nothing_to_report(self):
        capabilities, _ = probe(version="ELM327 v2.1")

        assert capabilities.notes == ()


class TestCapabilityValue:
    def test_capabilities_render_their_version(self):
        capabilities = ElmCapabilities(reported_version=1.5, supported=frozenset(PROBES))

        assert str(capabilities) == "ELM327 v1.5"

    def test_a_clone_says_so_when_rendered(self):
        capabilities = ElmCapabilities(reported_version=2.1, supported=frozenset())

        assert "probable clone" in str(capabilities)

    def test_an_unknown_version_renders_without_a_number(self):
        assert "unknown version" in str(ElmCapabilities())

    def test_an_unknown_version_reports_no_missing_capabilities(self):
        """With nothing claimed there is nothing to catch the board out on."""
        assert ElmCapabilities().missing_for_reported_version == ()

    def test_every_probe_has_a_version_it_was_introduced_in(self):
        assert set(PROBES) == set(INTRODUCED_IN)
