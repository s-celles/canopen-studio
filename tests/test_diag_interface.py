"""
Unit tests for the transport-neutral diagnostic interface contract.

Both backends — an ELM327 behind a serial port and a native CAN adapter running ISO-TP —
answer the same questions, so the contract is tested once here against a stub and reused
by the backend-specific suites.
"""

import pytest

from canopen_studio.diag import (
    DiagnosticError,
    DiagnosticInterface,
    DiagnosticResponse,
    NegativeResponse,
    NotConnectedError,
    ResponseCode,
)


class StubInterface(DiagnosticInterface):
    """Minimal DiagnosticInterface recording requests and replaying canned answers."""

    def __init__(self, replies=None):
        super().__init__()
        self.requests: list[bytes] = []
        self._replies = list(replies or [])
        self.opened = False
        self.closed = False

    @property
    def description(self) -> str:
        return "stub"

    def _open(self) -> None:
        self.opened = True

    def _close(self) -> None:
        self.closed = True

    def _request(self, payload: bytes, timeout: float) -> list[DiagnosticResponse]:
        self.requests.append(payload)
        return self._replies.pop(0) if self._replies else []


def response(data: bytes, source: int = 0x7E8) -> DiagnosticResponse:
    return DiagnosticResponse(source=source, data=data)


class TestDiagnosticResponse:
    def test_service_id_and_payload_are_split(self):
        """A positive reply carries the echoed service in its first byte."""
        reply = response(bytes([0x41, 0x0C, 0x1A, 0xF8]))

        assert reply.service_id == 0x41
        assert reply.payload == bytes([0x0C, 0x1A, 0xF8])

    def test_positive_reply_echoes_the_request_mode_plus_0x40(self):
        assert response(bytes([0x41, 0x0C])).request_mode == 0x01
        assert response(bytes([0x49, 0x02])).request_mode == 0x09

    def test_negative_reply_is_recognised(self):
        """0x7F marks a rejection and carries the service and the reason."""
        reply = response(bytes([0x7F, 0x01, 0x12]))

        assert reply.is_negative is True
        assert reply.rejected_service == 0x01
        assert reply.response_code == 0x12

    def test_negative_reply_names_its_response_code(self):
        reply = response(bytes([0x7F, 0x01, 0x12]))

        assert ResponseCode.describe(reply.response_code) == "Subfunction not supported"

    def test_unknown_response_code_still_describes_itself(self):
        """An unlisted code must not raise — manufacturers add their own."""
        assert "0xAB" in ResponseCode.describe(0xAB)

    def test_positive_reply_has_no_response_code(self):
        reply = response(bytes([0x41, 0x0C]))

        assert reply.is_negative is False
        assert reply.response_code is None

    def test_empty_reply_is_rejected_at_construction(self):
        """A zero-length response is a framing bug, never a valid answer."""
        with pytest.raises(ValueError):
            DiagnosticResponse(source=0x7E8, data=b"")

    def test_response_is_hashable_and_comparable(self):
        """Responses are values, so tests and caches can compare them directly."""
        assert response(b"\x41\x0c") == response(b"\x41\x0c")
        assert len({response(b"\x41\x0c"), response(b"\x41\x0c")}) == 1

    def test_hex_rendering_is_uppercase_and_spaced(self):
        assert response(bytes([0x41, 0x0C, 0x1A])).hex() == "41 0C 1A"


class TestLifecycle:
    def test_interface_starts_closed(self):
        assert StubInterface().is_open is False

    def test_open_marks_the_interface_open(self):
        iface = StubInterface()

        iface.open()

        assert iface.is_open is True
        assert iface.opened is True

    def test_open_is_idempotent(self):
        """Re-opening must not tear down a working session."""
        iface = StubInterface()
        iface.open()
        iface.opened = False

        iface.open()

        assert iface.opened is False

    def test_close_marks_the_interface_closed(self):
        iface = StubInterface()
        iface.open()

        iface.close()

        assert iface.is_open is False
        assert iface.closed is True

    def test_close_on_a_closed_interface_is_harmless(self):
        iface = StubInterface()

        iface.close()

        assert iface.closed is False

    def test_context_manager_opens_and_closes(self):
        iface = StubInterface()

        with iface as entered:
            assert entered is iface
            assert iface.is_open is True

        assert iface.is_open is False

    def test_context_manager_closes_after_an_error(self):
        """A failure mid-session must still release the serial port or the bus."""
        iface = StubInterface()

        with pytest.raises(RuntimeError):
            with iface:
                raise RuntimeError("boom")

        assert iface.closed is True


class TestRequestContract:
    def test_request_requires_an_open_interface(self):
        iface = StubInterface()

        with pytest.raises(NotConnectedError):
            iface.request(b"\x01\x0c")

    def test_request_forwards_the_payload_verbatim(self):
        iface = StubInterface([[response(b"\x41\x0c\x1a\xf8")]])
        iface.open()

        iface.request(bytes([0x01, 0x0C]))

        assert iface.requests == [bytes([0x01, 0x0C])]

    def test_request_returns_every_responding_ecu(self):
        """A functional request is answered by each ECU that implements the service."""
        iface = StubInterface([[response(b"\x41\x0c\x1a\xf8", 0x7E8), response(b"\x41\x0c\x1b\x00", 0x7E9)]])
        iface.open()

        replies = iface.request(b"\x01\x0c")

        assert [r.source for r in replies] == [0x7E8, 0x7E9]

    def test_empty_payload_is_refused(self):
        iface = StubInterface()
        iface.open()

        with pytest.raises(ValueError):
            iface.request(b"")

    def test_service_builds_the_request_from_mode_and_arguments(self):
        iface = StubInterface([[response(b"\x41\x0c\x1a\xf8")]])
        iface.open()

        iface.service(0x01, 0x0C)

        assert iface.requests == [bytes([0x01, 0x0C])]

    def test_service_accepts_a_mode_without_arguments(self):
        iface = StubInterface([[response(b"\x43\x00")]])
        iface.open()

        iface.service(0x03)

        assert iface.requests == [bytes([0x03])]

    def test_service_rejects_a_mode_outside_a_byte(self):
        iface = StubInterface()
        iface.open()

        with pytest.raises(ValueError):
            iface.service(0x1FF)

    def test_positive_responses_filters_out_rejections(self):
        """Callers reading a value want the ECUs that answered, not the ones that refused."""
        iface = StubInterface([[response(b"\x41\x0c\x1a\xf8", 0x7E8), response(b"\x7f\x01\x12", 0x7E9)]])
        iface.open()

        replies = iface.positive_responses(0x01, 0x0C)

        assert [r.source for r in replies] == [0x7E8]

    def test_positive_responses_ignores_a_reply_to_another_service(self):
        """A stale frame from a previous request must not be mistaken for this answer."""
        iface = StubInterface([[response(b"\x49\x02\x01", 0x7E8), response(b"\x41\x0c\x1a\xf8", 0x7E9)]])
        iface.open()

        replies = iface.positive_responses(0x01, 0x0C)

        assert [r.source for r in replies] == [0x7E9]

    def test_negative_response_can_be_raised_on_demand(self):
        """Callers that need a hard failure get the reason, not an empty list."""
        iface = StubInterface([[response(b"\x7f\x01\x12", 0x7E9)]])
        iface.open()

        with pytest.raises(NegativeResponse) as excinfo:
            iface.positive_responses(0x01, 0x0C, raise_on_negative=True)

        assert excinfo.value.response_code == 0x12
        assert "Subfunction not supported" in str(excinfo.value)

    def test_negative_response_is_a_diagnostic_error(self):
        """One except clause must be enough to catch anything this layer raises."""
        assert issubclass(NegativeResponse, DiagnosticError)
        assert issubclass(NotConnectedError, DiagnosticError)

    def test_request_uses_the_default_timeout_when_none_is_given(self):
        iface = StubInterface([[response(b"\x41\x0c")]])
        iface.default_timeout = 2.5
        iface.open()
        seen = []
        iface._request = lambda payload, timeout: seen.append(timeout) or []

        iface.request(b"\x01\x0c")

        assert seen == [2.5]

    def test_explicit_timeout_overrides_the_default(self):
        iface = StubInterface()
        iface.default_timeout = 2.5
        iface.open()
        seen = []
        iface._request = lambda payload, timeout: seen.append(timeout) or []

        iface.request(b"\x01\x0c", timeout=0.25)

        assert seen == [0.25]
