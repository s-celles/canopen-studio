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
        frame = canopen_core.CanFrame(0x18DAF110, b"\x02\x01\x0c", is_extended=True)
        assert frame.id == 0x18DAF110
        assert frame.is_extended
        assert frame.dlc == 3

    def test_python_can_msgpack_roundtrip(self):
        orig = canopen_core.CanFrame(0x7DF, b"\x02\x01\x0d\x00", timestamp_us=1700000000123456)
        msgpack_bytes = orig.to_msgpack()
        decoded = canopen_core.CanFrame.from_msgpack(msgpack_bytes)

        assert decoded.id == 0x7DF
        assert decoded.dlc == 4
        assert decoded.data == b"\x02\x01\x0d\x00"
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

    def test_an_fd_frame_carries_a_payload_classic_can_cannot(self):
        payload = bytes(range(64))
        frame = canopen_core.CanFrame(0x123, payload, is_fd=True, bitrate_switch=True)

        assert frame.is_fd
        assert frame.bitrate_switch
        assert frame.dlc == 64
        # The wire code is not the length: 64 bytes travel under code 15.
        assert frame.dlc_code == 15
        assert frame.data == payload

    def test_an_fd_length_the_format_cannot_express_is_refused(self):
        # Nine bytes is the first length CAN FD has no DLC code for. Padding it
        # is the caller's decision, because the pad bytes arrive as data.
        with pytest.raises(ValueError, match="CAN FD data length"):
            canopen_core.CanFrame(0x123, bytes(9), is_fd=True)

    def test_fd_survives_the_python_can_wire_format(self):
        payload = bytes(range(48))
        orig = canopen_core.CanFrame(
            0x18DAF110, payload, timestamp_us=1700000000000000, is_fd=True, bitrate_switch=True
        )
        decoded = canopen_core.CanFrame.from_msgpack(orig.to_msgpack())

        assert decoded.is_fd
        assert decoded.bitrate_switch
        assert decoded.dlc == 48
        assert decoded.data == payload

    def test_an_fd_frame_spends_eighty_compact_bytes_and_a_classic_one_still_twenty_four(self):
        classic = canopen_core.CanFrame(0x180, b"\x00\x01\x02", timestamp_us=7)
        fd = canopen_core.CanFrame(0x180, bytes(range(32)), timestamp_us=7, is_fd=True)

        assert len(classic.to_compact()) == 24
        assert len(fd.to_compact()) == 80

        decoded = canopen_core.CanFrame.from_compact(fd.to_compact())
        assert decoded.is_fd
        assert decoded.data == bytes(range(32))
        assert decoded.timestamp_us == 7


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
        rpm_frame = canopen_core.CanFrame(0x7E8, b"\x04\x41\x0c\x1f\x40")
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
        f = canopen_core.build_sdo_write(node_id=5, index=0x1017, subindex=0, data=b"\xe8\x03")
        assert f.id == 0x605
        assert f.data[0] == 0x2B  # Expedited 2 bytes

        parsed = canopen_core.decode_sdo(f)
        assert parsed is not None
        assert parsed["type"] == "EXPEDITED_DOWNLOAD_REQUEST"
        assert parsed["node_id"] == 5
        assert parsed["data"] == b"\xe8\x03"

    def test_sdo_segmented_download_decode(self):
        # Client Request Segment
        payload = bytes([0x00 | 0x10 | 0x01, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x11])
        frame = canopen_core.CanFrame(0x605, payload)
        msg = canopen_core.decode_sdo(frame)
        assert msg is not None
        assert msg["type"] == "SEGMENT_DOWNLOAD_REQUEST"
        assert msg["node_id"] == 5
        assert msg["toggle"] is True
        assert msg["is_last"] is True
        assert msg["data"] == b"\xaa\xbb\xcc\xdd\xee\xff\x11"

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
        req = b""  # Mode 01 PID 0C
        frames = canopen_core.fragment_isotp(0x7DF, req)
        assert len(frames) == 1
        assert frames[0].id == 0x7DF
        assert frames[0].data[0] == 3  # SF len = 3

        # The reassembler is multiplexed: it takes no addresses, and every
        # frame is fed with the source it arrived on.
        reasm = canopen_core.IsoTpReassembler()
        result = reasm.feed(0x7DF, bytes(frames[0].data))
        assert not result.flow_control_required, "a single frame needs no flow control"
        assert result.completed == req

    def test_multi_frame_vin_reassembly(self):
        vin_resp = b"I1FA6P8CF4H5100000"  # 20 bytes
        frames = canopen_core.fragment_isotp(0x7E8, vin_resp)
        assert len(frames) == 3

        reasm = canopen_core.IsoTpReassembler()

        # First Frame: nothing complete, and the sender must be told to continue.
        first = reasm.feed(0x7E8, bytes(frames[0].data))
        assert first.completed is None
        assert first.flow_control_required
        assert reasm.pending_sources() == [0x7E8]

        # Consecutive Frame 1: still accumulating.
        second = reasm.feed(0x7E8, bytes(frames[1].data))
        assert second.completed is None
        assert not second.flow_control_required

        # Consecutive Frame 2: complete, and the source is released.
        third = reasm.feed(0x7E8, bytes(frames[2].data))
        assert third.completed == vin_resp
        assert not third.flow_control_required
        assert reasm.pending_sources() == []


@pytest.mark.skipif(not RUST_CORE_AVAILABLE, reason="Rust canopen_core module not compiled")
class TestRustPdo:
    def test_pdo_mapping_encode_and_decode(self):
        mapping = canopen_core.PdoMapping(0x181, "CiA402_TPDO1")
        mapping.add_signal("Velocity", 0, 32, signal_type="int32", factor=1.0, offset=0.0, unit="rpm")
        mapping.add_signal("StatusWord", 32, 16, signal_type="uint16", factor=1.0, offset=0.0, unit="raw")

        frame = mapping.encode_frame([("Velocity", -2500.0), ("StatusWord", 0x0237)])
        assert frame.id == 0x181

        readings = mapping.decode_frame(frame)
        assert len(readings) == 2
        assert readings[0]["name"] == "Velocity"
        assert readings[0]["value"] == -2500.0
        assert readings[0]["unit"] == "rpm"
        assert readings[1]["name"] == "StatusWord"
        assert readings[1]["value"] == 0x0237


@pytest.mark.skipif(not RUST_CORE_AVAILABLE, reason="Rust canopen_core module not compiled")
class TestRustNmt:
    def test_build_nmt_command(self):
        f = canopen_core.NmtMaster.build_command("start", target_node=7)
        assert f.id == 0x000
        assert f.data[0] == 0x01
        assert f.data[1] == 0x07

        f_reset = canopen_core.NmtMaster.build_command("reset", target_node=0)
        assert f_reset.id == 0x000
        assert f_reset.data[0] == 0x81
        assert f_reset.data[1] == 0x00

    def test_nmt_heartbeat_tracking_and_timeout(self):
        master = canopen_core.NmtMaster()
        master.set_heartbeat_interval(node_id=12, interval_ms=100)

        # Node 12 sends Heartbeat: Operational (0x05)
        hb_frame = canopen_core.CanFrame(0x70C, b"\x05", timestamp_us=1000000)
        change = master.process_frame(hb_frame)
        assert change is not None
        node_id, old_st, new_st = change
        assert node_id == 12
        assert "Operational" in new_st

        # Check nodes list
        nodes = master.all_nodes()
        assert len(nodes) == 1
        assert nodes[0]["node_id"] == 12
        assert not nodes[0]["is_timed_out"]

        # Check timeout after 120ms (not timed out yet, grace period is 150ms)
        timed_out = master.check_timeouts(now_sec=1.12)
        assert len(timed_out) == 0

        # Check timeout after 200ms -> timed out!
        timed_out2 = master.check_timeouts(now_sec=1.20)
        assert timed_out2 == [12]
        nodes_after = master.all_nodes()
        assert nodes_after[0]["is_timed_out"]


SAMPLE_EDS_CONTENT = """
[FileInfo]
FileName=MotorDrive.eds
FileVersion=1
EDSVersion=4.0
Description=AC Motor Drive Inverter

[DeviceInfo]
VendorName=InvertCorp
VendorNumber=0x00000042
ProductName=Inverter 50kW
ProductNumber=0x00000100

[1000]
ParameterName=Device Type
ObjectType=7
DataType=0x0007
AccessType=ro
DefaultValue=0x00020192
PDOMapping=0

[1001]
ParameterName=Error Register
ObjectType=7
DataType=0x0005
AccessType=ro
DefaultValue=0
PDOMapping=1

[6040]
ParameterName=CiA 402 Controlword
ObjectType=7
DataType=0x0006
AccessType=rw
DefaultValue=0x0000
PDOMapping=1

[6041]
ParameterName=CiA 402 Statusword
ObjectType=7
DataType=0x0006
AccessType=ro
DefaultValue=0x0000
PDOMapping=1

[1A00]
ParameterName=TPDO1 Mapping
ObjectType=9
SubNumber=2

[1A00sub1]
ParameterName=TPDO1 Mapping Entry 1
ObjectType=7
DataType=0x0007
AccessType=rw
DefaultValue=0x60410010
PDOMapping=0

[1A00sub2]
ParameterName=TPDO1 Mapping Entry 2
ObjectType=7
DataType=0x0007
AccessType=rw
DefaultValue=0x10010008
PDOMapping=0
"""


@pytest.mark.skipif(not RUST_CORE_AVAILABLE, reason="Rust canopen_core module not compiled")
class TestRustEds:
    def test_parse_eds_content(self):
        eds = canopen_core.EdsFile.parse(SAMPLE_EDS_CONTENT)
        assert len(eds) == 7

        info = eds.file_info()
        assert info["file_name"] == "MotorDrive.eds"
        assert info["eds_version"] == "4.0"

        dev = eds.device_info()
        assert dev["vendor_name"] == "InvertCorp"
        assert dev["vendor_number"] == 0x42
        assert dev["product_name"] == "Inverter 50kW"

    def test_get_objects(self):
        eds = canopen_core.EdsFile.parse(SAMPLE_EDS_CONTENT)

        dev_type = eds.get_object(0x1000, 0)
        assert dev_type is not None
        assert dev_type["name"] == "Device Type"
        assert dev_type["data_type"] == "UNSIGNED32"
        assert dev_type["bit_length"] == 32
        assert dev_type["access"] == "ro"
        assert not dev_type["pdo_mapping"]

        # Non-existent object
        assert eds.get_object(0x9999, 0) is None

    def test_pdo_mappable_and_search(self):
        eds = canopen_core.EdsFile.parse(SAMPLE_EDS_CONTENT)
        mappable = eds.pdo_mappable_objects()
        assert len(mappable) == 3  # 1001, 6040, 6041

        matches = eds.find_objects_by_name("controlword")
        assert len(matches) == 1
        assert matches[0]["index"] == 0x6040
        assert matches[0]["access"] == "rw"

    def test_create_pdo_mapping_from_eds(self):
        eds = canopen_core.EdsFile.parse(SAMPLE_EDS_CONTENT)
        pdo = eds.create_pdo_mapping(cob_id=0x181, mapping_index=0x1A00)
        assert pdo.cob_id == 0x181

        frame = canopen_core.CanFrame(0x181, b"\x37\x02\x00\x00\x00\x00\x00\x00")
        readings = pdo.decode_frame(frame)
        assert len(readings) == 2
        assert readings[0]["name"] == "CiA 402 Statusword"
        assert readings[0]["value"] == 0x0237
        assert readings[1]["name"] == "Error Register"
        assert readings[1]["value"] == 0.0
