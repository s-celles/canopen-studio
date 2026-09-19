/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use crate::frame::{CanError, CanFrame};
use socket2::{Domain, Protocol, Socket, Type};
use std::io;
use std::net::{SocketAddr, ToSocketAddrs, UdpSocket};
use std::time::Duration;

#[derive(thiserror::Error, Debug)]
pub enum UdpBusError {
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("CAN error: {0}")]
    Can(#[from] CanError),
    #[error("Invalid target address: {0}")]
    InvalidAddress(String),
}

/// Cross-platform UDP CAN bus interface compatible with `python-can` and `canopen_studio.interfaces.UdpBus`.
pub struct UdpCanBus {
    socket: UdpSocket,
    target_addr: SocketAddr,
    use_compact_wire: bool,
}

impl UdpCanBus {
    /// Bind a UDP CAN bus on the given port (e.g. 1750 or 50000) targeting a destination IP (unicast or broadcast).
    pub fn new(bind_port: u16, target_host: &str, target_port: u16, use_compact_wire: bool) -> Result<Self, UdpBusError> {
        let domain = Domain::IPV4;
        let sock = Socket::new(domain, Type::DGRAM, Some(Protocol::UDP))?;

        sock.set_reuse_address(true)?;
        #[cfg(all(unix, not(target_os = "solaris")))]
        {
            let _ = sock.set_reuse_port(true);
        }
        sock.set_broadcast(true)?;
        sock.set_nonblocking(false)?;

        let bind_addr: SocketAddr = format!("0.0.0.0:{}", bind_port)
            .parse()
            .map_err(|e: std::net::AddrParseError| UdpBusError::InvalidAddress(e.to_string()))?;
        sock.bind(&bind_addr.into())?;

        let socket: UdpSocket = sock.into();

        let target_str = format!("{}:{}", target_host, target_port);
        let target_addr = target_str
            .to_socket_addrs()?
            .next()
            .ok_or_else(|| UdpBusError::InvalidAddress(format!("Cannot resolve destination {}", target_str)))?;

        Ok(Self {
            socket,
            target_addr,
            use_compact_wire,
        })
    }

    /// Set socket read timeout.
    pub fn set_read_timeout(&self, timeout: Option<Duration>) -> Result<(), UdpBusError> {
        self.socket.set_read_timeout(timeout)?;
        Ok(())
    }

    /// Send a CAN frame across the network.
    pub fn send(&self, frame: &CanFrame) -> Result<(), UdpBusError> {
        let bytes = if self.use_compact_wire {
            frame.to_compact_bytes().to_vec()
        } else {
            frame.to_python_can_msgpack()?
        };
        self.socket.send_to(&bytes, self.target_addr)?;
        Ok(())
    }

    /// Receive the next CAN frame from the UDP socket.
    pub fn recv(&self) -> Result<(CanFrame, SocketAddr), UdpBusError> {
        let mut buf = [0u8; 4096];
        let (len, src_addr) = self.socket.recv_from(&mut buf)?;
        let raw = &buf[..len];

        let frame = if self.use_compact_wire && len == 24 {
            CanFrame::from_compact_bytes(raw)?
        } else {
            // Attempt python-can msgpack decoding first, then fallback to compact
            match CanFrame::from_python_can_msgpack(raw) {
                Ok(f) => f,
                Err(_) => CanFrame::from_compact_bytes(raw)?,
            }
        };

        Ok((frame, src_addr))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_udp_bus_local_communication() {
        let port1 = 17500;
        let port2 = 17501;

        let bus1 = UdpCanBus::new(port1, "127.0.0.1", port2, false).unwrap();
        let bus2 = UdpCanBus::new(port2, "127.0.0.1", port1, false).unwrap();

        bus2.set_read_timeout(Some(Duration::from_millis(500))).unwrap();

        let tx_frame = CanFrame::new(0x123, &[0xDE, 0xAD, 0xBE, 0xEF]).unwrap();
        bus1.send(&tx_frame).unwrap();

        let (rx_frame, _src) = bus2.recv().unwrap();
        assert_eq!(rx_frame.id, 0x123);
        assert_eq!(rx_frame.payload(), &[0xDE, 0xAD, 0xBE, 0xEF]);
    }
}
