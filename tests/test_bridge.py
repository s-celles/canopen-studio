"""
Unit tests for the CAN <-> network bridge.
"""

import time
import can
import pytest

from canopen_studio.bridge import CanBridge


class FakeBus:
    """python-can Bus stand-in: records sent frames and replays a scripted inbox."""

    def __init__(self, inbox=None):
        self.sent = []
        self.inbox = list(inbox or [])
        self.shutdown_called = False

    def send(self, msg, timeout=None):
        self.sent.append(msg)

    def recv(self, timeout=None):
        if self.inbox:
            return self.inbox.pop(0)
        time.sleep(0.005)
        return None

    def shutdown(self):
        self.shutdown_called = True


def frame(can_id=0x181, data=b"\x01\x02", extended=False):
    return can.Message(arbitration_id=can_id, data=data, is_extended_id=extended)


def wait_until(predicate, timeout=2.0):
    """Poll a condition so thread-driven tests do not rely on fixed sleeps."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def buses():
    return FakeBus(), FakeBus()


class TestForwardToNetwork:
    """The read direction: a physical bus is republished on the network."""

    def test_forwarded_frame_reaches_the_network_bus(self, buses):
        source, network = buses
        bridge = CanBridge(source, network)
        bridge.start()

        assert bridge.forward(frame(0x181, b"\x27\x00")) is True

        assert len(network.sent) == 1
        assert network.sent[0].arbitration_id == 0x181
        assert bytes(network.sent[0].data) == b"\x27\x00"
        bridge.stop()

    def test_extended_frames_keep_their_flag(self, buses):
        source, network = buses
        bridge = CanBridge(source, network)
        bridge.start()

        bridge.forward(frame(0x18FF0102, b"\x01", extended=True))

        assert network.sent[0].is_extended_id is True
        bridge.stop()

    def test_forwarding_counts_are_tracked(self, buses):
        source, network = buses
        bridge = CanBridge(source, network)
        bridge.start()

        bridge.forward(frame(0x100))
        bridge.forward(frame(0x101))

        assert bridge.stats["forwarded"] == 2
        bridge.stop()

    def test_nothing_is_forwarded_before_start(self, buses):
        source, network = buses
        bridge = CanBridge(source, network)

        assert bridge.forward(frame()) is False
        assert network.sent == []

    def test_nothing_is_forwarded_after_stop(self, buses):
        source, network = buses
        bridge = CanBridge(source, network)
        bridge.start()
        bridge.stop()

        assert bridge.forward(frame()) is False
        assert network.sent == []

    def test_network_failure_never_breaks_the_capture_loop(self, buses):
        """A send error must be swallowed: the RX loop keeps decoding the real bus."""
        source, network = buses

        def boom(msg, timeout=None):
            raise OSError("network down")

        network.send = boom
        bridge = CanBridge(source, network)
        bridge.start()

        assert bridge.forward(frame()) is False
        assert bridge.stats["errors"] == 1
        bridge.stop()


class TestInjectionIsOptIn:
    """The write direction reaches real hardware, so it must be explicitly enabled."""

    def test_injection_is_disabled_by_default(self, buses):
        source, network = buses
        network.inbox = [frame(0x200)]
        bridge = CanBridge(source, network)
        bridge.start()

        time.sleep(0.05)

        assert source.sent == []
        assert bridge.allow_inject is False
        bridge.stop()

    def test_enabled_injection_writes_to_the_real_bus(self, buses):
        source, network = buses
        network.inbox = [frame(0x200, b"\x0f")]
        bridge = CanBridge(source, network, allow_inject=True)
        bridge.start()

        assert wait_until(lambda: len(source.sent) == 1)
        assert source.sent[0].arbitration_id == 0x200
        assert bridge.stats["injected"] == 1
        bridge.stop()


class TestLoopProtection:
    """A bridged frame must not be echoed back onto the network it came from."""

    def test_injected_frame_is_not_forwarded_back(self, buses):
        source, network = buses
        injected = frame(0x300, b"\xaa")
        network.inbox = [injected]
        bridge = CanBridge(source, network, allow_inject=True)
        bridge.start()
        assert wait_until(lambda: len(source.sent) == 1)

        # The real bus echoes the frame back into the capture loop.
        assert bridge.forward(frame(0x300, b"\xaa")) is False

        assert network.sent == []
        assert bridge.stats["suppressed"] == 1
        bridge.stop()

    def test_unrelated_frames_still_pass(self, buses):
        source, network = buses
        network.inbox = [frame(0x300, b"\xaa")]
        bridge = CanBridge(source, network, allow_inject=True)
        bridge.start()
        assert wait_until(lambda: len(source.sent) == 1)

        assert bridge.forward(frame(0x301, b"\xbb")) is True
        bridge.stop()

    def test_same_frame_passes_once_the_echo_window_expires(self, buses):
        """Genuine repeated traffic (a cyclic PDO) must not be silently dropped."""
        source, network = buses
        network.inbox = [frame(0x300, b"\xaa")]
        bridge = CanBridge(source, network, allow_inject=True, echo_window=0.02)
        bridge.start()
        assert wait_until(lambda: len(source.sent) == 1)

        time.sleep(0.05)

        assert bridge.forward(frame(0x300, b"\xaa")) is True
        bridge.stop()

    def test_only_the_first_echo_is_suppressed(self, buses):
        """A single injection suppresses a single echo, not every later copy."""
        source, network = buses
        network.inbox = [frame(0x300, b"\xaa")]
        bridge = CanBridge(source, network, allow_inject=True)
        bridge.start()
        assert wait_until(lambda: len(source.sent) == 1)

        bridge.forward(frame(0x300, b"\xaa"))

        assert bridge.forward(frame(0x300, b"\xaa")) is True
        bridge.stop()


class TestLoopProtectionIsSymmetric:
    """
    A multicast socket sees its own datagrams come back, so a frame mirrored onto the
    network must not be injected onto the real bus a moment later.
    """

    def test_forwarded_frame_is_not_injected_back(self, buses):
        source, network = buses
        mirrored = frame(0x400, b"\x11\x22")
        bridge = CanBridge(source, network, allow_inject=True)
        bridge.start()

        bridge.forward(mirrored)
        network.inbox.append(frame(0x400, b"\x11\x22"))

        time.sleep(0.05)
        assert source.sent == []
        assert bridge.stats["suppressed_inject"] == 1
        bridge.stop()

    def test_genuine_remote_frame_is_still_injected(self, buses):
        source, network = buses
        bridge = CanBridge(source, network, allow_inject=True)
        bridge.start()

        bridge.forward(frame(0x400, b"\x11\x22"))
        network.inbox.append(frame(0x401, b"\x33"))

        assert wait_until(lambda: len(source.sent) == 1)
        assert source.sent[0].arbitration_id == 0x401
        bridge.stop()

    def test_a_mirrored_frame_sent_back_later_is_injected(self, buses):
        """Beyond the echo window it is genuine remote traffic, not our own reflection."""
        source, network = buses
        bridge = CanBridge(source, network, allow_inject=True, echo_window=0.02)
        bridge.start()

        bridge.forward(frame(0x400, b"\x11\x22"))
        time.sleep(0.05)
        network.inbox.append(frame(0x400, b"\x11\x22"))

        assert wait_until(lambda: len(source.sent) == 1)
        bridge.stop()

    def test_read_only_bridge_needs_no_suppression(self, buses):
        """Without injection there is no return path, so forwarding is never throttled."""
        source, network = buses
        bridge = CanBridge(source, network)
        bridge.start()

        for _ in range(3):
            assert bridge.forward(frame(0x400, b"\x11\x22")) is True

        assert bridge.stats["forwarded"] == 3
        bridge.stop()


class TestLifecycle:
    def test_status_reports_configuration_and_counters(self, buses):
        source, network = buses
        bridge = CanBridge(source, network, network_channel="239.0.0.1")
        bridge.start()
        bridge.forward(frame())

        status = bridge.get_status()

        assert status["running"] is True
        assert status["channel"] == "239.0.0.1"
        assert status["allow_inject"] is False
        assert status["stats"]["forwarded"] == 1
        bridge.stop()

    def test_stop_shuts_the_network_bus_down(self, buses):
        source, network = buses
        bridge = CanBridge(source, network)
        bridge.start()

        bridge.stop()

        assert bridge.running is False
        assert network.shutdown_called is True

    def test_stop_is_idempotent(self, buses):
        source, network = buses
        bridge = CanBridge(source, network)
        bridge.start()
        bridge.stop()
        bridge.stop()

        assert bridge.running is False

    def test_the_source_bus_is_never_shut_down(self, buses):
        """The bridge borrows the capture bus; the application owns its lifecycle."""
        source, network = buses
        bridge = CanBridge(source, network)
        bridge.start()
        bridge.stop()

        assert source.shutdown_called is False
