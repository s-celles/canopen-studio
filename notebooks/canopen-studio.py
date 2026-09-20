"""CANopen Studio — marimo variant.

A notebook flavour of CANopen Studio: connect to a CAN interface, capture
frames, decode the CANopen function codes and watch the traffic per node.

It is an application distributed through a catalogue rather than shipped with
the platform: its archive carries `python-can` next to this file, which the
launcher puts on the interpreter's path.

The `virtual` interface needs no hardware, so the notebook can be used for
demonstrations or for trying things out on any machine.
"""

import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium", app_title="CANopen Studio")


@app.cell
def _():
    import time
    from collections import Counter

    import marimo as mo

    return Counter, mo, time


@app.cell
def _(mo):
    mo.md(
        """
        # CANopen Studio — notebook

        Connectez-vous à un bus CAN, capturez des trames et lisez leur
        décodage CANopen. L'interface `virtual` ne demande aucun matériel :
        elle sert aux essais et aux démonstrations.
        """
    )
    return


@app.cell
def _(mo):
    # python-can ships inside this application's archive; a clear message
    # beats an import traceback if the archive was incomplete.
    try:
        import can

        available = True
        detail = f"python-can {can.__version__}"
    except ImportError as exc:  # pragma: no cover - depends on the archive
        can = None
        available = False
        detail = str(exc)

    mo.stop(
        not available,
        mo.callout(
            f"`python-can` est introuvable : {detail}.\n\n"
            "Réinstallez l'application depuis la fenêtre **Applications**.",
            kind="danger",
        ),
    )
    mo.callout(f"Bibliothèque CAN disponible — {detail}", kind="success")
    return (can,)


@app.cell
def _(mo):
    interface = mo.ui.dropdown(
        options=["virtual", "pcan", "kvaser", "ixxat", "vector", "slcan", "socketcan"],
        value="virtual",
        label="Interface :",
    )
    channel = mo.ui.text(value="0", label="Canal :")
    bitrate = mo.ui.dropdown(
        options=["125000", "250000", "500000", "1000000"],
        value="500000",
        label="Débit (bit/s) :",
    )
    count = mo.ui.slider(10, 500, value=50, step=10, label="Trames à capturer :")
    timeout = mo.ui.slider(1, 30, value=5, step=1, label="Délai d'attente (s) :")

    mo.hstack(
        [
            mo.vstack([interface, channel, bitrate]),
            mo.vstack([count, timeout]),
        ],
        justify="start",
        gap=3,
    )
    return bitrate, channel, count, interface, timeout


@app.cell
def _(mo):
    demo_button = mo.ui.run_button(label="📤 Émettre des trames de démonstration")
    capture_button = mo.ui.run_button(label="🎧 Capturer", kind="success")
    mo.hstack([capture_button, demo_button], justify="start")
    return capture_button, demo_button


@app.cell
def _(bitrate, can, channel, interface):
    def open_bus():
        """Open the configured bus; the caller closes it."""
        kwargs = {"interface": interface.value, "channel": channel.value}
        if interface.value != "virtual":
            kwargs["bitrate"] = int(bitrate.value)
        return can.Bus(**kwargs)

    return (open_bus,)


@app.cell
def _(can, demo_button, mo, open_bus):
    mo.stop(not demo_button.value)

    # A short, realistic CANopen exchange: boot-up, heartbeat, a TPDO and an
    # emergency object, so the decoding below has something to show.
    _frames = [
        (0x701, b"\x00"),  # boot-up, node 1
        (0x181, b"\x12\x34\x56\x78"),  # TPDO1, node 1
        (0x701, b"\x05"),  # heartbeat, operational
        (0x081, b"\x10\x10\x02\x00\x00\x00\x00\x00"),  # emergency, node 1
        (0x582, b"\x43\x00\x10\x00\x92\x01\x02\x00"),  # SDO reply, node 2
    ]
    try:
        _bus = open_bus()
        try:
            for _cob_id, _data in _frames:
                _bus.send(can.Message(arbitration_id=_cob_id, data=_data, is_extended_id=False))
        finally:
            _bus.shutdown()
        _out = mo.callout(f"{len(_frames)} trames émises.", kind="success")
    except Exception as _exc:
        _out = mo.callout(f"Émission impossible : {_exc}", kind="danger")
    _out
    return


@app.cell
def _(mo):
    get_frames, set_frames = mo.state([])
    return get_frames, set_frames


@app.cell
def _(capture_button, count, mo, open_bus, set_frames, time, timeout):
    mo.stop(not capture_button.value)

    _collected = []
    _error = None
    try:
        _bus = open_bus()
        try:
            _deadline = time.monotonic() + timeout.value
            while len(_collected) < count.value and time.monotonic() < _deadline:
                _message = _bus.recv(timeout=0.2)
                if _message is not None:
                    _collected.append(
                        {
                            "t": _message.timestamp,
                            "id": _message.arbitration_id,
                            "dlc": _message.dlc,
                            "data": bytes(_message.data),
                            "extended": bool(_message.is_extended_id),
                        }
                    )
        finally:
            _bus.shutdown()
    except Exception as _exc:
        _error = str(_exc)

    set_frames(_collected)
    if _error:
        _out = mo.callout(f"Capture impossible : {_error}", kind="danger")
    elif not _collected:
        _out = mo.callout(
            "Aucune trame reçue. Vérifiez l'interface, le débit et le câblage — "
            "ou émettez des trames de démonstration sur l'interface `virtual`.",
            kind="warn",
        )
    else:
        _out = mo.callout(f"{len(_collected)} trame(s) capturée(s).", kind="success")
    _out
    return


@app.cell
def _():
    # CANopen splits an 11-bit COB-ID into a function code and a node id.
    FUNCTIONS = [
        (0x000, 0x000, "NMT"),
        (0x080, 0x080, "SYNC"),
        (0x080, 0x0FF, "EMCY"),
        (0x100, 0x100, "TIME"),
        (0x180, 0x1FF, "TPDO1"),
        (0x200, 0x27F, "RPDO1"),
        (0x280, 0x2FF, "TPDO2"),
        (0x300, 0x37F, "RPDO2"),
        (0x380, 0x3FF, "TPDO3"),
        (0x400, 0x47F, "RPDO3"),
        (0x480, 0x4FF, "TPDO4"),
        (0x500, 0x57F, "RPDO4"),
        (0x580, 0x5FF, "SDO (réponse)"),
        (0x600, 0x67F, "SDO (requête)"),
        (0x700, 0x77F, "Heartbeat / Bootup"),
    ]

    NMT_STATES = {
        0x00: "Bootup",
        0x04: "Stopped",
        0x05: "Operational",
        0x7F: "Pre-operational",
    }

    def decode(cob_id: int, data: bytes) -> tuple[str, int | None, str]:
        """Function name, node id and a short reading of the payload."""
        for low, high, name in FUNCTIONS:
            if low <= cob_id <= high:
                node = cob_id - low if high != low else None
                detail = ""
                if name.startswith("Heartbeat") and data:
                    detail = NMT_STATES.get(data[0] & 0x7F, f"état 0x{data[0]:02X}")
                elif name == "EMCY" and len(data) >= 2:
                    detail = f"code erreur 0x{int.from_bytes(data[:2], 'little'):04X}"
                elif name.startswith("SDO") and len(data) >= 4:
                    index = int.from_bytes(data[1:3], "little")
                    detail = f"index 0x{index:04X}, sous-index {data[3]}"
                return name, node, detail
        return "?", None, ""

    return (decode,)


@app.cell
def _(decode, get_frames, mo):
    _frames = get_frames()
    mo.stop(not _frames, mo.md("_Aucune trame capturée pour l'instant._"))

    _start = _frames[0]["t"]
    _rows = []
    for _frame in _frames:
        _name, _node, _detail = decode(_frame["id"], _frame["data"])
        _rows.append(
            {
                "t (ms)": round((_frame["t"] - _start) * 1000, 1),
                "COB-ID": f"0x{_frame['id']:03X}",
                "Fonction": _name,
                "Nœud": _node if _node is not None else "",
                "DLC": _frame["dlc"],
                "Données": _frame["data"].hex(" ").upper(),
                "Lecture": _detail,
            }
        )
    mo.ui.table(_rows, page_size=15, selection=None)
    return


@app.cell
def _(Counter, decode, get_frames, mo):
    _frames = get_frames()
    mo.stop(not _frames)

    _by_function = Counter(decode(f["id"], f["data"])[0] for f in _frames)
    _by_node = Counter(node for node in (decode(f["id"], f["data"])[1] for f in _frames) if node is not None)
    _span = max(f["t"] for f in _frames) - min(f["t"] for f in _frames)

    mo.hstack(
        [
            mo.vstack(
                [
                    mo.md("**Par fonction**"),
                    mo.md("\n".join(f"- {name} : {n}" for name, n in _by_function.most_common())),
                ]
            ),
            mo.vstack(
                [
                    mo.md("**Par nœud**"),
                    mo.md(
                        "\n".join(f"- nœud {node} : {n}" for node, n in sorted(_by_node.items()))
                        or "_aucun nœud identifié_"
                    ),
                ]
            ),
            mo.vstack(
                [
                    mo.md("**Débit**"),
                    mo.md(
                        f"- {len(_frames)} trames\n- sur {_span:.2f} s\n- soit {len(_frames) / _span:.0f} trames/s"
                        if _span > 0
                        else f"- {len(_frames)} trames"
                    ),
                ]
            ),
        ],
        justify="start",
        gap=3,
    )
    return


if __name__ == "__main__":
    app.run()
