# Justfile for Universal CAN & CANopen Studio (Analyzer & Transmit Station)

# Windows has no POSIX shell on PATH, and `just` needs one even for plain
# recipes, so point it at the bash that ships with Git for Windows.
#
# It must be bin/bash.exe, not usr/bin/sh.exe: only the former sets up the
# MSYS PATH. Under sh.exe the coreutils are missing entirely and `find`
# resolves to Windows' own find.exe, which would break the NixOS probe in
# scripts/launch_gui.sh in a way that is hard to spot.
#
# No recipe uses a `#!` shebang either: `#!/usr/bin/env ...` cannot work on
# Windows, where `env` does not exist.
set windows-shell := ["C:/Program Files/Git/bin/bash.exe", "-cu"]


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
    sh scripts/launch_gui.sh

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
    uv run can-sniffer -n {{count}}

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

# Open a marimo notebook for interactive bus exploration (created on first run)
# marimo stores notebooks as plain .py, so they diff and version like code.
notebook file="notebooks/explore.py":
    mkdir -p "$(dirname "{{file}}")"
    uv run --with marimo marimo edit "{{file}}"

# Clean temporary files, caches, build artifacts, and documentation site
clean:
    uv run python scripts/clean.py

# Run OS-specific installer (sets up environment and creates application shortcuts)
install:
    uv run python scripts/install.py


# Generate application icon assets (.ico and .png)
generate-icon:
    uv run --with pillow python scripts/generate_icon.py

# Build standalone Windows executable folder with PyInstaller
build-exe:
    uv run --with pyinstaller python scripts/build_exe.py --clean

# Build single-file portable Windows executable (.exe)
build-portable:
    uv run --with pyinstaller python scripts/build_exe.py --clean --onefile

# Build Rust crates (canopen-core and canopen-cli)
rust-build:
    cargo build --workspace

# Build the release GUI for distribution, with its dependency list embedded.
# A stripped Rust binary names none of its crates, so an SBOM taken from the
# published archive comes back empty and "no vulnerabilities" means nothing.
# cargo-auditable embeds the list; syft and `cargo audit bin` then read it.
# Install once: cargo install cargo-auditable cargo-audit
rust-release:
    cargo auditable build --release -p canopen-gui

# Check the released binary's embedded dependencies against RustSec
rust-audit: rust-release
    cargo audit bin target/release/canopen-gui.exe

# Run Rust unit tests
rust-test:
    cargo test --workspace

# Build and link Rust extension into Python package
rust-python:
    uv run python scripts/build_extension.py

# Build and run the Rust/Slint GUI (requires nix-shell for fontconfig)
rust-gui:
    cargo run --release -p canopen-gui

# Run high-speed Rust transmitter benchmark
rust-bench-tx count="200000":
    cargo run --release --bin canopen-cli -- bench-tx --count {{count}} --compact

# Ping benchmark: send N pings to target and measure RTT (default: 10 pings to localhost)
rust-bench-ping target="127.0.0.1" target-port="1750" count="10" interval="1000":
    cargo run --release --bin canopen-cli -- bench-ping --target {{target}} --target-port {{target-port}} --count {{count}} --interval-ms {{interval}}

# Echo responder: reply to 0x7E0 ping frames with 0x7E1 (run on remote machine)
rust-echo target="127.0.0.1" target-port="1750":
    cargo run --release --bin canopen-cli -- echo --target {{target}} --target-port {{target-port}}

# Run headless Rust virtual simulator (SYNC 50 Hz, Heartbeat 1 Hz, TPDOs 25 Hz)
rust-simulate port="1750" duration="0":
    cargo run --release --bin canopen-cli -- simulate --port {{port}} --duration-secs {{duration}}

# Measure SYNC jitter of the Rust simulator (run rust-simulate in another terminal first)
rust-bench-latency port="1750" filter="0x080" nominal="20000":
    cargo run --release --bin canopen-cli -- latency --port {{port}} --filter-id {{filter}} --nominal-us {{nominal}}
