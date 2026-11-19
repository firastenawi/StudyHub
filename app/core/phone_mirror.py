"""
"Phone screen on the laptop": a web page can't capture an Android screen, so Study Hub drives
scrcpy (free, open source, by Genymobile) — the phone's screen in a window on the laptop, controlled
with touch / mouse / keyboard, and (Android 11+) the phone's sound played on the laptop.

Needs USB debugging (Developer options) on the phone, once. Over Wi-Fi: Wireless debugging ›
"Pair device with pairing code" (pair once), then connect to the IP:port that screen shows.
scrcpy is found in Settings' path, PATH, a winget install or %LOCALAPPDATA%\\Programs\\scrcpy.
"""
from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

CREATE_NO_WINDOW, CREATE_NEW_CONSOLE = 0x08000000, 0x00000010
RELEASES_URL = "https://github.com/Genymobile/scrcpy/releases/latest"
WINGET_ID = "Genymobile.scrcpy"
ADDR_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}:\d{2,5}$")


def find_scrcpy(configured: str = "") -> str:
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("scrcpy")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    patterns = [os.path.join(local, "Microsoft", "WinGet", "Packages", "Genymobile.scrcpy*", "**", "scrcpy.exe"),
                os.path.join(local, "Microsoft", "WinGet", "Links", "scrcpy.exe"),
                os.path.join(local, "Programs", "scrcpy*", "**", "scrcpy.exe"),
                os.path.join(os.environ.get("USERPROFILE", ""), "scrcpy*", "**", "scrcpy.exe"),
                r"C:\scrcpy*\**\scrcpy.exe"]
    for pattern in patterns:
        hits = sorted(glob.glob(pattern, recursive=True), reverse=True)  # newest version first
        if hits:
            return hits[0]
    return ""


def adb_for(scrcpy: str) -> str:
    """scrcpy ships its own adb.exe next to it."""
    if scrcpy:
        local = Path(scrcpy).with_name("adb.exe")
        if local.is_file():
            return str(local)
    return shutil.which("adb") or ""


def valid_addr(addr: str) -> bool:
    return bool(ADDR_RE.match(addr.strip()))


def build_command(scrcpy: str, opts: dict) -> list[str]:
    """scrcpy arguments from the options in Phone › Phone screen."""
    cmd = [scrcpy, "--window-title=Study Hub · Phone"]
    if opts.get("serial"):
        cmd.append(f"--serial={opts['serial']}")
    elif opts.get("usb"):
        cmd.append("--select-usb")
    if opts.get("screen_off"):
        cmd.append("--turn-screen-off")
    if opts.get("stay_awake", True):
        cmd.append("--stay-awake")
    if not opts.get("audio", True):
        cmd.append("--no-audio")
    if opts.get("max_size"):
        cmd.append(f"--max-size={int(opts['max_size'])}")
    if opts.get("on_top"):
        cmd.append("--always-on-top")
    if opts.get("keyboard_uhid"):
        cmd.append("--keyboard=uhid")
    if opts.get("record_to"):
        cmd.append(f"--record={opts['record_to']}")
    return cmd


def app_command(scrcpy: str, target: dict, pkg: str, title: str) -> list[str]:
    """One phone app on its own virtual display, in its own window (scrcpy 3.0+)."""
    cmd = [scrcpy, f"--window-title={title}", "--new-display", f"--start-app={pkg}", "--no-audio",
           "--stay-awake", "--no-vd-destroy-content"]
    if target.get("serial"):
        cmd.append(f"--serial={target['serial']}")
    elif target.get("usb"):
        cmd.append("--select-usb")
    return cmd


def media_path(kind: str, ext: str) -> Path:
    """A dated file name in Pictures/Study Hub (screenshots) or Videos/Study Hub (recordings)."""
    from datetime import datetime
    from PyQt6.QtCore import QStandardPaths
    loc = QStandardPaths.StandardLocation.PicturesLocation if kind == "shot" else QStandardPaths.StandardLocation.MoviesLocation
    folder = Path(QStandardPaths.writableLocation(loc)) / "Study Hub"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"Phone {datetime.now():%Y-%m-%d %H-%M-%S}.{ext}"


def run_adb(adb: str, *args: str, timeout: int = 20) -> tuple[bool, str]:
    try:
        r = subprocess.run([adb, *args], capture_output=True, text=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    out = (r.stdout + r.stderr).strip()
    ok = r.returncode == 0 and not re.search(r"\b(failed|error|unable|cannot)\b", out, re.I)
    return ok, out


def devices(adb: str) -> list[dict]:
    ok, out = run_adb(adb, "devices", "-l", timeout=10)
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            model = next((p.split(":", 1)[1] for p in parts if p.startswith("model:")), "")
            found.append({"serial": parts[0], "state": parts[1], "model": model.replace("_", " ")})
    return found


def install_with_winget() -> bool:
    """Opens a console that installs scrcpy with winget (the user sees and can cancel it)."""
    winget = shutil.which("winget")
    if not winget:
        return False
    subprocess.Popen([winget, "install", "--id", WINGET_ID, "-e", "--accept-source-agreements"],
                     creationflags=CREATE_NEW_CONSOLE)
    return True


# ---------------------------------------------------------------------------------------- controller
import threading  # noqa: E402
import time  # noqa: E402

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402

DEFAULTS = {"mode": "wifi", "wifi_addr": "", "screen_off": False, "stay_awake": True, "audio": True,
            "max_size": 0, "on_top": False, "path": "", "record": False}


class Mirror(QObject):
    """Runs adb / scrcpy off the GUI thread; reports back with signals (queued)."""

    message = pyqtSignal(str, str)     # text key or raw text, kind (success / error / info)
    changed = pyqtSignal()
    app_window = pyqtSignal(int, str, str)  # window handle, package, title (an app window appeared)
    shot = pyqtSignal(str)             # a screenshot was saved here
    recorded = pyqtSignal(str)         # a screen recording was saved here

    def __init__(self, ctl):
        super().__init__(ctl)
        self.ctl = ctl
        self.proc: subprocess.Popen | None = None
        self.busy = False
        self.apps: dict[str, subprocess.Popen] = {}  # package -> its own scrcpy window
        self.recording = ""

    def options(self) -> dict:
        return {**DEFAULTS, **(self.ctl.db.get_setting("phone_mirror", {}) or {})}

    def save(self, **changes) -> None:
        self.ctl.db.set_setting("phone_mirror", {**self.options(), **changes})
        self.changed.emit()

    def scrcpy(self) -> str:
        return find_scrcpy(self.options().get("path", ""))

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _bg(self, fn) -> None:
        if self.busy:
            return
        self.busy = True
        self.changed.emit()

        def run():
            try:
                fn()
            except Exception as exc:  # report, never crash the app
                log.exception("phone mirror failed")
                self.message.emit(str(exc)[:300], "error")
            finally:
                self.busy = False
                self.changed.emit()
        threading.Thread(target=run, name="phone-mirror", daemon=True).start()

    def pair(self, addr: str, code: str) -> None:
        addr, code = addr.strip(), re.sub(r"\D", "", code)
        scrcpy = self.scrcpy()
        if not scrcpy:
            self.message.emit("mirror_no_scrcpy", "error")
            return
        if not valid_addr(addr) or len(code) != 6:
            self.message.emit("mirror_bad_pair", "error")
            return

        def work():
            ok, out = run_adb(adb_for(scrcpy), "pair", addr, code, timeout=30)
            self.message.emit("mirror_paired" if ok else (out.splitlines()[-1] if out else "mirror_failed"),
                              "success" if ok else "error")
        self._bg(work)

    def target(self, adb: str, opts: dict) -> dict | None:
        """How to reach the phone over ADB ({"serial": addr} or {"usb": True}); connects Wi-Fi first.
        Runs on a worker thread; reports problems with message()."""
        if opts["mode"] == "wifi":
            addr = opts["wifi_addr"].strip()
            if not valid_addr(addr):
                self.message.emit("mirror_need_addr", "error")
                return None
            ok, out = run_adb(adb, "connect", addr, timeout=15)
            if not ok or "connected" not in out.lower():
                self.message.emit(out.splitlines()[-1] if out else "mirror_failed", "error")
                return None
            return {"serial": addr}
        return {"usb": True}

    @staticmethod
    def adb_args(target: dict) -> list[str]:
        return ["-s", target["serial"]] if target.get("serial") else ["-d"]

    # ---------------------------------------------------------------- one phone app in its own window
    def open_app(self, pkg: str, title: str) -> None:
        proc = self.apps.get(pkg)
        if proc is not None and proc.poll() is None:
            from app.core import winapi
            for hwnd in winapi.windows_of(proc.pid):
                winapi.bring_to_front(hwnd)
            return
        scrcpy = self.scrcpy()
        if not scrcpy:
            self.message.emit("mirror_no_scrcpy", "error")
            return
        opts = self.options()

        def work():
            target = self.target(adb_for(scrcpy), opts)
            if target is None:
                return
            proc = subprocess.Popen(app_command(scrcpy, target, pkg, title), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, errors="replace",
                                    creationflags=CREATE_NO_WINDOW)
            self.apps[pkg] = proc
            from app.core import winapi
            for _ in range(80):  # its window: give it our taskbar identity and the app's icon
                if proc.poll() is not None:
                    break
                found = winapi.windows_of(proc.pid)
                if found:
                    self.app_window.emit(found[0], pkg, title)
                    break
                time.sleep(0.25)
            tail: list[str] = []
            for line in proc.stdout:
                tail = (tail + [line.strip()])[-6:]
            code = proc.wait()
            self.apps.pop(pkg, None)
            if code not in (0, 2):
                err = next((l for l in reversed(tail) if "ERROR" in l), tail[-1] if tail else "")
                self.message.emit(err.replace("ERROR:", "").strip() or "mirror_failed", "error")
        threading.Thread(target=work, name=f"phone-app-{pkg}", daemon=True).start()

    # ---------------------------------------------------------------- screenshot
    def screenshot(self) -> None:
        scrcpy = self.scrcpy()
        if not scrcpy:
            self.message.emit("mirror_no_scrcpy", "error")
            return
        opts = self.options()

        def work():
            adb = adb_for(scrcpy)
            target = self.target(adb, opts)
            if target is None:
                return
            try:
                r = subprocess.run([adb, *self.adb_args(target), "exec-out", "screencap", "-p"], capture_output=True,
                                   timeout=20, creationflags=CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.message.emit(str(exc), "error")
                return
            if not r.stdout.startswith(b"\x89PNG"):
                self.message.emit("mirror_shot_failed", "error")
                return
            path = media_path("shot", "png")
            path.write_bytes(r.stdout)
            self.shot.emit(str(path))
        threading.Thread(target=work, name="phone-shot", daemon=True).start()

    def start(self) -> None:
        if self.running:
            self.message.emit("mirror_already", "info")
            return
        scrcpy = self.scrcpy()
        if not scrcpy:
            self.message.emit("mirror_no_scrcpy", "error")
            return
        opts = self.options()

        def work():
            adb = adb_for(scrcpy)
            target = self.target(adb, opts)
            if target is None:
                return
            run_opts = {**opts, **target}
            self.recording = ""
            if opts.get("record"):
                self.recording = str(media_path("video", "mp4"))
                run_opts["record_to"] = self.recording
            self.message.emit("mirror_starting", "info")
            self.proc = subprocess.Popen(build_command(scrcpy, run_opts), stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, errors="replace",
                                         creationflags=CREATE_NO_WINDOW)
            self.busy = False  # connected: pairing / other actions are allowed again while it runs
            self.changed.emit()
            tail: list[str] = []
            for line in self.proc.stdout:  # scrcpy talks on stdout/stderr until it closes
                tail = (tail + [line.strip()])[-6:]
            code = self.proc.wait()
            self.changed.emit()
            if self.recording and Path(self.recording).is_file():
                self.recorded.emit(self.recording)
            if code not in (0, 2):  # 2 = the phone disconnected
                err = next((l for l in reversed(tail) if "ERROR" in l), tail[-1] if tail else "")
                self.message.emit(err.replace("ERROR:", "").strip() or "mirror_failed", "error")
        self._bg(work)

    def stop(self) -> None:
        if self.running:
            self.proc.terminate()
