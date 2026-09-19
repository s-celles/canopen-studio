"""
Unit tests for the high-level OBD-II session.

The point of this layer is that it is written once and works over either backend, so the
last class runs the same assertions against a native ISO-TP session and an ELM327 session
answering from the same scripted ECU.
"""

import pytest
from elm327_fake import FakeElm327

from canopen_studio.diag import DiagnosticInterface, DiagnosticResponse
from canopen_studio.diag.elm327.interface import ElmDiagnosticInterface
from canopen_studio.diag.j1979.client import J1979Client, VehicleIdentity
from canopen_studio.diag.j1979.pids import PidTable

VIN = "1HGBH41JXMN109186"

TABLE = PidTable.from_mapping(
    {
        "01:0C": {"name": "engine_speed", "formula": "(256 * A + B) / 4", "unit": "rpm", "bytes": 2},
        "01:05": {"name": "coolant_temperature", "formula": "A - 40", "unit": "°C", "bytes": 1},
        "01:1C": {"name": "obd_standard", "values": {6: "EOBD"}, "bytes": 1},
        "09:02": {"name": "vin", "ascii": True},
    }
)


class ScriptedInterface(DiagnosticInterface):
    """Replays canned answers, keyed by the request bytes."""

    def __init__(self, script=None):
        super().__init__()
        self.script = {bytes(k): v for k, v in (script or {}).items()}
        self.requests = []
        self.open()

    @property
    def description(self):
        return "scripted"

    def _open(self):
        pass

    def _close(self):
        pass

    def _request(self, payload, timeout):
        self.requests.append(payload)
        return [DiagnosticResponse(source=source, data=data) for source, data in self.script.get(payload, [])]


def client(script=None, table=TABLE, **kwargs):
    return J1979Client(ScriptedInterface(script), table=table, **kwargs)


class TestReadingPids:
    def test_a_pid_decodes_through_the_table(self):
        session = client({b"\x01\x0c": [(0x7E8, bytes([0x41, 0x0C, 0x1A, 0xF8]))]})

        readings = session.read_pid(0x0C)

        assert readings[0].name == "engine_speed"
        assert readings[0].value == pytest.approx(1726.0)

    def test_the_answering_ecu_is_reported(self):
        session = client({b"\x01\x0c": [(0x7E8, bytes([0x41, 0x0C, 0x1A, 0xF8]))]})

        assert session.read_pid(0x0C)[0].source == 0x7E8

    def test_every_answering_ecu_is_reported(self):
        script = {b"\x01\x0c": [(0x7E8, bytes([0x41, 0x0C, 0x1A, 0xF8])), (0x7E9, bytes([0x41, 0x0C, 0x00, 0x00]))]}
        session = client(script)

        assert [r.source for r in session.read_pid(0x0C)] == [0x7E8, 0x7E9]

    def test_an_unsupported_pid_returns_nothing_rather_than_raising(self):
        assert client().read_pid(0x0C) == []

    def test_a_pid_the_table_does_not_describe_still_comes_back_as_bytes(self):
        """Knowing the ECU answered is worth more than a clean failure."""
        session = client({b"\x01\xff": [(0x7E8, bytes([0x41, 0xFF, 0xDE, 0xAD]))]})

        readings = session.read_pid(0xFF)

        assert readings[0].value == b"\xde\xad"

    def test_a_reply_echoing_another_pid_is_discarded(self):
        """A late answer to an earlier request would decode to a plausible wrong value."""
        session = client({b"\x01\x0c": [(0x7E8, bytes([0x41, 0x0D, 0x50]))]})

        assert session.read_pid(0x0C) == []

    def test_a_reply_too_short_for_its_formula_falls_back_to_raw_bytes(self):
        session = client({b"\x01\x0c": [(0x7E8, bytes([0x41, 0x0C, 0x1A]))]})

        readings = session.read_pid(0x0C)

        assert readings[0].value == b"\x1a"

    def test_a_pid_can_be_read_by_name(self):
        session = client({b"\x01\x05": [(0x7E8, bytes([0x41, 0x05, 0x7B]))]})

        assert session.read("coolant_temperature").value == 83

    def test_an_unknown_name_yields_nothing(self):
        assert client().read("nonexistent") is None

    def test_a_known_name_that_does_not_answer_yields_nothing(self):
        assert client().read("coolant_temperature") is None

    def test_the_definition_for_a_pid_can_be_looked_up(self):
        assert client().describes(0x0C).name == "engine_speed"

    def test_an_undescribed_pid_looks_up_to_nothing(self):
        assert client().describes(0xFF) is None


class TestCapabilities:
    def test_supported_pids_are_discovered(self):
        session = client({b"\x01\x00": [(0x7E8, bytes([0x41, 0x00, 0x18, 0x00, 0x00, 0x00]))]})

        assert set(session.supported_pids()) == {0x00, 0x04, 0x05}

    def test_the_result_is_cached(self):
        """A vehicle's capabilities do not change while the ignition is on."""
        session = client({b"\x01\x00": [(0x7E8, bytes([0x41, 0x00, 0x18, 0x00, 0x00, 0x00]))]})
        session.supported_pids()

        session.supported_pids()

        assert session.interface.requests.count(b"\x01\x00") == 1

    def test_the_cache_can_be_refreshed(self):
        session = client({b"\x01\x00": [(0x7E8, bytes([0x41, 0x00, 0x18, 0x00, 0x00, 0x00]))]})
        session.supported_pids()

        session.supported_pids(refresh=True)

        assert session.interface.requests.count(b"\x01\x00") == 2


class TestScanning:
    def test_every_supported_pid_is_read(self):
        script = {
            b"\x01\x00": [(0x7E8, bytes([0x41, 0x00, 0x08, 0x00, 0x00, 0x00]))],  # PID 05 only
            b"\x01\x05": [(0x7E8, bytes([0x41, 0x05, 0x7B]))],
        }
        session = client(script)

        readings = session.scan()

        assert [r.name for r in readings] == ["coolant_temperature"]

    def test_the_bitmask_pids_themselves_are_skipped(self):
        """They describe capabilities, not state, and discovery already read them."""
        script = {b"\x01\x00": [(0x7E8, bytes([0x41, 0x00, 0x08, 0x00, 0x00, 0x00]))]}
        session = client(script)

        session.scan()

        assert session.interface.requests.count(b"\x01\x00") == 1

    def test_a_vehicle_that_answers_nothing_scans_to_nothing(self):
        assert client().scan() == []


class TestTroubleCodes:
    def test_stored_codes_are_read_from_mode_03(self):
        session = client({b"\x03": [(0x7E8, bytes([0x43, 0x01, 0x01, 0x43]))]})

        codes = session.read_dtcs()

        assert [code.code for code in codes] == ["P0143"]
        assert codes[0].kind == "stored"

    def test_pending_codes_are_read_from_mode_07(self):
        session = client({b"\x07": [(0x7E8, bytes([0x47, 0x01, 0x01, 0x96]))]})

        assert session.read_dtcs("pending")[0].code == "P0196"

    def test_permanent_codes_are_read_from_mode_0a(self):
        session = client({b"\x0a": [(0x7E8, bytes([0x4A, 0x01, 0xC1, 0x00]))]})

        assert session.read_dtcs("permanent")[0].code == "U0100"

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(ValueError):
            client().read_dtcs("historical")

    def test_a_clean_vehicle_reports_no_codes(self):
        session = client({b"\x03": [(0x7E8, bytes([0x43, 0x00]))]})

        assert session.read_dtcs() == []

    def test_codes_from_several_ecus_are_gathered(self):
        script = {b"\x03": [(0x7E8, bytes([0x43, 0x01, 0x01, 0x43])), (0x7E9, bytes([0x43, 0x01, 0x41, 0x00]))]}
        session = client(script)

        assert [code.code for code in session.read_dtcs()] == ["P0143", "C0100"]

    def test_profile_descriptions_are_applied(self):
        session = client(
            {b"\x03": [(0x7E8, bytes([0x43, 0x01, 0x01, 0x43]))]},
            dtc_descriptions={"P0143": "O2 sensor circuit low voltage"},
        )

        assert session.read_dtcs()[0].description == "O2 sensor circuit low voltage"

    def test_every_kind_can_be_read_in_one_pass(self):
        script = {
            b"\x03": [(0x7E8, bytes([0x43, 0x01, 0x01, 0x43]))],
            b"\x07": [(0x7E8, bytes([0x47, 0x01, 0x01, 0x96]))],
            b"\x0a": [(0x7E8, bytes([0x4A, 0x00]))],
        }

        codes = client(script).read_all_dtcs()

        assert {code.code for code in codes} == {"P0143", "P0196"}

    def test_a_summary_counts_by_kind_and_system(self):
        script = {
            b"\x03": [(0x7E8, bytes([0x43, 0x01, 0x01, 0x43]))],
            b"\x07": [(0x7E8, bytes([0x47, 0x01, 0xC1, 0x00]))],
        }

        summary = client(script).dtc_summary()

        assert summary["total"] == 2
        assert summary["by_system"] == {"Powertrain": 1, "Network": 1}


class TestVehicleInformation:
    def test_the_vin_is_read(self):
        script = {b"\x09\x02": [(0x7E8, bytes([0x49, 0x02, 0x01]) + VIN.encode())]}

        assert client(script).read_vin() == VIN

    def test_a_vehicle_that_withholds_its_vin_returns_nothing(self):
        assert client().read_vin() is None

    def test_a_truncated_vin_returns_nothing(self):
        """A short VIN nobody notices is worse than no VIN."""
        script = {b"\x09\x02": [(0x7E8, bytes([0x49, 0x02, 0x01]) + VIN[:10].encode())]}

        assert client(script).read_vin() is None

    def test_the_ecu_name_is_read(self):
        script = {b"\x09\x0a": [(0x7E8, bytes([0x49, 0x0A]) + b"ECM-EngineControl")]}

        assert client(script).read_text_info(0x0A) == {0x7E8: "ECM-EngineControl"}

    def test_a_text_reply_echoing_another_pid_is_discarded(self):
        script = {b"\x09\x0a": [(0x7E8, bytes([0x49, 0x04]) + b"CALIBRATION")]}

        assert client(script).read_text_info(0x0A) == {}


class TestIdentify:
    SCRIPT = {
        b"\x01\x00": [
            (0x7E8, bytes([0x41, 0x00, 0x00, 0x00, 0x00, 0x10])),
            (0x7E9, bytes([0x41, 0x00, 0x18, 0, 0, 0])),
        ],
        b"\x09\x02": [(0x7E8, bytes([0x49, 0x02, 0x01]) + VIN.encode())],
        b"\x09\x0a": [(0x7E8, bytes([0x49, 0x0A]) + b"ECM")],
        b"\x09\x04": [(0x7E8, bytes([0x49, 0x04]) + b"CAL-1234")],
        b"\x01\x1c": [(0x7E8, bytes([0x41, 0x1C, 0x06]))],
    }

    def test_the_vin_is_gathered(self):
        assert client(self.SCRIPT).identify().vin == VIN

    def test_the_vin_is_parsed(self):
        identity = client(self.SCRIPT).identify()

        assert identity.vin_info.wmi == "1HG"
        assert identity.vin_info.model_year == 1991

    def test_the_answering_ecus_are_listed(self):
        assert client(self.SCRIPT).identify().ecus == [0x7E8, 0x7E9]

    def test_the_ecu_names_are_gathered(self):
        assert client(self.SCRIPT).identify().ecu_names == {0x7E8: "ECM"}

    def test_the_calibration_identifiers_are_gathered(self):
        assert client(self.SCRIPT).identify().calibration_ids == {0x7E8: "CAL-1234"}

    def test_the_obd_standard_is_gathered(self):
        assert client(self.SCRIPT).identify().obd_standard == "EOBD"

    def test_the_supported_pid_fingerprint_is_the_union(self):
        """What profile resolution falls back to when a vehicle withholds its VIN."""
        assert client(self.SCRIPT).identify().fingerprint == frozenset({0x00, 0x04, 0x05, 0x1C})

    def test_a_silent_vehicle_identifies_to_nothing_usable(self):
        identity = client().identify()

        assert identity.vin is None
        assert identity.ecus == []
        assert identity.fingerprint == frozenset()

    def test_an_identity_serialises_for_an_agent(self):
        payload = client(self.SCRIPT).identify().as_dict()

        assert payload["vin"] == VIN
        assert payload["ecus"] == ["0x7E8", "0x7E9"]
        assert payload["obd_standard"] == "EOBD"

    def test_an_identity_renders_a_summary(self):
        assert "2 ECU(s)" in str(client(self.SCRIPT).identify())

    def test_an_empty_identity_says_the_vin_is_unavailable(self):
        assert "VIN unavailable" in str(VehicleIdentity())


class TestSameApiOverBothBackends:
    """The same assertions, over a native ISO-TP session and over an ELM327."""

    ECUS = {
        "010C": {0x7E8: bytes([0x41, 0x0C, 0x1A, 0xF8])},
        "0902": {0x7E8: bytes([0x49, 0x02, 0x01]) + VIN.encode()},
        "03": {0x7E8: bytes([0x43, 0x01, 0x01, 0x43])},
    }

    @pytest.fixture(params=["scripted", "elm327"])
    def session(self, request):
        if request.param == "scripted":
            script = {
                b"\x01\x0c": [(0x7E8, bytes([0x41, 0x0C, 0x1A, 0xF8]))],
                b"\x09\x02": [(0x7E8, bytes([0x49, 0x02, 0x01]) + VIN.encode())],
                b"\x03": [(0x7E8, bytes([0x43, 0x01, 0x01, 0x43]))],
            }
            return J1979Client(ScriptedInterface(script), table=TABLE)

        interface = ElmDiagnosticInterface(FakeElm327(ecus=self.ECUS))
        interface.default_timeout = 1.0
        interface.open()
        return J1979Client(interface, table=TABLE)

    def test_a_pid_reads_the_same(self, session):
        assert session.read_pid(0x0C)[0].value == pytest.approx(1726.0)

    def test_the_vin_reads_the_same(self, session):
        assert session.read_vin() == VIN

    def test_the_trouble_codes_read_the_same(self, session):
        assert [code.code for code in session.read_dtcs()] == ["P0143"]


class TestInterfaceDelegation:
    """Both backends expose the high-level operations directly, over the same client."""

    def test_a_pid_can_be_read_straight_from_the_interface(self):
        """Without a table the bytes come back undecoded, which is still a reading."""
        interface = ScriptedInterface({b"\x01\x0c": [(0x7E8, bytes([0x41, 0x0C, 0x1A, 0xF8]))]})

        reading = interface.read_pid(0x0C)[0]

        assert reading.source == 0x7E8
        assert reading.value == b"\x1a\xf8"

    def test_the_vin_can_be_read_straight_from_the_interface(self):
        interface = ScriptedInterface({b"\x09\x02": [(0x7E8, bytes([0x49, 0x02, 0x01]) + VIN.encode())]})

        assert interface.read_vin() == VIN

    def test_the_trouble_codes_can_be_read_straight_from_the_interface(self):
        interface = ScriptedInterface({b"\x03": [(0x7E8, bytes([0x43, 0x01, 0x01, 0x43]))]})

        assert interface.read_dtcs()[0].code == "P0143"

    def test_the_client_is_created_once_and_reused(self):
        interface = ScriptedInterface()

        assert interface.j1979() is interface.j1979()

    def test_a_table_can_be_supplied_and_replaced(self):
        interface = ScriptedInterface({b"\x01\x0c": [(0x7E8, bytes([0x41, 0x0C, 0x1A, 0xF8]))]})
        interface.j1979(table=TABLE)

        assert interface.read_pid(0x0C)[0].name == "engine_speed"

    def test_identification_is_reachable_from_the_interface(self):
        interface = ScriptedInterface({b"\x09\x02": [(0x7E8, bytes([0x49, 0x02, 0x01]) + VIN.encode())]})

        assert interface.identify().vin == VIN
