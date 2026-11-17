"""
Global paths and constants.

This module must stay free of Qt imports: it is used by the lightweight
"forward the command to the running instance" path in main.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Study Hub"
APP_ID = "StudyHub"
APP_USER_MODEL_ID = "StudyHub.Desktop.App"
VERSION = "1.9.0"
IS_FROZEN = getattr(sys, "frozen", False)  # True inside the PyInstaller-built StudyHub.exe

# Project root (folder that contains main.py) — or the exe's folder in the packaged app
BASE_DIR = Path(sys.executable).parent if IS_FROZEN else Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    """User data lives in %APPDATA%\\StudyHub so it survives app updates."""
    root = os.environ.get("APPDATA") or str(Path.home())
    path = Path(root) / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_DIR = _data_dir()
DB_PATH = DATA_DIR / "studyhub.db"
BACKUP_DIR = DATA_DIR / "backups"
ICON_PATH = DATA_DIR / "studyhub.ico"

# Name of the named pipe / instance lock (per Windows user). STUDYHUB_INSTANCE lets automated
# tests run beside the real app without ever talking to it.
PIPE_NAME = f"{APP_ID}-{os.environ.get('USERNAME', 'user')}{os.environ.get('STUDYHUB_INSTANCE', '')}"

# Global (system-wide) hotkeys. Users can change them in Settings.
DEFAULT_HOTKEYS: dict[str, str] = {
    "quick_note": "Ctrl+Alt+N",         # floating quick-note window
    "capture_selection": "Ctrl+Alt+S",  # save the selected text of ANY app as a note
    "clipboard_link": "Ctrl+Alt+L",     # pin the link that is in the clipboard
    "toggle_window": "Ctrl+Alt+H",      # show / hide Study Hub
    "command_palette": "Ctrl+Alt+K",    # open the search / command palette
    "paste_to_desktop": "Ctrl+Alt+V",   # clipboard (files / link / text) -> Study Hub desktop
    "lock_screen": "Ctrl+Alt+D",        # Always On Display lock screen
    "explorer": "Ctrl+Alt+E",           # Study Hub Explorer (file manager)
    "clipboard_history": "Ctrl+Alt+C",  # clipboard history popup
    "screenshot": "Ctrl+Alt+P",         # select part of the screen -> annotate / copy / OCR / note
    "snippets": "Ctrl+Alt+J",           # code snippets: find one and paste it into the program you're in
}

DEFAULT_LANGUAGE = "ar"


def launch_command() -> list[str]:
    """Command line that starts this app (used by Explorer menu and autostart)."""
    if IS_FROZEN:  # packaged with PyInstaller: the exe itself (already windowless)
        return [sys.executable]
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # no console window
    return [str(pythonw if pythonw.exists() else exe), str(BASE_DIR / "main.py")]
