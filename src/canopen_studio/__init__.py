"""
CAN & CANopen Studio — universal protocol analyzer, telemetry plotter and transmit station.

Author: Sébastien Celles
License: GNU General Public License v3.0 (GPL-3.0-or-later)
Copyright (C) 2026 Sébastien Celles
"""

# Single source of truth for the project version: pyproject.toml reads it from here through
# setuptools' dynamic metadata, and every module that displays a version imports it.
# Keep this module free of submodule imports so importing the version stays cheap.
__version__ = "0.3.0"

__all__ = ["__version__"]
