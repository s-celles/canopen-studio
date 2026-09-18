# Justfile for Universal CAN & CANopen Studio (Analyzer & Transmit Station)

set shell := ["powershell.exe", "-NoProfile", "-Command"]

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


# Launch full graphical studio (Network Monitor, Reverse Plotter, Trace, Transmit, SDO Explorer)
gui:
    uv run python can_gui.py

# Run sniffer on hardware (default: SLCAN at 500 kbps)
sniff interface="slcan" bitrate="500000":
    uv run python can_sniffer.py -I {{interface}} -b {{bitrate}}

# Run sniffer in Virtual Simulation mode (no hardware required, ideal for study)
simulate duration="10":
    uv run python can_sniffer.py -I virtual --simulate -t {{duration}}

# Launch interactive real-time dashboard (RPM, Temperatures, Torque, Status)
dashboard interface="slcan" bitrate="500000":
    uv run python can_sniffer.py -I {{interface}} -b {{bitrate}} --dashboard

# Launch Tbruno25/can-explorer (real-time payload plotting tool)
can-explorer:
    uv run --with can-explorer can-explorer

# Monitor Extended (29-bit) frames only
extended:
    uv run python can_sniffer.py --extended-only

# Monitor Standard (11-bit) frames only
standard:
    uv run python can_sniffer.py --standard-only

# Filter for a specific CAN ID (e.g. just filter 0x473)
filter can_id="0x473":
    uv run python can_sniffer.py -i {{can_id}}

# Record CAN frames to CSV log file (e.g. just record my_log.csv)
record filename="capture_sevcon.csv":
    uv run python can_sniffer.py -o {{filename}}

# Passive listen-only mode (no ACK frames transmitted on bus)
listen-only:
    uv run python can_sniffer.py --listen-only

# Capture a fixed number of frames and stop (e.g. just sample 50)
sample count="20":
    uv run python can_sniffer.py -c {{count}}

# Clean temporary files, caches, and logs
clean:
    Get-ChildItem -Path . -Include __pycache__,*.csv,.pytest_cache,build,dist -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# Run 1-click Windows installer (sets up environment and creates Desktop & Start Menu shortcuts)
install:
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_windows.ps1

# Generate application icon assets (.ico and .png)
generate-icon:
    uv run --with pillow python scripts/generate_icon.py

# Build standalone Windows executable folder with PyInstaller
build-exe:
    uv run --with pyinstaller python scripts/build_exe.py --clean

# Build single-file portable Windows executable (.exe)
build-portable:
    uv run --with pyinstaller python scripts/build_exe.py --clean --onefile

