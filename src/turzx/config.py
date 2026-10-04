"""Settings, layout storage and the small file-based IPC shared by the daemon, CLI and GUI.

Files::

    ~/.config/turzx/settings.json      settings (the daemon reloads it on change)
    ~/.config/turzx/layouts/*.json     user layouts
    <package>/presets/*.json           built-in layouts (read-only)
    $XDG_RUNTIME_DIR/turzx/status.json daemon status, rewritten every ~2 s
    $XDG_RUNTIME_DIR/turzx/preview.json layout being edited in the GUI (expires unless refreshed)
    $XDG_RUNTIME_DIR/turzx/pause       "<pid>": the daemon releases the screen while that pid lives
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from importlib import resources
from pathlib import Path
from typing import Any

from .render.layout import new_layout, normalize


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or os.path.expanduser(default))


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", "~/.config") / "turzx"
LAYOUT_DIR = CONFIG_DIR / "layouts"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
RUNTIME_DIR = (Path(os.environ["XDG_RUNTIME_DIR"]) if os.environ.get("XDG_RUNTIME_DIR")
               else Path(tempfile.gettempdir()) / f"turzx-{os.getuid()}") / "turzx"
STATUS_FILE = RUNTIME_DIR / "status.json"
PREVIEW_FILE = RUNTIME_DIR / "preview.json"
PAUSE_FILE = RUNTIME_DIR / "pause"
LOCK_FILE = RUNTIME_DIR / "daemon.lock"


def write_json(path: Path, data: Any) -> None:
    """Atomic write: readers never see a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# ------------------------------------------------------------------ settings
@dataclass
class Settings:
    layout: str = "classic"
    orientation: str = "landscape"
    brightness: float = 75
    fps: float = 2.0
    quality: int = 90
    supersample: int = 2
    sensor_interval: float = 1.0
    disk: str = "/"
    on_exit: str = "off"
    device_pid: str = ""  # hex PID to prefer when several screens are attached
    night_enabled: bool = False
    night_start: str = "23:00"
    night_end: str = "07:00"
    night_brightness: float = 10
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> Settings:
        data = read_json(SETTINGS_FILE, {}) or {}
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in data.items() if k in known})
        return s

    def save(self) -> None:
        write_json(SETTINGS_FILE, asdict(self))

    def effective_brightness(self, now: float | None = None) -> float:
        if not self.night_enabled:
            return self.brightness
        t = time.localtime(now)
        cur = t.tm_hour * 60 + t.tm_min

        def minutes(s: str) -> int:
            try:
                h, m = s.split(":")
                return int(h) * 60 + int(m)
            except ValueError:
                return 0

        start, end = minutes(self.night_start), minutes(self.night_end)
        night = start <= cur < end if start <= end else (cur >= start or cur < end)
        return self.night_brightness if night else self.brightness


# ------------------------------------------------------------------- layouts
@dataclass(frozen=True)
class LayoutRef:
    id: str
    name: str
    builtin: bool
    path: Path
    description: str = ""
    canvas: tuple[int, int] = (1920, 462)
    order: int = 1000


def preset_dir() -> Path:
    return Path(str(resources.files("turzx") / "presets"))


def _ref(path: Path, builtin: bool) -> LayoutRef | None:
    data = read_json(path)
    if not isinstance(data, dict):
        return None
    canvas = data.get("canvas") or [1920, 462]
    return LayoutRef(path.stem, data.get("name") or path.stem, builtin, path, data.get("description", ""),
                     (int(canvas[0]), int(canvas[1])), int(data.get("order", 1000)))


def list_layouts() -> list[LayoutRef]:
    refs = []
    for builtin, folder in ((True, preset_dir()), (False, LAYOUT_DIR)):
        if folder.is_dir():
            for p in folder.glob("*.json"):
                r = _ref(p, builtin)
                if r:
                    refs.append(r)
    builtins = sorted((r for r in refs if r.builtin), key=lambda r: (r.order, r.name.lower()))
    users = sorted((r for r in refs if not r.builtin), key=lambda r: r.name.lower())
    return builtins + users


def find_layout(layout_id: str) -> LayoutRef | None:
    user = LAYOUT_DIR / f"{layout_id}.json"
    if user.exists():
        return _ref(user, False)
    builtin = preset_dir() / f"{layout_id}.json"
    if builtin.exists():
        return _ref(builtin, True)
    return None


def load_layout(id_or_path: str) -> dict[str, Any]:
    """Load a layout by id, or from a .json path."""
    if id_or_path.endswith(".json") or os.sep in id_or_path:
        path = Path(id_or_path).expanduser()
    else:
        ref = find_layout(id_or_path)
        if ref is None:
            raise FileNotFoundError(f"no layout named {id_or_path!r} (see `turzx layouts`)")
        path = ref.path
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a valid layout file")
    return normalize(data)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "layout"


def unique_id(name: str) -> str:
    base = slugify(name)
    taken = {r.id for r in list_layouts()}
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def save_user_layout(layout: dict[str, Any], layout_id: str | None = None) -> str:
    layout_id = layout_id or unique_id(layout.get("name", "layout"))
    data = dict(layout)
    data.pop("order", None)
    write_json(LAYOUT_DIR / f"{layout_id}.json", data)
    return layout_id


def delete_user_layout(layout_id: str) -> None:
    try:
        (LAYOUT_DIR / f"{layout_id}.json").unlink()
    except FileNotFoundError:
        pass


def blank_layout(name: str, canvas: tuple[int, int]) -> dict[str, Any]:
    return new_layout(name, canvas)


# ----------------------------------------------------------------------- ipc
def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_status(max_age: float = 6.0) -> dict[str, Any] | None:
    """Daemon status, or None when no daemon is running."""
    st = read_json(STATUS_FILE)
    if not isinstance(st, dict) or not pid_alive(int(st.get("pid", 0))):
        return None
    if time.time() - float(st.get("updated", 0)) > max_age:
        return None
    return st


def write_preview(layout: dict[str, Any], ttl: float = 6.0) -> None:
    write_json(PREVIEW_FILE, {"expires": time.time() + ttl, "pid": os.getpid(), "layout": layout})


def clear_preview() -> None:
    try:
        PREVIEW_FILE.unlink()
    except FileNotFoundError:
        pass


def read_preview() -> dict[str, Any] | None:
    data = read_json(PREVIEW_FILE)
    if not isinstance(data, dict) or float(data.get("expires", 0)) < time.time():
        return None
    if not pid_alive(int(data.get("pid", 0))):
        return None
    layout = data.get("layout")
    return normalize(layout) if isinstance(layout, dict) else None


def pause_owner() -> int | None:
    try:
        pid = int(PAUSE_FILE.read_text().strip() or 0)
    except (OSError, ValueError):
        return None
    return pid if pid_alive(pid) else None


class DaemonPause:
    """Context manager: ask a running daemon to let go of the screen while we use it."""

    def __init__(self, timeout: float = 4.0):
        self.timeout = timeout
        self.active = False

    def __enter__(self) -> DaemonPause:
        if read_status() is None:
            return self
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        PAUSE_FILE.write_text(str(os.getpid()))
        self.active = True
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            st = read_status()
            if st is None or st.get("state") == "paused":
                break
            time.sleep(0.1)
        return self

    def __exit__(self, *exc) -> None:
        if self.active:
            try:
                if PAUSE_FILE.read_text().strip() == str(os.getpid()):
                    PAUSE_FILE.unlink()
            except OSError:
                pass
