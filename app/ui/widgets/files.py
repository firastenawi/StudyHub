"""File rows, Windows file icons and colored tag chips (shared by the Files page views)."""
from __future__ import annotations

import os

from PyQt6.QtCore import QFileInfo, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QFileIconProvider, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.core.files import format_mtime, human_size
from app.core.i18n import is_rtl, ltr
from app.ui.animations import OUT, value_animation
from app.ui.theme import Palette, qcolor, ui_font, ink
from app.ui.widgets.common import label

TAG_COLORS = ["#7C5CFF", "#22D3EE", "#34D399", "#FBBF24", "#F472B6", "#60A5FA", "#FB7185", "#A3E635"]
_provider: QFileIconProvider | None = None
_icon_cache: dict[str, QPixmap] = {}


def file_icon(path: str, is_dir: bool = False, size: int = 28) -> QPixmap:
    """Real Windows icon. Cached per extension (folders, .exe and shortcuts per file)."""
    global _provider
    ext = os.path.splitext(path)[1].lower()
    key = f"{'dir' if is_dir else ext}:{size}" if ext not in (".exe", ".lnk", ".ico", ".url") else f"{path}:{size}"
    if key not in _icon_cache:
        _provider = _provider or QFileIconProvider()
        if is_dir:
            icon = _provider.icon(QFileIconProvider.IconType.Folder)
        else:
            icon = _provider.icon(QFileInfo(path))
        if icon.isNull():
            icon = _provider.icon(QFileIconProvider.IconType.File)
        _icon_cache[key] = icon.pixmap(QSize(size, size))
    return _icon_cache[key]


class TagChips(QWidget):
    """Small rounded colored labels painted in one row."""

    def __init__(self, tags: list[dict], parent=None):
        super().__init__(parent)
        self.tags = tags
        self.setFont(ui_font(8.2, QFont.Weight.DemiBold))
        fm = QFontMetrics(self.font())
        self._width = sum(fm.horizontalAdvance(t["name"]) + 20 for t in tags) + 6 * max(0, len(tags) - 1)
        self.setFixedSize(max(1, self._width), 22)
        self.setVisible(bool(tags))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = p.fontMetrics()
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        x = float(self.width()) if rtl else 0.0
        for t in self.tags:
            w = fm.horizontalAdvance(t["name"]) + 20
            r = QRectF(x - w if rtl else x, 1, w, 20)
            color = t.get("color") or TAG_COLORS[0]
            p.setPen(qcolor(color, 0.55))
            p.setBrush(qcolor(color, 0.16))
            p.drawRoundedRect(r, 10, 10)
            p.setPen(QColor(color).lighter(130))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, t["name"])
            x = x - w - 6 if rtl else x + w + 6


class FileRow(QWidget):
    """One file or folder: icon, name, meta line, tag chips. Hover-highlighted."""

    activated = pyqtSignal(dict)       # double click
    menu_requested = pyqtSignal(dict, object)

    def __init__(self, entry: dict, tags: list[dict] | None = None, meta: str | None = None,
                 extra: QWidget | None = None, parent=None):
        super().__init__(parent)
        self.entry = entry
        self._hover = 0.0
        self._anim = value_animation(self, 0.0, 1.0, 150, self._set, OUT)
        missing = entry.get("missing", False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 7, 14, 7)
        lay.setSpacing(12)
        icon = QLabel()
        icon.setFixedSize(30, 30)
        icon.setPixmap(file_icon(entry["path"], entry.get("is_dir", False)))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(1)
        # file names keep their natural order ("ملخص.docx", not "docx.ملخص") and always sit next
        # to the icon, whatever the script of the name or the UI language
        align = (Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignRight if is_rtl()
                 else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter
        name = label(ltr(entry["name"]), 10, QFont.Weight.Medium, "Faint" if missing else "")
        name.setAlignment(align)
        col.addWidget(name)
        if meta is None:
            meta = format_mtime(entry["mtime"]) if "mtime" in entry else ""
            if meta:
                meta = ltr(meta)
            if not entry.get("is_dir") and entry.get("size") is not None and "mtime" in entry:
                meta += "  ·  " + ltr(human_size(entry["size"]))
        if meta:
            meta_label = label(meta, 8.4, role="Faint")
            meta_label.setAlignment(align)
            col.addWidget(meta_label)
        lay.addLayout(col, 1)
        if tags:
            lay.addWidget(TagChips(tags))
        if extra is not None:
            lay.addWidget(extra)

    def _set(self, v):
        self._hover = float(v)
        self.update()

    def enterEvent(self, e):
        self._anim.setDirection(self._anim.Direction.Forward)
        self._anim.start()

    def leaveEvent(self, e):
        self._anim.setDirection(self._anim.Direction.Backward)
        self._anim.start()

    def paintEvent(self, _):
        if self._hover > 0:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink(0.05 * self._hover))
            p.drawRoundedRect(QRectF(self.rect()), 12, 12)

    def mouseDoubleClickEvent(self, _):
        self.activated.emit(self.entry)

    def contextMenuEvent(self, e):
        self.menu_requested.emit(self.entry, e.globalPos())
        e.accept()


def tag_lookup(tags: list[dict]) -> dict[int, dict]:
    return {t["id"]: t for t in tags}


def tags_from_ids(ids: str | list | None, lookup: dict[int, dict]) -> list[dict]:
    if not ids:
        return []
    if isinstance(ids, str):
        ids = [int(x) for x in ids.split(",") if x]
    return [lookup[i] for i in ids if i in lookup]


def color_icon(color: str, size: int = 12) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
    p.end()
    return QIcon(pm)
