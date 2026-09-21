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

use crate::frame::CanFrame;

/// CAN's CRC-15 generator polynomial, x^15 + x^14 + x^10 + x^8 + x^7 + x^4 + x^3 + 1.
const CRC15_POLYNOMIAL: u16 = 0x4599;

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
