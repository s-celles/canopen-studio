"""
An in-memory ELM327 that speaks the real protocol, for deterministic unit tests.

This is the fast, hermetic half of the test strategy. It answers the AT dialogue, honours
the echo / linefeed / header settings the studio sets, frames OBD responses as ISO-TP the
way a real adapter prints them, and can be told to misbehave: report a firmware version
it does not live up to, answer a status word, or truncate a reply.

The other half is Ircama's ELM327-emulator, driven by `tests/test_diag_elm_emulator.py`.
That one is realistic but is licensed CC-BY-NC-SA-4.0 and has to be installed separately,
so it sits behind an opt-in marker while this fake carries the everyday suite.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional

from canopen_studio.diag.elm327.transport import ElmTransport
from canopen_studio.diag.isotp import segment

# What a genuine chip answers to AT@1.
DEVICE_DESCRIPTION = "OBDII to RS232 Interpreter"

# AT commands a v1.0-era chip never had. A clone claiming v1.5 while refusing these is
# the exact failure the capability probe exists to catch.
LATE_FIRMWARE_COMMANDS = ("CRA", "FCSH", "FCSD", "FCSM", "PPS", "AT1", "AT2", "CEA", "DPN")


class FakeElm327(ElmTransport):
    """
    A scriptable ELM327 standing in for a real adapter.

    Args:
        version: What ATI reports. Clones lie here, which is the point.
        ecus: Maps a request, as uppercase hex without spaces, to the response bytes of
            each ECU: `{"010C": {0x7E8: b"\\x41\\x0c\\x1a\\xf8"}}`.
        unsupported: AT commands this firmware answers '?' to, without the AT prefix.
        status: A status word to answer every OBD request with, e.g. "NO DATA".
        truncate: Cut every OBD reply line to this many characters, to exercise the
            parser against a reply that was cut off mid-transfer.
        extended: Print 29-bit headers, byte by byte, as an adapter does.
        dpn: What ATDPN reports, e.g. "A6" for autodetected ISO 15765-4 CAN 11/500.
        voltage: What ATRV reports.
    """

    def __init__(
        self,
        version: str = "ELM327 v1.5",
        ecus: Optional[Dict[str, Dict[int, bytes]]] = None,
        unsupported: Iterable[str] = (),
        status: Optional[str] = None,
        truncate: Optional[int] = None,
        device_description: str = DEVICE_DESCRIPTION,
        extended: bool = False,
        dpn: str = "A6",
        voltage: str = "12.6V",
    ):
        self.version = version
        self.ecus = {k.upper().replace(" ", ""): v for k, v in (ecus or {}).items()}
        self.unsupported = {c.upper() for c in unsupported}
        self.status = status
        self.truncate = truncate
        self.device_description = device_description
        self.extended = extended
        self.dpn = dpn
        self.voltage = voltage

        # Adapter settings, in the state a real chip powers up in.
        self.echo = True
        self.linefeeds = True
        self.spaces = True
        self.headers = False
        self.protocol = 0
        self.adaptive_timing = 1

        self.commands: list[str] = []
        self._opened = False
        self._pending = bytearray()
        self._out = bytearray()

    # -- ElmTransport ------------------------------------------------------

    @property
    def description(self) -> str:
        return f"fake {self.version}"

    @property
    def is_open(self) -> bool:
        return self._opened

    def open(self) -> None:
        self._opened = True

    def close(self) -> None:
        self._opened = False

    def write(self, data: bytes) -> None:
        for byte in data:
            if byte in (0x0D, 0x0A):
                command = self._pending.decode("ascii", errors="ignore")
                self._pending.clear()
                self._handle(command)
            else:
                self._pending.append(byte)

    def read(self, timeout: float) -> bytes:
        chunk = bytes(self._out)
        self._out.clear()
        return chunk

    def reset_input_buffer(self) -> None:
        self._out.clear()

    # -- Adapter behaviour -------------------------------------------------

    def _emit(self, text: str) -> None:
        self._out.extend(text.encode("ascii", errors="ignore"))

    def _line(self, text: str) -> None:
        self._emit(text + ("\r\n" if self.linefeeds else "\r"))

    def _prompt(self) -> None:
        self._emit("\r>" if not self.linefeeds else "\r\n>")

    def _handle(self, command: str) -> None:
        command = command.strip()
        if not command:
            return
        self.commands.append(command)

        if self.echo:
            self._line(command)

        compact = command.replace(" ", "").upper()
        if compact.startswith("AT"):
            self._handle_at(compact[2:])
        else:
            self._handle_obd(compact)
        self._prompt()

    def _handle_at(self, body: str) -> None:
        if body in self.unsupported:
            self._line("?")
            return

        if body == "Z" or body == "WS":
            self.echo = True
            self.linefeeds = True
            self.spaces = True
            self.headers = False
            self._line("")
            self._line(self.version)
            return
        if body == "I":
            self._line(self.version)
            return
        if body == "@1":
            self._line(self.device_description)
            return
        if body == "@2":
            self._line(self.version.replace(" ", ""))
            return
        if body == "DP":
            self._line("AUTO, ISO 15765-4 (CAN 11/500)")
            return
        if body == "DPN":
            self._line(self.dpn)
            return
        if body == "RV":
            self._line(self.voltage)
            return
        if body == "PC":
            self._line("OK")
            return
        if body in ("E0", "E1"):
            self.echo = body == "E1"
        elif body in ("L0", "L1"):
            self.linefeeds = body == "L1"
        elif body in ("S0", "S1"):
            self.spaces = body == "S1"
        elif body in ("H0", "H1"):
            self.headers = body == "H1"
        elif body.startswith("SP"):
            self.protocol = body[2:]
        elif body.startswith("AT"):
            self.adaptive_timing = body[2:]
        elif body.startswith(LATE_FIRMWARE_COMMANDS) or body.startswith(("ST", "CAF", "D", "M0", "M1", "RV")):
            pass
        else:
            self._line("?")
            return
        self._line("OK")

    def _handle_obd(self, request_hex: str) -> None:
        # A trailing digit on an odd-length request is the ELM327 "expected responses"
        # hint, not part of the request itself.
        if len(request_hex) % 2:
            request_hex = request_hex[:-1]

        if self.status:
            self._line(self.status)
            return

        responses = self.ecus.get(request_hex)
        if not responses:
            self._line("NO DATA")
            return

        self._line("SEARCHING...")
        for source, payload in responses.items():
            for frame in self._frames(payload):
                line = self._render(source, frame)
                if self.truncate is not None:
                    line = line[: self.truncate]
                self._line(line)

    def _frames(self, payload: bytes) -> list[bytes]:
        """
        Frame a response the way an adapter prints it.

        A real ELM327 drops the padding of a single frame rather than printing eight
        bytes, so the trailing filler is trimmed back to the declared length.
        """
        frames = segment(payload)
        if len(frames) == 1:
            return [frames[0][: 1 + (frames[0][0] & 0x0F)]]
        return frames

    def _render(self, source: int, frame: bytes) -> str:
        header = f"{source:08X}" if self.extended else f"{source:03X}"
        body = frame.hex().upper()
        if not self.spaces:
            return header + body
        chunks = [body[i : i + 2] for i in range(0, len(body), 2)]
        if self.extended:
            header = " ".join(header[i : i + 2] for i in range(0, len(header), 2))
        return " ".join([header, *chunks])


def single_frame_ecu(request: str, payload: bytes, source: int = 0x7E8) -> Dict[str, Dict[int, bytes]]:
    """Build the `ecus` mapping for one ECU answering one request."""
    return {request: {source: payload}}
