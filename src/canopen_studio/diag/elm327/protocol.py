"""
The ELM327 command dialogue: framing, cleaning and the error taxonomy.

An ELM327 answers every command with zero or more lines and then a '>' prompt, which is
the only reliable end marker — replies have no length field and arrive in pieces. So each
exchange reads until the prompt or until its deadline, and never counts lines.

Three kinds of noise have to be removed before anything can be parsed:

Echo
    Until `ATE0` takes effect the adapter repeats the command back. It is dropped by
    comparison rather than by counting lines, because a reset also emits a banner.

Progress chatter
    `SEARCHING...` is printed while protocol autodetection runs, sometimes glued to the
    line that follows it. `BUS INIT: ...` and the `LP ALERT` / `ACT ALERT` power
    management notices are the same kind of noise.

Status words
    In place of data the adapter may answer `NO DATA`, `CAN ERROR`, `UNABLE TO CONNECT`,
    `BUFFER FULL`, `STOPPED` or `?`. These are *not* failures of equal weight. `NO DATA`
    is the ordinary answer to an unsupported PID and a scan walks hundreds of them, so it
    is reported rather than raised; `?` is how capability probing learns a command is
    missing. The caller decides, which is why `ElmReply` carries the status and only
    `raise_for_status()` turns it into an exception.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..interface import DiagnosticError, DiagnosticTimeout
from .transport import ElmTransport

# The adapter is ready for the next command when it prints this.
PROMPT = ">"

# Commands are terminated by a carriage return; the adapter never expects a line feed.
TERMINATOR = b"\r"

# Longest an ordinary command may take. A reset or a first autodetection needs more and
# passes its own timeout.
DEFAULT_COMMAND_TIMEOUT = 5.0

# Longest single wait on the transport, so a command stays responsive to its deadline.
POLL_INTERVAL = 0.05

# Printed while protocol autodetection runs, sometimes glued to the reply that follows.
SEARCHING = "SEARCHING..."

# Noise that carries no information for us and is dropped before parsing.
_CHATTER = ("BUS INIT:", "BUS INIT", "LP ALERT", "ACT ALERT", "LV RESET")

# Internal adapter faults, reported as ERR followed by two digits.
_ERR_PATTERN = re.compile(r"^ERR\d{2}$")


class ElmError(DiagnosticError):
    """The adapter answered with a status word instead of data."""

    def __init__(self, status: str, command: str = ""):
        self.status = status
        self.command = command
        detail = f" (after {command})" if command else ""
        super().__init__(f"ELM327 answered {status}{detail}")


class ElmNoData(ElmError):
    """`NO DATA`: nothing on the bus answered. The usual reply to an unsupported PID."""


class ElmBusError(ElmError):
    """`CAN ERROR`, `BUS BUSY` and friends: the adapter could not work the bus."""


class ElmUnableToConnect(ElmError):
    """`UNABLE TO CONNECT`: no protocol matched, or the ignition is off."""


class ElmBufferFull(ElmError):
    """`BUFFER FULL`: the adapter's internal buffer overflowed mid-reply."""


class ElmStopped(ElmError):
    """`STOPPED`: the exchange was interrupted, typically by a character sent to it."""


class ElmCommandNotSupported(ElmError):
    """`?`: this firmware does not know the command. How clones betray their age."""


# Status words, mapped to what they mean. Longest first, so that a specific phrase is
# matched before a generic one it contains.
STATUS_EXCEPTIONS = {
    "UNABLE TO CONNECT": ElmUnableToConnect,
    "BUS INIT: ERROR": ElmUnableToConnect,
    "BUFFER FULL": ElmBufferFull,
    "CAN ERROR": ElmBusError,
    "DATA ERROR": ElmBusError,
    "BUS ERROR": ElmBusError,
    "BUS BUSY": ElmBusError,
    "FB ERROR": ElmBusError,
    "NO DATA": ElmNoData,
    "STOPPED": ElmStopped,
    "?": ElmCommandNotSupported,
}


@dataclass(frozen=True)
class ElmReply:
    """One complete answer, cleaned of echo and progress chatter."""

    command: str
    raw: str
    lines: Tuple[str, ...]
    status: Optional[str] = None

    @property
    def ok(self) -> bool:
        """Whether the adapter answered with data rather than a status word."""
        return self.status is None

    @property
    def is_unsupported(self) -> bool:
        """Whether the adapter does not know the command — the '?' answer."""
        return self.status == "?"

    @property
    def no_data(self) -> bool:
        """Whether nothing on the bus answered."""
        return self.status == "NO DATA"

    @property
    def value(self) -> str:
        """The single data line, for the AT commands that return one."""
        return self.lines[0] if self.lines else ""

    def raise_for_status(self) -> "ElmReply":
        """Raise the exception matching the status word, or return self when there is none."""
        if self.status is None:
            return self
        raise STATUS_EXCEPTIONS.get(self.status, ElmError)(self.status, self.command)


def clean_reply(command: str, raw: str) -> ElmReply:
    """
    Turn the bytes an adapter printed into a reply, dropping echo and chatter.

    Exposed separately from the transport so that malformed output can be tested without
    a device, and so that a captured session can be replayed through it.
    """
    body = raw.replace(PROMPT, " ")
    # The adapter separates lines with a carriage return, and adds a line feed when ATL1
    # is in force. Splitting on both copes with either setting and with a clone that
    # emits the pair in the wrong order.
    pieces = re.split(r"[\r\n]+", body)

    normalised_command = command.replace(" ", "").upper()
    status: Optional[str] = None
    lines: List[str] = []

    for piece in pieces:
        text = piece.strip()
        if not text:
            continue

        # Autodetection chatter may be glued to the line that follows it, more than once.
        while text.upper().startswith(SEARCHING):
            text = text[len(SEARCHING) :].strip()
        if not text:
            continue

        upper = " ".join(text.upper().split())

        if upper.replace(" ", "") == normalised_command and not lines:
            # The echo of our own command, still on because ATE0 has not landed yet.
            continue

        if any(upper.startswith(noise) for noise in _CHATTER) and "ERROR" not in upper:
            continue

        matched = _match_status(upper)
        if matched is not None:
            status = matched
            continue

        lines.append(text)

    return ElmReply(command=command, raw=raw, lines=tuple(lines), status=status)


def _match_status(upper: str) -> Optional[str]:
    """Identify a status word in a cleaned line, or return None when it carries data."""
    if upper == "?":
        return "?"
    if _ERR_PATTERN.match(upper.replace(" ", "")):
        return upper.replace(" ", "")
    for keyword in STATUS_EXCEPTIONS:
        if keyword != "?" and keyword in upper:
            return keyword
    return None


class ElmProtocol:
    """
    Sends commands to an ELM327 and returns cleaned replies.

    Args:
        transport: The byte stream to the adapter.
        timeout: Default seconds to wait for a prompt.
    """

    def __init__(self, transport: ElmTransport, timeout: float = DEFAULT_COMMAND_TIMEOUT):
        self.transport = transport
        self.timeout = timeout
        self.last_raw = ""

    def send(self, command: str, timeout: Optional[float] = None) -> ElmReply:
        """
        Send one command and read until the adapter prompts for the next.

        A status word is reported in the reply rather than raised: the caller knows
        whether `NO DATA` means "unsupported PID, move on" or "the session is broken".

        Args:
            command: The command text, without its terminator.
            timeout: Seconds to wait for the prompt, defaulting to the instance timeout.
        """
        self.transport.reset_input_buffer()
        self.transport.write(command.encode("ascii", errors="ignore") + TERMINATOR)
        raw = self._read_until_prompt(command, self.timeout if timeout is None else timeout)
        self.last_raw = raw
        return clean_reply(command, raw)

    def _read_until_prompt(self, command: str, timeout: float) -> str:
        """Accumulate bytes until the prompt appears, or give up at the deadline."""
        buffer = bytearray()
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DiagnosticTimeout(
                    f"the ELM327 did not answer {command!r} within {timeout:g} s (received {bytes(buffer)!r})"
                )
            chunk = self.transport.read(min(remaining, POLL_INTERVAL))
            if not chunk:
                continue
            buffer.extend(chunk)
            if PROMPT.encode("ascii") in buffer:
                # Everything after the prompt belongs to no reply; the adapter is idle.
                return buffer.decode("ascii", errors="replace")
