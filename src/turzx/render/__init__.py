"""Layout rendering: widgets, palettes, templates."""

from .context import RenderContext
from .layout import Renderer, new_layout, normalize
from .theme import DEFAULT_PALETTE, Palette
from .widgets import WIDGETS, Widget, widget_for

__all__ = ["RenderContext", "Renderer", "new_layout", "normalize", "Palette", "DEFAULT_PALETTE", "WIDGETS",
           "Widget", "widget_for"]
