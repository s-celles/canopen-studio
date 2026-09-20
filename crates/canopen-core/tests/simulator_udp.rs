//! The bidirectional UDP simulator must answer what the UI sends it:
//! NMT commands change the simulated node state, SDO uploads get a response.

use canopen_core::frame::CanFrame;
use canopen_core::nmt::{NmtCommand, NmtMaster};
use canopen_core::simulator_ext::spawn_udp_simulator_bound;
use canopen_core::udp::UdpCanBus;
use std::time::{Duration, Instant};

/// Ports are per-test so the cases can run concurrently.
struct Peer {
    bus: UdpCanBus,
}

impl Peer {
    /// Stand in for the UI: bind `ui_port`, talk to a simulator on `sim_port`.
    fn connect(ui_port: u16, sim_port: u16) -> Self {
        spawn_udp_simulator_bound(sim_port, "127.0.0.1", ui_port);
        let bus = UdpCanBus::new(ui_port, "127.0.0.1", sim_port, false)
            .expect("the test peer must bind its port");
        bus.set_read_timeout(Some(Duration::from_millis(50)))
            .expect("read timeout");
        Self { bus }
    }

    /// Wait until `predicate` accepts a received frame, or give up.
    fn wait_for<T>(
        &self,
        timeout: Duration,
        mut predicate: impl FnMut(&CanFrame) -> Option<T>,
    ) -> Option<T> {
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            if let Ok((frame, _)) = self.bus.recv()
                && let Some(v) = predicate(&frame)
            {
                return Some(v);
            }
        }
        None
    }
}

#[test]
fn the_simulator_streams_heartbeats_without_being_asked() {
    let peer = Peer::connect(29750, 29751);
    let state = peer.wait_for(Duration::from_secs(5), |f| {
        let mut nmt = NmtMaster::new();
        nmt.process_frame(f)?;
        nmt.get_node(1).map(|n| n.state)
    });
    assert!(state.is_some(), "no heartbeat for node 1 arrived");
}

#[test]
fn an_nmt_start_moves_the_simulated_node_to_operational() {
    let peer = Peer::connect(29752, 29753);

    // Heartbeat state byte: 0x7F pre-operational, 0x05 operational.
    let heartbeat_state = |timeout| {
        peer.wait_for(timeout, |f| {
            (f.id == 0x701 && !f.payload().is_empty()).then(|| f.payload()[0])
        })
    };

    assert_eq!(
        heartbeat_state(Duration::from_secs(5)),
        Some(0x7F),
        "the simulator should start pre-operational"
    );

    let start = NmtMaster::build_nmt_command(NmtCommand::StartRemoteNode, 1).unwrap();
    peer.bus.send(&start).expect("NMT command must go out");

    // The next heartbeats should report the new state.
    let became_operational = peer
        .wait_for(Duration::from_secs(5), |f| {
            (f.id == 0x701 && f.payload().first() == Some(&0x05)).then_some(())
        })
        .is_some();
    assert!(
        became_operational,
        "node 1 never reported Operational after NMT Start"
    );
}

#[test]
fn an_nmt_stop_moves_the_simulated_node_to_stopped() {
    let peer = Peer::connect(29754, 29755);
    let stop = NmtMaster::build_nmt_command(NmtCommand::StopRemoteNode, 1).unwrap();
    peer.bus.send(&stop).expect("NMT command must go out");

    let became_stopped = peer
        .wait_for(Duration::from_secs(5), |f| {
            (f.id == 0x701 && f.payload().first() == Some(&0x04)).then_some(())
        })
        .is_some();
    assert!(became_stopped, "node 1 never reported Stopped");
}

#[test]
fn an_sdo_upload_of_the_device_type_is_answered() {
    let peer = Peer::connect(29756, 29757);

    // Expedited upload request of 0x1000:00 (device type) to node 1.
    let request = CanFrame::new(0x601, &[0x40, 0x00, 0x10, 0x00, 0, 0, 0, 0]).unwrap();
    peer.bus.send(&request).expect("SDO request must go out");

    let response = peer.wait_for(Duration::from_secs(5), |f| {
        (f.id == 0x581).then(|| f.payload().to_vec())
    });
    let response = response.expect("no SDO response on 0x581");

    assert_eq!(response[0], 0x43, "expedited 4-byte upload response");
    assert_eq!(&response[1..4], &[0x00, 0x10, 0x00], "echoes 0x1000:00");
    assert_eq!(
        u32::from_le_bytes([response[4], response[5], response[6], response[7]]),
        0x0002_0192,
        "device type of the simulated drive"
    );
}

#[test]
fn a_transmit_only_simulator_never_answers() {
    // Binding an ephemeral port leaves the simulator unaddressable: this is the
    // documented limitation of `spawn_udp_simulator`.
    let peer = Peer::connect(29758, 29759);
    drop(peer);

    let bus = UdpCanBus::new(29760, "127.0.0.1", 29761, false).unwrap();
    bus.set_read_timeout(Some(Duration::from_millis(50)))
        .unwrap();
    canopen_core::simulator_ext::spawn_udp_simulator("127.0.0.1", 29760);

    let request = CanFrame::new(0x601, &[0x40, 0x00, 0x10, 0x00, 0, 0, 0, 0]).unwrap();
    bus.send(&request).unwrap();

    let deadline = Instant::now() + Duration::from_secs(2);
    let mut saw_heartbeat = false;
    let mut saw_sdo_response = false;
    while Instant::now() < deadline {
        if let Ok((frame, _)) = bus.recv() {
            if frame.id == 0x701 {
                saw_heartbeat = true;
            }
            if frame.id == 0x581 {
                saw_sdo_response = true;
            }
        }
    }
    assert!(saw_heartbeat, "a transmit-only simulator still streams");
    assert!(!saw_sdo_response, "but it cannot answer a request");
}

#[test]
fn the_simulator_answers_a_ping_so_the_round_trip_can_be_measured() {
    use canopen_core::latency::{build_ping_request, ping_response_sequence};

    let peer = Peer::connect(29762, 29763);
    let request = build_ping_request(4242, 0).unwrap();
    peer.bus.send(&request).expect("ping must go out");

    let seq = peer.wait_for(Duration::from_secs(5), ping_response_sequence);
    assert_eq!(seq, Some(4242), "the response must echo our sequence");
}

#[test]
fn the_simulator_does_not_answer_its_own_ping_response() {
    use canopen_core::latency::PING_RESPONSE_ID;

    let peer = Peer::connect(29764, 29765);
    // Feeding a response in must not produce another one, or two responders
    // on a bus would echo each other forever.
    let response = CanFrame::new(PING_RESPONSE_ID, &[1, 0, 0, 0, 0, 0, 0, 0]).unwrap();
    peer.bus.send(&response).unwrap();

    let deadline = Instant::now() + Duration::from_secs(2);
    let mut responses = 0;
    while Instant::now() < deadline {
        if let Ok((frame, _)) = peer.bus.recv()
            && frame.id == PING_RESPONSE_ID
        {
            responses += 1;
        }
    }
    assert_eq!(responses, 0, "a response must not be echoed");
}

#[test]
fn the_simulator_answers_every_obd2_pid_it_advertises() {
    use canopen_core::obd2::{
        SUPPORTED_MODE01_PIDS, build_obd2_mode01_request, decode_obd2_mode01_frame,
    };

    let peer = Peer::connect(29766, 29767);
    for &pid in SUPPORTED_MODE01_PIDS {
        peer.bus
            .send(&build_obd2_mode01_request(pid).unwrap())
            .expect("request must go out");
        let reading = peer.wait_for(Duration::from_secs(5), |f| {
            decode_obd2_mode01_frame(f).filter(|r| r.pid == pid)
        });
        let reading = reading.unwrap_or_else(|| panic!("no Mode 01 response for PID {pid:#04X}"));
        assert!(!reading.name.is_empty(), "PID {pid:#04X} decoded unnamed");
        assert!(!reading.unit.is_empty(), "PID {pid:#04X} decoded unitless");
    }
}

#[test]
fn the_simulator_stays_silent_on_an_unsupported_obd2_pid() {
    use canopen_core::obd2::{OBD2_RESPONSE_ID, build_obd2_mode01_request};

    let peer = Peer::connect(29768, 29769);
    // 0x1F (run time since engine start) is not in the supported set.
    peer.bus
        .send(&build_obd2_mode01_request(0x1F).unwrap())
        .unwrap();

    let deadline = Instant::now() + Duration::from_secs(2);
    let mut answered = false;
    while Instant::now() < deadline {
        if let Ok((frame, _)) = peer.bus.recv()
            && frame.id == OBD2_RESPONSE_ID
        {
            answered = true;
        }
    }
    assert!(!answered, "an unsupported PID must get no response");
}

#[test]
fn the_reported_engine_speed_tracks_the_simulated_motor() {
    use canopen_core::obd2::{build_obd2_mode01_request, decode_obd2_mode01_frame};

    let peer = Peer::connect(29770, 29771);
    // The motor sweeps 400..3200 rpm, so the reading must land in that band
    // rather than being a constant.
    peer.bus
        .send(&build_obd2_mode01_request(0x0C).unwrap())
        .unwrap();
    let reading = peer
        .wait_for(Duration::from_secs(5), |f| {
            decode_obd2_mode01_frame(f).filter(|r| r.pid == 0x0C)
        })
        .expect("no engine speed response");
    assert_eq!(reading.name, "Engine RPM");
    assert!(
        (300.0..=3400.0).contains(&reading.value),
        "engine speed {} is outside the simulated sweep",
        reading.value
    );
}
