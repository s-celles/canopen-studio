# Protocol Layers (OSI Model)

The studio speaks three protocol families over the same pair of wires: plain
**CAN**, **CANopen** and **OBD-II**. They are not alternatives to one another.
They share the bottom two layers entirely and differ only in what they put on
top — which is why one adapter, one capture and one decoder stack can serve all
three.

This page maps each of them onto the OSI reference model, and names the
standard that governs every box.

## The three stacks side by side

```mermaid
flowchart TB
    H1["<b>Classic CAN</b>"]
    H2["<b>CANopen</b>"]
    H3["<b>OBD-II</b>"]

    C7["<b>L7 · Application</b><br/>none<br/>an identifier means whatever<br/>its designer decided"]
    O7["<b>L7 · Application</b><br/>CiA 301<br/>NMT · SDO · PDO · SYNC<br/>EMCY · Heartbeat<br/>profiles: CiA 402…"]
    D7["<b>L7 · Application</b><br/>SAE J1979 / ISO 15031-5<br/>modes 01–0A<br/>PIDs · DTCs · VIN<br/>UDS ISO 14229 beyond it"]

    C56["<b>L5–6 · Session, Presentation</b><br/>none — CAN has no connection<br/>to manage, no representation<br/>to negotiate"]
    O56["<b>L5–6</b><br/>none"]
    D56["<b>L5–6</b><br/>none"]

    C34["<b>L3–4 · Network, Transport</b><br/>none<br/>8 bytes per frame,<br/>no routing, no segmentation"]
    O34["<b>L3–4</b><br/>SDO transfer, CiA 301<br/>segmented and block modes<br/>carry objects past 8 bytes"]
    D34["<b>L3–4</b><br/>ISO-TP, ISO 15765-2<br/>SF · FF · CF · FC<br/>up to 4095 bytes"]

    C2["<b>L2 · Data Link</b><br/>ISO 11898-1<br/>frame format · arbitration<br/>bit stuffing · CRC<br/>CAN FD since the 2015 edition"]
    O2["<b>L2</b><br/>ISO 11898-1<br/><i>unchanged</i>"]
    D2["<b>L2</b><br/>ISO 11898-1, narrowed by<br/>ISO 15765-4: 11- or 29-bit,<br/>fixed request/response IDs"]

    C1["<b>L1 · Physical</b><br/>ISO 11898-2 high-speed<br/>ISO 11898-3 fault tolerant<br/>two-wire differential, 120 Ω"]
    O1["<b>L1</b><br/>ISO 11898-2<br/><i>unchanged</i>"]
    D1["<b>L1</b><br/>ISO 11898-2, narrowed by<br/>ISO 15765-4:<br/>500 or 250 kbit/s"]

    H1 --- C7 --- C56 --- C34 --- C2 --- C1
    H2 --- O7 --- O56 --- O34 --- O2 --- O1
    H3 --- D7 --- D56 --- D34 --- D2 --- D1

    classDef head fill:#0b7285,stroke:#0b7285,color:#fff
    classDef empty fill:#f1f3f5,stroke:#adb5bd,color:#495057,font-style:italic
    classDef shared fill:#e3fafc,stroke:#0b7285
    classDef own fill:#fff4e6,stroke:#e8590c

    class H1,H2,H3 head
    class C7,C56,C34,O56,D56 empty
    class C2,C1,O2,O1,D2,D1 shared
    class O7,O34,D7,D34 own
```

## Reading the diagram

**The bottom two layers are common ground.** A CANopen drive and a car's engine
ECU put electrically indistinguishable frames on the wire. Everything the
studio does below the application layer — capture, timestamping, the trace, the
bit-level view, the PCAP-NG export — therefore works the same for all three.

**Layers 3 to 6 are mostly empty, and that is the interesting part.** A classic
CAN frame carries at most eight bytes and there is nowhere to put a longer
message, so any protocol needing more has to invent its own segmentation:
CANopen does it with segmented and block SDO transfers, OBD-II with ISO-TP.
They solve the same problem in incompatible ways, which is why reading a VIN
and reading an object dictionary entry share no code above the data link layer.

**Layer 7 is where the three part company.** Classic CAN has nothing there at
all: an identifier means whatever the designer decided, which is why the studio
ships [decoders](decoders.md) for specific devices rather than a universal one.

!!! note "CAN FD does not add a layer"
    CAN FD changes layers 1 and 2 only — a second bit rate for the data phase,
    payloads to 64 bytes, different CRCs. Everything above is unchanged, which
    is why a protocol written for classic CAN keeps working over it.

## Where an adapter cuts the stack

Which layers the adapter implements, and which are left to the studio, decides
what an adapter can be used for. This is the distinction behind the two
diagnostic backends described in [OBD-II Vehicle Diagnostics](obd.md).

```mermaid
flowchart TB
    S["<b>Native CAN adapter</b><br/>SLCAN · PCAN · SocketCAN · UDP"]
    E["<b>ELM327 adapter</b>"]

    S7["<b>L7</b> · J1979 or CANopen<br/><i>in the studio</i>"]
    S34["<b>L3–4</b> · ISO-TP or SDO<br/><i>in the studio</i>"]
    S12["<b>L1–2</b> · CAN frames<br/><i>on the adapter</i>"]

    E7["<b>L7</b> · J1979<br/><i>in the studio</i>"]
    E14["<b>L1–4</b> · physical, framing,<br/>protocol autodetection, ISO-TP<br/><i>on the adapter, in its firmware</i>"]

    S --- S7 --- S34 --- S12
    E --- E7 --- E14

    classDef head fill:#0b7285,stroke:#0b7285,color:#fff
    classDef studio fill:#e3fafc,stroke:#0b7285
    classDef adapter fill:#f1f3f5,stroke:#868e96,color:#495057

    class S,E head
    class S7,S34,E7 studio
    class S12,E14 adapter
```

A **native CAN adapter** is transparent: it hands over raw frames and the studio
owns everything from ISO-TP upwards. That is why the same adapter serves
CANopen, OBD-II and the raw trace at once.

An **ELM327** is not transparent. It runs protocol autodetection and ISO-TP
inside its own firmware and answers in ASCII hexadecimal, so it can only ever
be a diagnostic endpoint — never a source of raw frames for the trace, the
plotter or the bridge. That is the reason `DiagnosticInterface` is a separate
abstraction from the CAN adapter catalogue rather than another entry in it.

## Standards referenced here

| Standard | Layer | What it defines |
| :--- | :---: | :--- |
| ISO 11898-1 | 2 | CAN data link layer; CAN FD since the 2015 edition |
| ISO 11898-2 | 1 | High-speed CAN physical layer |
| ISO 11898-3 | 1 | Low-speed, fault-tolerant CAN physical layer |
| ISO 15765-2 | 3–4 | ISO-TP: segmentation and flow control over CAN |
| ISO 15765-4 | 1–2 | Constrains CAN for road-vehicle diagnostics |
| ISO 15031-5 / SAE J1979 | 7 | Emissions-related diagnostic services |
| ISO 14229 | 7 | UDS, manufacturer diagnostic services |
| CiA 301 | 3–4, 7 | CANopen application layer and communication profile |
| CiA 306 | — | EDS file format describing a device's object dictionary |
| CiA 402 | 7 | CANopen device profile for drives and motion control |

!!! warning "K-line and J1850 are out of scope"
    OBD-II predates CAN and permits other physical layers. The studio implements
    the CAN protocols of ISO 15765-4 only. A session that autodetects ISO 9141-2,
    ISO 14230-4 or J1850 reports which protocol it found and stops, rather than
    mis-parsing a differently framed reply.
