#!/usr/bin/env bash
# Remove everything scripts/install.sh installed. Your layouts in ~/.config/turzx are kept
# unless you pass --purge.
set -euo pipefail

PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

if command -v systemctl >/dev/null; then
  systemctl --user disable --now turzx.service 2>/dev/null || true
  rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/turzx.service"
  systemctl --user daemon-reload 2>/dev/null || true
fi
rm -f "$HOME/.local/bin/turzx"
rm -f "$PREFIX/applications/turzx-studio.desktop" "$PREFIX/icons/hicolor/scalable/apps/turzx-studio.svg"
rm -rf "$PREFIX/turzx"
if [[ -f /etc/udev/rules.d/60-turzx.rules ]]; then
  echo "removing the udev rule (sudo)"
  sudo rm -f /etc/udev/rules.d/60-turzx.rules && sudo udevadm control --reload-rules || true
fi
if ((PURGE)); then
  rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/turzx"
  echo "removed settings and layouts"
fi
echo "turzx uninstalled"
