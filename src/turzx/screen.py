"""Logical screen: orientation handling on top of :class:`turzx.device.Device`."""

from __future__ import annotations

import enum

from PIL import Image, ImageChops

from .device import Device


class Orientation(str, enum.Enum):
    """How the panel is mounted, i.e. which way is "up" for what you draw.

    ``landscape`` is the panel lying on its long side the way the factory
    wallpaper (with its "TURZX V2" logo) reads upright; ``landscape-flipped``
    is that turned upside down. ``portrait`` keeps the firmware's native
    orientation.
    """

    LANDSCAPE = "landscape"
    LANDSCAPE_FLIPPED = "landscape-flipped"
    PORTRAIT = "portrait"
    PORTRAIT_FLIPPED = "portrait-flipped"

    @property
    def is_landscape(self) -> bool:
        return self in (Orientation.LANDSCAPE, Orientation.LANDSCAPE_FLIPPED)


# PIL transpose that turns a logical frame into the native portrait frame.
_TO_NATIVE = {
    Orientation.LANDSCAPE: Image.Transpose.ROTATE_270,
    Orientation.LANDSCAPE_FLIPPED: Image.Transpose.ROTATE_90,
    Orientation.PORTRAIT: None,
    Orientation.PORTRAIT_FLIPPED: Image.Transpose.ROTATE_180,
}


class Screen:
    """Draw in your own orientation at the visible resolution; Screen does the rest."""

    def __init__(self, device: Device, orientation: Orientation = Orientation.LANDSCAPE, *, quality: int = 90):
        self.device = device
        self.orientation = Orientation(orientation)
        self.quality = quality
        self._last: Image.Image | None = None

    @property
    def size(self) -> tuple[int, int]:
        """Logical (width, height) of the visible area in the chosen orientation."""
        w, h = self.device.model.visible
        return (h, w) if self.orientation.is_landscape else (w, h)

    def new_canvas(self, color: tuple[int, ...] = (0, 0, 0)) -> Image.Image:
        return Image.new("RGB", self.size, color)

    def to_native(self, image: Image.Image) -> Image.Image:
        if image.size != self.size:
            image = image.resize(self.size, Image.Resampling.LANCZOS)
        op = _TO_NATIVE[self.orientation]
        rotated = image.transpose(op) if op is not None else image
        native_size = self.device.model.native
        if rotated.size == native_size:
            return rotated
        # Columns beyond the visible width are hidden by the bezel: pad them.
        frame = Image.new(rotated.mode, native_size, (0, 0, 0, 0) if rotated.mode == "RGBA" else (0, 0, 0))
        frame.paste(rotated, (0, 0))
        return frame

    def show(self, image: Image.Image, *, force: bool = False, overlay: bool = False) -> bool:
        """Display ``image``. Returns False when skipped because nothing changed."""
        if not force and self._last is not None and image.size == self._last.size and image.mode == self._last.mode:
            if ImageChops.difference(image, self._last).getbbox() is None:
                return False
        self.device.show_native(self.to_native(image), quality=self.quality, overlay=overlay)
        self._last = image.copy()
        return True
