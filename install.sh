#!/usr/bin/env bash
# CAN & CANopen Studio - Launcher Setup for macOS / Linux

set -e

echo "============================================================"
echo "   CAN & CANopen Studio - Setup & Launcher Installation     "
echo "============================================================"
echo ""

if [ "$(uname)" == "Darwin" ]; then
    bash "$(dirname "$0")/scripts/install_macos.sh"
else
    echo "Linux installation script not implemented yet."
    echo "You can still run the app using 'uv run can_gui.py'."
    exit 0
fi
