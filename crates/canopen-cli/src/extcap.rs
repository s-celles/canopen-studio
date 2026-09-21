/*
 * CANopen Studio — Wireshark extcap interface
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

//! Present the studio's CAN buses to Wireshark as capture interfaces.
//!
//! Wireshark cannot capture CAN anywhere but Linux: SocketCAN is a kernel
//! feature, and on macOS and Windows its interface list simply has nothing to
//! offer. An extcap is the supported way to fill that in — Wireshark runs this
//! binary, asks what it can capture, and then asks it to write PCAP-NG into a
//! pipe, which is what `canopen-cli sniff --pcap` already does.
//!
//! The protocol is line-based on stdout. Wireshark calls the binary once per
//! question:
//!
//! ```text
//! --extcap-interfaces                      -> interface {value=…}{display=…}
//! --extcap-interface=X --extcap-dlts       -> dlt {number=227}{name=…}
//! --extcap-interface=X --extcap-config     -> arg {number=0}{call=--port}…
//! --extcap-interface=X --capture --fifo=P  -> PCAP-NG written to P
//! ```

use canopen_core::{CanFrame, PcapNgWriter, UdpCanBus, simulator_ext::spawn_udp_simulator_bound};
use std::io::Write;

/// The interfaces this binary offers. Kept to what `canopen-core` can really
/// open today: the UDP transport and the bundled simulator. The serial and
/// SocketCAN backends live in the Python studio and have no Rust equivalent
/// yet, so advertising them here would put dead entries in Wireshark's list.
pub const INTERFACES: &[(&str, &str)] = &[
    (
        "canopen-udp",
        "CANopen Studio: CAN over UDP (network bridge)",
    ),
    (
        "canopen-sim",
        "CANopen Studio: virtual simulator (demo bus)",
    ),
];

/// LINKTYPE_CAN_SOCKETCAN, which is what `pcap.rs` encodes and what the
/// CANopen, ISO 15765 and J1939 dissectors expect.
const DLT_NUMBER: u16 = canopen_core::LINKTYPE_CAN_SOCKETCAN;

/// What Wireshark asked for. Extcap passes `--flag=value` as one argument,
/// which is why this is parsed by hand rather than with the clap parser the
/// rest of the CLI uses.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct ExtcapRequest {
    pub list_interfaces: bool,
    pub list_dlts: bool,
    pub show_config: bool,
    pub capture: bool,
    pub interface: Option<String>,
    pub fifo: Option<String>,
    pub port: Option<u16>,
    pub target: Option<String>,
    pub target_port: Option<u16>,
}

fn value_of<'a>(arg: &'a str, name: &str) -> Option<&'a str> {
    arg.strip_prefix(name)?.strip_prefix('=')
}

impl ExtcapRequest {
    pub fn parse<S: AsRef<str>>(args: &[S]) -> Self {
        let mut req = Self::default();
        let mut pending: Option<&'static str> = None;

        for arg in args {
            let arg = arg.as_ref();

            // A value that followed a space-separated flag.
            if let Some(name) = pending.take() {
                match name {
                    "interface" => req.interface = Some(arg.to_string()),
                    "fifo" => req.fifo = Some(arg.to_string()),
                    "port" => req.port = arg.parse().ok(),
                    "target" => req.target = Some(arg.to_string()),
                    "target-port" => req.target_port = arg.parse().ok(),
                    _ => {}
                }
                continue;
            }

            match arg {
                "--extcap-interfaces" => req.list_interfaces = true,
                "--extcap-dlts" => req.list_dlts = true,
                "--extcap-config" => req.show_config = true,
                "--capture" => req.capture = true,
                "--extcap-interface" => pending = Some("interface"),
                "--fifo" => pending = Some("fifo"),
                "--port" => pending = Some("port"),
                "--target" => pending = Some("target"),
                "--target-port" => pending = Some("target-port"),
                _ => {
                    if let Some(v) = value_of(arg, "--extcap-interface") {
                        req.interface = Some(v.to_string());
                    } else if let Some(v) = value_of(arg, "--fifo") {
                        req.fifo = Some(v.to_string());
                    } else if let Some(v) = value_of(arg, "--port") {
                        req.port = v.parse().ok();
                    } else if let Some(v) = value_of(arg, "--target") {
                        req.target = Some(v.to_string());
                    } else if let Some(v) = value_of(arg, "--target-port") {
                        req.target_port = v.parse().ok();
                    }
                }
            }
        }
        req
    }
}

pub fn interface_lines() -> Vec<String> {
    INTERFACES
        .iter()
        .map(|(value, display)| format!("interface {{value={value}}}{{display={display}}}"))
        .collect()
}

pub fn dlt_lines(interface: &str) -> Vec<String> {
    if !INTERFACES.iter().any(|(v, _)| *v == interface) {
        return Vec::new();
    }
    vec![format!(
        "dlt {{number={DLT_NUMBER}}}{{name=LINKTYPE_CAN_SOCKETCAN}}{{display=CAN frames (SocketCAN)}}"
    )]
}

/// The knobs Wireshark shows in its interface options dialog. The simulator
/// needs none: it produces its own traffic on a loopback pair.
pub fn config_lines(interface: &str) -> Vec<String> {
    match interface {
        "canopen-udp" => vec![
            "arg {number=0}{call=--port}{display=Listen port}\
             {tooltip=UDP port the studio's frames arrive on}{type=unsigned}{default=1750}"
                .to_string(),
            "arg {number=1}{call=--target}{display=Peer address}\
             {tooltip=Host the bus sends to}{type=string}{default=127.0.0.1}"
                .to_string(),
            "arg {number=2}{call=--target-port}{display=Peer port}\
             {tooltip=UDP port the studio's frames are sent to}{type=unsigned}{default=1750}"
                .to_string(),
        ],
        _ => Vec::new(),
    }
}

/// Capture until the pipe closes, which is how Wireshark says stop.
pub fn capture(req: &ExtcapRequest) -> Result<(), String> {
    let fifo = req.fifo.as_deref().ok_or("--capture needs --fifo")?;
    let interface = req.interface.as_deref().unwrap_or("canopen-udp");

    // The simulator needs a peer to talk to, so it gets a fixed loopback pair;
    // the UDP interface takes whatever the options dialog supplied.
    let (port, target, target_port) = match interface {
        "canopen-sim" => (1750, "127.0.0.1".to_string(), 1751),
        _ => (
            req.port.unwrap_or(1750),
            req.target
                .clone()
                .unwrap_or_else(|| "127.0.0.1".to_string()),
            req.target_port.unwrap_or(1750),
        ),
    };

    if interface == "canopen-sim" {
        spawn_udp_simulator_bound(target_port, &target, port);
    }

    let bus = UdpCanBus::new(port, &target, target_port, false)
        .map_err(|e| format!("cannot bind UDP port {port}: {e:?}"))?;

    let sink = canopen_core::pcap::open_capture_sink(fifo)
        .map_err(|e| format!("cannot open the capture pipe {fifo}: {e}"))?;
    let mut writer =
        PcapNgWriter::new(sink, interface).map_err(|e| format!("cannot start the capture: {e}"))?;

    loop {
        let frame: CanFrame = match bus.recv() {
            Ok((frame, _)) => frame,
            Err(_) => continue,
        };
        // Wireshark closing the pipe is how a capture ends, not a failure.
        if writer.write_frame(&frame).is_err() {
            return Ok(());
        }
    }
}

/// Answer one extcap question with the lines to print. A capture request is
/// not one of them: it is handled separately, because it never returns.
pub fn respond(req: &ExtcapRequest) -> Vec<String> {
    if req.list_interfaces {
        let mut lines = vec![format!(
            "extcap {{version={}}}{{display=CANopen Studio}}{{help=https://github.com/s-celles/canopen-studio}}",
            env!("CARGO_PKG_VERSION")
        )];
        lines.extend(interface_lines());
        return lines;
    }
    let interface = req.interface.as_deref().unwrap_or_default();
    if req.list_dlts {
        return dlt_lines(interface);
    }
    if req.show_config {
        return config_lines(interface);
    }
    Vec::new()
}

pub fn run(args: &[String]) -> Result<(), String> {
    let req = ExtcapRequest::parse(args);

    if req.capture {
        return capture(&req);
    }

    let lines = respond(&req);
    if lines.is_empty() && !req.list_dlts && !req.show_config {
        return Err("no extcap operation requested".to_string());
    }
    let mut out = std::io::stdout().lock();
    for line in lines {
        let _ = writeln!(out, "{line}");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wireshark_passes_a_flag_and_its_value_joined_by_an_equals_sign() {
        let req = ExtcapRequest::parse(&["--extcap-interface=canopen-udp", "--extcap-dlts"]);
        assert_eq!(req.interface.as_deref(), Some("canopen-udp"));
        assert!(req.list_dlts);
    }

    #[test]
    fn a_flag_separated_by_a_space_is_read_the_same_way() {
        let req = ExtcapRequest::parse(&["--extcap-interface", "canopen-sim", "--extcap-config"]);
        assert_eq!(req.interface.as_deref(), Some("canopen-sim"));
        assert!(req.show_config);
    }

    #[test]
    fn a_capture_request_carries_the_pipe_it_must_write_to() {
        let req = ExtcapRequest::parse(&[
            "--capture",
            "--extcap-interface=canopen-udp",
            "--fifo=/tmp/ws.fifo",
            "--port=1770",
            "--target-port=1771",
        ]);
        assert!(req.capture);
        assert_eq!(req.fifo.as_deref(), Some("/tmp/ws.fifo"));
        assert_eq!(req.port, Some(1770));
        assert_eq!(req.target_port, Some(1771));
    }

    #[test]
    fn an_unparsable_port_is_left_unset_rather_than_guessed() {
        let req = ExtcapRequest::parse(&["--port=not-a-number"]);
        assert_eq!(req.port, None);
    }

    #[test]
    fn the_interface_list_names_itself_before_what_it_offers() {
        let lines = respond(&ExtcapRequest {
            list_interfaces: true,
            ..Default::default()
        });
        assert!(lines[0].starts_with("extcap {version="));
        assert_eq!(lines.len(), 1 + INTERFACES.len());
        assert!(
            lines[1..]
                .iter()
                .all(|l| l.starts_with("interface {value="))
        );
    }

    #[test]
    fn every_advertised_interface_captures_socketcan_frames() {
        for (value, _) in INTERFACES {
            let lines = dlt_lines(value);
            assert_eq!(lines.len(), 1, "{value} must offer exactly one link type");
            assert!(
                lines[0].contains("{number=227}"),
                "{value} must offer LINKTYPE_CAN_SOCKETCAN"
            );
        }
    }

    #[test]
    fn an_interface_that_is_not_offered_is_given_no_link_type() {
        assert!(dlt_lines("canopen-serial").is_empty());
        assert!(config_lines("canopen-serial").is_empty());
    }

    #[test]
    fn the_udp_interface_asks_for_the_ports_it_needs() {
        let lines = config_lines("canopen-udp");
        assert_eq!(lines.len(), 3);
        // Wireshark orders the dialog by the number, so they must be distinct
        // and start at zero.
        for (i, line) in lines.iter().enumerate() {
            assert!(line.starts_with(&format!("arg {{number={i}}}")), "{line}");
        }
    }

    #[test]
    fn the_simulator_needs_no_configuring_because_it_makes_its_own_traffic() {
        assert!(config_lines("canopen-sim").is_empty());
    }

    #[test]
    fn a_capture_without_a_pipe_is_refused_rather_than_writing_nowhere() {
        let req = ExtcapRequest {
            capture: true,
            interface: Some("canopen-udp".to_string()),
            ..Default::default()
        };
        assert!(capture(&req).is_err());
    }
}
