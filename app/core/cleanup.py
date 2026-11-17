"""
Free up disk space (no Qt here; the Storage page runs these in a background thread).

Every category has scan() -> (bytes, files) and clean() -> bytes freed. What gets touched:

  recycle_bin    Windows Recycle Bin (emptied for good)
  temp           your %TEMP% files not changed for 2 days (files in use are skipped)
  browser_cache  Chrome / Edge / Brave / Firefox *cache folders only* — never history, cookies,
                 passwords or bookmarks; skipped while that browser is running
  crash_dumps    %LOCALAPPDATA%\\CrashDumps and Windows error reports
  dev_cache      pip / npm / NuGet download caches (re-downloaded when needed)
  backups        Study Hub backups beyond the newest N (the newest is always kept) + old broken-* copies
  orphans        attachment folders of notes that no longer exist
  thumbnails     Explorer thumbnail cache (rebuilt automatically; files in use are skipped)

Your own files (large files, old downloads) are never deleted here — the page sends them to the
Recycle Bin, so they stay recoverable.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import string
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.config import BACKUP_DIR, DATA_DIR

DAY = 86400
TEMP_AGE = 2 * DAY  # temp files untouched for 2 days (installers / open apps keep theirs fresh)


# ------------------------------------------------------------------- helpers
def env_path(var: str, *parts: str) -> Path:
    return Path(os.environ.get(var, ""), *parts)


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _walk_files(root: Path, min_age: float = 0, stop: Callable[[], bool] = lambda: False):
    """Yields (path, size) for files under root older than min_age seconds; never follows links."""
    if not root.is_dir():
        return
    now = time.time()
    for dirpath, dirnames, files in os.walk(root, followlinks=False):
        if stop():
            return
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if min_age and now - st.st_mtime < min_age:
                continue
            yield Path(p), st.st_size


def folder_usage(roots, min_age: float = 0, stop=lambda: False) -> tuple[int, int]:
    size = count = 0
    for root in roots:
        for _, s in _walk_files(Path(root), min_age, stop):
            size += s
            count += 1
    return size, count


def delete_contents(roots, min_age: float = 0, stop=lambda: False) -> int:
    """Deletes files (older than min_age) under each root, then empty folders. Root folders stay.
    Files that are in use / protected are skipped. Returns bytes freed."""
    freed = 0
    for root in roots:
        root = Path(root)
        for path, size in list(_walk_files(root, min_age, stop)):
            try:
                os.chmod(path, 0o666)
                path.unlink()
                freed += size
            except OSError:
                pass
        for dirpath, dirnames, files in os.walk(root, topdown=False):
            if Path(dirpath) != root:
                try:
                    os.rmdir(dirpath)  # only succeeds when empty
                except OSError:
                    pass
    return freed


def running(*exe_names: str) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
    except Exception:
        return False
    return any(f'"{name.lower()}"' in out for name in exe_names)


# ----------------------------------------------------------------- drives
def drives() -> list[dict]:
    """Fixed (local) drives with total / used / free bytes."""
    result = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(root) != 3:  # DRIVE_FIXED
            continue
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        name = ctypes.create_unicode_buffer(261)
        ctypes.windll.kernel32.GetVolumeInformationW(root, name, 261, None, None, None, None, 0)
        result.append({"root": root, "label": name.value, "total": usage.total, "used": usage.used,
                       "free": usage.free, "system": root[0] == os.environ.get("SystemDrive", "C:")[0]})
    return result


# ------------------------------------------------------------- recycle bin
class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]


def recycle_bin_info() -> tuple[int, int]:
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
        return 0, 0
    return int(info.i64Size), int(info.i64NumItems)


def empty_recycle_bin() -> int:
    size, _ = recycle_bin_info()
    SHERB_NOCONFIRMATION, SHERB_NOPROGRESSUI, SHERB_NOSOUND = 0x1, 0x2, 0x4
    flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
    return size if result in (0, -2147418113) else 0  # E_UNEXPECTED = already empty


# ------------------------------------------------------------ categories
BROWSERS = [  # (exe, profiles root under %LOCALAPPDATA%, cache sub-folders inside each profile)
    ("chrome.exe", ("Google", "Chrome", "User Data"), ("Cache", "Code Cache", "GPUCache", "Service Worker/CacheStorage")),
    ("msedge.exe", ("Microsoft", "Edge", "User Data"), ("Cache", "Code Cache", "GPUCache", "Service Worker/CacheStorage")),
    ("brave.exe", ("BraveSoftware", "Brave-Browser", "User Data"), ("Cache", "Code Cache", "GPUCache")),
]


def browser_cache_dirs() -> list[tuple[str, Path]]:
    found = []
    for exe, root_parts, subs in BROWSERS:
        root = env_path("LOCALAPPDATA", *root_parts)
        if not root.is_dir():
            continue
        for profile in root.iterdir():
            if profile.is_dir() and (profile.name == "Default" or profile.name.startswith("Profile ")):
                for sub in subs:
                    d = profile / sub
                    if d.is_dir():
                        found.append((exe, d))
        for shared in ("ShaderCache", "GrShaderCache"):
            if (root / shared).is_dir():
                found.append((exe, root / shared))
    ff = env_path("LOCALAPPDATA", "Mozilla", "Firefox", "Profiles")
    if ff.is_dir():
        for profile in ff.iterdir():
            if (profile / "cache2").is_dir():
                found.append(("firefox.exe", profile / "cache2"))
    return found


def dev_cache_dirs() -> list[Path]:
    candidates = [env_path("LOCALAPPDATA", "pip", "Cache"), env_path("LOCALAPPDATA", "npm-cache"),
                  env_path("APPDATA", "npm-cache"), env_path("LOCALAPPDATA", "NuGet", "v3-cache"),
                  env_path("LOCALAPPDATA", "NuGet", "plugins-cache"), env_path("LOCALAPPDATA", "Yarn", "Cache")]
    return [p for p in candidates if p.is_dir()]


@dataclass
class Category:
    key: str
    glyph: str
    permanent: bool             # True: deleted for good · False: Recycle Bin
    default_on: bool
    safe_auto: bool             # may run in the weekly automatic clean


def backup_files_to_remove(keep: int) -> list[Path]:
    if not BACKUP_DIR.is_dir():
        return []
    files = sorted((p for p in BACKUP_DIR.glob("*.db")), key=lambda p: p.stat().st_mtime, reverse=True)
    old = files[max(1, keep):]
    old += [p for p in DATA_DIR.glob("broken-*") if p.is_dir() and time.time() - p.stat().st_mtime > 30 * DAY]
    return old


def orphan_attachment_dirs(note_ids: set[int]) -> list[Path]:
    root = DATA_DIR / "attachments"
    if not root.is_dir():
        return []
    return [d for d in root.iterdir() if d.is_dir() and d.name.isdigit() and int(d.name) not in note_ids]


def _paths_usage(paths: list[Path]) -> tuple[int, int]:
    size = count = 0
    for p in paths:
        if p.is_file():
            size += p.stat().st_size
            count += 1
        else:
            s, c = folder_usage([p])
            size += s
            count += c
    return size, count


def _remove_paths(paths: list[Path]) -> int:
    freed = 0
    for p in paths:
        s, _ = _paths_usage([p])
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            freed += s
        except OSError:
            pass
    return freed


class Cleaner:
    """scan(key) / clean(key) for every category. note_ids and keep_backups come from the app."""

    CATEGORIES = [
        Category("recycle_bin", "", True, True, False),
        Category("temp", "", True, True, True),
        Category("browser_cache", "", True, True, False),
        Category("crash_dumps", "", True, True, True),
        Category("thumbnails", "", True, False, False),
        Category("dev_cache", "", True, False, False),
        Category("backups", "", True, True, True),
        Category("orphans", "", False, True, True),
    ]

    def __init__(self, note_ids: Callable[[], set[int]], keep_backups: Callable[[], int]):
        self.note_ids = note_ids
        self.keep_backups = keep_backups

    def paths(self, key: str) -> list[Path]:
        """Folders the user can open to look (Open folder button)."""
        return {
            "temp": [env_path("TEMP")],
            "browser_cache": [d for _, d in browser_cache_dirs()],
            "crash_dumps": [env_path("LOCALAPPDATA", "CrashDumps"),
                            env_path("LOCALAPPDATA", "Microsoft", "Windows", "WER")],
            "thumbnails": [env_path("LOCALAPPDATA", "Microsoft", "Windows", "Explorer")],
            "dev_cache": dev_cache_dirs(),
            "backups": [BACKUP_DIR],
            "orphans": [DATA_DIR / "attachments"],
        }.get(key, [])

    def blocked_by(self, key: str) -> list[str]:
        """Running programs that must be closed first (browser caches)."""
        if key != "browser_cache":
            return []
        exes = sorted({exe for exe, _ in browser_cache_dirs()})
        return [e for e in exes if running(e)]

    def scan(self, key: str, stop=lambda: False) -> tuple[int, int]:
        if key == "recycle_bin":
            return recycle_bin_info()
        if key == "temp":
            return folder_usage([env_path("TEMP")], min_age=TEMP_AGE, stop=stop)
        if key == "browser_cache":
            return folder_usage([d for _, d in browser_cache_dirs()], stop=stop)
        if key == "crash_dumps":
            return folder_usage(self.paths("crash_dumps"), stop=stop)
        if key == "thumbnails":
            folder = env_path("LOCALAPPDATA", "Microsoft", "Windows", "Explorer")
            files = list(folder.glob("thumbcache_*.db")) + list(folder.glob("iconcache_*.db")) if folder.is_dir() else []
            return sum(f.stat().st_size for f in files), len(files)
        if key == "dev_cache":
            return folder_usage(dev_cache_dirs(), stop=stop)
        if key == "backups":
            return _paths_usage(backup_files_to_remove(self.keep_backups()))
        if key == "orphans":
            return _paths_usage(orphan_attachment_dirs(self.note_ids()))
        return 0, 0

    def clean(self, key: str, stop=lambda: False) -> int:
        if key == "recycle_bin":
            return empty_recycle_bin()
        if key == "temp":
            return delete_contents([env_path("TEMP")], min_age=TEMP_AGE, stop=stop)
        if key == "browser_cache":
            busy = set(self.blocked_by(key))
            return delete_contents([d for exe, d in browser_cache_dirs() if exe not in busy], stop=stop)
        if key == "crash_dumps":
            return delete_contents(self.paths("crash_dumps"), stop=stop)
        if key == "thumbnails":
            folder = env_path("LOCALAPPDATA", "Microsoft", "Windows", "Explorer")
            return _remove_paths(list(folder.glob("thumbcache_*.db")) + list(folder.glob("iconcache_*.db")))
        if key == "dev_cache":
            return delete_contents(dev_cache_dirs(), stop=stop)
        if key == "backups":
            return _remove_paths(backup_files_to_remove(self.keep_backups()))
        if key == "orphans":
            from app.core import fileops
            dirs = orphan_attachment_dirs(self.note_ids())
            size, _ = _paths_usage(dirs)
            return size if dirs and fileops.recycle([str(d) for d in dirs]) else 0
        return 0


# --------------------------------------------------------------- your files
USER_FOLDERS = ("Downloads", "Desktop", "Documents", "Videos", "Pictures", "Music")


def user_roots() -> list[Path]:
    home = Path.home()
    roots = []
    for name in USER_FOLDERS:
        p = home / name
        if not p.is_dir() and (home / "OneDrive" / name).is_dir():
            p = home / "OneDrive" / name
        if p.is_dir():
            roots.append(p)
    return roots


def large_files(min_bytes: int, roots=None, limit: int = 150, stop=lambda: False) -> list[dict]:
    found = []
    for root in roots or user_roots():
        for dirpath, dirnames, files in os.walk(root, followlinks=False):
            if stop():
                break
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in
                           ("node_modules", "$recycle.bin", "appdata")]
            for f in files:
                p = os.path.join(dirpath, f)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if st.st_size >= min_bytes:
                    found.append({"path": p, "size": st.st_size, "mtime": st.st_mtime})
    return sorted(found, key=lambda x: x["size"], reverse=True)[:limit]


def old_downloads(days: int, folder: Path | None = None) -> list[dict]:
    folder = folder or next((r for r in user_roots() if r.name == "Downloads"), None)
    if not folder or not folder.is_dir():
        return []
    cutoff = time.time() - days * DAY
    items = []
    for entry in folder.iterdir():
        try:
            st = entry.stat()
        except OSError:
            continue
        if st.st_mtime < cutoff:
            size = st.st_size if entry.is_file() else folder_usage([entry])[0]
            items.append({"path": str(entry), "size": size, "mtime": st.st_mtime, "folder": entry.is_dir()})
    return sorted(items, key=lambda x: x["size"], reverse=True)


def app_data_usage() -> list[tuple[str, int]]:
    """Study Hub's own folders: database, backups, attachments, icons, sounds, logs."""
    parts = [("database", sum(p.stat().st_size for p in DATA_DIR.glob("studyhub.db*") if p.is_file())),
             ("backups", folder_usage([BACKUP_DIR])[0]),
             ("attachments", folder_usage([DATA_DIR / "attachments"])[0]),
             ("icons", folder_usage([DATA_DIR / "icons"])[0]),
             ("sounds", folder_usage([DATA_DIR / "sounds"])[0]),
             ("logs", sum(p.stat().st_size for p in DATA_DIR.glob("*.log*") if p.is_file()))]
    return parts
