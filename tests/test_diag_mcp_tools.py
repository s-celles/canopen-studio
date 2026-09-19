"""
Unit tests for the OBD-II MCP tools.

The tools are plain functions, so they are called directly here, the same way
`tests/test_mcp_server.py` exercises the CAN tools. The classes that matter most are the
last two: exactly one tool can change a vehicle, and it stays shut unless the operator
opened both switches.
"""

import pytest
from elm327_fake import FakeElm327

import canopen_studio.diag.mcp_tools as tools
import canopen_studio.mcp_server as mcp_server
from canopen_studio.diag.security import AGENT_WRITE_ENABLED_ENV, WRITE_ENABLED_ENV
from canopen_studio.diag.elm327.interface import ElmDiagnosticInterface
from canopen_studio.diag.j1979.client import J1979Client
from canopen_studio.diag.profiles.library import ProfileLibrary
from canopen_studio.diag.profiles.resolver import ProfileResolver

VIN = "1HGBH41JXMN109186"

ECUS = {
    # Supported PIDs 01-20: 0x04, 0x05, 0x0C and 0x0D.
    "0100": {0x7E8: bytes([0x41, 0x00, 0x18, 0x18, 0x00, 0x00])},
    "010C": {0x7E8: bytes([0x41, 0x0C, 0x1A, 0xF8])},
    "010D": {0x7E8: bytes([0x41, 0x0D, 0x64])},
    "0105": {0x7E8: bytes([0x41, 0x05, 0x7B])},
    "0902": {0x7E8: bytes([0x49, 0x02, 0x01]) + VIN.encode()},
    "090A": {0x7E8: bytes([0x49, 0x0A]) + b"ECM-EngineControl"},
    "03": {0x7E8: bytes([0x43, 0x01, 0x01, 0x43])},
    "07": {0x7E8: bytes([0x47, 0x00])},
    "0A": {0x7E8: bytes([0x4A, 0x00])},
}


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Every test starts with no session and both write switches closed."""
    monkeypatch.delenv(WRITE_ENABLED_ENV, raising=False)
    monkeypatch.delenv(AGENT_WRITE_ENABLED_ENV, raising=False)
    tools._reset_state()
    tools.set_app(None)
    yield
    tools._reset_state()
    tools.set_app(None)


@pytest.fixture
def writes_allowed(monkeypatch):
    """Both switches open, as an operator who deliberately enabled agent writes."""
    monkeypatch.setenv(WRITE_ENABLED_ENV, "1")
    monkeypatch.setenv(AGENT_WRITE_ENABLED_ENV, "1")


@pytest.fixture
def session(monkeypatch):
    """A connected diagnostic session over the fake ELM327."""
    adapter = FakeElm327(ecus=ECUS)
    interface = ElmDiagnosticInterface(adapter)
    interface.default_timeout = 1.0
    interface.open()

    library = ProfileLibrary().load()
    client = J1979Client(interface)
    match = ProfileResolver(library).resolve(client.identify())
    client.table = match.profile.table
    client.dtc_descriptions = dict(match.profile.dtc_descriptions)

    monkeypatch.setattr(tools, "_session", interface)
    monkeypatch.setattr(tools, "_client", client)
    monkeypatch.setattr(tools, "_match", match)
    return interface


class TestRegistration:
    def test_the_tools_join_the_existing_server(self):
        """One server, one port, one set of guards — not a second server."""
        assert "obd_read_pid" in mcp_server.DIAGNOSTIC_TOOLS

    def test_every_tool_is_registered(self):
        assert len(mcp_server.DIAGNOSTIC_TOOLS) == len(tools.TOOLS)

    def test_tool_names_follow_the_existing_convention(self):
        for name in mcp_server.DIAGNOSTIC_TOOLS:
            assert name.islower()
            assert name.startswith("obd_")

    def test_the_write_tool_is_registered_like_the_others(self):
        """Registered unconditionally, so a refusal is a message an agent can reason about."""
        assert "obd_clear_dtcs" in mcp_server.DIAGNOSTIC_TOOLS

    def test_every_tool_documents_itself(self):
        """The docstring is the schema an agent reads."""
        for tool in tools.TOOLS:
            assert tool.__doc__ and tool.__doc__.strip()

    def test_the_can_tools_are_untouched(self):
        assert hasattr(mcp_server, "send_frame")
        assert hasattr(mcp_server, "get_status")


class TestWithoutASession:
    def test_status_says_nothing_is_connected(self):
        assert tools.obd_status()["connected"] is False

    def test_status_still_reports_the_profiles_available(self):
        assert tools.obd_status()["profiles_available"] > 0

    def test_disconnecting_when_idle_is_harmless(self):
        assert tools.obd_disconnect() == "No diagnostic session."

    @pytest.mark.parametrize(
        "call",
        [
            lambda: tools.obd_read_pid("0C"),
            lambda: tools.obd_read_dtcs(),
            lambda: tools.obd_read_vin(),
            lambda: tools.obd_identify_vehicle(),
            lambda: tools.obd_list_supported_pids(),
        ],
    )
    def test_reading_without_a_session_says_to_connect(self, call):
        with pytest.raises(Exception) as excinfo:
            call()

        assert "obd_connect" in str(excinfo.value)

    def test_the_profiles_can_be_listed_without_a_vehicle(self):
        listing = tools.obd_list_profiles()

        assert listing["count"] > 0
        assert any(profile["id"] == "j1979_base" for profile in listing["profiles"])


class TestConnecting:
    def test_an_unknown_transport_is_reported(self):
        result = tools.obd_connect(transport="carrier pigeon")

        assert result["connected"] is False
        assert "unknown transport" in result["error"]

    def test_a_failure_leaves_no_session_behind(self):
        tools.obd_connect(transport="elm327", port="/dev/does-not-exist")

        assert tools.obd_status()["connected"] is False

    def test_a_second_connect_is_refused_while_one_is_open(self, session):
        assert "obd_disconnect" in tools.obd_connect()["message"]

    def test_disconnecting_closes_the_session(self, session):
        tools.obd_disconnect()

        assert session.is_open is False
        assert tools.obd_status()["connected"] is False


class TestStatus:
    def test_a_connected_session_reports_its_link(self, session):
        assert "ELM327" in tools.obd_status()["link"]

    def test_the_active_profile_is_reported(self, session):
        assert tools.obd_status()["profile"]["profile"]["id"] == "j1979_base"

    def test_the_write_posture_is_reported(self, session):
        """An agent should be able to see that writing is off, and why."""
        writes = tools.obd_status()["writes"]

        assert writes["enabled"] is False
        assert writes["agent_writes_enabled"] is False
        assert "WriteDataByIdentifier" in writes["refused_services"]

    def test_the_status_carries_a_warning_once_agents_may_write(self, session, writes_allowed):
        writes = tools.obd_status()["writes"]

        assert writes["agent_writes_enabled"] is True
        assert "readiness monitors" in writes["warning"]

    def test_the_status_carries_no_warning_while_writes_are_off(self, session):
        assert "warning" not in tools.obd_status()["writes"]


class TestReadingPids:
    def test_supported_pids_are_listed(self, session):
        listing = tools.obd_list_supported_pids()

        assert listing["count"] > 0
        assert any(entry["pid"] == "01:0C" for entry in listing["pids"])

    def test_the_listing_names_the_pids_the_profile_describes(self, session):
        listing = tools.obd_list_supported_pids()

        entry = next(entry for entry in listing["pids"] if entry["pid"] == "01:0C")
        assert entry["name"] == "engine_speed"
        assert entry["unit"] == "rpm"

    def test_the_listing_says_which_ecu_implements_each_pid(self, session):
        listing = tools.obd_list_supported_pids()

        assert next(entry for entry in listing["pids"] if entry["pid"] == "01:0C")["ecus"] == ["0x7E8"]

    def test_a_pid_reads_by_bare_identifier(self, session):
        result = tools.obd_read_pid("0C")

        assert result["supported"] is True
        assert result["readings"][0]["value"] == pytest.approx(1726.0)

    def test_a_pid_reads_by_full_key(self, session):
        assert tools.obd_read_pid("01:0C")["supported"] is True

    def test_a_pid_reads_by_name(self, session):
        result = tools.obd_read_pid("engine_speed")

        assert result["readings"][0]["name"] == "engine_speed"

    def test_the_answering_ecu_is_reported(self, session):
        assert tools.obd_read_pid("0C")["readings"][0]["ecu"] == "0x7E8"

    def test_an_unsupported_pid_says_so_rather_than_failing(self, session):
        result = tools.obd_read_pid("1F")

        assert result["supported"] is False
        assert "unsupported PID" in result["message"]

    def test_an_unreadable_pid_reference_is_reported(self, session):
        assert "error" in tools.obd_read_pid("not-a-pid")

    def test_mode_nine_can_be_enumerated(self, session):
        assert tools.obd_list_supported_pids(mode=9)["mode"] == "09"


class TestReadingTroubleCodes:
    def test_stored_codes_are_read(self, session):
        result = tools.obd_read_dtcs()

        assert result["count"] == 1
        assert result["codes"][0]["code"] == "P0143"

    def test_the_system_is_reported(self, session):
        assert tools.obd_read_dtcs()["codes"][0]["system"] == "Powertrain"

    def test_pending_codes_can_be_read(self, session):
        assert tools.obd_read_dtcs("pending")["count"] == 0

    def test_every_kind_can_be_read_at_once(self, session):
        assert tools.obd_read_dtcs("all")["count"] == 1

    def test_an_unknown_kind_is_reported(self, session):
        assert "error" in tools.obd_read_dtcs("historical")

    def test_the_result_says_clearing_is_not_available(self, session):
        """An agent asking to clear should learn why it cannot, from the read itself."""
        clearing = tools.obd_read_dtcs()["clearing"]

        assert "not available" in clearing
        assert AGENT_WRITE_ENABLED_ENV in clearing

    def test_the_result_warns_when_clearing_is_available(self, session, writes_allowed):
        assert "WARNING" in tools.obd_read_dtcs()["clearing"]


class TestVehicleIdentity:
    def test_the_vin_is_read(self, session):
        assert tools.obd_read_vin()["vin"] == VIN

    def test_the_vin_is_decoded(self, session):
        details = tools.obd_read_vin()["details"]

        assert details["wmi"] == "1HG"
        assert details["region"] == "North America"

    def test_a_vehicle_that_withholds_its_vin_says_so(self, monkeypatch):
        adapter = FakeElm327(ecus={"0100": {0x7E8: bytes([0x41, 0x00, 0x18, 0x18, 0x00, 0x00])}})
        interface = ElmDiagnosticInterface(adapter)
        interface.default_timeout = 1.0
        interface.open()
        monkeypatch.setattr(tools, "_session", interface)
        monkeypatch.setattr(tools, "_client", J1979Client(interface))

        assert tools.obd_read_vin()["vin"] is None

    def test_the_vehicle_identifies_itself(self, session):
        result = tools.obd_identify_vehicle()

        assert result["vehicle"]["vin"] == VIN
        assert result["vehicle"]["ecus"] == ["0x7E8"]

    def test_the_ecu_name_is_reported(self, session):
        assert tools.obd_identify_vehicle()["vehicle"]["ecu_names"] == {"0x7E8": "ECM-EngineControl"}

    def test_identification_reports_the_profile_it_resolves_to(self, session):
        result = tools.obd_identify_vehicle()

        assert result["profile"]["stage"] == "fallback"
        assert result["profile"]["profile"]["generic"] is True


def _flatten(text: str) -> str:
    """Collapse whitespace, so an assertion is not hostage to where a line wraps."""
    return " ".join(str(text).split())


class TestOnlyOneToolWrites:
    """Everything else reads, and the one that does not is identified as such."""

    FORBIDDEN = ("write", "erase", "reset", "routine", "control")

    def test_exactly_one_tool_can_change_the_vehicle(self):
        assert tools.WRITE_TOOLS == (tools.obd_clear_dtcs,)

    def test_no_read_tool_name_suggests_a_write(self):
        for tool in tools.READ_TOOLS:
            assert not any(word in tool.__name__ for word in self.FORBIDDEN), tool.__name__

    def test_no_read_tool_reaches_the_clear_service(self):
        for tool in tools.READ_TOOLS:
            constants = [c for c in tool.__code__.co_consts if isinstance(c, str)]
            assert "clear_trouble_codes" not in constants, tool.__name__

    def test_the_write_tool_describes_its_consequence(self):
        """The docstring is the only thing a model reads before deciding to call it."""
        doc = _flatten(tools.obd_clear_dtcs.__doc__)

        assert "DESTRUCTIVE" in doc
        assert "readiness monitors" in doc
        assert "Ask the person first" in doc

    def test_the_write_tool_tells_the_model_not_to_act_alone(self):
        assert "on your own initiative" in _flatten(tools.obd_clear_dtcs.__doc__)

    def test_the_server_instructions_warn_about_it(self):
        instructions = _flatten(mcp_server.mcp.instructions)

        assert "obd_clear_dtcs" in instructions
        assert "Ask the person first" in instructions


class TestClearingIsGated:
    """The whole point of the specific configuration: it is shut unless both are open."""

    def test_nothing_is_sent_with_both_switches_closed(self, session):
        result = tools.obd_clear_dtcs(confirm=True)

        assert result["cleared"] is False
        assert result["refused"] is True

    def test_the_refusal_names_the_process_switch_first(self, session):
        assert WRITE_ENABLED_ENV in tools.obd_clear_dtcs(confirm=True)["reason"]

    def test_the_process_switch_alone_is_not_enough(self, session, monkeypatch):
        """Enabling writes for the GUI must not hand the capability to an agent."""
        monkeypatch.setenv(WRITE_ENABLED_ENV, "1")

        result = tools.obd_clear_dtcs(confirm=True)

        assert result["refused"] is True
        assert AGENT_WRITE_ENABLED_ENV in result["reason"]

    def test_the_agent_switch_alone_is_not_enough(self, session, monkeypatch):
        monkeypatch.setenv(AGENT_WRITE_ENABLED_ENV, "1")

        result = tools.obd_clear_dtcs(confirm=True)

        assert result["refused"] is True
        assert WRITE_ENABLED_ENV in result["reason"]

    def test_confirmation_is_still_required_with_both_switches_open(self, session, writes_allowed):
        result = tools.obd_clear_dtcs()

        assert result["refused"] is True
        assert "confirm=True" in result["reason"]

    def test_a_refusal_transmits_nothing(self, session, monkeypatch):
        """The property that matters: a refused clear never reaches the vehicle."""
        sent = []
        monkeypatch.setattr(session, "_request", lambda payload, timeout: sent.append(payload) or [])

        tools.obd_clear_dtcs(confirm=True)

        assert sent == []

    def test_a_confirmed_clear_is_sent_when_both_switches_are_open(self, session, writes_allowed):
        result = tools.obd_clear_dtcs(confirm=True)

        assert result["cleared"] is True

    def test_the_result_states_the_consequence(self, session, writes_allowed):
        assert "readiness monitors" in tools.obd_clear_dtcs(confirm=True)["consequence"]

    def test_the_result_reads_the_codes_back_from_the_vehicle(self, session, writes_allowed):
        """Reporting the vehicle's state rather than the request's success."""
        assert "remaining_codes" in tools.obd_clear_dtcs(confirm=True)

    def test_clearing_without_a_session_is_refused(self):
        with pytest.raises(Exception) as excinfo:
            tools.obd_clear_dtcs(confirm=True)

        assert "obd_connect" in str(excinfo.value)


class TestStartupNotice:
    def test_nothing_is_announced_while_agent_writes_are_off(self):
        assert tools.startup_notice() is None

    def test_the_capability_is_announced_at_startup(self, writes_allowed):
        """Nobody should discover it by watching a model use it."""
        notice = tools.startup_notice()

        assert notice and AGENT_WRITE_ENABLED_ENV in notice

    def test_the_server_surfaces_the_same_notice(self, writes_allowed):
        assert mcp_server.diagnostic_write_notice() == tools.startup_notice()
