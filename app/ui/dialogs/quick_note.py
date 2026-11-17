"""
Floating quick-note window.

Opens on top of any application (global hotkey / tray / Explorer menu), on the
screen where the mouse is. Ctrl+Enter saves, Esc closes. Closing without saving
keeps the text as a draft, so nothing is ever lost.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QPropertyAnimation, QSequentialAnimationGroup, Qt, QTimer
from PyQt6.QtGui import QCursor, QFont, QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from app.core import winapi
from app.core.i18n import is_rtl, tr
from app.ui.animations import OUT
from app.ui.dialogs.base import drop_shadow
from app.ui.theme import Icons, Palette, Theme, glyph_icon, ui_font
from app.ui.widgets.common import GlyphLabel, IconButton, button, label, primary_button
from app.ui.widgets.markdown import auto_direction

SOURCE_GLYPHS = {"manual": Icons.EDIT, "quick": Icons.NOTES, "clipboard": Icons.PASTE,
                 "selection": Icons.SELECT, "file": Icons.DOCUMENT}


class QuickNoteWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.ctl = ctl
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft if is_rtl() else Qt.LayoutDirection.LeftToRight)
        self.resize(600, 430)
        self._note_id: int | None = None
        self._source, self._source_ref = "quick", ""
        self._draft = ("", "")
        self._drag_from: QPoint | None = None
        self._build()

        for keys in ("Ctrl+Return", "Ctrl+Enter", "Ctrl+S"):
            QShortcut(QKeySequence(keys), self, activated=self.save)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=lambda: self._wrap("**", "**"))
        QShortcut(QKeySequence("Ctrl+I"), self, activated=lambda: self._wrap("*", "*"))
        QShortcut(QKeySequence("Ctrl+U"), self, activated=lambda: self._wrap("<u>", "</u>"))
        QShortcut(QKeySequence("Escape"), self, activated=self.dismiss)

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 26, 26, 26)
        self.frame = QFrame()
        self.frame.setObjectName("DialogFrame")
        drop_shadow(self.frame, 56, 16, 190)
        outer.addWidget(self.frame)

        lay = QVBoxLayout(self.frame)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(GlyphLabel(Icons.NOTES, 14, Theme.accent()))
        self.heading = label(tr("quick_note"), 13, QFont.Weight.DemiBold)
        header.addWidget(self.heading)
        self.source_label = label("", 9, role="Badge")
        header.addWidget(self.source_label)
        header.addStretch(1)
        close = IconButton(Icons.CLOSE, tr("close") + " (Esc)", 32, 9.5)
        close.clicked.connect(self.dismiss)
        header.addWidget(close)
        lay.addLayout(header)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText(tr("note_title_optional"))
        self.title_edit.setFont(ui_font(11, QFont.Weight.DemiBold))
        lay.addWidget(self.title_edit)

        tools = QHBoxLayout()
        tools.setSpacing(2)
        self.fmt_buttons = []
        for text, tip, slot in (("B", tr("fmt_bold") + " (Ctrl+B)", lambda: self._wrap("**", "**")),
                                ("I", tr("fmt_italic") + " (Ctrl+I)", lambda: self._wrap("*", "*")),
                                ("U", tr("fmt_underline") + " (Ctrl+U)", lambda: self._wrap("<u>", "</u>")),
                                ("S", tr("fmt_strike"), lambda: self._wrap("~~", "~~")),
                                ("•", tr("fmt_bullet"), lambda: self._prefix("- ")),
                                ("☐", tr("fmt_checkbox"), lambda: self._prefix("- [ ] "))):
            b = QPushButton(text)
            b.setObjectName("IconButton")
            b.setFixedSize(32, 30)
            f = ui_font(10.5, QFont.Weight.Bold if text == "B" else QFont.Weight.Normal)
            f.setItalic(text == "I")
            f.setUnderline(text == "U")
            f.setStrikeOut(text == "S")
            b.setFont(f)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            tools.addWidget(b)
        tools.addSpacing(10)
        self.sticky_tools = QWidget()  # only when editing a desktop sticky note
        st = QHBoxLayout(self.sticky_tools)
        st.setContentsMargins(0, 0, 0, 0)
        st.setSpacing(2)
        for text, tip, delta in (("A−", tr("sticky_smaller"), -1), ("A+", tr("sticky_bigger"), +1)):
            b = QPushButton(text)
            b.setObjectName("IconButton")
            b.setFixedSize(36, 30)
            b.setFont(ui_font(9.5 if delta < 0 else 11, QFont.Weight.DemiBold))
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, d=delta: self._font(d))
            st.addWidget(b)
        self.size_label = label("", 8.8, role="Dim")
        st.addWidget(self.size_label)
        st.addSpacing(8)
        from app.ui.pages.desktop_page import NOTE_COLORS
        self.color_dots = []
        for color in NOTE_COLORS:
            d = QPushButton()
            d.setFixedSize(20, 20)
            d.setCursor(Qt.CursorShape.PointingHandCursor)
            d.setToolTip(tr("note_color"))
            d.setProperty("color", color)
            d.clicked.connect(lambda _=False, c=color: self._color(c))
            self.color_dots.append(d)
            st.addWidget(d)
        tools.addWidget(self.sticky_tools)
        tools.addStretch(1)
        lay.addLayout(tools)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(tr("note_placeholder"))
        self.editor.setFont(ui_font(11))
        auto_direction(self.editor)
        lay.addWidget(self.editor, 1)

        footer = QHBoxLayout()
        footer.addWidget(label(tr("quick_note_hint"), 8.8, role="Faint"))
        footer.addStretch(1)
        self.delete_btn = button(tr("delete"), Icons.DELETE, "Danger")
        self.delete_btn.clicked.connect(self._delete)
        footer.addWidget(self.delete_btn)
        self.save_btn = primary_button(tr("save"), Icons.SAVE)
        self.save_btn.clicked.connect(self.save)
        footer.addWidget(self.save_btn)
        lay.addLayout(footer)

    # ------------------------------------------------------------------ open
    def open(self, text: str = "", source: str = "quick", source_ref: str = "",
             note_id: int | None = None) -> None:
        self._note_id = note_id
        self._source, self._source_ref = source, source_ref
        self.save_btn.setEnabled(True)
        self.save_btn.setText(tr("save"))
        self.save_btn.setIcon(glyph_icon(Icons.SAVE, "#FFFFFF", 16))
        note = self.ctl.db.get_note(note_id) if note_id else None
        if note:
            self.heading.setText(tr("edit_note"))
            self.title_edit.setText(note["title"])
            self.editor.setPlainText(note["content"])
            self._source, self._source_ref = note["source"], note["source_ref"]
        else:
            self.heading.setText(tr("quick_note"))
            if text:
                self.title_edit.clear()
                self.editor.setPlainText(text)
            else:
                self.title_edit.setText(self._draft[0])
                self.editor.setPlainText(self._draft[1])
        self.delete_btn.setVisible(note is not None)
        self._sticky = self.ctl.sticky_item(note_id)
        self.sticky_tools.setVisible(self._sticky is not None)
        self._show_sticky_state()
        self._update_source_badge()
        self._animate_in()
        self.editor.setFocus()
        self.editor.moveCursor(self.editor.textCursor().MoveOperation.End)

    # ------------------------------------------------------------ formatting
    def _wrap(self, left: str, right: str) -> None:
        """Bold / italic / underline / strike around the selection (or an empty pair to type into)."""
        c = self.editor.textCursor()
        text = c.selectedText()
        if text.startswith(left) and text.endswith(right) and len(text) >= len(left) + len(right):
            c.insertText(text[len(left):len(text) - len(right)])  # pressed again: remove it
        else:
            c.insertText(f"{left}{text}{right}")
            if not text:
                c.movePosition(c.MoveOperation.Left, n=len(right))
                self.editor.setTextCursor(c)
        self.editor.setFocus()

    def _prefix(self, mark: str) -> None:
        """Bullet / checkbox at the start of every selected line (toggles)."""
        c = self.editor.textCursor()
        doc = self.editor.document()
        start, end = doc.findBlock(c.selectionStart()), doc.findBlock(c.selectionEnd())
        c.beginEditBlock()
        block = start
        while block.isValid():
            line = block.text()
            bc = self.editor.textCursor()
            bc.setPosition(block.position())
            if line.startswith(mark):
                bc.setPosition(block.position() + len(mark), bc.MoveMode.KeepAnchor)
                bc.removeSelectedText()
            else:
                bc.insertText(mark)
            if block == end:
                break
            block = block.next()
        c.endEditBlock()
        self.editor.setFocus()

    def _font(self, delta: int) -> None:
        if self._sticky:
            self.ctl.set_sticky_font(self._sticky["id"], delta)
            self._sticky = self.ctl.db.get_item(self._sticky["id"])
            self._show_sticky_state()

    def _color(self, color: str) -> None:
        if self._sticky:
            self.ctl.set_item_color(self._sticky["id"], color)
            self._sticky = self.ctl.db.get_item(self._sticky["id"])
            self._show_sticky_state()

    def _show_sticky_state(self) -> None:
        if not getattr(self, "_sticky", None):
            return
        from app.ui.pages.desktop_page import NOTE_COLORS, sticky_font
        self.size_label.setText(f"{sticky_font(self._sticky):g} pt")
        current = self._sticky.get("color") or NOTE_COLORS[0]
        for d in self.color_dots:
            c = d.property("color")
            ring = Palette.text if c == current else "transparent"
            d.setStyleSheet(f"QPushButton {{ background: {c}; border-radius: 10px; border: 2px solid {ring}; }}")

    def _update_source_badge(self) -> None:
        if self._source in ("clipboard", "selection", "file") or self._source_ref:
            ref = self._source_ref if len(self._source_ref) < 40 else self._source_ref[:38] + "…"
            self.source_label.setText(tr(f"source_{self._source}") + (f" · {ref}" if ref else ""))
            self.source_label.show()
        else:
            self.source_label.hide()

    def _animate_in(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        end = QPoint(area.center().x() - self.width() // 2, area.top() + int(area.height() * 0.18))
        if getattr(self, "_anim_fade", None) is not None:
            self._anim_fade.stop()  # a running fade-out must not hide us after re-opening
        self.setWindowOpacity(0.0)
        self.move(end + QPoint(0, 24))
        self.show()
        self.raise_()
        self.activateWindow()
        winapi.force_foreground(int(self.winId()))
        self._anim_pos = QPropertyAnimation(self, b"pos", self)
        self._anim_pos.setStartValue(end + QPoint(0, 24))
        self._anim_pos.setEndValue(end)
        self._anim_pos.setDuration(320)
        self._anim_pos.setEasingCurve(OUT)
        self._anim_fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim_fade.setStartValue(0.0)
        self._anim_fade.setEndValue(1.0)
        self._anim_fade.setDuration(220)
        self._anim_pos.start()
        self._anim_fade.start()

    # ------------------------------------------------------------ save/close
    def save(self) -> None:
        if not self.isVisible() or not self.save_btn.isEnabled():
            return
        title = self.title_edit.text().strip()
        content = self.editor.toPlainText().strip()
        if not title and not content:
            self._shake()
            return
        self.ctl.save_note(content, title, self._source, self._source_ref, self._note_id)
        self._draft = ("", "")
        self.save_btn.setEnabled(False)
        self.save_btn.setText(tr("saved"))
        self.save_btn.setIcon(glyph_icon(Icons.ACCEPT, "#FFFFFF", 16))
        QTimer.singleShot(480, lambda: self.dismiss(keep_draft=False))

    def _delete(self) -> None:
        if self._note_id:
            self.ctl.delete_note(self._note_id, confirm_parent=self)
            if self.ctl.db.get_note(self._note_id) is None:
                self.dismiss(keep_draft=False)

    def dismiss(self, keep_draft: bool = True) -> None:
        if keep_draft and self._note_id is None and self.save_btn.isEnabled():
            self._draft = (self.title_edit.text(), self.editor.toPlainText())
        self._anim_fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim_fade.setStartValue(self.windowOpacity())
        self._anim_fade.setEndValue(0.0)
        self._anim_fade.setDuration(160)
        self._anim_fade.finished.connect(self.hide)
        self._anim_fade.start()

    def _shake(self) -> None:
        """Gentle 'nope' shake when trying to save an empty note."""
        origin = self.pos()
        seq = QSequentialAnimationGroup(self)
        for dx in (10, -8, 6, -4, 0):
            step = QPropertyAnimation(self, b"pos", self)
            step.setEndValue(origin + QPoint(dx, 0))
            step.setDuration(45)
            seq.addAnimation(step)
        self._shake_anim = seq
        seq.start()

    # ------------------------------------------------------------------ drag
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_from = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag_from is not None:
            self.move(e.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, _):
        self._drag_from = None
