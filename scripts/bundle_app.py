"""Build the deployable archive of the Python studio and publish it.

The studio is offered through Simple Apps Deployment as a `kind: python`
application: the platform's interpreter imports `canopen_studio.gui:main`, so
what travels is the package and whatever it needs that the platform does not
already have.

    python scripts/bundle_app.py --catalog \\\\server\\share\\bin\\apps

Nothing here is specific to a machine or a share: the catalogue is given on
the command line, and the metadata comes from the project.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The minimum platform this archive may be installed on. 1.8.0 is the release
# that promised numpy, pandas and matplotlib to the applications; on anything
# older this archive arrives without matplotlib and dies on its first import.
MINIMUM_PLATFORM = "1.8.0"

# What the archive carries, which is everything the studio imports **except**
# what the platform provides. The platform's own README lists those, as a
# promise rather than an accident: marimo, numpy, pandas, matplotlib.
#
# matplotlib alone accounted for 35 of the 38 MB this archive used to weigh,
# once per installation and again at every update, over the share.
#
# The trade, also written down there: relying on the platform's libraries ties
# the application to the interpreter the platform ships. It will not start on
# one the user picked with `--use-python` unless that one has matplotlib too.
CARRIED = [
    "canopen>=2.4.1",
    "pyserial>=3.5",
    "python-can>=4.4.0",
    "msgpack>=1.0.8",
    "pyyaml>=6.0",
]

# Everything else the project declares — the agent servers, the DBC importer,
# the test tools — stays out: `--no-deps` installs the package alone, and
# `CARRIED` says what goes with it.


def version() -> str:
    for line in (PROJECT_ROOT / "src" / "canopen_studio" / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split('"')[1]
    raise SystemExit("No __version__ in src/canopen_studio/__init__.py")


def stage(target: Path, python: str) -> None:
    """Lay out what the archive will contain."""
    for step in (
        ["uv", "pip", "install", "--target", str(target), "--python", python, *CARRIED],
        ["uv", "pip", "install", "--target", str(target), "--python", python, "--no-deps", "."],
    ):
        print("  " + " ".join(step))
        if subprocess.run(step, cwd=str(PROJECT_ROOT)).returncode != 0:
            raise SystemExit("Could not stage the archive.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, help="catalogue folder to publish into")
    parser.add_argument(
        "--python",
        default="3.13",
        help=(
            "the interpreter to resolve the wheels for — the one the platform "
            "ships, not the one running this script: a wheel is compiled for a "
            "single ABI and lands unusable otherwise"
        ),
    )
    args = parser.parse_args(argv)

    release = version()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "canopen-studio"
        stage(target, args.python)
        command = [
            sys.executable,
            "-m",
            "simple_apps_deployment.publish",
            "--catalog",
            args.catalog,
            "--key",
            "canopen-studio-python",
            "--name",
            "CAN & CANopen Studio (Python)",
            "--version",
            release,
            "--kind",
            "python",
            "--from",
            str(target),
            "--launch",
            "canopen_studio.gui:main",
            "--family",
            "canopen-studio",
            "--variant",
            "Python / Tkinter",
            "--platform",
            "windows-x86_64",
            "--authors",
            "Sébastien Celles",
            "--licence",
            "GPL-3.0-or-later",
            "--summary-fr",
            "Analyseur CAN / CANopen en Python, interface Tkinter.",
            "--summary-en",
            "CAN / CANopen analyser in Python, Tkinter interface.",
        ]
        print(f"CAN & CANopen Studio (Python) {release} — needs platform {MINIMUM_PLATFORM}")
        return subprocess.run(command).returncode


if __name__ == "__main__":
    raise SystemExit(main())
