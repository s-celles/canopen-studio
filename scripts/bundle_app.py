"""Build the deployable archives of the studio and publish them.

Two of the three variants offered through Simple Apps Deployment are built
here; the third, the native Slint studio, comes from the release workflow.

    uv run --with git+https://github.com/s-celles/simple-apps-deployment.git \\
        scripts/bundle_app.py --catalog \\\\server\\share\\bin\\apps
    uv run --with git+... scripts/bundle_app.py --catalog ... --variant notebook

Publishing shells out to `simple_apps_deployment.publish`, so that package has
to be importable by the interpreter running this script — hence `--with`. It is
deliberately not a project dependency: the repository is private, and uv
resolves every dependency group when it locks, whichever one it is declared in,
so a single entry anywhere in `pyproject.toml` fails every CI job on every
platform before a test runs. Nothing but this script needs it.

Nothing here is specific to a machine or a share: the catalogue is given on
the command line and the metadata comes from the project.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The minimum platform these archives may be installed on. 1.8.0 is the
# release that promised numpy, pandas and matplotlib to the applications; on
# anything older the Python variant arrives without matplotlib and dies on its
# first import.
MINIMUM_PLATFORM = "1.8.0"

# What each archive carries: everything it imports **except** what the
# platform provides. The platform's own README lists those, as a promise
# rather than an accident — marimo, numpy, pandas, matplotlib.
#
# For the Python studio, matplotlib alone accounted for 35 of the 38 MB the
# archive used to weigh, sent over the share once per installation and again
# at every update.
#
# The trade, also written down there: relying on the platform's libraries ties
# an application to the interpreter the platform ships. It will not start on
# one the user picked with `--use-python` unless that one has them too.
CARRIED = {
    "python": [
        "canopen>=2.4.1",
        "pyserial>=3.5",
        "python-can>=4.4.0",
        "msgpack>=1.0.8",
        "pyyaml>=6.0",
    ],
    # The notebook opens a bus and decodes frames; it plots with marimo's own
    # elements, so it needs neither matplotlib nor numpy of its own.
    "notebook": [
        "canopen>=2.4.1",
        "python-can>=4.4.0",
    ],
}

VARIANTS = {
    "python": {
        "key": "canopen-studio-python",
        "name": "CAN & CANopen Studio (Python)",
        "kind": "python",
        "launch": "canopen_studio.gui:main",
        "variant": "Python / Tkinter",
        "summary_fr": "Analyseur CAN / CANopen en Python, interface Tkinter.",
        "summary_en": "CAN / CANopen analyser in Python, Tkinter interface.",
        # Tkinter and the compiled wheels it carries make this one Windows.
        "platform": ["windows-x86_64"],
    },
    "notebook": {
        "key": "canopen-studio-notebook",
        "name": "CAN & CANopen Studio (notebook)",
        "kind": "notebook",
        "launch": "notebook.py",
        "variant": "notebook marimo",
        "summary_fr": "Capture et décodage CANopen dans un notebook marimo.",
        "summary_en": "CANopen capture and decoding in a marimo notebook.",
        # Nothing here is tied to a system: the platform serves it anywhere.
        "platform": [],
    },
}

NOTEBOOK_SOURCE = PROJECT_ROOT / "notebooks" / "canopen-studio.py"


def version() -> str:
    text = (PROJECT_ROOT / "src" / "canopen_studio" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split('"')[1]
    raise SystemExit("No __version__ in src/canopen_studio/__init__.py")


def stage(target: Path, variant: str, python: str) -> None:
    """Lay out what the archive will contain."""
    steps = [["uv", "pip", "install", "--target", str(target), "--python", python, *CARRIED[variant]]]
    if variant == "python":
        # `--no-deps`: `CARRIED` above says what travels, so that the agent
        # servers, the DBC importer and the test tools stay out.
        steps.append(["uv", "pip", "install", "--target", str(target), "--python", python, "--no-deps", "."])
    for step in steps:
        print("  " + " ".join(step))
        if subprocess.run(step, cwd=str(PROJECT_ROOT)).returncode != 0:
            raise SystemExit("Could not stage the archive.")

    if variant == "notebook":
        # The platform starts `launch`, which is why the file is named for
        # what it is rather than for where it came from.
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(NOTEBOOK_SOURCE, target / "notebook.py")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, help="catalogue folder to publish into")
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="python")
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

    spec = VARIANTS[args.variant]
    release = version()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "canopen-studio"
        stage(target, args.variant, args.python)
        command = [
            sys.executable,
            "-m",
            "simple_apps_deployment.publish",
            "--catalog",
            args.catalog,
            "--key",
            spec["key"],
            "--name",
            spec["name"],
            "--version",
            release,
            "--kind",
            spec["kind"],
            "--from",
            str(target),
            "--launch",
            spec["launch"],
            "--family",
            "canopen-studio",
            "--variant",
            spec["variant"],
            "--authors",
            "Sébastien Celles",
            "--licence",
            "GPL-3.0-or-later",
            "--summary-fr",
            spec["summary_fr"],
            "--summary-en",
            spec["summary_en"],
        ]
        for system in spec["platform"]:
            command += ["--platform", system]
        print(f"{spec['name']} {release} — needs platform {MINIMUM_PLATFORM}")
        return subprocess.run(command).returncode


if __name__ == "__main__":
    raise SystemExit(main())
