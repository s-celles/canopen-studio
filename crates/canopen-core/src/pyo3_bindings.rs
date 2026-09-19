/*
 * CANopen Studio — High-Performance Core Engine (canopen-core)
 *
 * Python bindings via PyO3.
 *
 * Copyright (C) 2026 Sébastien Celles <s.celles@gmail.com>
 * License: GNU General Public License v3.0 (GPL-3.0-or-later)
 */

#[cfg(feature = "python")]
use pyo3::exceptions::{PyIOError, PyValueError};
#[cfg(feature = "python")]
use pyo3::prelude::*;
#[cfg(feature = "python")]
use pyo3::types::{PyBytes, PyDict};

#[cfg(feature = "python")]
use crate::canopen::{decode_canopen_frame, CanopenService};
#[cfg(feature = "python")]
use crate::frame::CanFrame;
#[cfg(feature = "python")]
use crate::latency::LatencyTracker;
#[cfg(feature = "python")]
use crate::obd2::decode_obd2_mode01_frame;
#[cfg(feature = "python")]
use crate::ring_buffer::TraceRingBuffer;
#[cfg(feature = "python")]
use crate::udp::UdpCanBus;
#[cfg(feature = "python")]
use std::sync::Arc;
#[cfg(feature = "python")]
use std::time::Duration;

#[cfg(feature = "python")]
#[pyclass(name = "CanFrame")]
#[derive(Clone)]
pub struct PyCanFrame {
    pub inner: CanFrame,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyCanFrame {
    #[new]
    #[pyo3(signature = (id, data=None, timestamp_us=None, is_extended=None))]
    pub fn new(
        id: u32,
        data: Option<&[u8]>,
        timestamp_us: Option<u64>,
        is_extended: Option<bool>,
    ) -> PyResult<Self> {
        let payload = data.unwrap_or(&[]);
        let mut frame = if let Some(ts) = timestamp_us {
            CanFrame::new_with_timestamp(id, payload, ts)
        } else {
            CanFrame::new(id, payload)
        }
        .map_err(|e| PyValueError::new_err(e.to_string()))?;

        if let Some(ext) = is_extended {
            frame.is_extended = ext;
        }

        Ok(PyCanFrame { inner: frame })
    }

    #[getter]
    pub fn id(&self) -> u32 {
        self.inner.id
    }

    #[getter]
    pub fn dlc(&self) -> u8 {
        self.inner.dlc
    }

    #[getter]
    pub fn is_extended(&self) -> bool {
        self.inner.is_extended
    }

    #[getter]
    pub fn is_remote(&self) -> bool {
        self.inner.is_remote
    }

    #[getter]
    pub fn is_error(&self) -> bool {
        self.inner.is_error
    }

    #[getter]
    pub fn timestamp_us(&self) -> u64 {
        self.inner.timestamp_us
    }

    #[getter]
    pub fn timestamp_sec(&self) -> f64 {
        (self.inner.timestamp_us as f64) / 1_000_000.0
    }

    #[getter]
    pub fn data<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, self.inner.payload())
    }

    pub fn to_msgpack<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let bytes = self
            .inner
            .to_python_can_msgpack()
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(PyBytes::new(py, &bytes))
    }

    #[staticmethod]
    pub fn from_msgpack(data: &[u8]) -> PyResult<Self> {
        let frame = CanFrame::from_python_can_msgpack(data)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(PyCanFrame { inner: frame })
    }

    pub fn to_compact<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        let bytes = self.inner.to_compact_bytes();
        PyBytes::new(py, &bytes)
    }

    #[staticmethod]
    pub fn from_compact(data: &[u8]) -> PyResult<Self> {
        let frame =
            CanFrame::from_compact_bytes(data).map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(PyCanFrame { inner: frame })
    }

    fn __repr__(&self) -> String {
        format!(
            "CanFrame(id=0x{:X}, dlc={}, data={:?}, timestamp_us={})",
            self.inner.id,
            self.inner.dlc,
            self.inner.payload(),
            self.inner.timestamp_us
        )
    }

    fn __str__(&self) -> String {
        self.inner.to_string()
    }
}

#[cfg(feature = "python")]
#[pyclass(name = "LatencyTracker")]
pub struct PyLatencyTracker {
    inner: Arc<LatencyTracker>,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyLatencyTracker {
    #[new]
    #[pyo3(signature = (nominal_interval_us=None))]
    pub fn new(nominal_interval_us: Option<f64>) -> Self {
        Self {
            inner: Arc::new(LatencyTracker::new(nominal_interval_us)),
        }
    }

    pub fn record_frame_timestamp(&self, timestamp_us: u64) -> Option<f64> {
        self.inner.record_frame_timestamp(timestamp_us)
    }

    pub fn record_sample_us(&self, delta_us: f64) {
        self.inner.record_sample_us(delta_us);
    }

    pub fn stats<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let s = self.inner.stats();
        let dict = PyDict::new(py);
        dict.set_item("count", s.count)?;
        dict.set_item("min_us", s.min_us)?;
        dict.set_item("max_us", s.max_us)?;
        dict.set_item("avg_us", s.avg_us)?;
        dict.set_item("jitter_us", s.jitter_us)?;
        dict.set_item("std_dev_us", s.std_dev_us)?;
        dict.set_item("nominal_us", s.nominal_us)?;
        Ok(dict)
    }

    pub fn reset(&self) {
        self.inner.reset();
    }
}

#[cfg(feature = "python")]
#[pyclass(name = "TraceRingBuffer")]
pub struct PyTraceRingBuffer {
    inner: Arc<TraceRingBuffer>,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyTraceRingBuffer {
    #[new]
    pub fn new(capacity: usize) -> Self {
        Self {
            inner: Arc::new(TraceRingBuffer::new(capacity)),
        }
    }

    pub fn push(&self, frame: &PyCanFrame) {
        self.inner.push(frame.inner);
    }

    pub fn len(&self) -> usize {
        self.inner.len()
    }

    pub fn total_pushed(&self) -> u64 {
        self.inner.total_pushed()
    }

    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }

    pub fn clear(&self) {
        self.inner.clear();
    }

    #[pyo3(signature = (limit=None))]
    pub fn snapshot(&self, limit: Option<usize>) -> Vec<PyCanFrame> {
        self.inner
            .snapshot(limit)
            .into_iter()
            .map(|f| PyCanFrame { inner: f })
            .collect()
    }
}

#[cfg(feature = "python")]
#[pyclass(name = "UdpCanBus")]
pub struct PyUdpCanBus {
    inner: UdpCanBus,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyUdpCanBus {
    #[new]
    #[pyo3(signature = (bind_port=1750, target_host="127.0.0.1", target_port=1750, compact=false))]
    pub fn new(
        bind_port: u16,
        target_host: &str,
        target_port: u16,
        compact: bool,
    ) -> PyResult<Self> {
        let bus = UdpCanBus::new(bind_port, target_host, target_port, compact)
            .map_err(|e| PyIOError::new_err(e.to_string()))?;
        Ok(Self { inner: bus })
    }

    pub fn send(&self, frame: &PyCanFrame) -> PyResult<()> {
        self.inner
            .send(&frame.inner)
            .map_err(|e| PyIOError::new_err(e.to_string()))
    }

    #[pyo3(signature = (timeout_ms=None))]
    pub fn recv(&self, timeout_ms: Option<u64>) -> PyResult<Option<(PyCanFrame, String)>> {
        let timeout = timeout_ms.map(Duration::from_millis);
        self.inner
            .set_read_timeout(timeout)
            .map_err(|e| PyIOError::new_err(e.to_string()))?;

        match self.inner.recv() {
            Ok((frame, addr)) => Ok(Some((PyCanFrame { inner: frame }, addr.to_string()))),
            Err(e) => {
                // Return None on timeout
                let err_str = e.to_string();
                if err_str.contains("timed out")
                    || err_str.contains("Resource temporarily unavailable")
                {
                    Ok(None)
                } else {
                    Err(PyIOError::new_err(err_str))
                }
            }
        }
    }
}

#[cfg(feature = "python")]
#[pyfunction]
pub fn decode_canopen(frame: &PyCanFrame) -> (String, String) {
    let info = decode_canopen_frame(&frame.inner);
    let s_type = match info.service {
        CanopenService::NmtCommand => "NMT",
        CanopenService::Sync => "SYNC",
        CanopenService::Time => "TIME",
        CanopenService::Emergency { .. } => "EMCY",
        CanopenService::Tpdo { .. } => "TPDO",
        CanopenService::Rpdo { .. } => "RPDO",
        CanopenService::Tsdo { .. } => "TSDO",
        CanopenService::Rsdo { .. } => "RSDO",
        CanopenService::Heartbeat { .. } => "HEARTBEAT",
        CanopenService::Other { .. } => "CAN",
    };
    (s_type.to_string(), info.description)
}

#[cfg(feature = "python")]
#[pyfunction]
pub fn decode_obd2<'py>(
    py: Python<'py>,
    frame: &PyCanFrame,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    if let Some(r) = decode_obd2_mode01_frame(&frame.inner) {
        let d = PyDict::new(py);
        d.set_item("mode", r.mode)?;
        d.set_item("pid", r.pid)?;
        d.set_item("name", r.name)?;
        d.set_item("value", r.value)?;
        d.set_item("unit", r.unit)?;
        Ok(Some(d))
    } else {
        Ok(None)
    }
}

#[cfg(feature = "python")]
#[pyfunction]
pub fn build_sdo_read(node_id: u8, index: u16, subindex: u8) -> PyResult<PyCanFrame> {
    let frame = crate::sdo::build_sdo_read(node_id, index, subindex)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(PyCanFrame { inner: frame })
}

#[cfg(feature = "python")]
#[pyfunction]
pub fn build_sdo_write(node_id: u8, index: u16, subindex: u8, data: &[u8]) -> PyResult<PyCanFrame> {
    let frame = crate::sdo::build_sdo_write(node_id, index, subindex, data)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(PyCanFrame { inner: frame })
}

#[cfg(feature = "python")]
#[pyfunction]
pub fn build_sdo_abort(
    node_id: u8,
    index: u16,
    subindex: u8,
    abort_code: u32,
) -> PyResult<PyCanFrame> {
    let code = crate::sdo::SdoAbortCode::from(abort_code);
    let frame = crate::sdo::build_sdo_abort(node_id, index, subindex, code)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(PyCanFrame { inner: frame })
}

#[cfg(feature = "python")]
#[pyfunction]
pub fn decode_sdo<'py>(
    py: Python<'py>,
    frame: &PyCanFrame,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    if let Some(msg) = crate::sdo::parse_sdo_frame(&frame.inner) {
        let d = PyDict::new(py);
        match msg {
            crate::sdo::SdoMessage::UploadRequest {
                node_id,
                index,
                subindex,
            } => {
                d.set_item("type", "UPLOAD_REQUEST")?;
                d.set_item("node_id", node_id)?;
                d.set_item("index", index)?;
                d.set_item("subindex", subindex)?;
            }
            crate::sdo::SdoMessage::ExpeditedUploadResponse {
                node_id,
                index,
                subindex,
                data,
            } => {
                d.set_item("type", "EXPEDITED_UPLOAD_RESPONSE")?;
                d.set_item("node_id", node_id)?;
                d.set_item("index", index)?;
                d.set_item("subindex", subindex)?;
                d.set_item("data", PyBytes::new(py, &data))?;
            }
            crate::sdo::SdoMessage::ExpeditedDownloadRequest {
                node_id,
                index,
                subindex,
                data,
            } => {
                d.set_item("type", "EXPEDITED_DOWNLOAD_REQUEST")?;
                d.set_item("node_id", node_id)?;
                d.set_item("index", index)?;
                d.set_item("subindex", subindex)?;
                d.set_item("data", PyBytes::new(py, &data))?;
            }
            crate::sdo::SdoMessage::DownloadResponse {
                node_id,
                index,
                subindex,
            } => {
                d.set_item("type", "DOWNLOAD_RESPONSE")?;
                d.set_item("node_id", node_id)?;
                d.set_item("index", index)?;
                d.set_item("subindex", subindex)?;
            }
            crate::sdo::SdoMessage::Abort {
                node_id,
                index,
                subindex,
                code,
            } => {
                d.set_item("type", "ABORT")?;
                d.set_item("node_id", node_id)?;
                d.set_item("index", index)?;
                d.set_item("subindex", subindex)?;
                d.set_item("code", code.code())?;
                d.set_item("description", code.description())?;
            }
            crate::sdo::SdoMessage::Other {
                node_id,
                cs,
                index,
                subindex,
            } => {
                d.set_item("type", "OTHER")?;
                d.set_item("node_id", node_id)?;
                d.set_item("cs", cs)?;
                d.set_item("index", index)?;
                d.set_item("subindex", subindex)?;
            }
        }
        Ok(Some(d))
    } else {
        Ok(None)
    }
}

#[cfg(feature = "python")]
#[pyclass(name = "IsoTpReassembler")]
pub struct PyIsoTpReassembler {
    inner: crate::isotp::IsoTpReassembler,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyIsoTpReassembler {
    #[new]
    pub fn new(expected_rx_id: u32, tx_fc_id: u32) -> Self {
        Self {
            inner: crate::isotp::IsoTpReassembler::new(expected_rx_id, tx_fc_id),
        }
    }

    pub fn reset(&mut self) {
        self.inner.reset();
    }

    pub fn is_transfer_in_progress(&self) -> bool {
        self.inner.is_transfer_in_progress()
    }

    pub fn process_frame<'py>(
        &mut self,
        py: Python<'py>,
        frame: &PyCanFrame,
    ) -> PyResult<(Option<Bound<'py, PyBytes>>, Option<PyCanFrame>)> {
        let (data_opt, fc_opt) = self
            .inner
            .process_frame(&frame.inner)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;

        let py_data = data_opt.map(|d| PyBytes::new(py, &d));
        let py_fc = fc_opt.map(|f| PyCanFrame { inner: f });
        Ok((py_data, py_fc))
    }
}

#[cfg(feature = "python")]
#[pyfunction]
pub fn fragment_isotp(tx_id: u32, data: &[u8]) -> PyResult<Vec<PyCanFrame>> {
    let frames = crate::isotp::fragment_isotp_message(tx_id, data)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(frames
        .into_iter()
        .map(|f| PyCanFrame { inner: f })
        .collect())
}

/// PyO3 C-Python module definition.
#[cfg(feature = "python")]
#[pymodule]
pub fn canopen_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyCanFrame>()?;
    m.add_class::<PyLatencyTracker>()?;
    m.add_class::<PyTraceRingBuffer>()?;
    m.add_class::<PyUdpCanBus>()?;
    m.add_class::<PyIsoTpReassembler>()?;
    m.add_function(wrap_pyfunction!(decode_canopen, m)?)?;
    m.add_function(wrap_pyfunction!(decode_obd2, m)?)?;
    m.add_function(wrap_pyfunction!(build_sdo_read, m)?)?;
    m.add_function(wrap_pyfunction!(build_sdo_write, m)?)?;
    m.add_function(wrap_pyfunction!(build_sdo_abort, m)?)?;
    m.add_function(wrap_pyfunction!(decode_sdo, m)?)?;
    m.add_function(wrap_pyfunction!(fragment_isotp, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
