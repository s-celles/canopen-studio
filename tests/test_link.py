"""
Unit tests for the single link the studio talks through.

CAN buses are replaced by recording fakes and the simulator by a stub, so nothing here
opens a socket or starts the Rust core; ELM327 links run against the in-memory fake.
"""

import threading
import time

import pytest
from elm327_fake import FakeElm327

from canopen_studio import link as link_module
from canopen_studio.diag import DiagnosticInterface, DiagnosticResponse
from canopen_studio.diag.elm327.ble import BleElmTransport
from canopen_studio.diag.elm327.transport import SerialElmTransport, TcpElmTransport
from canopen_studio.diag.native import NativeCanDiagnosticInterface, QueueFrameSource
from canopen_studio.link import (
    BLE_FIRST_FOUND,
    CAP_CANOPEN,
    CAP_OBD,
    CAP_TRACE,
    LINK_INTERFACES,
    Link,
    LinkError,
    build_elm_transport,
    is_elm327,
    link_capabilities,
)


class FakeBus:
    def __init__(self, interface, channel, bitrate, hop_limit=None):
        self.args = (interface, channel, bitrate)
        self.shut = False

    def send(self, msg):
        pass

    def recv(self, timeout=None):
        return None

    def shutdown(self):
        self.shut = True


class Opener:
    """Records every bus it opens, and can be told to fail on a given call."""

    def __init__(self, fail_on=None):
        self.buses = []
        self.fail_on = fail_on

    def __call__(self, interface, channel, bitrate, hop_limit=None):
        if self.fail_on == len(self.buses) + 1:
            raise OSError("device busy")
        bus = FakeBus(interface, channel, bitrate, hop_limit)
        self.buses.append(bus)
        return bus


class FakeSimulator:
    made: list["FakeSimulator"] = []

    def __init__(self, channel_or_bus):
        self.target = channel_or_bus
        self.started = self.stopped = False
        FakeSimulator.made.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def fake_simulator(monkeypatch):
    FakeSimulator.made = []
    monkeypatch.setattr(link_module, "VirtualCanopenSimulator", FakeSimulator)


@pytest.fixture
def fake_elm(monkeypatch):
    adapter = FakeElm327(ecus={"0100": {0x7E8: bytes([0x41, 0x00, 0x18, 0x18, 0x00, 0x00])}})
    monkeypatch.setattr(link_module, "build_elm_transport", lambda interface, channel: adapter)
    return adapter


class TestWhatALinkCanDo:
    def test_a_can_adapter_carries_everything(self):
        assert link_capabilities("slcan") == {CAP_TRACE, CAP_CANOPEN, CAP_OBD}

    def test_an_elm327_carries_only_obd(self):
        for key in ("elm327_serial", "elm327_tcp", "elm327_ble"):
            assert link_capabilities(key) == {CAP_OBD}
            assert is_elm327(key)

    def test_one_chooser_lists_can_adapters_and_elm327_adapters(self):
        assert "slcan" in LINK_INTERFACES
        assert "elm327_ble" in LINK_INTERFACES

    def test_an_elm327_names_what_it_cannot_do_and_why(self):
        reason = Link("elm327_ble", "").unavailable_reason(CAP_CANOPEN)

        assert "CANopen" in reason
        assert "CAN adapter" in reason

    def test_a_capability_the_link_has_has_no_reason(self):
        assert Link("slcan", "COM4").unavailable_reason(CAP_OBD) is None

    def test_an_unknown_adapter_is_refused(self):
        with pytest.raises(LinkError):
            Link("carrier_pigeon", "")


class TestChannels:
    def test_a_serial_elm327_takes_a_port(self):
        transport = build_elm_transport("elm327_serial", "/dev/ttyUSB0")

        assert isinstance(transport, SerialElmTransport)
        assert transport.port == "/dev/ttyUSB0"
        assert transport.baudrate == 38400

    def test_an_older_board_can_be_given_its_rate(self):
        assert build_elm_transport("elm327_serial", "COM4@9600").baudrate == 9600

    def test_a_bad_rate_is_refused(self):
        with pytest.raises(LinkError):
            build_elm_transport("elm327_serial", "COM4@fast")

    def test_a_missing_port_is_refused(self):
        with pytest.raises(LinkError):
            build_elm_transport("elm327_serial", "")

    def test_a_tcp_elm327_takes_host_and_port(self):
        transport = build_elm_transport("elm327_tcp", "10.0.0.5:35001")

        assert isinstance(transport, TcpElmTransport)
        assert (transport.host, transport.port) == ("10.0.0.5", 35001)

    def test_a_tcp_port_defaults_to_the_convention(self):
        assert build_elm_transport("elm327_tcp", "10.0.0.5").port == 35000

    def test_a_ble_elm327_left_on_the_placeholder_takes_the_first_adapter(self):
        transport = build_elm_transport("elm327_ble", BLE_FIRST_FOUND)

        assert isinstance(transport, BleElmTransport)
        assert transport.device is None

    def test_a_ble_elm327_can_be_named(self):
        assert build_elm_transport("elm327_ble", "OBDII").device == "OBDII"


class TestCanLinks:
    def test_opening_opens_one_bus(self):
        opener = Opener()
        link = Link("slcan", "COM4", 250000, opener=opener)

        link.open()

        assert link.is_open
        assert opener.buses[0].args == ("slcan", "COM4", 250000)
        assert FakeSimulator.made == []

    def test_the_virtual_adapter_always_brings_its_simulator(self):
        link = Link("virtual", "virtual_bus", opener=Opener())

        link.open()

        assert FakeSimulator.made[0].target == "virtual_bus"
        assert FakeSimulator.made[0].started

    def test_simulating_on_udp_opens_a_second_socket_for_the_simulator(self):
        opener = Opener()
        link = Link("udp_multicast", "239.0.0.1", simulate=True, opener=opener)

        link.open()

        assert len(opener.buses) == 2
        assert FakeSimulator.made[0].target is opener.buses[1]

    def test_closing_releases_the_buses_and_stops_the_simulator(self):
        opener = Opener()
        link = Link("udp_multicast", "239.0.0.1", simulate=True, opener=opener)
        link.open()

        link.close()

        assert all(bus.shut for bus in opener.buses)
        assert FakeSimulator.made[0].stopped
        assert not link.is_open

    def test_closing_twice_is_harmless(self):
        link = Link("slcan", "COM4", opener=Opener())
        link.open()
        link.close()

        link.close()

    def test_a_failure_leaves_nothing_half_open(self):
        opener = Opener(fail_on=2)
        link = Link("udp_multicast", "239.0.0.1", simulate=True, opener=opener)

        with pytest.raises(LinkError) as excinfo:
            link.open()

        assert "device busy" in str(excinfo.value)
        assert opener.buses[0].shut
        assert not link.is_open

    def test_obd_on_a_can_link_is_iso_tp_over_the_same_bus(self):
        opener = Opener()
        link = Link("slcan", "COM4", opener=opener)
        link.open()

        session, source = link.diagnostic_session()

        assert isinstance(session, NativeCanDiagnosticInterface)
        assert session.bus is opener.buses[0]
        assert isinstance(source, QueueFrameSource)
        assert session.is_open


class TestElm327Links:
    def test_opening_initialises_the_adapter(self, fake_elm):
        link = Link("elm327_ble", "")

        link.open()

        assert link.is_open
        assert link.kind == "elm327"
        assert "ATZ" in fake_elm.commands

    def test_obd_on_an_elm327_is_the_adapter_session_itself(self, fake_elm):
        link = Link("elm327_serial", "COM4")
        link.open()

        session, source = link.diagnostic_session()

        assert session is link.elm
        assert source is None

    def test_closing_releases_the_adapter(self, fake_elm):
        link = Link("elm327_tcp", "10.0.0.5")
        link.open()

        link.close()

        assert not link.is_open
        assert not fake_elm.is_open

    def test_a_link_that_is_not_open_has_no_session(self):
        with pytest.raises(LinkError):
            Link("elm327_ble", "").diagnostic_session()


class SlowInterface(DiagnosticInterface):
    """Records when each exchange starts and ends, taking a while over each."""

    def __init__(self):
        super().__init__()
        self.spans = []

    @property
    def description(self):
        return "slow"

    def _open(self):
        pass

    def _close(self):
        pass

    def _request(self, payload, timeout):
        start = time.monotonic()
        time.sleep(0.02)
        self.spans.append((start, time.monotonic()))
        return [DiagnosticResponse(source=0x7E8, data=bytes([payload[0] + 0x40]))]


class TestSharedSessions:
    def test_callers_sharing_a_session_take_turns(self):
        """The studio, a poller and an agent can all hold one ELM327 session."""
        interface = SlowInterface()
        interface.open()

        threads = [threading.Thread(target=lambda: interface.request(b"\x01\x0c")) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        spans = sorted(interface.spans)
        assert len(spans) == 4
        assert all(earlier[1] <= later[0] for earlier, later in zip(spans, spans[1:]))
