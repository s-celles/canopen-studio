"""
Unit tests for the ELM327 byte transports.

The serial transport is exercised against a stand-in for `serial.Serial`; the TCP one
against a real loopback server, which is cheap and tests the socket handling for real.
"""

import socket
import threading

import pytest

from canopen_studio.diag import TransportError
from canopen_studio.diag.elm327.transport import (
    DEFAULT_BAUDRATE,
    DEFAULT_TCP_PORT,
    SerialElmTransport,
    TcpElmTransport,
)


class FakeSerial:
    """Stands in for pyserial, recording writes and replaying canned bytes."""

    def __init__(self, port, baudrate=None, timeout=None):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.written = bytearray()
        self.buffer = bytearray()
        self.closed = False
        self.flushed = 0
        self.reset_count = 0

    @property
    def in_waiting(self):
        return len(self.buffer)

    def write(self, data):
        self.written.extend(data)

    def flush(self):
        self.flushed += 1

    def read(self, size):
        chunk = bytes(self.buffer[:size])
        del self.buffer[: len(chunk)]
        return chunk

    def reset_input_buffer(self):
        self.reset_count += 1
        self.buffer.clear()

    def close(self):
        self.closed = True


@pytest.fixture
def fake_serial(monkeypatch):
    """Install FakeSerial in place of serial.Serial and hand back the instances made."""
    import serial

    made = []

    def factory(port, **kwargs):
        instance = FakeSerial(port, **kwargs)
        made.append(instance)
        return instance

    monkeypatch.setattr(serial, "Serial", factory)
    return made


class TestSerialTransport:
    def test_description_names_the_port_and_rate(self):
        assert "/dev/ttyUSB0" in SerialElmTransport("/dev/ttyUSB0").description

    def test_default_rate_is_the_one_elm327_boards_come_up_at(self):
        assert SerialElmTransport("COM4").baudrate == DEFAULT_BAUDRATE

    def test_transport_starts_closed(self):
        assert SerialElmTransport("COM4").is_open is False

    def test_open_creates_the_port_with_a_non_blocking_read(self):
        """The protocol layer owns the waiting, so the port itself must not block."""
        import serial

        made = []
        original = serial.Serial

        try:
            serial.Serial = lambda port, **kw: made.append(FakeSerial(port, **kw)) or made[-1]
            SerialElmTransport("COM4").open()
        finally:
            serial.Serial = original

        assert made[0].timeout == 0

    def test_open_is_idempotent(self, fake_serial):
        transport = SerialElmTransport("COM4")

        transport.open()
        transport.open()

        assert len(fake_serial) == 1

    def test_a_port_that_cannot_be_opened_raises_a_transport_error(self, monkeypatch):
        import serial

        def refuse(port, **kwargs):
            raise OSError("no such device")

        monkeypatch.setattr(serial, "Serial", refuse)

        with pytest.raises(TransportError) as excinfo:
            SerialElmTransport("/dev/nope").open()

        assert "/dev/nope" in str(excinfo.value)

    def test_write_reaches_the_port_and_is_flushed(self, fake_serial):
        transport = SerialElmTransport("COM4")
        transport.open()

        transport.write(b"ATZ\r")

        assert bytes(fake_serial[0].written) == b"ATZ\r"
        assert fake_serial[0].flushed == 1

    def test_write_before_opening_is_refused(self):
        with pytest.raises(TransportError):
            SerialElmTransport("COM4").write(b"ATZ\r")

    def test_read_returns_what_arrived(self, fake_serial):
        transport = SerialElmTransport("COM4")
        transport.open()
        fake_serial[0].buffer.extend(b"ELM327 v1.5\r>")

        assert transport.read(0.1) == b"ELM327 v1.5\r>"

    def test_read_on_a_quiet_port_returns_nothing(self, fake_serial):
        transport = SerialElmTransport("COM4")
        transport.open()

        assert transport.read(0.01) == b""

    def test_read_before_opening_is_refused(self):
        with pytest.raises(TransportError):
            SerialElmTransport("COM4").read(0.1)

    def test_close_releases_the_port(self, fake_serial):
        transport = SerialElmTransport("COM4")
        transport.open()

        transport.close()

        assert fake_serial[0].closed is True
        assert transport.is_open is False

    def test_close_on_a_closed_transport_is_harmless(self):
        SerialElmTransport("COM4").close()

    def test_a_port_that_fails_to_close_still_ends_the_session(self, fake_serial):
        """A yanked USB adapter must not leave the session believing it is connected."""
        transport = SerialElmTransport("COM4")
        transport.open()
        fake_serial[0].close = lambda: (_ for _ in ()).throw(OSError("gone"))

        transport.close()

        assert transport.is_open is False

    def test_reset_input_buffer_drops_pending_bytes(self, fake_serial):
        transport = SerialElmTransport("COM4")
        transport.open()
        fake_serial[0].buffer.extend(b"stale")

        transport.reset_input_buffer()

        assert fake_serial[0].reset_count == 1

    def test_context_manager_opens_and_closes(self, fake_serial):
        with SerialElmTransport("COM4") as transport:
            assert transport.is_open is True

        assert fake_serial[0].closed is True


class EchoServer:
    """A loopback TCP server replaying a canned banner and echoing what it receives."""

    def __init__(self, banner=b"ELM327 v1.5\r\r>"):
        self.banner = banner
        self.received = bytearray()
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._server.accept()
        except OSError:
            return
        with conn:
            conn.sendall(self.banner)
            while True:
                try:
                    chunk = conn.recv(256)
                except OSError:
                    return
                if not chunk:
                    return
                self.received.extend(chunk)
                conn.sendall(chunk)

    def close(self):
        self._server.close()


@pytest.fixture
def echo_server():
    server = EchoServer()
    yield server
    server.close()


class TestTcpTransport:
    def test_default_port_is_the_wifi_adapter_convention(self):
        assert TcpElmTransport().port == DEFAULT_TCP_PORT

    def test_description_names_the_endpoint(self):
        assert "10.0.0.1:35000" in TcpElmTransport("10.0.0.1").description

    def test_open_connects_and_reads_the_banner(self, echo_server):
        transport = TcpElmTransport("127.0.0.1", echo_server.port)

        transport.open()

        assert transport.is_open is True
        assert b"ELM327" in transport.read(1.0)
        transport.close()

    def test_write_reaches_the_server(self, echo_server):
        transport = TcpElmTransport("127.0.0.1", echo_server.port)
        transport.open()
        transport.read(1.0)

        transport.write(b"ATI\r")

        assert transport.read(1.0) == b"ATI\r"
        transport.close()

    def test_a_refused_connection_raises_a_transport_error(self):
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()

        with pytest.raises(TransportError):
            TcpElmTransport("127.0.0.1", port, connect_timeout=0.5).open()

    def test_read_on_a_quiet_connection_returns_nothing(self, echo_server):
        transport = TcpElmTransport("127.0.0.1", echo_server.port)
        transport.open()
        transport.read(1.0)

        assert transport.read(0.05) == b""
        transport.close()

    def test_write_before_connecting_is_refused(self):
        with pytest.raises(TransportError):
            TcpElmTransport("127.0.0.1", 1).write(b"ATI\r")

    def test_close_releases_the_connection(self, echo_server):
        transport = TcpElmTransport("127.0.0.1", echo_server.port)
        transport.open()

        transport.close()

        assert transport.is_open is False

    def test_close_on_a_closed_transport_is_harmless(self):
        TcpElmTransport("127.0.0.1", 1).close()
