use std::sync::Arc;
use std::thread;
use std::time::Duration;
use slint::Weak;
use canopen_core::udp::UdpCanBus;
use canopen_core::frame::CanFrame;

slint::include_modules!();

fn main() -> Result<(), slint::PlatformError> {
    let ui = MainWindow::new()?;
    let ui_handle: Weak<MainWindow> = ui.as_weak();

    thread::spawn(move || {
        let bus = match UdpCanBus::new(1750, "127.0.0.1", 1750, false) {
            Ok(b) => b,
            Err(e) => {
                eprintln!("Failed to bind UdpCanBus: {:?}", e);
                return;
            }
        };
        let _ = bus.set_read_timeout(Some(Duration::from_millis(10)));

        let mut last_rpm = 0;
        let mut last_nmt = "Offline".to_string();

        loop {
            match bus.recv() {
                Ok((frame, _addr)) => {
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
                        updated = true;
                    }

                    if updated {
                        let rpm = last_rpm;
                        let nmt = last_nmt.clone();
                        let _ = ui_handle.upgrade_in_event_loop(move |ui| {
                            ui.set_rpm(rpm);
                            ui.set_nmt_state(nmt.into());
                        });
                    }
                }
                Err(_) => {
                    // Timeout or error, just loop
                }
            }
        }
    });

    ui.run()
}
