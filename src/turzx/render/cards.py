"""The "System card" widget: complete CPU / GPU / memory / network / disk / clock panels."""

from __future__ import annotations

import time

from .draw import Layer, fmt_bytes, fmt_duration, fmt_rate
from .fields import Field
from .widgets import Widget, register

KINDS = (
    ("cpu", "CPU"), ("gpu", "GPU"), ("memory", "Memory"), ("io", "Network + disk"),
    ("network", "Network"), ("disk", "Disk"), ("clock", "Clock"),
)
DEFAULT_ACCENT = {"cpu": "@cpu", "gpu": "@gpu", "memory": "@mem", "io": "@net", "network": "@net",
                  "disk": "@disk", "clock": "@accent"}


@register
class Card(Widget):
    type = "card"
    title = "System card"
    category = "Panels"
    description = "A complete panel with gauge, stats and graph. Resize freely; it adapts."
    default_size = (486, 434)
    fields = [
        Field("kind", "Panel", "choice", "cpu", options=KINDS),
        Field("title", "Title", "text", "", help="Empty = default"),
        Field("accent", "Accent", "color", "", optional=True, help="Empty = palette colour for this panel"),
        Field("background", "Background", "color", "@card"),
        Field("border", "Border", "color", "@card_border"),
        Field("radius", "Corner radius", "float", 18, min=0, max=200),
        Field("graph", "Show history graph", "bool", True),
        Field("clock_24h", "24-hour clock", "bool", True),
    ]

    def draw(self, L, ctx, pal, p, k):
        L.rect(0, 0, L.w, L.h, pal.resolve(p["background"], "card"), p["radius"] * k, pal.resolve(p["border"]),
               max(1.0, k))
        kind = p["kind"]
        self.pal, self.ctx, self.k, self.p = pal, ctx, k, p
        self.fam = pal.font
        self.accent = pal.resolve(p["accent"] or DEFAULT_ACCENT[kind])
        getattr(self, f"_{kind}")(L)

    # ------------------------------------------------------------- helpers
    def _header(self, L: Layer, title: str, subtitle: str = "") -> None:
        k = self.k
        L.rect(22 * k, 24 * k, 6 * k, 22 * k, self.accent, 3 * k)
        tw = L.text_size(title, 22 * k, "bold", self.fam)[0]
        L.text(38 * k, 21 * k, title, 22 * k, self.pal.resolve("@text"), "bold", self.fam)
        if subtitle:
            room = L.w - 38 * k - tw - 36 * k
            while subtitle and L.text_size(subtitle, 17 * k, "regular", self.fam)[0] > room:
                subtitle = subtitle[:-2] + "…"
            L.text(L.w - 22 * k, 25 * k, subtitle, 17 * k, self.pal.resolve("@dim"), "regular", self.fam, "ra")

    def _title(self, default: str) -> str:
        return self.p["title"] or default

    def _gauge(self, L: Layer, frac: float, label: str) -> float:
        k = self.k
        r = max(20 * k, min(100 * k, (L.h - 200 * k) / 2, L.w * 0.22))
        cx, cy = 28 * k + r, 72 * k + r
        th = 20 * k * min(1.0, r / (100 * k))
        L.arc(cx, cy, r, th, 135, 405, self.pal.resolve("@track"))
        if frac > 0.002:
            L.arc(cx, cy, r, th, 135, 135 + 270 * min(1.0, frac), self.accent)
        L.text(cx + 4 * k, cy - 8 * k, f"{frac * 100:.0f}", r * 0.62, self.pal.resolve("@text"), "bold", self.fam, "mm")
        L.text(cx, cy + r * 0.45, label, 15 * k, self.pal.resolve("@dim"), "semibold", self.fam, "mm")
        return cx + r + 34 * k

    def _stat(self, L: Layer, x, y, label, value, color=None, width=140.0) -> None:
        k = self.k
        L.text(x, y, label, 14 * k, self.pal.resolve("@dim"), "semibold", self.fam)
        size = 30 * k
        while size > 14 * k and L.text_size(value, size, "bold", self.fam)[0] > width - 10 * k:
            size -= 2 * k
        L.text(x, y + 19 * k + (30 * k - size) * 0.8, value, size, color or self.pal.resolve("@text"), "bold", self.fam)

    def _grid(self, L: Layer, sx: float, items: list[tuple]) -> None:
        k = self.k
        colw = (L.w - 22 * k - sx) / 2
        for i, item in enumerate(items):
            label, value, *col = item
            self._stat(L, sx + (i % 2) * colw, 76 * k + (i // 2) * 72 * k, label, value, col[0] if col else None, colw)

    def _spark(self, L: Layer, key: str, y: float, h: float, color=None, vmax: float | None = 100,
               fill: bool = True, floor: float = 0.0) -> None:
        if h <= 4:
            return
        vals = self.ctx.history(key)[-120:]
        if not vals:
            return
        x, w = 24 * self.k, L.w - 48 * self.k
        top = vmax if vmax else max(max(vals), floor, 1e-9)
        step = w / 119
        x0 = x + w - step * (len(vals) - 1)
        pts = [(x0 + i * step, y + h - h * min(v, top) / top) for i, v in enumerate(vals)]
        col = color or self.accent
        if fill and len(pts) > 1:
            L.polygon([(pts[0][0], y + h)] + pts + [(pts[-1][0], y + h)], (*col[:3], 56))
        L.line(pts, col, 2 * self.k)

    def _v(self, key: str):
        return self.ctx.value(key)

    def _n(self, key: str, default: float = 0.0) -> float:
        return self.ctx.number(key, default)

    # --------------------------------------------------------------- cards
    def _cpu(self, L: Layer) -> None:
        k, pal = self.k, self.pal
        self._header(L, self._title("CPU"), str(self._v("cpu.name") or ""))
        sx = self._gauge(L, self._n("cpu.util") / 100, "USAGE %")
        t, f = self._v("cpu.temp"), self._v("cpu.freq")
        self._grid(L, sx, [
            ("TEMP", f"{t:.0f}°C" if t is not None else "—", pal.temp_color(t, 75, 90)),
            ("CLOCK", f"{f:.1f} GHz" if f else "—"),
            ("LOAD 1m", f"{self._n('cpu.load1'):.2f}"),
            ("THREADS", f"{self._n('cpu.threads'):.0f}"),
        ])
        cores = self._v("cpu.cores") or []
        graph_h = 70 * k if self.p["graph"] else 0
        by, bh = L.h - (88 * k + graph_h), 52 * k
        if cores and by > 270 * k * 0.8:
            n, gap = len(cores), (3 if len(cores) <= 64 else 1) * k
            cw = (L.w - 48 * k - gap * (n - 1)) / n
            for i, v in enumerate(cores):
                x = 24 * k + i * (cw + gap)
                L.rect(x, by, cw, bh, pal.resolve("@track"), 2 * k)
                fh = max(2 * k, bh * v / 100)
                L.rect(x, by + bh - fh, cw, fh, self.accent if v < 85 else pal.resolve("@hot"), 2 * k)
        if self.p["graph"]:
            self._spark(L, "cpu.util", L.h - 92 * k, 70 * k)

    def _gpu(self, L: Layer) -> None:
        k, pal = self.k, self.pal
        name = str(self._v("gpu.name") or "").replace("NVIDIA ", "").replace("GeForce ", "")
        self._header(L, self._title("GPU"), name)
        if self._v("gpu.util") is None:
            L.text(L.w / 2, L.h / 2, "no GPU detected", 20 * k, pal.resolve("@dim"), "semibold", self.fam, "mm")
            return
        sx = self._gauge(L, self._n("gpu.util") / 100, "USAGE %")
        t, pw, c, fan = (self._v(x) for x in ("gpu.temp", "gpu.power", "gpu.clock", "gpu.fan"))
        self._grid(L, sx, [
            ("TEMP", f"{t:.0f}°C" if t is not None else "—", pal.temp_color(t, 70, 85)),
            ("POWER", f"{pw:.0f} W" if pw is not None else "—"),
            ("CLOCK", f"{c:.1f} GHz" if c else "—"),
            ("FAN", f"{fan:.0f}%" if fan is not None else "—"),
        ])
        graph_h = 70 * k if self.p["graph"] else 0
        vy = L.h - (84 * k + graph_h)
        if vy > 250 * k * 0.8:
            used, total = self._n("gpu.vram_used"), self._n("gpu.vram_total")
            L.text(24 * k, vy, "VRAM", 14 * k, pal.resolve("@dim"), "semibold", self.fam)
            L.text(L.w - 24 * k, vy - 2 * k, f"{used / 1024**3:.1f} / {total / 1024**3:.0f} GB", 17 * k,
                   pal.resolve("@text"), "semibold", self.fam, "ra")
            self._bar(L, 24 * k, vy + 24 * k, L.w - 48 * k, 14 * k, used / total if total else 0)
        if self.p["graph"]:
            self._spark(L, "gpu.util", L.h - 92 * k, 70 * k)

    def _bar(self, L: Layer, x, y, w, h, frac, color=None) -> None:
        L.rect(x, y, w, h, self.pal.resolve("@track"), h / 2)
        if frac > 0:
            L.rect(x, y, max(h, w * min(1.0, frac)), h, color or self.accent, h / 2)

    def _memory(self, L: Layer) -> None:
        k, pal = self.k, self.pal
        self._header(L, self._title("MEMORY"))
        pct = self._n("mem.percent")
        used, total = self._n("mem.used"), self._n("mem.total")
        L.text(24 * k, 70 * k, f"{pct:.0f}", 76 * k, pal.resolve("@text"), "bold", self.fam)
        pw = L.text_size(f"{pct:.0f}", 76 * k, "bold", self.fam)[0]
        L.text(30 * k + pw, 104 * k, "%", 30 * k, pal.resolve("@dim"), "bold", self.fam)
        L.text(24 * k, 168 * k, f"{used / 1024**3:.1f} / {total / 1024**3:.0f} GB", 22 * k, pal.resolve("@text"),
               "semibold", self.fam)
        self._bar(L, 24 * k, 204 * k, L.w - 48 * k, 14 * k, pct / 100)
        gy = 240 * k
        if self._n("swap.total"):
            L.text(24 * k, 232 * k, "SWAP", 14 * k, pal.resolve("@dim"), "semibold", self.fam)
            L.text(L.w - 24 * k, 230 * k,
                   f"{self._n('swap.used') / 1024**3:.1f} / {self._n('swap.total') / 1024**3:.0f} GB", 16 * k,
                   pal.resolve("@text"), "semibold", self.fam, "ra")
            gy = 270 * k
        if self.p["graph"]:
            self._spark(L, "mem.percent", gy, L.h - 22 * k - gy)

    def _net_block(self, L: Layer, y0: float, graph_h: float) -> None:
        k, pal = self.k, self.pal
        L.text(24 * k, y0, "↓", 26 * k, pal.resolve("@net"), "bold", self.fam)
        L.text(52 * k, y0, fmt_rate(self._n("net.down")), 26 * k, pal.resolve("@text"), "bold", self.fam)
        L.text(24 * k, y0 + 36 * k, "↑", 26 * k, pal.resolve("@net_up"), "bold", self.fam)
        L.text(52 * k, y0 + 36 * k, fmt_rate(self._n("net.up")), 26 * k, pal.resolve("@text"), "bold", self.fam)
        if self.p["graph"] and graph_h > 4:
            top = max(max(self.ctx.history("net.down")[-120:], default=0),
                      max(self.ctx.history("net.up")[-120:], default=0), 125_000)
            gy = y0 + 78 * k
            self._spark(L, "net.down", gy, graph_h, pal.resolve("@net"), top)
            self._spark(L, "net.up", gy, graph_h, pal.resolve("@net_up"), top, fill=False)

    def _disk_block(self, L: Layer, y0: float, graph: bool) -> None:
        k, pal = self.k, self.pal
        disk = pal.resolve("@disk")
        L.rect(22 * k, y0 + 3 * k, 6 * k, 22 * k, disk, 3 * k)
        L.text(38 * k, y0, "DISK", 22 * k, pal.resolve("@text"), "bold", self.fam)
        t = self._v("disk.temp")
        if t is not None:
            L.text(L.w - 22 * k, y0 + 4 * k, f"{t:.0f}°C", 17 * k, pal.temp_color(t, 60, 75), "semibold", self.fam, "ra")
        used, total = self._n("disk.used"), self._n("disk.total")
        L.text(24 * k, y0 + 42 * k, f"{fmt_bytes(used, 0)} / {fmt_bytes(total, 1)}", 18 * k, pal.resolve("@text"),
               "semibold", self.fam)
        self._bar(L, 24 * k, y0 + 72 * k, L.w - 48 * k, 12 * k, used / total if total else 0, disk)
        L.text(24 * k, y0 + 98 * k, f"R {fmt_bytes(self._n('disk.read'))}/s", 16 * k, pal.resolve("@dim"), "semibold",
               self.fam)
        L.text(L.w - 24 * k, y0 + 98 * k, f"W {fmt_bytes(self._n('disk.write'))}/s", 16 * k, pal.resolve("@dim"),
               "semibold", self.fam, "ra")
        gy = y0 + 128 * k
        if graph and L.h - 22 * k - gy > 4:
            top = max(max(self.ctx.history("disk.read")[-120:], default=0),
                      max(self.ctx.history("disk.write")[-120:], default=0), 1_000_000)
            self._spark(L, "disk.read", gy, L.h - 22 * k - gy, disk, top)
            self._spark(L, "disk.write", gy, L.h - 22 * k - gy, pal.resolve("@net"), top, fill=False)

    def _io(self, L: Layer) -> None:
        k = self.k
        self._header(L, self._title("NETWORK"))
        self._net_block(L, 70 * k, 58 * k)
        self._disk_block(L, 228 * k, self.p["graph"])

    def _network(self, L: Layer) -> None:
        k = self.k
        self._header(L, self._title("NETWORK"))
        self._net_block(L, 70 * k, L.h - 170 * k)

    def _disk(self, L: Layer) -> None:
        self._disk_block(L, 21 * self.k, self.p["graph"])

    def _clock(self, L: Layer) -> None:
        k, pal = self.k, self.pal
        now = time.localtime(self.ctx.now)
        h24 = self.p["clock_24h"]
        hhmm = time.strftime("%H:%M" if h24 else "%I:%M", now)
        if not h24:
            hhmm = hhmm.lstrip("0")
        size = 96 * k
        while size > 30 * k and L.text_size(hhmm, size, "bold", self.fam)[0] > L.w - 40 * k:
            size -= 4 * k
        cx = L.w / 2
        L.text(cx, 60 * k, hhmm, size, pal.resolve("@text"), "bold", self.fam, "ma")
        sec = time.strftime(":%S", now) + ("" if h24 else time.strftime(" %p", now))
        L.text(cx, 60 * k + size * 1.08, sec, 22 * k, pal.resolve("@dim"), "semibold", self.fam, "ma")
        L.text(cx, 220 * k, time.strftime("%A", now), 22 * k, pal.resolve("@text"), "semibold", self.fam, "ma")
        L.text(cx, 252 * k, time.strftime("%d %B %Y", now), 18 * k, pal.resolve("@dim"), "medium", self.fam, "ma")
        hy = max(L.h - 92 * k, 300 * k)
        if hy + 50 * k < L.h:
            L.text(cx, hy, str(self._v("host.name") or ""), 18 * k, pal.resolve("@text"), "semibold", self.fam, "ma")
            L.text(cx, hy + 30 * k, f"up {fmt_duration(self._n('host.uptime'))}", 16 * k, pal.resolve("@dim"), "medium",
                   self.fam, "ma")
