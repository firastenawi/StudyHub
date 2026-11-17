"""
Never run with another app's redirected AppData.

Programs started from a packaged (MSIX) app — e.g. a terminal or assistant inside one — can inherit its
file-system redirection: what they write to %APPDATA% silently lands in
%LOCALAPPDATA%\\Packages\\<that app>\\LocalCache\\Roaming instead. Study Hub started that way would use a
second, separate data folder (it happened: the data seemed to "disappear" depending on how the app was
opened). So before starting, Study Hub writes a probe file; if it shows up inside a package folder, it
restarts itself through WMI — outside the package — and this copy exits.
"""
from __future__ import annotations

import base64
import glob
import os
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def _default_appdata() -> bool:
    """Only the user's real data folder is checked (tests and the self-test use their own APPDATA)."""
    appdata = os.environ.get("APPDATA", "")
    return bool(appdata) and Path(appdata).resolve() == (Path.home() / "AppData" / "Roaming").resolve()


def redirected_appdata() -> str:
    """The package folder %APPDATA% is redirected into, or "" when writes go to the real folder."""
    if os.environ.get("STUDYHUB_NO_SHELL") or not _default_appdata():
        return ""
    folder = Path(os.environ["APPDATA"]) / "StudyHub"
    name = f".redirect-probe-{os.getpid()}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"")
        hits = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", "*", "LocalCache",
                                      "Roaming", "StudyHub", name))
    except OSError:
        return ""
    finally:
        try:
            (folder / name).unlink()
        except OSError:
            pass
    return hits[0].split(os.sep + "LocalCache")[0] if hits else ""


def relaunch_outside(args: list[str]) -> bool:
    """Starts this same program again through WMI (a process WMI creates is not inside the package)."""
    exe = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(sys.argv[0]).resolve())]
    command = subprocess.list2cmdline(exe + list(args)).replace("'", "''")
    script = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
              f"-Arguments @{{ CommandLine = '{command}' }}; exit $r.ReturnValue")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                              creationflags=CREATE_NO_WINDOW, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0
