"""Small Qt helpers shared by the GUI modules."""

from __future__ import annotations

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QStyleFactory

from ..render.draw import color as parse_color

ACCENT = "#38bdf8"


def pil_to_qimage(img: Image.Image) -> QImage:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    return QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888).copy()


def swatch_icon(value: str, palette=None, size: int = 16) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # checkerboard so translucent colours read as such
    for y in range(0, size, 4):
        for x in range(0, size, 4):
            p.fillRect(x, y, 4, 4, QColor("#bbbbbb") if (x + y) // 4 % 2 else QColor("#ffffff"))
    try:
        rgba = palette.resolve(value) if palette is not None else parse_color(value)
        p.fillRect(0, 0, size, size, QColor(*rgba))
    except (ValueError, AttributeError):
        p.setPen(QColor("#ff5555"))
        p.drawLine(0, 0, size, size)
    p.setPen(QColor(0, 0, 0, 90))
    p.drawRect(0, 0, size - 1, size - 1)
    p.end()
    return QIcon(pm)


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle(QStyleFactory.create("Fusion"))
    pal = QPalette()
    base, alt, text, mid = QColor("#14171e"), QColor("#1b1f28"), QColor("#e6e9ef"), QColor("#2a303c")
    pal.setColor(QPalette.ColorRole.Window, QColor("#101319"))
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, base)
    pal.setColor(QPalette.ColorRole.AlternateBase, alt)
    pal.setColor(QPalette.ColorRole.ToolTipBase, alt)
    pal.setColor(QPalette.ColorRole.ToolTipText, text)
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, QColor("#1b1f28"))
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.BrightText, QColor("#ff6b6b"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#06121a"))
    pal.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    pal.setColor(QPalette.ColorRole.Mid, mid)
    pal.setColor(QPalette.ColorRole.Dark, QColor("#0b0d12"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#6b7385"))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#5b6272"))
    app.setPalette(pal)
    app.setStyleSheet(f"""
        QToolTip {{ border: 1px solid #2a303c; padding: 4px; }}
        QGroupBox {{ border: 1px solid #252a35; border-radius: 6px; margin-top: 14px; padding-top: 6px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #9aa3b5; }}
        QDockWidget::title {{ padding: 6px; background: #151921; }}
        QToolBar {{ spacing: 4px; padding: 4px; border: none; background: #151921; }}
        QToolButton {{ padding: 4px 8px; border-radius: 5px; }}
        QToolButton:hover {{ background: #232936; }}
        QToolButton:checked {{ background: #1f3a4d; }}
        QPushButton {{ padding: 5px 12px; border-radius: 5px; border: 1px solid #2a303c; background: #1b1f28; }}
        QPushButton:hover {{ background: #232936; }}
        QPushButton#primary {{ background: {ACCENT}; color: #06121a; border: none; font-weight: 600; }}
        QPushButton#primary:hover {{ background: #5fcbfa; }}
        QListWidget, QTreeWidget {{ border: none; }}
        QListWidget::item {{ padding: 4px; border-radius: 4px; }}
        QListWidget::item:selected {{ background: #1f3a4d; color: #e6e9ef; }}
        QTreeWidget::item:selected {{ background: #1f3a4d; color: #e6e9ef; }}
        QLabel#hint {{ color: #8a93a6; }}
        QLabel#title {{ font-size: 15px; font-weight: 600; }}
        QFrame#banner {{ background: #2a2410; border: 1px solid #5a4a12; border-radius: 6px; }}
        QStatusBar {{ background: #151921; }}
    """)


def load_app_icon() -> QIcon:
    from importlib import resources

    try:
        path = resources.files("turzx") / "gui" / "icon.svg"
        if path.is_file():
            return QIcon(str(path))
    except (FileNotFoundError, ModuleNotFoundError):
        pass
    return QIcon()
