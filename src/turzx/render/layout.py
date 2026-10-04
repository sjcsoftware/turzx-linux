"""Layouts: a JSON document with a canvas size, palette, background and widgets."""

from __future__ import annotations

import copy
import logging
from typing import Any

from PIL import Image, ImageOps

from .context import RenderContext
from .draw import Layer
from .theme import Palette
from .widgets import widget_for

# Importing registers the composite widgets.
from . import cards  # noqa: F401  isort:skip

log = logging.getLogger(__name__)

FORMAT = 1
DEFAULT_CANVAS = (1920, 462)


def new_layout(name: str = "Untitled", canvas: tuple[int, int] = DEFAULT_CANVAS) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "name": name,
        "description": "",
        "author": "",
        "canvas": list(canvas),
        "font": "Inter, Ubuntu Sans, Noto Sans, DejaVu Sans",
        "palette": {},
        "background": {"color": "@background", "color2": "", "vertical": True, "image": "", "fit": "cover",
                       "dim": 0.0},
        "widgets": [],
    }


def normalize(layout: dict[str, Any]) -> dict[str, Any]:
    """Fill in missing keys so older or hand-written files keep working."""
    base = new_layout()
    out = copy.deepcopy(layout)
    for key, value in base.items():
        out.setdefault(key, copy.deepcopy(value))
    for key, value in base["background"].items():
        out["background"].setdefault(key, value)
    out["widgets"] = [w for w in out.get("widgets", []) if isinstance(w, dict) and "type" in w]
    return out


class Renderer:
    """Renders layouts. Keep one per screen so caches (images, commands) persist."""

    def __init__(self, ctx: RenderContext):
        self.ctx = ctx

    def render(self, layout: dict[str, Any], size: tuple[int, int], *, skip: set[str] | None = None) -> Image.Image:
        ctx = self.ctx
        ctx.tick()
        W, H = size
        cw, ch = layout.get("canvas") or DEFAULT_CANVAS
        sx, sy = W / cw, H / ch
        pal = Palette(layout.get("palette"), layout.get("font", ""))
        frame = self._background(layout.get("background", {}), pal, size)
        for data in layout.get("widgets", []):
            if not data.get("visible", True) or (skip and data.get("id") in skip):
                continue
            w = widget_for(data)
            if w is None:
                continue
            try:
                out = w.render(ctx, pal, sx, sy)
            except Exception:  # one broken widget must not blank the whole screen
                log.exception("widget %s (%s) failed to render", data.get("name"), data.get("type"))
                continue
            if out is not None:
                img, pad = out
                x, y, _, _ = w.box
                _paste(frame, img, round(x * sx - pad), round(y * sy - pad))
        return frame.convert("RGB")

    def _background(self, bg: dict[str, Any], pal: Palette, size: tuple[int, int]) -> Image.Image:
        W, H = size
        c1 = pal.resolve(bg.get("color"), "background")
        if bg.get("color2"):
            L = Layer(W, H, 1)
            L.gradient_rect(0, 0, W, H, c1, pal.resolve(bg["color2"], "background"), bg.get("vertical", True))
            frame = L.img
            base = Image.new("RGBA", size, (0, 0, 0, 255))
            base.alpha_composite(frame)
            frame = base
        else:
            frame = Image.new("RGBA", size, c1[:3] + (255,))
        if bg.get("image"):
            img = self.ctx.images.frame_at(bg["image"], self.ctx.now)
            if img is not None:
                fit = bg.get("fit", "cover")
                if fit == "stretch":
                    img = img.resize(size, Image.Resampling.LANCZOS)
                elif fit == "contain":
                    img = ImageOps.pad(img, size, Image.Resampling.LANCZOS, color=(0, 0, 0, 0))
                else:
                    img = ImageOps.fit(img, size, Image.Resampling.LANCZOS)
                frame.alpha_composite(img.convert("RGBA"))
                dim = float(bg.get("dim") or 0)
                if dim > 0:
                    frame.alpha_composite(Image.new("RGBA", size, (0, 0, 0, round(255 * min(1.0, dim)))))
        return frame


def _paste(frame: Image.Image, img: Image.Image, x: int, y: int) -> None:
    """alpha_composite that tolerates images hanging off the frame edges."""
    if x >= frame.width or y >= frame.height or x + img.width <= 0 or y + img.height <= 0:
        return
    if x < 0 or y < 0:
        img = img.crop((max(0, -x), max(0, -y), img.width, img.height))
        x, y = max(0, x), max(0, y)
    if x + img.width > frame.width or y + img.height > frame.height:
        img = img.crop((0, 0, min(img.width, frame.width - x), min(img.height, frame.height - y)))
    frame.alpha_composite(img, (x, y))
