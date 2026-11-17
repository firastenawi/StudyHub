"""
"Save the selected text of any application" feature.

Flow when the user presses the capture hotkey:
  1. remember the title of the active window (used as the note's source),
  2. wait until the user releases Ctrl/Alt/Shift (otherwise our Ctrl+C would
     become Ctrl+Alt+C),
  3. simulate Ctrl+C in that application,
  4. read the clipboard once it changes, then restore the previous clipboard.
Everything is timer-driven so the UI never freezes.
"""
from __future__ import annotations

from PyQt6.QtCore import QMimeData, QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from app.core import winapi

_TICK_MS = 25
_KEY_RELEASE_TIMEOUT_MS = 1200
_CLIPBOARD_TIMEOUT_MS = 700


def _clone_mime(source: QMimeData | None) -> QMimeData | None:
    if source is None:
        return None
    clone = QMimeData()
    for fmt in source.formats():
        clone.setData(fmt, source.data(fmt))
    return clone


class SelectionCapture(QObject):
    captured = pyqtSignal(str, str)  # (selected text, source window title)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._stage = "idle"
        self._elapsed = 0
        self._source = ""
        self._sequence = 0
        self._backup: QMimeData | None = None

    def capture(self) -> None:
        if self._stage != "idle":
            return
        self._source = winapi.foreground_window_title()
        self._stage, self._elapsed = "wait_keys", 0
        self._timer.start()

    def _tick(self) -> None:
        self._elapsed += _TICK_MS
        if self._stage == "wait_keys":
            if winapi.modifier_keys_down() and self._elapsed < _KEY_RELEASE_TIMEOUT_MS:
                return
            self._backup = _clone_mime(QApplication.clipboard().mimeData())
            self._sequence = winapi.clipboard_sequence()
            winapi.send_ctrl_c()
            self._stage, self._elapsed = "wait_clipboard", 0
        elif self._stage == "wait_clipboard":
            changed = winapi.clipboard_sequence() != self._sequence
            if not changed and self._elapsed < _CLIPBOARD_TIMEOUT_MS:
                return
            self._timer.stop()
            self._stage = "idle"
            text = QApplication.clipboard().text() if changed else ""
            if changed and self._backup is not None:
                QApplication.clipboard().setMimeData(self._backup)  # give the user's clipboard back
            self._backup = None
            self.captured.emit(text.strip(), self._source)
