"""
Clipboard history popup (Ctrl+Alt+C): opens next to the mouse over any app.

  type to search · ↑ ↓ choose · Enter paste into the app you were in · Ctrl+1–9 paste that row
  Ctrl+C copy only · Ctrl+P pin / unpin · Delete remove · Esc close
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PyQt6.QtGui import QCursor, QFont, QGuiApplication, QPixmap
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
                             QWidget)

from app.core import winapi
from app.core.clipboard_history import CLIP_DIR
from app.core.i18n import ltr, relative_time, tr
from app.ui.dialogs.base import drop_shadow
from app.ui.theme import Icons, Palette, Theme
from app.ui.widgets.common import GlyphLabel, IconButton, label

KIND_GLYPH = {"text": Icons.NOTES, "link": Icons.LINK, "image": Icons.PREVIEW, "files": Icons.FOLDER}


class ClipRow(QWidget):
    def __init__(self, clip: dict, number: int):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(10)
        num = label(str(number) if number <= 9 else "", 8.5, QFont.Weight.DemiBold, "Faint")
        num.setFixedWidth(12)
        lay.addWidget(num)
        if clip["kind"] == "image":
            thumb = QLabel()
            pix = QPixmap(str(CLIP_DIR / clip["content"]))
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(QSize(64, 40), Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
            thumb.setFixedSize(64, 40)
            lay.addWidget(thumb)
        else:
            lay.addWidget(GlyphLabel(KIND_GLYPH.get(clip["kind"], Icons.NOTES), 11,
                                     Theme.accent2() if clip["kind"] == "link" else Palette.text_dim))
        col = QVBoxLayout()
        col.setSpacing(0)
        text = clip["preview"] or clip["content"]
        if clip["kind"] == "files":
            names = json.loads(clip["content"])
            text = ", ".join(Path(p).name for p in names[:4]) + (f" +{len(names) - 4}" if len(names) > 4 else "")
        main = label(text[:140], 9.8)
        main.setTextFormat(Qt.TextFormat.PlainText)
        col.addWidget(main)
        meta = relative_time(clip["created_at"])
        if clip["kind"] == "text" and len(clip["content"]) > 140:
            meta += "  ·  " + ltr(tr("clip_chars", n=len(clip["content"])))
        col.addWidget(label(meta, 8.2, role="Faint"))
        lay.addLayout(col, 1)
        if clip["pinned"]:
            lay.addWidget(GlyphLabel(Icons.PIN, 9, Theme.accent()))


class ClipboardPopup(QFrame):
    def __init__(self, ctl):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.ctl = ctl
        self.history = ctl.clipboard_history
        self.target_hwnd = 0
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        self.card = QFrame()
        self.card.setObjectName("DialogFrame")
        drop_shadow(self.card)
        outer.addWidget(self.card)
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(12, 12, 12, 10)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(GlyphLabel(Icons.PASTE, 12, Theme.accent()))
        head.addWidget(label(tr("clip_title"), 11, QFont.Weight.DemiBold), 1)
        self.paused = label("", 8.6)
        self.paused.setStyleSheet(f"color: {Palette.warning};")
        head.addWidget(self.paused)
        clear = IconButton(Icons.DELETE, tr("clip_clear"), 28, 9)
        clear.clicked.connect(self._clear)
        head.addWidget(clear)
        lay.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("clip_search"))
        self.search.textChanged.connect(lambda _: self.fill())
        self.search.installEventFilter(self)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.itemActivated.connect(lambda _: self.paste_current())
        self.list.itemDoubleClicked.connect(lambda _: self.paste_current())
        self.list.installEventFilter(self)
        lay.addWidget(self.list, 1)
        lay.addWidget(label(tr("clip_hint"), 8.2, role="Faint"))
        self.empty = label(tr("clip_empty"), 9.5, role="Dim", wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty)
        self.resize(500, 560)
        self.history.changed.connect(lambda: self.isVisible() and self.fill())

    # --------------------------------------------------------------- open
    def open(self) -> None:
        self.target_hwnd = winapi.foreground_hwnd()
        self.search.clear()
        self.fill()
        self.paused.setText("" if self.history.enabled else tr("clip_paused"))
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        pos = QCursor.pos() - QPoint(self.width() // 2, 60)
        pos.setX(max(area.left(), min(pos.x(), area.right() - self.width())))
        pos.setY(max(area.top(), min(pos.y(), area.bottom() - self.height())))
        self.move(pos)
        self.show()
        self.raise_()
        self.activateWindow()
        winapi.force_foreground(int(self.winId()))
        self.search.setFocus()

    def fill(self) -> None:
        clips = self.ctl.db.list_clips(self.search.text().strip())
        self.list.clear()
        for i, clip in enumerate(clips, start=1):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, clip["id"])
            row = ClipRow(clip, i)
            item.setSizeHint(QSize(0, 56 if clip["kind"] == "image" else 50))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        if clips:
            self.list.setCurrentRow(0)
        self.empty.setVisible(not clips)
        self.list.setVisible(bool(clips))

    def _current_id(self) -> int | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    # ------------------------------------------------------------ actions
    def paste_current(self, row: int | None = None) -> None:
        if row is not None:
            if row >= self.list.count():
                return
            self.list.setCurrentRow(row)
        clip_id = self._current_id()
        if clip_id is None or not self.history.put_on_clipboard(clip_id):
            return
        self.hide()
        hwnd = self.target_hwnd
        if hwnd:
            winapi.force_foreground(hwnd)
            QTimer.singleShot(120, winapi.send_ctrl_v)

    def copy_current(self) -> None:
        clip_id = self._current_id()
        if clip_id is not None and self.history.put_on_clipboard(clip_id):
            self.ctl.notify(tr("copied"), "success")
            self.hide()

    def _toggle_pin(self) -> None:
        clip_id = self._current_id()
        clip = self.ctl.db.get_clip(clip_id) if clip_id else None
        if clip:
            self.history.set_pinned(clip_id, not clip["pinned"])

    def _delete(self) -> None:
        clip_id = self._current_id()
        if clip_id is not None:
            row = self.list.currentRow()
            self.history.delete([clip_id])
            self.list.setCurrentRow(min(row, self.list.count() - 1))

    def _clear(self) -> None:
        self.history.clear(keep_pinned=True)
        self.ctl.notify(tr("clip_cleared"), "success")

    # ------------------------------------------------------------- keys
    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.KeyPress:
            key, mods = e.key(), e.modifiers()
            ctrl = mods & Qt.KeyboardModifier.ControlModifier
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and obj is self.search:
                n = self.list.count()
                if n:
                    self.list.setCurrentRow((self.list.currentRow() + (1 if key == Qt.Key.Key_Down else -1)) % n)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.paste_current()
                return True
            if ctrl and key == Qt.Key.Key_C and not (obj is self.search and self.search.hasSelectedText()):
                self.copy_current()
                return True
            if ctrl and key == Qt.Key.Key_P:
                self._toggle_pin()
                return True
            if key == Qt.Key.Key_Delete and (obj is self.list or not self.search.text()):
                self._delete()
                return True
            if ctrl and Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
                self.paste_current(key - Qt.Key.Key_1)
                return True
        return super().eventFilter(obj, e)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            QTimer.singleShot(150, lambda: (not self.isActiveWindow()) and self.hide())
