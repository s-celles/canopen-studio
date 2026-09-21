"""
VCD waveform export, as sigrok and PulseView read it.

The bit-level work is checked in the Rust unit tests — stuffing, CRC-15, field
layout. What matters here is that the binding reaches Python, that the knobs do
what they say, and above all that the warning survives into the file: this is a
reconstruction, and the file will outlive the conversation that produced it.
"""

import pytest

from canopen_studio.sniffer import parse_args

canopen_core = pytest.importorskip(
    "canopen_studio.canopen_core",
    reason="the compiled core is built by 'just rust-python', not by 'uv sync'",
)


def transitions(text: str) -> list[tuple[int, int]]:
    """The (tick, level) pairs a VCD body records."""
    body = text.split("$enddefinitions $end\n", 1)[1]
    out = []
    tick = None
    for line in body.splitlines():
        if line.startswith("#"):
            tick = int(line[1:])
        elif line and tick is not None:
            out.append((tick, int(line[0])))
    return out


class TestTheWarningTravelsWithTheFile:
    def test_the_channel_name_says_reconstructed(self, tmp_path):
        """PulseView shows it beside the trace, for as long as anyone looks."""
        path = tmp_path / "bus.vcd"
        with canopen_core.VcdWriter(str(path)) as waveform:
            waveform.write_frame(0x080, b"")

        assert "CAN_RX_RECONSTRUCTED" in path.read_text(encoding="utf-8")

    def test_the_header_states_what_is_exact_and_what_is_not(self, tmp_path):
        path = tmp_path / "bus.vcd"
        canopen_core.VcdWriter(str(path)).close()
        header = path.read_text(encoding="utf-8")

        assert "THIS IS A RECONSTRUCTION, NOT A MEASUREMENT." in header
        assert "bit stuffing and CRC-15 are exact" in header
        assert "the ACK slot is drawn" in header
        assert "frames the adapter dropped are missing" in header


class TestVcdWriter:
    def test_a_waveform_starts_from_an_idle_recessive_bus(self, tmp_path):
        path = tmp_path / "bus.vcd"
        with canopen_core.VcdWriter(str(path)) as waveform:
            waveform.write_frame(0x123, bytes([0xAA]))

        assert transitions(path.read_text(encoding="utf-8"))[0] == (0, 1)

    def test_closing_reports_how_many_frames_were_written(self, tmp_path):
        path = tmp_path / "bus.vcd"
        waveform = canopen_core.VcdWriter(str(path))
        # Spaced a millisecond apart, because a frame occupies the wire for
        # roughly 220 µs at 500 kbit/s. Left to take the current time, all
        # three would land inside one frame's duration and two would be
        # displaced — the writer working, not failing, but timing-dependent
        # and therefore not something to assert on.
        for offset, id_ in enumerate((0x080, 0x181, 0x701)):
            waveform.write_frame(id_, bytes([0x01]), 1_000_000 + offset * 1_000)

        written, displaced = waveform.close()
        assert written == 3
        assert displaced == 0

    def test_frames_sharing_a_timestamp_are_shifted_and_counted(self, tmp_path):
        """The adapter's clock is coarser than a frame is long."""
        path = tmp_path / "bus.vcd"
        waveform = canopen_core.VcdWriter(str(path))
        waveform.write_frame(0x123, bytes([0xAA]), 1_000_000)
        waveform.write_frame(0x124, bytes([0xBB]), 1_000_000)

        written, displaced = waveform.close()
        assert (written, displaced) == (2, 1)

    def test_packing_drops_the_idle_stretches_timestamps_would_keep(self, tmp_path):
        spaced = tmp_path / "spaced.vcd"
        packed = tmp_path / "packed.vcd"
        for path, timing in ((spaced, "timestamps"), (packed, "packed")):
            with canopen_core.VcdWriter(str(path), 500_000, 100, timing) as waveform:
                waveform.write_frame(0x123, bytes([0xAA]), 1_000_000)
                waveform.write_frame(0x124, bytes([0xBB]), 2_000_000)  # a second later

        last = lambda text: transitions(text)[-1][0]  # noqa: E731
        assert last(packed.read_text(encoding="utf-8")) < last(spaced.read_text(encoding="utf-8"))

    def test_an_unanswered_frame_can_be_drawn_too(self, tmp_path):
        path = tmp_path / "bus.vcd"
        with canopen_core.VcdWriter(str(path), 500_000, 100, "timestamps", "unanswered") as waveform:
            waveform.write_frame(0x123, bytes([0xAA]))

        assert "recessive by request" in path.read_text(encoding="utf-8")

    def test_the_bitrate_reaches_the_header_so_the_decoder_can_be_told(self, tmp_path):
        path = tmp_path / "bus.vcd"
        canopen_core.VcdWriter(str(path), 250_000).close()

        assert "nominal_bitrate=250000" in path.read_text(encoding="utf-8")

    def test_the_tick_becomes_the_vcd_timescale(self, tmp_path):
        path = tmp_path / "bus.vcd"
        canopen_core.VcdWriter(str(path), 500_000, 50).close()

        assert "$timescale 50 ns $end" in path.read_text(encoding="utf-8")

    def test_an_unknown_timing_is_refused_by_name(self, tmp_path):
        with pytest.raises(ValueError, match="timestamps"):
            canopen_core.VcdWriter(str(tmp_path / "bus.vcd"), 500_000, 100, "sideways")

    def test_an_unknown_ack_is_refused_by_name(self, tmp_path):
        with pytest.raises(ValueError, match="acknowledged"):
            canopen_core.VcdWriter(str(tmp_path / "bus.vcd"), 500_000, 100, "packed", "maybe")

    def test_a_zero_tick_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            canopen_core.VcdWriter(str(tmp_path / "bus.vcd"), 500_000, 0)

    def test_writing_after_close_is_refused_rather_than_silently_dropped(self, tmp_path):
        waveform = canopen_core.VcdWriter(str(tmp_path / "bus.vcd"))
        waveform.close()

        with pytest.raises(OSError):
            waveform.write_frame(0x080, b"")

    def test_a_path_that_cannot_be_opened_raises(self, tmp_path):
        with pytest.raises(OSError):
            canopen_core.VcdWriter(str(tmp_path / "no" / "such" / "dir" / "bus.vcd"))


class TestSnifferVcdFlags:
    def test_the_sniffer_takes_a_vcd_path(self):
        assert parse_args(["--vcd", "bus.vcd"]).vcd == "bus.vcd"

    def test_no_waveform_is_written_unless_asked_for(self):
        assert parse_args([]).vcd is None

    def test_the_knobs_default_to_the_honest_choices(self):
        args = parse_args([])
        assert args.vcd_timing == "timestamps"
        assert args.vcd_ack == "acknowledged"
        assert args.vcd_tick_ns == 100

    def test_each_knob_can_be_turned(self):
        args = parse_args(
            ["--vcd", "b.vcd", "--vcd-timing", "packed", "--vcd-ack", "unanswered", "--vcd-tick-ns", "50"]
        )
        assert args.vcd_timing == "packed"
        assert args.vcd_ack == "unanswered"
        assert args.vcd_tick_ns == 50

    def test_a_timing_the_writer_would_reject_is_caught_at_the_command_line(self):
        with pytest.raises(SystemExit):
            parse_args(["--vcd-timing", "sideways"])
