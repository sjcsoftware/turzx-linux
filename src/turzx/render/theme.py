"""Layout palettes. Widget colours may reference a palette entry as ``@name``."""

from __future__ import annotations

from .draw import RGBA, color, mix

DEFAULT_PALETTE: dict[str, str] = {
    "background": "#07090d",
    "card": "#11151c",
    "card_border": "#1c222d",
    "track": "#222935",
    "text": "#eef1f6",
    "dim": "#7d8698",
    "accent": "#38bdf8",
    "cpu": "#38bdf8",
    "gpu": "#84cc16",
    "mem": "#c084fc",
    "net": "#fbbf24",
    "net_up": "#fb7185",
    "disk": "#f472b6",
    "good": "#4ade80",
    "warn": "#facc15",
    "hot": "#f87171",
}

PALETTE_LABELS = {
    "background": "Background", "card": "Card", "card_border": "Card border", "track": "Gauge track",
    "text": "Text", "dim": "Dim text", "accent": "Accent", "cpu": "CPU", "gpu": "GPU", "mem": "Memory",
    "net": "Network down", "net_up": "Network up", "disk": "Disk", "good": "Good", "warn": "Warning", "hot": "Hot",
}


class Palette:
    def __init__(self, entries: dict[str, str] | None = None, font: str = ""):
        self.entries = {**DEFAULT_PALETTE, **(entries or {})}
        self.font = font

    def resolve(self, value: str | None, fallback: str = "text") -> RGBA:
        """Resolve ``#hex`` or ``@name`` (optionally ``@name/0.5`` for alpha)."""
        if not value:
            value = "@" + fallback
        if value.startswith("@"):
            name, _, alpha = value[1:].partition("/")
            base = self.entries.get(name) or self.entries.get(fallback) or "#ffffff"
            try:
                return color(base, float(alpha) if alpha else None)
            except ValueError:
                return color(base)
        try:
            return color(value)
        except ValueError:
            return color(self.entries.get(fallback, "#ffffff"))

    def temp_color(self, celsius: float | None, warn: float = 70, hot: float = 85) -> RGBA:
        if celsius is None:
            return self.resolve("@dim")
        good, w, h = self.resolve("@good"), self.resolve("@warn"), self.resolve("@hot")
        if celsius < warn - 15:
            return good
        if celsius < warn:
            return mix(good, w, (celsius - (warn - 15)) / 15)
        return mix(w, h, (celsius - warn) / max(1.0, hot - warn))
