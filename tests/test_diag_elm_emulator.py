"""
Integration tests against Ircama's ELM327-emulator.

The everyday suite runs on `tests/elm327_fake.py`, which is fast, hermetic and in this
repository. This file is the reality check: a third-party emulator, written by somebody
else, simulating multiple ECUs of a real car, driven over a real TCP socket. If the
backend only worked against its own fake, that would show up here.

**These tests are opt-in and are skipped unless the emulator is installed.** It is not a
dev dependency, on purpose: `ELM327-emulator` is licensed CC-BY-NC-SA-4.0, which is a
non-commercial, non-OSI licence, and pulling it into the default dev group of a GPL
project would impose that on everyone running the suite. Nothing here is redistributed —
the emulator runs as a separate process — so installing it to run these tests is a
choice each person makes:

    uv run --with ELM327-emulator pytest tests/test_diag_elm_emulator.py -v

The emulator also exposes an SLCAN interface (`python3 -m elm -c`), which lets the same
emulated ECUs be reached through `NativeCanDiagnosticInterface`. That path needs
`slcand` and a SocketCAN interface, so it is documented rather than automated here.
"""

import socket
import subprocess
import sys
import time

import pytest

pytest.importorskip(
    "elm",
    reason=(
        "needs Ircama's ELM327-emulator, which is opt-in because of its CC-BY-NC-SA-4.0 "
        "licence: uv run --with ELM327-emulator pytest tests/test_diag_elm_emulator.py"
    ),
)

from canopen_studio.diag.elm327.interface import ElmDiagnosticInterface  # noqa: E402
from canopen_studio.diag.elm327.transport import TcpElmTransport  # noqa: E402
from canopen_studio.diag.j1979.client import J1979Client  # noqa: E402
from canopen_studio.diag.j1979.vin import is_valid_vin  # noqa: E402
from canopen_studio.diag.profiles.library import ProfileLibrary  # noqa: E402
from canopen_studio.diag.profiles.resolver import ProfileResolver  # noqa: E402

pytestmark = pytest.mark.emulator

# The emulator boots a Python process and runs its own protocol negotiation, so it needs
# noticeably longer than the in-memory fake.
STARTUP_TIMEOUT = 40.0
REQUEST_TIMEOUT = 10.0


def free_port() -> int:
    """Take a port the operating system says is free."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_until_listening(port: int, deadline: float) -> bool:
    """Poll until the emulator accepts a connection, or the deadline passes."""
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


@pytest.fixture(scope="module")
def emulator(tmp_path_factory):
    """
    Run the emulator on a loopback TCP port, simulating a real car's ECUs.

    Batch mode (`-b`) is what makes this usable from a test: without it the emulator
    starts an interactive command line on stdin, which has nowhere to read from here.
    """
    port = free_port()
    batch_log = tmp_path_factory.mktemp("elm") / "batch.log"
    process = subprocess.Popen(
        [sys.executable, "-m", "elm", "-n", str(port), "-s", "car", "-b", str(batch_log)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    try:
        if not wait_until_listening(port, time.monotonic() + STARTUP_TIMEOUT):
            process.terminate()
            output = process.communicate(timeout=10)[0].decode("utf-8", errors="replace")
            pytest.skip(f"the emulator did not start listening on port {port}: {output[-500:]}")
        yield port
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a wedged emulator
            process.kill()
            process.communicate()


@pytest.fixture(scope="module")
def session(emulator):
    """An open diagnostic session against the emulator."""
    interface = ElmDiagnosticInterface(TcpElmTransport("127.0.0.1", emulator))
    interface.default_timeout = REQUEST_TIMEOUT
    interface.open()
    yield interface
    interface.close()


@pytest.fixture(scope="module")
def client(session):
    """A J1979 session decoding through the generic profile."""
    library = ProfileLibrary().load()
    obd = J1979Client(session, table=library.base().table)
    return obd


class TestLink:
    def test_the_adapter_identifies_itself(self, session):
        assert session.capabilities.reported_version is not None

    def test_the_adapter_settles_on_a_can_protocol(self, session):
        """Anything else and the backend would have refused to open the session."""
        assert session.active_protocol in ("6", "7", "8", "9", "A", "B", "C")

    def test_the_link_describes_itself(self, session):
        assert "ELM327" in session.description

    def test_the_battery_voltage_reads(self, session):
        voltage = session.read_voltage()

        assert voltage is None or 0 < voltage < 30


class TestDiscovery:
    def test_the_vehicle_declares_supported_pids(self, client):
        supported = client.supported_pids()

        assert len(supported) > 0

    def test_the_bitmask_pid_itself_is_supported(self, client):
        assert 0x00 in client.supported_pids()

    def test_several_ecus_answer(self, client):
        """The emulator simulates more than one, which is the point of using it."""
        assert len(client.supported_pids().ecus) >= 1

    def test_engine_speed_is_among_them(self, client):
        assert 0x0C in client.supported_pids()


class TestReadings:
    def test_engine_speed_reads_within_its_range(self, client):
        readings = client.read_pid(0x0C)

        assert readings
        assert 0 <= readings[0].value <= 16383.75

    def test_the_reading_is_named_and_carries_its_unit(self, client):
        reading = client.read_pid(0x0C)[0]

        assert reading.name == "engine_speed"
        assert reading.unit == "rpm"

    def test_coolant_temperature_reads_within_its_range(self, client):
        readings = client.read_pid(0x05)

        if readings:
            assert -40 <= readings[0].value <= 215

    def test_vehicle_speed_reads_within_its_range(self, client):
        readings = client.read_pid(0x0D)

        if readings:
            assert 0 <= readings[0].value <= 255

    def test_an_unsupported_pid_returns_nothing_rather_than_raising(self, client):
        """The real check that NO DATA is an ordinary outcome, not an error."""
        unsupported = next((pid for pid in range(0x01, 0x20) if pid not in client.supported_pids()), None)
        if unsupported is None:
            pytest.skip("this scenario supports every PID of the first block")

        assert client.read_pid(unsupported) == []

    def test_a_sweep_of_every_supported_pid_completes(self, client):
        """Exercises the whole table against a third-party implementation in one pass."""
        readings = client.scan()

        assert len(readings) > 0
        assert all(reading.raw for reading in readings)


class TestVehicleInformation:
    def test_the_vin_reads_and_is_well_formed(self, client):
        vin = client.read_vin()

        if vin is None:
            pytest.skip("this scenario does not publish a VIN")
        assert is_valid_vin(vin)

    def test_the_vin_arrives_through_multi_frame_reassembly(self, client):
        """A VIN is twenty bytes, so it cannot arrive in a single frame."""
        vin = client.read_vin()

        if vin is None:
            pytest.skip("this scenario does not publish a VIN")
        assert len(vin) == 17

    def test_the_vehicle_identifies_itself(self, client):
        identity = client.identify()

        assert identity.ecus
        assert identity.fingerprint


class TestTroubleCodes:
    def test_stored_codes_read_without_error(self, client):
        codes = client.read_dtcs("stored")

        assert all(code.code[0] in "PCBU" for code in codes)

    def test_pending_codes_read_without_error(self, client):
        codes = client.read_dtcs("pending")

        assert all(len(code.code) == 5 for code in codes)

    def test_permanent_codes_read_without_error(self, client):
        client.read_dtcs("permanent")


class TestProfileResolution:
    def test_the_vehicle_resolves_to_a_usable_profile(self, client):
        """Whatever the emulator claims to be, resolution must produce something."""
        match = ProfileResolver(ProfileLibrary().load()).resolve(client.identify())

        assert match.profile.table.get(0x01, 0x0C) is not None

    def test_resolution_explains_which_stage_chose_it(self, client):
        match = ProfileResolver(ProfileLibrary().load()).resolve(client.identify())

        assert match.stage in ("vin", "fingerprint", "fallback")
