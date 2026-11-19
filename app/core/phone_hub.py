"""
The phone as seen from the laptop (Phone > Home): its state, its notifications, and commands sent to it.

The Android app pushes its state (battery, network, DND, volume...) and notifications to the phone link server,
and keeps a request open for commands (lan_share.PhoneLinkServer.wait_commands), so a click here reaches the
phone in about a second. Notifications are kept in memory only, never written to disk (one-time codes etc.).

Wi-Fi and Bluetooth can't be switched by an app on Android 10+, so those go through ADB when the phone is
connected for screen mirroring (Phone > Phone screen).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPixmap

from app.config import DATA_DIR
from app.core.i18n import tr

log = logging.getLogger(__name__)

ICON_DIR = Path(DATA_DIR) / "phone_icons"
MAX_NOTIFS = 60
LOW_BATTERY = 15


class PhoneHub(QObject):
    changed = pyqtSignal(str)   # "state" | "notifs" | "icon"
    message = pyqtSignal(str, str)  # text, kind (from worker threads -> ctl.notify on the GUI thread)
    _handoff = pyqtSignal(str, str)  # url, title (worked out on a thread, sent from the GUI thread)

    def __init__(self, ctl):
        super().__init__(ctl)
        self.ctl = ctl
        self.states: dict[str, dict] = {}        # device -> last state
        self.notifs: dict[str, list[dict]] = {}  # device -> newest first
        self._battery_alert: dict[str, str] = {}  # device -> "low" | "full" (alerted once until it changes)
        self._pending: dict[int, str] = {}       # command id -> what it was (for error messages)
        self.message.connect(ctl.notify)
        self._handoff.connect(self._send_handoff)
        srv = ctl.phone
        srv.phone_status.connect(self._on_state)
        srv.phone_notifs.connect(self._on_notifs)
        srv.phone_icon.connect(lambda pkg: self.changed.emit("icon"))
        srv.phone_result.connect(self._on_result)
        srv.phone_apps.connect(self._on_apps)
        self._apps_asked = 0.0
        self._mirror_hooked = False

    # ---------------------------------------------------------------- reading
    def device(self) -> str:
        """The phone to show: the one heard from most recently."""
        if not self.states:
            return ""
        return max(self.states, key=lambda d: self.states[d].get("at", 0))

    def state(self, device: str = "") -> dict:
        return dict(self.states.get(device or self.device(), {}))

    def online(self, device: str = "") -> bool:
        device = device or self.device()
        return bool(device) and self.ctl.phone.phone_online(device)

    def device_name(self, device: str = "") -> str:
        device = device or self.device()
        row = self.ctl.db.device_by_hash(device) if device else None
        return (row["name"] if row else "") or self.state(device).get("model", "") or tr("nav_phone")

    def notifications(self, device: str = "") -> list[dict]:
        muted = self.muted()
        return [n for n in self.notifs.get(device or self.device(), []) if n["pkg"] not in muted]

    def muted(self) -> set[str]:
        return set(self.ctl.db.get_setting("phone_muted_apps", []) or [])

    @staticmethod
    def icon(pkg: str) -> QPixmap | None:
        path = ICON_DIR / f"{pkg}.png"
        if path.is_file():
            pix = QPixmap(str(path))
            return pix if not pix.isNull() else None
        return None

    # ---------------------------------------------------------------- from the phone
    def _on_state(self, device: str, state: dict) -> None:
        self.states[device] = state
        self._check_battery(device, state)
        self.changed.emit("state")

    def _check_battery(self, device: str, st: dict) -> None:
        level = st.get("battery")
        if level is None or not self.ctl.db.get_setting("phone_battery_alerts", True):
            return
        name = self.device_name(device)
        last = self._battery_alert.get(device, "")
        if level <= LOW_BATTERY and not st.get("charging"):
            if last != "low":
                self._battery_alert[device] = "low"
                self.ctl.notify(tr("phone_battery_low", name=name, n=level), "warning")
        elif level >= 100 and st.get("charging"):
            if last != "full":
                self._battery_alert[device] = "full"
                self.ctl.notify(tr("phone_battery_full", name=name), "success")
        elif LOW_BATTERY + 5 < level < 95:
            self._battery_alert.pop(device, None)

    def _on_notifs(self, device: str, data: dict) -> None:
        items = data["items"]
        current = self.notifs.setdefault(device, [])
        if data["op"] == "sync":
            current[:] = sorted(items, key=lambda n: -n["when"])
        elif data["op"] == "remove":
            gone = {n["key"] for n in items}
            current[:] = [n for n in current if n["key"] not in gone]
        else:
            muted = self.muted()
            for n in items:
                was_new = all(o["key"] != n["key"] for o in current)
                current[:] = [o for o in current if o["key"] != n["key"]]
                current.insert(0, n)
                if was_new and n["pkg"] not in muted and self.ctl.db.get_setting("phone_notif_popup", True):
                    text = " · ".join(t for t in (n["title"], n["text"]) if t)
                    self.ctl.notify(f"{n['app'] or self.device_name(device)}: {text[:160]}", "info")
        del current[MAX_NOTIFS:]
        self.changed.emit("notifs")

    def _on_result(self, device: str, res: dict) -> None:
        what = self._pending.pop(res["id"], "")
        if not res["ok"]:
            err = res["error"]
            msg = tr(f"phone_err_{err}") if err in ("no_access", "no_dnd_access", "no_torch", "gone", "no_field",
                                                   "no_type_access") else err
            self.ctl.notify(tr("phone_cmd_failed", what=what, error=msg), "error")

    # ---------------------------------------------------------------- to the phone
    def send(self, op: str, label: str = "", device: str = "", quiet: bool = False, **args) -> bool:
        device = device or self.device()
        if not device:
            self.ctl.notify(tr("phone_none_connected"), "warning")
            return False
        cid = self.ctl.phone.send_command(device, {"op": op, **args})
        self._pending[cid] = label or op
        if not self.online(device) and not quiet:
            self.ctl.notify(tr("phone_cmd_queued"), "info")
        return True

    def set_torch(self, on: bool) -> None:
        if self.send("torch", tr("phone_ctl_torch"), on=on):
            self._optimistic(torch=on)

    def set_dnd(self, on: bool) -> None:
        if self.send("dnd", tr("phone_ctl_dnd"), on=on):
            self._optimistic(dnd=on)

    def set_ringer(self, mode: str) -> None:  # normal | vibrate | silent
        if self.send("ringer", tr("phone_ctl_ringer"), mode=mode):
            self._optimistic(ringer=mode)

    def set_volume(self, percent: int) -> None:
        if self.send("volume", tr("phone_ctl_volume"), value=int(percent)):
            self._optimistic(volume=int(percent))

    def find(self, on: bool = True) -> bool:
        return self.send("find", tr("phone_find"), on=on)

    def reply(self, key: str, text: str) -> None:
        if text.strip():
            self.send("reply", tr("phone_reply"), key=key, text=text.strip()[:2000])

    def dismiss(self, key: str) -> None:
        self.send("dismiss", tr("phone_dismiss"), key=key)
        self._drop(key)

    def mark_read(self, key: str) -> None:
        self.send("mark_read", tr("phone_mark_read"), key=key)
        self._drop(key)

    def clear_all(self) -> None:
        for n in self.notifications():
            self.send("dismiss", tr("phone_dismiss"), key=n["key"])
        self.notifs[self.device()] = []
        self.changed.emit("notifs")

    def mute_app(self, pkg: str, mute: bool = True) -> None:
        muted = self.muted()
        (muted.add if mute else muted.discard)(pkg)
        self.ctl.db.set_setting("phone_muted_apps", sorted(muted))
        self.changed.emit("notifs")

    def _drop(self, key: str) -> None:
        lst = self.notifs.get(self.device(), [])
        lst[:] = [n for n in lst if n["key"] != key]
        self.changed.emit("notifs")

    def _optimistic(self, **changes) -> None:
        """Show the change at once; the phone's next state report confirms (or corrects) it."""
        device = self.device()
        if device in self.states:
            self.states[device].update(changes)
            self.changed.emit("state")

    # ---------------------------------------------------------------- phone apps (each in its own window)
    def apps(self) -> list[dict]:
        cache = self.ctl.db.get_setting("phone_apps_cache", {}) or {}
        return list(cache.get(self.device(), []))

    def request_apps(self, force: bool = False) -> None:
        """Asks the phone for its app list (at most every 10 minutes unless forced)."""
        if not self.device() or (not force and time.time() - self._apps_asked < 600):
            return
        self._apps_asked = time.time()
        self.send("apps", tr("phone_apps"))

    def _on_apps(self, device: str, apps: list) -> None:
        cache = dict(self.ctl.db.get_setting("phone_apps_cache", {}) or {})
        cache[device] = sorted(apps, key=lambda a: a["label"].lower())
        self.ctl.db.set_setting("phone_apps_cache", cache)
        self.changed.emit("apps")

    def open_app(self, pkg: str, label: str) -> None:
        mirror = self._mirror()
        mirror.open_app(pkg, f"{label} — {self.device_name()}")
        self.ctl.notify(tr("phone_app_opening", app=label), "info")

    def _mirror(self):
        mirror = self.ctl.phone_mirror()
        if not self._mirror_hooked:
            self._mirror_hooked = True
            mirror.app_window.connect(self._style_app_window)
            mirror.shot.connect(self._shot_saved)
            mirror.recorded.connect(lambda path: self.ctl.notify(tr("mirror_recorded", path=path), "success"))
        return mirror

    def _style_app_window(self, hwnd: int, pkg: str, title: str) -> None:
        """The phone app's window gets its own taskbar button, with the app's icon."""
        from app.core import winapi
        winapi.set_window_app_id(hwnd, f"StudyHub.PhoneApp.{pkg}"[:120], "", title)
        pix = self.icon(pkg)
        if pix is not None:
            winapi.set_window_icon(hwnd, pix.toImage())

    def screenshot(self) -> None:
        self._mirror().screenshot()

    def _shot_saved(self, path: str) -> None:
        from PyQt6.QtGui import QImage
        from PyQt6.QtWidgets import QApplication
        img = QImage(path)
        if not img.isNull():
            QApplication.clipboard().setImage(img)
        self.ctl.notify(tr("mirror_shot_saved", path=path), "success")

    # ---------------------------------------------------------------- camera: photo / scan to the laptop
    def request_camera(self, mode: str) -> None:
        """mode: photo (goes on the clipboard) | scan (opens the scan window). The phone app opens its camera
        at once if it's on screen, otherwise it shows a notification to tap; the phone page gets an event."""
        if self.send("camera", tr("phone_camera"), mode=mode):
            self.ctl.phone.push_event("camera", mode)
            self.ctl.notify(tr("phone_camera_asked"), "info")

    def camera_received(self, path: str, mode: str) -> None:
        if mode == "scan":
            from app.ui.dialogs.scan_dialog import ScanDialog
            dlg = ScanDialog(self.ctl, path)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return
        from PyQt6.QtGui import QImage
        from PyQt6.QtWidgets import QApplication
        img = QImage(path)
        if img.isNull():
            self.ctl.notify(tr("phone_camera_bad"), "error")
            return
        QApplication.clipboard().setImage(img)
        self.ctl.notify(tr("phone_photo_on_clipboard"), "success")

    # ---------------------------------------------------------------- handoff: a link opens on the phone
    def open_on_phone(self, url: str, title: str = "") -> None:
        """Opens the link on the phone (right away if the app is on screen, otherwise from a notification).
        A YouTube link continues where the video playing in the laptop's browser is."""
        from app.core.launcher import normalize_url
        url = normalize_url(url.strip()) or ""
        if not url.startswith(("http://", "https://")):
            self.ctl.notify(tr("handoff_no_link"), "warning")
            return

        def work():
            from app.core import media_session
            final = url
            if media_session.is_youtube(url):
                pos = media_session.youtube_position()
                if pos:
                    final = media_session.with_time(url, pos)
            self._handoff.emit(final, title)
        threading.Thread(target=work, name="phone-handoff", daemon=True).start()

    def _send_handoff(self, url: str, title: str) -> None:
        if self.send("open_url", tr("handoff_title"), url=url, title=title[:200]):
            from app.core.media_session import is_youtube
            at = "t=" in url and is_youtube(url)
            self.ctl.notify(tr("handoff_sent_at") if at else tr("handoff_sent"), "success")
            self.ctl.db.add_transfer("out", "link", title or url, url, 0, None, None)
            self.ctl.data_changed.emit("phone")

    # ---------------------------------------------------------------- typing on the phone
    def type_text(self, text: str) -> None:
        if text:
            self.send("type", tr("phone_type"), quiet=True, text=text[:4000])

    def type_key(self, key: str) -> None:  # backspace | enter
        self.send("key", tr("phone_type"), quiet=True, key=key)

    # ---------------------------------------------------------------- Wi-Fi / Bluetooth through ADB
    def adb_ready(self) -> bool:
        mirror = self.ctl.phone_mirror()
        return bool(mirror.scrcpy())

    def set_radio(self, which: str, on: bool) -> None:
        """which: wifi | bt. Needs the phone connected over ADB (Phone > Phone screen)."""
        mirror = self.ctl.phone_mirror()
        scrcpy = mirror.scrcpy()
        if not scrcpy:
            self.ctl.notify(tr("phone_radio_needs_adb"), "warning")
            return
        from app.core.phone_mirror import adb_for, run_adb, valid_addr
        adb = adb_for(scrcpy)
        opts = mirror.options()
        self._optimistic(**{which: on})

        def work():
            target: list[str] = []
            if opts.get("mode") == "wifi":
                addr = opts.get("wifi_addr", "").strip()
                if not valid_addr(addr):
                    self.message.emit(tr("phone_radio_needs_adb"), "warning")
                    return
                run_adb(adb, "connect", addr, timeout=10)
                target = ["-s", addr]
            else:
                target = ["-d"]
            if which == "wifi":
                cmd = ["shell", "svc", "wifi", "enable" if on else "disable"]
            else:
                cmd = ["shell", "cmd", "bluetooth_manager", "enable" if on else "disable"]
            ok, out = run_adb(adb, *target, *cmd, timeout=15)
            if not ok and which == "bt":
                ok, out = run_adb(adb, *target, "shell", "svc", "bluetooth", "enable" if on else "disable", timeout=15)
            if not ok:
                log.info("adb %s: %s", which, out)
                self.message.emit(tr("phone_radio_failed"), "error")
        threading.Thread(target=work, name="phone-radio", daemon=True).start()
