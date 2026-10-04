#!/usr/bin/env bash
# Install turzx for the current user:
#   - a private virtualenv in ~/.local/share/turzx/venv with the driver, GUI and sensors
#   - the `turzx` command in ~/.local/bin
#   - "TURZX Studio" in the applications menu
#   - a systemd user service that drives the screen in the background (starts at login)
#   - a udev rule (needs sudo) so the screen is accessible and starts the service when plugged in
#
# Usage: scripts/install.sh [--no-udev] [--no-service] [--editable]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
VENV="$PREFIX/turzx/venv"
BIN="$HOME/.local/bin"
DO_UDEV=1
DO_SERVICE=1
EDITABLE=0

for arg in "$@"; do
  case "$arg" in
    --no-udev) DO_UDEV=0 ;;
    --no-service) DO_SERVICE=0 ;;
    --editable) EDITABLE=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

# --- prerequisites -----------------------------------------------------------
missing=()
command -v python3 >/dev/null || missing+=(python3)
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null || missing+=("python3>=3.10")
python3 -c 'import venv, ensurepip' 2>/dev/null || missing+=(python3-venv)
python3 -c 'import ctypes.util, sys; sys.exit(ctypes.util.find_library("usb-1.0") is None)' || missing+=(libusb-1.0-0)
if ((${#missing[@]})); then
  warn "missing: ${missing[*]}"
  if command -v apt-get >/dev/null; then
    echo "    sudo apt install python3 python3-venv libusb-1.0-0"
  elif command -v dnf >/dev/null; then
    echo "    sudo dnf install python3 libusb1"
  elif command -v pacman >/dev/null; then
    echo "    sudo pacman -S python libusb"
  fi
  exit 1
fi

# --- python package ----------------------------------------------------------
say "creating virtualenv in $VENV"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
extras="gui,mirror"
command -v nvidia-smi >/dev/null && extras="$extras,nvidia"
say "installing turzx[$extras]"
if ((EDITABLE)); then
  "$VENV/bin/python" -m pip install --quiet -e "$REPO[$extras]"
else
  "$VENV/bin/python" -m pip install --quiet "$REPO[$extras]"
fi

mkdir -p "$BIN"
ln -sf "$VENV/bin/turzx" "$BIN/turzx"
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not in your PATH; add it to use the turzx command" ;; esac

# --- desktop entry -----------------------------------------------------------
say "adding TURZX Studio to the applications menu"
mkdir -p "$PREFIX/applications" "$PREFIX/icons/hicolor/scalable/apps"
sed "s|^Exec=turzx gui|Exec=$VENV/bin/turzx gui|" "$REPO/packaging/turzx-studio.desktop" \
  > "$PREFIX/applications/turzx-studio.desktop"
cp "$REPO/packaging/turzx-studio.svg" "$PREFIX/icons/hicolor/scalable/apps/turzx-studio.svg"
command -v update-desktop-database >/dev/null && update-desktop-database "$PREFIX/applications" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "$PREFIX/icons/hicolor" 2>/dev/null || true

# --- udev --------------------------------------------------------------------
if ((DO_UDEV)); then
  say "installing the udev rule (sudo)"
  if sudo install -m 0644 "$REPO/packaging/60-turzx.rules" /etc/udev/rules.d/60-turzx.rules; then
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=1cbe || true
  else
    warn "udev rule not installed; the screen may need root. Retry: sudo install -m 0644 packaging/60-turzx.rules /etc/udev/rules.d/"
  fi
fi

# --- service -----------------------------------------------------------------
if ((DO_SERVICE)); then
  if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
    say "installing and starting the background service"
    "$VENV/bin/turzx" service install
  else
    warn "no systemd user session; start the service yourself with: turzx daemon"
  fi
fi

say "done. Open \"TURZX Studio\" from your applications, or run: turzx gui"
