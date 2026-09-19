/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

use serde::{Deserialize, Serialize};
use std::sync::RwLock;

/// Summary statistics for latency and jitter measurements (in microseconds).
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct LatencyStats {
    pub count: u64,
    pub min_us: f64,
    pub max_us: f64,
    pub avg_us: f64,
    pub jitter_us: f64,
    pub std_dev_us: f64,
    pub nominal_us: Option<f64>,
}

/// High-precision tracker for periodic frame jitter and round-trip times (RTT).
pub struct LatencyTracker {
    nominal_interval_us: Option<f64>,
    inner: RwLock<TrackerInner>,
}

struct TrackerInner {
    last_timestamp_us: Option<u64>,
    count: u64,
    min_us: f64,
    max_us: f64,
    sum_us: f64,
    sum_sq_us: f64,
    max_jitter_us: f64,
}

impl LatencyTracker {
    /// Create a new tracker. Optionally pass a nominal interval in microseconds (e.g. 20,000 µs for 50 Hz SYNC).
    pub fn new(nominal_interval_us: Option<f64>) -> Self {
        Self {
            nominal_interval_us,
            inner: RwLock::new(TrackerInner {
                last_timestamp_us: None,
                count: 0,
                min_us: f64::INFINITY,
                max_us: 0.0,
                sum_us: 0.0,
                sum_sq_us: 0.0,
                max_jitter_us: 0.0,
            }),
        }
    }

    /// Record a frame timestamp (in microseconds).
    /// Computes the delta relative to the previous frame timestamp.
    pub fn record_frame_timestamp(&self, timestamp_us: u64) -> Option<f64> {
        let mut inner = self.inner.write().unwrap();
        if let Some(prev) = inner.last_timestamp_us {
            let delta = if timestamp_us >= prev {
                (timestamp_us - prev) as f64
            } else {
                0.0
            };
            inner.last_timestamp_us = Some(timestamp_us);
            Self::record_sample(&mut inner, delta, self.nominal_interval_us);
            Some(delta)
        } else {
            inner.last_timestamp_us = Some(timestamp_us);
            None
        }
    }

    /// Record an explicit measurement sample (e.g. ping RTT in microseconds).
    pub fn record_sample_us(&self, delta_us: f64) {
        let mut inner = self.inner.write().unwrap();
        Self::record_sample(&mut inner, delta_us, self.nominal_interval_us);
    }

    fn record_sample(inner: &mut TrackerInner, delta: f64, nominal: Option<f64>) {
        inner.count += 1;
        if delta < inner.min_us {
            inner.min_us = delta;
        }
        if delta > inner.max_us {
            inner.max_us = delta;
        }
        inner.sum_us += delta;
        inner.sum_sq_us += delta * delta;

        if let Some(nom) = nominal {
            let jitter = (delta - nom).abs();
            if jitter > inner.max_jitter_us {
                inner.max_jitter_us = jitter;
            }
        }
    }

    /// Get current statistical snapshot.
    pub fn stats(&self) -> LatencyStats {
        let inner = self.inner.read().unwrap();
        if inner.count == 0 {
            return LatencyStats {
                count: 0,
                min_us: 0.0,
                max_us: 0.0,
                avg_us: 0.0,
                jitter_us: 0.0,
                std_dev_us: 0.0,
                nominal_us: self.nominal_interval_us,
            };
        }

        let n = inner.count as f64;
        let avg = inner.sum_us / n;
        let variance = (inner.sum_sq_us / n) - (avg * avg);
        let std_dev = if variance > 0.0 { variance.sqrt() } else { 0.0 };

        let jitter = if self.nominal_interval_us.is_some() {
            inner.max_jitter_us
        } else {
            inner.max_us - inner.min_us
        };

        LatencyStats {
            count: inner.count,
            min_us: inner.min_us,
            max_us: inner.max_us,
            avg_us: avg,
            jitter_us: jitter,
            std_dev_us: std_dev,
            nominal_us: self.nominal_interval_us,
        }
    }

    /// Reset all recorded statistics.
    pub fn reset(&self) {
        let mut inner = self.inner.write().unwrap();
        inner.last_timestamp_us = None;
        inner.count = 0;
        inner.min_us = f64::INFINITY;
        inner.max_us = 0.0;
        inner.sum_us = 0.0;
        inner.sum_sq_us = 0.0;
        inner.max_jitter_us = 0.0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_latency_jitter_tracking() {
        // Nominal 20 ms = 20,000 µs (50 Hz SYNC)
        let tracker = LatencyTracker::new(Some(20_000.0));

        tracker.record_frame_timestamp(1_000_000);
        tracker.record_frame_timestamp(1_020_000); // delta = 20,000 µs (0 jitter)
        tracker.record_frame_timestamp(1_040_500); // delta = 20,500 µs (+500 jitter)
        tracker.record_frame_timestamp(1_059_800); // delta = 19,300 µs (-700 jitter)

        let stats = tracker.stats();
        assert_eq!(stats.count, 3);
        assert_eq!(stats.min_us, 19_300.0);
        assert_eq!(stats.max_us, 20_500.0);
        assert_eq!(stats.jitter_us, 700.0);
        assert!((stats.avg_us - 19_933.33).abs() < 1.0);
    }
}
