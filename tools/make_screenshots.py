#!/usr/bin/env python3
"""Render docs/images/*.png from demo data (busy-looking, no personal machine details).

    python tools/make_screenshots.py            # layout previews
    python tools/make_screenshots.py --studio   # also a screenshot of the editor (needs PySide6)
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

if "--studio" in sys.argv:  # isolate settings/status before turzx.config reads the environment
    import tempfile

    _TMP = tempfile.mkdtemp()
    os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")
    os.environ["XDG_RUNTIME_DIR"] = os.path.join(_TMP, "run")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from turzx.config import preset_dir  # noqa: E402
from turzx.render import RenderContext, Renderer  # noqa: E402

OUT = ROOT / "docs" / "images"


class DemoSensors:
    """Plausible, gently varying values with full histories."""

    BASE = {
        "cpu.util": 42.0, "cpu.temp": 63.0, "cpu.freq": 5.41, "cpu.load1": 6.8, "cpu.load5": 5.9, "cpu.threads": 32.0,
        "cpu.name": "AMD Ryzen 9 9950X", "gpu.util": 71.0, "gpu.temp": 64.0, "gpu.power": 212.0, "gpu.clock": 2.61,
        "gpu.fan": 48.0, "gpu.vram_used": 7.4 * 1024**3, "gpu.vram_total": 12 * 1024**3, "gpu.vram_percent": 61.7,
        "gpu.name": "NVIDIA GeForce RTX 5070", "mem.used": 23.6 * 1024**3, "mem.total": 64 * 1024**3,
        "mem.percent": 36.9, "swap.used": 0.2 * 1024**3, "swap.total": 8 * 1024**3, "swap.percent": 2.5,
        "disk.used": 1.12e12, "disk.total": 2.0e12, "disk.percent": 56.0, "disk.read": 84e6, "disk.write": 22e6,
        "disk.temp": 44.0, "net.down": 6.1e6, "net.up": 0.9e6, "host.name": "workstation",
        "host.os": "Ubuntu 24.04 LTS", "host.kernel": "6.11.0-generic", "host.uptime": 3 * 86400 + 5 * 3600 + 720,
        "host.processes": 512.0, "media.title": "Midnight City", "media.artist": "M83", "media.status": "Playing",
    }
    WAVY = {"cpu.util": (25, 0.21), "gpu.util": (24, 0.13), "mem.percent": (2, 0.05), "net.down": (5e6, 0.37),
            "net.up": (0.8e6, 0.29), "disk.read": (70e6, 0.45), "disk.write": (20e6, 0.33), "cpu.temp": (6, 0.1),
            "gpu.temp": (5, 0.08), "gpu.vram_percent": (4, 0.04)}

    def __init__(self) -> None:
        self.values = dict(self.BASE)

    def _wave(self, key: str, i: int) -> float:
        amp, f = self.WAVY.get(key, (0, 0))
        base = self.BASE[key]
        v = base + amp * (0.6 * math.sin(i * f) + 0.4 * math.sin(i * f * 2.7 + 1.3))
        return max(0.0, v)

    def get(self, key, default=None):
        v = self.values.get(key)
        return default if v is None else v

    def history(self, key):
        if not isinstance(self.BASE.get(key), float):
            return []
        return [self._wave(key, i) for i in range(300)]

    def cores(self):
        return [max(0.0, min(100.0, 42 + 38 * math.sin(i * 1.7) + 12 * math.cos(i * 0.6))) for i in range(32)]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TZ", "UTC")
    ctx = RenderContext(DemoSensors(), scale=2)
    ctx.tick = lambda: None  # freeze the clock for reproducible images
    ctx.now = time.mktime((2026, 10, 4, 21, 42, 7, 0, 0, -1))
    r = Renderer(ctx)
    for p in sorted(preset_dir().glob("*.json")):
        lay = json.loads(p.read_text())
        r.render(lay, tuple(lay["canvas"])).save(OUT / f"{p.stem}.png", optimize=True)
        print("wrote", OUT / f"{p.stem}.png")
    if "--studio" in sys.argv:
        studio()


def studio() -> None:
    from PySide6.QtCore import QTimer

    from turzx import config

    s = config.Settings()
    s.layout = "classic"
    s.extra["asked_autostart"] = True
    s.save()
    fake = {"pid": os.getpid(), "updated": time.time() + 3600, "state": "running", "layout": "Classic Cards",
            "fps": 2.0, "model": 'Turing 9.2" (TURZX V2)', "firmware": "turzx_0001_0015", "size": [1920, 462]}
    config.write_json(config.STATUS_FILE, fake)
    from PySide6.QtWidgets import QApplication

    from turzx.gui import app as studio_app
    from turzx.gui import preview

    app = QApplication(sys.argv)
    studio_app.apply_dark_theme(app)
    demo = DemoSensors()
    studio_app.Sensors = lambda **_kw: type("S", (), {"start": lambda self: demo})()  # type: ignore[assignment]
    demo.stop = lambda: None  # type: ignore[attr-defined]
    orig = preview.PreviewEngine.__init__

    def init(self, sensors, scale=1):
        orig(self, sensors, scale=2)

    preview.PreviewEngine.__init__ = init  # type: ignore[method-assign]
    win = studio_app.MainWindow()
    win.resize(1600, 900)
    win.show()

    def select():
        win.doc.open("gauges")
        win.doc.set_selection([win.doc.widgets()[1]["id"]])

    QTimer.singleShot(800, select)

    def shot():
        win.grab().save(str(OUT / "studio.png"))
        print("wrote", OUT / "studio.png")
        app.quit()

    QTimer.singleShot(4000, shot)
    app.exec()


if __name__ == "__main__":
    main()
