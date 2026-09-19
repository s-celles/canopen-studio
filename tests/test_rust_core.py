"""
Unit tests for the compiled Rust core (canopen_core) accessed from Python.

Verifies PyO3 interoperability, zero-copy CanFrame operations, latency/jitter tracking,
ring buffer recording, and CANopen/OBD-II decoding.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
"""

import pytest

try:
    from canopen_studio import canopen_core
    RUST_CORE_AVAILABLE = True
except ImportError:
    RUST_CORE_AVAILABLE = False


@pytest.mark.skipif(not RUST_CORE_AVAILABLE, reason="Rust canopen_core module not compiled")
class TestRustCanFrame:
    def test_standard_frame_properties(self):
        frame = canopen_core.CanFrame(0x123, b"\x01\x02\x03\x04")
        assert frame.id == 0x123
        assert frame.dlc == 4
        assert frame.data == b"\x01\x02\x03\x04"
        assert not frame.is_extended
        assert not frame.is_remote
        assert not frame.is_error
        assert frame.timestamp_us > 0
        assert frame.timestamp_sec > 0

    def test_extended_frame(self):
        frame = canopen_core.CanFrame(0x18DAF110, b"\x02\x01\x0C", is_extended=True)
        assert frame.id == 0x18DAF110
        assert frame.is_extended
        assert frame.dlc == 3

    def test_python_can_msgpack_roundtrip(self):
        orig = canopen_core.CanFrame(0x7DF, b"\x02\x01\x0D\x00", timestamp_us=1700000000123456)
        msgpack_bytes = orig.to_msgpack()
        decoded = canopen_core.CanFrame.from_msgpack(msgpack_bytes)

        assert decoded.id == 0x7DF
        assert decoded.dlc == 4
        assert decoded.data == b"\x02\x01\x0D\x00"
        assert decoded.timestamp_us == 1700000000123456

    def test_compact_binary_roundtrip(self):
        orig = canopen_core.CanFrame(0x180, b"\x00", timestamp_us=50000)
        compact = orig.to_compact()
        assert len(compact) == 24
        decoded = canopen_core.CanFrame.from_compact(compact)

        assert decoded.id == 0x180
        assert decoded.dlc == 1
        assert decoded.data == b"\x00"
        assert decoded.timestamp_us == 50000


@pytest.mark.skipif(not RUST_CORE_AVAILABLE, reason="Rust canopen_core module not compiled")
class TestRustLatencyTracker:
    def test_jitter_and_average(self):
        tracker = canopen_core.LatencyTracker(nominal_interval_us=20000.0)
        tracker.record_sample_us(20000.0)
        tracker.record_sample_us(20500.0)
        tracker.record_sample_us(19300.0)

        stats = tracker.stats()
        assert stats["count"] == 3
        assert stats["min_us"] == 19300.0
        assert stats["max_us"] == 20500.0
        assert stats["jitter_us"] == 700.0
        assert abs(stats["avg_us"] - 19933.33) < 1.0


@pytest.mark.skipif(not RUST_CORE_AVAILABLE, reason="Rust canopen_core module not compiled")
class TestRustTraceRingBuffer:
    def test_push_and_snapshot(self):
        buf = canopen_core.TraceRingBuffer(3)
        assert buf.len() == 0
        assert buf.is_empty()

        for i in range(1, 6):
            buf.push(canopen_core.CanFrame(i, bytes([i])))

        assert buf.len() == 3
        assert buf.total_pushed() == 5

        snap = buf.snapshot()
        assert len(snap) == 3
        assert [f.id for f in snap] == [3, 4, 5]

        last_one = buf.snapshot(limit=1)
        assert len(last_one) == 1
        assert last_one[0].id == 5


@pytest.mark.skipif(not RUST_CORE_AVAILABLE, reason="Rust canopen_core module not compiled")
class TestRustDecoders:
    def test_canopen_decoding(self):
        sync = canopen_core.CanFrame(0x080, b"")
        service, desc = canopen_core.decode_canopen(sync)
        assert service == "SYNC"
        assert "SYNC" in desc

        hb = canopen_core.CanFrame(0x705, b"\x05")
        service, desc = canopen_core.decode_canopen(hb)
        assert service == "HEARTBEAT"
        assert "Operational" in desc

    def test_obd2_decoding(self):
        # PID 0x0C (RPM): 2000 rpm -> A=0x1F, B=0x40
        rpm_frame = canopen_core.CanFrame(0x7E8, b"\x04\x41\x0C\x1F\x40")
        res = canopen_core.decode_obd2(rpm_frame)
        assert res is not None
        assert res["pid"] == 0x0C
        assert res["value"] == 2000.0
        assert res["unit"] == "rpm"


@pytest.mark.skipif(not RUST_CORE_AVAILABLE, reason="Rust canopen_core module not compiled")
class TestRustSdo:
    def test_sdo_read_request_creation_and_decoding(self):
        f = canopen_core.build_sdo_read(node_id=5, index=0x1017, subindex=0)
        assert f.id == 0x605
        assert f.dlc == 8
        assert f.data[0] == 0x40  # Initiate upload

        parsed = canopen_core.decode_sdo(f)
        assert parsed is not None
        assert parsed["type"] == "UPLOAD_REQUEST"
        assert parsed["node_id"] == 5
        assert parsed["index"] == 0x1017
        assert parsed["subindex"] == 0

    def test_sdo_expedited_write_creation_and_decoding(self):
        f = canopen_core.build_sdo_write(node_id=5, index=0x1017, subindex=0, data=b"\xE8\x03")
        assert f.id == 0x605
        assert f.data[0] == 0x2B  # Expedited 2 bytes

        parsed = canopen_core.decode_sdo(f)
        assert parsed is not None
        assert parsed["type"] == "EXPEDITED_DOWNLOAD_REQUEST"
        assert parsed["node_id"] == 5
        assert parsed["data"] == b"\xE8\x03"

    def test_sdo_abort_creation_and_decoding(self):
        # 0x06090011 = Sub-index does not exist
        f = canopen_core.build_sdo_abort(node_id=5, index=0x1000, subindex=1, abort_code=0x06090011)
        assert f.id == 0x585
        assert f.data[0] == 0x80

        parsed = canopen_core.decode_sdo(f)
        assert parsed is not None
        assert parsed["type"] == "ABORT"
        assert parsed["code"] == 0x06090011
        assert "Sub-index does not exist" in parsed["description"]


@pytest.mark.skipif(not RUST_CORE_AVAILABLE, reason="Rust canopen_core module not compiled")
class TestRustIsoTp:
    def test_single_frame_fragment_and_reassembly(self):
        req = b"\x02\x01\x0C"  # Mode 01 PID 0C
        frames = canopen_core.fragment_isotp(0x7DF, req)
        assert len(frames) == 1
        assert frames[0].id == 0x7DF
        assert frames[0].data[0] == 3  # SF len = 3

        reasm = canopen_core.IsoTpReassembler(0x7DF, 0x7E8)
        data, fc = reasm.process_frame(frames[0])
        assert fc is None
        assert data == req

    def test_multi_frame_vin_reassembly(self):
        vin_resp = b"\x49\x02\x011FA6P8CF4H5100000"  # 20 bytes
        frames = canopen_core.fragment_isotp(0x7E8, vin_resp)
        assert len(frames) == 3

        reasm = canopen_core.IsoTpReassembler(0x7E8, 0x7E0)
        # Frame 0: First Frame
        data1, fc1 = reasm.process_frame(frames[0])
        assert data1 is None
        assert fc1 is not None
        assert fc1.id == 0x7E0
        assert fc1.data[0] == 0x30  # Flow Control CTS

        # Frame 1: Consecutive Frame 1
        data2, fc2 = reasm.process_frame(frames[1])
        assert data2 is None
        assert fc2 is None

        # Frame 2: Consecutive Frame 2
        data3, fc3 = reasm.process_frame(frames[2])
        assert fc3 is None
        assert data3 == vin_resp


