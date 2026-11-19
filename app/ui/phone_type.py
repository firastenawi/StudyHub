"""
Type on phone: a small window; what you type here goes into the text field that's focused on the phone.

Letters are sent in short batches (so fast typing doesn't send one request per key); Backspace and Enter go as
keys. Ctrl+V pastes the laptop's clipboard into the phone's field. Any keyboard layout works, Arabic included.
On the phone this needs the Study Hub accessibility service ("Type from laptop"), switched on by you.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QKeySequence
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.core import winapi
from app.core.i18n import tr
from app.ui.theme import Icons, Palette, Theme, qcolor
from app.ui.widgets.common import GlyphLabel, label

BATCH_MS = 90
QColor_Hex = QColor.NameFormat.HexArgb


class KeyPad(QFrame):
    """Takes the keyboard while focused and shows what was sent."""

    def __init__(self, win: "TypeWindow"):
        super().__init__()
        self.win = win
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
        self.setObjectName("KeyPad")
        self.setMinimumHeight(120)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        self.echo = QLabel("")
        self.echo.setWordWrap(True)
        self.echo.setFont(QFont(self.font().family(), 12))
        self.echo.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeading)
        lay.addWidget(self.echo, 1)
        self.hint = label(tr("type_hint"), 9, role="Faint", wrap=True)
        lay.addWidget(self.hint)
        self._style(False)

    def _style(self, focused: bool) -> None:
        border = Theme.accent() if focused else qcolor(Palette.text, 0.15).name(QColor_Hex)
        self.setStyleSheet(f"#KeyPad {{ background: {qcolor(Palette.text, 0.05).name(QColor_Hex)};"
                           f" border: 2px solid {border}; border-radius: 16px; }}")

    def focusInEvent(self, e):
        self._style(True)
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._style(False)
        super().focusOutEvent(e)

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.StandardKey.Paste):
            self.win.add_text(QApplication.clipboard().text())
        elif e.key() == Qt.Key.Key_Backspace:
            self.win.key("backspace")
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.win.key("enter")
        elif e.key() == Qt.Key.Key_Escape:
            self.win.close()
        elif e.text() and e.text().isprintable():
            self.win.add_text(e.text())
        else:
            super().keyPressEvent(e)

    def inputMethodEvent(self, e):  # input methods that compose characters
        if e.commitString():
            self.win.add_text(e.commitString())
        e.accept()


class TypeWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.ctl = ctl
        self.hub = ctl.phone_hub
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{tr('phone_type')} — {self.hub.device_name()}")
        from app.ui import apps as app_registry
        info = app_registry.find(ctl, "phone")
        if info:
            self.setWindowIcon(info.icon())
        self.resize(460, 300)
        self.setStyleSheet(f"TypeWindow {{ background: {Palette.bg}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(GlyphLabel(Icons.KEYBOARD, 14, Theme.accent()))
        hl.addWidget(label(tr("phone_type"), 12, QFont.Weight.DemiBold))
        hl.addStretch(1)
        self.status = label("", 9)
        hl.addWidget(self.status)
        lay.addWidget(head)
        self.pad = KeyPad(self)
        lay.addWidget(self.pad, 1)
        self.buffer = ""
        self.sent = ""
        self._flush_timer = QTimer(self, singleShot=True, interval=BATCH_MS, timeout=self.flush)
        self._status_timer = QTimer(self, interval=3000, timeout=self.refresh_status)
        self._status_timer.start()
        self.hub.changed.connect(self._hub_changed)
        self.refresh_status()
        winapi.dark_title_bar(self)

    # ---------------------------------------------------------------- sending
    def add_text(self, text: str) -> None:
        self.buffer += text
        self._echo(text)
        self._flush_timer.start()

    def key(self, name: str) -> None:
        self.flush()
        self.hub.type_key(name)
        if name == "backspace":
            self.sent = self.sent[:-1]
        else:
            self.sent += "⏎\n"
        self.pad.echo.setText(self.sent[-400:])

    def flush(self) -> None:
        self._flush_timer.stop()
        if self.buffer:
            self.hub.type_text(self.buffer)
            self.buffer = ""

    def _echo(self, text: str) -> None:
        self.sent += text
        self.pad.echo.setText(self.sent[-400:])

    # ---------------------------------------------------------------- status
    def _hub_changed(self, what: str) -> None:
        if what == "state":
            try:
                self.refresh_status()
            except RuntimeError:
                pass

    def refresh_status(self) -> None:
        st = self.hub.state()
        if not self.hub.online():
            text, color = tr("type_offline"), Palette.text_faint
        elif not st.get("type_access"):
            text, color = tr("type_need_access"), Palette.warning
        else:
            text, color = tr("type_ready"), Palette.success
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color};")

    def showEvent(self, e):
        super().showEvent(e)
        self.pad.setFocus()

    def closeEvent(self, e):
        self.flush()
        try:
            self.hub.changed.disconnect(self._hub_changed)
        except (TypeError, RuntimeError):
            pass
        super().closeEvent(e)
