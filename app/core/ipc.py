"""
Single-instance support: the *client* side.

A second launch of the app (double click, Explorer context menu, autostart...)
does not open a second window. It forwards its command to the running instance
through a Windows named pipe and exits.

This file is pure Python (no Qt import) so that a context-menu click returns in
milliseconds. The server side lives in ipc_server.py and uses QLocalServer,
which on Windows is a named pipe with exactly the same name.
"""
from __future__ import annotations

import ctypes
import json
import time

from app.config import PIPE_NAME

PIPE_PATH = "\\\\.\\pipe\\" + PIPE_NAME
ERROR_ALREADY_EXISTS = 183
_mutex_handle = None  # kept for the whole process lifetime


def acquire_instance_lock() -> bool:
    """True if this is the first Study Hub process of this Windows user.

    A named mutex exists from the very first line of startup, long before the pipe
    server is ready, so two quick double-clicks can never both open the database."""
    global _mutex_handle
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _mutex_handle = kernel32.CreateMutexW(None, False, f"Local\\{PIPE_NAME}-instance")
    if not _mutex_handle:
        # ERROR_ACCESS_DENIED: the mutex belongs to a copy running as administrator
        return False
    return kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def show_already_running_message() -> None:
    """Native message box (Qt isn't loaded in a forwarding launch)."""
    text = ("Study Hub يعمل حالياً بنسخة ثانية لا تستجيب أو تعمل كمسؤول (Run as administrator)، "
            "مثلاً من Visual Studio.\n"
            "سكّرها من مدير المهام (Task Manager ← pythonw.exe ← End task) أو من Visual Studio، "
            "وبعدين افتح البرنامج من جديد.\n\n"
            "Another copy of Study Hub is running but not responding, or it runs as administrator "
            "(e.g. started from Visual Studio). Close it in Task Manager (pythonw.exe → End task) "
            "or in Visual Studio, then start Study Hub again.")
    ctypes.windll.user32.MessageBoxW(None, text, "Study Hub", 0x30 | 0x40000)  # warning + topmost


def forward_to_running_instance(command: dict, wait_seconds: float = 20.0) -> bool:
    """Used by a second launch: waits until the first instance is listening, then forwards."""
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if send_command(command):
            return True
        time.sleep(0.25)  # the first instance is still starting (e.g. upgrading the database)
    return False


def send_command(command: dict, retries: int = 10) -> bool:
    """Send `command` to the running instance. Returns False if none is running."""
    payload = (json.dumps(command, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        # We were started by a user click, so we own the "foreground right".
        # Hand it to the running instance so its window can come to the front.
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception:
        pass

    for _ in range(retries):
        try:
            with open(PIPE_PATH, "wb", buffering=0) as pipe:
                pipe.write(payload)
            return True
        except FileNotFoundError:
            return False  # no instance is listening
        except OSError:
            time.sleep(0.05)  # pipe busy: another client is connected right now
    return False
