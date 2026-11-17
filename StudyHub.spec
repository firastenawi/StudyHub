# PyInstaller build of Study Hub — run build.bat (or: pyinstaller StudyHub.spec --noconfirm).
# onedir (a folder with StudyHub.exe): starts much faster than onefile and updates cleanly.
# Qt WebEngine / Pdf / Multimedia are picked up by PyInstaller's own PyQt6 hooks.

from PyInstaller.utils.hooks import collect_submodules

# the terminal (pywinpty): its helper programs are started at run time, so PyInstaller can't see them
import os as _os
import winpty as _winpty
_WINPTY = _os.path.dirname(_winpty.__file__)
_WINPTY_BIN = [(_os.path.join(_WINPTY, f), "winpty") for f in ("OpenConsole.exe", "winpty-agent.exe", "conpty.dll", "winpty.dll")
               if _os.path.exists(_os.path.join(_WINPTY, f))]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=_WINPTY_BIN,
    datas=[("app/assets", "app/assets")],   # KaTeX + Mermaid for the note preview (offline)
    hiddenimports=[
        "app.selftest",                 # only imported by --self-test
        *collect_submodules("app.plugins"),   # built-in plugins are imported by name at run time
        "paramiko",
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtWebChannel",           # the terminal's page talks to Python through it
        "winpty",
        "PyQt6.QtPdf",
        "PyQt6.QtPdfWidgets",
        "PyQt6.QtMultimedia",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "test", "PyQt6.QtQuick3D",
              "PyQt6.QtBluetooth", "PyQt6.QtSensors", "PyQt6.QtSerialPort", "PyQt6.QtDesigner"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StudyHub",
    icon="installer/studyhub.ico",
    version="installer/version_info.txt",
    console=False,                      # windowed: no console window
    upx=False,                          # UPX breaks Qt WebEngine and trips antivirus
    debug=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="StudyHub")

# build_info.json next to StudyHub.exe: what the in-app updater compares (app/core/updater.py)
import datetime as _dt
import json as _json
import pathlib as _pl
import re as _re

_version = _re.search(r'VERSION = "([^"]+)"', _pl.Path("app/config.py").read_text(encoding="utf-8")).group(1)
_notes = _pl.Path("installer/whats_new.json")
_pl.Path(DISTPATH, "StudyHub", "build_info.json").write_text(_json.dumps({
    "version": _version,
    "built": _dt.datetime.now().isoformat(timespec="seconds"),
    "notes": _json.loads(_notes.read_text(encoding="utf-8")) if _notes.exists() else {},
}, ensure_ascii=False, indent=1), encoding="utf-8")
