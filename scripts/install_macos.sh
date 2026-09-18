#!/usr/bin/env bash
# CAN & CANopen Studio - Automated macOS Installer

set -e

echo "============================================================"
echo "   CAN & CANopen Studio - Automated macOS Installer         "
echo "============================================================"
echo ""

cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)

echo "[1/4] Checking Python environment and 'uv' package manager..."
if ! command -v uv &> /dev/null; then
    echo "      'uv' not found. Installing uv automatically..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
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

echo "[4/4] Creating macOS wrapper Application..."

APP_DIR="$HOME/Desktop/CANopen Studio.app"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

cat << 'LAUNCHER' > "$APP_DIR/Contents/MacOS/CANopen Studio"
#!/usr/bin/env bash
export PATH="$HOME/.local/bin:$PATH"
cd "APP_ROOT_DIR"
exec uv run can_gui.py >/dev/null 2>&1
LAUNCHER

sed -i '' "s|APP_ROOT_DIR|$PROJECT_ROOT|g" "$APP_DIR/Contents/MacOS/CANopen Studio"
chmod +x "$APP_DIR/Contents/MacOS/CANopen Studio"

# Create a basic Info.plist
cat << 'PLIST' > "$APP_DIR/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>CANopen Studio</string>
    <key>CFBundleIconFile</key>
    <string>applet.icns</string>
    <key>CFBundleIdentifier</key>
    <string>com.s-celles.canopenstudio</string>
    <key>CFBundleName</key>
    <string>CANopen Studio</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.10</string>
</dict>
</plist>
PLIST

# Copy icon if available (macOS uses .icns, we'll try to convert .png to .icns using sips and iconutil)
if command -v sips &> /dev/null && command -v iconutil &> /dev/null && [ -f "$ICON_PATH" ]; then
    echo "      Generating .icns file for the application..."
    ICONSET_DIR="/tmp/CANopenStudio.iconset"
    rm -rf "$ICONSET_DIR"
    mkdir "$ICONSET_DIR"
    sips -z 16 16     "$ICON_PATH" --out "$ICONSET_DIR/icon_16x16.png" > /dev/null
    sips -z 32 32     "$ICON_PATH" --out "$ICONSET_DIR/icon_16x16@2x.png" > /dev/null
    sips -z 32 32     "$ICON_PATH" --out "$ICONSET_DIR/icon_32x32.png" > /dev/null
    sips -z 64 64     "$ICON_PATH" --out "$ICONSET_DIR/icon_32x32@2x.png" > /dev/null
    sips -z 128 128   "$ICON_PATH" --out "$ICONSET_DIR/icon_128x128.png" > /dev/null
    sips -z 256 256   "$ICON_PATH" --out "$ICONSET_DIR/icon_128x128@2x.png" > /dev/null
    sips -z 256 256   "$ICON_PATH" --out "$ICONSET_DIR/icon_256x256.png" > /dev/null
    sips -z 512 512   "$ICON_PATH" --out "$ICONSET_DIR/icon_256x256@2x.png" > /dev/null
    sips -z 512 512   "$ICON_PATH" --out "$ICONSET_DIR/icon_512x512.png" > /dev/null
    sips -z 1024 1024 "$ICON_PATH" --out "$ICONSET_DIR/icon_512x512@2x.png" > /dev/null
    iconutil -c icns "$ICONSET_DIR" -o "$APP_DIR/Contents/Resources/applet.icns"
    rm -rf "$ICONSET_DIR"
fi

echo "      [OK] macOS Application wrapper created on Desktop:"
echo "           $APP_DIR"

echo ""
echo "============================================================"
echo "   Installation complete!                                   "
echo "   You can now launch CANopen Studio from your Desktop.     "
echo "============================================================"
echo ""
