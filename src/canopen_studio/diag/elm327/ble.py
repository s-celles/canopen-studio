"""
Bluetooth Low Energy transport for an ELM327 adapter.

A BLE adapter is not a serial port. It exposes a GATT service holding one characteristic
the host writes commands to and one it subscribes to for replies, and nothing standard
says which: every chip vendor picked its own UUIDs. What the adapters do agree on is the
byte stream carried across that pair, which is the same AT dialogue a USB adapter speaks,
so once the pair is found this is just another `ElmTransport`.

`bleak` is asynchronous and the rest of the diagnostic stack is not. The client therefore
lives on an event loop in a private thread; notifications land in a buffer that `read`
waits on, and `write` hands its coroutine to that loop and waits for it. Callers — the
Tkinter studio, the MCP server — never see asyncio.

`bleak` is an optional dependency (`pip install canopen-studio[ble]`), imported only when
a BLE link is opened so that USB and Wi-Fi users do not need it.
"""

from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Tuple

from ..interface import TransportError
from .transport import ElmTransport


def _uuid16(short: str) -> str:
    """Expand a 16-bit SIG UUID to the 128-bit form `bleak` reports."""
    return f"0000{short.lower()}-0000-1000-8000-00805f9b34fb"


@dataclass(frozen=True)
class GattLayout:
    """Where an adapter family takes commands and sends replies."""

    name: str
    service: str
    notify: str
    write: str


# The layouts ELM327 clones are known to ship with, most common first. The PIC18F25K80
# "Bluetooth 4.0" boards sold as iPhone-compatible use FFF0; HM-10 style modules use FFE0,
# with one characteristic doing both jobs.
KNOWN_LAYOUTS: Tuple[GattLayout, ...] = (
    GattLayout("FFF0 (generic ELM327 BLE)", _uuid16("fff0"), _uuid16("fff1"), _uuid16("fff2")),
    GattLayout("FFE0 (HM-10 style)", _uuid16("ffe0"), _uuid16("ffe1"), _uuid16("ffe1")),
    GattLayout("18F0 (Vgate iCar)", _uuid16("18f0"), _uuid16("2af0"), _uuid16("2af1")),
    GattLayout(
        "Nordic UART",
        "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
        "6e400003-b5a3-f393-e0a9-e50e24dcca9e",
        "6e400002-b5a3-f393-e0a9-e50e24dcca9e",
    ),
)

# Services every BLE device carries, which can never be the data channel.
_STANDARD_SERVICES = {_uuid16(short) for short in ("1800", "1801", "180a", "180f")}

# Names adapters advertise under, used only when no known service is advertised.
_NAME_HINTS = ("obd", "elm", "vgate", "icar", "v-link", "vlink", "veepeak", "obdlink", "konnwei", "carista")

# ATT's default MTU leaves 20 bytes per write; larger writes are split to that.
_DEFAULT_CHUNK = 20

_ADDRESS = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$|^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$", re.IGNORECASE)


def looks_like_address(text: str) -> bool:
    """
    Whether `text` names a device by address rather than by advertised name.

    Linux and Windows address a device by its MAC; macOS hides the MAC and hands out a
    per-host UUID instead, so both forms count.
    """
    return bool(_ADDRESS.match(text.strip()))


def pick_characteristics(services: Iterable[Any]) -> Tuple[str, str, str]:
    """
    Find the notify and write characteristics carrying the ELM327 byte stream.

    Args:
        services: `bleak` GATT services, or anything with `uuid` and `characteristics`,
            each characteristic having `uuid` and `properties`.

    Returns:
        The notify UUID, the write UUID, and a description of the layout matched.

    Raises:
        TransportError: When no service offers a usable pair. The message lists what the
            device does offer, which is what a user needs to report an unknown adapter.
    """
    services = list(services)
    by_uuid = {str(s.uuid).lower(): s for s in services}

    for layout in KNOWN_LAYOUTS:
        service = by_uuid.get(layout.service)
        if service is None:
            continue
        uuids = {str(c.uuid).lower() for c in service.characteristics}
        if layout.notify in uuids and layout.write in uuids:
            return layout.notify, layout.write, layout.name

    # An unknown vendor: settle for the first custom service with exactly one way in and
    # one way out. Guessing wider than that risks writing AT commands into something else.
    for service in services:
        if str(service.uuid).lower() in _STANDARD_SERVICES:
            continue
        notify = [c for c in service.characteristics if {"notify", "indicate"} & set(c.properties)]
        write = [c for c in service.characteristics if {"write", "write-without-response"} & set(c.properties)]
        if len(notify) == 1 and len(write) == 1:
            return str(notify[0].uuid).lower(), str(write[0].uuid).lower(), f"service {service.uuid}"

    offered = "; ".join(
        f"{s.uuid} [" + ", ".join(f"{c.uuid} {'/'.join(c.properties)}" for c in s.characteristics) + "]"
        for s in services
        if str(s.uuid).lower() not in _STANDARD_SERVICES
    )
    raise TransportError(
        "no ELM327 data channel found on this BLE device; pass notify_uuid and write_uuid explicitly. "
        f"Services offered: {offered or 'none'}"
    )


def _is_candidate(device: Any, advertisement: Any) -> bool:
    """Whether an advertisement looks like an OBD adapter."""
    advertised = {str(u).lower() for u in (getattr(advertisement, "service_uuids", None) or [])}
    if advertised & {layout.service for layout in KNOWN_LAYOUTS}:
        return True
    name = (getattr(advertisement, "local_name", None) or getattr(device, "name", None) or "").lower()
    return any(hint in name for hint in _NAME_HINTS)


def _load_bleak():
    """Import `bleak`, turning its absence into an instruction rather than a traceback."""
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise TransportError(
            "Bluetooth LE adapters need the 'bleak' package: pip install 'canopen-studio[ble]'"
        ) from exc
    return BleakScanner, BleakClient


def scan_ble_adapters(timeout: float = 5.0) -> List[Tuple[str, str]]:
    """
    List nearby BLE devices that look like OBD adapters.

    Returns:
        (address, name) pairs. The address is what `BleElmTransport` accepts; on macOS it
        is a UUID rather than a MAC.
    """
    scanner, _ = _load_bleak()

    async def scan():
        return await scanner.discover(timeout=timeout, return_adv=True)

    found = asyncio.run(scan())
    return [
        (device.address, advertisement.local_name or device.name or "")
        for device, advertisement in found.values()
        if _is_candidate(device, advertisement)
    ]


class BleElmTransport(ElmTransport):
    """
    An ELM327 on Bluetooth Low Energy.

    Args:
        device: The adapter's address (a MAC, or a UUID on macOS), a fragment of its
            advertised name, or None to take the first adapter that looks like one.
        notify_uuid: Characteristic replies arrive on. Found automatically when omitted.
        write_uuid: Characteristic commands are written to. Found automatically when omitted.
        scan_timeout: Seconds to look for the device before giving up.

    The adapter must not be paired to the operating system as a classic device, nor held
    by another application: BLE peripherals accept one central at a time.
    """

    def __init__(
        self,
        device: Optional[str] = None,
        notify_uuid: Optional[str] = None,
        write_uuid: Optional[str] = None,
        scan_timeout: float = 10.0,
    ):
        self.device = (device or "").strip() or None
        self.notify_uuid = notify_uuid.lower() if notify_uuid else None
        self.write_uuid = write_uuid.lower() if write_uuid else None
        self.scan_timeout = scan_timeout
        self.layout: Optional[str] = None
        self.address: Optional[str] = None

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client: Any = None
        self._chunk = _DEFAULT_CHUNK
        self._with_response = False
        self._buffer = bytearray()
        self._arrived = threading.Condition()
        self._lost = False

    @property
    def description(self) -> str:
        where = self.address or self.device or "first adapter found"
        return f"ELM327 on Bluetooth LE ({where})"

    @property
    def is_open(self) -> bool:
        return self._client is not None and not self._lost

    # -- lifecycle ---------------------------------------------------------------------

    def open(self) -> None:
        if self._client is not None:
            return
        scanner, client_class = _load_bleak()

        self._lost = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="elm327-ble", daemon=True)
        self._thread.start()

        try:
            # Scanning and connecting can each take the full timeout; allow for both.
            self._run(self._connect(scanner, client_class), timeout=self.scan_timeout * 2 + 15)
        except TransportError:
            self._stop_loop()
            raise
        except Exception as exc:
            self._stop_loop()
            raise TransportError(f"cannot connect to {self.device or 'a BLE adapter'}: {exc}") from exc

    async def _connect(self, scanner, client_class) -> None:
        target = await self._find(scanner)
        client = client_class(target, disconnected_callback=self._on_disconnect, timeout=self.scan_timeout)
        await client.connect()
        try:
            services = client.services
            if self.notify_uuid and self.write_uuid:
                notify, write, layout = self.notify_uuid, self.write_uuid, "given UUIDs"
            else:
                notify, write, layout = pick_characteristics(services)

            characteristic = services.get_characteristic(write) if hasattr(services, "get_characteristic") else None
            if characteristic is not None:
                properties = set(characteristic.properties)
                # Without-response is faster, but only if the adapter offers it.
                self._with_response = "write-without-response" not in properties
                size = getattr(characteristic, "max_write_without_response_size", 0) or 0
                self._chunk = max(size, _DEFAULT_CHUNK)

            await client.start_notify(notify, self._on_notify)
        except BaseException:
            await client.disconnect()
            raise

        self.notify_uuid, self.write_uuid, self.layout = notify, write, layout
        self.address = getattr(target, "address", None) or str(target)
        self._client = client

    async def _find(self, scanner):
        """Resolve `device` to something `BleakClient` can connect to."""
        if self.device and looks_like_address(self.device):
            found = await scanner.find_device_by_address(self.device, timeout=self.scan_timeout)
            if found is None:
                raise TransportError(f"no BLE device at {self.device}; is the adapter powered and in range?")
            return found

        found = await scanner.discover(timeout=self.scan_timeout, return_adv=True)
        wanted = self.device.lower() if self.device else None
        seen = []
        for device, advertisement in found.values():
            name = advertisement.local_name or device.name or ""
            seen.append(name or device.address)
            if wanted is not None and wanted in name.lower():
                return device
            if wanted is None and _is_candidate(device, advertisement):
                return device

        what = f"named like {self.device!r}" if self.device else "that looks like an OBD adapter"
        listed = ", ".join(sorted(set(seen))) or "nothing"
        raise TransportError(f"no BLE device {what} found in {self.scan_timeout:g} s (saw: {listed})")

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None and self._loop is not None:
            try:
                self._run(client.disconnect(), timeout=5.0)
            except Exception:
                # An adapter already out of range is not a reason to fail closing a session.
                pass
        self._stop_loop()
        with self._arrived:
            self._buffer.clear()

    def _stop_loop(self) -> None:
        loop, thread = self._loop, self._thread
        self._loop = self._thread = None
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=2.0)
        if not loop.is_running():
            loop.close()

    def _run(self, coroutine, timeout: float):
        """Run a coroutine on the private loop and wait for its result."""
        if self._loop is None:
            coroutine.close()
            raise TransportError("the Bluetooth LE link is not open")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout)
        except TimeoutError as exc:
            future.cancel()
            raise TransportError("the Bluetooth LE adapter did not respond in time") from exc

    # -- callbacks, on the private loop's thread ---------------------------------------

    def _on_notify(self, _characteristic, data: bytearray) -> None:
        with self._arrived:
            self._buffer.extend(data)
            self._arrived.notify_all()

    def _on_disconnect(self, _client) -> None:
        with self._arrived:
            self._lost = True
            self._arrived.notify_all()

    # -- byte stream -------------------------------------------------------------------

    def _check_open(self) -> None:
        if self._client is None:
            raise TransportError("the Bluetooth LE link is not open")
        if self._lost:
            raise TransportError("the Bluetooth LE adapter disconnected")

    def write(self, data: bytes) -> None:
        self._check_open()
        try:
            for start in range(0, len(data), self._chunk):
                piece = bytes(data[start : start + self._chunk])
                self._run(
                    self._client.write_gatt_char(self.write_uuid, piece, response=self._with_response), timeout=5.0
                )
        except TransportError:
            raise
        except Exception as exc:
            raise TransportError(f"write to the BLE adapter failed: {exc}") from exc

    def read(self, timeout: float) -> bytes:
        self._check_open()
        with self._arrived:
            if not self._buffer and not self._lost:
                self._arrived.wait(max(timeout, 0.0))
            if self._lost and not self._buffer:
                raise TransportError("the Bluetooth LE adapter disconnected")
            chunk = bytes(self._buffer)
            self._buffer.clear()
        return chunk

    def reset_input_buffer(self) -> None:
        with self._arrived:
            self._buffer.clear()

    def __enter__(self) -> "BleElmTransport":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
