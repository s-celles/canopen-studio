"""The Python package and the Rust workspace ship as one product.

`canopen_studio.__version__` is the source of truth: the release workflow reads
it to version the Windows installer. The Cargo workspace had drifted to 0.2.2
against 0.5.0 on the Python side, which surfaced to users as two different
version numbers in the same application. These tests keep them in step.
"""

import re
from pathlib import Path

import pytest

import canopen_studio

REPO_ROOT = Path(__file__).resolve().parent.parent


def _workspace_version() -> str:
    """Read `version` from the `[workspace.package]` table of the root Cargo.toml.

    Parsed by hand rather than with tomllib, which is only stdlib from 3.11 and
    this project supports 3.10.
    """
    text = (REPO_ROOT / "Cargo.toml").read_text(encoding="utf-8")
    table = re.search(r"^\[workspace\.package\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    assert table, "Cargo.toml has no [workspace.package] table"
    version = re.search(r'^\s*version\s*=\s*"([^"]+)"', table.group(1), re.M)
    assert version, "[workspace.package] declares no version"
    return version.group(1)


def test_the_rust_workspace_carries_the_python_version():
    assert _workspace_version() == canopen_studio.__version__, (
        "Cargo.toml [workspace.package] version and canopen_studio.__version__ have drifted; they ship as one product"
    )


@pytest.mark.parametrize(
    "manifest",
    sorted(p.relative_to(REPO_ROOT) for p in REPO_ROOT.glob("crates/*/Cargo.toml")),
)
def test_every_crate_inherits_the_workspace_version(manifest):
    text = (REPO_ROOT / manifest).read_text(encoding="utf-8")
    assert "version.workspace = true" in text, (
        f"{manifest} should inherit its version from the workspace rather than pinning one of its own"
    )


def test_no_crate_pins_a_sibling_by_a_literal_version():
    """A hardcoded version on a path dependency is one more place to forget."""
    offenders = []
    for manifest in sorted(REPO_ROOT.glob("crates/*/Cargo.toml")):
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if "path = " in line and re.search(r'version\s*=\s*"\d', line):
                offenders.append(f"{manifest.relative_to(REPO_ROOT)}: {line.strip()}")
    assert not offenders, "path dependencies should not carry a literal version: " + "; ".join(offenders)


def test_the_version_is_a_plain_semver_triple():
    assert re.fullmatch(r"\d+\.\d+\.\d+", canopen_studio.__version__), (
        f"unexpected version format: {canopen_studio.__version__!r}"
    )
