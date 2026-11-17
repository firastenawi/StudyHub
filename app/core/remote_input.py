"""
Phone remote: mouse, keyboard and media keys sent to Windows (SendInput), plus the monitor list.

Only a fixed set of named keys is accepted (no arbitrary key codes), so a phone can drive slides,
a touchpad, volume / media and type text, nothing more. Called in the GUI thread.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_WHEEL = 0x0008, 0x0010, 0x0800
MOUSEEVENTF_HWHEEL, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x1000, 0x8000, 0x4000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79

# name -> (virtual key, extended); combos are lists of names
KEYS = {
    "right": (0x27, True), "left": (0x25, True), "up": (0x26, True), "down": (0x28, True),
    "pagedown": (0x22, True), "pageup": (0x21, True), "home": (0x24, True), "end": (0x23, True),
    "space": (0x20, False), "enter": (0x0D, False), "esc": (0x1B, False), "tab": (0x09, False),
    "backspace": (0x08, False), "delete": (0x2E, True), "f5": (0x74, False), "f11": (0x7A, False),
    "b": (0x42, False), "w": (0x57, False),
    "shift": (0x10, False), "ctrl": (0x11, False), "alt": (0x12, False), "win": (0x5B, True),
    "vol_up": (0xAF, True), "vol_down": (0xAE, True), "mute": (0xAD, True),
    "play": (0xB3, True), "next_track": (0xB0, True), "prev_track": (0xB1, True),
}
COMBOS = {  # what the phone's buttons send
    "next": ["right"], "prev": ["left"], "start_show": ["f5"], "start_here": ["shift", "f5"], "end_show": ["esc"],
    "black": ["b"], "white": ["w"], "alt_tab": ["alt", "tab"], "show_desktop": ["win", "d"],
    "fullscreen": ["f11"], "close_tab": ["ctrl", "w"],
}
KEYS["d"] = (0x44, False)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _U(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def _send(*inputs: _INPUT) -> None:
    arr = (_INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def _key(vk: int, up: bool, extended: bool = False) -> _INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    return _INPUT(INPUT_KEYBOARD, _U(ki=_KEYBDINPUT(vk, 0, flags, 0, 0)))


def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> _INPUT:
    return _INPUT(INPUT_MOUSE, _U(mi=_MOUSEINPUT(dx, dy, ctypes.c_uint32(data).value, flags, 0, 0)))


def press(action: str) -> bool:
    """A named action (COMBOS) or a single named key (KEYS). Unknown names are refused."""
    names = COMBOS.get(action) or ([action] if action in KEYS else None)
    if not names:
        return False
    keys = [KEYS[n] for n in names]
    _send(*[_key(vk, False, ext) for vk, ext in keys], *[_key(vk, True, ext) for vk, ext in reversed(keys)])
    return True


def type_text(text: str) -> None:
    """Types text as Unicode characters (works for Arabic too, whatever the keyboard layout)."""
    events = []
    for ch in text[:2000]:
        if ch == "\n":
            events += [_key(0x0D, False), _key(0x0D, True)]
            continue
        data = ch.encode("utf-16-le")  # surrogate pairs -> two units
        for i in range(0, len(data), 2):
            code = int.from_bytes(data[i:i + 2], "little")
            events.append(_INPUT(INPUT_KEYBOARD, _U(ki=_KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0))))
            events.append(_INPUT(INPUT_KEYBOARD, _U(ki=_KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0))))
    for i in range(0, len(events), 200):
        _send(*events[i:i + 200])


def move(dx: float, dy: float) -> None:
    _send(_mouse(MOUSEEVENTF_MOVE, int(max(-400, min(400, dx))), int(max(-400, min(400, dy)))))


def click(button: str = "left", double: bool = False) -> None:
    down, up = ((MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP) if button == "right"
                else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))
    seq = [_mouse(down), _mouse(up)] * (2 if double else 1)
    _send(*seq)


def scroll(dy: float, dx: float = 0) -> None:
    if dy:
        _send(_mouse(MOUSEEVENTF_WHEEL, data=int(max(-1200, min(1200, -dy * 3)))))
    if dx:
        _send(_mouse(MOUSEEVENTF_HWHEEL, data=int(max(-1200, min(1200, dx * 3)))))


def click_at(rect: tuple[int, int, int, int], fx: float, fy: float, button: str = "left") -> None:
    """Clicks at a spot of a monitor (rect = left, top, right, bottom in physical pixels; fx/fy 0..1)."""
    left, top, right, bottom = rect
    x = left + max(0.0, min(1.0, fx)) * (right - left - 1)
    y = top + max(0.0, min(1.0, fy)) * (bottom - top - 1)
    vx, vy = user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw, vh = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    nx = int((x - vx) * 65535 / max(1, vw - 1))
    ny = int((y - vy) * 65535 / max(1, vh - 1))
    _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))
    click(button)


class _MONITORINFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


def monitors() -> dict[str, tuple[int, int, int, int]]:
    """Device name (\\\\.\\DISPLAY1 …) -> physical rectangle."""
    found: dict[str, tuple[int, int, int, int]] = {}
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                              wintypes.LPARAM)

    def cb(hmon, _hdc, _rect, _data):
        info = _MONITORINFOEX()
        info.cbSize = ctypes.sizeof(_MONITORINFOEX)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            found[info.szDevice] = (r.left, r.top, r.right, r.bottom)
        return True
    user32.EnumDisplayMonitors(None, None, proc(cb), 0)
    return found
