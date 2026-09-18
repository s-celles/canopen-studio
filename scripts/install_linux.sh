#!/usr/bin/env bash
# CAN & CANopen Studio - Automated Linux Installer

set -e

echo "============================================================"
echo "   CAN & CANopen Studio - Automated Linux Installer         "
echo "============================================================"
echo ""

cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)

echo "[1/4] Checking Python environment and 'uv' package manager..."
if ! command -v uv &> /dev/null; then
    echo "      'uv' not found. Installing uv automatically..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
fi

if command -v uv &> /dev/null; then
    echo "      Found 'uv': $(command -v uv)"
else
    echo "Failed to locate or install 'uv'. Aborting installation."
    exit 1
fi

echo "[2/4] Synchronizing virtual environment and dependencies..."
uv sync
echo "      Environment synchronized successfully!"

echo "[3/4] Checking application assets and icons..."
ICON_PATH="$PROJECT_ROOT/assets/icon.png"
if [ ! -f "$ICON_PATH" ]; then
    echo "      Generating application icon..."
    uv run --with pillow python scripts/generate_icon.py
fi
if [ -f "$ICON_PATH" ]; then
    echo "      Application icon ready."
fi

echo "[4/4] Creating Linux Desktop entry..."

# Setup directories
mkdir -p "$HOME/.local/share/applications"
mkdir -p "$HOME/.local/share/icons/hicolor/512x512/apps"
mkdir -p "$HOME/.local/bin"

# Copy Icon
if [ -f "$ICON_PATH" ]; then
    cp "$ICON_PATH" "$HOME/.local/share/icons/hicolor/512x512/apps/canopen-studio.png"
    # Try to update icon cache
    if command -v gtk-update-icon-cache &> /dev/null; then
        gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
    fi
fi

# Create launcher script
LAUNCHER_SCRIPT="$HOME/.local/bin/canopen-studio-launcher"
cat << LAUNCHER > "$LAUNCHER_SCRIPT"
#!/usr/bin/env bash
export PATH="\$HOME/.cargo/bin:\$HOME/.local/bin:\$PATH"
cd "$PROJECT_ROOT"
exec uv run can_gui.py
LAUNCHER
chmod +x "$LAUNCHER_SCRIPT"

# Create .desktop file
DESKTOP_FILE="$HOME/.local/share/applications/canopen-studio.desktop"
cat << DESKTOP > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Type=Application
Name=CANopen Studio
Comment=Universal CAN & CANopen protocol analyzer, telemetry plotter, and transmit station
Exec="$LAUNCHER_SCRIPT"
Icon=canopen-studio
Terminal=false
Categories=Development;Engineering;Utility;
Keywords=CAN;CANopen;Bus;Analyzer;
DESKTOP
chmod +x "$DESKTOP_FILE"

# Try to update desktop database
if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

echo "      [OK] Linux Desktop shortcut created:"
echo "           $DESKTOP_FILE"

echo ""
echo "============================================================"
echo "   Installation complete!                                   "
echo "   You can now launch CANopen Studio from your application  "
echo "   launcher (GNOME/KDE/etc).                                "
echo "============================================================"
echo ""
