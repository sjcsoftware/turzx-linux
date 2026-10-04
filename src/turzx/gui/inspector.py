"""Property inspector: editors generated from each widget's field schema."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..render.draw import color as parse_color
from ..render.draw import to_hex
from ..render.fields import Field
from ..render.theme import DEFAULT_PALETTE, PALETTE_LABELS, Palette
from ..render.widgets import WIDGETS
from ..sensors import METRICS
from .document import LayoutDocument
from .qtutil import swatch_icon

TIME_FORMATS = [
    ("{time:%H:%M}", "14:05"), ("{time:%H:%M:%S}", "14:05:09"), ("{time:%I:%M %p}", "02:05 PM"),
    ("{time:%A}", "Sunday"), ("{time:%a %d %b}", "Sun 04 Oct"), ("{time:%d %B %Y}", "04 October 2026"),
    ("{time:%Y-%m-%d}", "2026-10-04"), ("{time:%d.%m.%Y}", "04.10.2026"),
]
FILTERS = [("|rate", "network rate, e.g. 5.1 Mb/s"), ("|bytes", "1.2 GB"), ("|ibytes", "1.1 GiB"),
           ("|gib:.1f", "number of GiB"), ("|duration", "2h 5m"), ("|int", "rounded"), ("|upper", "UPPER CASE")]


# --------------------------------------------------------------------- editors
class ColorEdit(QWidget):
    """Swatch + text: '#rrggbb', '#rrggbbaa' or a palette reference '@name'."""

    edited = Signal(str)

    def __init__(self, palette_fn: Callable[[], Palette], optional: bool = False):
        super().__init__()
        self.palette_fn, self.optional = palette_fn, optional
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.btn = QToolButton()
        self.btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn.setMenu(self._menu())
        self.text = QLineEdit()
        self.text.setPlaceholderText("none" if optional else "#rrggbb or @name")
        self.text.editingFinished.connect(lambda: self._set(self.text.text().strip(), emit=True))
        lay.addWidget(self.btn)
        lay.addWidget(self.text, 1)

    def _menu(self) -> QMenu:
        m = QMenu(self)
        m.addAction("Custom colour…", self._pick)
        if self.optional:
            m.addAction("None", lambda: self._set("", emit=True))
        m.addSeparator()
        pal = self.palette_fn()
        for key in DEFAULT_PALETTE:
            m.addAction(swatch_icon("@" + key, pal), f"@{key}   {PALETTE_LABELS.get(key, key)}",
                        lambda k=key: self._set("@" + k, emit=True))
        return m

    def _pick(self) -> None:
        try:
            start = QColor(*self.palette_fn().resolve(self.text.text() or "#ffffff"))
        except ValueError:
            start = QColor("#ffffff")
        c = QColorDialog.getColor(start, self, "Colour", QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            self._set(to_hex((c.red(), c.green(), c.blue(), c.alpha())), emit=True)

    def _set(self, value: str, emit: bool = False) -> None:
        if value and not value.startswith("@"):
            try:
                parse_color(value)
            except ValueError:
                self.text.setStyleSheet("color: #ff6b6b")
                return
        self.text.setStyleSheet("")
        self.text.setText(value)
        self.btn.setIcon(swatch_icon(value or "#00000000", self.palette_fn()))
        if emit:
            self.edited.emit(value)

    def set_value(self, value: str) -> None:
        self._set(value or "")


class TemplateEdit(QWidget):
    edited = Signal(str)

    def __init__(self, multiline: bool = False):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.line = QLineEdit()
        self.line.editingFinished.connect(lambda: self.edited.emit(self.line.text()))
        btn = QToolButton()
        btn.setText("{ }")
        btn.setToolTip("Insert live data")
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setMenu(self._menu())
        lay.addWidget(self.line, 1)
        lay.addWidget(btn)

    def _menu(self) -> QMenu:
        m = QMenu(self)
        groups: dict[str, QMenu] = {}
        names = {"cpu": "CPU", "gpu": "GPU", "mem": "Memory", "swap": "Swap", "disk": "Disk", "net": "Network",
                 "battery": "Battery", "host": "System", "media": "Now playing"}
        for key, d in METRICS.items():
            g = key.split(".")[0]
            if g not in groups:
                groups[g] = m.addMenu(names.get(g, g))
            groups[g].addAction(f"{d.label}   {{{key}}}", lambda k=key: self._insert("{" + k + "}"))
        tm = m.addMenu("Time and date")
        for tpl, example in TIME_FORMATS:
            tm.addAction(f"{example}   {tpl}", lambda t=tpl: self._insert(t))
        fm = m.addMenu("Filters (type after a key)")
        for f, desc in FILTERS:
            fm.addAction(f"{f}   {desc}").setEnabled(False)
        return m

    def _insert(self, text: str) -> None:
        self.line.insert(text)
        self.edited.emit(self.line.text())

    def set_value(self, value: str) -> None:
        if self.line.text() != value:
            self.line.setText(value)


class FileEdit(QWidget):
    edited = Signal(str)

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.line = QLineEdit()
        self.line.setPlaceholderText("no file")
        self.line.editingFinished.connect(lambda: self.edited.emit(self.line.text()))
        btn = QToolButton()
        btn.setText("…")
        btn.clicked.connect(self._browse)
        lay.addWidget(self.line, 1)
        lay.addWidget(btn)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose image", self.line.text(),
                                              "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;All files (*)")
        if path:
            self.line.setText(path)
            self.edited.emit(path)

    def set_value(self, value: str) -> None:
        self.line.setText(value or "")


_FAMILIES: list[str] = []


def font_families() -> list[str]:
    if not _FAMILIES:
        _FAMILIES.extend(sorted(set(QFontDatabase.families())))
    return _FAMILIES


def make_editor(f: Field, value: Any, on_change: Callable[[Any], None], palette_fn) -> QWidget:
    k = f.kind
    if k == "bool":
        w = QCheckBox()
        w.setChecked(bool(value))
        w.toggled.connect(on_change)
        return w
    if k == "int":
        w = QSpinBox()
        w.setRange(int(f.min if f.min is not None else -100000), int(f.max if f.max is not None else 100000))
        w.setValue(int(value))
        w.setKeyboardTracking(False)
        w.valueChanged.connect(on_change)
        return w
    if k == "float":
        w = QDoubleSpinBox()
        w.setRange(f.min if f.min is not None else -100000, f.max if f.max is not None else 100000)
        step = f.step or (0.05 if (f.max or 10) <= 1 else 1)
        w.setSingleStep(step)
        w.setDecimals(2 if step < 1 else 1)
        w.setValue(float(value))
        w.setKeyboardTracking(False)
        w.valueChanged.connect(on_change)
        return w
    if k in ("choice", "metric", "weight"):
        w = QComboBox()
        choices = f.choices()
        for v, label in choices:
            w.addItem(label, v)
        idx = next((i for i, (v, _) in enumerate(choices) if v == value), -1)
        if idx < 0 and value:
            w.addItem(f"{value} (unknown)", value)
            idx = w.count() - 1
        w.setCurrentIndex(max(0, idx))
        w.currentIndexChanged.connect(lambda i: on_change(w.itemData(i)))
        return w
    if k == "color":
        w = ColorEdit(palette_fn, f.optional)
        w.set_value(value)
        w.edited.connect(on_change)
        return w
    if k == "template":
        w = TemplateEdit()
        w.set_value(value)
        w.edited.connect(on_change)
        return w
    if k == "file":
        w = FileEdit()
        w.set_value(value)
        w.edited.connect(on_change)
        return w
    if k == "font":
        w = QComboBox()
        w.setEditable(True)
        w.addItem("")
        w.addItems(font_families())
        w.setCurrentText(value or "")
        w.lineEdit().setPlaceholderText("theme font")
        w.lineEdit().editingFinished.connect(lambda: on_change(w.currentText()))
        w.activated.connect(lambda _i: on_change(w.currentText()))
        return w
    w = QLineEdit(str(value))
    if k == "command":
        w.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
    w.editingFinished.connect(lambda: on_change(w.text()))
    return w


# ------------------------------------------------------------------- inspector
class Inspector(QScrollArea):
    def __init__(self, doc: LayoutDocument, screen_size_fn: Callable[[], tuple[int, int] | None]):
        super().__init__()
        self.doc = doc
        self.screen_size_fn = screen_size_fn
        self.setWidgetResizable(True)
        self.setMinimumWidth(330)
        self._shown: tuple | None = None
        self._geo: dict[str, QSpinBox] = {}
        self._rebuild_timer = QTimer(self, singleShot=True, interval=0)
        self._rebuild_timer.timeout.connect(self.rebuild)
        doc.selectionChanged.connect(lambda _ids: self._rebuild_timer.start())
        doc.changed.connect(self._changed)

    def palette(self) -> Palette:  # noqa: D401 - used by colour editors
        return Palette(self.doc.layout.get("palette"), self.doc.layout.get("font", ""))

    def _changed(self, kind: str) -> None:
        if kind == "live":
            self._update_geometry()
        elif kind in ("all", "widgets", "geometry"):
            if kind == "geometry":
                self._update_geometry()
            else:
                self._rebuild_timer.start()
        elif kind == "layout" and not self.doc.selection:
            pass  # editors already show the value the user typed

    def _update_geometry(self) -> None:
        sel = self.doc.selected()
        if len(sel) != 1 or not self._geo:
            return
        for key, box in self._geo.items():
            box.blockSignals(True)
            box.setValue(int(sel[0].get(key, 0)))
            box.blockSignals(False)

    def rebuild(self) -> None:
        sel = self.doc.selected()
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(10, 10, 10, 10)
        self._geo = {}
        if not sel:
            self._layout_page(v)
        elif len(sel) == 1:
            self._widget_page(v, sel[0])
        else:
            title = QLabel(f"{len(sel)} widgets selected")
            title.setObjectName("title")
            v.addWidget(title)
            hint = QLabel("Drag to move them together, or use Arrange in the toolbar to align and distribute.")
            hint.setWordWrap(True)
            hint.setObjectName("hint")
            v.addWidget(hint)
        v.addStretch(1)
        old = self.takeWidget()
        self.setWidget(body)
        if old is not None:
            old.deleteLater()

    # ---- widget ---------------------------------------------------------
    def _widget_page(self, v: QVBoxLayout, data: dict[str, Any]) -> None:
        cls = WIDGETS.get(data["type"])
        wid = data["id"]
        title = QLabel(cls.title if cls else data["type"])
        title.setObjectName("title")
        v.addWidget(title)
        if cls and cls.description:
            d = QLabel(cls.description)
            d.setWordWrap(True)
            d.setObjectName("hint")
            v.addWidget(d)

        box = QGroupBox("Widget")
        form = QFormLayout(box)
        name = QLineEdit(data.get("name", ""))
        name.editingFinished.connect(lambda: self.doc.set_attr(wid, "name", name.text(), "widgets"))
        form.addRow("Name", name)
        flags = QHBoxLayout()
        vis = QCheckBox("Visible")
        vis.setChecked(data.get("visible", True))
        vis.toggled.connect(lambda b: self.doc.set_attr(wid, "visible", b, "widgets"))
        lock = QCheckBox("Locked")
        lock.setChecked(data.get("locked", False))
        lock.toggled.connect(lambda b: self.doc.set_attr(wid, "locked", b, "widgets"))
        flags.addWidget(vis)
        flags.addWidget(lock)
        flags.addStretch(1)
        form.addRow("", flags)
        geo = QGridLayout()
        for i, key in enumerate(("x", "y", "w", "h")):
            sb = QSpinBox()
            sb.setRange(-5000 if key in "xy" else 1, 10000)
            sb.setValue(int(data.get(key, 0)))
            sb.setKeyboardTracking(False)
            sb.setPrefix(f"{key.upper()} ")
            sb.valueChanged.connect(lambda val, k=key: self.doc.set_attr(wid, k, int(val), "geometry"))
            geo.addWidget(sb, i // 2, i % 2)
            self._geo[key] = sb
        form.addRow("Geometry", geo)
        v.addWidget(box)
        if not cls:
            return
        props = cls(data).props
        groups: dict[str, QFormLayout] = {}
        for f in cls.schema():
            if f.group not in groups:
                g = QGroupBox(f.group)
                groups[f.group] = QFormLayout(g)
                groups[f.group].setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                v.addWidget(g)
            ed = make_editor(f, props.get(f.key, f.default),
                             lambda val, k=f.key: self.doc.set_prop(wid, k, val), self.palette)
            if f.help:
                ed.setToolTip(f.help)
            label = QLabel(f.label)
            if f.help:
                label.setToolTip(f.help)
            groups[f.group].addRow(label, ed)

    # ---- layout ---------------------------------------------------------
    def _layout_page(self, v: QVBoxLayout) -> None:
        lay = self.doc.layout
        title = QLabel("Layout")
        title.setObjectName("title")
        v.addWidget(title)
        hint = QLabel("Nothing selected: these settings apply to the whole layout. Click a widget to edit it.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        v.addWidget(hint)

        box = QGroupBox("General")
        form = QFormLayout(box)
        for key, label in (("name", "Name"), ("description", "Description"), ("author", "Author")):
            ed = QLineEdit(str(lay.get(key, "")))
            ed.editingFinished.connect(lambda e=ed, k=key: self.doc.set_layout_value((k,), e.text()))
            form.addRow(label, ed)
        fam = make_editor(Field("font", "Font", "font", ""), lay.get("font", ""),
                          lambda val: self.doc.set_layout_value(("font",), val), self.palette)
        form.addRow("Theme font", fam)
        size_row = QHBoxLayout()
        cw, ch = self.doc.canvas
        wbox, hbox = QSpinBox(), QSpinBox()
        for sb, val in ((wbox, cw), (hbox, ch)):
            sb.setRange(16, 8000)
            sb.setValue(val)
            sb.setKeyboardTracking(False)
        wbox.valueChanged.connect(lambda val: self.doc.set_layout_value(("canvas",), [int(val), self.doc.canvas[1]]))
        hbox.valueChanged.connect(lambda val: self.doc.set_layout_value(("canvas",), [self.doc.canvas[0], int(val)]))
        size_row.addWidget(wbox)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(hbox)
        match = QPushButton("Match screen")
        match.setToolTip("Use the connected screen's resolution in the current orientation")
        match.clicked.connect(self._match_screen)
        size_row.addWidget(match)
        form.addRow("Canvas", size_row)
        v.addWidget(box)

        bg = lay.get("background", {})
        box = QGroupBox("Background")
        form = QFormLayout(box)
        for f in (
            Field("color", "Colour", "color", "@background"),
            Field("color2", "Gradient to", "color", "", optional=True),
            Field("vertical", "Vertical gradient", "bool", True),
            Field("image", "Image", "file", ""),
            Field("fit", "Image fit", "choice", "cover",
                  options=(("cover", "Cover"), ("contain", "Contain"), ("stretch", "Stretch"))),
            Field("dim", "Darken image", "float", 0.0, min=0, max=1, step=0.05),
        ):
            form.addRow(f.label, make_editor(f, bg.get(f.key, f.default),
                                             lambda val, k=f.key: self.doc.set_layout_value(("background", k), val),
                                             self.palette))
        v.addWidget(box)

        box = QGroupBox("Palette")
        grid = QGridLayout(box)
        pal = lay.get("palette", {})
        for i, key in enumerate(DEFAULT_PALETTE):
            ed = ColorEdit(self.palette)
            ed.set_value(pal.get(key, DEFAULT_PALETTE[key]))
            ed.edited.connect(lambda val, k=key: self._set_palette(k, val))
            grid.addWidget(QLabel(PALETTE_LABELS.get(key, key)), i, 0)
            grid.addWidget(ed, i, 1)
        reset = QPushButton("Reset palette")
        reset.clicked.connect(lambda: (self.doc.set_layout_value(("palette",), {}), self._rebuild_timer.start()))
        grid.addWidget(reset, len(DEFAULT_PALETTE), 1)
        v.addWidget(box)

    def _set_palette(self, key: str, value: str) -> None:
        pal = dict(self.doc.layout.get("palette", {}))
        if not value or value.startswith("@"):
            pal.pop(key, None)
        else:
            pal[key] = value
        self.doc.set_layout_value(("palette",), pal)

    def _match_screen(self) -> None:
        size = self.screen_size_fn()
        if size:
            self.doc.set_layout_value(("canvas",), list(size))
            self._rebuild_timer.start()
