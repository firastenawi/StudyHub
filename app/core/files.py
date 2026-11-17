"""File-system helpers: listing folders, safe moves, the real Downloads folder."""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

MAX_ENTRIES = 3000  # keep huge folders responsive


def list_dir(path: str, show_hidden: bool = False) -> list[dict]:
    """Folders first, then files, both alphabetical. Never raises."""
    entries = []
    try:
        with os.scandir(path) as it:
            for i, entry in enumerate(it):
                if i >= MAX_ENTRIES:
                    break
                if not show_hidden and (entry.name.startswith(".") or entry.name.lower() in ("desktop.ini", "thumbs.db")):
                    continue
                try:
                    st = entry.stat()
                    is_dir = entry.is_dir()
                except OSError:
                    continue
                entries.append({"name": entry.name, "path": entry.path, "is_dir": is_dir,
                                "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime,
                                "ext": "" if is_dir else Path(entry.name).suffix.lower()})
    except OSError:
        return []
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def format_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def unique_destination(folder: str, name: str) -> str:
    """'notes.pdf' -> 'notes (1).pdf' if a file with that name already exists."""
    target = Path(folder) / name
    stem, suffix, n = target.stem, target.suffix, 1
    while target.exists():
        target = Path(folder) / f"{stem} ({n}){suffix}"
        n += 1
    return str(target)


def move_file(src: str, folder: str) -> str:
    """Moves `src` into `folder` without overwriting anything. Returns the new path."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    destination = unique_destination(folder, Path(src).name)
    shutil.move(src, destination)
    return destination


def downloads_dir() -> str:
    """The real Downloads folder (it can be relocated, e.g. to D:\\Downloads)."""
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            value, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
            path = os.path.expandvars(value)
            if os.path.isdir(path):
                return path
    except OSError:
        pass
    return str(Path.home() / "Downloads")


def is_inside(path: str, folder: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path).lower(), os.path.abspath(folder).lower()]) == \
            os.path.abspath(folder).lower()
    except ValueError:  # different drives
        return False
