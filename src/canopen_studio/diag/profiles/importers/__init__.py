"""
Bringing PID definitions in from the formats people already have.

Torque Pro CSV exports and DBC databases both describe signals, but neither is a vehicle
profile, so importing is a translation with judgement in it. The rule both importers
share: a definition that would decode to a plausible wrong number is skipped with a
reason rather than imported, because nothing downstream can tell a wrong reading from a
right one.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from .torque_csv import ImportReport, import_torque_csv, translate_equation

__all__ = ["ImportReport", "import_torque_csv", "translate_equation"]


def __getattr__(name: str):
    """
    Expose the DBC importer lazily, so the package imports without cantools installed.

    cantools is an optional dependency, and importing it eagerly here would make the
    whole diagnostics package unusable for anyone who does not need DBC support.
    """
    if name in ("import_dbc", "is_diagnostic_response_id", "signal_formula"):
        from . import dbc

        return getattr(dbc, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
