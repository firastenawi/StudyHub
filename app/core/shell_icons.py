"""The icon Windows itself shows for anything it can name: Store apps (shell:AppsFolder\\<id>), shortcuts,
files and folders — through IShellItemImageFactory (the same thumbnails Explorer draws)."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PyQt6.QtGui import QImage, QPixmap

_shell32 = ctypes.windll.shell32
_gdi32 = ctypes.windll.gdi32


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        import uuid
        u = uuid.UUID(text)
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        g.Data4[:] = list(u.bytes[8:])
        return g


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


_IID_IMAGE_FACTORY = "{BCC18B79-BA16-442F-80C4-8A59C30C463B}"
SIIGBF_ICONONLY = 0x4
SIIGBF_BIGGERSIZEOK = 0x1

_cache: dict[tuple[str, int], QPixmap] = {}


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                ("bmWidthBytes", ctypes.c_long), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def _hbitmap_to_image(hbmp) -> QImage:
    """32-bit bitmap → QImage keeping the transparency (Qt's own conversion drops it: black corners)."""
    bm = _BITMAP()
    if not _gdi32.GetObjectW(wintypes.HANDLE(hbmp), ctypes.sizeof(bm), ctypes.byref(bm)):
        return QImage()
    w, h = bm.bmWidth, abs(bm.bmHeight)
    info = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # top-down BGRA
    buf = (ctypes.c_ubyte * (w * h * 4))()
    dc = ctypes.windll.user32.GetDC(None)
    try:
        got = _gdi32.GetDIBits(dc, wintypes.HANDLE(hbmp), 0, h, buf, ctypes.byref(info), 0)
    finally:
        ctypes.windll.user32.ReleaseDC(None, dc)
    if not got:
        return QImage()
    data = bytes(buf)
    has_alpha = any(data[i] for i in range(3, len(data), 4))
    fmt = QImage.Format.Format_ARGB32_Premultiplied if has_alpha else QImage.Format.Format_RGB32
    return QImage(data, w, h, w * 4, fmt).copy()  # copy: the bytes object goes away


def shell_pixmap(path: str, size: int = 48) -> QPixmap:
    """Windows' icon for a parsing name (path or shell:AppsFolder\\…); a null pixmap if Windows has none."""
    key = (path.lower(), size)
    if key in _cache:
        return _cache[key]
    pm = QPixmap()
    factory = ctypes.c_void_p()
    try:
        hr = _shell32.SHCreateItemFromParsingName(wintypes.LPCWSTR(path), None,
                                                  ctypes.byref(_GUID.of(_IID_IMAGE_FACTORY)), ctypes.byref(factory))
        if hr == 0 and factory:
            vtbl = ctypes.cast(ctypes.cast(factory, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
            get_image = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, _SIZE, ctypes.c_int,
                                           ctypes.POINTER(wintypes.HBITMAP))(vtbl[3])
            hbmp = wintypes.HBITMAP()
            if get_image(factory, _SIZE(size, size), SIIGBF_ICONONLY | SIIGBF_BIGGERSIZEOK, ctypes.byref(hbmp)) == 0 and hbmp:
                img = _hbitmap_to_image(hbmp.value)
                _gdi32.DeleteObject(hbmp)
                if not img.isNull():
                    pm = QPixmap.fromImage(img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied))
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(factory)
    except Exception:
        pm = QPixmap()
    _cache[key] = pm
    return pm
