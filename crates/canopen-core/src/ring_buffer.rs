/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use crate::frame::CanFrame;
use std::sync::RwLock;

/// Fixed-capacity ring buffer for storing CAN traces without dynamic allocations.
/// Thread-safe for multi-producer / multi-consumer via an internal `RwLock`.
pub struct TraceRingBuffer {
    capacity: usize,
    buffer: RwLock<Vec<CanFrame>>,
    head: RwLock<usize>,
    total_pushed: RwLock<u64>,
}

impl TraceRingBuffer {
    /// Create a new ring buffer with the specified fixed capacity.
    pub fn new(capacity: usize) -> Self {
        assert!(capacity > 0, "TraceRingBuffer capacity must be > 0");
        Self {
            capacity,
            buffer: RwLock::new(Vec::with_capacity(capacity)),
            head: RwLock::new(0),
            total_pushed: RwLock::new(0),
        }
    }

    /// Push a frame into the buffer. Overwrites the oldest frame if full.
    pub fn push(&self, frame: CanFrame) {
        let mut buf = self.buffer.write().unwrap();
        let mut head = self.head.write().unwrap();
        let mut total = self.total_pushed.write().unwrap();

        if buf.len() < self.capacity {
            buf.push(frame);
        } else {
            buf[*head] = frame;
            *head = (*head + 1) % self.capacity;
        }
        *total += 1;
    }

    /// Get total number of frames pushed into this buffer over its lifetime.
    pub fn total_pushed(&self) -> u64 {
        *self.total_pushed.read().unwrap()
    }

    /// Current number of stored frames in the buffer (up to capacity).
    pub fn len(&self) -> usize {
        self.buffer.read().unwrap().len()
    }

    /// Whether the buffer is currently empty.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Clear all stored frames.
    pub fn clear(&self) {
        let mut buf = self.buffer.write().unwrap();
        let mut head = self.head.write().unwrap();
        let mut total = self.total_pushed.write().unwrap();
        buf.clear();
        *head = 0;
        *total = 0;
    }

    /// Get a chronological snapshot of the stored frames.
    /// If `limit` is specified, returns at most the last `limit` frames.
    pub fn snapshot(&self, limit: Option<usize>) -> Vec<CanFrame> {
        let buf = self.buffer.read().unwrap();
        let head = *self.head.read().unwrap();
        let len = buf.len();

        if len == 0 {
            return Vec::new();
        }

        let mut ordered = Vec::with_capacity(len);
        if len < self.capacity {
            // Buffer hasn't wrapped around yet
            ordered.extend_from_slice(&buf[..len]);
        } else {
            // Wrapped around: head points to oldest frame
            ordered.extend_from_slice(&buf[head..]);
            ordered.extend_from_slice(&buf[..head]);
        }

        if let Some(lim) = limit
            && lim < ordered.len()
        {
            let start = ordered.len() - lim;
            return ordered[start..].to_vec();
        }
        ordered
    }

    /// Filter snapshot by standard/extended CAN ID and mask.
    pub fn filter_by_id(&self, filter_id: u32, filter_mask: u32) -> Vec<CanFrame> {
        self.snapshot(None)
            .into_iter()
            .filter(|f| (f.id & filter_mask) == (filter_id & filter_mask))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ring_buffer_push_and_snapshot() {
        let ring = TraceRingBuffer::new(5);
        assert_eq!(ring.len(), 0);
        assert!(ring.is_empty());

        for i in 1..=3 {
            ring.push(CanFrame::new(i, &[i as u8]).unwrap());
        }
        assert_eq!(ring.len(), 3);
        assert_eq!(ring.total_pushed(), 3);

        let snap = ring.snapshot(None);
        assert_eq!(snap.len(), 3);
        assert_eq!(snap[0].id, 1);
        assert_eq!(snap[2].id, 3);
    }

    #[test]
    fn test_ring_buffer_overflow_wrapping() {
        let ring = TraceRingBuffer::new(3);
        for i in 1..=5 {
            ring.push(CanFrame::new(i, &[i as u8]).unwrap());
        }
        assert_eq!(ring.len(), 3);
        assert_eq!(ring.total_pushed(), 5);

        let snap = ring.snapshot(None);
        assert_eq!(snap.len(), 3);
        assert_eq!(snap[0].id, 3);
        assert_eq!(snap[1].id, 4);
        assert_eq!(snap[2].id, 5);

        let last_two = ring.snapshot(Some(2));
        assert_eq!(last_two.len(), 2);
        assert_eq!(last_two[0].id, 4);
        assert_eq!(last_two[1].id, 5);
    }
}
