"""Side panels: layout library, layers, and screen / service controls."""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTimeEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import config, service
from ..config import Settings
from ..render.widgets import WIDGETS
from ..screen import Orientation
from .document import LayoutDocument


# ------------------------------------------------------------------- library
class LibraryPanel(QWidget):
    openRequested = Signal(str)
    activateRequested = Signal(str)
    action = Signal(str)  # new | duplicate | rename | delete | import | export

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        self.list = QListWidget()
        self.list.setIconSize(QSize(150, 36))
        self.list.setSpacing(2)
        self.list.itemClicked.connect(lambda it: self.openRequested.emit(it.data(Qt.ItemDataRole.UserRole)))
        self.list.itemDoubleClicked.connect(lambda it: self.activateRequested.emit(it.data(Qt.ItemDataRole.UserRole)))
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        for key, text, tip in (("new", "New", "New blank layout"), ("duplicate", "Copy", "Duplicate this layout"),
                               ("rename", "Rename", "Rename"), ("delete", "Delete", "Delete this custom layout")):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        row = QHBoxLayout()
        for key, text in (("import", "Import…"), ("export", "Export…")):
            b = QToolButton()
            b.setText(text)
            b.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            row.addWidget(b)
        row.addStretch(1)
        use = QPushButton("Show on screen")
        use.setObjectName("primary")
        use.setToolTip("Make the selected layout the one the screen shows")
        use.clicked.connect(lambda: self.current_id() and self.activateRequested.emit(self.current_id()))
        row.addWidget(use)
        v.addLayout(row)
        self._thumbs: dict[str, QImage] = {}

    def current_id(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def refresh(self, current: str, active: str) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        refs = config.list_layouts()
        last_builtin = None
        for r in refs:
            if last_builtin is not None and last_builtin and not r.builtin:
                sep = QListWidgetItem("My layouts")
                sep.setFlags(Qt.ItemFlag.NoItemFlags)
                sep.setForeground(QBrush(QColor("#8a93a6")))
                self.list.addItem(sep)
            elif last_builtin is None:
                sep = QListWidgetItem("Built-in" if r.builtin else "My layouts")
                sep.setFlags(Qt.ItemFlag.NoItemFlags)
                sep.setForeground(QBrush(QColor("#8a93a6")))
                self.list.addItem(sep)
            last_builtin = r.builtin
            label = ("● " if r.id == active else "") + r.name
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, r.id)
            it.setToolTip(("Shown on the screen now\n" if r.id == active else "") + f"{r.description}\n{r.canvas[0]}×{r.canvas[1]}  ·  {'built-in' if r.builtin else 'custom'}"
                          f"  ·  id: {r.id}")
            if r.id == active:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            if r.id in self._thumbs:
                it.setIcon(QIcon(QPixmap.fromImage(self._thumbs[r.id])))
            self.list.addItem(it)
            if r.id == current:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)

    def set_thumb(self, layout_id: str, img: QImage) -> None:
        self._thumbs[layout_id] = img
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == layout_id:
                it.setIcon(QIcon(QPixmap.fromImage(img)))


# -------------------------------------------------------------------- layers
class LayersPanel(QWidget):
    def __init__(self, doc: LayoutDocument):
        super().__init__()
        self.doc = doc
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Widget", "👁", "🔒"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.header().setStretchLastSection(False)
        self.tree.setColumnWidth(1, 28)
        self.tree.setColumnWidth(2, 28)
        self.tree.header().setSectionResizeMode(0, self.tree.header().ResizeMode.Stretch)
        self.tree.itemSelectionChanged.connect(self._selection)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._reordered))
        v.addWidget(self.tree, 1)
        row = QHBoxLayout()
        for text, tip, fn in (("▲", "Bring forward", lambda: doc.reorder(doc.selection, "up")),
                              ("▼", "Send backward", lambda: doc.reorder(doc.selection, "down")),
                              ("⤒", "Bring to front", lambda: doc.reorder(doc.selection, "front")),
                              ("⤓", "Send to back", lambda: doc.reorder(doc.selection, "back"))):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self._syncing = False
        doc.changed.connect(lambda kind: kind in ("all", "widgets") and self.refresh())
        doc.selectionChanged.connect(self._doc_selection)

    def refresh(self) -> None:
        self._syncing = True
        self.tree.clear()
        for data in reversed(self.doc.widgets()):  # top-most first
            cls = WIDGETS.get(data["type"])
            it = QTreeWidgetItem([data.get("name") or (cls.title if cls else data["type"]), "", ""])
            it.setData(0, Qt.ItemDataRole.UserRole, data["id"])
            it.setToolTip(0, cls.title if cls else data["type"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            it.setCheckState(1, Qt.CheckState.Checked if data.get("visible", True) else Qt.CheckState.Unchecked)
            it.setCheckState(2, Qt.CheckState.Checked if data.get("locked") else Qt.CheckState.Unchecked)
            if not data.get("visible", True):
                it.setForeground(0, QBrush(QColor("#6b7385")))
            self.tree.addTopLevelItem(it)
            it.setSelected(data["id"] in self.doc.selection)
        self._syncing = False

    def _ids(self) -> list[str]:
        return [self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.tree.topLevelItemCount())]

    def _selection(self) -> None:
        if self._syncing:
            return
        ids = [it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()]
        order = [w["id"] for w in self.doc.widgets()]
        self.doc.set_selection(sorted(ids, key=order.index))

    def _doc_selection(self, ids: list[str]) -> None:
        self._syncing = True
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            it.setSelected(it.data(0, Qt.ItemDataRole.UserRole) in ids)
        self._syncing = False

    def _item_changed(self, it: QTreeWidgetItem, col: int) -> None:
        if self._syncing:
            return
        wid = it.data(0, Qt.ItemDataRole.UserRole)
        if col == 1:
            self.doc.set_attr(wid, "visible", it.checkState(1) == Qt.CheckState.Checked, "widgets")
        elif col == 2:
            self.doc.set_attr(wid, "locked", it.checkState(2) == Qt.CheckState.Checked, "widgets")

    def _reordered(self) -> None:
        if not self._syncing:
            self.doc.set_order(list(reversed(self._ids())))


# -------------------------------------------------------------------- screen
class ScreenPanel(QScrollArea):
    """Device status and settings. Everything is saved to settings.json; the service applies it."""

    livePreviewChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(10, 10, 10, 10)
        self.settings = Settings.load()
        self._pending: dict = {}
        self._save_timer = QTimer(self, singleShot=True, interval=250)
        self._save_timer.timeout.connect(self._flush)

        title = QLabel("Screen")
        title.setObjectName("title")
        v.addWidget(title)
        self.status = QLabel("…")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.status)

        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bl = QVBoxLayout(self.banner)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        bl.addWidget(self.banner_text)
        brow = QHBoxLayout()
        self.banner_btn = QPushButton()
        self.banner_btn.setObjectName("primary")
        self.banner_btn.clicked.connect(self._banner_action)
        brow.addWidget(self.banner_btn)
        brow.addStretch(1)
        bl.addLayout(brow)
        self.banner.hide()
        v.addWidget(self.banner)
        self._banner_kind = ""

        box = QGroupBox("Background service")
        f = QVBoxLayout(box)
        row = QHBoxLayout()
        for text, fn in (("Start", self._start), ("Stop", self._stop), ("Restart", self._restart)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        f.addLayout(row)
        self.autostart = QCheckBox("Start automatically at login")
        self.autostart.toggled.connect(self._autostart)
        f.addWidget(self.autostart)
        self.live = QCheckBox("Show edits on the screen while editing")
        self.live.setChecked(bool(self.settings.extra.get("live_preview", True)))
        self.live.toggled.connect(self._live)
        f.addWidget(self.live)
        v.addWidget(box)

        box = QGroupBox("Display")
        form = QFormLayout(box)
        self.bright = QSlider(Qt.Orientation.Horizontal)
        self.bright.setRange(0, 100)
        self.bright.setValue(int(self.settings.brightness))
        self.bright_label = QLabel(f"{int(self.settings.brightness)}%")
        self.bright.valueChanged.connect(self._brightness)
        br = QHBoxLayout()
        br.addWidget(self.bright, 1)
        br.addWidget(self.bright_label)
        form.addRow("Brightness", br)
        self.orient = QComboBox()
        for o in Orientation:
            self.orient.addItem(o.value.replace("-", " ").capitalize(), o.value)
        self.orient.setCurrentIndex(max(0, self.orient.findData(self.settings.orientation)))
        self.orient.currentIndexChanged.connect(lambda i: self._set("orientation", self.orient.itemData(i)))
        form.addRow("Orientation", self.orient)
        self.fps = QDoubleSpinBox()
        self.fps.setRange(0.2, 30)
        self.fps.setSingleStep(0.5)
        self.fps.setValue(self.settings.fps)
        self.fps.setToolTip("Redraws per second. 1-2 is plenty for system stats; raise it for GIFs or mirroring.")
        self.fps.valueChanged.connect(lambda val: self._set("fps", float(val)))
        form.addRow("Frame rate", self.fps)
        self.quality = QSpinBox()
        self.quality.setRange(30, 100)
        self.quality.setValue(self.settings.quality)
        self.quality.valueChanged.connect(lambda val: self._set("quality", int(val)))
        form.addRow("JPEG quality", self.quality)
        self.ss = QComboBox()
        self.ss.addItem("Smooth (2× supersampling)", 2)
        self.ss.addItem("Fast (no supersampling)", 1)
        self.ss.setCurrentIndex(0 if self.settings.supersample >= 2 else 1)
        self.ss.currentIndexChanged.connect(lambda i: self._set("supersample", int(self.ss.itemData(i))))
        form.addRow("Rendering", self.ss)
        self.on_exit = QComboBox()
        for val, text in (("off", "Turn off"), ("blank", "Blank (backlight on)"), ("keep", "Keep last frame")):
            self.on_exit.addItem(text, val)
        self.on_exit.setCurrentIndex(max(0, self.on_exit.findData(self.settings.on_exit)))
        self.on_exit.currentIndexChanged.connect(lambda i: self._set("on_exit", self.on_exit.itemData(i)))
        form.addRow("When stopped", self.on_exit)
        self.disk = QComboBox()
        self.disk.setEditable(True)
        try:
            import psutil

            self.disk.addItems(sorted({p.mountpoint for p in psutil.disk_partitions() if not p.mountpoint.startswith("/snap")}))
        except Exception:
            pass
        self.disk.setCurrentText(self.settings.disk)
        self.disk.currentTextChanged.connect(lambda t: self._set("disk", t or "/"))
        form.addRow("Disk to show", self.disk)
        v.addWidget(box)

        box = QGroupBox("Night mode")
        form = QFormLayout(box)
        self.night = QCheckBox("Dim the screen at night")
        self.night.setChecked(self.settings.night_enabled)
        self.night.toggled.connect(lambda b: self._set("night_enabled", b))
        form.addRow(self.night)
        self.night_from = QTimeEdit(QTime.fromString(self.settings.night_start, "HH:mm"))
        self.night_to = QTimeEdit(QTime.fromString(self.settings.night_end, "HH:mm"))
        for w, key in ((self.night_from, "night_start"), (self.night_to, "night_end")):
            w.setDisplayFormat("HH:mm")
            w.timeChanged.connect(lambda t, k=key: self._set(k, t.toString("HH:mm")))
        form.addRow("From", self.night_from)
        form.addRow("Until", self.night_to)
        self.night_b = QSpinBox()
        self.night_b.setRange(0, 100)
        self.night_b.setSuffix(" %")
        self.night_b.setValue(int(self.settings.night_brightness))
        self.night_b.valueChanged.connect(lambda val: self._set("night_brightness", float(val)))
        form.addRow("Brightness", self.night_b)
        v.addWidget(box)

        box = QGroupBox("Permissions")
        f = QVBoxLayout(box)
        self.perm = QLabel()
        self.perm.setWordWrap(True)
        f.addWidget(self.perm)
        self.perm_btn = QPushButton("Install udev rule (asks for your password)")
        self.perm_btn.clicked.connect(self._install_udev)
        f.addWidget(self.perm_btn)
        v.addWidget(box)
        v.addStretch(1)
        self.setWidget(body)

        self.daemon_status: dict | None = None
        self._timer = QTimer(self, interval=1500)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        self.poll()

    # --------------------------------------------------------- settings
    def _set(self, key: str, value) -> None:
        if getattr(self.settings, key) != value:
            setattr(self.settings, key, value)
            self._pending[key] = value
            self._save_timer.start()

    def _flush(self) -> None:
        """Merge only what changed here, so edits made elsewhere (e.g. `turzx use`) survive."""
        fresh = Settings.load()
        for key, value in self._pending.items():
            setattr(fresh, key, value)
        self._pending.clear()
        fresh.save()
        self.settings = fresh

    def set_active_layout(self, layout_id: str) -> None:
        self._set("layout", layout_id)
        self._flush()

    def _brightness(self, val: int) -> None:
        self.bright_label.setText(f"{val}%")
        self._set("brightness", float(val))

    def _live(self, on: bool) -> None:
        self._set("extra", {**self.settings.extra, "live_preview": on})
        self.livePreviewChanged.emit(on)

    # ---------------------------------------------------------- service
    def _start(self) -> None:
        st = service.status()
        if st["installed"]:
            err = service.start()
        else:
            subprocess.Popen([sys.executable, "-m", "turzx", "daemon"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            err = ""
        if err:
            self.status.setText(f"<span style='color:#f87171'>{err}</span>")
        QTimer.singleShot(1200, self.poll)

    def _stop(self) -> None:
        if service.status()["active"]:
            service.stop()
        else:
            st = config.read_status()
            if st:
                import os
                import signal

                try:
                    os.kill(int(st["pid"]), signal.SIGTERM)
                except OSError:
                    pass
        QTimer.singleShot(1200, self.poll)

    def _restart(self) -> None:
        if service.status()["active"]:
            service.restart()
        else:
            self._stop()
            QTimer.singleShot(1500, self._start)
        QTimer.singleShot(2500, self.poll)

    def _autostart(self, on: bool) -> None:
        st = service.status()
        if on and not (st["installed"] and st["enabled"]):
            if config.read_status() and not st["active"]:
                self._stop()  # replace an ad-hoc daemon with the managed service
                QTimer.singleShot(1500, lambda: service.install())
            else:
                err = service.install()
                if err:
                    self.status.setText(f"<span style='color:#f87171'>{err}</span>")
        elif not on and st["installed"]:
            service.uninstall()
        QTimer.singleShot(2000, self.poll)

    def _install_udev(self) -> None:
        ok, msg = service.install_udev_with_pkexec()
        self.perm.setText("udev rule installed. Unplug and replug the screen once." if ok else f"Failed: {msg}")
        QTimer.singleShot(500, self.poll)

    def _banner_action(self) -> None:
        if self._banner_kind == "start":
            self._start()
        elif self._banner_kind == "udev":
            self._install_udev()

    def _show_banner(self, kind: str, text: str, button: str) -> None:
        self._banner_kind = kind
        self.banner_text.setText(text)
        self.banner_btn.setText(button)
        self.banner.show()

    def poll(self) -> None:
        st = config.read_status()
        self.daemon_status = st
        svc = service.status()
        self.autostart.blockSignals(True)
        self.autostart.setChecked(svc["installed"] and svc["enabled"])
        self.autostart.blockSignals(False)
        access = service.device_access()
        udev = service.udev_installed()
        if access and not all(ok for _, ok in access):
            self.perm.setText("<b style='color:#f87171'>No permission to open the screen.</b> Install the udev rule.")
        elif udev:
            self.perm.setText("udev rule installed: any logged-in user can use the screen, and plugging it in "
                              "starts the service.")
        else:
            self.perm.setText("The screen is accessible, but the turzx udev rule is not installed. Install it so the "
                              "service starts when the screen is plugged in.")
        self.perm_btn.setVisible(not udev)

        if st is None:
            self.status.setText("<span style='color:#facc15'>●</span> Service not running: the screen is idle.")
            self._show_banner("start", "The background service is not running, so nothing is sent to the screen.",
                              "Start service")
            return
        state = st.get("state")
        if state == "running":
            self.status.setText(
                f"<span style='color:#4ade80'>●</span> <b>{st.get('model')}</b><br>"
                f"firmware {st.get('firmware')} · {st.get('fps')} fps<br>"
                f"showing <b>{st.get('layout')}</b>{' (live edit preview)' if st.get('preview') else ''}")
            self.banner.hide()
        elif state == "paused":
            self.status.setText("<span style='color:#facc15'>●</span> Paused: another turzx command is using the screen.")
            self.banner.hide()
        elif access and not all(ok for _, ok in access):
            self.status.setText("<span style='color:#f87171'>●</span> Permission denied opening the screen.")
            self._show_banner("udev", "Linux does not allow this user to open the screen yet.", "Fix permissions")
        else:
            self.status.setText(f"<span style='color:#facc15'>●</span> Waiting for a screen…<br>"
                                f"<span style='color:#8a93a6'>{st.get('error') or ''}</span>")
            self.banner.hide()

    def screen_size(self) -> tuple[int, int] | None:
        st = self.daemon_status
        if st and st.get("size"):
            return tuple(st["size"])  # type: ignore[return-value]
        try:
            from ..device import MODELS, find_devices

            devs = find_devices()
        except Exception:
            devs = []
        if not devs:
            return None
        w, h = MODELS[devs[0].idProduct].visible
        return (h, w) if Orientation(self.settings.orientation).is_landscape else (w, h)
