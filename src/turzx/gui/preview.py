"""Background rendering for the editor canvas and the layout thumbnails."""

from __future__ import annotations

import copy
import logging
import threading
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage

from ..render import RenderContext, Renderer
from ..sensors import Sensors
from .qtutil import pil_to_qimage

log = logging.getLogger(__name__)


class PreviewEngine(QObject):
    """Renders the latest requested layout on a worker thread (older requests are dropped)."""

    ready = Signal(QImage)

    def __init__(self, sensors: Sensors, scale: int = 1):
        super().__init__()
        self._renderer = Renderer(RenderContext(sensors, scale=scale))
        self._cond = threading.Condition()
        self._pending: tuple[dict[str, Any], tuple[int, int]] | None = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="turzx-preview", daemon=True)
        self._thread.start()

    def request(self, layout: dict[str, Any], size: tuple[int, int]) -> None:
        with self._cond:
            self._pending = (copy.deepcopy(layout), size)
            self._cond.notify()

    def set_scale(self, scale: int) -> None:
        self._renderer.ctx.scale = scale

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._pending is None and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                layout, size = self._pending
                self._pending = None
            try:
                img = self._renderer.render(layout, size)
                self.ready.emit(pil_to_qimage(img))
            except Exception:
                log.exception("preview render failed")


class ThumbnailWorker(QThread):
    """Renders small previews of every layout once."""

    thumb = Signal(str, QImage)

    def __init__(self, sensors: Sensors, items: list[tuple[str, dict[str, Any]]], width: int = 150):
        super().__init__()
        self.items, self.width = items, width
        self._renderer = Renderer(RenderContext(sensors, scale=1))

    def run(self) -> None:
        for layout_id, layout in self.items:
            try:
                cw, ch = layout.get("canvas") or (1920, 462)
                img = self._renderer.render(layout, (cw, ch))
                h = max(1, round(self.width * ch / cw)) if cw >= ch else 64
                w = self.width if cw >= ch else max(1, round(64 * cw / ch))
                self.thumb.emit(layout_id, pil_to_qimage(img.resize((w, h))))
            except Exception:
                log.exception("thumbnail for %s failed", layout_id)
