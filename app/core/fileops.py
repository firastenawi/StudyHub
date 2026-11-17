"""
File operations shared by the built-in explorer and the archive.

* delete = Recycle Bin (SHFileOperation + FOF_ALLOWUNDO) — never a permanent delete
* copy / move never overwrite: "name (1).ext"
* clipboard uses the same format as Windows Explorer (file URLs + "Preferred DropEffect"),
  so Ctrl+C / Ctrl+X / Ctrl+V work between Study Hub and Explorer in both directions
"""
from __future__ import annotations

import ctypes
import os
import shutil
from ctypes import wintypes
from pathlib import Path

from app.core.files import unique_destination

FO_DELETE = 3
FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x0004, 0x0010, 0x0040, 0x0400
DROPEFFECT_COPY, DROPEFFECT_MOVE = 1, 2


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


def recycle(paths: list[str]) -> bool:
    """Moves files/folders to the Recycle Bin (restorable). Returns True on success."""
    paths = [os.path.abspath(p) for p in paths if os.path.exists(p)]
    if not paths:
        return False
    op = _SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = "\0".join(paths) + "\0\0"  # double-NUL terminated list
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return result == 0 and not op.fAnyOperationsAborted


def copy_into(paths: list[str], folder: str) -> list[str]:
    done = []
    for p in paths:
        dst = unique_destination(folder, Path(p).name)
        if os.path.isdir(p):
            shutil.copytree(p, dst)
        else:
            shutil.copy2(p, dst)
        done.append(dst)
    return done


def move_into(paths: list[str], folder: str) -> list[str]:
    done = []
    for p in paths:
        if os.path.normcase(os.path.dirname(os.path.abspath(p))) == os.path.normcase(os.path.abspath(folder)):
            done.append(p)  # already there
            continue
        dst = unique_destination(folder, Path(p).name)
        shutil.move(p, dst)
        done.append(dst)
    return done


def new_folder(parent: str, name: str) -> str:
    path = unique_destination(parent, name)
    os.makedirs(path)
    return path


def rename(path: str, new_name: str) -> str:
    new_name = new_name.strip().strip(".")
    if not new_name or any(c in new_name for c in '\\/:*?"<>|'):
        raise OSError("invalid name")
    target = os.path.join(os.path.dirname(path), new_name)
    if os.path.exists(target) and os.path.normcase(target) != os.path.normcase(path):
        raise FileExistsError(target)
    os.rename(path, target)
    return target


def folder_size(path: str, limit_files: int = 20000) -> int:
    total = 0
    for i, (root, _, files) in enumerate(os.walk(path)):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
        if i > limit_files:
            break
    return total


# ------------------------------------------------------------------ clipboard
def clipboard_set_files(paths: list[str], cut: bool) -> None:
    from PyQt6.QtCore import QByteArray, QMimeData, QUrl
    from PyQt6.QtWidgets import QApplication
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(p) for p in paths])
    md.setText("\n".join(paths))
    effect = DROPEFFECT_MOVE if cut else DROPEFFECT_COPY
    md.setData('application/x-qt-windows-mime;value="Preferred DropEffect"',
               QByteArray(effect.to_bytes(4, "little")))
    QApplication.clipboard().setMimeData(md)


def clipboard_get_files() -> tuple[list[str], bool]:
    """(paths, is_cut) from the clipboard — works with files copied in Windows Explorer."""
    from PyQt6.QtWidgets import QApplication
    md = QApplication.clipboard().mimeData()
    if md is None or not md.hasUrls():
        return [], False
    paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile() and os.path.exists(u.toLocalFile())]
    raw = bytes(md.data('application/x-qt-windows-mime;value="Preferred DropEffect"'))
    cut = len(raw) >= 4 and int.from_bytes(raw[:4], "little") & DROPEFFECT_MOVE != 0
    return paths, cut


# ----------------------------------------------------------------- known folders
def known_folders() -> list[tuple[str, str]]:
    """(key, path) for Desktop / Documents / Downloads / Pictures / Music / Videos (relocations respected)."""
    import winreg
    names = {"desktop": "Desktop", "documents": "Personal", "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
             "pictures": "My Pictures", "music": "My Music", "videos": "My Video"}
    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            for k, value_name in names.items():
                try:
                    path = os.path.expandvars(winreg.QueryValueEx(key, value_name)[0])
                    if os.path.isdir(path):
                        out.append((k, path))
                except OSError:
                    pass
    except OSError:
        pass
    return out
