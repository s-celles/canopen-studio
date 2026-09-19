"""
An ELM327 adapter behind the `DiagnosticInterface` contract.

Two configuration choices shape everything else, and both are deliberate.

Headers on (`ATH1`)
    Without them every reply is anonymous, and on a functional request several ECUs
    answer at once. Headers give each line its source, which is what lets a response be
    attributed and a multi-frame transfer be reassembled per ECU.

Auto formatting left on (`ATCAF1`)
    With headers on *and* auto formatting on, an ELM327 still prints each CAN frame
    including its ISO-TP protocol control byte, while handling flow control itself. That
    is the best of both: the adapter does the timing-critical handshake, and we get the
    framing we need to reassemble. Turning auto formatting off would hand us the flow
    control duty over a serial link, where the timing is not ours to meet.

Spaces are kept on (`ATS1`) rather than turned off for throughput. With them, a line
splits into tokens and the header is the first one or four of them; without them, the
header and the data run together and parsing depends on knowing the addressing width
exactly. The few bytes per line are worth the robustness.

Only the CAN protocols of ISO 15765-4 are supported. The older K-line and J1850
protocols carry J1979 too, but the adapter frames them differently — no ISO-TP, a
three-byte header and a trailing checksum — so a session that autodetects one of them
says so plainly instead of mis-parsing it.
"""

from __future__ import annotations

from typing import List, Optional

from ..interface import DiagnosticInterface, DiagnosticResponse, ProtocolError
from ..isotp import IsoTpReassembler
from .capabilities import ElmCapabilities, probe_capabilities
from .protocol import DEFAULT_COMMAND_TIMEOUT, ElmProtocol, ElmReply
from .transport import ElmTransport

# A reset re-runs the power-on self test and takes about a second on a genuine chip.
RESET_TIMEOUT = 10.0

# The first request after a reset triggers protocol autodetection, which walks every
# protocol in turn and can take several seconds before the first answer appears.
AUTODETECT_TIMEOUT = 20.0

# ELM327 protocol numbers that are ISO 15765-4, i.e. CAN with ISO-TP.
CAN_PROTOCOLS = {
    "6": ("ISO 15765-4 CAN", 11, 500),
    "7": ("ISO 15765-4 CAN", 29, 500),
    "8": ("ISO 15765-4 CAN", 11, 250),
    "9": ("ISO 15765-4 CAN", 29, 250),
    "A": ("SAE J1939 CAN", 29, 250),
    "B": ("User CAN 1", 11, 125),
    "C": ("User CAN 2", 11, 50),
}

# Protocol numbers carrying 29-bit identifiers, whose headers print as four tokens.
EXTENDED_PROTOCOLS = {"7", "9", "A"}

# Non-CAN protocols an autodetect may settle on. Named so the failure says which.
NON_CAN_PROTOCOLS = {
    "1": "SAE J1850 PWM",
    "2": "SAE J1850 VPW",
    "3": "ISO 9141-2",
    "4": "ISO 14230-4 KWP (5 baud init)",
    "5": "ISO 14230-4 KWP (fast init)",
}


class UnsupportedElmProtocol(ProtocolError):
    """The adapter settled on a protocol this backend does not parse."""


def parse_frame_line(line: str, extended: bool = False) -> Optional[tuple]:
    """
    Split one printed line into its source identifier and its frame bytes.

    With headers and spaces on, an 11-bit line reads `7E8 04 41 0C 1A F8` and a 29-bit
    one `18 DA F1 10 04 41 0C 1A F8`, the header printed byte by byte.

    Returns None for a line that carries no usable frame — a bare header, a stray word,
    or the numbered `0:` continuation format that appears when headers are off.
    """
    tokens = line.split()
    if not tokens:
        return None

    # With headers off the adapter numbers the lines of a long reply instead. That format
    # carries no source, so it cannot be attributed or reassembled per ECU.
    if tokens[0].rstrip(":").isdigit() and tokens[0].endswith(":"):
        raise ProtocolError(f"the adapter answered in its headers-off format ({line!r}); ATH1 did not take effect")

    header_tokens = 4 if extended else 1
    if len(tokens) <= header_tokens:
        return None

    header = "".join(tokens[:header_tokens])
    body = "".join(tokens[header_tokens:])

    try:
        source = int(header, 16)
    except ValueError:
        return None

    if len(body) % 2:
        raise ProtocolError(f"the adapter printed an odd number of hex digits ({line!r})")
    try:
        frame = bytes.fromhex(body)
    except ValueError:
        raise ProtocolError(f"the adapter printed something that is not hexadecimal ({line!r})") from None

    return source, frame


class ElmDiagnosticInterface(DiagnosticInterface):
    """
    A diagnostic session carried by an ELM327.

    Args:
        transport: How the adapter is reached — serial, Bluetooth SPP or TCP.
        protocol: ELM327 protocol number to force, or "0" to let it autodetect. Forcing
            the right one skips the SEARCHING... delay on the first request.
        expected_responses: How many ECUs to expect. Telling the adapter lets it return
            as soon as that many have answered instead of waiting out its own timeout,
            which is the single biggest speed-up for a PID scan. Leave None when the
            number is unknown, such as on a functional request to an unfamiliar car.
        command_timeout: Seconds to allow an ordinary command.
    """

    def __init__(
        self,
        transport: ElmTransport,
        protocol: str = "0",
        expected_responses: Optional[int] = None,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
    ):
        super().__init__()
        self.transport = transport
        self.protocol_setting = str(protocol).upper()
        self.expected_responses = expected_responses
        self.elm = ElmProtocol(transport, timeout=command_timeout)
        self.capabilities = ElmCapabilities()
        self.active_protocol: Optional[str] = None
        self.last_errors: List[str] = []
        self._reassembler = IsoTpReassembler()
        self._extended = False
        self._first_request = True

    @property
    def description(self) -> str:
        parts = [str(self.capabilities), self.transport.description]
        if self.active_protocol:
            name, width, bitrate = CAN_PROTOCOLS.get(self.active_protocol, ("unknown", 0, 0))
            parts.append(f"{name} ({width}-bit, {bitrate} kbaud)")
        return " — ".join(parts)

    @property
    def extended_addressing(self) -> bool:
        """Whether the active protocol uses 29-bit identifiers."""
        return self._extended

    # -- Session -----------------------------------------------------------

    def _open(self) -> None:
        self.transport.open()
        self._reassembler.reset()
        self._first_request = True
        self._configure()
        self.capabilities = probe_capabilities(self.elm)
        self._apply_capabilities()
        self._select_protocol()

    def _configure(self) -> None:
        """Bring the adapter into the state this backend parses."""
        self.elm.send("ATZ", timeout=RESET_TIMEOUT)
        self.elm.send("ATE0").raise_for_status()
        self.elm.send("ATL0")
        # Spaces stay on: they make the header and the data separable without depending
        # on knowing the addressing width exactly.
        self.elm.send("ATS1")
        # Headers are what make a response attributable to an ECU, and what leaves the
        # ISO-TP protocol control bytes visible so that a transfer can be reassembled.
        self.elm.send("ATH1").raise_for_status()

    def _apply_capabilities(self) -> None:
        """Turn on what the board actually supports, and leave the rest alone."""
        if self.capabilities.has("adaptive_timing"):
            # Lets the adapter shorten its wait once it has learned how fast the car
            # answers, instead of holding the full timeout on every request.
            self.elm.send("ATAT1")

    def _select_protocol(self) -> None:
        """Set or detect the protocol, and refuse the ones this backend cannot parse."""
        self.elm.send(f"ATSP{self.protocol_setting}")
        detected = self.elm.send("ATDPN").value.strip().upper()
        # An autodetected protocol is reported with a leading 'A'.
        number = detected[1:] if detected.startswith("A") and len(detected) > 1 else detected
        self.active_protocol = number or None

        if number in NON_CAN_PROTOCOLS:
            raise UnsupportedElmProtocol(
                f"the adapter settled on {NON_CAN_PROTOCOLS[number]}, which this backend does not parse. "
                f"Only the CAN protocols of ISO 15765-4 are supported."
            )
        self._extended = number in EXTENDED_PROTOCOLS

    def _close(self) -> None:
        self._reassembler.reset()
        try:
            # Ask the adapter to drop the protocol so the next session starts clean.
            self.elm.send("ATPC", timeout=1.0)
        except Exception:
            # A link already gone is not a reason to fail closing a session.
            pass
        self.transport.close()

    # -- Exchange ----------------------------------------------------------

    def _request(self, payload: bytes, timeout: float) -> List[DiagnosticResponse]:
        self.last_errors = []
        self._reassembler.reset()

        command = payload.hex().upper()
        if self.expected_responses is not None:
            # The adapter returns as soon as this many replies are in, rather than
            # holding its own timeout open. Worth several seconds across a PID scan.
            command += format(min(self.expected_responses, 0xF), "X")

        # The first request after a reset runs protocol autodetection, which walks every
        # protocol in turn and needs far longer than a steady-state request.
        allowance = max(timeout, AUTODETECT_TIMEOUT) if self._first_request else timeout
        reply = self.elm.send(command, timeout=allowance)
        self._first_request = False

        if reply.no_data:
            # An unsupported PID, which a scan meets constantly. Not an error.
            return []
        reply.raise_for_status()

        return self._collect(reply)

    def _collect(self, reply: ElmReply) -> List[DiagnosticResponse]:
        """Turn the printed lines into responses, keeping what survives a bad line."""
        responses: List[DiagnosticResponse] = []

        for line in reply.lines:
            try:
                parsed = parse_frame_line(line, self._extended)
            except ProtocolError as exc:
                self.last_errors.append(str(exc))
                continue
            if parsed is None:
                continue

            source, frame = parsed
            try:
                result = self._reassembler.feed(source, frame)
            except ProtocolError as exc:
                # A transfer cut off mid-flight. Record it and keep the other ECUs.
                self.last_errors.append(str(exc))
                continue

            if result.completed:
                responses.append(DiagnosticResponse(source=source, data=result.completed))

        for pending in self._reassembler.pending_sources():
            self.last_errors.append(f"the reply from 0x{pending:X} was cut off before it completed")
        self._reassembler.reset()

        return responses

    # -- Adapter extras ----------------------------------------------------

    def read_voltage(self) -> Optional[float]:
        """
        Read the battery voltage the adapter measures on the OBD connector.

        Useful on its own: a reading well under twelve volts explains an
        `UNABLE TO CONNECT` far better than a protocol error does.
        """
        reply = self.elm.send("ATRV")
        text = reply.value.upper().replace("V", "").strip()
        try:
            return float(text)
        except ValueError:
            return None
