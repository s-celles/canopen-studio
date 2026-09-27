"""
Unit tests for the Bluetooth LE ELM327 transport.

`bleak` is replaced by a stand-in that plays an adapter: it advertises, offers a GATT
table, and answers every write by notifying the reply, as a real ELM327 does. That keeps
the tests independent of a radio and of `bleak` being installed at all.
"""

from types import SimpleNamespace

import pytest

from canopen_studio.diag import TransportError
from canopen_studio.diag.elm327 import ble
from canopen_studio.diag.elm327.ble import (
    BleElmTransport,
    looks_like_address,
    pick_characteristics,
    scan_ble_adapters,
)
from canopen_studio.diag.elm327.protocol import ElmProtocol
from canopen_studio.diag.elm327.transport import ElmTransport

FFF0 = "0000fff0-0000-1000-8000-00805f9b34fb"
FFF1 = "0000fff1-0000-1000-8000-00805f9b34fb"
FFF2 = "0000fff2-0000-1000-8000-00805f9b34fb"
GAP = "00001800-0000-1000-8000-00805f9b34fb"


def char(uuid, *properties, mtu=0):
    return SimpleNamespace(uuid=uuid, properties=list(properties), max_write_without_response_size=mtu)


def service(uuid, *characteristics):
    return SimpleNamespace(uuid=uuid, characteristics=list(characteristics))


class Services(list):
    def get_characteristic(self, uuid):
        for s in self:
            for c in s.characteristics:
                if c.uuid == uuid:
                    return c
        return None


def fff0_table(write_props=("write-without-response", "write")):
    return Services(
        [
            service(GAP, char("00002a00-0000-1000-8000-00805f9b34fb", "read")),
            service(FFF0, char(FFF1, "notify"), char(FFF2, *write_props)),
        ]
    )


class FakeAdapter:
    """What the fake radio can see: one device, its advertisement and its GATT table."""

    def __init__(self, name="OBDII", address="AA:BB:CC:DD:EE:FF", services=None, advertised=(FFF0,)):
        self.device = SimpleNamespace(address=address, name=name)
        self.advertisement = SimpleNamespace(local_name=name, service_uuids=list(advertised))
        self.services = services if services is not None else fff0_table()
        self.writes = []
        self.connected = False
        self.client = None


def install(monkeypatch, *adapters):
    """Replace bleak with fakes serving `adapters`, and return nothing needed by callers."""

    class FakeScanner:
        @staticmethod
        async def discover(timeout=5.0, return_adv=False):
            return {a.device.address: (a.device, a.advertisement) for a in adapters}

        @staticmethod
        async def find_device_by_address(address, timeout=10.0):
            for a in adapters:
                if a.device.address.lower() == address.lower():
                    return a.device
            return None

    class FakeClient:
        def __init__(self, target, disconnected_callback=None, timeout=10.0):
            self.adapter = next(a for a in adapters if a.device is target)
            self.adapter.client = self
            self.on_disconnect = disconnected_callback
            self.callback = None
            self.write_args = []

        async def connect(self):
            self.adapter.connected = True

        @property
        def services(self):
            return self.adapter.services

        async def start_notify(self, uuid, callback):
            self.notify_uuid = uuid
            self.callback = callback

        async def write_gatt_char(self, uuid, data, response=None):
            self.write_args.append((uuid, bytes(data), response))
            self.adapter.writes.append(bytes(data))
            if bytes(data).endswith(b"\r"):
                command = b"".join(self.adapter.writes).strip()
                self.adapter.writes.clear()
                self.callback(None, bytearray(command + b"\rOK\r\r>"))

        async def disconnect(self):
            self.adapter.connected = False

        def drop(self):
            self.adapter.connected = False
            self.on_disconnect(self)

    monkeypatch.setattr(ble, "_load_bleak", lambda: (FakeScanner, FakeClient))


@pytest.fixture
def adapter(monkeypatch):
    a = FakeAdapter()
    install(monkeypatch, a)
    return a


class TestCharacteristicSelection:
    def test_the_generic_fff0_layout_is_recognised(self):
        notify, write, layout = pick_characteristics(fff0_table())

        assert (notify, write) == (FFF1, FFF2)
        assert "FFF0" in layout

    def test_an_hm10_module_uses_one_characteristic_both_ways(self):
        ffe1 = "0000ffe1-0000-1000-8000-00805f9b34fb"
        services = [service("0000ffe0-0000-1000-8000-00805f9b34fb", char(ffe1, "notify", "write"))]

        assert pick_characteristics(services)[:2] == (ffe1, ffe1)

    def test_the_nordic_uart_service_is_recognised(self):
        base = "6e40000{}-b5a3-f393-e0a9-e50e24dcca9e"
        services = [service(base.format(1), char(base.format(2), "write"), char(base.format(3), "notify"))]

        assert pick_characteristics(services)[:2] == (base.format(3), base.format(2))

    def test_an_unknown_vendor_with_one_way_in_and_out_is_accepted(self):
        custom = "12345678-0000-1000-8000-00805f9b34fb"
        services = [service(GAP, char("a", "read")), service(custom, char("rx", "notify"), char("tx", "write"))]

        assert pick_characteristics(services)[:2] == ("rx", "tx")

    def test_an_ambiguous_custom_service_is_refused_rather_than_guessed(self):
        custom = "12345678-0000-1000-8000-00805f9b34fb"
        services = [service(custom, char("a", "notify"), char("b", "notify"), char("c", "write"))]

        with pytest.raises(TransportError) as excinfo:
            pick_characteristics(services)

        assert custom in str(excinfo.value)


class TestAddresses:
    def test_a_mac_address_is_an_address(self):
        assert looks_like_address("AA:BB:CC:DD:EE:FF")

    def test_a_macos_device_uuid_is_an_address(self):
        assert looks_like_address("6F1C2D3E-1111-2222-3333-444455556666")

    def test_an_advertised_name_is_not_an_address(self):
        assert not looks_like_address("OBDII")


class TestBleTransport:
    def test_it_is_an_elm_transport(self):
        assert isinstance(BleElmTransport(), ElmTransport)

    def test_transport_starts_closed(self):
        assert BleElmTransport().is_open is False

    def test_without_a_device_the_first_obd_adapter_is_taken(self, monkeypatch):
        other = FakeAdapter(name="Headphones", address="11:11:11:11:11:11", advertised=())
        wanted = FakeAdapter()
        install(monkeypatch, other, wanted)

        with BleElmTransport(scan_timeout=0.1) as transport:
            assert transport.address == wanted.device.address

    def test_a_name_fragment_selects_the_matching_device(self, monkeypatch):
        first = FakeAdapter(name="OBDII", address="11:11:11:11:11:11")
        second = FakeAdapter(name="Vgate iCar Pro", address="22:22:22:22:22:22")
        install(monkeypatch, first, second)

        with BleElmTransport("icar", scan_timeout=0.1) as transport:
            assert transport.address == "22:22:22:22:22:22"

    def test_an_address_is_looked_up_directly(self, adapter):
        with BleElmTransport("aa:bb:cc:dd:ee:ff", scan_timeout=0.1) as transport:
            assert transport.is_open is True
            assert adapter.connected is True

    def test_a_missing_device_says_what_was_seen(self, adapter):
        with pytest.raises(TransportError) as excinfo:
            BleElmTransport("nonexistent", scan_timeout=0.1).open()

        assert "OBDII" in str(excinfo.value)

    def test_a_device_without_a_data_channel_is_disconnected_and_refused(self, monkeypatch):
        bare = FakeAdapter(services=Services([service(GAP, char("x", "read"))]))
        install(monkeypatch, bare)

        with pytest.raises(TransportError):
            BleElmTransport(scan_timeout=0.1).open()

        assert bare.connected is False

    def test_a_command_written_comes_back_as_notified_bytes(self, adapter):
        with BleElmTransport(scan_timeout=0.1) as transport:
            transport.write(b"ATE0\r")

            assert transport.read(1.0) == b"ATE0\rOK\r\r>"

    def test_long_commands_are_split_to_the_att_payload(self, adapter):
        with BleElmTransport(scan_timeout=0.1) as transport:
            transport.write(b"A" * 45 + b"\r")

            sizes = [len(data) for _, data, _ in adapter.client.write_args]
            assert sizes == [20, 20, 6]

    def test_write_without_response_is_used_when_offered(self, adapter):
        with BleElmTransport(scan_timeout=0.1) as transport:
            transport.write(b"ATI\r")

            assert adapter.client.write_args[0][2] is False

    def test_write_with_response_is_used_when_that_is_all_there_is(self, monkeypatch):
        strict = FakeAdapter(services=fff0_table(write_props=("write",)))
        install(monkeypatch, strict)

        with BleElmTransport(scan_timeout=0.1) as transport:
            transport.write(b"ATI\r")

            assert strict.client.write_args[0][2] is True

    def test_a_quiet_link_reads_nothing(self, adapter):
        with BleElmTransport(scan_timeout=0.1) as transport:
            assert transport.read(0.01) == b""

    def test_reset_input_buffer_drops_pending_bytes(self, adapter):
        with BleElmTransport(scan_timeout=0.1) as transport:
            transport.write(b"ATI\r")
            transport.reset_input_buffer()

            assert transport.read(0.01) == b""

    def test_an_adapter_dropping_the_link_fails_the_next_read(self, adapter):
        with BleElmTransport(scan_timeout=0.1) as transport:
            adapter.client.drop()

            assert transport.is_open is False
            with pytest.raises(TransportError):
                transport.read(0.1)

    def test_write_before_opening_is_refused(self):
        with pytest.raises(TransportError):
            BleElmTransport().write(b"ATZ\r")

    def test_close_releases_the_adapter(self, adapter):
        transport = BleElmTransport(scan_timeout=0.1)
        transport.open()

        transport.close()

        assert adapter.connected is False
        assert transport.is_open is False

    def test_close_on_a_closed_transport_is_harmless(self):
        BleElmTransport().close()

    def test_the_protocol_layer_talks_through_it(self, adapter):
        """The point of the transport: the AT dialogue cannot tell BLE from USB."""
        with BleElmTransport(scan_timeout=0.1) as transport:
            reply = ElmProtocol(transport, timeout=1.0).send("ATE0")

        assert reply.ok


class TestScan:
    def test_only_adapters_that_look_like_obd_are_listed(self, monkeypatch):
        install(monkeypatch, FakeAdapter(), FakeAdapter(name="Watch", address="11:11:11:11:11:11", advertised=()))

        assert scan_ble_adapters(timeout=0.1) == [("AA:BB:CC:DD:EE:FF", "OBDII")]


class TestMissingBleak:
    def test_a_missing_bleak_explains_how_to_install_it(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name == "bleak":
                raise ImportError("no bleak")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)

        with pytest.raises(TransportError) as excinfo:
            BleElmTransport().open()

        assert "canopen-studio[ble]" in str(excinfo.value)
