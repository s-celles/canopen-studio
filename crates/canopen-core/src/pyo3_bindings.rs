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
use crate::bitstream::AckSlot;
#[cfg(feature = "python")]
use crate::canopen::{CanopenService, decode_canopen_frame};
#[cfg(feature = "python")]
use crate::frame::CanFrame;
#[cfg(feature = "python")]
use crate::latency::LatencyTracker;
#[cfg(feature = "python")]
use crate::obd2::decode_obd2_mode01_frame;
#[cfg(feature = "python")]
use crate::pcap::PcapNgWriter;
#[cfg(feature = "python")]
use crate::ring_buffer::TraceRingBuffer;
#[cfg(feature = "python")]
use crate::udp::UdpCanBus;
#[cfg(feature = "python")]
use crate::vcd::{Timing, VcdOptions, VcdWriter};
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
pub fn decode_canopen_message<'py>(
    py: Python<'py>,
    frame: &PyCanFrame,
) -> PyResult<Bound<'py, PyDict>> {
    let info = crate::canopen::decode_canopen_frame(&frame.inner);
    let d = PyDict::new(py);

    let mut s_type = "RAW_CAN";
    let mut node_id: Option<u8> = None;
    let mut pdo_num: Option<u8> = None;

    if frame.inner.is_extended {
        s_type = "EXTENDED_J1939";
        node_id = Some((frame.inner.id & 0xFF) as u8);
    } else {
        match info.service {
            crate::canopen::CanopenService::NmtCommand => {
                s_type = "NMT_MASTER";
                // Target node is byte 1
                if let Some(&node) = frame.inner.payload().get(1) {
                    node_id = Some(node);
                }
            }
            crate::canopen::CanopenService::Sync => {
                s_type = "SYNC";
            }
            crate::canopen::CanopenService::Time => {
                s_type = "TIME_STAMP";
            }
            crate::canopen::CanopenService::Emergency { node_id: n, .. } => {
                s_type = "EMERGENCY";
                node_id = Some(n);
            }
            crate::canopen::CanopenService::Tpdo {
                pdo_num: p,
                node_id: n,
            } => {
                s_type = "TPDO";
                pdo_num = Some(p);
                node_id = Some(n);
            }
            crate::canopen::CanopenService::Rpdo {
                pdo_num: p,
                node_id: n,
            } => {
                s_type = "RPDO";
                pdo_num = Some(p);
                node_id = Some(n);
            }
            crate::canopen::CanopenService::Tsdo { node_id: n } => {
                s_type = "SDO_TX"; // Server->Client
                node_id = Some(n);
            }
            crate::canopen::CanopenService::Rsdo { node_id: n } => {
                s_type = "SDO_RX"; // Client->Server
                node_id = Some(n);
            }
            crate::canopen::CanopenService::Heartbeat { node_id: n, state } => {
                s_type = "HEARTBEAT";
                node_id = Some(n);
                d.set_item("nmt_state", state as u8)?;
            }
            crate::canopen::CanopenService::Other { .. } => {}
        }
    }

    d.set_item("service", s_type)?;
    d.set_item("description", info.description)?;
    if let Some(n) = node_id {
        d.set_item("node_id", n)?;
    } else {
        d.set_item("node_id", py.None())?;
    }
    if let Some(p) = pdo_num {
        d.set_item("pdo_number", p)?;
    } else {
        d.set_item("pdo_number", py.None())?;
    }

    Ok(d)
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
                d.set_item("data", data)?;
            }
            crate::sdo::SdoMessage::InitiateUploadResponse {
                node_id,
                index,
                subindex,
                size,
            } => {
                d.set_item("type", "INITIATE_UPLOAD_RESPONSE")?;
                d.set_item("node_id", node_id)?;
                d.set_item("index", index)?;
                d.set_item("subindex", subindex)?;
                if let Some(s) = size {
                    d.set_item("size", s)?;
                }
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
                d.set_item("data", data)?;
            }
            crate::sdo::SdoMessage::InitiateDownloadRequest {
                node_id,
                index,
                subindex,
                size,
            } => {
                d.set_item("type", "INITIATE_DOWNLOAD_REQUEST")?;
                d.set_item("node_id", node_id)?;
                d.set_item("index", index)?;
                d.set_item("subindex", subindex)?;
                if let Some(s) = size {
                    d.set_item("size", s)?;
                }
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
            crate::sdo::SdoMessage::SegmentDownloadRequest {
                node_id,
                toggle,
                is_last,
                data,
            } => {
                d.set_item("type", "SEGMENT_DOWNLOAD_REQUEST")?;
                d.set_item("node_id", node_id)?;
                d.set_item("toggle", toggle)?;
                d.set_item("is_last", is_last)?;
                d.set_item("data", data)?;
            }
            crate::sdo::SdoMessage::SegmentDownloadResponse { node_id, toggle } => {
                d.set_item("type", "SEGMENT_DOWNLOAD_RESPONSE")?;
                d.set_item("node_id", node_id)?;
                d.set_item("toggle", toggle)?;
            }
            crate::sdo::SdoMessage::SegmentUploadRequest { node_id, toggle } => {
                d.set_item("type", "SEGMENT_UPLOAD_REQUEST")?;
                d.set_item("node_id", node_id)?;
                d.set_item("toggle", toggle)?;
            }
            crate::sdo::SdoMessage::SegmentUploadResponse {
                node_id,
                toggle,
                is_last,
                data,
            } => {
                d.set_item("type", "SEGMENT_UPLOAD_RESPONSE")?;
                d.set_item("node_id", node_id)?;
                d.set_item("toggle", toggle)?;
                d.set_item("is_last", is_last)?;
                d.set_item("data", data)?;
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
                payload,
            } => {
                d.set_item("type", "OTHER")?;
                d.set_item("node_id", node_id)?;
                d.set_item("cs", cs)?;
                d.set_item("payload", payload)?;
            }
        }
        Ok(Some(d))
    } else {
        Ok(None)
    }
}

#[cfg(feature = "python")]
#[pyclass(name = "FeedResult")]
pub struct PyFeedResult {
    #[pyo3(get)]
    pub completed: Option<PyObject>,
    #[pyo3(get)]
    pub flow_control_required: bool,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyFeedResult {
    fn __bool__(&self) -> bool {
        self.completed.is_some()
    }
}

#[cfg(feature = "python")]
#[pyclass(name = "IsoTpReassembler")]
pub struct PyIsoTpReassembler {
    manager: crate::isotp_manager::IsoTpManager,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyIsoTpReassembler {
    #[new]
    pub fn new() -> Self {
        Self {
            manager: crate::isotp_manager::IsoTpManager::new(),
        }
    }

    pub fn reset(&mut self) {
        self.manager.reset();
    }

    pub fn pending_sources(&self) -> Vec<u32> {
        self.manager.pending_sources()
    }

    pub fn feed(&mut self, py: Python, source: u32, data: &[u8]) -> PyResult<PyFeedResult> {
        if data.iter().all(|&x| x == 0) {
            return Ok(PyFeedResult {
                completed: None,
                flow_control_required: false,
            });
        }

        let frame = crate::frame::CanFrame::new(source, data).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(format!("Invalid CAN frame: {}", e))
        })?;

        match self.manager.process_frame(&frame) {
            Ok((completed, fc_frame)) => {
                let py_completed = match completed {
                    Some(ref b) => Some(pyo3::types::PyBytes::new(py, b.as_slice()).into()),
                    None => None,
                };
                Ok(PyFeedResult {
                    completed: py_completed,
                    flow_control_required: fc_frame.is_some(),
                })
            }
            Err(e) => Err(pyo3::exceptions::PyValueError::new_err(format!(
                "ProtocolError: {}",
                e
            ))),
        }
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

#[cfg(feature = "python")]
#[pyclass(name = "PdoMapping")]
pub struct PyPdoMapping {
    inner: crate::pdo::PdoMapping,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyPdoMapping {
    #[new]
    pub fn new(cob_id: u32, name: &str) -> Self {
        Self {
            inner: crate::pdo::PdoMapping::new(cob_id, name),
        }
    }

    #[getter]
    pub fn cob_id(&self) -> u32 {
        self.inner.cob_id
    }

    #[getter]
    pub fn name(&self) -> &str {
        &self.inner.name
    }

    #[pyo3(signature = (name, bit_start, bit_length, signal_type="uint16", factor=1.0, offset=0.0, unit=""))]
    pub fn add_signal(
        &mut self,
        name: &str,
        bit_start: u8,
        bit_length: u8,
        signal_type: &str,
        factor: f64,
        offset: f64,
        unit: &str,
    ) -> PyResult<()> {
        let st = match signal_type.to_lowercase().as_str() {
            "bool" => crate::pdo::SignalType::Bool,
            "uint8" => crate::pdo::SignalType::Uint8,
            "int8" => crate::pdo::SignalType::Int8,
            "uint16" => crate::pdo::SignalType::Uint16,
            "int16" => crate::pdo::SignalType::Int16,
            "uint32" => crate::pdo::SignalType::Uint32,
            "int32" => crate::pdo::SignalType::Int32,
            other => {
                return Err(PyValueError::new_err(format!(
                    "Unsupported signal type: {}",
                    other
                )));
            }
        };
        self.inner.add_signal(crate::pdo::SignalDefinition::new(
            name, bit_start, bit_length, st, factor, offset, unit,
        ));
        Ok(())
    }

    pub fn decode_frame<'py>(
        &self,
        py: Python<'py>,
        frame: &PyCanFrame,
    ) -> PyResult<Vec<Bound<'py, PyDict>>> {
        let readings = self.inner.decode_frame(&frame.inner);
        let mut results = Vec::with_capacity(readings.len());
        for r in readings {
            let d = PyDict::new(py);
            d.set_item("name", r.name)?;
            d.set_item("value", r.value)?;
            d.set_item("unit", r.unit)?;
            results.push(d);
        }
        Ok(results)
    }

    pub fn encode_frame(&self, values: Vec<(String, f64)>) -> PyResult<PyCanFrame> {
        let slice: Vec<(&str, f64)> = values.iter().map(|(n, v)| (n.as_str(), *v)).collect();
        let frame = self
            .inner
            .encode_frame(&slice)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(PyCanFrame { inner: frame })
    }
}

#[cfg(feature = "python")]
#[pyclass(name = "NmtMaster")]
pub struct PyNmtMaster {
    inner: crate::nmt::NmtMaster,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyNmtMaster {
    #[new]
    pub fn new() -> Self {
        Self {
            inner: crate::nmt::NmtMaster::new(),
        }
    }

    #[staticmethod]
    #[pyo3(signature = (command, target_node=0))]
    pub fn build_command(command: &str, target_node: u8) -> PyResult<PyCanFrame> {
        let cmd = match command.to_lowercase().as_str() {
            "start" => crate::nmt::NmtCommand::StartRemoteNode,
            "stop" => crate::nmt::NmtCommand::StopRemoteNode,
            "preop" | "preoperational" => crate::nmt::NmtCommand::EnterPreOperational,
            "reset" | "reset_node" => crate::nmt::NmtCommand::ResetNode,
            "reset_comm" | "reset_communication" => crate::nmt::NmtCommand::ResetCommunication,
            other => {
                return Err(PyValueError::new_err(format!(
                    "Unknown NMT command: {}",
                    other
                )));
            }
        };
        let frame = crate::nmt::NmtMaster::build_nmt_command(cmd, target_node)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(PyCanFrame { inner: frame })
    }

    pub fn set_heartbeat_interval(&mut self, node_id: u8, interval_ms: u64) {
        self.inner
            .set_node_heartbeat_interval(node_id, interval_ms * 1_000);
    }

    pub fn process_frame(&mut self, frame: &PyCanFrame) -> Option<(u8, String, String)> {
        self.inner
            .process_frame(&frame.inner)
            .map(|(node, old, new)| (node, format!("{:?}", old), format!("{:?}", new)))
    }

    #[pyo3(signature = (now_sec=None))]
    pub fn check_timeouts(&mut self, now_sec: Option<f64>) -> Vec<u32> {
        let now_us = if let Some(sec) = now_sec {
            (sec * 1_000_000.0) as u64
        } else {
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_micros() as u64
        };
        self.inner
            .check_timeouts(now_us)
            .into_iter()
            .map(|id| id as u32)
            .collect()
    }

    pub fn all_nodes<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyDict>>> {
        let nodes = self.inner.all_nodes();
        let mut results = Vec::with_capacity(nodes.len());
        for n in nodes {
            let d = PyDict::new(py);
            d.set_item("node_id", n.node_id)?;
            d.set_item("state", format!("{:?}", n.state))?;
            d.set_item("last_seen_sec", (n.last_seen_us as f64) / 1_000_000.0)?;
            d.set_item("is_timed_out", n.is_timed_out)?;
            results.push(d);
        }
        Ok(results)
    }
}

#[cfg(feature = "python")]
fn object_entry_to_pydict<'py>(
    py: Python<'py>,
    obj: &crate::eds::ObjectEntry,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("index", obj.index)?;
    d.set_item("subindex", obj.subindex)?;
    d.set_item("name", &obj.name)?;
    d.set_item("object_type", obj.object_type)?;
    d.set_item("data_type", obj.data_type.type_name())?;
    d.set_item("bit_length", obj.data_type.bit_length())?;
    d.set_item("access", obj.access.as_str())?;
    d.set_item("default_value", &obj.default_value)?;
    d.set_item("pdo_mapping", obj.pdo_mapping)?;
    d.set_item("low_limit", &obj.low_limit)?;
    d.set_item("high_limit", &obj.high_limit)?;
    Ok(d)
}

#[cfg(feature = "python")]
#[pyclass(name = "EdsFile")]
pub struct PyEdsFile {
    inner: crate::eds::EdsFile,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyEdsFile {
    #[staticmethod]
    pub fn parse(content: &str) -> PyResult<Self> {
        let eds = crate::eds::EdsFile::parse(content)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(Self { inner: eds })
    }

    #[staticmethod]
    pub fn load_file(path: &str) -> PyResult<Self> {
        let content = std::fs::read_to_string(path)
            .map_err(|e| PyValueError::new_err(format!("Cannot read EDS file {}: {}", path, e)))?;
        Self::parse(&content)
    }

    pub fn file_info<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        d.set_item("file_name", &self.inner.file_info.file_name)?;
        d.set_item("file_version", &self.inner.file_info.file_version)?;
        d.set_item("description", &self.inner.file_info.description)?;
        d.set_item("eds_version", &self.inner.file_info.eds_version)?;
        d.set_item("created_by", &self.inner.file_info.created_by)?;
        Ok(d)
    }

    pub fn device_info<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        d.set_item("vendor_name", &self.inner.device_info.vendor_name)?;
        d.set_item("vendor_number", self.inner.device_info.vendor_number)?;
        d.set_item("product_name", &self.inner.device_info.product_name)?;
        d.set_item("product_number", self.inner.device_info.product_number)?;
        d.set_item("revision_number", self.inner.device_info.revision_number)?;
        d.set_item("order_code", &self.inner.device_info.order_code)?;
        Ok(d)
    }

    #[pyo3(signature = (index, subindex=0))]
    pub fn get_object<'py>(
        &self,
        py: Python<'py>,
        index: u16,
        subindex: u8,
    ) -> PyResult<Option<Bound<'py, PyDict>>> {
        match self.inner.get(index, subindex) {
            Some(obj) => Ok(Some(object_entry_to_pydict(py, obj)?)),
            None => Ok(None),
        }
    }

    pub fn find_objects_by_name<'py>(
        &self,
        py: Python<'py>,
        query: &str,
    ) -> PyResult<Vec<Bound<'py, PyDict>>> {
        let objs = self.inner.find_by_name(query);
        let mut results = Vec::with_capacity(objs.len());
        for obj in objs {
            results.push(object_entry_to_pydict(py, obj)?);
        }
        Ok(results)
    }

    pub fn pdo_mappable_objects<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyDict>>> {
        let objs = self.inner.pdo_mappable();
        let mut results = Vec::with_capacity(objs.len());
        for obj in objs {
            results.push(object_entry_to_pydict(py, obj)?);
        }
        Ok(results)
    }

    pub fn all_objects<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyDict>>> {
        let mut results = Vec::with_capacity(self.inner.len());
        for obj in self.inner.entries.values() {
            results.push(object_entry_to_pydict(py, obj)?);
        }
        Ok(results)
    }

    pub fn create_pdo_mapping(&self, cob_id: u32, mapping_index: u16) -> PyPdoMapping {
        let mapping = self.inner.create_pdo_mapping(cob_id, mapping_index);
        PyPdoMapping { inner: mapping }
    }

    pub fn __len__(&self) -> usize {
        self.inner.len()
    }

    pub fn __repr__(&self) -> String {
        format!(
            "<EdsFile entries={} vendor='{}' product='{}'>",
            self.inner.len(),
            self.inner.device_info.vendor_name,
            self.inner.device_info.product_name
        )
    }
}

/// PyO3 C-Python module definition.
#[cfg(feature = "python")]
#[cfg(feature = "python")]
struct PyCallableBus {
    send_callback: PyObject,
    recv_callback: PyObject,
}

#[cfg(feature = "python")]
impl crate::simulator::CanBusWrapper for PyCallableBus {
    fn send(&self, frame: &crate::frame::CanFrame) -> bool {
        Python::with_gil(|py| {
            let py_frame = PyCanFrame {
                inner: frame.clone(),
            };
            if let Err(e) = self.send_callback.call1(py, (py_frame,)) {
                eprintln!("VirtualSimulator send callback error: {:?}", e);
                false
            } else {
                true
            }
        })
    }

    fn recv(&self, timeout_ms: u64) -> Option<crate::frame::CanFrame> {
        Python::with_gil(|py| {
            match self.recv_callback.call1(py, (timeout_ms as f64 / 1000.0,)) {
                Ok(obj) => {
                    if obj.is_none(py) {
                        None
                    } else if let Ok(py_frame) = obj.extract::<PyRef<PyCanFrame>>(py) {
                        Some(py_frame.inner.clone())
                    } else {
                        None
                    }
                }
                Err(_e) => {
                    // Usually timeouts cause errors
                    None
                }
            }
        })
    }
}

#[cfg(feature = "python")]
#[pyclass(name = "VirtualCanopenSimulator")]
pub struct PyVirtualCanopenSimulator {
    state: std::sync::Arc<std::sync::RwLock<crate::simulator::SimulatorState>>,
    thread_handle: Option<std::thread::JoinHandle<()>>,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyVirtualCanopenSimulator {
    #[new]
    pub fn new() -> Self {
        Self {
            state: std::sync::Arc::new(std::sync::RwLock::new(
                crate::simulator::SimulatorState::new(),
            )),
            thread_handle: None,
        }
    }

    pub fn start(&mut self, send_func: PyObject, recv_func: PyObject) -> PyResult<()> {
        // Guard on the thread, not on the flag. `SimulatorState::new` starts
        // `running` at true — `run_simulator_loop` reads it as "keep going", and
        // the UDP simulator hands it straight to that loop — so testing the flag
        // here read every fresh simulator as already started and never spawned
        // the thread at all.
        if self.thread_handle.is_some() {
            return Ok(());
        }
        self.state
            .write()
            .unwrap()
            .running
            .store(true, std::sync::atomic::Ordering::SeqCst);
        let state_clone = self.state.clone();

        self.thread_handle = Some(std::thread::spawn(move || {
            let bus = Box::new(PyCallableBus {
                send_callback: send_func,
                recv_callback: recv_func,
            });
            crate::simulator::run_simulator_loop(bus, state_clone);
        }));
        Ok(())
    }

    pub fn stop(&mut self, py: Python) {
        self.state
            .write()
            .unwrap()
            .running
            .store(false, std::sync::atomic::Ordering::SeqCst);
        // Drop the handle so the simulator can be started again. The join must
        // release the GIL: the simulator thread takes it on every send and recv
        // to call back into Python, so joining while holding it deadlocks.
        if let Some(handle) = self.thread_handle.take() {
            py.allow_threads(move || {
                let _ = handle.join();
            });
        }
    }
}

#[cfg(feature = "python")]
#[pymodule]
pub fn canopen_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyCanFrame>()?;
    m.add_class::<PyLatencyTracker>()?;
    m.add_class::<PyTraceRingBuffer>()?;
    m.add_class::<PyUdpCanBus>()?;
    m.add_class::<PyIsoTpReassembler>()?;
    m.add_class::<PyPdoMapping>()?;
    m.add_class::<PyNmtMaster>()?;
    m.add_class::<PyEdsFile>()?;
    m.add_class::<PyVirtualCanopenSimulator>()?;
    m.add_class::<PyFeedResult>()?;
    m.add_class::<PyPcapNgWriter>()?;
    m.add_class::<PyVcdWriter>()?;
    m.add_function(wrap_pyfunction!(decode_canopen, m)?)?;
    m.add_function(wrap_pyfunction!(decode_canopen_message, m)?)?;
    m.add_function(wrap_pyfunction!(decode_obd2, m)?)?;
    m.add_function(wrap_pyfunction!(build_sdo_read, m)?)?;
    m.add_function(wrap_pyfunction!(build_sdo_write, m)?)?;
    m.add_function(wrap_pyfunction!(build_sdo_abort, m)?)?;
    m.add_function(wrap_pyfunction!(decode_sdo, m)?)?;
    m.add_function(wrap_pyfunction!(fragment_isotp, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

#[cfg(feature = "python")]
#[pyclass(name = "PcapNgWriter")]
pub struct PyPcapNgWriter {
    // None once closed, so a second close is harmless and a write after close
    // is an error the caller can see rather than a panic.
    writer: Option<PcapNgWriter<std::fs::File>>,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyPcapNgWriter {
    /// Open a capture at `path`, either a file or a named pipe.
    ///
    /// A pipe path blocks until Wireshark connects to it, so this releases the
    /// GIL: the caller's other threads keep running while we wait.
    #[new]
    #[pyo3(signature = (path, interface_name="canopen-studio"))]
    pub fn new(py: Python<'_>, path: &str, interface_name: &str) -> PyResult<Self> {
        let file = py
            .allow_threads(|| crate::pcap::open_capture_sink(path))
            .map_err(|e| PyIOError::new_err(format!("cannot open capture {path}: {e}")))?;

        let writer = PcapNgWriter::new(file, interface_name)
            .map_err(|e| PyIOError::new_err(format!("cannot start capture {path}: {e}")))?;

        Ok(PyPcapNgWriter {
            writer: Some(writer),
        })
    }

    /// Append one frame to the capture.
    ///
    /// `dlc` only matters for a remote frame, which asks for a length it does
    /// not carry: everywhere else the payload already states it.
    #[pyo3(signature = (id, data=None, timestamp_us=None, is_extended=false, is_remote=false, is_error=false, dlc=None))]
    #[allow(clippy::too_many_arguments)]
    pub fn write_frame(
        &mut self,
        id: u32,
        data: Option<&[u8]>,
        timestamp_us: Option<u64>,
        is_extended: bool,
        is_remote: bool,
        is_error: bool,
        dlc: Option<u8>,
    ) -> PyResult<()> {
        let payload = data.unwrap_or(&[]);
        let mut frame = match timestamp_us {
            Some(ts) => CanFrame::new_with_timestamp(id, payload, ts),
            None => CanFrame::new(id, payload),
        }
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
        frame.is_extended = is_extended;
        frame.is_remote = is_remote;
        frame.is_error = is_error;
        if let Some(requested) = dlc {
            frame.dlc = requested.min(8);
        }

        let writer = self
            .writer
            .as_mut()
            .ok_or_else(|| PyIOError::new_err("capture is closed"))?;
        writer
            .write_frame(&frame)
            .map_err(|e| PyIOError::new_err(format!("cannot write frame: {e}")))
    }

    /// Close the capture. Closing twice is allowed.
    pub fn close(&mut self) -> PyResult<()> {
        if let Some(mut writer) = self.writer.take() {
            writer
                .flush()
                .map_err(|e| PyIOError::new_err(format!("cannot flush capture: {e}")))?;
        }
        Ok(())
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    #[pyo3(signature = (_exc_type=None, _exc_value=None, _traceback=None))]
    fn __exit__(
        &mut self,
        _exc_type: Option<Bound<'_, PyAny>>,
        _exc_value: Option<Bound<'_, PyAny>>,
        _traceback: Option<Bound<'_, PyAny>>,
    ) -> PyResult<bool> {
        self.close()?;
        Ok(false)
    }
}

#[cfg(feature = "python")]
#[pyclass(name = "VcdWriter")]
pub struct PyVcdWriter {
    // None once closed, so a second close is harmless and a write afterwards
    // is an error the caller can see rather than a panic.
    writer: Option<VcdWriter<std::fs::File>>,
    frames_displaced: u64,
}

#[cfg(feature = "python")]
#[pymethods]
impl PyVcdWriter {
    /// Open a reconstructed waveform at `path`.
    ///
    /// `timing` is "timestamps" (frames sit where the adapter said, to its own
    /// accuracy) or "packed" (frames back to back, time axis meaningless).
    /// `ack` is "acknowledged" or "unanswered".
    #[new]
    #[pyo3(signature = (path, bitrate=500_000, tick_ns=100, timing="timestamps", ack="acknowledged"))]
    pub fn new(path: &str, bitrate: u32, tick_ns: u32, timing: &str, ack: &str) -> PyResult<Self> {
        let timing = match timing {
            "timestamps" => Timing::Timestamps,
            "packed" => Timing::Packed,
            other => {
                return Err(PyValueError::new_err(format!(
                    "timing must be 'timestamps' or 'packed', not '{other}'"
                )));
            }
        };
        let ack = match ack {
            "acknowledged" => AckSlot::Acknowledged,
            "unanswered" => AckSlot::Unanswered,
            other => {
                return Err(PyValueError::new_err(format!(
                    "ack must be 'acknowledged' or 'unanswered', not '{other}'"
                )));
            }
        };
        if tick_ns == 0 {
            return Err(PyValueError::new_err("tick_ns must be at least 1"));
        }

        let file = std::fs::File::create(path)
            .map_err(|e| PyIOError::new_err(format!("cannot open waveform {path}: {e}")))?;
        let options = VcdOptions {
            bitrate,
            tick_ns,
            timing,
            ack,
        };
        let writer = VcdWriter::new(file, options)
            .map_err(|e| PyIOError::new_err(format!("cannot start waveform {path}: {e}")))?;

        Ok(PyVcdWriter {
            writer: Some(writer),
            frames_displaced: 0,
        })
    }

    /// Append one frame, rebuilt as a waveform.
    #[pyo3(signature = (id, data=None, timestamp_us=None, is_extended=false, is_remote=false, dlc=None))]
    pub fn write_frame(
        &mut self,
        id: u32,
        data: Option<&[u8]>,
        timestamp_us: Option<u64>,
        is_extended: bool,
        is_remote: bool,
        dlc: Option<u8>,
    ) -> PyResult<()> {
        let payload = data.unwrap_or(&[]);
        let mut frame = match timestamp_us {
            Some(ts) => CanFrame::new_with_timestamp(id, payload, ts),
            None => CanFrame::new(id, payload),
        }
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
        frame.is_extended = is_extended;
        frame.is_remote = is_remote;
        if let Some(requested) = dlc {
            frame.dlc = requested.min(8);
        }

        let writer = self
            .writer
            .as_mut()
            .ok_or_else(|| PyIOError::new_err("waveform is closed"))?;
        writer
            .write_frame(&frame)
            .map_err(|e| PyIOError::new_err(format!("cannot write frame: {e}")))
    }

    /// How many frames had to be moved because the previous one was still on
    /// the wire at their reported time. Only meaningful after close().
    #[getter]
    pub fn frames_displaced(&self) -> u64 {
        self.frames_displaced
    }

    /// Close the waveform, returning (frames written, frames displaced).
    pub fn close(&mut self) -> PyResult<(u64, u64)> {
        match self.writer.take() {
            Some(writer) => {
                let (written, displaced) = writer
                    .finish()
                    .map_err(|e| PyIOError::new_err(format!("cannot finish waveform: {e}")))?;
                self.frames_displaced = displaced;
                Ok((written, displaced))
            }
            None => Ok((0, self.frames_displaced)),
        }
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    #[pyo3(signature = (_exc_type=None, _exc_value=None, _traceback=None))]
    fn __exit__(
        &mut self,
        _exc_type: Option<Bound<'_, PyAny>>,
        _exc_value: Option<Bound<'_, PyAny>>,
        _traceback: Option<Bound<'_, PyAny>>,
    ) -> PyResult<bool> {
        self.close()?;
        Ok(false)
    }
}
