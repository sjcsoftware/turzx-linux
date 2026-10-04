"""Drawing primitives: colours, fonts, and a supersampled RGBA layer."""

from __future__ import annotations

import functools
import math
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFilter, ImageFont

RGBA = tuple[int, int, int, int]

WEIGHTS = ["thin", "light", "regular", "medium", "semibold", "bold", "black"]
FALLBACK_FAMILIES = ["Inter", "Ubuntu Sans", "Noto Sans", "DejaVu Sans"]
MONO_FAMILIES = ["JetBrains Mono", "Ubuntu Sans Mono", "Ubuntu Mono", "Noto Sans Mono", "DejaVu Sans Mono"]


def color(value, alpha: float | None = None) -> RGBA:
    """Parse ``#rgb``, ``#rrggbb``, ``#rrggbbaa`` or an (r, g, b[, a]) sequence."""
    if isinstance(value, str):
        s = value.strip().lstrip("#")
        if len(s) in (3, 4):
            s = "".join(c * 2 for c in s)
        if len(s) == 6:
            s += "ff"
        if len(s) != 8:
            raise ValueError(f"bad colour: {value!r}")
        c = tuple(int(s[i : i + 2], 16) for i in (0, 2, 4, 6))
    else:
        c = tuple(int(x) for x in value)
        if len(c) == 3:
            c = c + (255,)
    if alpha is not None:
        c = c[:3] + (round(c[3] * alpha),)
    return c  # type: ignore[return-value]


def to_hex(c: RGBA) -> str:
    return "#" + "".join(f"{v:02x}" for v in (c if c[3] != 255 else c[:3]))


def mix(a, b, t: float) -> RGBA:
    a, b = color(a), color(b)
    t = min(1.0, max(0.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(4))  # type: ignore[return-value]


# ------------------------------------------------------------------- fonts
@functools.lru_cache(maxsize=256)
def _fc_match(family: str, weight: str) -> str | None:
    if not shutil.which("fc-match"):
        return None
    try:
        out = subprocess.run(
            ["fc-match", "-f", "%{family}\n%{file}", f"{family}:{weight}"],
            capture_output=True, text=True, timeout=2,
        ).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if len(out) == 2 and family.lower().replace(" ", "") in out[0].lower().replace(" ", ""):
        return out[1]
    return None


@functools.lru_cache(maxsize=256)
def font_path(family: str, weight: str = "regular") -> str | None:
    """Resolve a family (or comma-separated fallback list) to a font file."""
    families = [f.strip() for f in family.split(",") if f.strip()] if family else []
    mono = any("mono" in f.lower() for f in families)
    for fam in families + (MONO_FAMILIES if mono else FALLBACK_FAMILIES):
        path = _fc_match(fam, weight)
        if path:
            return path
    bold = weight in ("semibold", "bold", "black")
    for p in (
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'Mono' if mono else ''}{'-Bold' if bold else ''}.ttf",
    ):
        try:
            ImageFont.truetype(p, 10)
            return p
        except OSError:
            pass
    return None


@functools.lru_cache(maxsize=512)
def font(size: int, weight: str = "regular", family: str = "") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(1, int(size))
    path = font_path(family, weight)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def list_font_families() -> list[str]:
    if not shutil.which("fc-list"):
        return FALLBACK_FAMILIES
    try:
        out = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return FALLBACK_FAMILIES
    return sorted({line.split(",")[0].strip() for line in out.splitlines() if line.strip()})


# ------------------------------------------------------------------- layer
class Layer:
    """A transparent RGBA drawing surface for one widget.

    Coordinates are logical pixels relative to the widget box; everything is
    drawn ``scale`` times larger and downsampled by the compositor, which gives
    anti-aliased shapes and text.
    """

    def __init__(self, w: float, h: float, scale: int = 2, pad: float = 0):
        self.w, self.h, self.s, self.pad = w, h, scale, pad
        self.img = Image.new("RGBA", (max(1, round((w + 2 * pad) * scale)), max(1, round((h + 2 * pad) * scale))),
                             (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def _p(self, *v: float) -> list[int]:
        return [round((x + self.pad) * self.s) for x in v]

    def _xy(self, x: float, y: float) -> tuple[float, float]:
        return (x + self.pad) * self.s, (y + self.pad) * self.s

    # shapes -------------------------------------------------------------
    def rect(self, x, y, w, h, fill=None, radius: float = 0, outline=None, width: float = 0) -> None:
        if w <= 0 or h <= 0:
            return
        r = max(0.0, min(radius, w / 2, h / 2)) * self.s
        self.d.rounded_rectangle(
            self._p(x, y, x + w, y + h), radius=r,
            fill=color(fill) if fill else None,
            outline=color(outline) if outline and width > 0 else None,
            width=max(1, round(width * self.s)) if outline and width > 0 else 0,
        )

    def gradient_rect(self, x, y, w, h, c1, c2, vertical: bool = True, radius: float = 0) -> None:
        if w <= 0 or h <= 0:
            return
        W, H = max(1, round(w * self.s)), max(1, round(h * self.s))
        a, b = color(c1), color(c2)
        line = Image.new("RGBA", (1, 256) if vertical else (256, 1))
        line.putdata([mix(a, b, i / 255) for i in range(256)])
        grad = line.resize((W, H), Image.Resampling.BILINEAR)
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, H - 1], radius=max(0.0, min(radius, w / 2, h / 2)) * self.s,
                                               fill=255)
        self.img.paste(grad, tuple(self._p(x, y)), mask)

    def ellipse(self, x, y, w, h, fill=None, outline=None, width: float = 0) -> None:
        self.d.ellipse(self._p(x, y, x + w, y + h), fill=color(fill) if fill else None,
                       outline=color(outline) if outline else None, width=max(0, round(width * self.s)))

    def line(self, points, fill, width: float = 1) -> None:
        if len(points) < 2:
            return
        self.d.line([self._xy(px, py) for px, py in points], fill=color(fill), width=max(1, round(width * self.s)),
                    joint="curve")

    def polygon(self, points, fill) -> None:
        if len(points) >= 3:
            self.d.polygon([self._xy(px, py) for px, py in points], fill=color(fill))

    def arc(self, cx, cy, r, width, start, end, fill, round_caps: bool = True) -> None:
        """Arc in degrees, 0 = 3 o'clock, growing clockwise (PIL convention)."""
        if end - start <= 0.05 or r <= 0:
            return
        box = self._p(cx - r, cy - r, cx + r, cy + r)
        self.d.arc(box, start, end, fill=color(fill), width=max(1, round(width * self.s)))
        if round_caps and width > 2:
            rr = r - width / 2
            for ang in (start, end):
                a = math.radians(ang)
                px, py = cx + rr * math.cos(a), cy + rr * math.sin(a)
                self.d.ellipse(self._p(px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill=color(fill))

    # text ---------------------------------------------------------------
    def font(self, size: float, weight: str = "regular", family: str = ""):
        return font(round(size * self.s), weight, family)

    def text_size(self, s: str, size: float, weight: str = "regular", family: str = "", spacing: float = 1.15):
        f = self.font(size, weight, family)
        lines = s.split("\n") or [""]
        width = max(self.d.textlength(line, font=f) for line in lines) / self.s
        return width, size * spacing * len(lines)

    def text(self, x, y, s: str, size: float, fill="#ffffff", weight: str = "regular", family: str = "",
             anchor: str = "la", spacing: float = 1.15, stroke: float = 0, stroke_fill=None) -> None:
        f = self.font(size, weight, family)
        kw = {}
        if stroke > 0:
            kw = {"stroke_width": max(1, round(stroke * self.s)), "stroke_fill": color(stroke_fill or "#000000")}
        if "\n" in s:
            align = {"l": "left", "m": "center", "r": "right"}[anchor[0]]
            self.d.multiline_text(self._xy(x, y), s, font=f, fill=color(fill), anchor=anchor, align=align,
                                  spacing=round(size * (spacing - 1) * self.s), **kw)
        else:
            self.d.text(self._xy(x, y), s, font=f, fill=color(fill), anchor=anchor, **kw)

    def paste(self, image: Image.Image, x: float, y: float) -> None:
        image = image.convert("RGBA")
        self.img.alpha_composite(image, tuple(self._p(x, y)))

    # output -------------------------------------------------------------
    def result(self, opacity: float = 1.0, glow: float = 0, glow_color=None) -> Image.Image:
        """Downsample to logical size, applying opacity and an optional glow."""
        out = self.img
        if glow > 0:
            g = out.getchannel("A").filter(ImageFilter.GaussianBlur(glow * self.s))
            gc = color(glow_color or "#ffffff")
            halo = Image.new("RGBA", out.size, gc[:3] + (0,))
            halo.putalpha(g.point(lambda a: min(255, int(a * 1.6 * gc[3] / 255))))
            halo.alpha_composite(out)
            out = halo
        if self.s != 1:
            out = out.resize((max(1, round(self.w + 2 * self.pad)), max(1, round(self.h + 2 * self.pad))),
                             Image.Resampling.LANCZOS)
        if opacity < 1:
            a = out.getchannel("A").point(lambda v: int(v * max(0.0, opacity)))
            out.putalpha(a)
        return out


# ----------------------------------------------------------------- formats
def fmt_bytes(n: float, digits: int = 1) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1000 or unit == "PB":
            return f"{n:.0f} B" if unit == "B" else f"{n:.{digits}f} {unit}"
        n /= 1000
    return ""


def fmt_ibytes(n: float, digits: int = 1) -> str:
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(n) < 1024 or unit == "PiB":
            return f"{n:.0f} B" if unit == "B" else f"{n:.{digits}f} {unit}"
        n /= 1024
    return ""


def fmt_rate(n: float) -> str:
    """Network speeds are compared in bits per second."""
    bits = float(n) * 8
    for unit in ("b/s", "Kb/s", "Mb/s", "Gb/s"):
        if bits < 1000 or unit == "Gb/s":
            return f"{bits:.0f} {unit}" if unit == "b/s" else f"{bits:.1f} {unit}"
        bits /= 1000
    return ""


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"
