# Justfile for Universal CAN & CANopen Studio (Analyzer & Transmit Station)


# List all available recipes
default:
    @just --list

# Synchronize environment and dependencies with pyproject.toml and uv.lock
setup:
    @echo "==> Syncing project with uv..."
    uv sync

# Run test suite with pytest
test:
    uv run pytest -v

# Check code style and linting with Ruff
lint:
    uv run ruff check .

# Automatically fix linting and formatting with Ruff
format:
    uv run ruff format .
    uv run ruff check --fix .

# Run full local quality verification (format check, linting, tests)
check:
    uv run ruff format --check .
    uv run ruff check .
    uv run pytest -v


# Launch full graphical studio (Network Monitor, Reverse Plotter, Trace, Transmit, SDO Explorer)
gui:
    #!/usr/bin/env bash
    # NixOS: binary wheels (numpy, matplotlib) require libstdc++.so.6 from gcc-lib
    if command -v nix-store &>/dev/null; then
        STDCXX=$(find /nix/store -maxdepth 3 -name "libstdc++.so.6" -path "*/gcc-*-lib/lib/*" 2>/dev/null | head -1 | xargs -r dirname)
        [ -n "$STDCXX" ] && export LD_LIBRARY_PATH="${STDCXX}:${LD_LIBRARY_PATH:-}"
    fi
    uv run canopen-studio

# Run sniffer on hardware (default: SLCAN at 500 kbps)
sniff interface="slcan" bitrate="500000":
    uv run can-sniffer -I {{interface}} -b {{bitrate}}

# Run sniffer in Virtual Simulation mode (no hardware required, ideal for study)
simulate duration="10":
    uv run can-sniffer -I virtual --simulate -t {{duration}}

# Launch interactive real-time dashboard (RPM, Temperatures, Torque, Status)
dashboard interface="slcan" bitrate="500000":
    uv run can-sniffer -I {{interface}} -b {{bitrate}} --dashboard

# Launch Tbruno25/can-explorer (real-time payload plotting tool)
can-explorer:
    uv run --with can-explorer can-explorer

# Monitor Extended (29-bit) frames only
extended:
    uv run can-sniffer --extended-only

# Monitor Standard (11-bit) frames only
standard:
    uv run can-sniffer --standard-only

# Filter for a specific CAN ID (e.g. just filter 0x473)
filter can_id="0x473":
    uv run can-sniffer -i {{can_id}}

# Record CAN frames to CSV log file (e.g. just record my_log.csv)
record filename="capture_sevcon.csv":
    uv run can-sniffer -o {{filename}}

# Passive listen-only mode (no ACK frames transmitted on bus)
listen-only:
    uv run can-sniffer --listen-only

# Capture a fixed number of frames and stop (e.g. just sample 50)
sample count="20":
    uv run can-sniffer -c {{count}}

# Run the OBD-II integration tests against Ircama's ELM327 emulator (installed on demand)
test-emulator:
    uv run --with ELM327-emulator pytest -m emulator -v

# Start Ircama's ELM327 emulator on TCP port 35000 for manual testing (Ctrl-C to stop)
emulator scenario="car" port="35000":
    uv run --with ELM327-emulator python -m elm -n {{port}} -s {{scenario}}

# Import a Torque Pro custom-PID CSV into a vehicle profile (e.g. just import-torque pids.csv my_car)
import-torque file profile_id:
    uv run python -c "from canopen_studio.diag.profiles.importers import import_torque_csv; import sys, yaml; r = import_torque_csv('{{file}}', '{{profile_id}}'); print(r); [print(' skipped:', s) for s in r.skipped]"

# List the vehicle profiles available to the OBD-II diagnostics
profiles:
    uv run python -c "from canopen_studio.diag.profiles import ProfileLibrary; lib = ProfileLibrary().load(); [print(f'{p.id:24} {p.name}  ({len(p.table)} PIDs)') for p in lib.resolved()]; [print('ERROR:', e) for e in lib.errors]"

# Build static documentation site with MkDocs Material (also emits llms.txt and llms-full.txt)
doc-build:
    uv run --with mkdocs-material --with mkdocs-llmstxt mkdocs build --strict

# Serve live documentation locally
doc-serve:
    uv run --with mkdocs-material --with mkdocs-llmstxt mkdocs serve

# Clean temporary files, caches, build artifacts, and documentation site
clean:
    #!/usr/bin/env python3
    import shutil, glob, os
    for p in ["build", "dist", "site"] + glob.glob("*.csv"):
        if os.path.exists(p):
            if os.path.isdir(p): shutil.rmtree(p)
            else: os.remove(p)
    for root, dirs, files in os.walk("."):
        for d in dirs:
            if d in ("__pycache__", ".pytest_cache"):
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)

# Run OS-specific installer (sets up environment and creates application shortcuts)
install:
    #!/usr/bin/env python3
    import os, subprocess
    if os.name == "nt":
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/install_windows.ps1"])
    else:
        subprocess.run(["bash", "install.sh"])


# Generate application icon assets (.ico and .png)
generate-icon:
    uv run --with pillow python scripts/generate_icon.py

# Build standalone Windows executable folder with PyInstaller
build-exe:
    uv run --with pyinstaller python scripts/build_exe.py --clean

# Build single-file portable Windows executable (.exe)
build-portable:
    uv run --with pyinstaller python scripts/build_exe.py --clean --onefile
