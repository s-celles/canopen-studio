#!/usr/bin/env bash
# CAN & CANopen Studio - Launcher Setup for macOS / Linux

set -e

echo "============================================================"
echo "   CAN & CANopen Studio - Setup & Launcher Installation     "
echo "============================================================"
echo ""

if [ "$(uname)" == "Darwin" ]; then
    bash "$(dirname "$0")/scripts/install_macos.sh"
elif [ "$(expr substr $(uname -s) 1 5)" == "Linux" ]; then
    bash "$(dirname "$0")/scripts/install_linux.sh"
else
    echo "Unsupported OS for automated installer: $(uname)"
    echo "You can still run the app using 'uv run canopen-studio'."
    exit 1
fi
