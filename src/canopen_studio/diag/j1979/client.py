"""
The high-level OBD-II session: read a PID, read the trouble codes, read the VIN.

This is the layer callers actually use, and it is written once against
`DiagnosticInterface`. Whether the bytes reach the car through an ELM327 or through a
native CAN adapter running ISO-TP makes no difference to anything below.

Everything here reads. Clearing trouble codes and the UDS write services are handled by
`canopen_studio.diag.security`, which gates them; they are deliberately absent from this
class so that nothing can reach them by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..interface import DiagnosticInterface, DiagnosticResponse
from .discovery import SupportedPids, discover_supported_pids
from .dtc import DTC_MODES, TroubleCode, decode_dtc_response, sort_codes, summarise
from .pids import MODE_CURRENT_DATA, MODE_VEHICLE_INFO, PidDefinition, PidError, PidTable, PidValue
from .vin import VinError, VinInfo, extract_vin, parse_vin

# Mode 09 PID 02 carries the vehicle identification number.
PID_VIN = 0x02
PID_CALIBRATION_ID = 0x04
PID_ECU_NAME = 0x0A


@dataclass
class VehicleIdentity:
    """What a vehicle says about itself before any profile is applied."""

    vin: Optional[str] = None
    vin_info: Optional[VinInfo] = None
    ecus: List[int] = field(default_factory=list)
    ecu_names: Dict[int, str] = field(default_factory=dict)
    calibration_ids: Dict[int, str] = field(default_factory=dict)
    supported_pids: Optional[SupportedPids] = None
    obd_standard: Optional[str] = None

    @property
    def fingerprint(self) -> frozenset:
        """
        The set of supported PIDs, which identifies a vehicle when the VIN does not.

        Two cars of the same model and year implement the same PIDs, so this is what
        profile resolution falls back to on a vehicle that will not give up its VIN.
        """
        return self.supported_pids.all if self.supported_pids else frozenset()

    def as_dict(self) -> Dict[str, object]:
        """A JSON-friendly rendering, for the MCP tools and the GUI."""
        payload: Dict[str, object] = {
            "vin": self.vin,
            "ecus": [f"0x{ecu:X}" for ecu in self.ecus],
        }
        if self.vin_info is not None:
            payload["vin_details"] = self.vin_info.as_dict()
        if self.ecu_names:
            payload["ecu_names"] = {f"0x{ecu:X}": name for ecu, name in self.ecu_names.items()}
        if self.calibration_ids:
            payload["calibration_ids"] = {f"0x{ecu:X}": value for ecu, value in self.calibration_ids.items()}
        if self.obd_standard:
            payload["obd_standard"] = self.obd_standard
        if self.supported_pids is not None:
            payload["supported_pids"] = self.supported_pids.as_dict()
        return payload

    def __str__(self) -> str:
        parts = [self.vin or "VIN unavailable"]
        if self.vin_info and self.vin_info.model_year:
            parts.append(str(self.vin_info.model_year))
        parts.append(f"{len(self.ecus)} ECU(s)")
        if self.supported_pids is not None:
            parts.append(f"{len(self.supported_pids)} PID(s)")
        return ", ".join(parts)


class J1979Client:
    """
    An OBD-II session over either diagnostic backend.

    Args:
        interface: An open `DiagnosticInterface`.
        table: How to decode PIDs. Without one, readings come back as raw bytes, which
            is still useful: the PID and the ECU that answered are known either way.
        dtc_descriptions: Optional code-to-wording map, typically from a profile.
    """

    def __init__(
        self,
        interface: DiagnosticInterface,
        table: Optional[PidTable] = None,
        dtc_descriptions: Optional[Dict[str, str]] = None,
    ):
        self.interface = interface
        self.table = table if table is not None else PidTable()
        self.dtc_descriptions = dict(dtc_descriptions or {})
        self._supported: Dict[int, SupportedPids] = {}

    # -- Capabilities ------------------------------------------------------

    def supported_pids(self, mode: int = MODE_CURRENT_DATA, refresh: bool = False) -> SupportedPids:
        """
        Which PIDs of a mode the vehicle implements, discovered from its bitmasks.

        The result is cached: a vehicle's capabilities do not change while the ignition
        is on, and the walk costs several round trips.

        Args:
            mode: The service to enumerate.
            refresh: Discard the cached answer and ask again.
        """
        if refresh or mode not in self._supported:
            self._supported[mode] = discover_supported_pids(self.interface, mode)
        return self._supported[mode]

    def describes(self, pid: int, mode: int = MODE_CURRENT_DATA) -> Optional[PidDefinition]:
        """The definition for a PID, or None when the active table does not describe it."""
        return self.table.get(mode, pid)

    # -- Reading -----------------------------------------------------------

    def read_pid(
        self,
        pid: int,
        mode: int = MODE_CURRENT_DATA,
        timeout: Optional[float] = None,
    ) -> List[PidValue]:
        """
        Read one PID and decode every ECU's answer.

        Args:
            pid: The parameter identifier.
            mode: The service to ask, 0x01 for live data.
            timeout: Seconds to allow the request.

        Returns:
            One reading per answering ECU, empty when the vehicle does not implement it.
            An unsupported PID is an ordinary outcome, so it does not raise.
        """
        definition = self.table.get(mode, pid) or PidDefinition(mode=mode, pid=pid, name=f"pid_{mode:02x}_{pid:02x}")
        readings = []
        for reply in self.interface.positive_responses(mode, pid, timeout=timeout):
            data = self._payload_after_echo(reply, pid)
            if data is None:
                continue
            try:
                readings.append(definition.decode(data, source=reply.source))
            except PidError:
                # A response too short for its declared decoding. Report it as raw bytes
                # rather than dropping the fact that the ECU answered at all.
                raw = PidDefinition(mode=mode, pid=pid, name=definition.name, description=definition.description)
                readings.append(raw.decode(data, source=reply.source))
        return readings

    def read(self, name: str, timeout: Optional[float] = None) -> Optional[PidValue]:
        """
        Read a PID by its name, e.g. `engine_speed`.

        Args:
            name: The name a definition carries in the active table.
            timeout: Seconds to allow the request.

        Returns:
            The first ECU's reading, or None when the name is unknown or nothing
            answered.
        """
        definition = self.table.by_name(name)
        if definition is None:
            return None
        readings = self.read_pid(definition.pid, definition.mode, timeout=timeout)
        return readings[0] if readings else None

    def scan(self, mode: int = MODE_CURRENT_DATA, timeout: Optional[float] = None) -> List[PidValue]:
        """
        Read every PID the vehicle says it supports, once.

        The bitmask PIDs themselves are skipped: they describe the vehicle's
        capabilities rather than its state, and have already been read by discovery.

        Args:
            mode: The service to sweep.
            timeout: Seconds to allow each request.
        """
        supported = self.supported_pids(mode)
        readings = []
        for pid in supported:
            if pid % 0x20 == 0:
                continue
            readings.extend(self.read_pid(pid, mode, timeout=timeout))
        return readings

    # -- Trouble codes -----------------------------------------------------

    def read_dtcs(self, kind: str = "stored", timeout: Optional[float] = None) -> List[TroubleCode]:
        """
        Read the diagnostic trouble codes of one kind.

        Args:
            kind: `stored` for confirmed faults, `pending` for ones awaiting a second
                drive cycle, `permanent` for the ones only the vehicle may clear.
            timeout: Seconds to allow the request.
        """
        if kind not in DTC_MODES:
            raise ValueError(f"unknown trouble code kind {kind!r}; expected one of {', '.join(DTC_MODES)}")
        mode = DTC_MODES[kind]
        codes: List[TroubleCode] = []
        for reply in self.interface.positive_responses(mode, timeout=timeout):
            codes.extend(
                decode_dtc_response(
                    reply.payload,
                    kind=kind,
                    source=reply.source,
                    descriptions=self.dtc_descriptions,
                )
            )
        return list(sort_codes(codes))

    def read_all_dtcs(self, timeout: Optional[float] = None) -> List[TroubleCode]:
        """Read stored, pending and permanent codes in one pass."""
        codes: List[TroubleCode] = []
        for kind in DTC_MODES:
            codes.extend(self.read_dtcs(kind, timeout=timeout))
        return codes

    def dtc_summary(self, timeout: Optional[float] = None) -> Dict[str, object]:
        """Counts of every kind of trouble code, for a status line."""
        return summarise(self.read_all_dtcs(timeout=timeout))

    # -- Identity ----------------------------------------------------------

    def read_vin(self, timeout: Optional[float] = None) -> Optional[str]:
        """
        Read the vehicle identification number, via mode 09 PID 02.

        Returns:
            The seventeen characters, or None when no ECU gave a usable one. A partial
            reassembly comes back as None rather than as a short VIN nobody notices.
        """
        for reply in self.interface.positive_responses(MODE_VEHICLE_INFO, PID_VIN, timeout=timeout):
            vin = extract_vin(reply.payload)
            if vin:
                return vin
        return None

    def read_text_info(self, pid: int, timeout: Optional[float] = None) -> Dict[int, str]:
        """
        Read a textual mode 09 parameter from every ECU that has one.

        Args:
            pid: The vehicle information PID, e.g. 0x0A for the ECU name.
            timeout: Seconds to allow the request.
        """
        results = {}
        for reply in self.interface.positive_responses(MODE_VEHICLE_INFO, pid, timeout=timeout):
            data = self._payload_after_echo(reply, pid)
            if data is None:
                continue
            text = "".join(chr(byte) for byte in data if 0x20 <= byte < 0x7F).strip()
            if text:
                results[reply.source] = text
        return results

    def identify(self, timeout: Optional[float] = None) -> VehicleIdentity:
        """
        Gather everything the vehicle will say about itself.

        This is the input to profile resolution: the VIN when there is one, the
        supported-PID fingerprint when there is not, plus the ECU names and calibration
        identifiers that distinguish two cars sharing both.

        Args:
            timeout: Seconds to allow each request.
        """
        supported = self.supported_pids(MODE_CURRENT_DATA)
        vin = self.read_vin(timeout=timeout)

        vin_info: Optional[VinInfo] = None
        if vin:
            try:
                vin_info = parse_vin(vin)
            except VinError:
                # A VIN that survived extraction but not parsing is worth keeping as
                # text; it is still an identifier, just not a decodable one.
                vin_info = None

        obd_standard = None
        standard_readings = self.read_pid(0x1C, MODE_CURRENT_DATA, timeout=timeout) if 0x1C in supported else []
        if standard_readings:
            obd_standard = str(standard_readings[0].value)

        return VehicleIdentity(
            vin=vin,
            vin_info=vin_info,
            ecus=list(supported.ecus),
            ecu_names=self.read_text_info(PID_ECU_NAME, timeout=timeout),
            calibration_ids=self.read_text_info(PID_CALIBRATION_ID, timeout=timeout),
            supported_pids=supported,
            obd_standard=obd_standard,
        )

    # -- Internals ---------------------------------------------------------

    @staticmethod
    def _payload_after_echo(reply: DiagnosticResponse, pid: int) -> Optional[bytes]:
        """
        Strip the PID echo, refusing a reply that echoes a different one.

        On a shared bus a late answer to an earlier request can still be in flight, and
        decoding it under this PID's definition would produce a plausible wrong reading.
        """
        payload = reply.payload
        if not payload or payload[0] != pid:
            return None
        return payload[1:]
