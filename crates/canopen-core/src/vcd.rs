/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

//! Writing reconstructed CAN waveforms as VCD, for PulseView and sigrok.
//!
//! PulseView reads VCD natively and its `can` decoder takes exactly one
//! channel, so this writes one: the RX line a transceiver would have driven.
//! Only transitions are recorded, which is what keeps a file of a long capture
//! small even at twenty samples per bit.
//!
//! The waveform is rebuilt from decoded frames, never measured — see
//! [`crate::bitstream`] for what that does and does not preserve. Because a
//! file outlives the conversation that produced it, the warning travels inside
//! it: the channel is named `CAN_RX_RECONSTRUCTED`, so PulseView shows it
//! beside the trace for as long as anybody looks at it, and the header says the
//! rest in full.

use std::io::{self, Write};

use crate::bitstream::{AckSlot, frame_bits};
use crate::frame::CanFrame;

/// What PulseView labels the trace. The name is the warning.
pub const CHANNEL_NAME: &str = "CAN_RX_RECONSTRUCTED";

/// VCD identifier for that single channel — any printable ASCII will do.
const CHANNEL_ID: char = '!';

/// How frames are laid out in time.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Timing {
    /// Place each frame at the timestamp the adapter reported.
    ///
    /// The gaps between frames are then real, to the adapter's accuracy — which
    /// is software receive time, tens of microseconds out, against a bit that
    /// lasts two at 500 kbit/s. So the frames sit roughly where they belong and
    /// their exact position means nothing.
    Timestamps,
    /// Pack frames back to back, separated by intermission only.
    ///
    /// Nothing about the timing is true any more, but the waveform is dense and
    /// easy to read: useful for teaching, where the bits are the subject and the
    /// idle stretches are not.
    Packed,
}

/// Knobs a caller may want to turn.
#[derive(Debug, Clone, Copy)]
pub struct VcdOptions {
    /// Bit rate the waveform is drawn at, which the sigrok decoder must be
    /// told as well for it to read anything back.
    pub bitrate: u32,
    /// Duration of one tick, in nanoseconds. It sets the effective sample rate:
    /// 100 ns is 20 samples per bit at 500 kbit/s, comfortably above what the
    /// decoder needs, and still only transitions on disk.
    pub tick_ns: u32,
    /// Where frames sit in time.
    pub timing: Timing,
    /// Whether the ACK slot is drawn dominant.
    pub ack: AckSlot,
}

impl Default for VcdOptions {
    fn default() -> Self {
        Self {
            bitrate: 500_000,
            tick_ns: 100,
            timing: Timing::Timestamps,
            ack: AckSlot::Acknowledged,
        }
    }
}

impl VcdOptions {
    /// Ticks one bit lasts.
    ///
    /// Rounded, because a bit is not always a whole number of ticks: every
    /// standard rate divides cleanly at 100 ns except 800 kbit/s and 83.333
    /// kbit/s, where the rounding shifts a bit edge by under one percent —
    /// far inside what the decoder's sample point tolerates.
    pub fn ticks_per_bit(&self) -> u64 {
        let ticks_per_second = 1_000_000_000u64 / u64::from(self.tick_ns);
        let bitrate = u64::from(self.bitrate.max(1));
        (ticks_per_second + bitrate / 2) / bitrate
    }

    fn ticks_per_microsecond(&self) -> u64 {
        (1_000u64 / u64::from(self.tick_ns)).max(1)
    }
}

/// Streams reconstructed frames into a VCD file.
pub struct VcdWriter<W: Write> {
    sink: W,
    options: VcdOptions,
    /// Where the next frame may start, in ticks.
    next_free_tick: u64,
    /// Timestamp of the first frame, the origin of the time axis.
    origin_us: Option<u64>,
    /// Current line level; the bus idles recessive.
    level: bool,
    frames_written: u64,
    /// Frames that had to be pushed later because the previous one was still
    /// on the wire at their reported time.
    frames_displaced: u64,
}

impl<W: Write> VcdWriter<W> {
    /// Open a waveform and write its header.
    pub fn new(sink: W, options: VcdOptions) -> io::Result<Self> {
        let mut writer = Self {
            sink,
            options,
            next_free_tick: 0,
            origin_us: None,
            level: true,
            frames_written: 0,
            frames_displaced: 0,
        };
        writer.write_header()?;
        Ok(writer)
    }

    /// Append one frame, rebuilt as a waveform.
    pub fn write_frame(&mut self, frame: &CanFrame) -> io::Result<()> {
        // The waveform is rebuilt by `frame_bits`, which speaks classic CAN:
        // FD switches bit rate mid-frame, stuffs differently and uses CRC-17
        // or CRC-21 instead of CRC-15. Drawing one with the classic rules
        // would produce a picture no analyser agrees with.
        if frame.is_fd {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "CAN FD frame cannot be rebuilt with the classic CAN bit rules",
            ));
        }
        let bits = frame_bits(frame, self.options.ack);
        let ticks_per_bit = self.options.ticks_per_bit();

        let start = match self.options.timing {
            Timing::Packed => self.next_free_tick,
            Timing::Timestamps => {
                let origin = *self.origin_us.get_or_insert(frame.timestamp_us);
                let elapsed_us = frame.timestamp_us.saturating_sub(origin);
                let wanted = elapsed_us * self.options.ticks_per_microsecond();
                if wanted < self.next_free_tick {
                    // Two frames cannot share the wire. The adapter's clock is
                    // coarser than a frame is long, so this is common and not
                    // an error — it is counted and reported, not hidden.
                    self.frames_displaced += 1;
                    self.next_free_tick
                } else {
                    wanted
                }
            }
        };

        let mut tick = start;
        for &bit in &bits {
            if bit != self.level {
                self.level = bit;
                writeln!(self.sink, "#{tick}")?;
                writeln!(self.sink, "{}{}", u8::from(bit), CHANNEL_ID)?;
            }
            tick += ticks_per_bit;
        }

        // The last bits are recessive, so the line is already idle; make sure
        // the file says so at the end of the frame rather than leaving the
        // final transition dangling.
        if !self.level {
            self.level = true;
            writeln!(self.sink, "#{tick}")?;
            writeln!(self.sink, "1{CHANNEL_ID}")?;
        }

        self.next_free_tick = tick;
        self.frames_written += 1;
        Ok(())
    }

    /// Close the waveform, returning how many frames were written and how many
    /// had to be moved to keep the wire to one frame at a time.
    pub fn finish(mut self) -> io::Result<(u64, u64)> {
        // One last timestamp, so a reader knows how long the capture ran.
        writeln!(self.sink, "#{}", self.next_free_tick)?;
        self.sink.flush()?;
        Ok((self.frames_written, self.frames_displaced))
    }

    fn write_header(&mut self) -> io::Result<()> {
        let options = self.options;
        writeln!(self.sink, "$version")?;
        writeln!(
            self.sink,
            "  CAN & CANopen Studio {} — reconstructed waveform",
            env!("CARGO_PKG_VERSION")
        )?;
        writeln!(self.sink, "$end")?;

        writeln!(self.sink, "$comment")?;
        writeln!(self.sink, "  THIS IS A RECONSTRUCTION, NOT A MEASUREMENT.")?;
        writeln!(
            self.sink,
            "  These bits were computed from frames a CAN adapter had already"
        )?;
        writeln!(
            self.sink,
            "  decoded. Frame structure, bit stuffing and CRC-15 are exact."
        )?;
        writeln!(self.sink, "  Everything else is not:")?;
        writeln!(
            self.sink,
            "    - the ACK slot is drawn {}, which the adapter never reported;",
            match options.ack {
                AckSlot::Acknowledged => "dominant by assumption",
                AckSlot::Unanswered => "recessive by request",
            }
        )?;
        writeln!(
            self.sink,
            "    - errors, retransmissions and lost arbitration are absent,"
        )?;
        writeln!(
            self.sink,
            "      the controller resolved them before we saw the frame;"
        )?;
        writeln!(
            self.sink,
            "    - frames the adapter dropped are missing, with nothing to show it."
        )?;
        match options.timing {
            Timing::Timestamps => writeln!(
                self.sink,
                "  Frames sit at adapter timestamps: software receive time, tens of\n  microseconds out, against a bit lasting {} ns.",
                1_000_000_000u64 / u64::from(options.bitrate.max(1))
            )?,
            Timing::Packed => writeln!(
                self.sink,
                "  Frames are packed back to back. The time axis carries no meaning."
            )?,
        }
        writeln!(
            self.sink,
            "  Decode with sigrok's 'can' decoder at nominal_bitrate={}.",
            options.bitrate
        )?;
        writeln!(self.sink, "$end")?;

        writeln!(self.sink, "$timescale {} ns $end", options.tick_ns)?;
        writeln!(self.sink, "$scope module can $end")?;
        writeln!(self.sink, "$var wire 1 {CHANNEL_ID} {CHANNEL_NAME} $end")?;
        writeln!(self.sink, "$upscope $end")?;
        writeln!(self.sink, "$enddefinitions $end")?;

        // An idle bus, from the first tick.
        writeln!(self.sink, "#0")?;
        writeln!(self.sink, "1{CHANNEL_ID}")?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame_at(id: u32, payload: &[u8], timestamp_us: u64) -> CanFrame {
        CanFrame::new_with_timestamp(id, payload, timestamp_us).expect("payload fits")
    }

    fn write(frames: &[CanFrame], options: VcdOptions) -> (String, u64, u64) {
        let mut buffer = Vec::new();
        let mut writer = VcdWriter::new(&mut buffer, options).unwrap();
        for frame in frames {
            writer.write_frame(frame).unwrap();
        }
        let (written, displaced) = writer.finish().unwrap();
        (String::from_utf8(buffer).unwrap(), written, displaced)
    }

    #[test]
    fn a_bit_lasts_twenty_ticks_at_five_hundred_kilobits() {
        let options = VcdOptions::default();
        assert_eq!(options.ticks_per_bit(), 20);
    }

    #[test]
    fn a_megabit_bus_still_gets_ten_samples_per_bit() {
        let options = VcdOptions {
            bitrate: 1_000_000,
            ..VcdOptions::default()
        };
        assert_eq!(options.ticks_per_bit(), 10);
    }

    #[test]
    fn a_rate_that_does_not_divide_evenly_is_rounded_rather_than_refused() {
        let options = VcdOptions {
            bitrate: 800_000,
            ..VcdOptions::default()
        };
        // 12.5 ticks a bit; 13 is under one percent out.
        assert_eq!(options.ticks_per_bit(), 13);
    }

    #[test]
    fn the_channel_name_carries_the_warning_into_pulseview() {
        let (text, _, _) = write(&[frame_at(0x123, &[0xAA], 0)], VcdOptions::default());
        assert!(text.contains("$var wire 1 ! CAN_RX_RECONSTRUCTED $end"));
    }

    #[test]
    fn the_header_says_plainly_that_this_is_not_a_measurement() {
        let (text, _, _) = write(&[frame_at(0x123, &[0xAA], 0)], VcdOptions::default());
        assert!(text.contains("THIS IS A RECONSTRUCTION, NOT A MEASUREMENT."));
        assert!(text.contains("nominal_bitrate=500000"));
    }

    #[test]
    fn the_capture_opens_on_an_idle_recessive_bus() {
        let (text, _, _) = write(&[frame_at(0x123, &[0xAA], 0)], VcdOptions::default());
        let body = text.split("$enddefinitions $end\n").nth(1).unwrap();
        assert!(body.starts_with("#0\n1!\n"));
    }

    #[test]
    fn timestamps_put_a_gap_between_frames_that_arrived_apart() {
        let frames = [
            frame_at(0x123, &[0xAA], 1_000_000),
            frame_at(0x124, &[0xBB], 1_010_000), // 10 ms later
        ];
        let (text, written, displaced) = write(&frames, VcdOptions::default());

        assert_eq!((written, displaced), (2, 0));
        // 10 ms at 100 ns a tick is 100_000 ticks, so the second frame starts
        // there — the first is far shorter than that.
        let ticks: Vec<u64> = text
            .lines()
            .filter_map(|line| line.strip_prefix('#'))
            .filter_map(|t| t.parse().ok())
            .collect();
        assert!(
            ticks.iter().any(|&t| t >= 100_000),
            "the second frame sits at its own timestamp"
        );
    }

    #[test]
    fn packing_ignores_timestamps_entirely() {
        let far_apart = [
            frame_at(0x123, &[0xAA], 1_000_000),
            frame_at(0x124, &[0xBB], 9_000_000), // 8 seconds later
        ];
        let options = VcdOptions {
            timing: Timing::Packed,
            ..VcdOptions::default()
        };
        let (text, _, _) = write(&far_apart, options);

        let last_tick: u64 = text
            .lines()
            .filter_map(|line| line.strip_prefix('#'))
            .filter_map(|t| t.parse::<u64>().ok())
            .max()
            .unwrap();
        // Two frames back to back are a few thousand ticks, not the eighty
        // million that eight seconds would be.
        assert!(last_tick < 10_000, "packed frames stay adjacent");
    }

    #[test]
    fn frames_too_close_to_fit_are_pushed_later_and_counted() {
        // Same microsecond: the wire cannot carry both at once.
        let frames = [
            frame_at(0x123, &[0xAA], 5_000),
            frame_at(0x124, &[0xBB], 5_000),
        ];
        let (_, written, displaced) = write(&frames, VcdOptions::default());

        assert_eq!(written, 2);
        assert_eq!(displaced, 1, "the second had to move, and says so");
    }

    #[test]
    fn a_transition_is_written_only_when_the_level_actually_changes() {
        let (text, _, _) = write(&[frame_at(0x2AA, &[0x55], 0)], VcdOptions::default());
        let values: Vec<&str> = text
            .lines()
            .filter(|line| line.ends_with('!') && line.len() == 2)
            .collect();

        for pair in values.windows(2) {
            assert_ne!(pair[0], pair[1], "no transition repeats a level");
        }
    }
}
