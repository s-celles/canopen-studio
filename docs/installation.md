# Installation & Deployment Guide

This guide details all available deployment and installation workflows for **CAN & CANopen Studio**.

---

## 1. Standalone Windows Executable & Installer (.exe)

Ideal for workshop technicians, lab benches, and educational computers where Python is not installed.

### Standard Setup Wizard
1. Download **`CANopen-Studio-vX.Y.Z-Windows-Setup.exe`** from [GitHub Releases](https://github.com/s-celles/canopen-studio/releases).
2. Follow the setup wizard to install into `C:\Program Files\CANopen Studio`.
3. The wizard creates:
   - Desktop shortcut with the application icon
   - Start Menu program group with uninstaller
   - System uninstaller entry in Windows Settings / Control Panel

### Portable Zip Package
1. Download **`CANopen-Studio-vX.Y.Z-Windows-x64-Portable.zip`**.
2. Extract anywhere (e.g. on a USB drive).
3. Double-click **`CANopen-Studio.exe`** to launch immediately.

---

## 2. One-Click Local Script Installer (`install.bat`)

When developing or cloning from GitHub:
1. Clone or download the repository:
   ```bash
   git clone https://github.com/s-celles/canopen-studio.git
   cd canopen-studio
   ```
2. Double-click **`install.bat`** (or run `just install`).
3. The installer script:
   - Checks and automatically installs Astral's `uv` if missing
   - Synchronizes dependencies into `.venv`
   - Generates the high-resolution vector icon assets
   - Creates a Windows Desktop shortcut pointing to `can_gui.py` via `pythonw.exe` (no terminal window)
   - Creates a Start Menu shortcut

---

## 3. Python CLI / Package Installation

### Via `uv tool`
```bash
uv tool install git+https://github.com/s-celles/canopen-studio.git
```
Then invoke the tools directly from any terminal:
```bash
canopen-studio          # Launches the graphical studio
can-sniffer --help      # Command-line protocol sniffer
```

### Via `pip`
```bash
git clone https://github.com/s-celles/canopen-studio.git
cd canopen-studio
pip install -e .
```

---

## 4. Building from Source

To compile the standalone Windows executable yourself:
```powershell
# Build standalone directory
just build-exe

# Build single-file portable executable
just build-portable
```
Artifacts are generated in the `dist/` directory.

---

## 5. Automated CI/CD Releases

The repository includes a GitHub Actions workflow in [`.github/workflows/release.yml`](file:///C:/Users/scelles/Downloads/Huard/CANopen_kart/.github/workflows/release.yml).
Whenever a Git tag matching `v*` is pushed:
1. PyInstaller builds the standalone executable on Windows.
2. Inno Setup compiles the native Windows Setup wizard.
3. A portable `.zip` is created.
4. Assets are automatically attached to the GitHub Release.

---

## 6. In-App Updates & Maintenance

CAN & CANopen Studio includes a built-in update mechanism:
- **Background Checks**: On application launch, a non-blocking background thread checks GitHub Releases for new versions without impacting startup performance.
- **Visual Notification**: If an update is detected, a notice and an update button appear in the bottom status bar.
- **Manual Verification**: Accessible via **Help → Check for Updates...**.
- **1-Click Upgrade**:
  - For Windows Installer users: Downloads the setup executable directly and executes it to update the existing installation.
  - For Source / Git users: Offers a **Update via Git & uv** button that executes `git pull` and `uv sync` automatically.

