"""The editing canvas: live preview with movable, resizable widget frames."""

from __future__ import annotations

import json
import os
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)

from .document import LayoutDocument

HANDLE = 9  # handle size in screen pixels
SEL_COLOR = QColor("#38bdf8")
HOVER_COLOR = QColor(255, 255, 255, 110)


class Handle(QGraphicsRectItem):
    CURSORS = {
        "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
        "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
        "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
        "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
    }

    def __init__(self, frame: Frame, role: str):
        super().__init__(-HANDLE / 2, -HANDLE / 2, HANDLE, HANDLE, frame)
        self.frame, self.role = frame, role
        self.setBrush(QBrush(QColor("#ffffff")))
        self.setPen(QPen(SEL_COLOR, 1.5))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setCursor(self.CURSORS[role])
        self.setZValue(10)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self._start: tuple[QPointF, QRectF, QPointF] | None = None

    def mousePressEvent(self, e):  # noqa: N802
        self.frame.canvas.begin_edit()
        self._start = (e.scenePos(), QRectF(self.frame.rect()), QPointF(self.frame.pos()))
        e.accept()

    def mouseMoveEvent(self, e):  # noqa: N802
        if not self._start:
            return
        p0, r0, pos0 = self._start
        d = e.scenePos() - p0
        x, y, w, h = pos0.x(), pos0.y(), r0.width(), r0.height()
        snap = self.frame.canvas.snap_value
        free = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if "l" in self.role:
            nx = x + d.x() if free else snap(x + d.x())
            w, x = w + (x - nx), nx
        if "r" in self.role:
            w = (w + d.x()) if free else snap(x + w + d.x()) - x
        if "t" in self.role:
            ny = y + d.y() if free else snap(y + d.y())
            h, y = h + (y - ny), ny
        if "b" in self.role:
            h = (h + d.y()) if free else snap(y + h + d.y()) - y
        w, h = max(4.0, w), max(4.0, h)
        self.frame.setPos(x, y)
        self.frame.setRect(0, 0, w, h)
        self.frame.layout_handles()
        self.frame.canvas.live_sync()
        e.accept()

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._start = None
        self.frame.canvas.end_edit("Resize")
        e.accept()


class Frame(QGraphicsRectItem):
    """Transparent outline standing in for one widget on the canvas."""

    def __init__(self, canvas: Canvas, data: dict[str, Any]):
        super().__init__()
        self.canvas = canvas
        self.wid = data["id"]
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.handles = [Handle(self, r) for r in ("tl", "t", "tr", "r", "br", "b", "bl", "l")]
        self._hover = False
        self.update_from(data)

    def update_from(self, data: dict[str, Any]) -> None:
        self.setPos(float(data.get("x", 0)), float(data.get("y", 0)))
        self.setRect(0, 0, max(1.0, float(data.get("w", 10))), max(1.0, float(data.get("h", 10))))
        locked = bool(data.get("locked"))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not locked)
        self.setVisible(bool(data.get("visible", True)))
        self.setToolTip(f"{data.get('name', '')}  ({data.get('type')})" + ("  [locked]" if locked else ""))
        self.locked = locked
        self.layout_handles()

    def layout_handles(self) -> None:
        r = self.rect()
        pts = {"tl": r.topLeft(), "t": QPointF(r.center().x(), r.top()), "tr": r.topRight(),
               "r": QPointF(r.right(), r.center().y()), "br": r.bottomRight(), "b": QPointF(r.center().x(), r.bottom()),
               "bl": r.bottomLeft(), "l": QPointF(r.left(), r.center().y())}
        show = self.isSelected() and not self.locked and len(self.canvas.scene().selectedItems()) == 1
        for h in self.handles:
            h.setPos(pts[h.role])
            h.setVisible(show)

    def paint(self, painter: QPainter, option, widget=None):
        if self.isSelected():
            pen = QPen(SEL_COLOR, 0)
            pen.setCosmetic(True)
            pen.setWidthF(1.6)
            painter.setPen(pen)
        elif self._hover or self.canvas.show_outlines:
            pen = QPen(HOVER_COLOR, 0, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
        else:
            return
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect())

    def hoverEnterEvent(self, e):  # noqa: N802
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):  # noqa: N802
        self._hover = False
        self.update()

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.canvas.dragging:
            if not (QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier):
                return QPointF(self.canvas.snap_value(value.x()), self.canvas.snap_value(value.y()))
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            for f in self.canvas.frames.values():
                f.layout_handles()
        return super().itemChange(change, value)

    def mousePressEvent(self, e):  # noqa: N802
        if self.locked:
            e.ignore()
            return
        super().mousePressEvent(e)


class Canvas(QGraphicsView):
    status = Signal(str)
    dropped_files = Signal(list, QPointF)

    def __init__(self, doc: LayoutDocument):
        super().__init__()
        self.doc = doc
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setBackgroundBrush(QColor("#0b0d12"))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setAcceptDrops(True)
        self.preview = QGraphicsPixmapItem()
        self.preview.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.preview.setZValue(-10)
        self.scene().addItem(self.preview)
        self.border = QGraphicsRectItem()
        pen = QPen(QColor("#2f3644"), 0)
        pen.setCosmetic(True)
        self.border.setPen(pen)
        self.border.setZValue(-5)
        self.scene().addItem(self.border)
        self.frames: dict[str, Frame] = {}
        self.grid = 8
        self.snap = True
        self.show_outlines = False
        self.dragging = False
        self.auto_fit = True
        self._before: dict | None = None
        self._syncing = False
        self.scene().selectionChanged.connect(self._scene_selection)
        doc.changed.connect(self._doc_changed)
        doc.selectionChanged.connect(self._doc_selection)

    # ------------------------------------------------------------- preview
    def set_preview(self, image: QImage) -> None:
        self.preview.setPixmap(QPixmap.fromImage(image))
        cw, ch = self.doc.canvas
        if image.width() and (image.width() != cw or image.height() != ch):
            self.preview.setScale(cw / image.width())

    # ------------------------------------------------------------ geometry
    def snap_value(self, v: float) -> float:
        if not self.snap or self.grid <= 1:
            return round(v)
        return round(v / self.grid) * self.grid

    def fit(self) -> None:
        self.auto_fit = True
        cw, ch = self.doc.canvas
        self.fitInView(QRectF(-12, -12, cw + 24, ch + 24), Qt.AspectRatioMode.KeepAspectRatio)

    def zoom(self, factor: float) -> None:
        self.auto_fit = False
        self.scale(factor, factor)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self.auto_fit:
            self.fit()

    def wheelEvent(self, e):  # noqa: N802
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
        else:
            super().wheelEvent(e)

    # ---------------------------------------------------------- doc → view
    def _doc_changed(self, kind: str) -> None:
        cw, ch = self.doc.canvas
        self.scene().setSceneRect(QRectF(-400, -400, cw + 800, ch + 800))
        self.border.setRect(QRectF(0, 0, cw, ch))
        if kind in ("all", "widgets", "geometry", "props", "layout"):
            self._rebuild()
        if kind == "all" and self.auto_fit:
            self.fit()

    def _rebuild(self) -> None:
        self._syncing = True
        ids = [w["id"] for w in self.doc.widgets()]
        for wid in list(self.frames):
            if wid not in ids:
                self.scene().removeItem(self.frames.pop(wid))
        for z, data in enumerate(self.doc.widgets()):
            f = self.frames.get(data["id"])
            if f is None:
                f = self.frames[data["id"]] = Frame(self, data)
                self.scene().addItem(f)
            else:
                f.update_from(data)
            f.setZValue(z)
            f.setSelected(data["id"] in self.doc.selection)
        self._syncing = False

    def _doc_selection(self, ids: list[str]) -> None:
        self._syncing = True
        for wid, f in self.frames.items():
            f.setSelected(wid in ids)
        self._syncing = False
        for f in self.frames.values():
            f.layout_handles()

    def _scene_selection(self) -> None:
        if self._syncing:
            return
        ids = [f.wid for f in self.frames.values() if f.isSelected()]
        order = [w["id"] for w in self.doc.widgets()]
        self.doc.set_selection(sorted(ids, key=order.index))

    # ---------------------------------------------------------- view → doc
    def begin_edit(self) -> None:
        self._before = self.doc.snapshot()

    def live_sync(self) -> None:
        """Write frame geometry into the layout without an undo step (while dragging)."""
        for wid, f in self.frames.items():
            w = self.doc.widget(wid)
            if w is None:
                continue
            r = f.rect()
            w["x"], w["y"] = round(f.pos().x()), round(f.pos().y())
            w["w"], w["h"] = max(1, round(r.width())), max(1, round(r.height()))
        self.doc.changed.emit("live")
        sel = self.doc.selected()
        if len(sel) == 1:
            s = sel[0]
            self.status.emit(f"{s.get('name')}: x {s['x']}  y {s['y']}  w {s['w']}  h {s['h']}")

    def end_edit(self, label: str) -> None:
        if self._before is None:
            return
        self.live_sync()
        before, self._before = self._before, None
        self.doc.commit(before, label, "geometry")

    def mousePressEvent(self, e):  # noqa: N802
        item = self.itemAt(e.position().toPoint())
        if isinstance(item, Frame) and e.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.begin_edit()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):  # noqa: N802
        super().mouseMoveEvent(e)
        if self.dragging and e.buttons() & Qt.MouseButton.LeftButton:
            self.live_sync()

    def mouseReleaseEvent(self, e):  # noqa: N802
        super().mouseReleaseEvent(e)
        if self.dragging:
            self.dragging = False
            self.end_edit("Move")

    def nudge(self, dx: int, dy: int) -> None:
        sel = self.doc.selected()
        if not sel:
            return
        before = self.doc.snapshot()
        for w in sel:
            if not w.get("locked"):
                w["x"] += dx
                w["y"] += dy
        self.doc.commit(before, "Nudge", "geometry", merge_key="nudge")

    # ------------------------------------------------------- context, drops
    def contextMenuEvent(self, e):  # noqa: N802
        item = self.itemAt(e.pos())
        if isinstance(item, Handle):
            item = item.frame
        if isinstance(item, Frame) and item.wid not in self.doc.selection:
            self.doc.set_selection([item.wid])
        win = self.window()
        menu = QMenu(self)
        if self.doc.selection and hasattr(win, "actions_for_context"):
            for a in win.actions_for_context():
                menu.addAction(a) if a is not None else menu.addSeparator()
        elif hasattr(win, "add_menu"):
            menu.addMenu(win.add_menu)
            if hasattr(win, "act_paste"):
                menu.addAction(win.act_paste)
        menu.exec(e.globalPos())

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.dropped_files.emit(paths, self.mapToScene(e.position().toPoint()))
            e.acceptProposedAction()


def widgets_to_clipboard(widgets: list[dict[str, Any]]) -> str:
    return json.dumps({"turzx-widgets": widgets}, ensure_ascii=False)


def widgets_from_clipboard(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    items = data.get("turzx-widgets") if isinstance(data, dict) else None
    return [w for w in items or [] if isinstance(w, dict) and "type" in w]


IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXT
