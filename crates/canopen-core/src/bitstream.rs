/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

//! Rebuilding the CAN bit stream a decoded frame would have produced.
//!
//! An adapter hands over frames its controller already accepted, so the wire
//! itself — the stuff bits, the CRC, the ACK slot — is gone by the time the
//! studio sees anything. This module puts it back: frame in, bits out, ready
//! to be written as a waveform PulseView can decode.
//!
//! **What comes out is a reconstruction, not a measurement.** The frame
//! structure, the stuffing and the CRC are exact, because they follow from the
//! frame. Nothing else does: whether anybody acknowledged, what the bus did
//! between frames, which frames were retransmitted after an error, and which
//! ones the adapter dropped on the floor are all unknowable from this side.
//! Every caller is expected to carry that warning through to whoever looks at
//! the result — see [`crate::vcd`], which puts it in the channel name.
//!
//! Levels are the ones a transceiver's RX pin carries: `false` is dominant,
//! `true` is recessive, and an idle bus is recessive.
//!
//! The reverse direction — [`decode_frame_bits`] — reads bits a probe really
//! saw and gives back the frame *and* the two things the wire knows and an
//! adapter does not: whether the CRC checked out, and whether anybody pulled
//! the ACK slot dominant.

use crate::frame::CanFrame;

/// CAN's CRC-15 generator polynomial, x^15 + x^14 + x^10 + x^8 + x^7 + x^4 + x^3 + 1.
const CRC15_POLYNOMIAL: u16 = 0x4599;

/// CAN FD's CRC-17 generator, x^17 + x^16 + x^14 + x^13 + x^11 + x^6 + x^4 + x^3 + x + 1,
/// without the implicit leading term. Used when the payload is 16 bytes or fewer.
const CRC17_POLYNOMIAL: u32 = 0x1685B;

/// CAN FD's CRC-21 generator, x^21 + x^20 + x^13 + x^11 + x^7 + x^4 + x^3 + 1,
/// without the implicit leading term. Used beyond 16 bytes of payload.
const CRC21_POLYNOMIAL: u32 = 0x10_2899;

/// The payload length at which CAN FD changes CRC.
pub const CRC17_MAX_PAYLOAD: usize = 16;

/// Bits of the same value after which the transmitter inserts its complement.
const STUFF_RUN: usize = 5;

/// Recessive bits closing a frame: 7 of EOF, then 3 of intermission.
const EOF_BITS: usize = 7;
const INTERMISSION_BITS: usize = 3;

/// Whether the ACK slot is driven dominant, which is a thing this side cannot
/// know: the controller consumed the answer before reporting the frame.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AckSlot {
    /// Assume somebody answered. The usual choice: a frame that reached an
    /// adapter was, in practice, acknowledged by somebody.
    Acknowledged,
    /// Leave it recessive, the picture of a frame nobody answered.
    Unanswered,
}

/// CAN's CRC-15 over a run of bits, most significant first.
pub fn crc15(bits: &[bool]) -> u16 {
    let mut crc: u16 = 0;
    for &bit in bits {
        let shifted_out = (crc >> 14) & 1 == 1;
        crc = (crc << 1) & 0x7FFF;
        if bit != shifted_out {
            crc ^= CRC15_POLYNOMIAL;
        }
    }
    crc
}

/// CAN FD's CRC-17 over a run of bits, most significant first.
///
/// The register starts at zero here, which is the polynomial on its own. A CAN
/// FD *frame* does not start it at zero — ISO 11898-1:2015 seeds it with a
/// leading one, and that seeding is what separates ISO CAN FD from the 2012
/// Bosch version whose controllers are not interoperable with it. Keeping the
/// seed out of this function is what lets it be checked against the published
/// parametrised algorithm; the framing layer supplies its own.
pub fn crc17(bits: &[bool]) -> u32 {
    crc_fd(bits, CRC17_POLYNOMIAL, 17, 0)
}

/// CAN FD's CRC-21 over a run of bits, most significant first. Same seeding
/// note as [`crc17`].
pub fn crc21(bits: &[bool]) -> u32 {
    crc_fd(bits, CRC21_POLYNOMIAL, 21, 0)
}

/// Which CRC a payload of this many bytes is covered by (ISO 11898-1).
pub fn fd_crc_width(payload_len: usize) -> u8 {
    if payload_len > CRC17_MAX_PAYLOAD {
        21
    } else {
        17
    }
}

/// What the FD framing above these CRCs still needs, and why it is not here.
///
/// Four details decide whether an FD frame this crate builds matches one a
/// controller builds, and every one of them survives a round trip against our
/// own encoder — get the parity backwards and the bits still decode perfectly
/// here while matching nothing on a real bus:
///
/// - the Gray code mapping from stuff-bit count modulo 8 to the three
///   transmitted bits (the count is Gray-coded and carries an **even** parity
///   bit, which is pinned; the mapping itself is not),
/// - whether the CRC covers the dynamic stuff bits as well as the data,
/// - the interval at which fixed stuff bits are inserted through the CRC field
///   (their value is pinned: always the complement of the preceding bit),
/// - the CRC register's initial value, which ISO 11898-1:2015 seeds with a
///   leading one where the 2012 version seeds with zero.
///
/// Confirming them needs the standard itself, or a published bit pattern for a
/// complete FD frame. Guessing would produce a frame that looks right in every
/// test this file can write.
///
/// A CAN FD CRC of either width, over bits, most significant first.
///
/// `seed` is the initial register value: zero to match the published
/// algorithm, or a leading one as an ISO CAN FD frame requires.
pub fn crc_fd(bits: &[bool], polynomial: u32, width: u8, seed: u32) -> u32 {
    let mask = (1u32 << width) - 1;
    let top = 1u32 << (width - 1);
    let mut crc = seed & mask;
    for &bit in bits {
        let shifted_out = (crc & top) != 0;
        crc = (crc << 1) & mask;
        if bit != shifted_out {
            crc ^= polynomial;
        }
    }
    crc & mask
}

/// Insert a complement bit after every run of five equal bits.
///
/// Only the span from SOF through the CRC is stuffed; the delimiters, the ACK
/// slot and everything after them are fixed-form and go out untouched.
pub fn stuff(bits: &[bool]) -> Vec<bool> {
    let mut out = Vec::with_capacity(bits.len() + bits.len() / STUFF_RUN + 1);
    let mut run_value = None;
    let mut run_length = 0usize;

    for &bit in bits {
        if Some(bit) == run_value {
            run_length += 1;
        } else {
            run_value = Some(bit);
            run_length = 1;
        }
        out.push(bit);

        if run_length == STUFF_RUN {
            out.push(!bit);
            run_value = Some(!bit);
            run_length = 1;
        }
    }
    out
}

fn push_bits(out: &mut Vec<bool>, value: u32, width: usize) {
    for shift in (0..width).rev() {
        out.push((value >> shift) & 1 == 1);
    }
}

/// The bits of one frame, from SOF to the end of intermission.
///
/// Dominant is `false`. The result already carries its stuff bits, so it is
/// what a probe on the RX pin would have seen — for this frame, in isolation.
pub fn frame_bits(frame: &CanFrame, ack: AckSlot) -> Vec<bool> {
    debug_assert!(
        !frame.is_fd,
        "frame_bits speaks classic CAN: FD switches bit rate, stuffs \
         differently and uses CRC-17/CRC-21"
    );
    // Everything from SOF through the data field, unstuffed: this is what the
    // CRC is computed over.
    let mut body = Vec::with_capacity(128);
    body.push(false); // SOF, dominant

    if frame.is_extended {
        let id = frame.id & 0x1FFF_FFFF;
        push_bits(&mut body, id >> 18, 11); // identifier A
        body.push(true); // SRR, recessive in an extended frame
        body.push(true); // IDE, recessive: the identifier continues
        push_bits(&mut body, id & 0x0003_FFFF, 18); // identifier B
        body.push(frame.is_remote); // RTR
        body.push(false); // r1
        body.push(false); // r0
    } else {
        push_bits(&mut body, frame.id & 0x7FF, 11);
        body.push(frame.is_remote); // RTR
        body.push(false); // IDE, dominant: the identifier ends here
        body.push(false); // r0
    }

    let dlc = frame.dlc.min(8);
    push_bits(&mut body, u32::from(dlc), 4);

    // A remote frame carries the length it asks for and no data behind it.
    if !frame.is_remote {
        for &byte in &frame.data[..usize::from(dlc)] {
            push_bits(&mut body, u32::from(byte), 8);
        }
    }

    let crc = crc15(&body);
    let mut stuffed_span = body;
    push_bits(&mut stuffed_span, u32::from(crc), 15);

    let mut bits = stuff(&stuffed_span);
    bits.push(true); // CRC delimiter
    bits.push(ack == AckSlot::Unanswered); // ACK slot: dominant when answered
    bits.push(true); // ACK delimiter
    bits.extend(std::iter::repeat_n(true, EOF_BITS + INTERMISSION_BITS));
    bits
}

/// Why a run of bits is not a frame.
///
/// These are not failures of this code: a bus in trouble puts exactly these on
/// the wire, and reporting which one appeared is the point of decoding from the
/// wire rather than from an adapter.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BitError {
    /// The stream is recessive where a frame would have started.
    NoStartOfFrame,
    /// The bits ran out mid-frame.
    Truncated,
    /// Six equal bits inside the stuffed span. A transmitter never sends that,
    /// so it is either an error flag or a bus fault — in both cases something
    /// an adapter would have resolved and never mentioned.
    StuffViolation,
    /// A fixed-form bit carried the wrong value: a delimiter that was dominant,
    /// or a reserved bit that was recessive.
    FormError,
}

/// A frame recovered from the wire, with what only the wire could tell.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MeasuredFrame {
    pub frame: CanFrame,
    /// The CRC over the received fields came out to zero remainder. When this
    /// is false the frame still decoded — that is deliberate. A frame whose
    /// CRC fails is evidence, and a controller would have destroyed it before
    /// any adapter could report it.
    pub crc_ok: bool,
    /// Somebody other than the transmitter pulled the ACK slot dominant.
    pub acknowledged: bool,
    /// Bits consumed, so a caller walking a stream knows where the next frame
    /// may start.
    pub bits_consumed: usize,
}

/// Reads the wire the way a receiver does: one unstuffed bit at a time,
/// dropping the transmitter's inserted complements as it goes.
///
/// It cannot know in advance how long the frame is — the DLC arrives a third of
/// the way in — so destuffing has to run alongside the parse rather than as a
/// pass over a known span.
struct WireReader<'a> {
    bits: &'a [bool],
    pos: usize,
    run_value: Option<bool>,
    run_len: usize,
    /// Stuffing covers SOF through the CRC and nothing after it. Past that,
    /// EOF's seven recessive bits are legitimate and must not be read as a run
    /// that somebody forgot to break up.
    destuffing: bool,
    /// Every unstuffed bit so far, which is what the CRC is taken over.
    body: Vec<bool>,
}

impl<'a> WireReader<'a> {
    fn new(bits: &'a [bool]) -> Self {
        Self {
            bits,
            pos: 0,
            run_value: None,
            run_len: 0,
            destuffing: true,
            body: Vec::with_capacity(128),
        }
    }

    fn raw(&mut self) -> Result<bool, BitError> {
        let bit = *self.bits.get(self.pos).ok_or(BitError::Truncated)?;
        self.pos += 1;
        Ok(bit)
    }

    /// Consume the complement the transmitter owes after five equal bits.
    fn take_pending_stuff_bit(&mut self) -> Result<(), BitError> {
        if !self.destuffing || self.run_len != STUFF_RUN {
            return Ok(());
        }
        let stuff = self.raw()?;
        if Some(stuff) == self.run_value {
            return Err(BitError::StuffViolation);
        }
        self.run_value = Some(stuff);
        self.run_len = 1;
        Ok(())
    }

    fn next(&mut self) -> Result<bool, BitError> {
        self.take_pending_stuff_bit()?;
        let bit = self.raw()?;
        if Some(bit) == self.run_value {
            self.run_len += 1;
        } else {
            self.run_value = Some(bit);
            self.run_len = 1;
        }
        if self.destuffing {
            self.body.push(bit);
        }
        Ok(bit)
    }

    fn number(&mut self, width: usize) -> Result<u32, BitError> {
        let mut value = 0u32;
        for _ in 0..width {
            value = (value << 1) | u32::from(self.next()?);
        }
        Ok(value)
    }

    /// Leave the stuffed span. A run ending on the CRC's last bit still owes a
    /// stuff bit, which sits between the CRC and its delimiter.
    fn end_stuffed_span(&mut self) -> Result<(), BitError> {
        self.take_pending_stuff_bit()?;
        self.destuffing = false;
        Ok(())
    }

    fn expect_recessive(&mut self) -> Result<(), BitError> {
        if self.next()? {
            Ok(())
        } else {
            Err(BitError::FormError)
        }
    }
}

/// Recover one frame from the bits of a probe.
///
/// The inverse of [`frame_bits`], and the reason a waveform read from a logic
/// analyzer can become a frame in the trace instead of a picture to squint at.
/// Classic CAN only: a recessive reserved bit means CAN FD, which this refuses
/// rather than mis-parsing into a plausible wrong frame.
pub fn decode_frame_bits(bits: &[bool]) -> Result<MeasuredFrame, BitError> {
    let mut wire = WireReader::new(bits);

    if wire.next()? {
        return Err(BitError::NoStartOfFrame);
    }

    let id_a = wire.number(11)?;
    // Bit 12 is RTR on a standard frame and SRR on an extended one; which it
    // was is only settled by the IDE bit that follows it.
    let rtr_or_srr = wire.next()?;
    let is_extended = wire.next()?;

    let (id, is_remote) = if is_extended {
        let id_b = wire.number(18)?;
        let rtr = wire.next()?;
        // r1 and r0. A recessive r1 is CAN FD's marker, not a classic frame.
        if wire.next()? || wire.next()? {
            return Err(BitError::FormError);
        }
        ((id_a << 18) | id_b, rtr)
    } else {
        if wire.next()? {
            return Err(BitError::FormError); // r0
        }
        (id_a, rtr_or_srr)
    };

    // Classic CAN caps the payload at eight bytes; a DLC above that still asks
    // for eight, and the extra codes only mean something to CAN FD.
    let dlc = wire.number(4)? as u8;
    let data_len = usize::from(dlc.min(8));

    let mut data = [0u8; 64];
    if !is_remote {
        for byte in data.iter_mut().take(data_len) {
            *byte = wire.number(8)? as u8;
        }
    }

    wire.number(15)?; // the transmitted CRC, checked below through the remainder
    wire.end_stuffed_span()?;

    // A CRC that covers its own remainder leaves zero. That is what a
    // controller computes, and it needs no separate comparison.
    let crc_ok = crc15(&wire.body) == 0;

    wire.expect_recessive()?; // CRC delimiter
    let acknowledged = !wire.next()?;
    wire.expect_recessive()?; // ACK delimiter
    for _ in 0..EOF_BITS {
        wire.expect_recessive()?;
    }

    Ok(MeasuredFrame {
        frame: CanFrame {
            id,
            is_extended,
            is_remote,
            is_error: false,
            // This decoder refuses a recessive r1 above, so whatever it
            // returns came off the wire as classic CAN.
            is_fd: false,
            bitrate_switch: false,
            error_state_indicator: false,
            dlc,
            data,
            timestamp_us: 0,
        },
        crc_ok,
        acknowledged,
        bits_consumed: wire.pos,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(id: u32, payload: &[u8]) -> CanFrame {
        CanFrame::new(id, payload).expect("payload fits a classic frame")
    }

    /// Bits from SOF through the CRC, before stuffing: what the CRC covers and
    /// what a receiver recovers. Standard frames carry 34 bits of framing
    /// around the payload, extended ones 54.
    fn unstuffed_len(frame: &CanFrame) -> usize {
        let framing = if frame.is_extended { 54 } else { 34 };
        let payload_bits = if frame.is_remote {
            0
        } else {
            8 * usize::from(frame.dlc.min(8))
        };
        framing + payload_bits
    }

    /// Undo the stuffing, the way a receiver does.
    ///
    /// It stops after `wanted` bits, because only the span up to the CRC is
    /// stuffed: run past it and EOF's seven recessive bits look exactly like a
    /// run that should have been broken up, and a bit gets eaten.
    fn destuff(bits: &[bool], wanted: usize) -> Vec<bool> {
        let mut out = Vec::with_capacity(wanted);
        let mut run_value = None;
        let mut run_length = 0usize;

        for &bit in bits {
            if out.len() == wanted {
                break;
            }
            if run_length == STUFF_RUN {
                // This one is the transmitter's inserted complement.
                run_value = Some(bit);
                run_length = 1;
                continue;
            }
            if Some(bit) == run_value {
                run_length += 1;
            } else {
                run_value = Some(bit);
                run_length = 1;
            }
            out.push(bit);
        }
        out
    }

    /// Strip the stuffing from a whole frame's worth of bits.
    fn frame_fields(frame: &CanFrame) -> Vec<bool> {
        let bits = frame_bits(frame, AckSlot::Acknowledged);
        destuff(&bits, unstuffed_len(frame))
    }

    fn to_number(bits: &[bool]) -> u32 {
        bits.iter().fold(0u32, |acc, &b| (acc << 1) | u32::from(b))
    }

    /// Bytes as bits, most significant first — how a CRC catalogue feeds them.
    fn bits_of(bytes: &[u8]) -> Vec<bool> {
        bytes
            .iter()
            .flat_map(|b| (0..8).rev().map(move |i| (b >> i) & 1 == 1))
            .collect()
    }

    #[test]
    fn crc15_matches_the_published_check_value() {
        // The classic CRC had no test against anything outside this file: it
        // rested on the zero-remainder property and on round trips, both of
        // which a wrong polynomial satisfies. 0x059E is what the catalogue
        // publishes for CRC-15/CAN over "123456789".
        assert_eq!(crc15(&bits_of(b"123456789")), 0x059E);
    }

    #[test]
    fn the_fd_crcs_match_the_published_check_value() {
        // "123456789" is the check string every CRC catalogue publishes a
        // value for, and the only thing here that verifies these polynomials
        // against something outside this file. A round trip against our own
        // encoder would pass just as happily with the wrong polynomial.
        let check = bits_of(b"123456789");
        assert_eq!(crc17(&check), 0x0_4F03, "CRC-17/CAN-FD");
        assert_eq!(crc21(&check), 0x0E_D841, "CRC-21/CAN-FD");
    }

    #[test]
    fn an_fd_crc_that_covers_its_own_remainder_leaves_zero() {
        // The property a controller relies on, holding for both FD widths.
        for (width, poly) in [(17u8, 0x1685Bu32), (21, 0x10_2899)] {
            let message = bits_of(&[0xDE, 0xAD, 0xBE, 0xEF]);
            let remainder = crc_fd(&message, poly, width, 0);

            let mut with_remainder = message.clone();
            for i in (0..width).rev() {
                with_remainder.push((remainder >> i) & 1 == 1);
            }
            assert_eq!(crc_fd(&with_remainder, poly, width, 0), 0, "width {width}");
        }
    }

    #[test]
    fn the_fd_crc_width_turns_over_after_sixteen_bytes() {
        assert_eq!(fd_crc_width(0), 17);
        assert_eq!(fd_crc_width(16), 17);
        assert_eq!(fd_crc_width(20), 21);
        assert_eq!(fd_crc_width(64), 21);
    }

    #[test]
    fn the_seed_is_what_separates_iso_fd_from_the_2012_version() {
        // Not a detail: a controller seeding with zero and one seeding with a
        // leading bit do not interoperate, which is why the seed is an
        // argument here rather than baked into the function.
        let message = bits_of(&[0x01, 0x02, 0x03]);
        assert_ne!(
            crc_fd(&message, 0x1685B, 17, 0),
            crc_fd(&message, 0x1685B, 17, 1 << 16)
        );
    }

    #[test]
    fn a_frame_opens_with_a_dominant_start_bit() {
        let bits = frame_bits(&frame(0x123, &[0xAA]), AckSlot::Acknowledged);
        assert!(!bits[0], "SOF is dominant");
    }

    #[test]
    fn a_frame_closes_with_ten_recessive_bits_of_eof_and_intermission() {
        let bits = frame_bits(&frame(0x123, &[0xAA]), AckSlot::Acknowledged);
        let tail = &bits[bits.len() - (EOF_BITS + INTERMISSION_BITS)..];
        assert!(tail.iter().all(|&b| b), "the bus returns to idle");
    }

    #[test]
    fn a_standard_frame_has_the_fields_the_standard_gives_it() {
        // SOF 1 + ID 11 + RTR 1 + IDE 1 + r0 1 + DLC 4 + data 8 + CRC 15.
        // Measured after destuffing: how many stuff bits a frame needs depends
        // on its contents, and the fields underneath do not.
        let sent = frame(0x2AA, &[0x55]);
        assert_eq!(frame_fields(&sent).len(), 1 + 11 + 1 + 1 + 1 + 4 + 8 + 15);
    }

    #[test]
    fn stuffing_only_ever_makes_a_frame_longer() {
        // The all-zero frame needs stuff bits; the alternating one needs far
        // fewer. Both carry the same fields.
        let flat = frame_bits(&frame(0x000, &[0x00]), AckSlot::Acknowledged);
        let alternating = frame_bits(&frame(0x2AA, &[0x55]), AckSlot::Acknowledged);

        assert!(flat.len() > alternating.len(), "stuffing costs bits");
    }

    #[test]
    fn five_equal_bits_are_followed_by_their_complement() {
        assert_eq!(
            stuff(&[false; 5]),
            vec![false, false, false, false, false, true]
        );
        assert_eq!(stuff(&[true; 5]), vec![true, true, true, true, true, false]);
    }

    #[test]
    fn the_stuffed_span_never_shows_six_equal_bits_in_a_row() {
        // An all-zero identifier and payload is the worst case for stuffing.
        let bits = frame_bits(
            &frame(0x000, &[0x00, 0x00, 0x00, 0x00]),
            AckSlot::Acknowledged,
        );
        // Only the span up to the CRC is stuffed; EOF is legitimately seven
        // recessive bits, so stop before the delimiters.
        let stuffed = &bits[..bits.len() - (3 + EOF_BITS + INTERMISSION_BITS)];
        let mut run = 1;
        for pair in stuffed.windows(2) {
            run = if pair[0] == pair[1] { run + 1 } else { 1 };
            assert!(
                run <= STUFF_RUN,
                "a run of {run} equal bits escaped stuffing"
            );
        }
    }

    #[test]
    fn a_crc_checked_over_its_own_remainder_comes_out_zero() {
        // The defining property of a CRC: appending it to the message makes
        // the whole thing divisible by the polynomial.
        let message = vec![true, false, true, true, false, false, false, true];
        let crc = crc15(&message);

        let mut with_crc = message;
        for shift in (0..15).rev() {
            with_crc.push((crc >> shift) & 1 == 1);
        }
        assert_eq!(crc15(&with_crc), 0);
    }

    #[test]
    fn a_standard_frame_reads_back_with_the_id_and_payload_it_went_in_with() {
        let sent = frame(0x123, &[0xDE, 0xAD, 0xBE, 0xEF]);
        let bits = frame_fields(&sent);

        assert_eq!(to_number(&bits[1..12]), 0x123, "identifier");
        assert!(!bits[12], "RTR is dominant on a data frame");
        assert!(!bits[13], "IDE is dominant on a standard frame");
        assert_eq!(to_number(&bits[15..19]), 4, "DLC");
        assert_eq!(to_number(&bits[19..51]), 0xDEAD_BEEF, "payload");
    }

    #[test]
    fn an_extended_frame_splits_its_identifier_around_the_substitute_bits() {
        let mut sent = frame(0x18EA_FFFE, &[0x01]);
        sent.is_extended = true;
        let bits = frame_fields(&sent);

        assert_eq!(to_number(&bits[1..12]), 0x18EA_FFFE >> 18, "identifier A");
        assert!(bits[12], "SRR is recessive");
        assert!(bits[13], "IDE is recessive, the identifier continues");
        assert_eq!(
            to_number(&bits[14..32]),
            0x18EA_FFFE & 0x3FFFF,
            "identifier B"
        );
    }

    #[test]
    fn a_remote_frame_is_dlc_without_the_bytes() {
        let mut sent = frame(0x123, &[]);
        sent.is_remote = true;
        sent.dlc = 8;
        let bits = frame_fields(&sent);

        assert!(bits[12], "RTR is recessive");
        assert_eq!(
            to_number(&bits[15..19]),
            8,
            "DLC still asks for eight bytes"
        );
        // Straight from DLC to the 15 CRC bits: no data field in between.
        assert_eq!(bits.len(), 19 + 15);
    }

    #[test]
    fn the_receiver_recomputes_a_zero_remainder_over_what_was_transmitted() {
        // What a real controller does to decide the frame arrived intact: the
        // CRC over the fields *including* the transmitted CRC is zero.
        for sent in [
            frame(0x321, &[0x11, 0x22, 0x33]),
            frame(0x000, &[0x00; 8]),
            frame(0x7FF, &[0xFF; 8]),
        ] {
            assert_eq!(crc15(&frame_fields(&sent)), 0, "id 0x{:X}", sent.id);
        }
    }

    /// Send a frame onto the wire and read it back, the way the two halves
    /// will meet once a probe feeds the decoder.
    fn round_trip(sent: &CanFrame) -> MeasuredFrame {
        decode_frame_bits(&frame_bits(sent, AckSlot::Acknowledged))
            .expect("a frame this side built is a frame the other side reads")
    }

    #[test]
    fn a_standard_frame_comes_back_off_the_wire_unchanged() {
        let sent = frame(0x123, &[0xDE, 0xAD, 0xBE, 0xEF]);
        let read = round_trip(&sent);

        assert_eq!(read.frame.id, sent.id);
        assert_eq!(read.frame.dlc, sent.dlc);
        assert_eq!(read.frame.data, sent.data);
        assert!(!read.frame.is_extended);
        assert!(!read.frame.is_remote);
        assert!(read.crc_ok, "the CRC it carried is the one it deserved");
    }

    #[test]
    fn an_extended_frame_keeps_all_twenty_nine_bits_of_its_identifier() {
        let mut sent = frame(0x18EA_FFFE, &[0x01, 0x02]);
        sent.is_extended = true;
        let read = round_trip(&sent);

        assert!(read.frame.is_extended);
        assert_eq!(read.frame.id, 0x18EA_FFFE);
        assert_eq!(read.frame.data[..2], [0x01, 0x02]);
    }

    #[test]
    fn a_remote_frame_comes_back_asking_for_bytes_it_does_not_carry() {
        let mut sent = frame(0x123, &[]);
        sent.is_remote = true;
        sent.dlc = 8;
        let read = round_trip(&sent);

        assert!(read.frame.is_remote);
        assert_eq!(read.frame.dlc, 8);
        assert_eq!(
            read.frame.payload(),
            &[0u8; 8],
            "an RTR frame carries no data"
        );
    }

    #[test]
    fn every_payload_length_survives_the_wire() {
        for len in 0..=8usize {
            let payload: Vec<u8> = (0..len).map(|i| i as u8).collect();
            let sent = frame(0x2AA, &payload);
            let read = round_trip(&sent);

            assert_eq!(read.frame.dlc as usize, len);
            assert_eq!(read.frame.data[..len], payload[..], "payload of {len}");
        }
    }

    #[test]
    fn the_worst_case_for_stuffing_still_reads_back() {
        // All-dominant and all-recessive contents are where the transmitter
        // inserts the most stuff bits, and where a decoder that miscounts the
        // run goes wrong first.
        for sent in [frame(0x000, &[0x00; 8]), frame(0x7FF, &[0xFF; 8])] {
            let read = round_trip(&sent);
            assert_eq!(read.frame.id, sent.id);
            assert_eq!(read.frame.data, sent.data);
            assert!(read.crc_ok);
        }
    }

    #[test]
    fn the_decoder_consumes_exactly_the_frame_and_leaves_the_intermission() {
        let sent = frame(0x123, &[0xAA]);
        let bits = frame_bits(&sent, AckSlot::Acknowledged);
        let read = decode_frame_bits(&bits).expect("decodes");

        assert_eq!(
            bits.len() - read.bits_consumed,
            INTERMISSION_BITS,
            "a frame ends at EOF; the intermission belongs to the bus"
        );
    }

    #[test]
    fn a_frame_nobody_answered_is_reported_as_unacknowledged() {
        let sent = frame(0x123, &[0xAA]);

        let answered = decode_frame_bits(&frame_bits(&sent, AckSlot::Acknowledged)).unwrap();
        let ignored = decode_frame_bits(&frame_bits(&sent, AckSlot::Unanswered)).unwrap();

        assert!(answered.acknowledged);
        assert!(
            !ignored.acknowledged,
            "this is the question an adapter can never answer"
        );
    }

    #[test]
    fn a_corrupted_payload_decodes_but_fails_its_crc() {
        // The frame still parses: reporting it with crc_ok false is the point,
        // because a controller would have destroyed it and told nobody.
        let sent = frame(0x2AA, &[0x55, 0x55, 0x55, 0x55]);
        let mut bits = frame_bits(&sent, AckSlot::Acknowledged);

        // An alternating payload carries no stuff bits, so flipping one bit in
        // it corrupts the data without disturbing the framing.
        let flipped = 30;
        bits[flipped] = !bits[flipped];

        let read = decode_frame_bits(&bits).expect("the framing is still intact");
        assert!(!read.crc_ok, "the CRC no longer covers what arrived");
    }

    #[test]
    fn six_equal_bits_are_refused_rather_than_read_as_data() {
        // What an error flag looks like on the wire, and what an adapter
        // resolves silently before anybody sees it.
        let mut bits = frame_bits(&frame(0x2AA, &[0x55]), AckSlot::Acknowledged);
        for bit in bits.iter_mut().skip(1).take(8) {
            *bit = false;
        }

        assert_eq!(decode_frame_bits(&bits), Err(BitError::StuffViolation));
    }

    #[test]
    fn an_idle_bus_is_not_a_frame() {
        assert_eq!(
            decode_frame_bits(&[true; 20]),
            Err(BitError::NoStartOfFrame)
        );
    }

    #[test]
    fn bits_that_stop_mid_frame_are_reported_as_truncated() {
        let bits = frame_bits(&frame(0x123, &[0xAA, 0xBB]), AckSlot::Acknowledged);
        assert_eq!(
            decode_frame_bits(&bits[..bits.len() / 2]),
            Err(BitError::Truncated)
        );
    }

    #[test]
    fn an_unanswered_frame_leaves_the_ack_slot_recessive() {
        let sent = frame(0x123, &[0xAA]);
        let answered = frame_bits(&sent, AckSlot::Acknowledged);
        let ignored = frame_bits(&sent, AckSlot::Unanswered);

        let slot = answered.len() - (2 + EOF_BITS + INTERMISSION_BITS);
        assert!(!answered[slot], "somebody pulled it dominant");
        assert!(ignored[slot], "nobody did");
    }
}
