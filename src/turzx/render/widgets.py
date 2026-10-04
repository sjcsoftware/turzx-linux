"""Widget types. Each declares a field schema; the GUI builds its editor from it.

To add a widget: subclass :class:`Widget`, set ``type``/``title``/``fields`` and
implement :meth:`Widget.draw`, then decorate it with :func:`register`.
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from typing import Any, ClassVar

from PIL import Image, ImageDraw, ImageOps

from ..sensors import METRICS
from .context import RenderContext
from .draw import Layer, mix
from .fields import EFFECT_FIELDS, Field, font_fields
from .theme import Palette

WIDGETS: dict[str, type[Widget]] = {}

ALIGN = (("left", "Left"), ("center", "Centre"), ("right", "Right"))
VALIGN = (("top", "Top"), ("middle", "Middle"), ("bottom", "Bottom"))


def register(cls: type[Widget]) -> type[Widget]:
    WIDGETS[cls.type] = cls
    return cls


def new_id() -> str:
    return uuid.uuid4().hex[:8]


class Widget:
    type: ClassVar[str] = ""
    title: ClassVar[str] = ""
    category: ClassVar[str] = "Basic"
    description: ClassVar[str] = ""
    default_size: ClassVar[tuple[int, int]] = (200, 100)
    fields: ClassVar[list[Field]] = []

    def __init__(self, data: dict[str, Any]):
        self.data = data

    # -- schema / data ----------------------------------------------------
    @classmethod
    def schema(cls) -> list[Field]:
        return cls.fields + EFFECT_FIELDS

    @classmethod
    def create(cls, x: float, y: float, w: float | None = None, h: float | None = None, **props) -> dict[str, Any]:
        dw, dh = cls.default_size
        return {
            "id": new_id(), "type": cls.type, "name": cls.title,
            "x": x, "y": y, "w": w if w is not None else dw, "h": h if h is not None else dh,
            "visible": True, "locked": False,
            "props": {f.key: f.default for f in cls.schema()} | props,
        }

    @property
    def props(self) -> dict[str, Any]:
        stored = self.data.get("props", {})
        return {f.key: f.coerce(stored.get(f.key, f.default)) for f in self.schema()}

    @property
    def box(self) -> tuple[float, float, float, float]:
        d = self.data
        return float(d.get("x", 0)), float(d.get("y", 0)), float(d.get("w", 10)), float(d.get("h", 10))

    # -- rendering --------------------------------------------------------
    def render(self, ctx: RenderContext, pal: Palette, sx: float = 1.0, sy: float = 1.0
               ) -> tuple[Image.Image, float] | None:
        """Render to an RGBA image. Returns (image, pad): the image extends ``pad``
        pixels beyond the widget box on every side (room for the glow)."""
        _, _, w, h = self.box
        w, h = w * sx, h * sy
        if w < 1 or h < 1:
            return None
        p = self.props
        glow = p.get("glow", 0.0) * min(sx, sy)
        pad = float(math.ceil(glow * 2.5)) if glow > 0 else 0.0
        layer = Layer(w, h, ctx.scale, pad)
        self.draw(layer, ctx, pal, p, min(sx, sy))
        glow_color = pal.resolve(p.get("glow_color"), "accent")
        return layer.result(p.get("opacity", 1.0), glow, glow_color), pad

    def draw(self, L: Layer, ctx: RenderContext, pal: Palette, p: dict[str, Any], k: float) -> None:
        """Draw into ``L`` (local coordinates, already scaled). ``k`` scales sizes and fonts."""
        raise NotImplementedError

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def family(p: dict, pal: Palette) -> str:
        return p.get("family") or pal.font

    @staticmethod
    def frac(ctx: RenderContext, key: str, vmin: float, vmax: float) -> tuple[float | None, float]:
        """Return (value, fraction 0..1) for a metric, auto-scaling when vmax <= vmin."""
        v = ctx.value(key)
        if not isinstance(v, (int, float)):
            return None, 0.0
        if vmax <= vmin:
            d = METRICS.get(key)
            vmax = d.max if d and d.max else max(ctx.history(key) or [1.0])
            vmax = max(vmax, vmin + 1e-9)
        return float(v), min(1.0, max(0.0, (float(v) - vmin) / (vmax - vmin)))

    @staticmethod
    def threshold_color(pal: Palette, p: dict, value: float | None, base_key: str = "color"):
        c = pal.resolve(p.get(base_key), "accent")
        if value is None:
            return c
        if p.get("hot_at") and value >= p["hot_at"]:
            return pal.resolve(p.get("hot_color"), "hot")
        if p.get("warn_at") and value >= p["warn_at"]:
            return pal.resolve(p.get("warn_color"), "warn")
        return c


THRESHOLD_FIELDS = [
    Field("warn_at", "Warn at", "float", 0.0, min=0, help="0 = off", group="Thresholds"),
    Field("warn_color", "Warn colour", "color", "@warn", group="Thresholds"),
    Field("hot_at", "Hot at", "float", 0.0, min=0, help="0 = off", group="Thresholds"),
    Field("hot_color", "Hot colour", "color", "@hot", group="Thresholds"),
]

RANGE_FIELDS = [
    Field("min", "Minimum", "float", 0.0, group="Data"),
    Field("max", "Maximum", "float", 0.0, help="0 = automatic (100 for percentages)", group="Data"),
]


def _draw_text_block(L: Layer, text: str, size: float, color, weight: str, family: str, align: str, valign: str,
                     fit: bool, spacing: float = 1.15, stroke: float = 0, stroke_color=None, pad: float = 0) -> None:
    w, h = L.w - 2 * pad, L.h - 2 * pad
    if fit:
        lo, hi = 4.0, size
        while hi - lo > 0.5:
            mid = (lo + hi) / 2
            tw, th = _measure(L, text, mid, weight, family, spacing)
            if tw <= w and th <= h:
                lo = mid
            else:
                hi = mid
        tw, th = _measure(L, text, hi, weight, family, spacing)
        size = hi if tw <= w and th <= h else lo
    hz = {"left": "l", "center": "m", "right": "r"}[align]
    x = {"left": pad, "center": L.w / 2, "right": L.w - pad}[align]
    if "\n" not in text:
        vz = {"top": "a", "middle": "m", "bottom": "d"}[valign]
        y = {"top": pad, "middle": L.h / 2, "bottom": L.h - pad}[valign]
        L.text(x, y, text, size, color, weight, family, anchor=hz + vz, stroke=stroke, stroke_fill=stroke_color)
        return
    _, th = _measure(L, text, size, weight, family, spacing)
    y = {"top": pad, "middle": (L.h - th) / 2, "bottom": L.h - pad - th}[valign]
    L.text(x, y, text, size, color, weight, family, anchor=hz + "a", spacing=spacing, stroke=stroke,
           stroke_fill=stroke_color)


def _measure(L: Layer, text: str, size: float, weight: str, family: str, spacing: float) -> tuple[float, float]:
    f = L.font(size, weight, family)
    if "\n" in text:
        b = L.d.multiline_textbbox((0, 0), text, font=f, spacing=round(size * (spacing - 1) * L.s), anchor="la")
    else:
        b = L.d.textbbox((0, 0), text, font=f, anchor="la")
    return (b[2] - b[0]) / L.s, (b[3] - min(b[1], 0)) / L.s


# =========================================================================== basic
@register
class Shape(Widget):
    type = "shape"
    title = "Shape"
    description = "Rectangle or ellipse with optional gradient and border; use it for panels and accents."
    default_size = (300, 200)
    fields = [
        Field("shape", "Shape", "choice", "rect", options=(("rect", "Rectangle"), ("ellipse", "Ellipse"))),
        Field("fill", "Fill", "color", "@card"),
        Field("fill2", "Gradient to", "color", "", optional=True, help="Empty = solid fill"),
        Field("vertical", "Vertical gradient", "bool", True),
        Field("radius", "Corner radius", "float", 18, min=0, max=1000),
        Field("border_color", "Border colour", "color", "@card_border"),
        Field("border_width", "Border width", "float", 1, min=0, max=50),
    ]

    def draw(self, L, ctx, pal, p, k):
        bw = p["border_width"] * k
        if p["shape"] == "ellipse":
            L.ellipse(0, 0, L.w, L.h, fill=pal.resolve(p["fill"], "card"),
                      outline=pal.resolve(p["border_color"], "card_border") if bw else None, width=bw)
            return
        r = p["radius"] * k
        if p["fill2"]:
            L.gradient_rect(0, 0, L.w, L.h, pal.resolve(p["fill"], "card"), pal.resolve(p["fill2"], "card"),
                            p["vertical"], r)
            if bw:
                L.rect(0, 0, L.w, L.h, None, r, pal.resolve(p["border_color"], "card_border"), bw)
        else:
            L.rect(0, 0, L.w, L.h, pal.resolve(p["fill"], "card"), r,
                   pal.resolve(p["border_color"], "card_border") if bw else None, bw)


@register
class Text(Widget):
    type = "text"
    title = "Text"
    description = "Static text or a live template such as {cpu.temp:.0f}°C or {time:%H:%M}."
    default_size = (400, 80)
    fields = [
        Field("text", "Text", "template", "Hello {host.name}",
              help="Templates: {cpu.util}  {gpu.temp:.1f}  {net.down|rate}  {mem.used|gib:.1f}  {time:%H:%M:%S}"),
        *font_fields(48, "bold"),
        Field("align", "Align", "choice", "left", options=ALIGN, group="Text"),
        Field("valign", "Vertical align", "choice", "middle", options=VALIGN, group="Text"),
        Field("fit", "Shrink to fit", "bool", True, group="Text"),
        Field("spacing", "Line spacing", "float", 1.15, min=0.6, max=3, step=0.05, group="Text"),
        Field("stroke", "Outline width", "float", 0, min=0, max=20, group="Text"),
        Field("stroke_color", "Outline colour", "color", "#000000", group="Text"),
        Field("hide_missing", "Hide when data is missing", "bool", False,
              help="e.g. hide a now-playing line when nothing plays"),
    ]

    def draw(self, L, ctx, pal, p, k):
        if p.get("hide_missing") and ctx.missing(p["text"]):
            return
        _draw_text_block(L, ctx.format(p["text"]), p["size"] * k, pal.resolve(p["color"]), p["weight"],
                         self.family(p, pal), p["align"], p["valign"], p["fit"], p["spacing"], p["stroke"] * k,
                         pal.resolve(p["stroke_color"]))


@register
class Clock(Text):
    type = "clock"
    title = "Clock"
    description = "Text preset for time and date; any strftime codes in {time:...}."
    default_size = (360, 140)
    fields = [
        Field("text", "Format", "template", "{time:%H:%M}",
              help="{time:%H:%M:%S}  {time:%I:%M %p}  {time:%A %d %B}  {time:%Y-%m-%d}"),
        *font_fields(110, "bold"),
        Field("align", "Align", "choice", "center", options=ALIGN, group="Text"),
        Field("valign", "Vertical align", "choice", "middle", options=VALIGN, group="Text"),
        Field("fit", "Shrink to fit", "bool", True, group="Text"),
        Field("spacing", "Line spacing", "float", 1.1, min=0.6, max=3, step=0.05, group="Text"),
        Field("stroke", "Outline width", "float", 0, min=0, max=20, group="Text"),
        Field("stroke_color", "Outline colour", "color", "#000000", group="Text"),
    ]


@register
class Stat(Widget):
    type = "stat"
    title = "Stat"
    description = "A small label over a big value, e.g. TEMP / 54°C."
    default_size = (200, 90)
    fields = [
        Field("label", "Label", "template", "TEMP"),
        Field("value", "Value", "template", "{cpu.temp:.0f}°C"),
        Field("metric", "Colour by metric", "metric", "", optional=True, group="Colour",
              help="Optional: colour the value with the thresholds below"),
        Field("align", "Align", "choice", "left", options=ALIGN),
        Field("family", "Font", "font", "", group="Text"),
        Field("label_size", "Label size", "float", 15, min=4, max=200, group="Text"),
        Field("label_color", "Label colour", "color", "@dim", group="Text"),
        Field("value_size", "Value size", "float", 34, min=4, max=400, group="Text"),
        Field("value_weight", "Value weight", "weight", "bold", group="Text"),
        Field("color", "Value colour", "color", "@text", group="Colour"),
        *THRESHOLD_FIELDS,
    ]

    def draw(self, L, ctx, pal, p, k):
        fam = self.family(p, pal)
        ls, vs = p["label_size"] * k, p["value_size"] * k
        x = {"left": 0, "center": L.w / 2, "right": L.w}[p["align"]]
        a = {"left": "l", "center": "m", "right": "r"}[p["align"]]
        L.text(x, 0, ctx.format(p["label"]), ls, pal.resolve(p["label_color"], "dim"), "semibold", fam, a + "a")
        value = ctx.format(p["value"])
        v = ctx.value(p["metric"]) if p["metric"] else None
        col = self.threshold_color(pal, p, v if isinstance(v, (int, float)) else None)
        while vs > 8 and L.text_size(value, vs, p["value_weight"], fam)[0] > L.w:
            vs -= 1
        L.text(x, ls * 1.3, value, vs, col, p["value_weight"], fam, a + "a")


# =========================================================================== data
@register
class Ring(Widget):
    type = "ring"
    title = "Ring gauge"
    category = "Data"
    description = "Circular gauge for any metric."
    default_size = (220, 220)
    fields = [
        Field("metric", "Metric", "metric", "cpu.util", group="Data"),
        *RANGE_FIELDS,
        Field("thickness", "Thickness", "float", 20, min=1, max=300, group="Gauge"),
        Field("start", "Start angle", "float", 135, min=-360, max=360,
              help="Degrees clockwise from 3 o'clock", group="Gauge"),
        Field("sweep", "Sweep", "float", 270, min=1, max=360, group="Gauge"),
        Field("round_caps", "Round caps", "bool", True, group="Gauge"),
        Field("color", "Colour", "color", "@accent", group="Gauge"),
        Field("track", "Track colour", "color", "@track", group="Gauge"),
        Field("value", "Value text", "template", "{value:.0f}", help="{value} = the metric; empty = none",
              group="Text"),
        Field("label", "Label", "template", "CPU", group="Text"),
        Field("family", "Font", "font", "", group="Text"),
        Field("value_size", "Value size", "float", 0, min=0, max=400, help="0 = automatic", group="Text"),
        Field("label_size", "Label size", "float", 15, min=4, max=200, group="Text"),
        Field("text_color", "Text colour", "color", "@text", group="Text"),
        Field("label_color", "Label colour", "color", "@dim", group="Text"),
        *THRESHOLD_FIELDS,
    ]

    def draw(self, L, ctx, pal, p, k):
        v, f = self.frac(ctx, p["metric"], p["min"], p["max"])
        th = p["thickness"] * k
        r = min(L.w, L.h) / 2 - 1
        cx, cy = L.w / 2, L.h / 2
        L.arc(cx, cy, r, th, p["start"], p["start"] + p["sweep"], pal.resolve(p["track"], "track"), p["round_caps"])
        if f > 0:
            L.arc(cx, cy, r, th, p["start"], p["start"] + p["sweep"] * f, self.threshold_color(pal, p, v),
                  p["round_caps"])
        fam = self.family(p, pal)
        if p["value"]:
            text = ctx.format(p["value"].replace("{value", "{" + p["metric"])) if v is not None else "—"
            vs = p["value_size"] * k or r * 0.62
            L.text(cx, cy - (vs * 0.08 if p["label"] else 0), text, vs, pal.resolve(p["text_color"]), "bold", fam, "mm")
        if p["label"]:
            L.text(cx, cy + r * 0.45, ctx.format(p["label"]), p["label_size"] * k, pal.resolve(p["label_color"], "dim"),
                   "semibold", fam, "mm")


@register
class Bar(Widget):
    type = "bar"
    title = "Bar"
    category = "Data"
    description = "Horizontal or vertical progress bar, solid or segmented."
    default_size = (400, 18)
    fields = [
        Field("metric", "Metric", "metric", "mem.percent", group="Data"),
        *RANGE_FIELDS,
        Field("vertical", "Vertical", "bool", False, group="Bar"),
        Field("color", "Colour", "color", "@accent", group="Bar"),
        Field("color2", "Gradient to", "color", "", optional=True, help="Colour at 100%; empty = solid", group="Bar"),
        Field("track", "Track colour", "color", "@track", group="Bar"),
        Field("radius", "Corner radius", "float", 9, min=0, max=500, group="Bar"),
        Field("segments", "Segments", "int", 0, min=0, max=200, help="0 = continuous", group="Bar"),
        Field("gap", "Segment gap", "float", 3, min=0, max=50, group="Bar"),
        *THRESHOLD_FIELDS,
    ]

    def draw(self, L, ctx, pal, p, k):
        v, f = self.frac(ctx, p["metric"], p["min"], p["max"])
        col = self.threshold_color(pal, p, v)
        col2 = pal.resolve(p["color2"]) if p["color2"] else None
        track = pal.resolve(p["track"], "track")
        r = p["radius"] * k
        vert = p["vertical"]
        n = p["segments"]
        if n > 0:
            gap = p["gap"] * k
            length = L.h if vert else L.w
            seg = (length - gap * (n - 1)) / n
            lit = round(f * n)
            for i in range(n):
                c = (mix(col, col2, i / max(1, n - 1)) if col2 else col) if i < lit else track
                if vert:
                    L.rect(0, L.h - (i + 1) * seg - i * gap, L.w, seg, c, r)
                else:
                    L.rect(i * (seg + gap), 0, seg, L.h, c, r)
            return
        L.rect(0, 0, L.w, L.h, track, r)
        if f <= 0:
            return
        if vert:
            fh = max(min(L.w, 2 * r), L.h * f)
            if col2:
                L.gradient_rect(0, L.h - fh, L.w, fh, mix(col, col2, f), col, True, r)
            else:
                L.rect(0, L.h - fh, L.w, fh, col, r)
        else:
            fw = max(min(L.h, 2 * r), L.w * f)
            if col2:
                L.gradient_rect(0, 0, fw, L.h, col, mix(col, col2, f), False, r)
            else:
                L.rect(0, 0, fw, L.h, col, r)


@register
class Graph(Widget):
    type = "graph"
    title = "Graph"
    category = "Data"
    description = "History of one or two metrics as a line, area or bar chart."
    default_size = (500, 140)
    fields = [
        Field("metric", "Metric", "metric", "cpu.util", group="Data"),
        Field("metric2", "Second metric", "metric", "", optional=True, group="Data"),
        Field("max", "Maximum", "float", 0.0, help="0 = automatic", group="Data"),
        Field("floor", "Minimum scale", "float", 0.0, help="Auto scale never goes below this", group="Data"),
        Field("samples", "Samples shown", "int", 120, min=5, max=300, help="One sample per sensor interval",
              group="Data"),
        Field("style", "Style", "choice", "area", options=(("area", "Area"), ("line", "Line"), ("bars", "Bars")),
              group="Graph"),
        Field("color", "Colour", "color", "@accent", group="Graph"),
        Field("color2", "Second colour", "color", "@net_up", group="Graph"),
        Field("fill_opacity", "Fill opacity", "float", 0.25, min=0, max=1, step=0.05, group="Graph"),
        Field("line_width", "Line width", "float", 2, min=0.5, max=20, group="Graph"),
        Field("grid", "Grid lines", "int", 0, min=0, max=10, group="Graph"),
        Field("grid_color", "Grid colour", "color", "@track", group="Graph"),
        Field("background", "Background", "color", "", optional=True, group="Graph"),
        Field("radius", "Corner radius", "float", 0, min=0, max=200, group="Graph"),
    ]

    def draw(self, L, ctx, pal, p, k):
        if p["background"]:
            L.rect(0, 0, L.w, L.h, pal.resolve(p["background"]), p["radius"] * k)
        n = p["samples"]
        series = [(ctx.history(p["metric"])[-n:], pal.resolve(p["color"], "accent"))]
        if p["metric2"]:
            series.append((ctx.history(p["metric2"])[-n:], pal.resolve(p["color2"], "net_up")))
        top = p["max"]
        if top <= 0:
            d = METRICS.get(p["metric"])
            if d and d.max and not p["metric2"]:
                top = d.max
            else:
                top = max([max(s, default=0) for s, _ in series] + [p["floor"], 1e-9])
        lw = p["line_width"] * k
        pad = lw / 2 + 1
        h = L.h - 2 * pad
        for i in range(1, p["grid"] + 1):
            gy = pad + h * i / (p["grid"] + 1)
            L.line([(0, gy), (L.w, gy)], pal.resolve(p["grid_color"], "track"), max(1, k))
        step = L.w / max(n - 1, 1)
        for idx, (vals, col) in enumerate(series):
            if not vals:
                continue
            x0 = L.w - step * (len(vals) - 1)
            pts = [(x0 + i * step, pad + h - h * min(max(v, 0), top) / top) for i, v in enumerate(vals)]
            if p["style"] == "bars":
                bw = max(1.0, step * 0.7)
                for (x, y) in pts:
                    L.rect(x - bw / 2, y, bw, L.h - pad - y, col if idx == 0 else (*col[:3], 200))
                continue
            if p["style"] == "area" and idx == 0 and len(pts) > 1:
                L.polygon([(pts[0][0], L.h)] + pts + [(pts[-1][0], L.h)], (*col[:3], round(255 * p["fill_opacity"])))
            L.line(pts, col, lw)


@register
class Cores(Widget):
    type = "cores"
    title = "CPU threads"
    category = "Data"
    description = "One bar (or tile) per CPU thread."
    default_size = (460, 56)
    fields = [
        Field("style", "Style", "choice", "bars", options=(("bars", "Bars"), ("tiles", "Heat tiles"))),
        Field("rows", "Rows (tiles)", "int", 2, min=1, max=16),
        Field("color", "Colour", "color", "@cpu"),
        Field("hot_color", "Hot colour", "color", "@hot"),
        Field("track", "Track colour", "color", "@track"),
        Field("gap", "Gap", "float", 3, min=0, max=30),
        Field("radius", "Corner radius", "float", 2, min=0, max=50),
    ]

    def draw(self, L, ctx, pal, p, k):
        cores = ctx.value("cpu.cores") or []
        if not cores:
            return
        n, gap, r = len(cores), p["gap"] * k, p["radius"] * k
        col, hot, track = pal.resolve(p["color"]), pal.resolve(p["hot_color"], "hot"), pal.resolve(p["track"], "track")
        if p["style"] == "tiles":
            rows = max(1, min(p["rows"], n))
            cols = math.ceil(n / rows)
            tw, th = (L.w - gap * (cols - 1)) / cols, (L.h - gap * (rows - 1)) / rows
            for i, v in enumerate(cores):
                c = mix(track, col, v / 70) if v < 70 else mix(col, hot, (v - 70) / 30)
                L.rect((i % cols) * (tw + gap), (i // cols) * (th + gap), tw, th, c, r)
            return
        cw = (L.w - gap * (n - 1)) / n
        for i, v in enumerate(cores):
            x = i * (cw + gap)
            L.rect(x, 0, cw, L.h, track, r)
            fh = max(2 * k, L.h * v / 100)
            L.rect(x, L.h - fh, cw, fh, col if v < 85 else hot, r)


# =========================================================================== media
@register
class ImageWidget(Widget):
    type = "image"
    title = "Image / GIF"
    category = "Media"
    description = "PNG, JPEG, WebP or animated GIF from a file."
    default_size = (300, 300)
    fields = [
        Field("path", "File", "file", ""),
        Field("fit", "Fit", "choice", "cover",
              options=(("cover", "Cover (crop)"), ("contain", "Contain"), ("stretch", "Stretch"), ("center", "Centre"))),
        Field("radius", "Corner radius", "float", 0, min=0, max=1000),
        Field("speed", "GIF speed", "float", 1.0, min=0.1, max=10, step=0.1),
    ]

    def draw(self, L, ctx, pal, p, k):
        if not p["path"]:
            L.rect(0, 0, L.w, L.h, pal.resolve("@track/0.6"), 8 * k)
            L.text(L.w / 2, L.h / 2, "choose an image", min(28.0, L.w / 10), pal.resolve("@dim"), "semibold",
                   pal.font, "mm")
            return
        img = ctx.images.frame_at(p["path"], ctx.now * p["speed"])
        if img is None:
            return
        W, H = round(L.w * L.s), round(L.h * L.s)
        fit = p["fit"]
        if fit == "cover":
            out = ImageOps.fit(img, (W, H), Image.Resampling.LANCZOS)
        elif fit == "stretch":
            out = img.resize((W, H), Image.Resampling.LANCZOS)
        else:
            out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            src = ImageOps.contain(img, (W, H), Image.Resampling.LANCZOS) if fit == "contain" else img
            # paste with the image as its own mask so "center" may also crop an oversized image
            out.paste(src, ((W - src.width) // 2, (H - src.height) // 2), src)
        if p["radius"] > 0:
            mask = Image.new("L", (W, H), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, H - 1], radius=p["radius"] * k * L.s, fill=255)
            out.putalpha(Image.composite(out.getchannel("A"), mask, mask))
        L.img.alpha_composite(out, tuple(L._p(0, 0)))


@register
class Command(Widget):
    type = "command"
    title = "Command output"
    category = "Media"
    description = "Output of any shell command, refreshed on an interval."
    default_size = (600, 160)
    fields = [
        Field("command", "Command", "command", "uptime -p", help="Run with /bin/sh -c"),
        Field("interval", "Refresh every (s)", "float", 5, min=0.5, max=86400),
        Field("max_lines", "Max lines", "int", 6, min=1, max=100),
        *font_fields(22, "regular", "JetBrains Mono, monospace"),
        Field("align", "Align", "choice", "left", options=ALIGN, group="Text"),
        Field("valign", "Vertical align", "choice", "top", options=VALIGN, group="Text"),
        Field("fit", "Shrink to fit", "bool", True, group="Text"),
    ]

    def draw(self, L, ctx, pal, p, k):
        out = ctx.commands.get(p["command"], p["interval"]) if p["command"].strip() else ""
        lines = out.splitlines()[-p["max_lines"]:] or ["…"]
        _draw_text_block(L, "\n".join(lines), p["size"] * k, pal.resolve(p["color"]), p["weight"],
                         self.family(p, pal), p["align"], p["valign"], p["fit"], 1.2)


@register
class AnalogClock(Widget):
    type = "analog_clock"
    title = "Analog clock"
    category = "Media"
    description = "Clock face with hour, minute and optional second hands."
    default_size = (260, 260)
    fields = [
        Field("face", "Face", "color", "@card"),
        Field("border", "Rim colour", "color", "@card_border"),
        Field("ticks", "Tick colour", "color", "@dim"),
        Field("hands", "Hand colour", "color", "@text"),
        Field("seconds", "Show seconds", "bool", True),
        Field("second_color", "Second hand", "color", "@accent"),
    ]

    def draw(self, L, ctx, pal, p, k):
        r = min(L.w, L.h) / 2 - 2
        cx, cy = L.w / 2, L.h / 2
        L.ellipse(cx - r, cy - r, 2 * r, 2 * r, pal.resolve(p["face"], "card"), pal.resolve(p["border"], "card_border"),
                  max(1.0, r * 0.03))
        tick = pal.resolve(p["ticks"], "dim")
        for i in range(60):
            a = math.radians(i * 6 - 90)
            outer, inner = r * 0.92, r * (0.80 if i % 5 == 0 else 0.87)
            L.line([(cx + inner * math.cos(a), cy + inner * math.sin(a)),
                    (cx + outer * math.cos(a), cy + outer * math.sin(a))], tick, r * (0.025 if i % 5 == 0 else 0.01))
        t = time.localtime(ctx.now)
        hand = pal.resolve(p["hands"])
        for ang, length, width in (
            ((t.tm_hour % 12 + t.tm_min / 60) * 30, 0.5, 0.055),
            ((t.tm_min + t.tm_sec / 60) * 6, 0.75, 0.035),
        ):
            a = math.radians(ang - 90)
            L.line([(cx, cy), (cx + r * length * math.cos(a), cy + r * length * math.sin(a))], hand, r * width)
        if p["seconds"]:
            a = math.radians(t.tm_sec * 6 - 90)
            sc = pal.resolve(p["second_color"], "accent")
            L.line([(cx - r * 0.15 * math.cos(a), cy - r * 0.15 * math.sin(a)),
                    (cx + r * 0.85 * math.cos(a), cy + r * 0.85 * math.sin(a))], sc, r * 0.015)
            L.ellipse(cx - r * 0.04, cy - r * 0.04, r * 0.08, r * 0.08, sc)
        else:
            L.ellipse(cx - r * 0.035, cy - r * 0.035, r * 0.07, r * 0.07, hand)


_mss_local = threading.local()


@register
class ScreenMirror(Widget):
    type = "mirror"
    title = "Screen mirror"
    category = "Media"
    description = "Live capture of a monitor or desktop region (X11; needs the 'mss' package)."
    default_size = (640, 360)
    fields = [
        Field("monitor", "Monitor", "int", 1, min=0, max=16, help="1 = first monitor, 0 = whole desktop"),
        Field("region", "Region", "text", "", help="x,y,w,h in desktop pixels; empty = whole monitor"),
        Field("fit", "Fit", "choice", "contain",
              options=(("cover", "Cover (crop)"), ("contain", "Contain"), ("stretch", "Stretch"))),
    ]

    def draw(self, L, ctx, pal, p, k):
        try:
            import mss
        except ImportError:
            L.rect(0, 0, L.w, L.h, pal.resolve("@track/0.6"), 8 * k)
            L.text(L.w / 2, L.h / 2, "pip install mss", min(28.0, L.w / 10), pal.resolve("@dim"), "semibold",
                   pal.font, "mm")
            return
        sct = getattr(_mss_local, "sct", None)
        if sct is None:
            sct = _mss_local.sct = mss.mss()
        try:
            if p["region"].strip():
                x, y, w, h = (int(v) for v in p["region"].split(","))
                area = {"left": x, "top": y, "width": w, "height": h}
            else:
                area = sct.monitors[min(p["monitor"], len(sct.monitors) - 1)]
            shot = sct.grab(area)
        except Exception as e:
            L.text(L.w / 2, L.h / 2, f"capture failed: {e}"[:60], 18 * k, pal.resolve("@hot"), "semibold", pal.font,
                   "mm")
            return
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        W, H = round(L.w * L.s), round(L.h * L.s)
        if p["fit"] == "cover":
            out = ImageOps.fit(img, (W, H), Image.Resampling.BILINEAR)
        elif p["fit"] == "stretch":
            out = img.resize((W, H), Image.Resampling.BILINEAR)
        else:
            out = Image.new("RGB", (W, H))
            c = ImageOps.contain(img, (W, H), Image.Resampling.BILINEAR)
            out.paste(c, ((W - c.width) // 2, (H - c.height) // 2))
        L.img.paste(out, tuple(L._p(0, 0)))


def widget_for(data: dict[str, Any]) -> Widget | None:
    cls = WIDGETS.get(data.get("type", ""))
    return cls(data) if cls else None
