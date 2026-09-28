#!/usr/bin/env bash
# ============================================================================
#  LapseCam installer — https://github.com/V3tter/lapsecam
#
#  One-liner:  curl -fsSL <raw-url>/install.sh | bash
#  From clone: ./install.sh [options]
#
#  Options: --target DIR · --no-deps · --no-optional · --with-tray · -h
# ============================================================================
set -euo pipefail

REPO_URL="https://github.com/V3tter/lapsecam.git"

TARGET_DIR="$HOME/LapseCam"
INSTALL_DEPS=1
ASK_OPTIONALS=1
WITH_TRAY=0

usage() {
  cat <<'USAGE'
LapseCam installer (Arch Linux)

Usage:
  ./install.sh [options]        (from a clone)
  curl -fsSL <raw-url> | bash   (clones to the target first, then installs)

Options:
  --target DIR     install location            (default: ~/LapseCam)
  --no-deps        skip pacman package installation
  --no-optional    skip optional-extras and tray prompts
  --with-tray      also set up the tray-icon venv (non-interactive)
  -h, --help       this help
USAGE
}

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m ->\033[0m %s\n' "$*"; }

ask() {  # ask "Question?" -> exit status 0 on yes
  local reply
  read -r -p "$1 [y/N] " reply </dev/tty || return 1
  [[ "$reply" =~ ^[Yy]$ ]]
}

if [[ $EUID -eq 0 ]]; then
  echo "error: don't run this as root — it installs into your home directory," >&2
  echo "       and calls sudo itself where needed." >&2
  exit 1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)      [[ $# -ge 2 ]] || { echo "error: --target needs a directory" >&2; exit 1; }
                   TARGET_DIR="$2"; shift ;;
    --no-deps)     INSTALL_DEPS=0 ;;
    --no-optional) ASK_OPTIONALS=0 ;;
    --with-tray)   WITH_TRAY=1 ;;
    -h|--help)     usage; exit 0 ;;
    *)             echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"
TARGET_DIR="$(realpath -m "$TARGET_DIR")"

# --- piped invocation? (no app files next to this script) -------------------
if [[ ! -f "$SRC/TimelapseRecorder.py" ]]; then
  if [[ -e "$TARGET_DIR" ]]; then
    warn "$TARGET_DIR already exists."
    warn "To update:  cd $TARGET_DIR && git pull && ./install.sh --no-deps"
    exit 1
  fi
  command -v git >/dev/null 2>&1 || { echo "error: git is required for the one-line install." >&2; exit 1; }
  say "Cloning $REPO_URL …"
  git clone "$REPO_URL" "$TARGET_DIR"
  args=(--target "$TARGET_DIR")
  if (( INSTALL_DEPS == 0 ));  then args+=(--no-deps); fi
  if (( ASK_OPTIONALS == 0 )); then args+=(--no-optional); fi
  if (( WITH_TRAY == 1 ));     then args+=(--with-tray); fi
  exec bash "$TARGET_DIR/install.sh" "${args[@]}"
fi

# --- packages ---------------------------------------------------------------
if (( INSTALL_DEPS == 1 )) && ! command -v pacman >/dev/null 2>&1; then
  warn "pacman not found — this installer targets Arch Linux; skipping packages."
  warn "Needed otherwise: python3 + tk + opencv + numpy + pillow + ffmpeg + xdg-open."
  INSTALL_DEPS=0
fi

if (( INSTALL_DEPS == 1 )); then
  command -v sudo >/dev/null 2>&1 || { echo "error: sudo needed for pacman (or use --no-deps)." >&2; exit 1; }
  say "Installing required packages (sudo will ask for your password)…"
  sudo pacman -S --needed python tk python-opencv python-numpy python-pillow ffmpeg xdg-utils
  if (( ASK_OPTIONALS == 1 )) && ask "Install optional extras (fonts, chime, v4l2 tools)?"; then
    sudo pacman -S --needed ttf-dejavu libcanberra v4l-utils || warn "Optional extras failed — continuing."
  fi
fi

# --- app files --------------------------------------------------------------
if [[ "$SRC" != "$TARGET_DIR" ]]; then
  say "Copying app files to $TARGET_DIR …"
  mkdir -p "$TARGET_DIR"
  for f in TimelapseRecorder.py README.md requirements.txt myicon.png; do
    if [[ -f "$SRC/$f" ]]; then cp -f "$SRC/$f" "$TARGET_DIR/"; fi
  done
fi
[[ -f "$TARGET_DIR/TimelapseRecorder.py" ]] || { echo "error: TimelapseRecorder.py missing in $TARGET_DIR" >&2; exit 1; }

# --- optional tray venv -----------------------------------------------------
if (( WITH_TRAY == 0 )) && (( ASK_OPTIONALS == 1 )) && [[ ! -x "$TARGET_DIR/.venv/bin/python" ]]; then
  if ask "Enable the optional system-tray icon? (pacman GTK libs + small venv)"; then
    WITH_TRAY=1
  fi
fi
if (( WITH_TRAY == 1 )) && [[ ! -x "$TARGET_DIR/.venv/bin/python" ]]; then
  if command -v pacman >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    sudo pacman -S --needed python-gobject gtk3 libayatana-appindicator || warn "Tray packages failed — continuing without tray."
  fi
  if python3 -m venv --system-site-packages "$TARGET_DIR/.venv" \
     && "$TARGET_DIR/.venv/bin/pip" install --quiet pystray; then
    say "Tray-enabled venv ready ($TARGET_DIR/.venv)."
    warn "GNOME also needs the 'AppIndicator and KStatusNotifierItem Support' extension; KDE works out of the box."
  else
    warn "venv/pystray setup failed — LapseCam will run without a tray icon."
  fi
fi

PYEXE="python3"
if [[ -x "$TARGET_DIR/.venv/bin/python" ]]; then PYEXE="$TARGET_DIR/.venv/bin/python"; fi

# --- terminal launcher ------------------------------------------------------
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
rm -f "$BIN_DIR/lapsecam"     # replaces file OR symlink from earlier installs
cat > "$BIN_DIR/lapsecam" <<LAUNCHER
#!/usr/bin/env bash
# lapsecam — terminal launcher for LapseCam (generated by install.sh)
APP_DIR="\${LAPSECAM_HOME:-$TARGET_DIR}"

if [[ "\$1" == "-d" || "\$1" == "--detach" ]]; then
  shift
  setsid -f "\$0" "\$@"
  exit 0
fi

if [[ -x "\$APP_DIR/.venv/bin/python" ]]; then
  exec "\$APP_DIR/.venv/bin/python" "\$APP_DIR/TimelapseRecorder.py" "\$@"
else
  exec python3 "\$APP_DIR/TimelapseRecorder.py" "\$@"
fi
LAUNCHER
chmod +x "$BIN_DIR/lapsecam"

# --- application-menu entry -------------------------------------------------
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
{
  echo "[Desktop Entry]"
  echo "Type=Application"
  echo "Name=LapseCam"
  echo "Comment=Webcam timelapse recorder (24/30/60 fps)"
  echo "Exec=$BIN_DIR/lapsecam"
  if [[ -f "$TARGET_DIR/myicon.png" ]]; then
    echo "Icon=$TARGET_DIR/myicon.png"
  fi
  echo "Terminal=false"
  echo "Categories=AudioVideo;Video;Recorder;"
} > "$DESKTOP_DIR/lapsecam.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DESKTOP_DIR" || true
fi

# --- verify -----------------------------------------------------------------
say "Syntax-checking the app …"
"$PYEXE" -m py_compile "$TARGET_DIR/TimelapseRecorder.py"

say "Done. Installed to: $TARGET_DIR"
echo
echo "  Run:        lapsecam            (terminal; -d detaches)"
echo "              or 'LapseCam' from your application menu"
echo "  Update:     cd $TARGET_DIR && git pull && ./install.sh --no-deps"
echo "  Uninstall:  rm -rf '$TARGET_DIR' '$BIN_DIR/lapsecam' '$DESKTOP_DIR/lapsecam.desktop'"
echo
