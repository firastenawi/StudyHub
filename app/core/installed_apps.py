"""
What "Add to Study Hub" offers without typing:

  installed_apps()  every program in the Windows Start menu (desktop programs: their .lnk shortcuts) plus Store
                    apps (Get-StartApps; started through shell:AppsFolder\\<id>)
  recent_files()    what you opened lately in Windows (the Recent folder), newest first
  common_folders()  Desktop, Downloads, Documents, Pictures, Videos, Music, OneDrive
Nothing is read except names and shortcut targets.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from app.core.logs import log

_SKIP_WORDS = ("uninstall", "uninst", "readme", "read me", "help", "license", "release notes", "website",
               "documentation", "manual", "changelog", "إزالة")


def _start_menu_dirs() -> list[Path]:
    dirs = [Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
            Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs"]
    return [d for d in dirs if d.is_dir()]


def _desktop_programs() -> list[dict]:
    found: dict[str, dict] = {}
    for root in _start_menu_dirs():
        for lnk in root.rglob("*.lnk"):
            name = lnk.stem
            low = name.lower()
            if any(w in low for w in _SKIP_WORDS):
                continue
            if low not in found:
                found[low] = {"name": name, "kind": "app", "target": str(lnk), "source": "start"}
    return list(found.values())


def _store_apps() -> list[dict]:
    """Store / UWP apps (their id has a "!"); desktop programs come from the shortcuts above."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(out.stdout.decode("utf-8", "replace") or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        log.info("Get-StartApps unavailable: %s", exc)
        return []
    if isinstance(data, dict):
        data = [data]
    apps = []
    for entry in data:
        name, app_id = entry.get("Name") or "", entry.get("AppID") or ""
        if "!" in app_id and name and not any(w in name.lower() for w in _SKIP_WORDS):
            apps.append({"name": name, "kind": "app", "target": f"shell:AppsFolder\\{app_id}", "source": "store"})
    return apps


_cache: list[dict] | None = None


def installed_apps(refresh: bool = False) -> list[dict]:
    """Sorted by name; Store apps whose name already has a desktop shortcut are left out."""
    global _cache
    if _cache is None or refresh:
        apps = _desktop_programs()
        names = {a["name"].lower() for a in apps}
        apps += [a for a in _store_apps() if a["name"].lower() not in names]
        _cache = sorted(apps, key=lambda a: a["name"].lower())
    return list(_cache)


def recent_files(limit: int = 40) -> list[dict]:
    """Windows' Recent folder: shortcuts to what you opened; only targets that still exist."""
    from PyQt6.QtCore import QFileInfo
    folder = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Recent"
    if not folder.is_dir():
        return []
    links = sorted(folder.glob("*.lnk"), key=lambda p: p.stat().st_mtime, reverse=True)
    out, seen = [], set()
    for lnk in links[:200]:
        target = QFileInfo(str(lnk)).symLinkTarget()
        if not target or target.lower() in seen or not os.path.exists(target):
            continue
        seen.add(target.lower())
        is_dir = os.path.isdir(target)
        out.append({"name": Path(target).name or target, "kind": "folder" if is_dir else "file", "target": os.path.normpath(target),
                    "source": "recent"})
        if len(out) >= limit:
            break
    return out


def common_folders() -> list[dict]:
    from PyQt6.QtCore import QStandardPaths as SP
    places = [SP.StandardLocation.DesktopLocation, SP.StandardLocation.DownloadLocation,
              SP.StandardLocation.DocumentsLocation, SP.StandardLocation.PicturesLocation,
              SP.StandardLocation.MoviesLocation, SP.StandardLocation.MusicLocation]
    out = []
    for place in places:
        path = SP.writableLocation(place)
        if path and os.path.isdir(path):
            out.append({"name": SP.displayName(place) or Path(path).name, "kind": "folder", "target": os.path.normpath(path),
                        "source": "common"})
    onedrive = os.environ.get("OneDrive")
    if onedrive and os.path.isdir(onedrive):
        out.append({"name": "OneDrive", "kind": "folder", "target": onedrive, "source": "common"})
    return out


# websites a student often wants one click away (name, address)
SUGGESTED_SITES = [
    ("Google Classroom", "https://classroom.google.com"), ("Gmail", "https://mail.google.com"),
    ("Google Drive", "https://drive.google.com"), ("YouTube", "https://www.youtube.com"),
    ("ChatGPT", "https://chatgpt.com"), ("Claude", "https://claude.ai"), ("GitHub", "https://github.com"),
    ("Microsoft Teams", "https://teams.microsoft.com"), ("Outlook", "https://outlook.office.com"),
    ("Cisco NetAcad", "https://www.netacad.com"), ("Google Scholar", "https://scholar.google.com"),
    ("Wikipedia", "https://www.wikipedia.org"), ("Stack Overflow", "https://stackoverflow.com"),
    ("W3Schools", "https://www.w3schools.com"), ("Coursera", "https://www.coursera.org"),
    ("Udemy", "https://www.udemy.com"), ("Notion", "https://www.notion.so"), ("Canva", "https://www.canva.com"),
    ("Google Translate", "https://translate.google.com"), ("WhatsApp Web", "https://web.whatsapp.com"),
]
