use crate::frame::CanFrame;
use crate::simulator::{CanBusWrapper, SimulatorState, run_simulator_loop};
use crate::udp::UdpCanBus;
use std::sync::{Arc, RwLock};
use std::time::Duration;

struct UdpWrapper {
    bus: UdpCanBus,
    /// False when the socket was bound to an ephemeral port, in which case the
    /// simulator can only transmit: nobody can address it back.
    can_receive: bool,
}

impl CanBusWrapper for UdpWrapper {
    fn send(&self, frame: &CanFrame) -> bool {
        self.bus.send(frame).is_ok()
    }

    fn recv(&self, timeout_ms: u64) -> Option<CanFrame> {
        if !self.can_receive {
            // Transmit-only: pace the caller's loop instead of spinning.
            std::thread::sleep(Duration::from_millis(timeout_ms.min(10)));
            return None;
        }
        self.bus.recv().ok().map(|(frame, _addr)| frame)
    }
}

fn spawn(bind_port: u16, target_ip: &str, target_port: u16) {
    if let Ok(bus) = UdpCanBus::new(bind_port, target_ip, target_port, false) {
        let can_receive = bind_port != 0;
        if can_receive {
            // A short timeout keeps the simulator's periodic SYNC, heartbeat
            // and TPDO schedules running while it waits for a request.
            let _ = bus.set_read_timeout(Some(Duration::from_millis(5)));
        }
        let state = Arc::new(RwLock::new(SimulatorState::new()));
        let wrapper = Box::new(UdpWrapper { bus, can_receive });
        std::thread::spawn(move || {
            run_simulator_loop(wrapper, state);
        });
    }
}

/// Spawn a transmit-only UDP simulator: binds an ephemeral port and streams
/// SYNC, heartbeats and TPDOs to `target_ip:port`.
///
/// Because nothing can address the ephemeral port, the simulator never sees
/// NMT or SDO requests. Use [`spawn_udp_simulator_bound`] for a simulator that
/// also answers.
pub fn spawn_udp_simulator(target_ip: &str, port: u16) {
    spawn(0, target_ip, port);
}

/// Spawn a bidirectional UDP simulator listening on `bind_port` and sending to
/// `target_ip:target_port`.
///
/// Requests addressed to `bind_port` are answered: NMT commands change the
/// simulated node state and SDO uploads on 0x601 get a 0x581 response.
/// `bind_port` must differ from the peer's port, otherwise the two ends share
/// one socket and each would receive its own frames.
pub fn spawn_udp_simulator_bound(bind_port: u16, target_ip: &str, target_port: u16) {
    spawn(bind_port, target_ip, target_port);
}
