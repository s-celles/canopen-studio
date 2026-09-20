/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * CANopen Network Management (NMT) Master & Heartbeat Consumer Engine (CiA 301).
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use crate::canopen::NmtState;
use crate::frame::{CanError, CanFrame};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Standard NMT Command Specifiers (CiA 301).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum NmtCommand {
    StartRemoteNode = 0x01,
    StopRemoteNode = 0x02,
    EnterPreOperational = 0x80,
    ResetNode = 0x81,
    ResetCommunication = 0x82,
}

impl NmtCommand {
    pub fn to_byte(self) -> u8 {
        self as u8
    }
}

/// Status of a monitored CANopen node on the network.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MonitoredNode {
    pub node_id: u8,
    pub state: NmtState,
    pub last_seen_us: u64,
    pub heartbeat_interval_us: Option<u64>,
    pub is_timed_out: bool,
}

/// Network Management (NMT) Master and Heartbeat Consumer Monitor.
pub struct NmtMaster {
    nodes: HashMap<u8, MonitoredNode>,
}

impl Default for NmtMaster {
    fn default() -> Self {
        Self::new()
    }
}

impl NmtMaster {
    pub fn new() -> Self {
        Self {
            nodes: HashMap::new(),
        }
    }

    /// Build an NMT Master command frame.
    /// `target_node`: 1..=127 for specific node, 0 for all nodes (broadcast).
    pub fn build_nmt_command(command: NmtCommand, target_node: u8) -> Result<CanFrame, CanError> {
        let payload = [command.to_byte(), target_node];
        CanFrame::new(0x000, &payload)
    }

    /// Configure a monitored heartbeat interval for a node (e.g. 1000 ms = 1_000_000 µs).
    pub fn set_node_heartbeat_interval(&mut self, node_id: u8, interval_us: u64) {
        let entry = self.nodes.entry(node_id).or_insert_with(|| MonitoredNode {
            node_id,
            state: NmtState::Unknown,
            last_seen_us: 0,
            heartbeat_interval_us: Some(interval_us),
            is_timed_out: false,
        });
        entry.heartbeat_interval_us = Some(interval_us);
    }

    /// Process a received CAN frame.
    /// If it is a Heartbeat or Bootup frame (0x701..0x77F), updates node state.
    /// Returns `Some((node_id, old_state, new_state))` if node state changed.
    pub fn process_frame(&mut self, frame: &CanFrame) -> Option<(u8, NmtState, NmtState)> {
        let id = frame.id;
        if !(0x701..=0x77F).contains(&id) {
            return None;
        }

        let node_id = (id - 0x700) as u8;
        let payload = frame.payload();
        if payload.is_empty() {
            return None;
        }

        let raw_state = payload[0];
        let new_state = NmtState::from(raw_state);

        let entry = self.nodes.entry(node_id).or_insert_with(|| MonitoredNode {
            node_id,
            state: NmtState::Unknown,
            last_seen_us: frame.timestamp_us,
            heartbeat_interval_us: None,
            is_timed_out: false,
        });

        let old_state = entry.state;
        entry.state = new_state;
        entry.last_seen_us = frame.timestamp_us;
        entry.is_timed_out = false;

        if old_state != new_state {
            Some((node_id, old_state, new_state))
        } else {
            None
        }
    }

    /// Check nodes for heartbeat timeouts given the current timestamp in microseconds.
    /// Returns a list of node IDs that have timed out.
    pub fn check_timeouts(&mut self, now_us: u64) -> Vec<u8> {
        let mut timed_out_nodes = Vec::new();
        for node in self.nodes.values_mut() {
            if let Some(interval) = node.heartbeat_interval_us {
                // Allow a 50% grace period before declaring timeout
                let timeout_threshold = interval + (interval / 2);
                if now_us > node.last_seen_us
                    && (now_us - node.last_seen_us) > timeout_threshold
                    && !node.is_timed_out
                {
                    node.is_timed_out = true;
                    timed_out_nodes.push(node.node_id);
                }
            }
        }
        timed_out_nodes
    }

    /// Get current state of a node.
    pub fn get_node(&self, node_id: u8) -> Option<&MonitoredNode> {
        self.nodes.get(&node_id)
    }

    /// Get list of all currently tracked nodes.
    pub fn all_nodes(&self) -> Vec<MonitoredNode> {
        let mut list: Vec<MonitoredNode> = self.nodes.values().cloned().collect();
        list.sort_by_key(|n| n.node_id);
        list
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_nmt_commands() {
        let frame_start = NmtMaster::build_nmt_command(NmtCommand::StartRemoteNode, 5).unwrap();
        assert_eq!(frame_start.id, 0x000);
        assert_eq!(frame_start.payload(), &[0x01, 0x05]);

        let frame_reset_all = NmtMaster::build_nmt_command(NmtCommand::ResetNode, 0).unwrap();
        assert_eq!(frame_reset_all.id, 0x000);
        assert_eq!(frame_reset_all.payload(), &[0x81, 0x00]);
    }

    #[test]
    fn test_heartbeat_state_tracking() {
        let mut master = NmtMaster::new();

        // Bootup frame: Node 10, State 0x00 (Bootup)
        let f_boot = CanFrame::new_with_timestamp(0x70A, &[0x00], 1_000_000).unwrap();
        let change = master.process_frame(&f_boot);
        assert_eq!(change, Some((10, NmtState::Unknown, NmtState::Bootup)));

        // Operational frame: Node 10, State 0x05 (Operational)
        let f_oper = CanFrame::new_with_timestamp(0x70A, &[0x05], 1_100_000).unwrap();
        let change2 = master.process_frame(&f_oper);
        assert_eq!(change2, Some((10, NmtState::Bootup, NmtState::Operational)));

        // Subsequent frame with same state -> change is None
        let f_oper2 = CanFrame::new_with_timestamp(0x70A, &[0x05], 1_200_000).unwrap();
        let change3 = master.process_frame(&f_oper2);
        assert!(change3.is_none());

        let node = master.get_node(10).unwrap();
        assert_eq!(node.state, NmtState::Operational);
        assert_eq!(node.last_seen_us, 1_200_000);
    }

    #[test]
    fn test_heartbeat_timeout_detection() {
        let mut master = NmtMaster::new();
        master.set_node_heartbeat_interval(5, 100_000); // 100 ms

        // Node 5 emits heartbeat at t = 1,000,000 µs
        let f = CanFrame::new_with_timestamp(0x705, &[0x05], 1_000_000).unwrap();
        master.process_frame(&f);

        // At t = 1,120,000 µs (delta = 120 ms < 150 ms threshold) -> not timed out
        let timeouts1 = master.check_timeouts(1_120_000);
        assert!(timeouts1.is_empty());
        assert!(!master.get_node(5).unwrap().is_timed_out);

        // At t = 1,160,000 µs (delta = 160 ms > 150 ms threshold) -> timed out!
        let timeouts2 = master.check_timeouts(1_160_000);
        assert_eq!(timeouts2, vec![5]);
        assert!(master.get_node(5).unwrap().is_timed_out);

        // Subsequent check doesn't re-report the same timeout
        let timeouts3 = master.check_timeouts(1_170_000);
        assert!(timeouts3.is_empty());

        // Heartbeat received again -> clears timeout
        let f_recover = CanFrame::new_with_timestamp(0x705, &[0x05], 1_180_000).unwrap();
        master.process_frame(&f_recover);
        assert!(!master.get_node(5).unwrap().is_timed_out);
    }
}
