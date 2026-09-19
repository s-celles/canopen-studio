use crate::simulator::{run_simulator_loop, SimulatorState, CanBusWrapper};
use crate::udp::UdpCanBus;
use std::sync::{Arc, RwLock};

struct UdpWrapper {
    bus: UdpCanBus,
}
impl CanBusWrapper for UdpWrapper {
    fn send(&self, frame: &crate::frame::CanFrame) -> bool {
        self.bus.send(frame).is_ok()
    }
    fn recv(&self, timeout_ms: u64) -> Option<crate::frame::CanFrame> {
        let _ = self.bus.set_read_timeout(Some(std::time::Duration::from_millis(timeout_ms)));
        match self.bus.recv() {
            Ok((f, _)) => Some(f),
            Err(_) => None,
        }
    }
}

pub fn spawn_udp_simulator(target_ip: &str, port: u16) {
    if let Ok(bus) = UdpCanBus::new(port, target_ip, port, false) {
        let state = Arc::new(RwLock::new(SimulatorState::new()));
        let wrapper = Box::new(UdpWrapper { bus });
        std::thread::spawn(move || {
            run_simulator_loop(wrapper, state);
        });
    }
}
