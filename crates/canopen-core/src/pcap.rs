/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

//! PCAP-NG capture of CAN traffic, for Wireshark.
//!
//! Wireshark already dissects CANopen, J1939 and ISO-TP once it knows the link
//! type is SocketCAN, so the studio ships no dissector of its own: it only has
//! to hand the frames over in a shape Wireshark recognises.
//!
//! One writer serves both a file and a live capture. Wireshark *connects* to a
//! named pipe rather than creating one, so a live capture makes this side the
//! server: see [`open_capture_sink`]. Every frame is flushed for that reason
//! too — a reader at the far end of a pipe sees nothing until the bytes leave
//! our buffer.

use std::io::{self, Write};

use crate::frame::CanFrame;

/// `LINKTYPE_CAN_SOCKETCAN`, as registered with tcpdump.org.
pub const LINKTYPE_CAN_SOCKETCAN: u16 = 227;

/// A SocketCAN record: 8 bytes of header followed by 8 of payload.
///
/// Classic CAN carries at most 8 bytes, but the record keeps all of them even
/// when the DLC is shorter. That is what a real `libpcap` capture on Linux
/// produces — it dumps `struct can_frame` whole — and matching it keeps us on
/// the path every reader already handles.
pub const SOCKETCAN_RECORD_LEN: usize = 16;

/// Announced capture length. 72 is 8 + 64: classic CAN never needs it, but the
/// value survives the day the core grows CAN FD, and an over-wide snaplen
/// costs nothing.
const SNAPLEN: u32 = 72;

// Flags riding in the top bits of the 32-bit identifier field.
const EFF_FLAG: u32 = 0x8000_0000;
const RTR_FLAG: u32 = 0x4000_0000;
const ERR_FLAG: u32 = 0x2000_0000;

const STANDARD_ID_MASK: u32 = 0x0000_07FF;
const EXTENDED_ID_MASK: u32 = 0x1FFF_FFFF;

const BLOCK_SECTION_HEADER: u32 = 0x0A0D_0D0A;
const BLOCK_INTERFACE_DESCRIPTION: u32 = 0x0000_0001;
const BLOCK_ENHANCED_PACKET: u32 = 0x0000_0006;

const BYTE_ORDER_MAGIC: u32 = 0x1A2B_3C4D;

const OPT_END_OF_OPT: u16 = 0;
const OPT_IF_NAME: u16 = 2;
const OPT_IF_TSRESOL: u16 = 9;
const OPT_SHB_USERAPPL: u16 = 4;

/// Encode one frame as a SocketCAN record.
///
/// The header goes out in network byte order. Linux once wrote it in host
/// order, which left captures that only read back on the machine that made
/// them; the registered link type settles on big-endian, so that is what we
/// emit whatever the host does.
pub fn encode_socketcan(frame: &CanFrame) -> [u8; SOCKETCAN_RECORD_LEN] {
    let mut id = if frame.is_extended {
        (frame.id & EXTENDED_ID_MASK) | EFF_FLAG
    } else {
        frame.id & STANDARD_ID_MASK
    };
    if frame.is_remote {
        id |= RTR_FLAG;
    }
    if frame.is_error {
        id |= ERR_FLAG;
    }

    let mut record = [0u8; SOCKETCAN_RECORD_LEN];
    record[0..4].copy_from_slice(&id.to_be_bytes());

    let len = frame.dlc.min(8);
    record[4] = len;
    // Bytes 5..8 stay zero: FD flags, then two reserved bytes.

    // A remote frame requests data rather than carrying it, so its DLC names a
    // length that has no bytes behind it. Leave the payload zeroed.
    if !frame.is_remote {
        let payload = usize::from(len);
        record[8..8 + payload].copy_from_slice(&frame.data[..payload]);
    }

    record
}

/// Windows names its pipes `\\.\pipe\<name>`.
pub fn is_windows_named_pipe(path: &str) -> bool {
    path.starts_with(r"\\.\pipe\") || path.starts_with("//./pipe/")
}

/// Open the destination a capture should be written to.
///
/// A plain path is a file we create. A Windows pipe path is not: Wireshark
/// *connects* to a pipe rather than creating one, so the writer has to be the
/// server. This creates it and then **blocks until Wireshark connects** —
/// start the capture here first, then point Wireshark at the same name.
///
/// Unix needs none of this: `mkfifo` makes the FIFO, and opening it for
/// writing blocks until a reader shows up, which is the same rendezvous by
/// other means.
pub fn open_capture_sink(path: &str) -> io::Result<std::fs::File> {
    #[cfg(windows)]
    if is_windows_named_pipe(path) {
        return windows_pipe::serve(path);
    }

    std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .open(path)
}

#[cfg(windows)]
mod windows_pipe {
    use std::ffi::OsStr;
    use std::fs::File;
    use std::io;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::FromRawHandle;

    use windows_sys::Win32::Foundation::{CloseHandle, ERROR_PIPE_CONNECTED, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::Storage::FileSystem::PIPE_ACCESS_OUTBOUND;
    use windows_sys::Win32::System::Pipes::{
        ConnectNamedPipe, CreateNamedPipeW, PIPE_TYPE_BYTE, PIPE_WAIT,
    };

    /// One instance is enough: a capture has exactly one reader.
    const MAX_INSTANCES: u32 = 1;
    /// 64 KiB of slack absorbs a burst while Wireshark is busy drawing.
    const BUFFER_BYTES: u32 = 64 * 1024;

    pub fn serve(path: &str) -> io::Result<File> {
        let wide: Vec<u16> = OsStr::new(path)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();

        // SAFETY: `wide` is a NUL-terminated UTF-16 path that outlives the call.
        let handle = unsafe {
            CreateNamedPipeW(
                wide.as_ptr(),
                PIPE_ACCESS_OUTBOUND,
                PIPE_TYPE_BYTE | PIPE_WAIT,
                MAX_INSTANCES,
                BUFFER_BYTES,
                0,
                0,
                std::ptr::null(),
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(io::Error::last_os_error());
        }

        // SAFETY: the handle is ours and valid until the File below owns it.
        let connected = unsafe { ConnectNamedPipe(handle, std::ptr::null_mut()) };
        if connected == 0 {
            let error = io::Error::last_os_error();
            // A reader that got in before we asked is a success, not a race.
            if error.raw_os_error() != Some(ERROR_PIPE_CONNECTED as i32) {
                // SAFETY: closing a handle we own and are about to drop.
                unsafe { CloseHandle(handle) };
                return Err(error);
            }
        }

        // SAFETY: the handle is a valid, connected pipe we hand ownership of.
        Ok(unsafe { File::from_raw_handle(handle) })
    }
}

/// Streams CAN frames into a PCAP-NG capture.
pub struct PcapNgWriter<W: Write> {
    sink: W,
}

impl<W: Write> PcapNgWriter<W> {
    /// Open a capture, writing the section and interface headers immediately.
    ///
    /// `interface_name` is what Wireshark shows as the capture source — the
    /// adapter the frames came from, such as `slcan COM4`.
    pub fn new(sink: W, interface_name: &str) -> io::Result<Self> {
        let mut writer = Self { sink };
        writer.write_section_header()?;
        writer.write_interface_description(interface_name)?;
        // Wireshark refuses to show a live capture until it has read both
        // headers, so they cannot wait in a buffer for the first frame.
        writer.sink.flush()?;
        Ok(writer)
    }

    /// Append one frame.
    pub fn write_frame(&mut self, frame: &CanFrame) -> io::Result<()> {
        let record = encode_socketcan(frame);

        let mut body = Vec::with_capacity(20 + SOCKETCAN_RECORD_LEN);
        body.extend_from_slice(&0u32.to_le_bytes()); // interface id
        // The timestamp is one 64-bit count of microseconds since the epoch,
        // split across two words. `if_tsresol` below says which unit it is in.
        body.extend_from_slice(&((frame.timestamp_us >> 32) as u32).to_le_bytes());
        body.extend_from_slice(&((frame.timestamp_us & 0xFFFF_FFFF) as u32).to_le_bytes());
        body.extend_from_slice(&(SOCKETCAN_RECORD_LEN as u32).to_le_bytes()); // captured
        body.extend_from_slice(&(SOCKETCAN_RECORD_LEN as u32).to_le_bytes()); // original
        body.extend_from_slice(&record);

        self.write_block(BLOCK_ENHANCED_PACKET, &body)?;
        self.sink.flush()
    }

    /// Flush whatever the sink is still holding.
    pub fn flush(&mut self) -> io::Result<()> {
        self.sink.flush()
    }

    /// Give the sink back, so a caller can close or reuse it.
    pub fn into_inner(self) -> W {
        self.sink
    }

    fn write_section_header(&mut self) -> io::Result<()> {
        let mut body = Vec::new();
        body.extend_from_slice(&BYTE_ORDER_MAGIC.to_le_bytes());
        body.extend_from_slice(&1u16.to_le_bytes()); // major version
        body.extend_from_slice(&0u16.to_le_bytes()); // minor version
        // -1 means "length unknown", the only honest answer while we are still
        // writing, and the only possible one down a pipe.
        body.extend_from_slice(&(-1i64).to_le_bytes());

        let application = format!("CAN & CANopen Studio {}", env!("CARGO_PKG_VERSION"));
        push_option(&mut body, OPT_SHB_USERAPPL, application.as_bytes());
        end_options(&mut body);

        self.write_block(BLOCK_SECTION_HEADER, &body)
    }

    fn write_interface_description(&mut self, interface_name: &str) -> io::Result<()> {
        let mut body = Vec::new();
        body.extend_from_slice(&LINKTYPE_CAN_SOCKETCAN.to_le_bytes());
        body.extend_from_slice(&0u16.to_le_bytes()); // reserved
        body.extend_from_slice(&SNAPLEN.to_le_bytes());

        push_option(&mut body, OPT_IF_NAME, interface_name.as_bytes());
        // Microseconds. This is also the PCAP-NG default, but a capture that
        // states its own unit cannot be misread.
        push_option(&mut body, OPT_IF_TSRESOL, &[6u8]);
        end_options(&mut body);

        self.write_block(BLOCK_INTERFACE_DESCRIPTION, &body)
    }

    /// Wrap a body as a block: type, length, body, length again. The repeated
    /// length is what lets a reader walk a capture backwards.
    fn write_block(&mut self, block_type: u32, body: &[u8]) -> io::Result<()> {
        let total = (12 + body.len()) as u32;
        self.sink.write_all(&block_type.to_le_bytes())?;
        self.sink.write_all(&total.to_le_bytes())?;
        self.sink.write_all(body)?;
        self.sink.write_all(&total.to_le_bytes())
    }
}

/// Append one option, padded so the next one starts on a 4-byte boundary. The
/// length field keeps the true size, so the padding stays invisible.
fn push_option(body: &mut Vec<u8>, code: u16, value: &[u8]) {
    body.extend_from_slice(&code.to_le_bytes());
    body.extend_from_slice(&(value.len() as u16).to_le_bytes());
    body.extend_from_slice(value);
    let padding = (4 - value.len() % 4) % 4;
    body.resize(body.len() + padding, 0);
}

fn end_options(body: &mut Vec<u8>) {
    push_option(body, OPT_END_OF_OPT, &[]);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(id: u32, payload: &[u8]) -> CanFrame {
        let mut f = CanFrame::new(id, payload).expect("payload fits in a classic frame");
        // Pin the clock so a record can be compared byte for byte.
        f.timestamp_us = 0x0000_0001_2345_6789;
        f
    }

    fn read_u32(bytes: &[u8], offset: usize) -> u32 {
        u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap())
    }

    #[test]
    fn a_standard_frame_carries_its_eleven_bit_id_in_network_byte_order() {
        let record = encode_socketcan(&frame(0x123, &[0xAA, 0xBB]));

        assert_eq!(&record[0..4], &[0x00, 0x00, 0x01, 0x23]);
        assert_eq!(record[4], 2);
        assert_eq!(&record[8..10], &[0xAA, 0xBB]);
    }

    #[test]
    fn an_extended_frame_sets_the_eff_flag_beside_its_twenty_nine_bit_id() {
        let mut f = frame(0x18EA_FFFE, &[]);
        f.is_extended = true;

        let record = encode_socketcan(&f);

        assert_eq!(
            u32::from_be_bytes(record[0..4].try_into().unwrap()),
            0x98EA_FFFE
        );
    }

    #[test]
    fn a_remote_frame_announces_a_length_but_carries_no_payload() {
        let mut f = frame(0x321, &[1, 2, 3, 4]);
        f.is_remote = true;

        let record = encode_socketcan(&f);

        assert_eq!(record[0] & 0x40, 0x40, "RTR flag is set");
        assert_eq!(record[4], 4, "the requested length survives");
        assert_eq!(&record[8..16], &[0u8; 8], "but no data goes out with it");
    }

    #[test]
    fn an_error_frame_sets_the_err_flag() {
        let mut f = frame(0x001, &[]);
        f.is_error = true;

        let record = encode_socketcan(&f);

        assert_eq!(record[0] & 0x20, 0x20);
    }

    #[test]
    fn the_payload_is_padded_to_eight_bytes_so_the_record_matches_a_linux_capture() {
        let record = encode_socketcan(&frame(0x080, &[0x01]));

        assert_eq!(record.len(), 16);
        assert_eq!(record[4], 1, "the length field still names one byte");
        assert_eq!(&record[9..16], &[0u8; 7], "the rest is zero padding");
    }

    #[test]
    fn a_capture_opens_with_a_section_header_then_an_interface_description() {
        let mut buffer = Vec::new();
        PcapNgWriter::new(&mut buffer, "slcan COM4").expect("headers are written");

        assert_eq!(read_u32(&buffer, 0), BLOCK_SECTION_HEADER);
        assert_eq!(read_u32(&buffer, 8), BYTE_ORDER_MAGIC);

        let section_length = read_u32(&buffer, 4) as usize;
        assert_eq!(
            read_u32(&buffer, section_length),
            BLOCK_INTERFACE_DESCRIPTION
        );
        let interface_body = section_length + 8;
        assert_eq!(
            u16::from_le_bytes(
                buffer[interface_body..interface_body + 2]
                    .try_into()
                    .unwrap()
            ),
            LINKTYPE_CAN_SOCKETCAN
        );
    }

    #[test]
    fn every_block_repeats_its_length_at_the_end_so_a_reader_can_walk_backwards() {
        let mut buffer = Vec::new();
        let mut writer = PcapNgWriter::new(&mut buffer, "slcan COM4").unwrap();
        writer.write_frame(&frame(0x181, &[1, 2, 3])).unwrap();

        let mut offset = 0;
        let mut blocks = 0;
        while offset < buffer.len() {
            let length = read_u32(&buffer, offset + 4) as usize;
            assert!(length >= 12, "a block holds at least its own framing");
            assert_eq!(length % 4, 0, "blocks stay aligned on four bytes");
            assert_eq!(
                read_u32(&buffer, offset + length - 4),
                length as u32,
                "trailing length matches the leading one"
            );
            offset += length;
            blocks += 1;
        }
        assert_eq!(offset, buffer.len(), "the blocks tile the capture exactly");
        assert_eq!(
            blocks, 3,
            "section header, interface description, one packet"
        );
    }

    #[test]
    fn a_packet_block_splits_the_microsecond_timestamp_across_two_words() {
        let mut buffer = Vec::new();
        let mut writer = PcapNgWriter::new(&mut buffer, "slcan COM4").unwrap();
        let sent = frame(0x181, &[9]);
        writer.write_frame(&sent).unwrap();

        // Walk to the last block, whatever the headers before it weigh.
        let mut offset = 0;
        let mut packet = 0;
        while offset < buffer.len() {
            packet = offset;
            offset += read_u32(&buffer, offset + 4) as usize;
        }

        assert_eq!(read_u32(&buffer, packet), BLOCK_ENHANCED_PACKET);
        assert_eq!(read_u32(&buffer, packet + 8), 0, "interface id");
        let high = u64::from(read_u32(&buffer, packet + 12));
        let low = u64::from(read_u32(&buffer, packet + 16));
        assert_eq!((high << 32) | low, sent.timestamp_us);
        assert_eq!(read_u32(&buffer, packet + 20), SOCKETCAN_RECORD_LEN as u32);
        assert_eq!(read_u32(&buffer, packet + 24), SOCKETCAN_RECORD_LEN as u32);
    }

    #[test]
    fn an_option_is_padded_without_lying_about_its_length() {
        let mut body = Vec::new();
        push_option(&mut body, OPT_IF_NAME, b"can0");
        push_option(&mut body, OPT_IF_TSRESOL, &[6]);

        // "can0" needs no padding; the single resolution byte needs three.
        assert_eq!(body.len(), 4 + 4 + 4 + 4);
        assert_eq!(u16::from_le_bytes(body[10..12].try_into().unwrap()), 1);
        assert_eq!(&body[12..16], &[6, 0, 0, 0]);
    }
}
