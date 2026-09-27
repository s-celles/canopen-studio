"""
Tests for `scripts/obd_ble_check.py`, the first-contact check behind `just obd-ble-check`.

The script is loaded from its path, since `scripts/` is not a package, and run against
the fake BLE adapter the transport tests use.
"""

import importlib.util
from pathlib import Path

import pytest

from test_diag_elm_ble import FakeAdapter, install

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "obd_ble_check.py"


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location("obd_ble_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def chip(command):
    return {b"ATI": b"ELM327 v1.5", b"ATRV": b"12.4V"}.get(command, b"OK")


def test_the_adapter_stage_passes_when_the_chip_answers(check, monkeypatch, capsys):
    install(monkeypatch, FakeAdapter(answer=chip))

    assert check.main(["--scan-timeout", "0.1"]) == 0

    out = capsys.readouterr().out
    assert "ELM327 v1.5" in out
    assert "12.4V" in out


def test_the_adapter_stage_sends_nothing_but_at_commands(check, monkeypatch):
    """Stage 1 is the one run with the ignition off: it must never address the vehicle."""
    sent = []

    def record(command):
        sent.append(command)
        return chip(command)

    install(monkeypatch, FakeAdapter(answer=record))

    check.main(["--scan-timeout", "0.1"])

    assert sent and all(command.startswith(b"AT") for command in sent)


def test_a_clone_missing_optional_commands_still_passes(check, monkeypatch):
    install(monkeypatch, FakeAdapter(answer=lambda c: b"?" if c in (b"AT@1", b"ATRV") else chip(c)))

    assert check.main(["--scan-timeout", "0.1"]) == 0


def test_a_link_where_the_chip_does_not_identify_itself_fails(check, monkeypatch):
    install(monkeypatch, FakeAdapter(answer=lambda c: b"?" if c == b"ATI" else b"OK"))

    assert check.main(["--scan-timeout", "0.1"]) == 1


def test_no_adapter_in_range_is_reported_as_a_link_failure(check, monkeypatch, capsys):
    install(monkeypatch)

    assert check.main(["--scan-timeout", "0.1"]) == 2
    assert "BLE link failed" in capsys.readouterr().err
