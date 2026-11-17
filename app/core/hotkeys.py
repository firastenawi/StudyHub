"""
System-wide hotkeys (work even when Study Hub is hidden or in the background).

Uses the native Win32 RegisterHotKey API, so no extra package is needed.
A hidden native window receives WM_HOTKEY messages and we translate them into
a Qt signal carrying the action name (e.g. "quick_note").
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QWidget

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_user32 = ctypes.windll.user32
_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

K = Qt.Key
_SPECIAL_KEYS = {
    K.Key_Space: 0x20, K.Key_Return: 0x0D, K.Key_Enter: 0x0D, K.Key_Tab: 0x09,
    K.Key_Backspace: 0x08, K.Key_Insert: 0x2D, K.Key_Delete: 0x2E, K.Key_Home: 0x24,
    K.Key_End: 0x23, K.Key_PageUp: 0x21, K.Key_PageDown: 0x22, K.Key_Left: 0x25,
    K.Key_Up: 0x26, K.Key_Right: 0x27, K.Key_Down: 0x28, K.Key_Period: 0xBE,
    K.Key_Comma: 0xBC, K.Key_Minus: 0xBD, K.Key_Equal: 0xBB, K.Key_Plus: 0xBB,
    K.Key_Slash: 0xBF, K.Key_Semicolon: 0xBA, K.Key_Apostrophe: 0xDE,
    K.Key_BracketLeft: 0xDB, K.Key_BracketRight: 0xDD, K.Key_Backslash: 0xDC,
    K.Key_QuoteLeft: 0xC0,
}


def sequence_to_win32(text: str) -> tuple[int, int] | None:
    """'Ctrl+Alt+N' -> (modifiers, virtual-key). None if unsupported/unsafe."""
    seq = QKeySequence(text)
    if seq.isEmpty():
        return None
    combo = seq[0]
    key, mods = combo.key(), combo.keyboardModifiers()
    value = key.value

    is_function_key = K.Key_F1.value <= value <= K.Key_F24.value
    if K.Key_A.value <= value <= K.Key_Z.value or K.Key_0.value <= value <= K.Key_9.value:
        vk = value  # Qt and Win32 share ASCII codes for letters and digits
    elif is_function_key:
        vk = 0x70 + (value - K.Key_F1.value)
    else:
        vk = _SPECIAL_KEYS.get(key)
    if vk is None:
        return None

    m = 0
    if mods & Qt.KeyboardModifier.ControlModifier:
        m |= MOD_CONTROL
    if mods & Qt.KeyboardModifier.AltModifier:
        m |= MOD_ALT
    if mods & Qt.KeyboardModifier.ShiftModifier:
        m |= MOD_SHIFT
    if mods & Qt.KeyboardModifier.MetaModifier:
        m |= MOD_WIN
    if m == 0 and not is_function_key:
        return None  # a bare letter would hijack normal typing
    return m | MOD_NOREPEAT, vk


class _HotkeySink(QWidget):
    """Invisible native window that receives WM_HOTKEY."""

    def __init__(self, on_hotkey):
        super().__init__(None)
        self._on_hotkey = on_hotkey
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.hwnd = int(self.winId())  # forces creation of the native handle (never shown)

    def nativeEvent(self, event_type, message):
        # Note: calling super().nativeEvent() here crashes PyQt6 6.11 during window creation,
        # so unhandled messages simply return (False, 0) = "let Qt handle it".
        if message and bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._on_hotkey(int(msg.wParam))
                return True, 0
        return False, 0


class GlobalHotkeys(QObject):
    triggered = pyqtSignal(str)  # action name

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._sink = _HotkeySink(self._on_hotkey)
        self._actions: dict[int, str] = {}

    def register_all(self, mapping: dict[str, str]) -> list[str]:
        """(Re)registers every hotkey. Returns the actions that failed
        (invalid combination, or already taken by another program)."""
        self.unregister_all()
        failed = []
        for index, (action, text) in enumerate(mapping.items(), start=1):
            if not text:
                continue  # user disabled this hotkey
            parsed = sequence_to_win32(text)
            if parsed is None or not _user32.RegisterHotKey(self._sink.hwnd, index, *parsed):
                failed.append(action)
                continue
            self._actions[index] = action
        return failed

    def unregister_all(self) -> None:
        for hotkey_id in self._actions:
            _user32.UnregisterHotKey(self._sink.hwnd, hotkey_id)
        self._actions.clear()

    def _on_hotkey(self, hotkey_id: int) -> None:
        action = self._actions.get(hotkey_id)
        if action:
            self.triggered.emit(action)
