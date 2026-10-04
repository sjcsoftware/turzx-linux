"""The layout being edited: data, selection, undo history and autosave."""

from __future__ import annotations

import copy
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from .. import config
from ..render.layout import normalize
from ..render.widgets import WIDGETS, new_id


class _Snapshot(QUndoCommand):
    """Undo step holding the whole layout before and after an edit (layouts are small)."""

    def __init__(self, doc: LayoutDocument, before: dict, after: dict, label: str, merge_key: str | None):
        super().__init__(label)
        self.doc, self.before, self.after, self.merge_key = doc, before, after, merge_key
        self.time = time.monotonic()
        self._first = True

    def id(self) -> int:  # noqa: A003 - Qt API
        return 1 if self.merge_key else -1

    def mergeWith(self, other) -> bool:  # noqa: N802 - Qt API
        if not isinstance(other, _Snapshot) or other.merge_key != self.merge_key or other.time - self.time > 1.5:
            return False
        self.after = other.after
        self.time = other.time
        return True

    def redo(self) -> None:
        if self._first:  # the edit is already applied when pushed
            self._first = False
            return
        self.doc._restore(self.after)

    def undo(self) -> None:
        self.doc._restore(self.before)


class LayoutDocument(QObject):
    changed = Signal(str)  # "all" | "widgets" | "geometry" | "props" | "layout"
    selectionChanged = Signal(list)
    loaded = Signal()
    forked = Signal(str)  # new layout id, after a built-in was copied for editing
    saved = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.layout: dict[str, Any] = normalize({})
        self.layout_id = ""
        self.builtin = True
        self.selection: list[str] = []
        self.undo = QUndoStack(self)
        self.undo.setUndoLimit(200)
        self._save_timer = QTimer(self, singleShot=True, interval=500)
        self._save_timer.timeout.connect(self.save_now)

    # --------------------------------------------------------------- loading
    def open(self, layout_id: str) -> None:
        self.save_now()
        ref = config.find_layout(layout_id)
        if ref is None:
            return
        self.layout = config.load_layout(str(ref.path))
        self.layout_id, self.builtin = ref.id, ref.builtin
        self.selection = []
        self.undo.clear()
        self.loaded.emit()
        self.changed.emit("all")
        self.selectionChanged.emit([])

    # ------------------------------------------------------------ accessors
    def widgets(self) -> list[dict[str, Any]]:
        return self.layout["widgets"]

    def widget(self, wid: str) -> dict[str, Any] | None:
        return next((w for w in self.layout["widgets"] if w.get("id") == wid), None)

    def selected(self) -> list[dict[str, Any]]:
        return [w for w in (self.widget(i) for i in self.selection) if w is not None]

    @property
    def canvas(self) -> tuple[int, int]:
        c = self.layout.get("canvas") or [1920, 462]
        return int(c[0]), int(c[1])

    # ------------------------------------------------------------- editing
    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.layout)

    def commit(self, before: dict[str, Any], label: str, kind: str = "all", merge_key: str | None = None) -> None:
        """Record an edit already applied to ``self.layout``."""
        if before == self.layout:
            return
        self.undo.push(_Snapshot(self, before, self.snapshot(), label, merge_key))
        self.changed.emit(kind)
        self._touch()

    def _restore(self, data: dict[str, Any]) -> None:
        self.layout = copy.deepcopy(data)
        ids = {w["id"] for w in self.layout["widgets"]}
        self.selection = [i for i in self.selection if i in ids]
        self.changed.emit("all")
        self.selectionChanged.emit(self.selection)
        self._touch()

    def _touch(self) -> None:
        if self.builtin:
            self._fork()
        self._save_timer.start()

    def _fork(self) -> None:
        name = f"{self.layout.get('name', 'Layout')} (custom)"
        self.layout["name"] = name
        self.layout_id = config.unique_id(name)
        self.builtin = False
        config.save_user_layout(self.layout, self.layout_id)
        self.forked.emit(self.layout_id)

    def save_now(self) -> None:
        self._save_timer.stop()
        if self.layout_id and not self.builtin:
            config.save_user_layout(self.layout, self.layout_id)
            self.saved.emit(self.layout_id)

    def set_selection(self, ids: list[str]) -> None:
        if ids != self.selection:
            self.selection = list(ids)
            self.selectionChanged.emit(self.selection)

    def set_prop(self, wid: str, key: str, value: Any) -> None:
        w = self.widget(wid)
        if w is None or w.get("props", {}).get(key) == value:
            return
        before = self.snapshot()
        w.setdefault("props", {})[key] = value
        self.commit(before, f"Change {key}", "props", merge_key=f"{wid}:{key}")

    def set_attr(self, wid: str, key: str, value: Any, kind: str = "props") -> None:
        """Top-level widget attribute: name, visible, locked, x, y, w, h."""
        w = self.widget(wid)
        if w is None or w.get(key) == value:
            return
        before = self.snapshot()
        w[key] = value
        self.commit(before, f"Change {key}", kind, merge_key=f"{wid}:{key}")

    def set_layout_value(self, path: tuple[str, ...], value: Any) -> None:
        """Edit a layout-level value, e.g. ("background", "color") or ("name",)."""
        before = self.snapshot()
        node = self.layout
        for k in path[:-1]:
            node = node.setdefault(k, {})
        if node.get(path[-1]) == value:
            return
        node[path[-1]] = value
        self.commit(before, f"Change {'.'.join(path)}", "layout", merge_key="layout:" + ".".join(path))

    def add_widget(self, type_: str, x: float | None = None, y: float | None = None, **props) -> str:
        cls = WIDGETS[type_]
        cw, ch = self.canvas
        w, h = cls.default_size
        w, h = min(w, cw), min(h, ch)
        x = (cw - w) / 2 if x is None else x
        y = (ch - h) / 2 if y is None else y
        data = cls.create(round(x), round(y), w, h, **props)
        before = self.snapshot()
        self.layout["widgets"].append(data)
        self.commit(before, f"Add {cls.title}", "widgets")
        self.set_selection([data["id"]])
        return data["id"]

    def insert_widgets(self, items: list[dict[str, Any]], offset: float = 16) -> list[str]:
        before = self.snapshot()
        ids = []
        for item in items:
            d = copy.deepcopy(item)
            d["id"] = new_id()
            d["x"] = d.get("x", 0) + offset
            d["y"] = d.get("y", 0) + offset
            self.layout["widgets"].append(d)
            ids.append(d["id"])
        self.commit(before, "Paste" if offset else "Insert", "widgets")
        self.set_selection(ids)
        return ids

    def remove(self, ids: list[str]) -> None:
        if not ids:
            return
        before = self.snapshot()
        self.layout["widgets"] = [w for w in self.layout["widgets"] if w["id"] not in ids]
        self.selection = [i for i in self.selection if i not in ids]
        self.commit(before, "Delete", "widgets")
        self.selectionChanged.emit(self.selection)

    def duplicate(self, ids: list[str]) -> list[str]:
        return self.insert_widgets([w for w in self.layout["widgets"] if w["id"] in ids])

    def reorder(self, ids: list[str], where: str) -> None:
        """where: front | back | up | down"""
        ws = self.layout["widgets"]
        before = self.snapshot()
        sel = [w for w in ws if w["id"] in ids]
        rest = [w for w in ws if w["id"] not in ids]
        if where == "front":
            new = rest + sel
        elif where == "back":
            new = sel + rest
        else:
            new = list(ws)
            order = range(len(new) - 1, -1, -1) if where == "up" else range(len(new))
            for i in order:
                if new[i]["id"] in ids:
                    j = i + 1 if where == "up" else i - 1
                    if 0 <= j < len(new) and new[j]["id"] not in ids:
                        new[i], new[j] = new[j], new[i]
        self.layout["widgets"] = new
        self.commit(before, "Reorder", "widgets")

    def set_order(self, ids_bottom_to_top: list[str]) -> None:
        by_id = {w["id"]: w for w in self.layout["widgets"]}
        if list(by_id) == ids_bottom_to_top:
            return
        before = self.snapshot()
        self.layout["widgets"] = [by_id[i] for i in ids_bottom_to_top if i in by_id]
        self.commit(before, "Reorder", "widgets")

    def align(self, ids: list[str], how: str) -> None:
        ws = [w for w in self.layout["widgets"] if w["id"] in ids]
        if not ws:
            return
        before = self.snapshot()
        if len(ws) == 1:  # align to the canvas
            x0, y0, (x1, y1) = 0, 0, self.canvas
        else:
            x0 = min(w["x"] for w in ws)
            y0 = min(w["y"] for w in ws)
            x1 = max(w["x"] + w["w"] for w in ws)
            y1 = max(w["y"] + w["h"] for w in ws)
        for w in ws:
            if how == "left":
                w["x"] = x0
            elif how == "hcenter":
                w["x"] = round((x0 + x1 - w["w"]) / 2)
            elif how == "right":
                w["x"] = x1 - w["w"]
            elif how == "top":
                w["y"] = y0
            elif how == "vcenter":
                w["y"] = round((y0 + y1 - w["h"]) / 2)
            elif how == "bottom":
                w["y"] = y1 - w["h"]
        if how in ("hdistribute", "vdistribute") and len(ws) > 2:
            axis, size = ("x", "w") if how == "hdistribute" else ("y", "h")
            ws.sort(key=lambda w: w[axis])
            total = sum(w[size] for w in ws)
            span = (ws[-1][axis] + ws[-1][size]) - ws[0][axis]
            gap = (span - total) / (len(ws) - 1)
            pos = ws[0][axis]
            for w in ws:
                w[axis] = round(pos)
                pos += w[size] + gap
        self.commit(before, "Align", "geometry")
