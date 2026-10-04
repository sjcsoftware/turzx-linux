"""Per-frame render context: metric lookup, text templates, caches for files and commands.

Template syntax used by text widgets::

    {cpu.util}             value with a sensible default format for its kind
    {cpu.temp:.1f}         Python format spec
    {net.down|rate}        filter: rate bytes ibytes duration int upper lower title
                           gb gib mb mib k (unit conversions, then a format spec may follow)
    {mem.used|gib:.1f}     filter followed by a format spec
    {time:%H:%M}           local time with strftime codes
    {{ and }}              literal braces
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import threading
import time
from typing import Any

from PIL import Image, ImageSequence

from ..sensors import METRICS, Sensors
from .draw import fmt_bytes, fmt_duration, fmt_ibytes, fmt_rate

log = logging.getLogger(__name__)

MISSING = "—"
_TOKEN = re.compile(r"\{\{|\}\}|\{([a-zA-Z0-9_.]+)(?:\|([a-z]+))?(?::([^{}]*))?\}")

_NUM_FILTERS = {
    "gb": 1e-9, "gib": 1 / 1024**3, "mb": 1e-6, "mib": 1 / 1024**2, "k": 1e-3,
}
_STR_FILTERS = {
    "rate": fmt_rate, "bytes": fmt_bytes, "ibytes": fmt_ibytes, "duration": fmt_duration,
}


def default_format(key: str, value: Any) -> str:
    if value is None:
        return MISSING
    if isinstance(value, str):
        return value
    kind = METRICS[key].kind if key in METRICS else "number"
    if kind in ("percent", "temp"):
        return f"{value:.0f}"
    if kind == "bytes":
        return fmt_ibytes(value)
    if kind == "rate":
        return fmt_rate(value) if key.startswith("net.") else fmt_bytes(value) + "/s"
    if kind == "freq":
        return f"{value:.2f}"
    if kind == "power":
        return f"{value:.0f}"
    if kind == "duration":
        return fmt_duration(value)
    if float(value).is_integer():
        return f"{value:.0f}"
    return f"{value:.2f}"


class CommandCache:
    """Runs shell commands for command widgets in the background, at most every ``interval`` s."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._out: dict[str, str] = {}
        self._due: dict[str, float] = {}
        self._running: set[str] = set()

    def get(self, cmd: str, interval: float, timeout: float = 10) -> str:
        now = time.monotonic()
        with self._lock:
            due = self._due.get(cmd, 0)
            if now >= due and cmd not in self._running:
                self._running.add(cmd)
                self._due[cmd] = now + max(0.2, interval)
                threading.Thread(target=self._run, args=(cmd, timeout), daemon=True).start()
            return self._out.get(cmd, "")

    def _run(self, cmd: str, timeout: float) -> None:
        try:
            out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=timeout)
            text = (out.stdout or out.stderr).rstrip("\n")
        except subprocess.TimeoutExpired:
            text = f"timeout: {shlex.split(cmd)[0] if cmd.strip() else ''}"
        except OSError as e:
            text = str(e)
        with self._lock:
            self._out[cmd] = text
            self._running.discard(cmd)


class ImageCache:
    """Loads images once (re-loading when the file changes) and keeps GIF frames."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, list[Image.Image], list[float]]] = {}

    def frames(self, path: str) -> tuple[list[Image.Image], list[float]]:
        path = os.path.expanduser(path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return [], []
        hit = self._cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1], hit[2]
        try:
            im = Image.open(path)
            frames, durations = [], []
            for fr in ImageSequence.Iterator(im):
                frames.append(fr.convert("RGBA"))
                durations.append(max(0.02, fr.info.get("duration", 100) / 1000))
                if len(frames) >= 600:
                    break
        except Exception as e:  # unreadable image: draw nothing rather than crash the screen
            log.warning("cannot load image %s: %s", path, e)
            frames, durations = [], []
        self._cache[path] = (mtime, frames, durations)
        return frames, durations

    def frame_at(self, path: str, t: float) -> Image.Image | None:
        frames, durations = self.frames(path)
        if not frames:
            return None
        if len(frames) == 1:
            return frames[0]
        total = sum(durations)
        pos = t % total
        for fr, d in zip(frames, durations, strict=True):
            if pos < d:
                return fr
            pos -= d
        return frames[-1]


class RenderContext:
    def __init__(self, sensors: Sensors, *, scale: int = 2):
        self.sensors = sensors
        self.scale = scale
        self.now = time.time()
        self.commands = CommandCache()
        self.images = ImageCache()

    def tick(self) -> None:
        self.now = time.time()

    def value(self, key: str) -> Any:
        if key == "cpu.cores":
            return self.sensors.cores()
        return self.sensors.get(key)

    def number(self, key: str, default: float = 0.0) -> float:
        v = self.value(key)
        return float(v) if isinstance(v, (int, float)) else default

    def history(self, key: str) -> list[float]:
        return self.sensors.history(key)

    def missing(self, template: str) -> bool:
        """True when any metric the template references has no value (e.g. nothing is playing)."""
        for m in _TOKEN.finditer(template):
            key = m.group(1)
            if key and key != "time":
                v = self.value(key)
                if v is None or v == "":
                    return True
        return False

    def format(self, template: str) -> str:
        def repl(m: re.Match) -> str:
            tok = m.group(0)
            if tok == "{{":
                return "{"
            if tok == "}}":
                return "}"
            key, filt, spec = m.group(1), m.group(2), m.group(3)
            if key == "time":
                return time.strftime(spec or "%H:%M", time.localtime(self.now))
            value = self.value(key)
            if value is None:
                return MISSING
            try:
                if filt in _NUM_FILTERS:
                    value = float(value) * _NUM_FILTERS[filt]
                elif filt == "int":
                    value = int(round(float(value)))
                elif filt in _STR_FILTERS:
                    value = _STR_FILTERS[filt](float(value))
                elif filt == "upper":
                    value = str(value).upper()
                elif filt == "lower":
                    value = str(value).lower()
                elif filt == "title":
                    value = str(value).title()
                if spec:
                    return format(value, spec)
                if filt:
                    return value if isinstance(value, str) else default_format("", value)
                return default_format(key, value)
            except (ValueError, TypeError):
                return MISSING

        return _TOKEN.sub(repl, template)
