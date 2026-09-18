"""
Unit tests for CAN sniffer CLI parsing and filtering.
"""

from canopen_studio.sniffer import parse_args


class TestSnifferCliArgs:
    def test_default_arguments(self):
        """Test default CLI parameters."""
        args = parse_args([])
        assert args.interface == "slcan"
        assert args.bitrate == 500000
        assert args.profile == "All / Auto"
        assert args.extended_only is False
        assert args.standard_only is False
        assert args.listen_only is False

    def test_filter_id_hex_and_dec(self):
        """Test specifying hex CAN IDs."""
        args = parse_args(["-i", "0x473"])
        assert args.id == 0x473

        args2 = parse_args(["-i", "1000"])
        assert args2.id == 1000

    def test_extended_flag(self):
        """Test extended frame filtering flag."""
        args = parse_args(["--extended-only"])
        assert args.extended_only is True

    def test_virtual_interface_and_simulation(self):
        """Test virtual simulator flags."""
        args = parse_args(["-I", "virtual", "--simulate", "-t", "5"])
        assert args.interface == "virtual"
        assert args.simulate is True
        assert args.duration == 5.0
