"""
Byte transports for an ELM327 adapter.

An ELM327 is a modem-like device: bytes in, ASCII out, terminated by a '>' prompt. These
classes carry the bytes and nothing else — framing, the AT dialogue and the error
taxonomy all live in `protocol.py`, which keeps the transports trivial to fake.

Three ways to reach one are supported, all of them byte streams:

USB
    A CDC-ACM or FTDI serial port: `/dev/ttyUSB0`, `COM4`. The common case.

Bluetooth SPP
    Also a serial port once the operating system has bound one. On Linux, `rfcomm bind`
    produces `/dev/rfcomm0`; on Windows, pairing produces an outgoing `COMx`. Nothing
    here differs from USB, so `SerialElmTransport` covers both.

TCP
    Wi-Fi ELM327 clones expose port 35000, and the ELM327 emulator used by the test
    suite exposes the same interface with its `-n` option.

Bluetooth Low Energy is **not supported**. BLE adapters expose a GATT service rather than
a serial port, with a vendor-specific characteristic pair and no standard profile to bind
against; they need a `bleak` dependency and per-vendor handling. Use a USB, a classic
Bluetooth SPP, or a Wi-Fi adapter instead.
"""

from __future__ import annotations

import socket
from abc import ABC, abstractmethod
from typing import Optional

from ..interface import TransportError

# The rate an ELM327 comes up at. Genuine chips and most clones use it; a few older
# boards are wired for 9600 instead.
DEFAULT_BAUDRATE = 38400

# Port that Wi-Fi ELM327 adapters listen on, and the one the emulator documents.
DEFAULT_TCP_PORT = 35000

# Longest single read, keeping a waiting caller responsive to its own deadline.
READ_CHUNK = 256


class ElmTransport(ABC):
    """A byte stream to an ELM327 adapter."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Short human-readable description of the link."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Whether the link is currently usable."""

    @abstractmethod
    def open(self) -> None:
        """Bring the link up, raising `TransportError` if it cannot be established."""

    @abstractmethod
    def close(self) -> None:
        """Release the link. Closing a closed transport is harmless."""

    @abstractmethod
    def write(self, data: bytes) -> None:
        """Send bytes to the adapter."""

    @abstractmethod
    def read(self, timeout: float) -> bytes:
        """
        Read whatever has arrived, waiting up to `timeout` seconds for the first byte.

        Returns an empty bytes object when nothing arrived: the caller is looping until
        it sees a prompt and decides for itself when to give up.
        """

    def reset_input_buffer(self) -> None:
        """Discard unread bytes, so a command is not confused by an earlier reply."""

    def __enter__(self) -> "ElmTransport":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class SerialElmTransport(ElmTransport):
    """
    An ELM327 on a serial port: USB, or classic Bluetooth SPP once bound to a port.

    Args:
        port: The device, e.g. `/dev/ttyUSB0`, `/dev/rfcomm0` or `COM4`.
        baudrate: Line rate. 38400 suits genuine chips and most clones; try 9600 for an
            older board that stays silent.
    """

    def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE):
        self.port = port
        self.baudrate = baudrate
        self._serial = None

    @property
    def description(self) -> str:
        return f"ELM327 on {self.port} at {self.baudrate} baud"

    @property
    def is_open(self) -> bool:
        return self._serial is not None

    def open(self) -> None:
        if self._serial is not None:
            return
        import serial

        try:
            # A zero read timeout keeps read() non-blocking; the caller owns the waiting.
            self._serial = serial.Serial(self.port, baudrate=self.baudrate, timeout=0)
        except Exception as exc:
            raise TransportError(f"cannot open {self.port}: {exc}") from exc

    def close(self) -> None:
        if self._serial is None:
            return
        try:
            self._serial.close()
        except Exception:
            # A port already gone is not a reason to fail closing a session.
            pass
        finally:
            self._serial = None

    def write(self, data: bytes) -> None:
        if self._serial is None:
            raise TransportError("the serial port is not open")
        try:
            self._serial.write(data)
            self._serial.flush()
        except Exception as exc:
            raise TransportError(f"write to {self.port} failed: {exc}") from exc

    def read(self, timeout: float) -> bytes:
        if self._serial is None:
            raise TransportError("the serial port is not open")
        try:
            self._serial.timeout = max(timeout, 0.0)
            waiting = getattr(self._serial, "in_waiting", 0) or 1
            return self._serial.read(max(min(waiting, READ_CHUNK), 1)) or b""
        except Exception as exc:
            raise TransportError(f"read from {self.port} failed: {exc}") from exc

    def reset_input_buffer(self) -> None:
        if self._serial is None:
            return
        try:
            self._serial.reset_input_buffer()
        except Exception:
            pass


class TcpElmTransport(ElmTransport):
    """
    An ELM327 reachable over TCP: a Wi-Fi adapter, or the emulator's `-n` interface.

    Args:
        host: Address of the adapter. Wi-Fi clones usually answer on 192.168.0.10.
        port: TCP port, 35000 by convention.
        connect_timeout: Seconds to wait for the connection to be accepted.
    """

    def __init__(self, host: str = "192.168.0.10", port: int = DEFAULT_TCP_PORT, connect_timeout: float = 5.0):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self._socket: Optional[socket.socket] = None

    @property
    def description(self) -> str:
        return f"ELM327 at {self.host}:{self.port}"

    @property
    def is_open(self) -> bool:
        return self._socket is not None

    def open(self) -> None:
        if self._socket is not None:
            return
        try:
            self._socket = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        except OSError as exc:
            raise TransportError(f"cannot connect to {self.host}:{self.port}: {exc}") from exc

    def close(self) -> None:
        if self._socket is None:
            return
        try:
            self._socket.close()
        except OSError:
            pass
        finally:
            self._socket = None

    def write(self, data: bytes) -> None:
        if self._socket is None:
            raise TransportError("the connection is not open")
        try:
            self._socket.sendall(data)
        except OSError as exc:
            raise TransportError(f"write to {self.host}:{self.port} failed: {exc}") from exc

    def read(self, timeout: float) -> bytes:
        if self._socket is None:
            raise TransportError("the connection is not open")
        try:
            self._socket.settimeout(max(timeout, 0.0) or None)
            return self._socket.recv(READ_CHUNK)
        except socket.timeout:
            return b""
        except OSError as exc:
            raise TransportError(f"read from {self.host}:{self.port} failed: {exc}") from exc

    def reset_input_buffer(self) -> None:
        if self._socket is None:
            return
        try:
            self._socket.settimeout(0)
            while self._socket.recv(READ_CHUNK):
                pass
        except OSError:
            # Nothing left to drain, which is the expected way out of that loop.
            pass
