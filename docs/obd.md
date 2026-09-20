# OBD-II Vehicle Diagnostics

CAN & CANopen Studio can talk to a car's diagnostic port: read live parameters, trouble
codes and the VIN over **SAE J1979**, through either an ELM327 adapter or one of the CAN
adapters the studio already supports.

---

## Why diagnostics sit beside the CAN stack, not inside it

Everywhere else in the studio, an adapter yields raw frames. The CANopen layer, the trace,
the plotter and the bridge all consume `can.Message` objects, and that is the contract
`open_can_bus()` honours.

**An ELM327 cannot honour that contract.** It is not a transparent bridge: it runs its own
protocol autodetection and its own ISO-TP handling, and answers in ASCII hexadecimal
terminated by a `>` prompt. Plugging one into `SUPPORTED_INTERFACES` would break every
consumer downstream of it.

So diagnostics have their own abstraction, `DiagnosticInterface`, which answers one
question: *given a request, which ECUs replied and what did they say.* Two implementations
sit behind it and callers cannot tell them apart.

| | `ElmDiagnosticInterface` | `NativeCanDiagnosticInterface` |
|---|---|---|
| Hardware | Any ELM327 — USB, Bluetooth SPP, Wi-Fi | SLCAN, PCAN, Kvaser, Vector, IXXAT, gs_usb, SocketCAN, virtual |
| Protocol detection | Done by the adapter | You set the bitrate and addressing |
| ISO-TP | Adapter handles flow control; the studio reassembles | Fully implemented in the studio |
| Wiring | Plugs into the OBD-II port | Needs CAN-H/CAN-L on pins 6 and 14 |
| Speed | Limited by the serial link | Full bus speed |

Both expose the same high-level operations, so everything above them is written once.

---

## Quick start

=== "GUI"

    Open the **🩺 OBD-II Diagnostics** tab, choose an adapter, and press **Connect**. The
    studio reads the VIN, discovers which parameters the vehicle implements, and resolves
    a vehicle profile — all in one step.

=== "MCP (an AI agent)"

    ```python
    obd_connect(transport="elm327", port="/dev/ttyUSB0")
    obd_list_supported_pids()
    obd_read_pid("engine_speed")
    obd_read_dtcs(kind="all")
    ```

=== "Python"

    ```python
    from canopen_studio.diag.elm327 import ElmDiagnosticInterface, SerialElmTransport
    from canopen_studio.diag.profiles import ProfileLibrary, ProfileResolver

    with ElmDiagnosticInterface(SerialElmTransport("/dev/ttyUSB0")) as link:
        obd = link.j1979()
        identity = obd.identify()

        match = ProfileResolver(ProfileLibrary().load()).resolve(identity)
        obd.table = match.profile.table

        print(identity.vin, "→", match.profile.name)
        for reading in obd.scan():
            print(reading)
    ```

---

## Connecting an adapter

### USB

The common case. An ELM327 appears as a serial port:

```python
SerialElmTransport("/dev/ttyUSB0")  # Linux
SerialElmTransport("COM4")  # Windows
```

38400 baud suits genuine chips and most clones. A board that stays silent may be wired for
9600.

### Bluetooth (classic SPP)

Supported, and no different from USB once the operating system has bound a port.

```bash
# Linux: pair, then bind a serial port to the adapter
sudo rfcomm bind 0 00:11:22:33:44:55
```

```python
SerialElmTransport("/dev/rfcomm0")
```

On Windows, pairing creates an outgoing `COMx` port; use that.

### Wi-Fi and TCP

Wi-Fi clones listen on port 35000, and so does the test emulator:

```python
TcpElmTransport("192.168.0.10", 35000)
```

!!! warning "Bluetooth Low Energy is not supported"
    BLE adapters expose a GATT service rather than a serial port, with a vendor-specific
    characteristic pair and no standard profile to bind against. Supporting them means a
    `bleak` dependency and per-vendor handling. Use a USB, a classic Bluetooth SPP, or a
    Wi-Fi adapter instead.

### Native CAN

No ELM327 at all — ISO-TP straight over an adapter the studio already drives:

```python
from canopen_studio.diag.native import NativeCanDiagnosticInterface

link = NativeCanDiagnosticInterface.open_bus("socketcan", "can0", 500000)
```

In the GUI, a native session *borrows* the connected bus rather than opening a second one.
The capture loop owns the only reader, so it hands frames to the session through a queue —
the same arrangement the CAN-to-network bridge already uses. A diagnostic session therefore
never steals frames from the trace or the plotter, and disconnecting the bus ends the
session with it.

!!! note "Only the CAN protocols are supported"
    This backend implements ISO 15765-4. If an ELM327 autodetects K-line (ISO 9141-2,
    ISO 14230-4) or J1850, the session says which protocol it found and stops, rather than
    mis-parsing a differently framed reply into plausible-looking values.

---

## Counterfeit adapters

Counterfeit ELM327 boards are the norm rather than the exception, and the usual tell is
that `ATI` reports a version the firmware does not live up to — a v1.4-era die flashed to
answer "ELM327 v2.1".

The studio reads the version but never trusts it. Each capability is **probed** by sending
the command and watching for the `?` that means "unknown command". Every probe is a no-op
that leaves the adapter in its default state.

```python
print(link.capabilities)
# ELM327 v2.1 (probable clone)

for note in link.capabilities.notes:
    print(note)
# reports v2.1 but does not implement can_receive_filter — treating it as v1.2
# no CAN receive filter: responses are filtered in software instead
```

A clone is not rejected. It is used for what it can actually do, and the session says so.

---

## Reading parameters

Which PIDs a vehicle implements is **never assumed**. The vehicle declares its own
capabilities through bitmask PIDs — `0x00` covers `0x01`–`0x20`, `0x20` covers
`0x21`–`0x40` — and the last bit of each block says whether there is another. The walk ends
where the vehicle says it ends.

```python
supported = obd.supported_pids()
print(len(supported), "parameters")
print(supported.supported_by(0x0C))  # which ECU answers engine speed
```

Support is tracked **per ECU**, because the engine controller, the transmission and a
hybrid controller each answer a functional request with their own bitmask.

```python
obd.read_pid(0x0C)  # by identifier
obd.read("engine_speed")  # by name, from the active profile
obd.scan()  # everything the vehicle supports, once
```

Two specific wrong answers are guarded against. A reply echoing a *different* PID is
discarded rather than decoded under this PID's definition — on a shared bus a late answer
to an earlier request is still in flight. And a reply too short for its formula falls back
to raw bytes, because knowing the ECU answered is worth more than a clean failure.

---

## Trouble codes

Three modes return codes in the same ISO 15031-6 format:

| Kind | Mode | Meaning |
|---|---|---|
| `stored` | 0x03 | Confirmed fault, malfunction indicator lamp commanded on |
| `pending` | 0x07 | Seen once, awaiting confirmation on a second drive cycle |
| `permanent` | 0x0A | Confirmed fault that **only the vehicle** may clear |

```python
for code in obd.read_all_dtcs():
    print(code)  # P0143 (Powertrain) — O2 sensor circuit low voltage
```

A code's letter comes from its top two bits — **P**owertrain, **C**hassis, **B**ody,
**U** for network — and the digit after it says whether the standard or the manufacturer
gives it meaning. `P1xxx` is manufacturer-specific; `P2xxx` and `P3xxx` depend on the
system, so the studio reports them as ambiguous rather than claiming either way.

---

## Vehicle identification

The VIN comes from mode 09 PID 02. It is twenty bytes, so it always arrives segmented —
which is why both backends reassemble ISO-TP before anything tries to parse it.

```python
identity = obd.identify()
print(identity.vin)  # 1HGBH41JXMN109186
print(identity.vin_info.region)  # North America
print(identity.vin_info.model_year)  # 1991
print(identity.vin_info.model_year_is_ambiguous)  # True
print(identity.vin_info.alternate_model_year)  # 2021
```

!!! info "The model year is genuinely ambiguous"
    Its code repeats on a thirty-year cycle, so `M` is both 1991 and 2021. The studio
    infers using the North American convention — a letter in position seven marks a vehicle
    of 2010 or later — and **flags the result as inferred**, offering the other candidate
    alongside. Profile resolution considers both.

A partial reassembly comes back as `None`, never as a short VIN nobody notices.

---

## Vehicle profiles

A profile is a data file that says how to decode PIDs and what trouble codes mean. It
inherits: generic J1979 describes what every compliant car has, a make adds what that
manufacturer does, a model and year add what that car does. Each layer states only its own
difference.

```yaml
id: example_make
name: Example Make
extends: j1979_base

match:
  wmi: [ZZZ]
  years: {from: 2015, to: 2025}
  required_pids: ["01:5B"]     # the vehicle must support these
  forbidden_pids: ["01:5E"]    # and must NOT support these

pids:
  "01:A0":
    name: battery_temperature
    description: Traction battery temperature
    bytes: 2
    unit: "°C"
    formula: (256 * A + B) / 10 - 40
    min: -40
    max: 120

dtcs:
  P1234: Traction battery contactor fault
```

Formulas address data bytes as `A`, `B`, `C`… — the notation of the standard's own worked
examples. Five decoding shapes are available: `formula`, `values` (an enumeration on the
first byte), `bits` (named flags addressed as `A.0`), `ascii`, and raw bytes when none is
declared.

!!! danger "A profile is data, never code"
    Formulas are **interpreted, never executed**. The expression is parsed to a syntax
    tree, every node is checked against a whitelist, and evaluation walks that tree —
    nothing is compiled and `eval` is never called. A profile cannot reach the filesystem,
    the network or the interpreter however it is written. Files are read with
    `yaml.safe_load`.

### Where profiles live

Bundled profiles ship inside the package. Your own go anywhere you point the library:

```python
library = ProfileLibrary(directories=["~/.canopen-studio/profiles"]).load()
```

A local profile reusing a bundled `id` replaces it. One bad file is recorded in
`library.errors` and skipped, rather than taking the generic fallback down with it.

### How a profile is chosen

Four stages, and the last one always succeeds:

1. **VIN** — the manufacturer identifier and the model year must both fit. A profile
   claiming another manufacturer is not a weak match, it is the wrong profile.
2. **Supported-PID fingerprint** — for a vehicle that will not give up its VIN. Two cars of
   the same model and year implement the same PIDs. A profile whose manufacturer rules
   contradict a *known* VIN is dropped here too, so a matching PID set alone cannot pick
   the wrong car.
3. **Manual selection** — offered when neither automatic stage found anything. A profile
   chosen explicitly wins over all of the above.
4. **Generic SAE J1979** — the fallback.

```python
match = ProfileResolver(library).resolve(identity)
print(match.stage)  # vin | fingerprint | manual | fallback
print(match.reasons)  # ('manufacturer 1HG', 'model year 2018')
print(match.profile.lineage)  # ('j1979_base', 'acme', 'acme_model_2018')
```

**Resolution cannot fail, on purpose.** A wrong-but-specific profile would decode a
manufacturer PID into a plausible-looking wrong number, which is worse than decoding fewer
parameters correctly.

---

## Importing definitions you already have

### Torque Pro CSV

Torque's custom-PID exports are the files people actually have, and the formats line up:
Torque equations already address bytes as `A`, `B`, `C`. Only bit extraction needs
translating, from `{A:3}` to `bit(A, 3)`.

```python
from canopen_studio.diag.profiles.importers import import_torque_csv

report = import_torque_csv("my_car_pids.csv", profile_id="my_car")
print(report)  # 42 PID(s) imported, 3 skipped
for reason in report.skipped:
    print(reason)
library.add(report.profile)
```

### DBC databases

```bash
uv pip install 'canopen-studio[dbc]'
```

A DBC describes signals *broadcast* on a bus; a PID is *requested* and answered. The two
overlap in exactly one place — a database modelling an OBD-II response as a multiplexed
message, which is how real OBD-II DBCs are written.

```python
from canopen_studio.diag.profiles.importers import import_dbc

report = import_dbc("OBD2.dbc", profile_id="from_dbc")
```

The file is read as self-describing: the service comes from the multiplexer chain above the
PID signal, and the first data byte is the one after the PID echo wherever the database
puts it. A database that models the ISO-TP length byte and one that does not both import
correctly.

An ordinary broadcast DBC imports nothing and says why — that is the honest outcome, not a
failure.

!!! tip "Both importers refuse rather than approximate"
    An equation that will not parse, a bit-packed or little-endian signal, a malformed
    PID — each is skipped with a reason. An imported PID that decodes to a plausible wrong
    number is worse than one that is missing, because nothing downstream can tell them
    apart.

---

## Safety

**Read-only is the default, and it is enforced in three independent places.**

Reading a PID asks an ECU a question. Writing to one changes a car that people drive, and
some of those changes cannot be undone from a laptop.

| Gate | What it is |
|---|---|
| Kill switch | `CANOPEN_STUDIO_DIAG_WRITE` must be set. Off by default, like `CANOPEN_STUDIO_A2A`. |
| Per-profile whitelist | A manufacturer service is allowed only if the active profile names it in `write_whitelist` — a reviewed data file, not an argument passed once. |
| Per-call confirmation | `confirm=True` for that one call. It cannot be defaulted on and never persists. |
| Agent switch | `CANOPEN_STUDIO_MCP_DIAG_WRITE`, required *in addition* for a write arriving through MCP. |

A refused write **transmits nothing**.

### The UDS write services are not implemented

`WriteDataByIdentifier` (0x2E), `RoutineControl` (0x31) and
`InputOutputControlByIdentifier` (0x2F) are refused even with all three gates open,
because this release has no request path for them. The gate exists so that adding one is a
deliberate change rather than an accident of what happens not to be written yet.

### Clearing trouble codes

Implemented, since reading and clearing codes is what a diagnostic tool is for — and still
gated.

```bash
CANOPEN_STUDIO_DIAG_WRITE=1 uv run canopen-studio
```

```python
from canopen_studio.diag import WriteGate, clear_trouble_codes

clear_trouble_codes(link, WriteGate(match.profile), confirm=True)
```

!!! warning "Clearing costs more than the codes"
    Mode 04 erases the stored codes, the freeze frame **and the readiness monitors**. The
    vehicle needs a full drive cycle to rebuild those, and an emissions test taken before
    that will fail. Permanent codes are unaffected — only the vehicle can clear them.

### Letting an agent write

`obd_clear_dtcs` is the one MCP tool that changes the vehicle. It is registered always —
so that a refusal is a message an agent can reason about rather than a missing tool — and
shut unless **both** switches are set:

```bash
CANOPEN_STUDIO_DIAG_WRITE=1 CANOPEN_STUDIO_MCP_DIAG_WRITE=1 uv run canopen-mcp
```

The two are separate on purpose. Enabling writes so that a person can clear codes from the
GUI must not, by itself, hand that capability to whatever model is connected to the server.
With only one set, the call transmits nothing and the refusal names the missing one.

Once enabled, the capability is stated everywhere it could matter:

- on the server's console at startup, so nobody discovers it by watching a model use it;
- in the tool's own description, which is what a model reads before deciding to call it;
- in the result of every trouble-code read;
- in `obd_status()`, under `writes.warning`.

```
WARNING: CANOPEN_STUDIO_MCP_DIAG_WRITE is set — an AI agent connected to this server can
clear diagnostic trouble codes on the connected vehicle. Clearing also erases the
readiness monitors, which need a full drive cycle to rebuild and without which an
emissions test fails.
```

!!! danger "What this actually permits"
    A model that decides to "reset the fault and try again" will erase the readiness
    monitors on somebody's car. On a vehicle due for an emissions inspection, that costs
    its owner the appointment. The tool's description tells the model to ask first, and
    `confirm=True` is still required on every call — but neither is a substitute for
    deciding whether you want this on at all.

    The UDS write services remain unavailable through MCP, because they remain
    unimplemented everywhere.

---

## MCP tool reference

These join the [existing MCP server](ai_integration.md) — same process, same port, same
guards.

| Tool | Purpose |
|---|---|
| `obd_connect(transport, port, …, profile)` | Open a session, identify the vehicle, resolve a profile |
| `obd_disconnect()` | Close the session |
| `obd_status()` | Link, active profile and write posture |
| `obd_list_supported_pids(mode)` | What the vehicle declares, per ECU |
| `obd_read_pid(pid, mode)` | Read by identifier, key or name |
| `obd_read_dtcs(kind)` | `stored`, `pending`, `permanent` or `all` |
| `obd_read_vin()` | The VIN, decoded |
| `obd_identify_vehicle()` | Everything the vehicle says about itself |
| `obd_list_profiles()` | Profiles available for a manual choice |
| `obd_clear_dtcs(confirm)` | **Destructive.** Clears codes and readiness monitors; needs both write switches |

---

## Testing without a vehicle

Two layers, for two different jobs.

**The in-repository fake** (`tests/elm327_fake.py`) carries the everyday suite. It speaks
the real protocol, honours the echo, linefeed and header settings, frames responses as
ISO-TP, and can be told to misbehave — report a firmware version it does not live up to,
answer a status word, truncate a reply. It is fast and needs nothing installed.

**Ircama's ELM327-emulator** is the reality check: third-party software simulating the ECUs
of a real car, driven over a real socket.

```bash
uv run --with ELM327-emulator pytest -m emulator -v
```

!!! note "Why it is not a dev dependency"
    `ELM327-emulator` is licensed **CC-BY-NC-SA-4.0** — non-commercial and non-OSI.
    Pulling it into the default dev group of a GPL project would impose that on everyone
    running the suite. Nothing is redistributed, since it runs as a separate process, so
    installing it is a choice each person makes. Without it those tests skip with that
    instruction.

The emulator also exposes an SLCAN interface, which lets the *same* emulated ECUs be
reached through the native backend:

```bash
python3 -m elm -c /dev/pts/N -s car     # emulate a CANable running SLCAN
sudo slcand -o -s6 /dev/pts/N can0 && sudo ip link set can0 up
```

That path needs `slcand` and a SocketCAN interface, so it is documented rather than
automated in the suite.
