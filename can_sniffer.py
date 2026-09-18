"""
CAN / CANopen sniffer and analyzer for SEVCON Gen4 motor controller.
Uses the modular canopen_stack abstraction layer and extensible decoder registry.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

import sys
import time
import argparse
import csv
from typing import Optional

import serial.tools.list_ports
import can

from canopen_stack import (
    CANopenLayer,
    NmtState,
    get_default_registry,
)
from can_interfaces import (
    SUPPORTED_INTERFACES,
    STANDARD_BITRATES,
    list_com_ports,
    find_canusb_port,
    open_can_bus,
    VirtualCanopenSimulator,
)

# SLCAN bitrates supported by LAWICEL CANUSB
SLCAN_BITRATES = {b: f"S{i}" for i, b in enumerate(STANDARD_BITRATES)}


def run_sniffer(
    interface: str,
    channel: str,
    bitrate: int,
    listen_only: bool,
    extended_only: bool,
    standard_only: bool,
    filter_id: Optional[int],
    log_file: Optional[str],
    max_count: Optional[int] = None,
    max_duration: Optional[float] = None,
    profile: str = "All / Auto",
    simulate: bool = False,
):
    """Main reception and display loop using the CANopen protocol layer."""
    reg = get_default_registry()
    reg.active_profile = profile
    decoders_list = [d.device_name for d in reg.decoders]

    print("=" * 80)
    print("   CAN & CANopen Studio - Sniffer & Protocol Analyzer")
    print("=" * 80)
    print(f" Interface       : {SUPPORTED_INTERFACES.get(interface, {}).get('name', interface)}")
    print(f" Channel         : {channel}")
    print(f" Bitrate         : {bitrate / 1000:g} kbit/s")
    print(f" Mode            : {'Listen-Only (Passive, no ACKs)' if listen_only else 'Normal (Active / ACKs)'}")
    print(f" Profile         : {profile}")
    print(f" Active Decoders : {', '.join(decoders_list)}")
    if filter_id is not None:
        print(f" ID Filter       : 0x{filter_id:X}")
    if log_file:
        print(f" Logging to CSV  : {log_file}")
    if max_count:
        print(f" Max Frame Count : {max_count}")
    if max_duration:
        print(f" Max Duration    : {max_duration} seconds")
    print("=" * 80)
    print(f"{'Time (s)':<10} | {'Type':<4} | {'ID (Hex)':<10} | {'DLC':<3} | {'Data (Hex)':<24} | {'CANopen / Application Decode'}")
    print("-" * 110)

    f_csv = None
    csv_writer = None
    if log_file:
        f_csv = open(log_file, "w", newline="", encoding="utf-8")
        csv_writer = csv.writer(f_csv)
        csv_writer.writerow(["Timestamp", "Format", "CAN_ID", "RTR", "DLC", "Data_Hex", "Decode_Info", "Signals"])

    bus = None
    simulator = None
    msg_count = 0
    displayed_count = 0

    try:
        try:
            bus = open_can_bus(interface, channel, bitrate)
        except PermissionError:
            print(f"\n[!] ERROR: Port '{channel}' is already in use by another program.")
            print("[!] Please close any open terminals, monitors, or GUI instances and retry.")
            sys.exit(1)
        except Exception as e:
            print(f"\n[!] ERROR: Could not open CAN interface ({interface} on {channel}): {e}")
            sys.exit(1)

        if interface == "virtual" or simulate:
            simulator = VirtualCanopenSimulator(bus)
            simulator.start()
            print("[*] Virtual CANopen simulation node started.")

        # Initialize the CANopen abstraction layer on top of the CAN bus
        canopen_layer = CANopenLayer(bus=bus, registry=reg)

        start_time = time.time()

        while True:
            if max_duration is not None and (time.time() - start_time) >= max_duration:
                print(f"\n[+] Target duration reached ({max_duration}s).")
                break

            raw_msg = bus.recv(timeout=0.5)
            if raw_msg is None:
                continue

            msg_count += 1
            now = raw_msg.timestamp if raw_msg.timestamp else time.time()
            elapsed = now - start_time

            # Filter Standard / Extended
            if extended_only and not raw_msg.is_extended_id:
                continue
            if standard_only and raw_msg.is_extended_id:
                continue

            # Filter specific CAN ID
            if filter_id is not None and raw_msg.arbitration_id != filter_id:
                continue

            displayed_count += 1

            # Dispatch through CANopen layer and application decoders
            parsed = canopen_layer.process_can_message(raw_msg)

            typ = "EXT" if raw_msg.is_extended_id else "STD"
            cid = f"0x{raw_msg.arbitration_id:08X}" if raw_msg.is_extended_id else f"0x{raw_msg.arbitration_id:03X}"
            dlc = str(raw_msg.dlc)
            dhex = raw_msg.data.hex(" ").upper() if raw_msg.data else ""
            rtr = "RTR" if raw_msg.is_remote_frame else ""

            print(f"{elapsed:9.3f}s | {typ:<4} | {cid:<10} | {dlc:<3} | {dhex:<24} | {parsed.decoded_info}")

            if csv_writer:
                sig_str = "; ".join(f"{k}={v}" for k, v in parsed.signals.items())
                csv_writer.writerow([f"{elapsed:.6f}", typ, cid, rtr, dlc, dhex, parsed.decoded_info, sig_str])
                f_csv.flush()

            if max_count is not None and displayed_count >= max_count:
                print(f"\n[+] Target count reached ({max_count} frames).")
                break

    except KeyboardInterrupt:
        print("\n[!] Execution stopped by user (Ctrl+C).")
    finally:
        if simulator:
            simulator.stop()
        if bus:
            bus.shutdown()
        if f_csv:
            f_csv.close()
        print(f"\n[+] Finished. Total bus frames: {msg_count} (Displayed: {displayed_count})")


def run_live_dashboard(interface: str, channel: str, bitrate: int, profile: str = "All / Auto", simulate: bool = False):
    """Interactive real-time terminal dashboard for CAN & CANopen."""
    reg = get_default_registry()
    reg.active_profile = profile

    try:
        bus = open_can_bus(interface, channel, bitrate)
    except Exception as err:
        print(f"\n[-] Error opening interface {interface} [{channel}]: {err}")
        return

    simulator = None
    if interface == "virtual" or simulate:
        simulator = VirtualCanopenSimulator(bus)
        simulator.start()

    canopen_layer = CANopenLayer(bus=bus, registry=reg)

    print("\nStarting CAN & CANopen Live Dashboard (Press Ctrl+C to exit)...")
    time.sleep(1)

    state = {
        "heartbeat": "Unknown",
        "last_hb_time": 0,
        "rpm": 0,
        "max_rpm": 0,
        "t_heatsink": 0,
        "t_motor_raw": 0,
        "target_torque": 0,
        "ext_frames_seen": 0,
        "last_ext_id": None,
        "total_msgs": 0,
    }

    try:
        last_draw = 0
        while True:
            raw_msg = bus.recv(timeout=0.05)
            if raw_msg:
                state["total_msgs"] += 1
                if raw_msg.is_extended_id:
                    state["ext_frames_seen"] += 1
                    state["last_ext_id"] = f"0x{raw_msg.arbitration_id:08X}"

                parsed = canopen_layer.process_can_message(raw_msg)

                # Extract signals populated by decoders
                if "actual_speed_rpm" in parsed.signals:
                    state["rpm"] = parsed.signals["actual_speed_rpm"]
                    state["max_rpm"] = parsed.signals.get("max_speed_rpm", state["max_rpm"])
                elif "velocity_actual" in parsed.signals:
                    state["rpm"] = parsed.signals["velocity_actual"]
                if "heatsink_temp_c" in parsed.signals:
                    state["t_heatsink"] = parsed.signals["heatsink_temp_c"]
                    state["t_motor_raw"] = parsed.signals.get("motor_temp_raw", state["t_motor_raw"])
                if "target_torque" in parsed.signals:
                    state["target_torque"] = parsed.signals["target_torque"]
                if "sevcon_nmt_state" in parsed.signals:
                    state["heartbeat"] = parsed.signals["sevcon_nmt_state"]
                    state["last_hb_time"] = time.time()

            # Refresh display at ~5 Hz
            now = time.time()
            if now - last_draw > 0.2:
                last_draw = now
                hb_status = state["heartbeat"] if (now - state["last_hb_time"] < 3.0) else "TIMEOUT (>3s)"
                sys.stdout.write("\033[2J\033[H")  # ANSI clear screen
                sys.stdout.write(
                    f"===============================================================\n"
                    f"             CAN & CANopen REAL-TIME DASHBOARD\n"
                    f"===============================================================\n"
                    f" Interface: {interface} [{channel}] | Bitrate: {bitrate/1000:g} kbps | Frames: {state['total_msgs']}\n"
                    f" Profile: {profile}  |  Nodes Active: {len(canopen_layer.node_states)}\n"
                    f"---------------------------------------------------------------\n"
                    f" Controller Status (Heartbeat) : {hb_status}\n"
                    f" Motor Actual Speed (RPM)      : {state['rpm']:6d} RPM (Max: {state['max_rpm']} RPM)\n"
                    f" Target Torque / Statusword    : {state['target_torque']:6d}\n"
                    f" Heatsink / Primary Temp       : {state['t_heatsink']:6d} °C\n"
                    f" Motor Temperature Raw         : {state['t_motor_raw']:6d}\n"
                    f"---------------------------------------------------------------\n"
                    f" Extended (29-bit) Frames Seen : {state['ext_frames_seen']} (Last ID: {state['last_ext_id']})\n"
                    f"===============================================================\n"
                    f" (Press Ctrl+C to exit dashboard)\n"
                )
                sys.stdout.flush()

    except KeyboardInterrupt:
        print("\nDashboard exited.")
    finally:
        if simulator:
            simulator.stop()
        bus.shutdown()


def main():
    parser = argparse.ArgumentParser(
        description="Universal CAN & CANopen sniffer, protocol analyzer, and telemetry monitor."
    )
    parser.add_argument(
        "--version",
        action="version",
        version="canopen-kart 0.2.0 - Copyright (C) 2026 Sébastien Celles (GPL-3.0-or-later)",
    )
    parser.add_argument(
        "-I", "--interface",
        type=str,
        default="slcan",
        choices=list(SUPPORTED_INTERFACES.keys()),
        help="CAN hardware interface type (slcan, pcan, kvaser, vector, ixxat, gs_usb, socketcan, virtual).",
    )
    parser.add_argument(
        "-c", "--channel", "-p", "--port",
        type=str,
        default=None,
        help="Interface channel (e.g. COM4 for slcan, PCAN_USBBUS1 for pcan, 0 for kvaser/vector/ixxat, can0 for socketcan).",
    )
    parser.add_argument(
        "-b", "--bitrate",
        type=int,
        default=500000,
        choices=STANDARD_BITRATES,
        help="CAN bus bitrate in bit/s (default: 500000 = 500 kbit/s).",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="All / Auto",
        choices=["All / Auto", "CiA 402 Generic Drive", "SEVCON Gen4 Inverter", "De Haardt Safety Transponder", "J1939 Extended (29-bit)", "Raw CAN (No Decoders)"],
        help="Active device decoder profile.",
    )
    parser.add_argument(
        "--extended-only",
        action="store_true",
        help="Display Extended (29-bit) frames ONLY.",
    )
    parser.add_argument(
        "--standard-only",
        action="store_true",
        help="Display Standard (11-bit) frames ONLY.",
    )
    parser.add_argument(
        "-i", "--id",
        type=lambda x: int(x, 0),
        default=None,
        help="Filter for a specific CAN ID in hex or dec (e.g. 0x473 or 0x18FF0101).",
    )
    parser.add_argument(
        "--listen-only",
        action="store_true",
        help="Enable passive listen-only mode (no ACK frames sent on bus).",
    )
    parser.add_argument(
        "-o", "--log",
        type=str,
        default=None,
        help="Path to save captured frames as a CSV file.",
    )
    parser.add_argument(
        "-n", "--count",
        type=int,
        default=None,
        help="Stop after capturing N frames.",
    )
    parser.add_argument(
        "-t", "--duration",
        type=float,
        default=None,
        help="Capture duration in seconds (e.g. -t 10 for 10 seconds).",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch the interactive real-time dashboard (RPM, Temps, Status).",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Start virtual simulation nodes emitting synthetic CANopen traffic.",
    )

    args = parser.parse_args()

    channel = args.channel
    if not channel:
        if args.interface == "slcan":
            print("[*] Auto-detecting CANUSB adapter (VID_0403 & PID_6001)...")
            channel = find_canusb_port()
            if channel:
                print(f"[+] Adapter detected on port: {channel}")
            else:
                ports = list_com_ports()
                if ports:
                    channel = ports[0]
                    print(f"[*] Using first available COM port: {channel}")
                else:
                    print("[-] No COM port detected. Use --channel to specify one or -I virtual for simulation.")
                    sys.exit(1)
        elif args.interface == "virtual":
            channel = "virtual_bus"
        elif args.interface in ("kvaser", "vector", "ixxat", "gs_usb"):
            channel = "0"
        elif args.interface == "pcan":
            channel = "PCAN_USBBUS1"
        elif args.interface == "socketcan":
            channel = "can0"

    if args.dashboard:
        run_live_dashboard(args.interface, channel, args.bitrate, args.profile, args.simulate)
    else:
        run_sniffer(
            interface=args.interface,
            channel=channel,
            bitrate=args.bitrate,
            listen_only=args.listen_only,
            extended_only=args.extended_only,
            standard_only=args.standard_only,
            filter_id=args.id,
            log_file=args.log,
            max_count=args.count,
            max_duration=args.duration,
            profile=args.profile,
            simulate=args.simulate,
        )


if __name__ == "__main__":
    main()
