"""Install helpers: the per-user systemd service and the udev rule."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .device import MODELS, VENDOR_ID

UNIT_NAME = "turzx.service"
UNIT_PATH = Path(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")) / "systemd" / "user" / UNIT_NAME
UDEV_PATH = Path("/etc/udev/rules.d/60-turzx.rules")


def unit_text(python: str | None = None) -> str:
    python = python or sys.executable
    return f"""[Unit]
Description=TURZX / Turing smart screen
Documentation=https://github.com/sjcsoftware/turzx-linux
After=graphical-session.target

[Service]
Type=simple
ExecStart={python} -m turzx daemon
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""


def udev_rule_text() -> str:
    lines = [
        "# TURZX / Turing Smart Screen USB displays.",
        "# Gives the logged-in user access and starts the per-user turzx service on plug-in.",
        "# Only known screen product ids: VID 1cbe is shared with TI development boards.",
    ]
    for pid, model in sorted(MODELS.items()):
        lines.append(f"# {model.name}")  # udev has no trailing comments
        lines.append(
            f'SUBSYSTEM=="usb", ATTR{{idVendor}}=="{VENDOR_ID:04x}", ATTR{{idProduct}}=="{pid:04x}", '
            f'MODE="0660", TAG+="uaccess", TAG+="systemd", ENV{{SYSTEMD_USER_WANTS}}+="{UNIT_NAME}"'
        )
    return "\n".join(lines) + "\n"


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def systemd_available() -> bool:
    return shutil.which("systemctl") is not None and _systemctl("is-system-running").returncode != 127


def status() -> dict[str, bool]:
    if not shutil.which("systemctl"):
        return {"installed": False, "enabled": False, "active": False}
    return {
        "installed": UNIT_PATH.exists(),
        "enabled": _systemctl("is-enabled", UNIT_NAME).stdout.strip() == "enabled",
        "active": _systemctl("is-active", UNIT_NAME).stdout.strip() == "active",
    }


def install(enable: bool = True, start: bool = True) -> str:
    UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIT_PATH.write_text(unit_text())
    out = [_systemctl("daemon-reload")]
    if enable:
        out.append(_systemctl("enable", UNIT_NAME))
    if start:
        out.append(_systemctl("restart", UNIT_NAME))
    return "\n".join(p.stderr.strip() for p in out if p.returncode != 0)


def uninstall() -> None:
    _systemctl("disable", "--now", UNIT_NAME)
    try:
        UNIT_PATH.unlink()
    except FileNotFoundError:
        pass
    _systemctl("daemon-reload")


def start() -> str:
    return _systemctl("start", UNIT_NAME).stderr.strip()


def stop() -> str:
    return _systemctl("stop", UNIT_NAME).stderr.strip()


def restart() -> str:
    return _systemctl("restart", UNIT_NAME).stderr.strip()


def udev_installed() -> bool:
    return UDEV_PATH.exists()


def udev_install_command(rule_file: str) -> list[str]:
    return ["sh", "-c",
            f"install -m 0644 '{rule_file}' '{UDEV_PATH}' && udevadm control --reload-rules && "
            f"udevadm trigger --subsystem-match=usb --attr-match=idVendor={VENDOR_ID:04x}"]


def install_udev_with_pkexec() -> tuple[bool, str]:
    """Install the udev rule through polkit (graphical password prompt)."""
    if not shutil.which("pkexec"):
        return False, "pkexec is not available; run scripts/install.sh or the sudo command from the README"
    tmp = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "60-turzx.rules"
    tmp.write_text(udev_rule_text())
    p = subprocess.run(["pkexec", *udev_install_command(str(tmp))], capture_output=True, text=True)
    return p.returncode == 0, (p.stderr or p.stdout).strip()


def device_access() -> list[tuple[str, bool]]:
    """(node, accessible) for every attached known screen."""
    out = []
    base = Path("/sys/bus/usb/devices")
    if not base.is_dir():
        return out
    for d in base.iterdir():
        try:
            vid = int((d / "idVendor").read_text(), 16)
            pid = int((d / "idProduct").read_text(), 16)
            bus = int((d / "busnum").read_text())
            dev = int((d / "devnum").read_text())
        except (OSError, ValueError):
            continue
        if vid == VENDOR_ID and pid in MODELS:
            node = f"/dev/bus/usb/{bus:03d}/{dev:03d}"
            out.append((node, os.access(node, os.R_OK | os.W_OK)))
    return out
