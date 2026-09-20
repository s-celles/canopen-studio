"""
The project version must be declared once and read from that single place everywhere.
"""

import re
from pathlib import Path

import canopen_studio
from canopen_studio.updater import CURRENT_VERSION

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_a_semantic_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+", canopen_studio.__version__)


def _project_table() -> str:
    """The body of pyproject's `[project]` table.

    Read by hand rather than with tomllib, which is stdlib only from 3.11
    while this project supports 3.10.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    table = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    assert table, "pyproject.toml has no [project] table"
    return table.group(1)


def test_packaging_metadata_reads_the_package_version():
    """pyproject must derive the version instead of repeating it."""
    project = _project_table()

    assert re.search(r'^\s*dynamic\s*=\s*\[[^\]]*"version"', project, re.M), (
        "[project] should declare version as dynamic"
    )
    assert not re.search(r"^\s*version\s*=", project, re.M), "[project] should not carry a literal version"


def test_updater_reports_the_package_version():
    assert CURRENT_VERSION == canopen_studio.__version__


def test_no_module_hardcodes_a_version_string():
    """A literal version assigned anywhere in the sources would drift on the next release."""
    # A version being assigned: "version" on the line, and a dotted number in a literal
    # introduced by = or :. Narrow enough to ignore IP addresses and prose examples.
    assignment = re.compile(r"""[=:]\s*f?["'][^"']*\d+\.\d+\.\d+""")
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        if path.name == "__init__.py" and path.parent.name == "canopen_studio":
            continue  # the single source of truth
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "version" in line.lower() and assignment.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert offenders == [], "hardcoded versions:\n" + "\n".join(offenders)
