"""Floating translation card (PDF reader selection, screenshot text): result, swap direction, copy, to note."""
from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, QPoint, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QDesktopServices, QFont, QGuiApplication
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QPlainTextEdit, QVBoxLayout

from app.core.i18n import tr
from app.core.translate import translate, web_url
from app.ui.dialogs.base import drop_shadow
from app.ui.theme import Icons, Palette, Theme
from app.ui.widgets.common import GlyphLabel, IconButton, button, label

LANG_NAMES = {"ar": "العربية", "en": "English", "fr": "Français", "de": "Deutsch", "tr": "Türkçe", "es": "Español"}


class _Worker(QObject):
    done = pyqtSignal(int, object)

    def run(self, job: int, text: str, target: str) -> None:
        def work():
            try:
                result = translate(text, target)
            except Exception as exc:
                result = exc
            self.done.emit(job, result)
        threading.Thread(target=work, daemon=True).start()


class TranslatePopup(QFrame):
    """One per window that uses it. note_callback(source, translation) adds a "to note" button."""

    def __init__(self, ctl, parent=None, note_callback=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.ctl = ctl
        self.note_callback = note_callback
        self.text = ""
        self.target = "auto"
        self.result = ""
        self._job = 0
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        card = QFrame()
        card.setObjectName("DialogFrame")
        drop_shadow(card)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 10, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(GlyphLabel(Icons.GLOBE, 11, Theme.accent2()))
        self.title = label(tr("translate"), 10.5, QFont.Weight.DemiBold)
        head.addWidget(self.title, 1)
        swap = IconButton(Icons.SYNC, tr("translate_swap"), 28, 9)
        swap.clicked.connect(self._swap)
        head.addWidget(swap)
        close = IconButton(Icons.CLOSE, tr("close"), 28, 9)
        close.clicked.connect(self.hide)
        head.addWidget(close)
        lay.addLayout(head)
        self.source = label("", 9, role="Dim", wrap=True)
        self.source.setTextFormat(Qt.TextFormat.PlainText)
        self.source.setMaximumHeight(60)
        lay.addWidget(self.source)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMinimumHeight(110)
        self.out.setFont(QFont("Segoe UI", 11))
        lay.addWidget(self.out, 1)
        row = QHBoxLayout()
        copy = button(tr("copy"), Icons.COPY, role="Ghost")
        copy.clicked.connect(lambda: (QApplication.clipboard().setText(self.result), self.ctl.notify(tr("copied"), "success")))
        row.addWidget(copy)
        if note_callback:
            note = button(tr("translate_to_note"), Icons.NOTES, role="Ghost")
            note.clicked.connect(lambda: self.result and (note_callback(self.text, self.result), self.hide()))
            row.addWidget(note)
        row.addStretch(1)
        web = button(tr("translate_web"), Icons.FORWARD, role="Ghost")
        web.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(web_url(self.text, self._target_now()))))
        row.addWidget(web)
        lay.addLayout(row)
        lay.addWidget(label(tr("translate_privacy"), 7.8, role="Faint", wrap=True))
        self.resize(460, 330)
        self.worker = _Worker(self)
        self.worker.done.connect(self._done)

    def _target_now(self) -> str:
        return self.target if self.target != "auto" else ("en" if self._is_arabic() else "ar")

    def _is_arabic(self) -> bool:
        from app.core.translate import auto_target
        return auto_target(self.text) == "en"

    def show_for(self, text: str, at: QPoint | None = None, target: str | None = None) -> None:
        self.text = text.strip()
        self.target = target or self.ctl.db.get_setting("translate_target", "auto")
        self.source.setText(self.text[:400] + ("…" if len(self.text) > 400 else ""))
        self._run()
        pos = at or QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left(), min(pos.x() - self.width() // 2, area.right() - self.width()))
        y = pos.y() + 12 if pos.y() + 12 + self.height() < area.bottom() else pos.y() - self.height() - 12
        self.move(x, max(area.top(), y))
        self.show()
        self.raise_()

    def _run(self) -> None:
        self._job += 1
        self.result = ""
        self.out.setPlainText(tr("translating"))
        self.worker.run(self._job, self.text, self.target)

    def _swap(self) -> None:
        current = self._target_now()
        self.target = "en" if current == "ar" else "ar"
        self._run()

    def _done(self, job: int, result) -> None:
        if job != self._job:
            return
        if isinstance(result, Exception):
            self.out.setPlainText(tr("translate_failed", error=str(result)))
            return
        out, source, target = result
        self.result = out
        self.target = target
        self.title.setText(f"{tr('translate')}  ·  {LANG_NAMES.get(source, source or '?')} → {LANG_NAMES.get(target, target)}")
        self.out.setPlainText(out)
        doc_option = self.out.document().defaultTextOption()
        doc_option.setTextDirection(Qt.LayoutDirection.RightToLeft if target == "ar" else Qt.LayoutDirection.LeftToRight)
        self.out.document().setDefaultTextOption(doc_option)
