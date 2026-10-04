"""Property schema shared by the renderer (defaults, validation) and the GUI (editors)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..sensors import METRICS

#: Field kinds understood by the GUI inspector.
KINDS = {
    "text", "multiline", "template", "int", "float", "color", "bool", "choice",
    "metric", "font", "weight", "file", "command",
}


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str
    default: Any
    options: tuple = ()  # choice: values, or (value, label) pairs
    min: float | None = None
    max: float | None = None
    step: float | None = None
    help: str = ""
    group: str = "Properties"
    optional: bool = False  # metric/color fields that may be left empty

    def __post_init__(self) -> None:
        assert self.kind in KINDS, self.kind

    def choices(self) -> list[tuple[Any, str]]:
        if self.kind == "metric":
            numeric = [(k, f"{d.label}  ({k})") for k, d in METRICS.items() if d.kind != "text"]
            return ([("", "(none)")] if self.optional else []) + numeric
        if self.kind == "weight":
            return [(w, w) for w in ("thin", "light", "regular", "medium", "semibold", "bold", "black")]
        return [o if isinstance(o, tuple) else (o, str(o)) for o in self.options]

    def coerce(self, value: Any) -> Any:
        """Best-effort conversion of a stored value; falls back to the default."""
        if value is None:
            return self.default
        try:
            if self.kind == "int":
                v = int(round(float(value)))
            elif self.kind == "float":
                v = float(value)
            elif self.kind == "bool":
                v = bool(value)
            else:
                return str(value) if not isinstance(value, str) else value
        except (TypeError, ValueError):
            return self.default
        if self.min is not None:
            v = max(self.min, v)
        if self.max is not None:
            v = min(self.max, v)
        return v


# Common building blocks -------------------------------------------------------
def font_fields(size: int = 32, weight: str = "semibold", family: str = "", color: str = "@text",
                group: str = "Text") -> list[Field]:
    return [
        Field("family", "Font", "font", family, help="Family name; empty = theme font", group=group),
        Field("weight", "Weight", "weight", weight, group=group),
        Field("size", "Size", "float", size, min=4, max=600, step=1, group=group),
        Field("color", "Colour", "color", color, group=group),
    ]


EFFECT_FIELDS = [
    Field("opacity", "Opacity", "float", 1.0, min=0, max=1, step=0.05, group="Effects"),
    Field("glow", "Glow radius", "float", 0.0, min=0, max=40, step=1, group="Effects"),
    Field("glow_color", "Glow colour", "color", "@accent", group="Effects"),
]


@dataclass
class Schema:
    fields: list[Field] = field(default_factory=list)

    def defaults(self) -> dict[str, Any]:
        return {f.key: f.default for f in self.fields}

    def by_key(self) -> dict[str, Field]:
        return {f.key: f for f in self.fields}
