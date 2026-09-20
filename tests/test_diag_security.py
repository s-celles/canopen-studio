"""
Unit tests for the diagnostic write gate.

The gates are independent and all must open, so each is tested on its own and the
combination is tested for the property that matters most: when a write is refused,
nothing is transmitted. The fourth gate applies only to a request arriving through MCP,
and exists so that enabling writes for a person at the GUI does not hand the capability
to a connected model.
"""

import pytest

from canopen_studio.diag import DiagnosticInterface, DiagnosticResponse
from canopen_studio.diag.security import (
    AGENT_WRITE_ENABLED_ENV,
    IMPLEMENTED_SERVICES,
    MODE_CLEAR_DTC,
    WRITE_ENABLED_ENV,
    WRITE_SERVICES,
    DiagnosticWriteRefused,
    WhitelistEntry,
    WriteGate,
    agent_write_warning,
    agent_writes_enabled,
    clear_trouble_codes,
    service_name,
    writes_enabled,
)


class RecordingInterface(DiagnosticInterface):
    """Records every request, so a refused write can be shown to send nothing."""

    def __init__(self, replies=None):
        super().__init__()
        self.requests = []
        self.replies = replies or {}
        self.open()

    @property
    def description(self):
        return "recording"

    def _open(self):
        pass

    def _close(self):
        pass

    def _request(self, payload, timeout):
        self.requests.append(payload)
        return [DiagnosticResponse(source=source, data=data) for source, data in self.replies.get(payload, [])]


@pytest.fixture(autouse=True)
def writes_off(monkeypatch):
    """Every test starts with both switches closed, as a fresh process does."""
    monkeypatch.delenv(WRITE_ENABLED_ENV, raising=False)
    monkeypatch.delenv(AGENT_WRITE_ENABLED_ENV, raising=False)


class TestKillSwitch:
    def test_writes_are_off_in_a_fresh_process(self):
        assert writes_enabled() is False

    def test_the_environment_variable_opens_the_switch(self, monkeypatch):
        monkeypatch.setenv(WRITE_ENABLED_ENV, "1")

        assert writes_enabled() is True

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
    def test_the_usual_truthy_spellings_work(self, monkeypatch, value):
        monkeypatch.setenv(WRITE_ENABLED_ENV, value)

        assert writes_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_anything_else_leaves_it_closed(self, monkeypatch, value):
        monkeypatch.setenv(WRITE_ENABLED_ENV, value)

        assert writes_enabled() is False

    def test_a_closed_switch_refuses_even_a_confirmed_call(self):
        gate = WriteGate()

        with pytest.raises(DiagnosticWriteRefused) as excinfo:
            gate.check(MODE_CLEAR_DTC, confirm=True)

        assert WRITE_ENABLED_ENV in str(excinfo.value)

    def test_the_switch_can_be_overridden_for_tests(self):
        WriteGate(enabled=True).check(MODE_CLEAR_DTC, confirm=True)


class TestConfirmation:
    def test_an_unconfirmed_call_is_refused(self):
        gate = WriteGate(enabled=True)

        with pytest.raises(DiagnosticWriteRefused) as excinfo:
            gate.check(MODE_CLEAR_DTC)

        assert "confirm=True" in str(excinfo.value)

    def test_a_confirmed_call_passes(self):
        WriteGate(enabled=True).check(MODE_CLEAR_DTC, confirm=True)

    def test_the_refusal_says_confirmation_does_not_persist(self):
        with pytest.raises(DiagnosticWriteRefused) as excinfo:
            WriteGate(enabled=True).check(MODE_CLEAR_DTC)

        assert "never persists" in str(excinfo.value)


class TestUnimplementedServices:
    @pytest.mark.parametrize("service", sorted(WRITE_SERVICES))
    def test_every_uds_write_service_is_refused(self, service):
        """This release has no request path for them, by decision."""
        gate = WriteGate(enabled=True, whitelist=[{"service": service}])

        with pytest.raises(DiagnosticWriteRefused) as excinfo:
            gate.check(service, confirm=True)

        assert "no request path" in str(excinfo.value)

    def test_the_refusal_names_the_service(self):
        gate = WriteGate(enabled=True, whitelist=[{"service": 0x2E}])

        with pytest.raises(DiagnosticWriteRefused) as excinfo:
            gate.check(0x2E, identifier=0xF190, confirm=True)

        assert "WriteDataByIdentifier" in str(excinfo.value)

    def test_a_whitelist_entry_does_not_make_an_unimplemented_service_available(self):
        gate = WriteGate(enabled=True, whitelist=[{"service": 0x31, "identifier": 0x0203}])

        assert gate.allows(0x31, 0x0203) is False

    def test_only_clearing_codes_is_implemented(self):
        assert IMPLEMENTED_SERVICES == frozenset({MODE_CLEAR_DTC})

    def test_an_unknown_service_is_refused(self):
        with pytest.raises(DiagnosticWriteRefused):
            WriteGate(enabled=True).check(0x99, confirm=True)


class TestWhitelist:
    def test_an_entry_covers_its_own_service(self):
        entry = WhitelistEntry(service=0x2E, identifier=0xF190)

        assert entry.covers(0x2E, 0xF190) is True

    def test_an_entry_does_not_cover_another_identifier(self):
        entry = WhitelistEntry(service=0x2E, identifier=0xF190)

        assert entry.covers(0x2E, 0xF191) is False

    def test_an_entry_does_not_cover_another_service(self):
        assert WhitelistEntry(service=0x2E).covers(0x31) is False

    def test_an_entry_without_an_identifier_covers_the_whole_service(self):
        """How a profile allows a routine family without listing every member."""
        assert WhitelistEntry(service=0x31).covers(0x31, 0x0203) is True

    def test_an_entry_is_read_from_a_profile_section(self):
        entry = WhitelistEntry.from_mapping({"service": "0x2E", "identifier": "0xF190", "description": "Write VIN"})

        assert (entry.service, entry.identifier) == (0x2E, 0xF190)
        assert entry.description == "Write VIN"

    def test_decimal_values_are_accepted(self):
        assert WhitelistEntry.from_mapping({"service": 46}).service == 0x2E

    def test_an_entry_without_a_service_is_refused(self):
        with pytest.raises(DiagnosticWriteRefused):
            WhitelistEntry.from_mapping({"identifier": "0xF190"})

    def test_an_entry_that_is_not_a_mapping_is_refused(self):
        with pytest.raises(DiagnosticWriteRefused):
            WhitelistEntry.from_mapping("0x2E")

    def test_a_non_numeric_field_is_refused(self):
        """A typo must not become a silently empty whitelist."""
        with pytest.raises(DiagnosticWriteRefused):
            WhitelistEntry.from_mapping({"service": "not a number"})

    def test_an_entry_renders_readably(self):
        entry = WhitelistEntry(service=0x2E, identifier=0xF190, description="Write VIN")

        assert str(entry) == "WriteDataByIdentifier 0xF190 — Write VIN"

    def test_a_whitelist_can_come_from_a_resolved_profile(self):
        class FakeProfile:
            write_whitelist = ({"service": "0x2E", "identifier": "0xF190"},)

        gate = WriteGate(whitelist=FakeProfile(), enabled=True)

        assert len(gate.entries) == 1

    def test_no_whitelist_permits_nothing(self):
        assert WriteGate(enabled=True).entries == ()

    def test_a_whitelist_that_is_not_a_sequence_is_refused(self):
        with pytest.raises(DiagnosticWriteRefused):
            WriteGate(whitelist=42)


class TestClearingTroubleCodes:
    def test_a_confirmed_clear_is_sent(self):
        interface = RecordingInterface({b"\x04": [(0x7E8, bytes([0x44]))]})

        acknowledged = clear_trouble_codes(interface, WriteGate(enabled=True), confirm=True)

        assert interface.requests == [b"\x04"]
        assert acknowledged == [0x7E8]

    def test_clearing_needs_no_whitelist_entry(self):
        """It is a legislated service, not a manufacturer one."""
        interface = RecordingInterface({b"\x04": [(0x7E8, bytes([0x44]))]})

        clear_trouble_codes(interface, WriteGate(enabled=True), confirm=True)

        assert interface.requests == [b"\x04"]

    def test_an_unconfirmed_clear_transmits_nothing(self):
        """The property that matters: a refusal never reaches the bus."""
        interface = RecordingInterface()

        with pytest.raises(DiagnosticWriteRefused):
            clear_trouble_codes(interface, WriteGate(enabled=True))

        assert interface.requests == []

    def test_a_clear_with_the_switch_closed_transmits_nothing(self):
        interface = RecordingInterface()

        with pytest.raises(DiagnosticWriteRefused):
            clear_trouble_codes(interface, WriteGate(), confirm=True)

        assert interface.requests == []

    def test_clearing_is_off_by_default_even_with_confirmation(self):
        with pytest.raises(DiagnosticWriteRefused):
            clear_trouble_codes(RecordingInterface(), WriteGate(), confirm=True)

    def test_an_ecu_that_does_not_answer_is_simply_absent(self):
        interface = RecordingInterface()

        assert clear_trouble_codes(interface, WriteGate(enabled=True), confirm=True) == []


class TestPosture:
    def test_the_gate_describes_itself(self):
        description = WriteGate(enabled=True, whitelist=[{"service": "0x2E"}]).describe()

        assert description["enabled"] is True
        assert description["environment_variable"] == WRITE_ENABLED_ENV
        assert "WriteDataByIdentifier" in description["refused_services"]

    def test_the_description_lists_what_is_implemented(self):
        assert WriteGate().describe()["implemented_services"] == ["ClearDiagnosticInformation"]

    def test_a_closed_gate_says_so(self):
        assert "disabled" in str(WriteGate())

    def test_an_open_gate_says_so(self):
        assert "enabled" in str(WriteGate(enabled=True))

    def test_services_are_named_for_refusal_messages(self):
        assert service_name(0x2E) == "WriteDataByIdentifier"
        assert service_name(0x04) == "ClearDiagnosticInformation"
        assert "0x99" in service_name(0x99)


class TestAgentGate:
    """
    The fourth gate. A write arriving through MCP needs its own switch, so that enabling
    writes for a person at the GUI does not hand the capability to a connected model.
    """

    def test_agent_writes_are_off_in_a_fresh_process(self):
        assert agent_writes_enabled() is False

    def test_the_agent_environment_variable_opens_it(self, monkeypatch):
        monkeypatch.setenv(AGENT_WRITE_ENABLED_ENV, "1")

        assert agent_writes_enabled() is True

    def test_a_non_agent_gate_ignores_the_agent_switch(self):
        """A person at the GUI needs only the process switch."""
        WriteGate(enabled=True, agent=False).check(MODE_CLEAR_DTC, confirm=True)

    def test_an_agent_gate_needs_the_extra_switch(self):
        gate = WriteGate(enabled=True, agent=True, agent_enabled=False)

        with pytest.raises(DiagnosticWriteRefused) as excinfo:
            gate.check(MODE_CLEAR_DTC, confirm=True)

        assert AGENT_WRITE_ENABLED_ENV in str(excinfo.value)

    def test_the_refusal_says_the_two_are_separate_on_purpose(self):
        gate = WriteGate(enabled=True, agent=True, agent_enabled=False)

        with pytest.raises(DiagnosticWriteRefused) as excinfo:
            gate.check(MODE_CLEAR_DTC, confirm=True)

        assert "separate on purpose" in str(excinfo.value)

    def test_an_agent_gate_passes_with_both_switches_open(self):
        WriteGate(enabled=True, agent=True, agent_enabled=True).check(MODE_CLEAR_DTC, confirm=True)

    def test_the_process_switch_is_still_required_for_an_agent(self):
        """The agent switch alone must not bypass the general one."""
        gate = WriteGate(enabled=False, agent=True, agent_enabled=True)

        with pytest.raises(DiagnosticWriteRefused) as excinfo:
            gate.check(MODE_CLEAR_DTC, confirm=True)

        assert WRITE_ENABLED_ENV in str(excinfo.value)

    def test_confirmation_is_still_required_for_an_agent(self):
        gate = WriteGate(enabled=True, agent=True, agent_enabled=True)

        with pytest.raises(DiagnosticWriteRefused):
            gate.check(MODE_CLEAR_DTC)

    def test_a_gate_can_be_rederived_for_an_agent(self):
        gate = WriteGate(whitelist=[{"service": "0x2E"}], enabled=True, agent_enabled=False)

        agent_gate = gate.for_agent()

        assert agent_gate.agent is True
        assert agent_gate.entries == gate.entries

    def test_an_agent_refusal_transmits_nothing(self):
        interface = RecordingInterface()
        gate = WriteGate(enabled=True, agent=True, agent_enabled=False)

        with pytest.raises(DiagnosticWriteRefused):
            clear_trouble_codes(interface, gate, confirm=True)

        assert interface.requests == []

    def test_an_agent_clear_is_sent_once_both_switches_are_open(self):
        interface = RecordingInterface({b"\x04": [(0x7E8, bytes([0x44]))]})
        gate = WriteGate(enabled=True, agent=True, agent_enabled=True)

        assert clear_trouble_codes(interface, gate, confirm=True) == [0x7E8]

    def test_the_posture_reports_the_agent_switch(self):
        posture = WriteGate(enabled=True, agent_enabled=True).describe()

        assert posture["agent_writes_enabled"] is True
        assert posture["agent_environment_variable"] == AGENT_WRITE_ENABLED_ENV

    def test_the_posture_warns_when_agents_may_write(self):
        assert "readiness monitors" in WriteGate(enabled=True, agent_enabled=True).describe()["warning"]

    def test_the_posture_carries_no_warning_otherwise(self):
        assert "warning" not in WriteGate(enabled=True, agent_enabled=False).describe()

    def test_a_gate_allowing_agents_says_so_when_rendered(self):
        assert "agents allowed" in str(WriteGate(enabled=True, agent_enabled=True))

    def test_the_warning_names_the_variable_that_enabled_it(self):
        assert AGENT_WRITE_ENABLED_ENV in agent_write_warning()
