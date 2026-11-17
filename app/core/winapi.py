"""Thin ctypes wrappers around the few Win32 functions the app needs."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
shell32.ShellExecuteW.restype = ctypes.c_void_p

VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, VK_C = 0x10, 0x11, 0x12, 0x5B, 0x5C, 0x43
KEYEVENTF_KEYUP = 0x0002
SW_SHOWNORMAL = 1


def set_app_user_model_id(app_id: str) -> None:
    """Gives the app its own taskbar identity (correct icon, grouping)."""
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except Exception:
        pass


def foreground_window_title() -> str:
    """Title of the window the user is currently working in."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def force_foreground(hwnd: int) -> None:
    try:
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
    except Exception:
        pass


def modifier_keys_down() -> bool:
    """True while Ctrl / Alt / Shift / Win is physically held down."""
    return any(user32.GetAsyncKeyState(vk) & 0x8000
               for vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))


def foreground_hwnd() -> int:
    return int(user32.GetForegroundWindow() or 0)


def send_ctrl_v() -> None:
    """Ctrl+V into whatever window has the focus (releases held Alt/Shift first)."""
    for vk in (VK_MENU, VK_SHIFT):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(0x56, 0, 0, 0)
    user32.keybd_event(0x56, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "_GUID":
        import uuid
        u = uuid.UUID(text)
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        g.Data4[:] = list(u.bytes[8:])
        return g


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("pwszVal", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]


_APPUSERMODEL = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
_IID_IPROPERTYSTORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"


def set_window_app_id(widget, app_id: str, relaunch_command: str = "", display_name: str = "",
                      icon_resource: str = "") -> bool:
    """Gives one window its own taskbar button (own icon, own group, pinnable) instead of the process' one.
    The relaunch values make a pinned button start this same app again (e.g. StudyHub.exe --app notes)."""
    try:
        hwnd = int(widget.winId())
        store = ctypes.c_void_p()
        iid = _GUID.parse(_IID_IPROPERTYSTORE)
        if shell32.SHGetPropertyStoreForWindow(wintypes.HWND(hwnd), ctypes.byref(iid), ctypes.byref(store)) != 0:
            return False
        vtbl = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        set_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_PROPERTYKEY),
                                       ctypes.POINTER(_PROPVARIANT))(vtbl[6])
        commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[7])
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
        fmt = _GUID.parse(_APPUSERMODEL)
        # PKEY_AppUserModel_ID 5, RelaunchCommand 2, RelaunchIconResource 3, RelaunchDisplayNameResource 4
        values = [(5, app_id)]
        if relaunch_command and display_name:
            values += [(2, relaunch_command), (4, display_name)] + ([(3, icon_resource)] if icon_resource else [])
        for pid, text in values[1:] + values[:1]:  # the ID last: Windows reads the relaunch values with it
            pv = _PROPVARIANT(vt=31, pwszVal=text)  # VT_LPWSTR
            set_value(store, ctypes.byref(_PROPERTYKEY(fmt, pid)), ctypes.byref(pv))
        commit(store)
        release(store)
        return True
    except Exception:
        return False


class _OPENASINFO(ctypes.Structure):
    _fields_ = [("pcszFile", wintypes.LPCWSTR), ("pcszClass", wintypes.LPCWSTR), ("oaifInFlags", ctypes.c_int)]


def open_with_dialog(path: str, parent_widget=None) -> bool:
    """Windows' own "Open with" chooser for a file (OAIF_ALLOW_REGISTRATION | OAIF_REGISTER_EXT | OAIF_EXEC)."""
    try:
        info = _OPENASINFO(path, None, 0x1 | 0x2 | 0x4)
        hwnd = int(parent_widget.winId()) if parent_widget is not None else 0
        return shell32.SHOpenWithDialog(wintypes.HWND(hwnd), ctypes.byref(info)) == 0
    except Exception:
        return False


def dark_title_bar(widget, dark: bool | None = None) -> None:
    """Native title bar in the app's theme for top-level windows that keep the Windows frame."""
    try:
        if dark is None:
            from app.ui.theme import Palette
            dark = not Palette.light()
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(int(widget.winId()), attr, ctypes.byref(value), 4) == 0:
                break
    except Exception:
        pass


def send_ctrl_c() -> None:
    """Simulates Ctrl+C in the foreground application."""
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_C, 0, 0, 0)
    user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def clipboard_sequence() -> int:
    """Increments every time the clipboard content changes."""
    return int(user32.GetClipboardSequenceNumber())


class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]


def battery() -> tuple[int, bool] | None:
    """(percent, charging) or None on a desktop PC without battery."""
    status = _PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None
    if status.BatteryFlag == 128 or status.BatteryLifePercent == 255:  # no system battery
        return None
    return int(status.BatteryLifePercent), status.ACLineStatus == 1


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> float:
    """Seconds since the last keyboard / mouse input anywhere in Windows."""
    info = _LastInput()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0


def foreground_is_fullscreen() -> bool:
    """True when another app (video, game, slides) fills the whole screen."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd or hwnd == user32.GetDesktopWindow() or hwnd == user32.GetShellWindow():
        return False
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    monitor = user32.MonitorFromWindow(hwnd, 2)

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(mi)):
        return False
    m = mi.rcMonitor
    return rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom


def lock_workstation() -> None:
    user32.LockWorkStation()


def shell_execute(target: str, args: str = "", workdir: str | None = None,
                  verb: str = "open") -> bool:
    """Opens a program / file / folder / URL like a double click. verb='runas' = admin."""
    result = shell32.ShellExecuteW(None, verb, target, args or None, workdir, SW_SHOWNORMAL)
    return (result or 0) > 32
