use crate::frame::CanFrame;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

pub trait CanBusWrapper: Send + Sync {
    fn send(&self, frame: &CanFrame) -> bool;
    fn recv(&self, timeout_ms: u64) -> Option<CanFrame>;
}

pub struct SimulatorState {
    pub nmt_state_node1: u8,
    pub nmt_state_node2: u8,
    pub sim_rpm: i32,
    pub running: Arc<AtomicBool>,
}

impl SimulatorState {
    pub fn new() -> Self {
        Self {
            nmt_state_node1: 0x7F, // Pre-Operational
            nmt_state_node2: 0x7F,
            sim_rpm: 1800,
            running: Arc::new(AtomicBool::new(true)),
        }
    }
}

pub fn run_simulator_loop(
    bus: Box<dyn CanBusWrapper>,
    state: Arc<std::sync::RwLock<SimulatorState>>,
) {
    let mut angle: f32 = 0.0;

    let mut last_hb = Instant::now();
    let mut last_sync = Instant::now();
    let mut last_pdo = Instant::now();

    while state.read().unwrap().running.load(Ordering::SeqCst) {
        let now = Instant::now();

        // Polling RX via timeout
        if let Some(frame) = bus.recv(10) {
            let id = frame.id;
            let data = frame.payload();
            let mut s = state.write().unwrap();

            // NMT Command
            if id == 0x000 && data.len() >= 2 {
                let cmd = data[0];
                let target = data[1];
                if target == 0 || target == 1 {
                    if cmd == 0x01 {
                        s.nmt_state_node1 = 0x05;
                    } else if cmd == 0x02 {
                        s.nmt_state_node1 = 0x04;
                    } else if cmd == 0x80 {
                        s.nmt_state_node1 = 0x7F;
                    } else if cmd == 0x81 {
                        s.nmt_state_node1 = 0x00;
                    }
                }
            }
            // SDO Request
            else if id == 0x601 && data.len() >= 4 {
                let cs = data[0];
                let idx = data[1] as u16 | ((data[2] as u16) << 8);
                let sub = data[3];
                if cs == 0x40 {
                    let mut resp_payload = vec![0, 0, 0, 0];
                    let mut resp_cs = 0x42;

                    if idx == 0x1000 && sub == 0 {
                        resp_payload = 0x00020192_u32.to_le_bytes().to_vec();
                        resp_cs = 0x43;
                    } else if idx == 0x1008 && sub == 0 {
                        resp_payload = b"Virt".to_vec(); // Virtual Drive
                        resp_cs = 0x43;
                    } else if idx == 0x606C && sub == 0 {
                        resp_payload = s.sim_rpm.to_le_bytes().to_vec();
                        resp_cs = 0x43;
                    }

                    let mut reply = vec![resp_cs, (idx & 0xFF) as u8, (idx >> 8) as u8, sub];
                    reply.extend(resp_payload);
                    bus.send(&CanFrame::new(0x581, &reply).unwrap());
                }
            }
        }

        let now2 = Instant::now();

        // 1. SYNC frame at 50 Hz (20 ms)
        if now2.duration_since(last_sync).as_secs_f32() >= 0.020 {
            bus.send(&CanFrame::new(0x080, &[]).unwrap());
            last_sync = now2;
        }

        // 2. Heartbeat at 1 Hz
        if now2.duration_since(last_hb).as_secs_f32() >= 1.0 {
            let (n1, n2) = {
                let s = state.read().unwrap();
                (s.nmt_state_node1, s.nmt_state_node2)
            };
            bus.send(&CanFrame::new(0x701, &[n1]).unwrap());
            bus.send(&CanFrame::new(0x702, &[n2]).unwrap());
            last_hb = now2;
        }

        // 3. TPDOs at 25 Hz (40 ms)
        if now2.duration_since(last_pdo).as_secs_f32() >= 0.040 {
            angle += 0.08;
            let current_rpm = (1800.0 + 1400.0 * angle.sin()) as i32;
            {
                state.write().unwrap().sim_rpm = current_rpm;
            }
            let sim_temp = (32.0 + 8.0 * (angle * 0.3).sin()) as i32;
            let torque_val = (80.0 + 70.0 * angle.sin()) as i32;

            bus.send(&CanFrame::new(0x181, &0x0027_u16.to_le_bytes()).unwrap());

            let mut buf_281 = Vec::new();
            buf_281.extend_from_slice(&0x0027_u16.to_le_bytes());
            buf_281.extend_from_slice(&current_rpm.to_le_bytes());
            bus.send(&CanFrame::new(0x281, &buf_281).unwrap());

            let mut buf_473 = Vec::new();
            buf_473.extend_from_slice(&5000_i32.to_le_bytes());
            buf_473.extend_from_slice(&current_rpm.to_le_bytes());
            bus.send(&CanFrame::new(0x473, &buf_473).unwrap());

            let mut buf_270 = vec![3, 4, 0, 0, 0];
            buf_270.extend_from_slice(&(torque_val as i16).to_le_bytes());
            buf_270.push(0);
            bus.send(&CanFrame::new(0x270, &buf_270).unwrap());

            let mut buf_156 = vec![0, 0, 10, 0, 50, 0];
            buf_156.extend_from_slice(&(sim_temp as i16).to_le_bytes());
            bus.send(&CanFrame::new(0x156, &buf_156).unwrap());

            last_pdo = now2;
        }
    }
}
