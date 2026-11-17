"""Helpers for launcher items: detect kind, build titles, open targets."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from app.core import winapi

APP_EXTENSIONS = {".exe", ".lnk", ".bat", ".cmd", ".msc", ".url", ".appref-ms"}
_DOMAIN_RE = re.compile(r"^[\w.-]+\.[a-z]{2,}(:\d+)?(/.*)?$", re.IGNORECASE)


def normalize_url(text: str) -> str | None:
    """'youtube.com' -> 'https://youtube.com'. None if it is not a URL."""
    text = (text or "").strip()
    if not text or " " in text or "\n" in text:
        return None
    if re.match(r"^[a-z][a-z0-9+.-]*://", text, re.IGNORECASE):
        return text
    if _DOMAIN_RE.match(text):
        return "https://" + text
    return None


def detect_kind(target: str) -> str:
    if normalize_url(target) and not os.path.exists(target):
        return "url"
    path = Path(target)
    if path.is_dir():
        return "folder"
    if path.suffix.lower() in APP_EXTENSIONS:
        return "app"
    return "file"


def title_from_target(target: str, kind: str) -> str:
    if kind == "url":
        host = urlparse(normalize_url(target) or target).netloc or target
        host = host.split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        name = host.split(".")[0] if host.count(".") >= 1 else host
        return name.capitalize() or target
    path = Path(target)
    return path.stem if kind in ("app", "file") else (path.name or str(path))


def open_target(kind: str, target: str, args: str = "", admin: bool = False) -> bool:
    """Opens an item exactly like a double click in Windows. Returns success."""
    if kind == "url":
        target = normalize_url(target) or target
        return winapi.shell_execute(target)
    workdir = str(Path(target).parent) if kind in ("app", "file") and Path(target).parent.exists() else None
    return winapi.shell_execute(target, args, workdir, "runas" if admin else "open")


def reveal_in_explorer(path: str) -> None:
    """Opens Explorer with the item selected."""
    if os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
