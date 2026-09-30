#!/usr/bin/env bash
# Install AprilAire Home for the current user and add an application menu entry.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "$(uname -s)" == "Darwin" ]]; then
  DATA="${APRILAIRE_DATA_DIR:-$HOME/Library/Application Support/AprilAire Home}"
else
  DATA="${APRILAIRE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/aprilaire-homekit}"
fi

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "AprilAire Home needs Python 3.11 or newer." >&2
  exit 1
fi

mkdir -p "$DATA"
VENV="$DATA/venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install "$ROOT"

if [[ "$(uname -s)" == "Linux" ]]; then
  if ! "$VENV/bin/python" -c "import webview" >/dev/null 2>&1; then
    echo "The desktop window toolkit did not import."
    echo "On Debian or Ubuntu you can install it with:"
    echo "  sudo apt-get install python3-gi gir1.2-webkit2-4.1"
    echo "The app will open in a browser until that is installed."
  fi
  DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  mkdir -p "$DESKTOP_DIR"
  cat > "$DESKTOP_DIR/aprilaire-home.desktop" <<EOF
[Desktop Entry]
Name=AprilAire Home
Comment=Connect an AprilAire thermostat to Apple Home
Exec=$VENV/bin/aprilaire-homekit
Terminal=false
Type=Application
Categories=Utility;
StartupNotify=true
EOF
  echo "Installed. Open AprilAire Home from the application menu, or run:"
  echo "  $VENV/bin/aprilaire-homekit"
elif [[ "$(uname -s)" == "Darwin" ]]; then
  APP="$HOME/Applications/AprilAire Home.app"
  mkdir -p "$APP/Contents/MacOS"
  cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>AprilAire Home</string>
  <key>CFBundleDisplayName</key>
  <string>AprilAire Home</string>
  <key>CFBundleIdentifier</key>
  <string>com.aprilaire.homekit</string>
  <key>CFBundleExecutable</key>
  <string>launch</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
</dict>
</plist>
EOF
  cat > "$APP/Contents/MacOS/launch" <<EOF
#!/bin/bash
exec "$VENV/bin/aprilaire-homekit"
EOF
  chmod +x "$APP/Contents/MacOS/launch"
  echo "Installed. Open AprilAire Home from Applications, or run:"
  echo "  $VENV/bin/aprilaire-homekit"
else
  echo "Installed. Start it with:"
  echo "  $VENV/bin/aprilaire-homekit"
fi

if [[ -t 1 && "${APRILAIRE_NO_LAUNCH:-}" != "1" ]]; then
  exec "$VENV/bin/aprilaire-homekit"
fi
