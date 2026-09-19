use std::thread;
use std::time::Duration;
use std::collections::VecDeque;
use slint::Weak;
use canopen_core::udp::UdpCanBus;
use canopen_core::simulator_ext::spawn_udp_simulator;

slint::include_modules!();

fn main() -> Result<(), slint::PlatformError> {
    let ui = MainWindow::new()?;
    let ui_handle: Weak<MainWindow> = ui.as_weak();

    // Start background simulation thread on Multicast so all local sniffers get it!
    spawn_udp_simulator("127.0.0.1", 1750);

    thread::spawn(move || {
        let bus = match UdpCanBus::new(1750, "127.0.0.1", 1750, true) {
            Ok(b) => b,
            Err(e) => {
                eprintln!("Failed to bind UdpCanBus: {:?}", e);
                return;
            }
        };
        let _ = bus.set_read_timeout(Some(Duration::from_millis(5)));

        let mut last_rpm = 0;
        let mut last_nmt = "Offline".to_string();
        let mut trace_count = 0;
        
        let mut rpm_history: VecDeque<i32> = VecDeque::with_capacity(800);

        loop {
            match bus.recv() {
                Ok((frame, _addr)) => {
                    trace_count += 1;
                    let id = frame.id;
                    let data = frame.payload();
                    let mut updated = false;

                    if id == 0x701 && data.len() >= 1 {
                        let st = data[0];
                        last_nmt = match st {
                            0x00 => "Boot-Up".to_string(),
                            0x04 => "Stopped".to_string(),
                            0x05 => "Operational".to_string(),
                            0x7F => "Pre-Operational".to_string(),
                            _ => format!("Unknown (0x{:02X})", st),
                        };
                        updated = true;
                    }

                    if id == 0x473 && data.len() >= 8 {
                        let mut rpm_bytes = [0u8; 4];
                        rpm_bytes.copy_from_slice(&data[4..8]);
                        last_rpm = i32::from_le_bytes(rpm_bytes);
                        
                        rpm_history.push_back(last_rpm);
                        if rpm_history.len() > 800 {
                            rpm_history.pop_front();
                        }
                        updated = true;
                    }

                    if updated {
                        let rpm = last_rpm;
                        let nmt = last_nmt.clone();
                        let total_trace = trace_count;
                        
                        let mut path = String::with_capacity(rpm_history.len() * 15);
                        let w = 800.0;
                        let h = 300.0;
                        let max_rpm = 4000.0;
                        
                        for (i, &val) in rpm_history.iter().enumerate() {
                            let x = (i as f32 / 800.0) * w;
                            let v = if val < 0 { 0.0 } else if val as f32 > max_rpm { max_rpm } else { val as f32 };
                            let y = h - ((v / max_rpm) * h);
                            if i == 0 {
                                path.push_str(&format!("M {} {} ", x, y));
                            } else {
                                path.push_str(&format!("L {} {} ", x, y));
                            }
                        }

                        let _ = ui_handle.upgrade_in_event_loop(move |ui| {
                            ui.set_rpm(rpm);
                            ui.set_nmt_state(nmt.into());
                            ui.set_trace_count(total_trace);
                            if !path.is_empty() {
                                ui.set_plot_path_b0(path.into());
                            }
                        });
                    }
                }
                Err(_) => {}
            }
        }
    });

    ui.run()
}
