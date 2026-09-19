"""
Unit tests for the native CAN diagnostic interface.

This backend speaks ISO-TP over the CAN adapters the studio already supports, so it is
exercised against a scripted bus rather than hardware. The frame source is injectable
because the GUI capture loop owns the only reader on a connected bus, the same
arrangement `canopen_studio.bridge` already relies on.
"""

import queue

import can
import pytest

from canopen_studio.diag import DiagnosticResponse, NotConnectedError
from canopen_studio.diag.isotp import FUNCTIONAL_REQUEST_11BIT
from canopen_studio.diag.native import (
    NativeCanDiagnosticInterface,
    QueueFrameSource,
    response_to_request_id,
)


class ScriptedBus:
    """A python-can Bus stand-in that answers scripted frames to each request."""

    def __init__(self, script=None):
        self.sent: list[can.Message] = []
        self.shutdown_called = False
        # Maps the data of a request frame to the response frames it triggers.
        self.script = dict(script or {})
        self.inbox: queue.Queue = queue.Queue()

    def send(self, msg, timeout=None):
        self.sent.append(msg)
        for source, data in self.script.get(bytes(msg.data), []):
            self.inbox.put(can.Message(arbitration_id=source, is_extended_id=False, data=data))

    def recv(self, timeout=None):
        try:
            return self.inbox.get(timeout=timeout if timeout else 0.001)
        except queue.Empty:
            return None

    def shutdown(self):
        self.shutdown_called = True


def request_frame(*payload: int) -> bytes:
    """The ISO-TP single frame carrying a short request."""
    return bytes([len(payload), *payload]) + bytes(8 - 1 - len(payload))


RPM_REQUEST = request_frame(0x01, 0x0C)
RPM_RESPONSE = bytes([0x04, 0x41, 0x0C, 0x1A, 0xF8, 0, 0, 0])


def make_interface(script=None, **kwargs):
    bus = ScriptedBus(script)
    iface = NativeCanDiagnosticInterface(bus, settle_time=0.01, **kwargs)
    iface.default_timeout = 0.3
    return bus, iface


class TestLifecycle:
    def test_description_names_the_addressing_in_use(self):
        _, iface = make_interface()

        assert "7DF" in iface.description

    def test_a_borrowed_bus_is_not_shut_down(self):
        """The GUI owns its bus; a diagnostic session must not close it underneath."""
        bus, iface = make_interface()

        iface.open()
        iface.close()

        assert bus.shutdown_called is False

    def test_an_owned_bus_is_shut_down(self):
        bus, iface = make_interface()
        iface.owns_bus = True

        iface.open()
        iface.close()

        assert bus.shutdown_called is True

    def test_requests_before_opening_are_refused(self):
        _, iface = make_interface()

        with pytest.raises(NotConnectedError):
            iface.service(0x01, 0x0C)


class TestSingleFrameExchange:
    def test_request_is_sent_as_an_iso_tp_single_frame(self):
        bus, iface = make_interface({RPM_REQUEST: [(0x7E8, RPM_RESPONSE)]})
        iface.open()

        iface.service(0x01, 0x0C)

        assert bytes(bus.sent[0].data) == RPM_REQUEST

    def test_request_goes_to_the_functional_address_by_default(self):
        bus, iface = make_interface({RPM_REQUEST: [(0x7E8, RPM_RESPONSE)]})
        iface.open()

        iface.service(0x01, 0x0C)

        assert bus.sent[0].arbitration_id == FUNCTIONAL_REQUEST_11BIT

    def test_response_is_reassembled_and_attributed_to_its_ecu(self):
        _, iface = make_interface({RPM_REQUEST: [(0x7E8, RPM_RESPONSE)]})
        iface.open()

        replies = iface.service(0x01, 0x0C)

        assert replies == [DiagnosticResponse(source=0x7E8, data=bytes([0x41, 0x0C, 0x1A, 0xF8]))]

    def test_padding_byte_is_configurable(self):
        bus, iface = make_interface(padding=0xAA)
        iface.open()

        iface.service(0x01, 0x0C)

        assert bytes(bus.sent[0].data)[3:] == bytes([0xAA] * 5)

    def test_an_unanswered_request_returns_nothing_rather_than_raising(self):
        """An unsupported PID is an ordinary outcome of a scan, not an error."""
        _, iface = make_interface()
        iface.open()

        assert iface.service(0x01, 0x0C) == []

    def test_frames_from_other_identifiers_are_ignored(self):
        """A CANopen heartbeat sharing the bus must not be parsed as a response."""
        bus, iface = make_interface({RPM_REQUEST: [(0x701, bytes([0x05, 0, 0, 0, 0, 0, 0, 0]))]})
        iface.open()

        assert iface.service(0x01, 0x0C) == []


class TestMultiEcuExchange:
    def test_every_answering_ecu_is_reported(self):
        script = {RPM_REQUEST: [(0x7E8, RPM_RESPONSE), (0x7E9, bytes([0x04, 0x41, 0x0C, 0x20, 0x00, 0, 0, 0]))]}
        _, iface = make_interface(script)
        iface.open()

        replies = iface.service(0x01, 0x0C)

        assert sorted(r.source for r in replies) == [0x7E8, 0x7E9]

    def test_a_malformed_reply_does_not_hide_a_good_one(self):
        """One ECU emitting garbage must not cost the answer of the others."""
        script = {RPM_REQUEST: [(0x7E8, bytes([0x07, 0x41])), (0x7E9, RPM_RESPONSE)]}
        _, iface = make_interface(script)
        iface.open()

        replies = iface.service(0x01, 0x0C)

        assert [r.source for r in replies] == [0x7E9]

    def test_a_malformed_reply_is_recorded_for_the_caller(self):
        script = {RPM_REQUEST: [(0x7E8, bytes([0x07, 0x41]))]}
        _, iface = make_interface(script)
        iface.open()

        iface.service(0x01, 0x0C)

        assert len(iface.last_errors) == 1
        assert "0x7E8" in iface.last_errors[0]

    def test_errors_are_cleared_between_requests(self):
        script = {RPM_REQUEST: [(0x7E8, bytes([0x07, 0x41]))], request_frame(0x01, 0x0D): []}
        _, iface = make_interface(script)
        iface.open()
        iface.service(0x01, 0x0C)

        iface.service(0x01, 0x0D)

        assert iface.last_errors == []


class TestMultiFrameResponse:
    VIN_RESPONSE = [
        (0x7E8, bytes([0x10, 0x14, 0x49, 0x02, 0x01, 0x31, 0x44, 0x34])),
        (0x7E8, bytes([0x21, 0x47, 0x50, 0x30, 0x30, 0x52, 0x35, 0x35])),
        (0x7E8, bytes([0x22, 0x42, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36])),
    ]

    def test_multi_frame_response_is_reassembled(self):
        _, iface = make_interface({request_frame(0x09, 0x02): self.VIN_RESPONSE})
        iface.open()

        replies = iface.service(0x09, 0x02)

        assert replies[0].data[:3] == bytes([0x49, 0x02, 0x01])
        assert len(replies[0].data) == 20

    def test_a_first_frame_is_answered_with_flow_control(self):
        """Without it the ECU stops after the first frame and the VIN never arrives."""
        bus, iface = make_interface({request_frame(0x09, 0x02): self.VIN_RESPONSE})
        iface.open()

        iface.service(0x09, 0x02)

        flow_control = [m for m in bus.sent if m.data[0] >> 4 == 0x3]
        assert bytes(flow_control[0].data) == bytes([0x30, 0x00, 0x00, 0, 0, 0, 0, 0])

    def test_flow_control_is_addressed_to_the_ecu_that_sent_the_first_frame(self):
        """It must go to that ECU's physical address, never to the functional one."""
        bus, iface = make_interface({request_frame(0x09, 0x02): self.VIN_RESPONSE})
        iface.open()

        iface.service(0x09, 0x02)

        flow_control = [m for m in bus.sent if m.data[0] >> 4 == 0x3]
        assert flow_control[0].arbitration_id == 0x7E0

    def test_a_long_request_is_split_into_several_frames(self):
        """Six bytes fit the first frame and seven each consecutive one."""
        bus, iface = make_interface(flow_control_timeout=0.01)
        iface.open()

        iface.request(bytes([0x2E, 0xF1, 0x90] + list(range(12))))

        assert [m.data[0] for m in bus.sent] == [0x10, 0x21, 0x22]

    def test_a_segmented_request_is_sent_even_without_flow_control(self):
        """Some gateways stay silent and still accept the rest; abandoning helps nobody."""
        bus, iface = make_interface(flow_control_timeout=0.01)
        iface.open()

        iface.request(bytes([0x2E, 0xF1, 0x90] + list(range(10))))

        assert len(bus.sent) == 2


class TestResponsePending:
    def test_a_pending_rejection_is_not_returned_as_the_answer(self):
        """0x78 means "still working"; returning it would abort a slow read."""
        script = {
            request_frame(0x03): [
                (0x7E8, bytes([0x03, 0x7F, 0x03, 0x78, 0, 0, 0, 0])),
                (0x7E8, bytes([0x02, 0x43, 0x00, 0, 0, 0, 0, 0])),
            ]
        }
        _, iface = make_interface(script)
        iface.open()

        replies = iface.service(0x03)

        assert [r.data for r in replies] == [bytes([0x43, 0x00])]

    def test_an_ordinary_rejection_is_returned(self):
        script = {request_frame(0x03): [(0x7E8, bytes([0x03, 0x7F, 0x03, 0x12, 0, 0, 0, 0]))]}
        _, iface = make_interface(script)
        iface.open()

        replies = iface.service(0x03)

        assert replies[0].is_negative is True
        assert replies[0].response_code == 0x12


class TestAddressingHelpers:
    def test_a_standard_response_maps_back_to_its_physical_request(self):
        assert response_to_request_id(0x7E8) == 0x7E0
        assert response_to_request_id(0x7EF) == 0x7E7

    def test_an_extended_response_maps_back_to_its_physical_request(self):
        assert response_to_request_id(0x18DAF110, extended=True) == 0x18DA10F1


class TestQueueFrameSource:
    def test_a_fed_frame_is_returned(self):
        source = QueueFrameSource()
        msg = can.Message(arbitration_id=0x7E8, data=b"\x01")

        source.feed(msg)

        assert source.recv(0.01) is msg

    def test_an_empty_source_returns_nothing(self):
        assert QueueFrameSource().recv(0.01) is None

    def test_clear_drops_frames_left_over_from_an_earlier_request(self):
        source = QueueFrameSource()
        source.feed(can.Message(arbitration_id=0x7E8, data=b"\x01"))

        source.clear()

        assert source.recv(0.01) is None

    def test_a_full_source_drops_the_oldest_frame(self):
        """A stalled session must not grow without bound behind the capture loop."""
        source = QueueFrameSource(maxsize=2)
        for index in range(4):
            source.feed(can.Message(arbitration_id=0x7E8, data=bytes([index])))

        assert [source.recv(0.01).data[0] for _ in range(2)] == [2, 3]

    def test_an_injected_source_is_used_instead_of_the_bus(self):
        """This is how the GUI hands frames over without a second reader on the bus."""
        source = QueueFrameSource()

        class FeedingBus(ScriptedBus):
            """Stands in for a capture loop pushing what it reads into the session."""

            def send(self, msg, timeout=None):
                self.sent.append(msg)
                source.feed(can.Message(arbitration_id=0x7E8, is_extended_id=False, data=RPM_RESPONSE))

        iface = NativeCanDiagnosticInterface(FeedingBus(), source=source, settle_time=0.01)
        iface.default_timeout = 0.3
        iface.open()

        replies = iface.service(0x01, 0x0C)

        assert replies[0].data == bytes([0x41, 0x0C, 0x1A, 0xF8])

    def test_frames_left_over_from_an_earlier_request_are_dropped(self):
        """A late answer to the previous PID must not be reported as this one's."""
        source = QueueFrameSource()
        iface = NativeCanDiagnosticInterface(ScriptedBus(), source=source, settle_time=0.01)
        iface.default_timeout = 0.05
        iface.open()
        source.feed(can.Message(arbitration_id=0x7E8, is_extended_id=False, data=RPM_RESPONSE))

        assert iface.service(0x01, 0x0D) == []
