#!/usr/bin/env bash
# Build a minimal Imit8.app bundle in /Applications so imit8 can live in the dock.
set -euo pipefail

APP_DIR="${1:-/Applications}/Imit8.app"
PYTHON_BIN="$(command -v python3)"
IMIT8_BIN="$(command -v imit8 || true)"

if [[ -z "$IMIT8_BIN" ]]; then
  echo "imit8 is not on PATH — run 'pip install -e .' first" >&2
  exit 1
fi

mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Imit8</string>
  <key>CFBundleDisplayName</key><string>Imit8</string>
  <key>CFBundleIdentifier</key><string>dev.imit8.agent</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>imit8-launcher</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cat > "$APP_DIR/Contents/MacOS/imit8-launcher" <<LAUNCHER
#!/usr/bin/env bash
export PATH="$(dirname "$PYTHON_BIN"):/usr/local/bin:/opt/homebrew/bin:\$PATH"
exec "$IMIT8_BIN" ui
LAUNCHER

chmod +x "$APP_DIR/Contents/MacOS/imit8-launcher"
echo "installed $APP_DIR"
echo "open it once, then right-click its dock icon -> Options -> Keep in Dock"
echo "grant Screen Recording + Accessibility to Imit8.app in System Settings > Privacy & Security"
