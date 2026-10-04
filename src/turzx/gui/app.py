"""TURZX Studio main window."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, config, service
from ..render.layout import normalize
from ..render.widgets import WIDGETS
from ..sensors import Sensors
from .canvas import Canvas, is_image, widgets_from_clipboard, widgets_to_clipboard
from .document import LayoutDocument
from .inspector import Inspector
from .panels import LayersPanel, LibraryPanel, ScreenPanel
from .preview import PreviewEngine, ThumbnailWorker
from .qtutil import apply_dark_theme, load_app_icon

TEMPLATE_HELP = """
<h3>Live text templates</h3>
<p>Any text field can contain placeholders that are replaced with live data:</p>
<table cellpadding="3">
<tr><td><code>{cpu.util}</code></td><td>value with a sensible default format</td></tr>
<tr><td><code>{cpu.temp:.1f}</code></td><td>Python format spec (here: one decimal)</td></tr>
<tr><td><code>{net.down|rate}</code></td><td>filter: <code>rate bytes ibytes duration int upper lower title</code></td></tr>
<tr><td><code>{mem.used|gib:.1f}</code></td><td>unit filter <code>gb gib mb mib k</code>, then a format spec</td></tr>
<tr><td><code>{time:%H:%M:%S}</code></td><td>local time with <code>strftime</code> codes</td></tr>
<tr><td><code>{{</code> <code>}}</code></td><td>literal braces</td></tr>
</table>
<p>Use the <b>{ }</b> button next to a text field to insert any metric. Ring gauges also accept <code>{value}</code>
for their own metric.</p>
<h3>Colours</h3>
<p>Colours are <code>#rrggbb</code>, <code>#rrggbbaa</code> (with transparency) or a palette reference such as
<code>@cpu</code> or <code>@accent/0.5</code> (50% opacity). Change the palette (select nothing) and every widget that
references it follows.</p>
<h3>Canvas</h3>
<p>Drag to move, drag the handles to resize; hold <b>Shift</b> to move freely off the grid. Arrow keys nudge by 1 px
(Shift: 10 px). Ctrl+wheel zooms. Drop image files onto the canvas to add them; drop a layout .json to import it.
Copy widgets with Ctrl+C and paste them into any other layout.</p>
"""


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowIcon(load_app_icon())
        self.resize(1500, 900)
        self.sensors = Sensors(interval=1.0).start()
        self.doc = LayoutDocument()
        self.engine = PreviewEngine(self.sensors, scale=1)
        self.canvas = Canvas(self.doc)
        self.setCentralWidget(self.canvas)
        self.screen_panel = ScreenPanel()
        self.inspector = Inspector(self.doc, self.screen_panel.screen_size)
        self.library = LibraryPanel()
        self.layers = LayersPanel(self.doc)
        self._thumb_worker: ThumbnailWorker | None = None

        self._build_docks()
        self._build_actions()
        self._build_toolbar()
        self._build_menus()
        self.statusBar().showMessage("Ready", 3000)
        self.status_right = QLabel()
        self.statusBar().addPermanentWidget(self.status_right)

        # signals
        self.engine.ready.connect(self.canvas.set_preview)
        self.doc.changed.connect(self._doc_changed)
        self.doc.loaded.connect(self._update_title)
        self.doc.forked.connect(self._forked)
        self.doc.saved.connect(lambda _id: self._thumb_timer.start())
        self.doc.undo.cleanChanged.connect(lambda _c: self._update_title())
        self.canvas.status.connect(lambda m: self.statusBar().showMessage(m, 2000))
        self.canvas.dropped_files.connect(self._dropped)
        self.library.openRequested.connect(self.open_layout)
        self.library.activateRequested.connect(self.activate_layout)
        self.library.action.connect(self._library_action)
        self.screen_panel.livePreviewChanged.connect(lambda on: on or config.clear_preview())

        self._refresh_timer = QTimer(self, interval=700)  # live data in the canvas
        self._refresh_timer.timeout.connect(self._request_preview)
        self._refresh_timer.start()
        self._push_timer = QTimer(self, singleShot=True, interval=120)  # live edits to the screen
        self._push_timer.timeout.connect(self._push_preview)
        self._heartbeat = QTimer(self, interval=2000)
        self._heartbeat.timeout.connect(self._push_preview)
        self._heartbeat.start()
        self._thumb_timer = QTimer(self, singleShot=True, interval=800)
        self._thumb_timer.timeout.connect(self._render_current_thumb)
        self._status_timer = QTimer(self, interval=1500)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start()

        start = config.Settings.load().layout
        self.doc.open(start if config.find_layout(start) else "classic")
        self.library.refresh(self.doc.layout_id, config.Settings.load().layout)
        QTimer.singleShot(1500, self._render_thumbs)
        QTimer.singleShot(600, self._first_run)

    # ---------------------------------------------------------------- build
    def _build_docks(self) -> None:
        self.setDockOptions(QMainWindow.DockOption.AnimatedDocks | QMainWindow.DockOption.AllowTabbedDocks)
        d1 = QDockWidget("Layouts", self)
        d1.setObjectName("layouts")
        d1.setWidget(self.library)
        d2 = QDockWidget("Layers", self)
        d2.setObjectName("layers")
        d2.setWidget(self.layers)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, d1)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, d2)
        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(self.inspector, "Properties")
        self.right_tabs.addTab(self.screen_panel, "Screen")
        d3 = QDockWidget("Inspector", self)
        d3.setObjectName("inspector")
        d3.setWidget(self.right_tabs)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, d3)
        self.resizeDocks([d1, d2], [460, 340], Qt.Orientation.Vertical)
        self.resizeDocks([d1, d3], [340, 390], Qt.Orientation.Horizontal)
        self.dock_actions = [d1.toggleViewAction(), d2.toggleViewAction(), d3.toggleViewAction()]

    def _act(self, text: str, slot, shortcut=None, tip: str = "") -> QAction:
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        self.addAction(a)
        return a

    def _build_actions(self) -> None:
        d = self.doc
        self.act_undo = d.undo.createUndoAction(self, "Undo")
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_redo = d.undo.createRedoAction(self, "Redo")
        self.act_redo.setShortcuts([QKeySequence.StandardKey.Redo, QKeySequence("Ctrl+Y")])
        self.addAction(self.act_undo)
        self.addAction(self.act_redo)
        self.act_copy = self._act("Copy", self.copy, QKeySequence.StandardKey.Copy)
        self.act_cut = self._act("Cut", self.cut, QKeySequence.StandardKey.Cut)
        self.act_paste = self._act("Paste", self.paste, QKeySequence.StandardKey.Paste)
        self.act_dup = self._act("Duplicate", lambda: d.duplicate(d.selection), "Ctrl+D")
        self.act_del = self._act("Delete", lambda: d.remove(d.selection), QKeySequence.StandardKey.Delete)
        self.act_all = self._act("Select all", lambda: d.set_selection([w["id"] for w in d.widgets()]),
                                 QKeySequence.StandardKey.SelectAll)
        self.act_front = self._act("Bring to front", lambda: d.reorder(d.selection, "front"), "Ctrl+Shift+]")
        self.act_up = self._act("Bring forward", lambda: d.reorder(d.selection, "up"), "Ctrl+]")
        self.act_down = self._act("Send backward", lambda: d.reorder(d.selection, "down"), "Ctrl+[")
        self.act_back = self._act("Send to back", lambda: d.reorder(d.selection, "back"), "Ctrl+Shift+[")
        self.act_lock = self._act("Lock / unlock", self._toggle_lock, "Ctrl+L")
        self.act_hide = self._act("Hide / show", self._toggle_hide, "Ctrl+H")
        self.act_use = self._act("Show on screen", lambda: self.activate_layout(self.doc.layout_id), "Ctrl+Return",
                                 "Make this layout the one the screen shows")
        self.act_fit = self._act("Zoom to fit", self.canvas.fit, "Ctrl+0")
        self.act_zin = self._act("Zoom in", lambda: self.canvas.zoom(1.25), QKeySequence.StandardKey.ZoomIn)
        self.act_zout = self._act("Zoom out", lambda: self.canvas.zoom(0.8), QKeySequence.StandardKey.ZoomOut)
        self.act_snap = self._act("Snap to grid", lambda on: setattr(self.canvas, "snap", on), "Ctrl+G")
        self.act_snap.setCheckable(True)
        self.act_snap.setChecked(True)
        self.act_outlines = self._act("Show all outlines", self._toggle_outlines, "Ctrl+Shift+O")
        self.act_outlines.setCheckable(True)
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            for mod, k in (("", 1), ("Shift+", 10)):
                sc = QShortcut(QKeySequence(mod + key), self.canvas)
                sc.activated.connect(lambda dx=dx, dy=dy, k=k: self.canvas.nudge(dx * k, dy * k))
        self.align_actions = []
        for how, text in (("left", "Align left"), ("hcenter", "Align centre"), ("right", "Align right"),
                          ("top", "Align top"), ("vcenter", "Align middle"), ("bottom", "Align bottom"),
                          ("hdistribute", "Distribute horizontally"), ("vdistribute", "Distribute vertically")):
            self.align_actions.append(self._act(text, lambda _=False, h=how: d.align(d.selection, h)))

        self.add_menu = QMenu("Add widget", self)
        cats: dict[str, QMenu] = {}
        for cls in WIDGETS.values():
            if cls.category not in cats:
                cats[cls.category] = self.add_menu.addMenu(cls.category)
            act = cats[cls.category].addAction(cls.title, lambda _=False, t=cls.type: self.add_widget(t))
            act.setToolTip(cls.description)
        cats["Basic"].setToolTipsVisible(True)
        quick = self.add_menu.addMenu("Quick add")
        for text, type_, props in (
            ("CPU card", "card", {"kind": "cpu"}), ("GPU card", "card", {"kind": "gpu"}),
            ("Memory card", "card", {"kind": "memory"}), ("Network + disk card", "card", {"kind": "io"}),
            ("Clock card", "card", {"kind": "clock"}),
            ("CPU ring", "ring", {"metric": "cpu.util", "label": "CPU", "color": "@cpu", "value": "{value:.0f}%"}),
            ("GPU ring", "ring", {"metric": "gpu.util", "label": "GPU", "color": "@gpu", "value": "{value:.0f}%"}),
            ("CPU temperature ring", "ring", {"metric": "cpu.temp", "label": "CPU °C", "value": "{value:.0f}°",
                                              "warn_at": 70, "hot_at": 85}),
            ("Network graph", "graph", {"metric": "net.down", "metric2": "net.up", "color": "@net", "floor": 125000}),
            ("Date", "text", {"text": "{time:%A, %d %B}", "size": 32, "align": "center"}),
            ("Now playing", "text", {"text": "{media.title} · {media.artist}", "size": 28, "hide_missing": True}),
        ):
            quick.addAction(text, lambda _=False, t=type_, p=props: self.add_widget(t, **p))

    def actions_for_context(self) -> list:
        return [self.act_cut, self.act_copy, self.act_paste, self.act_dup, self.act_del, None,
                self.act_front, self.act_up, self.act_down, self.act_back, None, self.act_lock, self.act_hide]

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Main")
        tb.setObjectName("main")
        tb.setMovable(False)
        add = QToolButton()
        add.setText("＋ Add")
        add.setMenu(self.add_menu)
        add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tb.addWidget(add)
        tb.addSeparator()
        for a in (self.act_undo, self.act_redo):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.act_dup, self.act_del):
            tb.addAction(a)
        arrange = QToolButton()
        arrange.setText("Arrange")
        m = QMenu(arrange)
        for a in (self.act_front, self.act_up, self.act_down, self.act_back):
            m.addAction(a)
        m.addSeparator()
        for a in self.align_actions:
            m.addAction(a)
        arrange.setMenu(m)
        arrange.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tb.addWidget(arrange)
        tb.addSeparator()
        tb.addAction(self.act_snap)
        grid = QComboBox()
        for g in (1, 2, 4, 8, 10, 16, 20, 32):
            grid.addItem(f"{g} px", g)
        grid.setCurrentIndex(3)
        grid.setToolTip("Grid size")
        grid.currentIndexChanged.connect(lambda i: setattr(self.canvas, "grid", grid.itemData(i)))
        tb.addWidget(grid)
        tb.addSeparator()
        for a in (self.act_zout, self.act_fit, self.act_zin):
            tb.addAction(a)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding,
                             spacer.sizePolicy().verticalPolicy().Preferred)
        tb.addWidget(spacer)
        use = QToolButton()
        use.setDefaultAction(self.act_use)
        use.setStyleSheet("QToolButton { background: #38bdf8; color: #06121a; font-weight: 600; padding: 5px 14px; }"
                          "QToolButton:hover { background: #5fcbfa; }")
        tb.addWidget(use)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction("New layout…", lambda: self._library_action("new"), QKeySequence.StandardKey.New)
        m.addAction("Duplicate layout", lambda: self._library_action("duplicate"))
        m.addAction("Import layout…", lambda: self._library_action("import"), "Ctrl+I")
        m.addAction("Export layout…", lambda: self._library_action("export"), "Ctrl+E")
        m.addAction("Save preview image…", self._save_png)
        m.addSeparator()
        m.addAction("Quit", self.close, QKeySequence.StandardKey.Quit)
        m = mb.addMenu("&Edit")
        for a in (self.act_undo, self.act_redo, None, self.act_cut, self.act_copy, self.act_paste, self.act_dup,
                  self.act_del, None, self.act_all, self.act_lock, self.act_hide):
            m.addSeparator() if a is None else m.addAction(a)
        mb.addMenu(self.add_menu)
        m = mb.addMenu("&Arrange")
        for a in (self.act_front, self.act_up, self.act_down, self.act_back, None, *self.align_actions):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&View")
        for a in (self.act_fit, self.act_zin, self.act_zout, None, self.act_snap, self.act_outlines, None,
                  *self.dock_actions):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Screen")
        m.addAction(self.act_use)
        m.addAction("Screen settings", lambda: self.right_tabs.setCurrentWidget(self.screen_panel))
        m.addSeparator()
        m.addAction("Start service", self.screen_panel._start)
        m.addAction("Stop service", self.screen_panel._stop)
        m.addAction("Restart service", self.screen_panel._restart)
        m.addAction("Install udev rule…", self.screen_panel._install_udev)
        m = mb.addMenu("&Help")
        m.addAction("Templates and shortcuts", self._help)
        m.addAction("About TURZX Studio", self._about)

    # ------------------------------------------------------------ preview
    def _doc_changed(self, kind: str) -> None:
        self._request_preview()
        self._push_timer.start()
        if kind in ("all", "layout"):
            self._update_title()

    def _request_preview(self) -> None:
        self.engine.request(self.doc.layout, self.doc.canvas)

    def _push_preview(self) -> None:
        if self.screen_panel.live.isChecked() and config.read_status():
            try:
                config.write_preview(self.doc.layout)
            except OSError:
                pass

    def _update_status(self) -> None:
        st = self.screen_panel.daemon_status
        if st is None:
            self.status_right.setText("● service stopped  ")
            self.status_right.setStyleSheet("color: #facc15")
        elif st.get("state") == "running":
            self.status_right.setText(f"● {st.get('model')} · {st.get('fps')} fps  ")
            self.status_right.setStyleSheet("color: #4ade80")
        else:
            self.status_right.setText(f"● {st.get('state')}  ")
            self.status_right.setStyleSheet("color: #facc15")

    def _update_title(self) -> None:
        name = self.doc.layout.get("name", "Untitled")
        self.setWindowTitle(f"{name}{' (built-in, read-only)' if self.doc.builtin else ''} — TURZX Studio")

    # ------------------------------------------------------------ layouts
    def open_layout(self, layout_id: str) -> None:
        if layout_id and layout_id != self.doc.layout_id:
            self.doc.open(layout_id)
            self._request_preview()
            self._push_preview()

    def activate_layout(self, layout_id: str) -> None:
        if not layout_id:
            return
        self.doc.save_now()
        self.screen_panel.set_active_layout(layout_id)
        self.library.refresh(self.doc.layout_id, layout_id)
        ref = config.find_layout(layout_id)
        self.statusBar().showMessage(f"The screen now shows “{ref.name if ref else layout_id}”", 4000)
        if config.read_status() is None:
            self.statusBar().showMessage("Saved as the active layout. Start the service to show it.", 6000)

    def _forked(self, new_id: str) -> None:
        self.library.refresh(new_id, config.Settings.load().layout)
        self._update_title()
        self.statusBar().showMessage("Built-in layouts are read-only, so your edits are saved as a custom copy: "
                                     f"“{self.doc.layout.get('name')}”", 8000)

    def _library_action(self, action: str) -> None:
        d = self.doc
        if action == "new":
            name, ok = QInputDialog.getText(self, "New layout", "Name:", text="My layout")
            if not ok or not name.strip():
                return
            size = self.screen_panel.screen_size() or (1920, 462)
            lay = config.blank_layout(name.strip(), size)
            new_id = config.save_user_layout(lay)
            self.library.refresh(new_id, config.Settings.load().layout)
            self.doc.open(new_id)
        elif action == "duplicate":
            d.save_now()
            lay = dict(d.layout)
            lay["name"] = f"{lay.get('name', 'Layout')} copy"
            new_id = config.save_user_layout(lay)
            self.library.refresh(new_id, config.Settings.load().layout)
            self.doc.open(new_id)
            self._render_thumbs([new_id])
        elif action == "rename":
            if d.builtin:
                QMessageBox.information(self, "Rename", "Built-in layouts can't be renamed. Duplicate it first.")
                return
            name, ok = QInputDialog.getText(self, "Rename layout", "Name:", text=d.layout.get("name", ""))
            if ok and name.strip():
                d.set_layout_value(("name",), name.strip())
                d.save_now()
                self.library.refresh(d.layout_id, config.Settings.load().layout)
        elif action == "delete":
            target = self.library.current_id() or d.layout_id
            ref = config.find_layout(target)
            if ref is None or ref.builtin:
                QMessageBox.information(self, "Delete", "Built-in layouts can't be deleted.")
                return
            if QMessageBox.question(self, "Delete layout", f"Delete “{ref.name}”? This can't be undone.") \
                    != QMessageBox.StandardButton.Yes:
                return
            config.delete_user_layout(target)
            settings = config.Settings.load()
            if settings.layout == target:
                self.screen_panel.set_active_layout("classic")
            if d.layout_id == target:
                d.layout_id = ""
                d.open("classic")
            self.library.refresh(d.layout_id, config.Settings.load().layout)
        elif action == "import":
            path, _ = QFileDialog.getOpenFileName(self, "Import layout", str(Path.home()), "TURZX layout (*.json)")
            if path:
                self.import_layout(path)
        elif action == "export":
            d.save_now()
            default = str(Path.home() / f"{config.slugify(d.layout.get('name', 'layout'))}.json")
            path, _ = QFileDialog.getSaveFileName(self, "Export layout", default, "TURZX layout (*.json)")
            if path:
                config.write_json(Path(path), d.layout)
                self.statusBar().showMessage(f"Exported to {path}", 4000)

    def import_layout(self, path: str) -> None:
        data = config.read_json(Path(path))
        if not isinstance(data, dict) or "widgets" not in data:
            QMessageBox.warning(self, "Import", f"{path} is not a TURZX layout file.")
            return
        lay = normalize(data)
        new_id = config.save_user_layout(lay)
        self.library.refresh(new_id, config.Settings.load().layout)
        self.doc.open(new_id)
        self._render_thumbs([new_id])
        self.statusBar().showMessage(f"Imported “{lay.get('name')}”", 4000)

    # ---------------------------------------------------------- thumbnails
    def _render_thumbs(self, ids: list[str] | None = None) -> None:
        items = []
        for r in config.list_layouts():
            if ids is None or r.id in ids:
                try:
                    items.append((r.id, config.load_layout(str(r.path))))
                except (OSError, ValueError):
                    pass
        worker = ThumbnailWorker(self.sensors, items)
        worker.thumb.connect(self.library.set_thumb)
        worker.finished.connect(worker.deleteLater)
        self._thumb_worker = worker
        worker.start()

    def _render_current_thumb(self) -> None:
        if not self.doc.builtin and self.doc.layout_id:
            self._render_thumbs([self.doc.layout_id])

    # ------------------------------------------------------------- editing
    def add_widget(self, type_: str, **props) -> None:
        self.doc.add_widget(type_, **props)
        self.right_tabs.setCurrentWidget(self.inspector)

    def copy(self) -> None:
        if self.doc.selected():
            QGuiApplication.clipboard().setText(widgets_to_clipboard(self.doc.selected()))

    def cut(self) -> None:
        self.copy()
        self.doc.remove(self.doc.selection)

    def paste(self) -> None:
        items = widgets_from_clipboard(QGuiApplication.clipboard().text())
        if items:
            self.doc.insert_widgets(items)

    def _toggle_lock(self) -> None:
        for w in self.doc.selected():
            self.doc.set_attr(w["id"], "locked", not w.get("locked"), "widgets")

    def _toggle_hide(self) -> None:
        for w in self.doc.selected():
            self.doc.set_attr(w["id"], "visible", not w.get("visible", True), "widgets")

    def _toggle_outlines(self, on: bool) -> None:
        self.canvas.show_outlines = on
        self.canvas.scene().update()

    def _dropped(self, paths: list[str], pos: QPointF) -> None:
        for p in paths:
            if p.lower().endswith(".json"):
                self.import_layout(p)
            elif is_image(p):
                try:
                    with Image.open(p) as im:
                        iw, ih = im.size
                except OSError:
                    continue
                cw, ch = self.doc.canvas
                scale = min(1.0, (ch * 0.9) / ih, (cw * 0.5) / iw)
                w, h = max(16, round(iw * scale)), max(16, round(ih * scale))
                wid = self.doc.add_widget("image", round(pos.x() - w / 2), round(pos.y() - h / 2), path=p,
                                          fit="contain")
                self.doc.set_attr(wid, "w", w, "geometry")
                self.doc.set_attr(wid, "h", h, "geometry")
                self.doc.set_attr(wid, "name", os.path.basename(p), "widgets")

    def _save_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save preview", str(Path.home() / "turzx-preview.png"),
                                              "PNG (*.png)")
        if path:
            from ..render import RenderContext, Renderer

            Renderer(RenderContext(self.sensors)).render(self.doc.layout, self.doc.canvas).save(path)
            self.statusBar().showMessage(f"Saved {path}", 4000)

    # -------------------------------------------------------------- dialogs
    def _help(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Templates and shortcuts")
        dlg.resize(640, 560)
        v = QVBoxLayout(dlg)
        t = QTextBrowser()
        t.setHtml(TEMPLATE_HELP)
        v.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        dlg.exec()

    def _about(self) -> None:
        QMessageBox.about(self, "About TURZX Studio",
                          f"<h3>TURZX Studio {__version__}</h3><p>Open-source Linux software for TURZX / Turing "
                          "smart screens: a reverse-engineered USB driver, a background service and this layout "
                          "editor.</p><p><a href='https://github.com/sjcsoftware/turzx-linux'>github.com/sjcsoftware/turzx-linux</a>"
                          "</p><p>License: GPL-3.0-or-later.</p>")

    def _first_run(self) -> None:
        s = config.Settings.load()
        if config.read_status() is not None or s.extra.get("asked_autostart"):
            return
        box = QMessageBox(self)
        box.setWindowTitle("Run TURZX in the background?")
        box.setText("<b>The screen is driven by a small background service.</b>")
        box.setInformativeText("Start it automatically at login, so the screen works without this window open and "
                               "picks up the screen whenever it is plugged in?")
        auto = box.addButton("Start at login", QMessageBox.ButtonRole.AcceptRole)
        once = box.addButton("Just for now", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is auto:
            err = service.install()
            if err:
                QMessageBox.warning(self, "Autostart", err)
        elif box.clickedButton() is once:
            self.screen_panel._start()
        s = config.Settings.load()
        s.extra["asked_autostart"] = True
        s.save()
        QTimer.singleShot(1500, self.screen_panel.poll)

    def closeEvent(self, e):  # noqa: N802
        self.doc.save_now()
        self.screen_panel._flush()
        config.clear_preview()
        self.engine.stop()
        self.sensors.stop()
        super().closeEvent(e)


def main() -> int:
    QGuiApplication.setDesktopFileName("turzx-studio")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("TURZX Studio")
    app.setOrganizationName("turzx")
    apply_dark_theme(app)
    app.setWindowIcon(load_app_icon())
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
