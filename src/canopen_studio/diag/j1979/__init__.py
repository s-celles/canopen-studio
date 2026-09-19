"""
SAE J1979 (OBD-II) application layer.

Modes 01 through 0A, supported-PID discovery by bitmask, ISO 15031-6 trouble codes and
vehicle information, all decoded from declarative definitions rather than hard-coded
tables, over either diagnostic interface.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

from .formula import Formula, FormulaError

__all__ = ["Formula", "FormulaError"]
