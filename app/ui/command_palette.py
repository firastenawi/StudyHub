"""
Spotlight-style command palette (Ctrl+Space): type a few letters to launch an
app/site, open a note, jump to a page or run an action. Arrow keys + Enter.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QVBoxLayout, QWidget)

from app.core.i18n import tr
from app.ui.animations import OUT, value_animation
from app.ui.dialogs.base import drop_shadow
from app.ui.theme import Icons, Palette, Theme, ui_font
from app.ui.widgets.common import GlyphLabel, GlyphPlate, Overlay, label


class ResultRow(QWidget):
    def __init__(self, result: dict, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 14, 8)
        lay.setSpacing(14)
        if result.get("pixmap") is not None:
            icon = QLabel()
            icon.setFixedSize(36, 36)
            source = result["pixmap"]
            dpr = max(1.0, source.devicePixelRatio())  # avatars are rendered at 2x for sharpness
            pm = source.scaled(QSize(int(36 * dpr), int(36 * dpr)), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            icon.setPixmap(pm)
            lay.addWidget(icon)
        else:
            lay.addWidget(GlyphPlate(result.get("glyph", Icons.CHEVRON), result.get("color", Theme.accent()), 36))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(result["title"], 10.5, QFont.Weight.Medium))
        if result.get("subtitle"):
            sub = label(result["subtitle"], 8.8, role="Faint")
            sub.setMaximumWidth(460)
            col.addWidget(sub)
        lay.addLayout(col, 1)
        lay.addWidget(label(result.get("kind", ""), 8.6, role="Dim"))


class CommandPalette(Overlay):
    def __init__(self, ctl, host: QWidget):
        super().__init__(host)
        self.ctl = ctl
        self._t = 0.0
        self._results: list[dict] = []
        self._anim = value_animation(self, 0.0, 1.0, 240, self._set_t, OUT, self._anim_done)

        self.panel = QFrame(self)
        self.panel.setObjectName("DialogFrame")
        self.panel.setFixedWidth(660)
        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(GlyphLabel(Icons.SEARCH, 13, Theme.accent()))
        self.input = QLineEdit()
        self.input.setObjectName("PaletteInput")
        self.input.setFont(ui_font(13.5))
        self.input.setPlaceholderText(tr("palette_placeholder"))
        self.input.textChanged.connect(self._search)
        self.input.installEventFilter(self)
        top.addWidget(self.input, 1)
        esc = label("Esc", 8.5, QFont.Weight.Medium, "Keycap")
        top.addWidget(esc)
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.itemActivated.connect(lambda _: self._execute())
        self.list.itemClicked.connect(lambda _: self._execute())
        lay.addWidget(self.list)
        self.empty = label(tr("palette_empty"), 9.5, role="Faint")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty)
        self._panel_fx = QGraphicsOpacityEffect(self.panel)
        self.panel.setGraphicsEffect(self._panel_fx)

    # ---------------------------------------------------------------- open
    def open(self, text: str = ""):
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.input.setText(text)  # the Start menu hands over what was typed there
        if not text:
            self._search("")
        self._anim.setDirection(self._anim.Direction.Forward)
        self._anim.start()
        self.input.setFocus()

    def close_palette(self):
        if not self.isVisible():
            return
        self._anim.setDirection(self._anim.Direction.Backward)
        self._anim.start()

    def _anim_done(self):
        if self._anim.direction() == self._anim.Direction.Backward:
            self.hide()

    def _set_t(self, v):
        self._t = float(v)
        self._panel_fx.setOpacity(self._t)
        self.on_resized()
        self.update()

    def on_resized(self):
        x = (self.width() - self.panel.width()) // 2
        y = int(self.height() * 0.14 - (1 - self._t) * 18)
        self.panel.move(QPoint(x, y))

    # -------------------------------------------------------------- search
    def _search(self, text: str):
        self._results = self.ctl.search(text.strip())
        self.list.clear()
        for result in self._results:
            item = QListWidgetItem()
            row = ResultRow(result)
            item.setSizeHint(QSize(0, 56))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        self.list.setFixedHeight(min(7, max(1, len(self._results))) * 58 + 4)
        self.list.setVisible(bool(self._results))
        self.empty.setVisible(not self._results)
        if self._results:
            self.list.setCurrentRow(0)
        self.panel.adjustSize()

    def _execute(self):
        row = self.list.currentRow()
        if 0 <= row < len(self._results):
            result = self._results[row]
            callback = result["action"]
            if result.get("id"):  # history + most used first, next time
                self.ctl.record_palette_use(result["id"])
            self.close_palette()
            QTimer.singleShot(140, callback)

    # --------------------------------------------------------------- input
    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self._results:
                step = 1 if key == Qt.Key.Key_Down else -1
                self.list.setCurrentRow((self.list.currentRow() + step) % len(self._results))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._execute()
                return True
            if key == Qt.Key.Key_Escape:
                self.close_palette()
                return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, e):
        if not self.panel.geometry().contains(e.position().toPoint()):
            self.close_palette()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(4, 6, 10, int(150 * self._t)))
