/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * LAWICEL / SLCAN ASCII serial transport (CANUSB, USBtin, CANable).
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

//! SLCAN speaks ASCII lines terminated by a carriage return. A data frame is
//! a letter, the identifier, one hex digit of length and the payload:
//!
//! ```text
//!   t 1AB 2 DEAD            standard data, id 0x1AB, 2 bytes
//!   T 0000CAFE 1 FF         extended data, id 0xCAFE, 1 byte
//!   r 1AB 8                 standard remote, asks for 8 bytes
//!   R 0000CAFE 0            extended remote
//! ```
//!
//! CAN FD adds four more letters, and one trap with them: the length digit is
//! the **DLC code**, not the byte count, so `F` asks for 64 bytes and there is
//! no digit that means 9.
//!
//! ```text
//!   d 1AB 9 <12 bytes>      FD standard, no bitrate switch
//!   D 0000CAFE F <64 bytes> FD extended, no bitrate switch
//!   b 1AB 2 DEAD            FD standard, bitrate switch
//!   B 0000CAFE 0            FD extended, bitrate switch, empty
//! ```
//!
//! FD has no standard here to follow: the firmwares disagree, some using `x`
//! and `X`, others a leading `0` or `1`. This follows `python-can`'s slcan
//! driver, which follows the CANable 2.0 firmware — the adapter family this
//! module already names, and the stack the studio interoperates with
//! everywhere else. Note that `x` means something else in that dialect: a
//! CANdapter spelling of a *classic* extended frame, not an FD one.
//!
//! The adapter answers a bare `\r` for success and `\x07` (BELL) for refusal.
//!
//! The codec here is deliberately free of any serial dependency so it can be
//! tested without hardware; [`SlcanBus`] is the thin part that owns a port.

use crate::frame::{CanError, CanFrame, fd_dlc_code, fd_length_for_code};

/// The bit rates a LAWICEL adapter accepts, as the `S<n>` command that selects
/// one. Anything else has to be set on the adapter itself.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SlcanBitrate {
    B10k,
    B20k,
    B50k,
    B100k,
    B125k,
    B250k,
    B500k,
    B800k,
    B1M,
}

impl SlcanBitrate {
    pub fn from_bps(bps: u32) -> Option<Self> {
        Some(match bps {
            10_000 => Self::B10k,
            20_000 => Self::B20k,
            50_000 => Self::B50k,
            100_000 => Self::B100k,
            125_000 => Self::B125k,
            250_000 => Self::B250k,
            500_000 => Self::B500k,
            800_000 => Self::B800k,
            1_000_000 => Self::B1M,
            _ => return None,
        })
    }

    pub fn bps(&self) -> u32 {
        match self {
            Self::B10k => 10_000,
            Self::B20k => 20_000,
            Self::B50k => 50_000,
            Self::B100k => 100_000,
            Self::B125k => 125_000,
            Self::B250k => 250_000,
            Self::B500k => 500_000,
            Self::B800k => 800_000,
            Self::B1M => 1_000_000,
        }
    }

    /// The command that selects this rate, without its carriage return.
    pub fn command(&self) -> &'static str {
        match self {
            Self::B10k => "S0",
            Self::B20k => "S1",
            Self::B50k => "S2",
            Self::B100k => "S3",
            Self::B125k => "S4",
            Self::B250k => "S5",
            Self::B500k => "S6",
            Self::B800k => "S7",
            Self::B1M => "S8",
        }
    }
}

/// Render a frame as the line that transmits it, carriage return included.
pub fn encode_frame(frame: &CanFrame) -> Result<String, CanError> {
    let dlc = frame.dlc as usize;
    // The length digit differs between the two grammars: classic spells the
    // byte count, FD spells the DLC code that names it.
    let length_digit = if frame.is_fd {
        fd_dlc_code(dlc).ok_or(CanError::InvalidFdLength(dlc))?
    } else {
        if dlc > 8 {
            return Err(CanError::InvalidDlc(dlc));
        }
        dlc as u8
    };
    let limit = if frame.is_extended {
        0x1FFF_FFFF
    } else {
        0x7FF
    };
    if frame.id > limit {
        return Err(CanError::InvalidId(frame.id));
    }

    let mut out = String::with_capacity(4 + 8 + 1 + dlc * 2 + 1);
    out.push(
        match (frame.is_fd, frame.is_extended, frame.bitrate_switch) {
            // CAN FD has no remote frame, so is_remote plays no part here.
            (true, false, false) => 'd',
            (true, true, false) => 'D',
            (true, false, true) => 'b',
            (true, true, true) => 'B',
            (false, ..) => match (frame.is_extended, frame.is_remote) {
                (false, false) => 't',
                (true, false) => 'T',
                (false, true) => 'r',
                (true, true) => 'R',
            },
        },
    );
    if frame.is_extended {
        out.push_str(&format!("{:08X}", frame.id));
    } else {
        out.push_str(&format!("{:03X}", frame.id));
    }
    out.push_str(&format!("{length_digit:X}"));
    // A remote frame asks for a length and carries nothing. FD has no remote
    // frame, so an FD frame always carries its payload.
    if frame.is_fd || !frame.is_remote {
        for b in frame.payload() {
            out.push_str(&format!("{b:02X}"));
        }
    }
    out.push('\r');
    Ok(out)
}

/// Decode one line into a frame, or `None` if it is not one.
///
/// Status replies, version strings and the adapter's BELL are not frames and
/// are reported as `None` rather than as errors: a reader hands over every
/// line it sees and only some of them are traffic.
///
/// A trailing four-digit timestamp (the `Z1` option) is accepted and skipped.
/// It counts milliseconds and wraps at 60000, so it says nothing an absolute
/// timestamp could be built from; the frame is stamped with the arrival time
/// by the caller instead.
pub fn decode_line(line: &str, timestamp_us: u64) -> Option<CanFrame> {
    let line = line.trim_end_matches(['\r', '\n']);
    let (kind, rest) = line.split_at_checked(1)?;
    // `x` is deliberately absent: in this dialect it is a CANdapter spelling
    // of a classic extended frame, not an FD one, and guessing either way
    // would decode a frame into the wrong format.
    let (is_extended, is_remote, is_fd, bitrate_switch) = match kind {
        "t" => (false, false, false, false),
        "T" => (true, false, false, false),
        "r" => (false, true, false, false),
        "R" => (true, true, false, false),
        "d" => (false, false, true, false),
        "D" => (true, false, true, false),
        "b" => (false, false, true, true),
        "B" => (true, false, true, true),
        _ => return None,
    };

    let id_len = if is_extended { 8 } else { 3 };
    if rest.len() < id_len + 1 {
        return None;
    }
    let (id_hex, rest) = rest.split_at(id_len);
    let id = u32::from_str_radix(id_hex, 16).ok()?;
    if is_extended && id > 0x1FFF_FFFF {
        return None;
    }

    let (dlc_hex, mut payload_hex) = rest.split_at(1);
    let code = u8::from_str_radix(dlc_hex, 16).ok()?;
    // On FD the digit is the DLC code, so `F` is 64 bytes; on classic it is
    // the byte count itself, and a digit above 8 is not this grammar.
    let dlc = if is_fd {
        fd_length_for_code(code)
    } else {
        if code > 8 {
            return None;
        }
        code
    };

    let expected = if is_remote { 0 } else { dlc as usize * 2 };
    // Anything past the payload can only be the optional Z1 timestamp.
    if payload_hex.len() == expected + 4 {
        payload_hex = &payload_hex[..expected];
    }
    if payload_hex.len() != expected {
        return None;
    }

    let mut data = [0u8; 64];
    for i in 0..expected / 2 {
        data[i] = u8::from_str_radix(&payload_hex[i * 2..i * 2 + 2], 16).ok()?;
    }

    Some(CanFrame {
        id,
        is_extended,
        is_remote,
        is_error: false,
        is_fd,
        bitrate_switch,
        // The dialect carries no error state indicator, so it is never set
        // from a line rather than guessed at.
        error_state_indicator: false,
        dlc,
        data,
        timestamp_us,
    })
}

/// Reassemble carriage-return terminated lines out of whatever the port gives.
///
/// A serial read returns whatever has arrived, which splits lines anywhere,
/// so the tail has to be carried to the next read.
#[derive(Debug, Default)]
pub struct LineAssembler {
    partial: String,
}

impl LineAssembler {
    pub fn new() -> Self {
        Self::default()
    }

    /// Feed raw bytes and take whatever complete lines that produced.
    pub fn push(&mut self, bytes: &[u8]) -> Vec<String> {
        let mut lines = Vec::new();
        for &b in bytes {
            match b {
                // A carriage return ends a line; so does the adapter's
                // refusal marker, which arrives instead of one.
                b'\r' | 0x07 => lines.push(std::mem::take(&mut self.partial)),
                _ => self.partial.push(b as char),
            }
        }
        lines
    }

    /// What has arrived since the last complete line.
    pub fn pending(&self) -> &str {
        &self.partial
    }
}

/// A CAN bus reached through a LAWICEL adapter on a serial port.
///
/// The adapter is a state machine: it has to be closed, told its bit rate and
/// reopened before it will carry traffic, and it must be closed again or it
/// keeps the line asserted for the next program that opens the port.
pub struct SlcanBus {
    port: Box<dyn serialport::SerialPort>,
    assembler: LineAssembler,
    /// Frames decoded from a read that returned more than one line.
    pending: std::collections::VecDeque<CanFrame>,
    open: bool,
}

/// The default a LAWICEL adapter enumerates at. The figure is the serial line
/// speed, which has nothing to do with the CAN bit rate.
pub const DEFAULT_SERIAL_BAUD: u32 = 115_200;

impl SlcanBus {
    /// Open `path`, put the adapter on `bitrate` and start carrying traffic.
    pub fn open(path: &str, bitrate: SlcanBitrate) -> std::io::Result<Self> {
        Self::open_with_baud(path, bitrate, DEFAULT_SERIAL_BAUD)
    }

    pub fn open_with_baud(
        path: &str,
        bitrate: SlcanBitrate,
        serial_baud: u32,
    ) -> std::io::Result<Self> {
        let port = serialport::new(path, serial_baud)
            .timeout(std::time::Duration::from_millis(50))
            .open()
            .map_err(|e| std::io::Error::other(format!("cannot open {path}: {e}")))?;

        let mut bus = Self {
            port,
            assembler: LineAssembler::new(),
            pending: std::collections::VecDeque::new(),
            open: false,
        };

        // Close first: the adapter may have been left open by whatever ran
        // before, and it refuses a bit rate change while it is carrying.
        let _ = bus.write_line("C");
        bus.write_line(bitrate.command())?;
        bus.write_line("O")?;
        bus.open = true;
        Ok(bus)
    }

    /// List the serial ports the machine offers, for a chooser to show.
    pub fn available_ports() -> Vec<String> {
        serialport::available_ports()
            .map(|ports| ports.into_iter().map(|p| p.port_name).collect())
            .unwrap_or_default()
    }

    fn write_line(&mut self, body: &str) -> std::io::Result<()> {
        use std::io::Write;
        self.port.write_all(body.as_bytes())?;
        self.port.write_all(b"\r")?;
        self.port.flush()
    }

    pub fn send(&mut self, frame: &CanFrame) -> std::io::Result<()> {
        let line = encode_frame(frame).map_err(std::io::Error::other)?;
        use std::io::Write;
        self.port.write_all(line.as_bytes())?;
        self.port.flush()
    }

    /// Take the next frame, or `None` when the read timed out with nothing.
    pub fn recv(&mut self) -> std::io::Result<Option<CanFrame>> {
        if let Some(frame) = self.pending.pop_front() {
            return Ok(Some(frame));
        }
        let mut buf = [0u8; 512];
        let n = match std::io::Read::read(&mut self.port, &mut buf) {
            Ok(n) => n,
            Err(e) if e.kind() == std::io::ErrorKind::TimedOut => return Ok(None),
            Err(e) => return Err(e),
        };
        let now_us = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_micros() as u64;
        for line in self.assembler.push(&buf[..n]) {
            if let Some(frame) = decode_line(&line, now_us) {
                self.pending.push_back(frame);
            }
        }
        Ok(self.pending.pop_front())
    }

    /// Ask the adapter a single-letter question: `V` version, `N` serial
    /// number, `F` status flags. Returns its reply without the terminator.
    pub fn command(&mut self, letter: char) -> std::io::Result<String> {
        self.write_line(&letter.to_string())?;
        let mut buf = [0u8; 128];
        let n = match std::io::Read::read(&mut self.port, &mut buf) {
            Ok(n) => n,
            Err(e) if e.kind() == std::io::ErrorKind::TimedOut => return Ok(String::new()),
            Err(e) => return Err(e),
        };
        Ok(self
            .assembler
            .push(&buf[..n])
            .into_iter()
            .find(|l| !l.is_empty())
            .unwrap_or_default())
    }

    pub fn close(&mut self) -> std::io::Result<()> {
        if self.open {
            self.write_line("C")?;
            self.open = false;
        }
        Ok(())
    }
}

impl Drop for SlcanBus {
    fn drop(&mut self) {
        // Leaving the adapter carrying would break the next program to open it.
        let _ = self.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const T: u64 = 1_700_000_000_000_000;

    #[test]
    fn every_lawicel_bit_rate_maps_to_its_command_and_back() {
        for (bps, cmd) in [
            (10_000, "S0"),
            (20_000, "S1"),
            (50_000, "S2"),
            (100_000, "S3"),
            (125_000, "S4"),
            (250_000, "S5"),
            (500_000, "S6"),
            (800_000, "S7"),
            (1_000_000, "S8"),
        ] {
            let rate = SlcanBitrate::from_bps(bps).expect("a standard rate");
            assert_eq!(rate.command(), cmd, "{bps}");
            assert_eq!(rate.bps(), bps);
        }
    }

    #[test]
    fn a_rate_the_adapter_cannot_select_is_refused() {
        assert_eq!(SlcanBitrate::from_bps(33_333), None);
        assert_eq!(SlcanBitrate::from_bps(0), None);
    }

    #[test]
    fn a_standard_data_frame_is_a_letter_an_id_a_length_and_the_payload() {
        let frame = CanFrame::new_with_timestamp(0x1AB, &[0xDE, 0xAD], T).unwrap();
        assert_eq!(encode_frame(&frame).unwrap(), "t1AB2DEAD\r");
    }

    #[test]
    fn an_extended_frame_is_announced_in_upper_case_with_eight_id_digits() {
        let frame = CanFrame::new_extended(0xCAFE, &[0xFF], T).unwrap();
        assert_eq!(encode_frame(&frame).unwrap(), "T0000CAFE1FF\r");
    }

    #[test]
    fn a_remote_frame_states_a_length_but_carries_no_payload() {
        let mut frame = CanFrame::new_with_timestamp(0x1AB, &[], T).unwrap();
        frame.is_remote = true;
        frame.dlc = 8;
        assert_eq!(encode_frame(&frame).unwrap(), "r1AB8\r");
    }

    #[test]
    fn an_empty_frame_is_a_length_of_zero() {
        let frame = CanFrame::new_with_timestamp(0x080, &[], T).unwrap();
        assert_eq!(encode_frame(&frame).unwrap(), "t0800\r");
    }

    #[test]
    fn an_id_too_wide_for_its_frame_format_is_refused() {
        let mut frame = CanFrame::new_with_timestamp(0x7FF, &[], T).unwrap();
        frame.id = 0x800;
        frame.is_extended = false;
        assert!(encode_frame(&frame).is_err());
    }

    #[test]
    fn every_frame_shape_survives_a_round_trip() {
        let mut remote = CanFrame::new_with_timestamp(0x201, &[], T).unwrap();
        remote.is_remote = true;
        remote.dlc = 4;

        for original in [
            CanFrame::new_with_timestamp(0x000, &[], T).unwrap(),
            CanFrame::new_with_timestamp(0x7FF, &[1, 2, 3, 4, 5, 6, 7, 8], T).unwrap(),
            CanFrame::new_extended(0x1FFF_FFFF, &[0xAA], T).unwrap(),
            remote,
        ] {
            let line = encode_frame(&original).unwrap();
            let back = decode_line(&line, T).expect("what we wrote must read back");
            assert_eq!(back.id, original.id, "{line}");
            assert_eq!(back.dlc, original.dlc, "{line}");
            assert_eq!(back.is_extended, original.is_extended, "{line}");
            assert_eq!(back.is_remote, original.is_remote, "{line}");
            assert_eq!(back.payload(), original.payload(), "{line}");
        }
    }

    #[test]
    fn an_fd_frame_spells_its_length_as_a_dlc_code_not_a_byte_count() {
        // The trap of this grammar: `9` asks for twelve bytes and `F` for
        // sixty-four. Spelling the count would truncate every long frame.
        let twelve = CanFrame::new_fd(0x1AB, &[0xAA; 12], 0).unwrap();
        let line = encode_frame(&twelve).unwrap();
        assert!(line.starts_with("d1AB9"), "got {line}");
        assert_eq!(line.len(), 1 + 3 + 1 + 24 + 1);

        let full = CanFrame::new_fd(0x1AB, &[0xBB; 64], 0).unwrap();
        assert!(encode_frame(&full).unwrap().starts_with("d1ABF"));
    }

    #[test]
    fn the_four_fd_letters_say_extended_and_bitrate_switch() {
        let mut frame = CanFrame::new_fd(0x1AB, &[0xDE, 0xAD], 0).unwrap();
        assert!(encode_frame(&frame).unwrap().starts_with("d1AB2"));

        frame.bitrate_switch = true;
        assert!(encode_frame(&frame).unwrap().starts_with("b1AB2"));

        let mut ext = CanFrame::new_fd(0x0000CAFE, &[0xDE, 0xAD], 0).unwrap();
        ext.is_extended = true;
        assert!(encode_frame(&ext).unwrap().starts_with("D0000CAFE2"));

        ext.bitrate_switch = true;
        assert!(encode_frame(&ext).unwrap().starts_with("B0000CAFE2"));
    }

    #[test]
    fn a_length_the_fd_grammar_cannot_spell_is_refused() {
        // There is no digit meaning nine, so the frame cannot be sent as it
        // stands and saying so beats sending twelve bytes the caller never
        // wrote.
        let mut frame = CanFrame::new_fd(0x1AB, &[0u8; 12], 0).unwrap();
        frame.dlc = 9;
        assert!(matches!(
            encode_frame(&frame).unwrap_err(),
            CanError::InvalidFdLength(9)
        ));
    }

    #[test]
    fn an_fd_line_decodes_with_the_length_its_code_names() {
        let frame = decode_line(&format!("d1AB9{}\r", "AA".repeat(12)), 42).unwrap();
        assert!(frame.is_fd);
        assert!(!frame.bitrate_switch);
        assert_eq!(frame.dlc, 12);
        assert_eq!(frame.payload(), &[0xAA; 12]);
        assert_eq!(frame.timestamp_us, 42);

        let brs = decode_line(&format!("B0000CAFEF{}\r", "55".repeat(64)), 0).unwrap();
        assert!(brs.is_fd);
        assert!(brs.bitrate_switch);
        assert!(brs.is_extended);
        assert_eq!(brs.dlc, 64);
    }

    #[test]
    fn a_classic_line_is_never_reread_as_fd() {
        // `t1AB9...` is not a twelve-byte frame: on the classic grammar a
        // digit above eight is simply not a frame.
        assert!(decode_line(&format!("t1AB9{}\r", "AA".repeat(12)), 0).is_none());
        // And `x` is a CANdapter classic extended frame in this dialect, not
        // an FD one, so it is left alone rather than guessed at.
        assert!(decode_line("x0000CAFE1FF\r", 0).is_none());
    }

    #[test]
    fn every_fd_length_survives_a_round_trip() {
        for len in [0usize, 1, 8, 12, 16, 20, 24, 32, 48, 64] {
            let payload: Vec<u8> = (0..len).map(|i| i as u8).collect();
            let sent = CanFrame::new_fd(0x123, &payload, 7).unwrap();
            let line = encode_frame(&sent).unwrap();
            let read = decode_line(&line, 7).unwrap_or_else(|| panic!("{len}: {line}"));

            assert!(read.is_fd, "{len}");
            assert_eq!(read.dlc as usize, len, "{len}");
            assert_eq!(read.payload(), &payload[..], "{len}");
        }
    }

    #[test]
    fn a_line_the_adapter_sent_about_itself_is_not_a_frame() {
        assert!(decode_line("V1013", T).is_none(), "version");
        assert!(decode_line("NA123", T).is_none(), "serial number");
        assert!(decode_line("F00", T).is_none(), "status flags");
        assert!(decode_line("", T).is_none(), "the bare OK");
        assert!(decode_line("\x07", T).is_none(), "the refusal bell");
    }

    #[test]
    fn a_truncated_or_malformed_frame_is_refused_rather_than_guessed() {
        assert!(decode_line("t1A", T).is_none(), "id cut short");
        assert!(decode_line("t1AB", T).is_none(), "no length");
        assert!(
            decode_line("t1AB2DE", T).is_none(),
            "payload short of its length"
        );
        assert!(
            decode_line("t1AB2DEADBE", T).is_none(),
            "payload past its length"
        );
        assert!(decode_line("t1AB9", T).is_none(), "a length beyond eight");
        assert!(decode_line("tZZZ0", T).is_none(), "an id that is not hex");
        assert!(
            decode_line("t1AB1ZZ", T).is_none(),
            "a payload that is not hex"
        );
    }

    #[test]
    fn the_optional_millisecond_timestamp_is_accepted_and_skipped() {
        // Z1 appends four hex digits of milliseconds after the payload.
        let frame = decode_line("t1AB2DEAD04D2", T).expect("a timestamped frame still decodes");
        assert_eq!(frame.id, 0x1AB);
        assert_eq!(frame.payload(), &[0xDE, 0xAD]);
        // It wraps at 60000, so it cannot stand in for the arrival time.
        assert_eq!(frame.timestamp_us, T);
    }

    #[test]
    fn a_read_split_mid_line_is_carried_over_to_the_next_one() {
        let mut asm = LineAssembler::new();
        assert!(asm.push(b"t1AB2DE").is_empty(), "nothing is complete yet");
        assert_eq!(asm.pending(), "t1AB2DE");
        assert_eq!(asm.push(b"AD\r"), vec!["t1AB2DEAD"]);
        assert_eq!(asm.pending(), "");
    }

    #[test]
    fn one_read_can_carry_several_frames() {
        let mut asm = LineAssembler::new();
        assert_eq!(
            asm.push(b"t0800\rt1AB2DEAD\rT0000CAFE1FF\r"),
            vec!["t0800", "t1AB2DEAD", "T0000CAFE1FF"]
        );
    }

    #[test]
    fn the_refusal_bell_ends_a_reply_the_way_a_carriage_return_does() {
        let mut asm = LineAssembler::new();
        assert_eq!(asm.push(b"\x07"), vec![""]);
        assert_eq!(asm.push(b"S9\x07"), vec!["S9"]);
    }
}
