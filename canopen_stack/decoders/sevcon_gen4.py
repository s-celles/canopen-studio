"""
Application-specific decoder extension for SEVCON Gen4 AC/PMSM motor controller.
"""

from typing import Optional, Dict, Any, List
from ..base_decoder import BaseDeviceDecoder
from ..types import CanopenMessage, NmtState
from ..registry import register_decoder


@register_decoder
class SevconGen4Decoder(BaseDeviceDecoder):
    """
    Decoder extension tailored specifically for SEVCON Gen4 motor inverters.
    Maps custom TPDO COB-IDs, extracts motor speed, temperatures, target torque,
    and monitors device state.
    """

    @property
    def device_name(self) -> str:
        return "SEVCON Gen4 Inverter"

    @property
    def supported_node_ids(self) -> List[int]:
        return [1]  # Sevcon Gen4 defaults to Node 1 on the kart

    @property
    def custom_cob_ids(self) -> Dict[int, str]:
        return {
            0x148: "SEVCON TPDO1 (Inverter & Status Monitor)",
            0x441: "SEVCON TPDO2 (Motor Monitor)",
            0x156: "SEVCON TPDO3 (Temps & Voltages)",
            0x270: "SEVCON TPDO4 (Torque & Speed Limit)",
            0x473: "SEVCON TPDO5 (Actual Speed 0x606C & Max Speed)",
        }

    def decode(self, message: CanopenMessage) -> Optional[Dict[str, Any]]:
        cid = message.arbitration_id
        data = message.data
        signals: Dict[str, Any] = {}

        # 1. Heartbeat
        if cid == 0x701 and data:
            st = NmtState.from_byte(data[0])
            signals["sevcon_nmt_state"] = str(st)
            message.decoded_info = f"SEVCON Gen4 (Node 1) Heartbeat -> State: {st}"
            return signals

        # 2. TPDO1 (0x148): Status and Inverter Monitor (0x4600:05..08)
        if cid == 0x148 and len(data) >= 8:
            v1 = int.from_bytes(data[0:2], "little", signed=True)
            v2 = int.from_bytes(data[2:4], "little", signed=True)
            v3 = int.from_bytes(data[4:6], "little", signed=True)
            v4 = int.from_bytes(data[6:8], "little", signed=True)
            signals["inverter_status_v1"] = v1
            signals["inverter_status_v2"] = v2
            signals["inverter_status_v3"] = v3
            signals["inverter_status_v4"] = v4
            message.decoded_info = f"SEVCON TPDO1 [Monitor] -> v1:{v1}, v2:{v2}, v3:{v3}, v4:{v4}"
            return signals

        # 3. TPDO2 (0x441): Motor Monitor (0x4600:09..0B, 0x4602:1D)
        if cid == 0x441 and len(data) >= 8:
            m1 = int.from_bytes(data[0:2], "little", signed=True)
            m2 = int.from_bytes(data[2:4], "little", signed=True)
            m3 = int.from_bytes(data[4:6], "little", signed=True)
            m4 = int.from_bytes(data[6:8], "little", signed=True)
            signals["motor_monitor_m1"] = m1
            signals["motor_monitor_m2"] = m2
            signals["motor_monitor_m3"] = m3
            signals["motor_monitor_m4"] = m4
            message.decoded_info = f"SEVCON TPDO2 [Motor Monitor] -> {m1}, {m2}, {m3}, {m4}"
            return signals

        # 4. TPDO3 (0x156): Temperatures and Voltages (0x4602:1F..21, 0x4600:03)
        if cid == 0x156 and len(data) >= 8:
            t_heatsink = int.from_bytes(data[6:8], "little", signed=True)
            t_motor_raw = int.from_bytes(data[2:4], "little", signed=True)
            aux_val = int.from_bytes(data[4:6], "little", signed=True)
            signals["heatsink_temp_c"] = t_heatsink
            signals["motor_temp_raw"] = t_motor_raw
            signals["aux_voltage"] = aux_val
            message.decoded_info = f"SEVCON TPDO3 [Temps] -> Heatsink: {t_heatsink}°C, MotorRaw: {t_motor_raw}, Aux: {aux_val}"
            return signals

        # 5. TPDO4 (0x270): Target Torque and Speed Limiter (0x5100:02..04, 0x6071:00)
        if cid == 0x270 and len(data) >= 7:
            mode = data[0]
            spd_lim = data[1]
            torque = int.from_bytes(data[5:7], "little", signed=True)
            signals["control_mode"] = mode
            signals["speed_limit_step"] = spd_lim
            signals["target_torque"] = torque
            message.decoded_info = f"SEVCON TPDO4 [Torque] -> TargetTorque(0x6071): {torque} | SpeedStep: {spd_lim} | Mode: {mode}"
            return signals

        # 6. TPDO5 (0x473): Motor Speed RPM (0x606C:00) & Max Speed (0x6080:00)
        if cid == 0x473 and len(data) >= 8:
            max_rpm = int.from_bytes(data[0:4], "little", signed=True)
            rpm = int.from_bytes(data[4:8], "little", signed=True)
            signals["max_speed_rpm"] = max_rpm
            signals["actual_speed_rpm"] = rpm
            message.decoded_info = f"SEVCON TPDO5 [Speed] -> Actual Speed: {rpm} RPM | Max Speed: {max_rpm} RPM"
            return signals

        return None
