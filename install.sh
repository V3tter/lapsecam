#!/usr/bin/env bash
# Thin installer: copies this repo's files into place. Run from anywhere.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-$HOME/LapseCam}"
mkdir -p "$TARGET"
cp -f "$SRC/TimelapseRecorder.py" "$SRC/README.md" "$SRC/requirements.txt" "$TARGET/"
[[ -f "$SRC/myicon.png" ]] && cp -f "$SRC/myicon.png" "$TARGET/"
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
PY="python3"
[[ -x "$TARGET/.venv/bin/python" ]] && PY="$TARGET/.venv/bin/python"
cat > "$DESKTOP_DIR/lapsecam.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=LapseCam
Comment=Webcam timelapse recorder
Exec="$PY" "$TARGET/TimelapseRecorder.py"
Path=$TARGET
Icon=$TARGET/myicon.png
Terminal=false
Categories=AudioVideo;Video;Recorder;
DESK
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" || true
echo "Installed to $TARGET"
