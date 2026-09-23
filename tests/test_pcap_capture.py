"""
PCAP-NG capture, as Wireshark reads it.

The block structure itself is checked in the Rust unit tests; what matters here
is that the binding reaches Python at all, and that what it writes still parses
as a capture once it has crossed the FFI boundary.
"""

import struct

import pytest

from canopen_studio.sniffer import parse_args

canopen_core = pytest.importorskip(
    "canopen_studio.canopen_core",
    reason="the compiled core is built by 'just rust-python', not by 'uv sync'",
)

BLOCK_SECTION_HEADER = 0x0A0D0D0A
BLOCK_INTERFACE_DESCRIPTION = 0x00000001
BLOCK_ENHANCED_PACKET = 0x00000006
LINKTYPE_CAN_SOCKETCAN = 227


def walk_blocks(capture: bytes):
    """Yield (type, body) for every block, using the lengths the file states."""
    offset = 0
    while offset < len(capture):
        block_type, length = struct.unpack_from("<II", capture, offset)
        assert length >= 12, "a block holds at least its own framing"
        yield block_type, capture[offset + 8 : offset + length - 4]
        offset += length
    assert offset == len(capture), "the blocks tile the capture exactly"


class TestPcapNgWriter:
    def test_a_capture_names_its_link_type_as_socketcan(self, tmp_path):
        path = tmp_path / "bus.pcapng"
        writer = canopen_core.PcapNgWriter(str(path), "slcan COM4")
        writer.close()

        blocks = list(walk_blocks(path.read_bytes()))
        assert [b[0] for b in blocks] == [BLOCK_SECTION_HEADER, BLOCK_INTERFACE_DESCRIPTION]
        (link_type,) = struct.unpack_from("<H", blocks[1][1], 0)
        assert link_type == LINKTYPE_CAN_SOCKETCAN

    def test_each_frame_becomes_one_packet_block(self, tmp_path):
        path = tmp_path / "bus.pcapng"
        with canopen_core.PcapNgWriter(str(path), "virtual") as writer:
            writer.write_frame(0x080, b"")
            writer.write_frame(0x701, bytes([0x05]))
            writer.write_frame(0x181, bytes([0x10, 0x27]))

        packets = [body for kind, body in walk_blocks(path.read_bytes()) if kind == BLOCK_ENHANCED_PACKET]
        assert len(packets) == 3

    def test_a_frame_keeps_its_id_payload_and_timestamp(self, tmp_path):
        path = tmp_path / "bus.pcapng"
        with canopen_core.PcapNgWriter(str(path), "virtual") as writer:
            writer.write_frame(0x181, bytes([0xDE, 0xAD]), 1_700_000_000_000_000)

        (packet,) = [body for kind, body in walk_blocks(path.read_bytes()) if kind == BLOCK_ENHANCED_PACKET]
        _iface, ts_high, ts_low, captured, original = struct.unpack_from("<IIIII", packet, 0)
        assert (ts_high << 32) | ts_low == 1_700_000_000_000_000
        assert captured == original == 16

        record = packet[20:36]
        assert struct.unpack_from(">I", record, 0)[0] == 0x181
        assert record[4] == 2
        assert record[8:10] == bytes([0xDE, 0xAD])

    def test_an_fd_frame_is_written_as_a_72_byte_record_under_the_same_link_type(self, tmp_path):
        # There is no separate link type for CAN FD. A reader tells the two
        # apart by the record length and the CANFD_FDF flag, so the block's
        # lengths carry the distinction.
        path = tmp_path / "bus.pcapng"
        with canopen_core.PcapNgWriter(str(path), "virtual") as writer:
            writer.write_frame(0x123, b"", None, False, False, False, None)
            writer.write_frame(0x124, bytes(range(32)), 1_700_000_000_000_000, is_fd=True, bitrate_switch=True)

        classic, fd = [body for kind, body in walk_blocks(path.read_bytes()) if kind == BLOCK_ENHANCED_PACKET]

        assert struct.unpack_from("<I", classic, 12)[0] == 16
        assert struct.unpack_from("<I", fd, 12)[0] == 72

        record = fd[20:92]
        assert record[4] == 32, "byte 4 is the payload length, not the DLC code"
        assert record[5] & 0x04, "CANFD_FDF marks the record as FD"
        assert record[5] & 0x01, "CANFD_BRS was asked for"
        assert record[6] == record[7] == 0, "the reserved bytes are what make the flags trustworthy"
        assert record[8:40] == bytes(range(32))

    def test_an_extended_frame_is_flagged_so_wireshark_reads_29_bits(self, tmp_path):
        path = tmp_path / "bus.pcapng"
        with canopen_core.PcapNgWriter(str(path), "virtual") as writer:
            writer.write_frame(0x18EAFFFE, bytes([0xEE]), None, True)

        (packet,) = [body for kind, body in walk_blocks(path.read_bytes()) if kind == BLOCK_ENHANCED_PACKET]
        assert struct.unpack_from(">I", packet[20:36], 0)[0] == 0x98EAFFFE

    def test_a_remote_frame_keeps_the_length_it_requests(self, tmp_path):
        """Its DLC has no payload behind it, so the caller has to state it."""
        path = tmp_path / "bus.pcapng"
        with canopen_core.PcapNgWriter(str(path), "virtual") as writer:
            writer.write_frame(0x123, b"", None, False, True, False, 8)

        (packet,) = [body for kind, body in walk_blocks(path.read_bytes()) if kind == BLOCK_ENHANCED_PACKET]
        record = packet[20:36]
        assert record[0] & 0x40, "RTR flag"
        assert record[4] == 8

    def test_writing_after_close_is_refused_rather_than_silently_dropped(self, tmp_path):
        path = tmp_path / "bus.pcapng"
        writer = canopen_core.PcapNgWriter(str(path), "virtual")
        writer.close()

        with pytest.raises(OSError):
            writer.write_frame(0x080, b"")

    def test_closing_twice_is_harmless(self, tmp_path):
        path = tmp_path / "bus.pcapng"
        writer = canopen_core.PcapNgWriter(str(path), "virtual")
        writer.close()
        writer.close()

    def test_a_capture_that_cannot_be_opened_raises(self, tmp_path):
        with pytest.raises(OSError):
            canopen_core.PcapNgWriter(str(tmp_path / "no" / "such" / "dir" / "bus.pcapng"), "virtual")


class TestSnifferPcapFlag:
    def test_the_sniffer_takes_a_pcap_path(self):
        args = parse_args(["--pcap", "bus.pcapng"])
        assert args.pcap == "bus.pcapng"

    def test_no_capture_is_written_unless_asked_for(self):
        assert parse_args([]).pcap is None
