"""First contact with a Bluetooth LE ELM327 adapter, in stages that cannot harm a vehicle.

Stage 1, always: find the adapter, open the BLE link and talk to the ELM327 chip with AT
commands. The chip answers those itself; nothing reaches the vehicle's bus, so this runs
safely with the ignition off.

Stage 2, with --vehicle: identify the vehicle (supported PIDs, VIN) and count its trouble
codes. Every request is an OBD-II read. Clearing codes is not offered here at all, and
the diagnostic package refuses it anyway unless CANOPEN_STUDIO_DIAG_WRITE is set.

Run it from the justfile (`just obd-ble-check`, `just obd-ble-check --vehicle`).
"""

import argparse
import sys

from canopen_studio.diag import DiagnosticError, TransportError
from canopen_studio.diag.elm327 import BleElmTransport, ElmDiagnosticInterface, scan_ble_adapters
from canopen_studio.diag.elm327.protocol import ElmProtocol

# Answered by the ELM327 chip itself, never forwarded to the vehicle. AT@1 and ATRV are
# missing from some clones, which is worth knowing but not a failure.
ADAPTER_COMMANDS = (
    ("ATZ", "reset"),
    ("ATE0", "echo off"),
    ("ATI", "chip version"),
    ("AT@1", "device description"),
    ("ATRV", "battery voltage at the socket"),
)


def check_adapter(transport, timeout: float = 3.0) -> bool:
    """Stage 1. Returns whether the chip answered the commands that matter."""
    elm = ElmProtocol(transport, timeout=timeout)
    answered = {}
    for command, meaning in ADAPTER_COMMANDS:
        reply = elm.send(command)
        text = reply.value if reply.ok else f"(not supported: {reply.status})"
        answered[command] = reply.ok
        print(f"  {command:<5} {meaning:<30} {text}")
    # A clone may lack AT@1 or ATRV; without ATI the link is not carrying the dialogue.
    return answered["ATI"]


def check_vehicle(transport, protocol: str = "0") -> None:
    """Stage 2. Read-only requests to the vehicle."""
    with ElmDiagnosticInterface(transport, protocol=protocol) as link:
        obd = link.j1979()
        identity = obd.identify()
        for key, value in identity.as_dict().items():
            print(f"  {key}: {value}")
        print(f"  trouble codes: {obd.dtc_summary()}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--device", help="address or fragment of the advertised name; default: first adapter found")
    parser.add_argument("--vehicle", action="store_true", help="also run stage 2: read-only requests to the vehicle")
    parser.add_argument("--protocol", default="0", help="ELM327 protocol number for stage 2, 0 to autodetect")
    parser.add_argument("--scan-timeout", type=float, default=10.0, help="seconds to look for the adapter")
    args = parser.parse_args(argv)

    try:
        print("Scanning for BLE adapters that look like OBD dongles…")
        for address, name in scan_ble_adapters(timeout=min(args.scan_timeout, 5.0)):
            print(f"  {address}  {name}")

        transport = BleElmTransport(args.device, scan_timeout=args.scan_timeout)
        print("\nStage 1 — the adapter alone (nothing is sent to the vehicle)")
        transport.open()
        try:
            print(f"  link: {transport.description}, layout {transport.layout}")
            if not check_adapter(transport):
                print("\nThe link is up but the chip did not answer ATI: not an ELM327 dialogue.")
                return 1

            if not args.vehicle:
                print("\nStage 1 passed. With the ignition on and the engine off, run again with --vehicle.")
                return 0

            print("\nStage 2 — read-only requests to the vehicle")
            check_vehicle(transport, args.protocol)
        finally:
            transport.close()
    except TransportError as exc:
        # stdout is buffered when piped, as under `just`; without this the error lands first.
        sys.stdout.flush()
        print(f"\nBLE link failed: {exc}", file=sys.stderr)
        print("Close any phone app connected to the dongle, and check it is powered.", file=sys.stderr)
        return 2
    except DiagnosticError as exc:
        sys.stdout.flush()
        print(f"\nThe adapter answered but the vehicle did not: {exc}", file=sys.stderr)
        print("Is the ignition on? Try forcing the protocol, e.g. --protocol 6.", file=sys.stderr)
        return 3

    print("\nStage 2 passed. The studio can reach this vehicle over Bluetooth LE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
