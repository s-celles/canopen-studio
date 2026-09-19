use crate::simulator::{run_simulator_loop, SimulatorState, CanBusWrapper};
use crate::udp::UdpCanBus;
use crate::frame::CanFrame;
use std::sync::{Arc, RwLock};

struct UdpWrapper {
    bus: UdpCanBus,
}
impl CanBusWrapper for UdpWrapper {
    fn send(&self, frame: &CanFrame) -> bool {
        self.bus.send(frame).is_ok()
    }
    fn recv(&self, _timeout_ms: u64) -> Option<CanFrame> {
        std::thread::sleep(std::time::Duration::from_millis(5));
        None
    }
}

pub fn spawn_udp_simulator(target_ip: &str, port: u16) {
    // Bind to 0 (random port), send to port (1750)
    if let Ok(bus) = UdpCanBus::new(0, target_ip, port, false) {
        let state = Arc::new(RwLock::new(SimulatorState::new()));
        let wrapper = Box::new(UdpWrapper { bus });
        std::thread::spawn(move || {
            run_simulator_loop(wrapper, state);
        });
    }
}
