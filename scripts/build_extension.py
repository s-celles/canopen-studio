"""Build the Rust core as a Python extension and place it in the package.

The compiled `canopen_core` is not produced by `uv sync`: the build backend is
plain setuptools and the Rust workspace sits beside it. Without this step the
Python package imports nothing native, and every test that reaches ISO-TP,
the CANopen layer or the virtual simulator fails on the import alone.

Run it from the justfile (`just rust-python`) or from CI.
"""

import argparse
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = PROJECT_ROOT / "src" / "canopen_studio"


def built_library(profile: str) -> Path:
    """The shared library cargo just produced, whatever this platform names it."""
    target = PROJECT_ROOT / "target" / profile
    # Windows drops the `lib` prefix and uses .dll; Unix keeps it.
    candidates = [
        target / "canopen_core.dll",
        target / "libcanopen_core.so",
        target / "libcanopen_core.dylib",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit(f"No built extension in {target}. Looked for: " + ", ".join(c.name for c in candidates))


def extension_name() -> str:
    """What Python will import: .pyd on Windows, .so elsewhere."""
    suffix = ".pyd" if sys.platform == "win32" else sysconfig.get_config_var("EXT_SUFFIX")
    return f"canopen_core{suffix or '.so'}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        default="release",
        choices=["debug", "release"],
        help="cargo profile to build (default: release)",
    )
    args = parser.parse_args()

    cmd = ["cargo", "build", "-p", "canopen-core", "--features", "python"]
    if args.profile == "release":
        cmd.append("--release")

    # Pin PyO3 to the interpreter that will import the result. Left to itself it
    # picks the first Python on PATH, which on a CI matrix is not the one under
    # test: the 3.13 job linked against python311.lib and failed at LNK1181.
    env = dict(os.environ)
    env["PYO3_PYTHON"] = sys.executable

    if sys.platform == "darwin":
        # PyO3's extension-module feature deliberately does not link libpython:
        # the interpreter supplies those symbols when it loads the module. Only
        # the macOS linker has to be told that, otherwise it stops on undefined
        # _PyExc_* symbols. Linux and Windows need nothing here, which is why
        # this went unnoticed until a release built on macOS.
        flags = env.get("RUSTFLAGS", "")
        env["RUSTFLAGS"] = f"{flags} -C link-arg=-undefined -C link-arg=dynamic_lookup".strip()

    print("==>", " ".join(cmd))
    print("==> PYO3_PYTHON =", sys.executable)
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, env=env)
    if result.returncode != 0:
        return result.returncode

    source = built_library(args.profile)
    destination = PACKAGE / extension_name()
    shutil.copy(source, destination)
    print(f"==> Installed {source.name} as {destination.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
