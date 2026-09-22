# Installation & Deployment Guide

This guide details all available deployment and installation workflows for **CAN & CANopen Studio**.

---

Released builds include the compiled `canopen_core` engine. A Git checkout does not until
you build it — see [§4](#4-building-from-source) and
[Native Engine & Front End](rust_core.md).

---

## 1. Standalone Application (no Python required)

Ideal for workshop technicians, lab benches, and educational computers where Python is not installed.
Every tagged release publishes assets for the three desktop platforms:

| Platform | Asset |
|---|---|
| Windows | `CANopen-Studio-vX.Y.Z-Windows-Setup.exe` (setup wizard) |
| Windows | `CANopen-Studio-Windows-x64-Portable.zip` (portable) |
| macOS | `CANopen-Studio-macOS-x64-Portable.tar.gz` |
| Linux | `CANopen-Studio-Linux-x64-Portable.tar.gz` |

### Windows Setup Wizard
1. Download **`CANopen-Studio-vX.Y.Z-Windows-Setup.exe`** from [GitHub Releases](https://github.com/s-celles/canopen-studio/releases).
2. Follow the setup wizard to install into `C:\Program Files\CANopen Studio`.
3. The wizard creates:
   - Desktop shortcut with the application icon
   - Start Menu program group with uninstaller
   - System uninstaller entry in Windows Settings / Control Panel

### Portable Packages
1. Download the portable archive for your platform.
2. Extract anywhere (e.g. on a USB drive).
3. Launch **`CANopen-Studio.exe`** on Windows, `CANopen-Studio.app` on macOS, or the
   `CANopen-Studio` binary on Linux.

---

## 2. One-Click Local Script Installer (`install.bat`)

When developing or cloning from GitHub:
1. Clone or download the repository:
   ```bash
   git clone https://github.com/s-celles/canopen-studio.git
   cd canopen-studio
   ```
2. Double-click **`install.bat`** (or run `just install`).
3. The installer script (`scripts/install.py`, which dispatches to the per-OS script):
   - Checks and automatically installs Astral's `uv` if missing
   - Synchronizes dependencies into `.venv`
   - Generates the high-resolution vector icon assets
   - Creates a Desktop shortcut launching the studio without a terminal window
   - Creates a Start Menu entry (Windows)

It does **not** build the Rust engine; run `just rust-python` after it if you want the
native core and the OBD-II tab.

---

## 3. Python CLI / Package Installation

### Via `uv tool`
```bash
uv tool install git+https://github.com/s-celles/canopen-studio.git

# With the MCP and A2A servers, which are an optional extra since 0.6.1
uv tool install "canopen-studio[servers] @ git+https://github.com/s-celles/canopen-studio.git"
```
Then invoke the tools directly from any terminal:
```bash
canopen-studio          # Launches the graphical studio
can-sniffer --help      # Command-line protocol sniffer
canopen-mcp             # MCP server, standalone       ] the [servers] extra
canopen-a2a             # A2A server, standalone       ]
```

!!! note "The agent servers are optional"
    `fastmcp`, `a2a-sdk` and `fastapi[standard]` together pull in `cryptography`,
    `pydantic-core`, `beartype`, `pygments` and the `google` namespace — 61 MB compressed
    against 33 MB without them. The studio runs without them and simply starts no server,
    so the extra is worth installing only when an agent will actually connect. See
    [AI Integration](ai_integration.md). A development checkout gets them from the `dev`
    group regardless.

!!! warning "No compiled engine this way"
    A wheel built from source by `uv tool` or `pip` carries the Python code only: the
    build backend is plain setuptools and the Rust workspace sits beside it. The studio
    runs, a little slower, but the [OBD-II tab](obd.md) needs `canopen_core` and will
    raise `ImportError` on first use. Use a released build, or a checkout with
    `just rust-python`.

### Via `pip`
```bash
git clone https://github.com/s-celles/canopen-studio.git
cd canopen-studio
pip install -e .                  # add [servers] for MCP/A2A, [dbc] for DBC import
```

---

## 4. Building from Source

```bash
git clone https://github.com/s-celles/canopen-studio.git
cd canopen-studio

just setup          # uv sync: Python dependencies into .venv
just rust-python    # build the canopen_core extension and place it in the package
just check          # ruff format --check, ruff check, pytest
just gui            # launch the studio
```

`just rust-python` needs a [Rust toolchain](https://rustup.rs/). Without it the studio
still runs — `UdpBus`, the CANopen layer and the simulator fall back to Python — but the
OBD-II diagnostics do not, and roughly ninety tests fail on
`ImportError: cannot import name 'canopen_core'`. That is a missing build step, not a
broken tree.

To compile the standalone executable yourself:
```powershell
# Build standalone directory
just build-exe

# Build single-file portable executable
just build-portable
```
Artifacts are generated in the `dist/` directory.

The Rust workspace builds and tests on its own:
```bash
just rust-build     # cargo build --workspace
just rust-test      # cargo test --workspace
just rust-gui       # the native Slint front end
just rust-release   # release build with its dependency list embedded, for an SBOM
just rust-audit     # check that embedded list against RustSec
```

`rust-release` uses `cargo auditable`: a stripped Rust binary names none of its crates, so
an SBOM taken from the published archive comes back empty and "no vulnerabilities" would
mean nothing. Install the tools once with `cargo install cargo-auditable cargo-audit`.

---

## 5. Automated CI/CD Releases

The repository includes a GitHub Actions workflow in
[`.github/workflows/release.yml`](https://github.com/s-celles/canopen-studio/blob/main/.github/workflows/release.yml).
Whenever a Git tag matching `v*` is pushed, on Windows, macOS and Linux in parallel:
1. A Rust toolchain is installed and `scripts/build_extension.py` compiles `canopen_core`
   into the package, so the published builds carry the native engine.
2. PyInstaller builds the standalone application.
3. Inno Setup compiles the Windows Setup wizard.
4. A portable `.zip` (Windows) or `.tar.gz` (macOS, Linux) is created.
5. Assets are automatically attached to the GitHub Release.

---

## 6. In-App Updates & Maintenance

CAN & CANopen Studio includes a built-in update mechanism:
- **Background Checks**: On application launch, a non-blocking background thread checks GitHub Releases for new versions without impacting startup performance.
- **Visual Notification**: If an update is detected, a notice and an update button appear in the bottom status bar.
- **Manual Verification**: Accessible via **Help → Check for Updates...**.
- **1-Click Upgrade**:
  - For Windows Installer users: Downloads the setup executable directly and executes it to update the existing installation.
  - For Source / Git users: Offers a **Update via Git & uv** button that executes `git pull` and `uv sync` automatically.
