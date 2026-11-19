"""
Application controller: owns the database, windows, tray, hotkeys and IPC, and
exposes every user action (save note, pin link, launch app...) in one place.
The UI only calls these methods and listens to `data_changed`.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication, QDialog

from app.config import (APP_NAME, APP_USER_MODEL_ID, BACKUP_DIR, DATA_DIR, DEFAULT_HOTKEYS, DEFAULT_LANGUAGE,
                        ICON_PATH, IS_FROZEN)
from app.core.alarms import AlarmScheduler, next_occurrence, until_text
from app.core.vault import Vault
from app.core.favicon import FaviconFetcher
from app.core.sounds import SoundPlayer
from app.core import i18n, shell_integration, winapi
from app.core.capture import SelectionCapture
from app.core.logs import log
from app.core.safety import daily_backup, open_database
from app.core.hotkeys import GlobalHotkeys
from app.core.i18n import tr
from app.core import files as fs
from app.core import organizer
from app.core.ipc_server import CommandServer
from app.core.pomodoro import DEFAULT_DURATIONS, PomodoroTimer
from app.core.launcher import detect_kind, normalize_url, open_target, reveal_in_explorer, title_from_target
from app.core.lan_share import PhoneLinkServer, new_code
from app.ui import theme
from app.ui.theme import Icons, Palette, Theme, app_icon, app_icon_pixmap, stylesheet


def label_widget(text: str):
    from app.ui.widgets.common import label
    return label(text, 9, role="Dim", wrap=True)


def _deleted(widget) -> bool:
    """True once Qt destroyed the window (WA_DeleteOnClose)."""
    try:
        widget.isVisible()
        return False
    except RuntimeError:
        return True


# commands from Explorer that may arrive once per selected file and are merged
BATCHED_ACTIONS = {"add_path", "track_path", "send_to", "copy_to", "move_to", "link_to", "note_for", "send_phone"}


class StudyHubApp(QObject):
    _auto_clean_done = pyqtSignal(object)  # weekly clean thread -> GUI thread (bytes can exceed 32 bits)
    data_changed = pyqtSignal(str)  # "launcher" | "notes" | "tasks" | "schedule" | "files" | "tags" | ... | ""
    downloads_changed = pyqtSignal(int)  # number of files waiting in Downloads

    def __init__(self, initial_command: dict | None = None, start_minimized: bool = False):
        try:  # Qt WebEngine must be loaded before the QApplication exists (desktop web windows)
            import PyQt6.QtWebEngineWidgets  # noqa: F401
        except ImportError:
            pass
        self.qt = QApplication.instance() or QApplication(sys.argv)
        super().__init__()
        self.quitting = False
        winapi.set_app_user_model_id(APP_USER_MODEL_ID)
        self.qt.setApplicationName(APP_NAME)
        self.qt.setQuitOnLastWindowClosed(False)  # live in the tray
        for effect in (Qt.UIEffect.UI_AnimateMenu, Qt.UIEffect.UI_FadeMenu, Qt.UIEffect.UI_AnimateCombo,
                       Qt.UIEffect.UI_AnimateTooltip, Qt.UIEffect.UI_FadeTooltip):
            QApplication.setEffectEnabled(effect, True)

        self.db, recovery = open_database()  # restores a backup by itself if the file is damaged
        daily_backup(self.db)
        i18n.set_clock_format(self.db.get_setting("clock_12h", True))
        log.info("database ready (recovery: %s)", recovery)
        QTimer.singleShot(5000, lambda: self._safe(self.purge_old_trash))
        QTimer.singleShot(90_000, lambda: self._safe(self.maybe_auto_clean))
        self._apply_language(self.db.get_setting("language", DEFAULT_LANGUAGE))
        Theme.set_accent(self.db.get_setting("accent", "violet"))
        Palette.apply(self.db.get_setting("theme_mode", "dark"))  # dark / light / system
        self.qt.setStyleSheet(stylesheet())
        self.icon = app_icon()
        self.qt.setWindowIcon(self.icon)
        self._write_icon_file()

        self.server = CommandServer(self)
        self.server.command_received.connect(self.handle_command)
        log.info("command server listening: %s", self.server.listening)
        self.hotkeys = GlobalHotkeys(self)
        self.hotkeys.triggered.connect(self._on_hotkey)
        failed = self.hotkeys.register_all(self.hotkey_map())
        self.capture = SelectionCapture(self)
        from app.core.clipboard_history import ClipboardHistory
        self.clipboard_history = ClipboardHistory(self)
        self._clip_popup = None
        self._screenshots = None
        self._pdf_readers = None
        self.capture.captured.connect(self._on_captured)

        conf = self.pomodoro_settings()
        self.pomodoro = PomodoroTimer({k: conf[k] for k in ("focus", "short_break", "long_break")},
                                      conf["long_every"], conf["auto_start"], self)
        self.pomodoro.session_finished.connect(self._on_pomodoro_finished)
        self.pomodoro_subject_id: int | None = None
        self.pomodoro_task_id: int | None = None

        # password vault (locked at start; locks itself after inactivity)
        self.vault = Vault(self.db)
        self._vault_activity = time.monotonic()
        self._clip_secret: str | None = None
        self._vault_timer = QTimer(self, interval=10_000, timeout=self._vault_autolock)
        self._vault_timer.start()

        # alarms, sounds, lock screen
        self.sound = SoundPlayer(self)
        self.alarms = AlarmScheduler(self.db, self)
        self.alarms.ring.connect(self.ring_alarm)
        self.alarms.reminder.connect(self._class_reminder)
        self._ring_windows: list = []
        self._lock_windows: list = []
        self._idle_timer = QTimer(self, interval=20_000, timeout=self._check_idle)
        self._idle_timer.start()

        self.favicons = FaviconFetcher(DATA_DIR / "icons", self)
        self.favicons.fetched.connect(self._on_favicon)
        self._logos_timer = QTimer(self, singleShot=True, interval=700, timeout=self._emit_logos)

        # Explorer sends one command per selected file: collect them for a moment, act once
        self._batch: dict[tuple, list[str]] = {}
        self._batch_timer = QTimer(self, singleShot=True, interval=450, timeout=self._flush_batch)
        self._shell_timer = QTimer(self, singleShot=True, interval=1500, timeout=self._refresh_shell_menu)
        self.data_changed.connect(lambda what: what in ("subjects", "folders") and self._shell_timer.start())

        # Downloads watcher: only notifies, never moves anything by itself
        self._seen_downloads: set[str] = set()
        self._downloads_watcher = QFileSystemWatcher(self)
        self._downloads_watcher.directoryChanged.connect(lambda _: self._downloads_debounce.start())
        self._downloads_debounce = QTimer(self, singleShot=True, interval=2500, timeout=self._check_downloads)
        self.set_watch_downloads(self.db.get_setting("watch_downloads", True), save=False)

        # phone link: local-network web page for the phone (off unless the user turned it on)
        self.phone = PhoneLinkServer(self)
        self.phone.code = self.db.get_setting("phone_link_code") or new_code()
        self.db.set_setting("phone_link_code", self.phone.code)
        self.phone.received.connect(self._on_phone_item)
        self._auto_clean_done.connect(self._on_auto_clean_done)
        self.phone.paired.connect(self._on_phone_paired)
        self.phone.seen.connect(self._on_phone_seen)
        self.phone.fetched.connect(self._on_phone_fetched)
        self.context: dict = {}        # what you're reading / editing on the laptop (continue on the phone)
        self.phone_context: dict = {}  # … and on the phone (continue on the laptop)
        from app.core.phone_bridge import PhoneBridge
        self.phone_bridge = PhoneBridge(self)
        from app.core.phone_hub import PhoneHub
        self.phone_hub = PhoneHub(self)  # the phone's state, notifications and controls (Phone > Home)
        from app.core.deck import Deck
        self.deck = Deck(self)  # the phone's Deck of buttons (Phone > Deck)
        try:
            from app.core.power import mac_addresses
            self.phone.macs = mac_addresses()
        except Exception:
            pass
        from app.core.phone_sync import PhoneSync
        self.phone_sync = PhoneSync(self)  # photo backup + synced folders with the phone
        self.phone_sync._push_config()
        self.phone.request.connect(self.phone_bridge.handle)  # phone app: desktop, files, screen, notes...
        from app.core.phone_music import clear_folder
        clear_folder()  # music played from the phone last time
        from app.core.update_service import UpdateService
        self.updates = UpdateService(self)
        self.updates.checked.connect(self._on_update_checked)
        self.updates.failed.connect(lambda err: (self.notify(tr("update_failed", error=err), "error"),
                                                 self.data_changed.emit("updates")))
        self.updates.installing.connect(lambda: self.notify(tr("update_installing"), "info"))
        QTimer.singleShot(60_000, lambda: self._safe(self.updates.auto_check))
        self._mirror = None
        self.data_changed.connect(lambda what: what in ("subjects", "folders") and self.phone.running
                                  and self._sync_phone_state())

        # plugins (their pages, commands and translations must exist before the window is built)
        from app.core.plugins import PluginManager
        self.plugins = PluginManager(self)
        self._safe(self.plugins.discover)
        self._safe(self.plugins.load_enabled)

        # UI modules are imported here so the theme/language are set first
        from app.ui.dialogs.quick_note import QuickNoteWindow
        from app.ui.main_window import MainWindow
        from app.ui.tray import TrayIcon
        self._QuickNoteWindow = QuickNoteWindow
        self.quick_note = QuickNoteWindow(self)
        from app.ui.app_window import AppManager
        self.apps = AppManager(self)
        from app.core.system_status import SystemStatus
        self.system = SystemStatus(self)  # Wi-Fi, VPN, sound, brightness, battery (taskbar status icons)
        from app.ui.snippets import SnippetStore
        self.snippets = SnippetStore(self)  # code snippets (app/ui/snippets.py)
        self._snippet_popup = None
        from app.ui.action_center import QuickActions
        self.quick = QuickActions(self)  # quick-action tiles + notification history (taskbar clock)
        from app.core.recents import Recents
        self.recents = Recents(self)  # recent / pinned items + tasks per app (taskbar + Windows jump lists)
        from app.core.pdf_index import PdfIndex
        self.pdf_index = PdfIndex(self)  # full-text search across all PDFs (PDF Library)
        self._pdf_library = None
        QTimer.singleShot(20000, lambda: self._safe(self.pdf_index.refresh))
        QTimer.singleShot(8000, lambda: self._safe(self.recents.refresh_all_jumplists))  # every feature is an app in its own window (Start menu, taskbar)
        self.window = MainWindow(self)
        self.tray = TrayIcon(self)
        from app.ui.lab_window import LabService
        self.lab = LabService(self)  # lab report generator (records while a lab is running)
        QTimer.singleShot(2500, lambda: self._safe(self.lab.resume))
        self.lab.changed.connect(self._sync_lab_to_phone)
        QTimer.singleShot(3000, self._sync_lab_to_phone)
        log.info("window and tray ready")
        if self.db.get_setting("phone_link_enabled", False):
            self.set_phone_link(True, quiet=True)

        if os.environ.get("STUDYHUB_NO_SHELL") or shell_integration.owned_by_installed_copy():
            log.info("Explorer menu / autostart left as they are (tests, or the installed exe owns them)")
        else:  # keep Explorer entries and autostart pointing at this copy
            if not self.db.get_setting("explorer_menu_v2", False):
                # v2 of the Explorer menu is on by default (subjects, Send to, tags…)
                if self._safe(self._register_shell):
                    self.db.set_setting("explorer_menu_v2", True)
                    QTimer.singleShot(3500, lambda: self.notify(tr("explorer_menu_on"), "success"))
            elif shell_integration.is_context_menu_registered():
                self._safe(self._register_shell)  # keep paths and subject names fresh
            if self.db.get_setting("pdf_open_with", True):
                self._safe(lambda: shell_integration.register_pdf_handler(True))
            if shell_integration.is_autostart_enabled():
                # re-point autostart at whatever started us now (e.g. switched from run.bat to the exe)
                self._safe(lambda: shell_integration.set_autostart(True, self.autostart_shows_window()))
        QTimer.singleShot(3000, self._backfill_logos)
        if not start_minimized:
            self.window.show_os(splash=True)
        if failed:
            QTimer.singleShot(2200, lambda: self.notify(
                tr("hotkeys_failed", names=", ".join(tr(f"hk_{a}") for a in failed)), "warning"))
        if initial_command:
            QTimer.singleShot(500, lambda: self.handle_command(initial_command))
        if not self.server.listening:  # e.g. an administrator copy owns the channel
            log.error("Command server could not listen: %s", self.server._server.errorString())
            QTimer.singleShot(3000, lambda: self.notify(tr("server_blocked"), "warning"))
        if recovery:  # tell the user what happened instead of failing silently
            text = (tr("db_salvaged", folder=recovery["broken"]) if recovery.get("salvaged")
                    else tr("db_restored", name=recovery["restored"]) if recovery["restored"]
                    else tr("db_reset"))
            QTimer.singleShot(2500, lambda: self.notify(text, "warning"))

    def run(self) -> int:
        code = self.qt.exec()
        self.hotkeys.unregister_all()
        self.db.close()
        return code

    # ================================================================ plumbing
    def _apply_language(self, lang: str) -> None:
        i18n.set_language(lang)
        theme.set_rtl_fonts(i18n.is_rtl())
        self.qt.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl() else Qt.LayoutDirection.LeftToRight)
        self.qt.setFont(theme.ui_font(10))

    def _write_icon_file(self) -> None:
        """An .ico on disk is needed for the Explorer context-menu icon."""
        try:
            app_icon_pixmap(64).save(str(ICON_PATH), "ICO")
        except Exception:
            pass

    def _safe(self, fn) -> bool:
        try:
            fn()
            return True
        except OSError as exc:
            self.notify(f"{tr('error')}: {exc}", "error")
            return False

    def notify(self, text: str, kind: str = "info") -> None:
        """Toast in the app window you're using (or the shell), Windows notification otherwise."""
        if getattr(self, "quick", None) is not None:
            self.quick.record(text, kind)  # the clock's notification list
        apps = getattr(self, "apps", None)
        active = QApplication.activeWindow()
        if apps is not None and active is not None and active in apps.windows.values() and not active.isMinimized():
            active.toast(text, kind)
        elif self.window.isVisible() and not self.window.isMinimized():
            self.window.toast(text, kind)
        else:
            self.tray.showMessage(APP_NAME, text, self.icon, 3000)

    def handle_command(self, command: dict) -> None:
        """Commands coming from a second launch (Explorer menu, shortcut, autostart)."""
        action = command.get("action")
        if action == "show":
            self.show_window()
        elif action == "quick_note":
            self.open_quick_note()
        elif action == "clipboard_note":
            self.clipboard_note()
        elif action == "clipboard_link":
            self.clipboard_link()
        elif action == "paste":
            self.paste_hotkey()
        elif action == "open_pdf":
            self.open_pdf(command.get("path") or None)
        elif action == "open_app":  # StudyHub.exe --app notes (its own Start-menu shortcut)
            self.apps.open(command.get("key", ""))
        elif action == "open_note":  # Windows jump list: a recent / pinned note
            self.open_notebook(int(command.get("id", 0)) or None)
        elif action == "run_command":  # Windows jump list: an app's task (--do new_note)
            if not self.run_command(command.get("key", "")):
                self.notify(tr("error"), "error")
        elif action == "open_path":
            path = command.get("path", "")
            if os.path.isdir(path):
                self.open_folder(path)
            elif path:
                self.open_path(path)
        elif action == "quit":  # installer/updater asks us to close
            self.quit()
        elif action in BATCHED_ACTIONS:
            paths = command.get("paths") or ([command["path"]] if command.get("path") else [])
            key = (action, command.get("subject"))
            self._batch.setdefault(key, []).extend(p for p in paths if p)
            self._batch_timer.start()  # wait a moment for the other selected files

    def _flush_batch(self) -> None:
        batch, self._batch = self._batch, {}
        for (action, subject), paths in batch.items():
            paths = list(dict.fromkeys(os.path.normpath(p) for p in paths if os.path.exists(p)))
            if not paths:
                continue
            if action == "add_path":  # Explorer › "Pin to Study Hub": choose where, in the gallery
                self.show_window()
                QTimer.singleShot(250, lambda p=paths: self.show_add_gallery(paths=p))
            elif action == "track_path":
                self.show_window()
                QTimer.singleShot(150, lambda p=paths: self.tag_paths(p))
            elif action == "send_to":
                self.send_to_dialog(paths)
            elif action == "copy_to":
                folder = (self.subject_folders(int(subject)) or [None])[0]
                if folder:
                    self.copy_into(paths, folder)
            elif action == "move_to":
                folder = (self.subject_folders(int(subject)) or [None])[0]
                if folder:
                    done = sum(1 for p in paths if os.path.isfile(p) and self._move_one(p, int(subject), folder))
                    self.data_changed.emit("organizer")
                    self.notify(tr("moved_n", n=done), "success")
            elif action == "link_to":
                for p in paths:
                    if os.path.isdir(p):
                        self.link_folder(int(subject), path=p)
            elif action == "note_for":
                names = "\n".join(f"- [{Path(p).name}]({Path(p).as_uri()})" for p in paths)
                self.open_quick_note(names + "\n\n", source="file", source_ref=paths[0])

    def send_to_dialog(self, paths: list[str]) -> None:
        """'Send to > Study Hub' from Explorer: choose what to do with the files."""
        from app.ui.dialogs.send_to import SendToDialog
        dlg = SendToDialog(paths, self.db.list_subjects(), self)
        dlg.exec()

    def _register_shell(self) -> None:
        subjects = [(s["id"], s["name"], bool(self.subject_folders(s["id"]))) for s in self.db.list_subjects()]
        icon = f"{sys.executable},0" if IS_FROZEN else str(ICON_PATH)  # the exe carries the icon itself
        shell_integration.register_context_menu(icon, subjects)

    def _refresh_shell_menu(self) -> None:
        if (not os.environ.get("STUDYHUB_NO_SHELL") and not shell_integration.owned_by_installed_copy()
                and shell_integration.is_context_menu_registered()):
            self._safe(self._register_shell)

    def _on_hotkey(self, action: str) -> None:
        if action == "quick_note":
            self.open_quick_note()
        elif action == "capture_selection":
            self.capture_selection()
        elif action == "clipboard_link":
            self.clipboard_link()
        elif action == "toggle_window":
            self.toggle_window()
        elif action == "command_palette":
            self.open_palette()
        elif action == "paste_to_desktop":
            self.paste_hotkey()
        elif action == "lock_screen":
            self.show_lock_screen()
        elif action == "explorer":
            self.open_explorer()
        elif action == "clipboard_history":
            self.open_clipboard_history()
        elif action == "screenshot":
            self.take_screenshot()
        elif action == "snippets":
            self.snippet_popup().open()

    # ================================================================= window
    def show_window(self, page: str | None = None) -> None:
        """The shell (desktop + taskbar); with a page: just that app, in its own window."""
        if page and page != "desktop":
            self.apps.open(page)
            return
        self.window.show_os()

    def toggle_window(self) -> None:
        if self.window.isVisible() and self.window.isActiveWindow() and not self.window.isMinimized():
            self.window.hide()
        else:
            self.show_window()

    def open_palette(self) -> None:
        self.show_window()
        QTimer.singleShot(80, self.window.open_palette)

    # ============================================================== apps / taskbar
    def open_settings(self, section: str | None = None) -> None:
        page = self.apps.open("settings")
        if page is not None and section:
            QTimer.singleShot(0, lambda: page.show_section(section))

    def taskbar_conf(self, global_only: bool = False) -> dict:
        from app.ui.widgets.taskbar import taskbar_settings
        return taskbar_settings(self, global_only)

    def set_taskbar(self, **changes) -> None:
        conf = self.taskbar_conf(global_only=True)
        conf.update(changes)
        conf.pop("desk_pinned", None)
        self.db.set_setting("taskbar", conf)
        self.window.apply_taskbar_settings()
        self.data_changed.emit("taskbar")

    def set_start_item(self, key: str, on: bool) -> None:
        """A program / website / file / folder of the desktop in Study Hub's Start menu (pinned grid)."""
        items = list(self.db.get_setting("start_items", []) or [])
        if on and key not in items:
            items.append(key)
        elif not on and key in items:
            items.remove(key)
        self.db.set_setting("start_items", items)
        self.notify(tr("start_pinned_item") if on else tr("start_unpinned_item"), "success")

    def set_taskbar_pinned(self, key: str, on: bool) -> None:
        desk = self.current_desk()
        own = self.desk_look(desk).get("pinned")  # this desktop has its own apps: change those
        pinned = list(own if own is not None else self.taskbar_conf(global_only=True)["pinned"])
        if on and key not in pinned:
            pinned.append(key)
        elif not on and key in pinned:
            pinned.remove(key)
        if own is not None:
            self.set_desk_look(desk, pinned=pinned)
        else:
            self.set_taskbar(pinned=pinned)

    # ----------------------------------------------------- each desktop's own look
    def current_desk(self) -> str:
        desktop = getattr(getattr(self, "window", None), "desktop", None)
        return desktop.canvas.desk if desktop is not None else "all"

    def desk_look(self, desk: str) -> dict:
        """{"wallpaper": dict | None, "pinned": [app keys] | None} — None means "the general one"."""
        return dict((self.db.get_setting("desk_looks", {}) or {}).get(str(desk), {}))

    _KEEP = object()

    def set_desk_look(self, desk: str, wallpaper=_KEEP, pinned=_KEEP) -> None:
        looks = dict(self.db.get_setting("desk_looks", {}) or {})
        look = dict(looks.get(str(desk), {}))
        if wallpaper is not self._KEEP:
            look["wallpaper"] = wallpaper
        if pinned is not self._KEEP:
            look["pinned"] = pinned
        looks[str(desk)] = look
        self.db.set_setting("desk_looks", looks)
        if str(desk) == self.current_desk():
            self.window.desk_changed(str(desk))

    def desk_wallpaper(self) -> dict:
        own = self.desk_look(self.current_desk()).get("wallpaper")
        return {**self.wallpaper(), **own} if own else self.wallpaper()

    def edit_desk_look(self, desk: str | None = None, parent=None) -> None:
        from app.ui.dialogs.desk_look import DeskLookDialog
        desk = str(desk if desk is not None else self.current_desk())
        names = dict(self.window.desktop.desk_keys())
        DeskLookDialog(self, desk, names.get(desk, desk), parent or self._dialog_parent()).exec()

    def quit(self) -> None:
        self.quitting = True
        quick = getattr(self, "quick", None)
        if quick is not None:  # the app-wide "add this button" filter must not outlive the app
            QApplication.instance().removeEventFilter(quick._catcher)
        term = getattr(getattr(self, "apps", None), "terminal_window", None)
        if term is not None:
            term.close()  # end the shells it started
        self.phone.stop()
        self._safe(self.plugins.shutdown)
        self.tray.hide()
        self.qt.quit()

    # ============================================================== settings
    def set_language(self, lang: str) -> None:
        if lang == i18n.language():
            return
        self.db.set_setting("language", lang)
        self._apply_language(lang)
        self.phone.lang = lang  # language of the phone page
        self.window.build()
        self.apps.rebuild_all()
        self.tray.rebuild_menu()
        self.quick_note.deleteLater()
        self.quick_note = self._QuickNoteWindow(self)
        self._refresh_shell_menu()  # Explorer labels follow the app language
        self.notify(tr("language_changed"), "success")

    def set_accent(self, name: str) -> None:
        Theme.set_accent(name)
        self.db.set_setting("accent", name)
        self.qt.setStyleSheet(stylesheet())
        self.icon = app_icon()
        self.qt.setWindowIcon(self.icon)
        self.tray.setIcon(self.icon)
        self._write_icon_file()
        self.window.bg.refresh_colors()
        self.window.update()
        for win in self.apps.windows.values():
            win.bg.refresh_colors()
            win.update()

    def set_theme_mode(self, mode: str) -> None:
        """dark / light / system — rebuilds the window (widgets read the palette when they are built)."""
        self.db.set_setting("theme_mode", mode)
        Palette.apply(mode)
        self.qt.setStyleSheet(stylesheet())
        self.window.build()
        self.apps.rebuild_all()
        self.tray.rebuild_menu()
        self.quick_note.deleteLater()
        self.quick_note = self._QuickNoteWindow(self)
        self.notify(tr("theme_changed"), "success")

    def wallpaper(self) -> dict:
        from app.ui.widgets.background import DEFAULT_WALLPAPER
        return {**DEFAULT_WALLPAPER, **(self.db.get_setting("wallpaper", {}) or {})}

    def set_wallpaper(self, **changes) -> None:
        wp = {**self.wallpaper(), **changes}
        self.db.set_setting("wallpaper", wp)
        self.window.bg.set_wallpaper(self.desk_wallpaper())  # a desktop with its own picture keeps it
        for win in list(self.apps.windows.values()):  # the app windows show the same wallpaper
            win.bg.set_wallpaper(wp)
        self.data_changed.emit("wallpaper")

    def choose_wallpaper_image(self, parent=None) -> None:
        """Copies the picture into Study Hub's data folder, so moving / deleting the original is fine."""
        import shutil
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(parent or self._dialog_parent(), tr("wallpaper_pick"), str(Path.home()),
                                              "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return
        folder = DATA_DIR / "wallpapers"
        folder.mkdir(parents=True, exist_ok=True)
        old = self.wallpaper().get("path", "")
        target = folder / f"wallpaper-{datetime.now():%Y%m%d-%H%M%S}{Path(path).suffix.lower()}"
        shutil.copy2(path, target)
        self.set_wallpaper(kind="image", path=str(target))
        if old and Path(old).parent == folder and Path(old) != target:
            Path(old).unlink(missing_ok=True)

    def import_wallpaper_image(self, parent=None) -> str:
        """Settings' preview: copies a picked picture into the data folder and returns its path (not applied yet)."""
        import shutil
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(parent or self._dialog_parent(), tr("wallpaper_pick"), str(Path.home()),
                                              "Images (*.png *.jpg *.jpeg *.webp *.bmp)")
        if not path:
            return ""
        folder = DATA_DIR / "wallpapers"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"wallpaper-{datetime.now():%Y%m%d-%H%M%S}{Path(path).suffix.lower()}"
        shutil.copy2(path, target)
        return str(target)

    def set_animated_background(self, on: bool) -> None:
        self.db.set_setting("animated_background", on)
        self.window.bg.set_animated(on)

    def hotkey_map(self) -> dict[str, str]:
        mapping = dict(DEFAULT_HOTKEYS)
        mapping.update(self.db.get_setting("hotkeys", {}) or {})
        return mapping

    def hotkey_text(self, action: str) -> str:
        """Human readable, localized (e.g. 'Ctrl+Alt+N')."""
        return QKeySequence(self.hotkey_map().get(action, "")).toString(QKeySequence.SequenceFormat.NativeText)

    def apply_hotkeys(self, mapping: dict[str, str]) -> list[str]:
        self.db.set_setting("hotkeys", mapping)
        failed = self.hotkeys.register_all(self.hotkey_map())
        self.tray.rebuild_menu()
        return failed

    def set_explorer_menu(self, on: bool) -> bool:
        fn = self._register_shell if on else shell_integration.unregister_context_menu
        ok = self._safe(fn)
        if ok:
            self.notify(tr("explorer_menu_on") if on else tr("explorer_menu_off"), "success")
        return ok

    def set_plugin_enabled(self, plugin_id: str, on: bool) -> bool:
        """Turns a plugin on/off live: its pages, commands and tray actions appear or disappear."""
        ok = self.plugins.set_enabled(plugin_id, on)
        info = self.plugins.plugins.get(plugin_id)
        if not on:
            self.apps.close(plugin_id)
        self.window.build()
        self.apps.rebuild_all()
        self.tray.rebuild_menu()
        if ok:
            self.notify(tr("plugin_on" if on else "plugin_off", name=info.text("name") if info else plugin_id),
                        "success")
        else:
            self.notify(tr("plugin_failed", name=info.text("name") if info else plugin_id), "error")
        return ok

    # ================================================================ tools
    def open_clipboard_history(self) -> None:
        from app.ui.clipboard_popup import ClipboardPopup
        if self._clip_popup is None:
            self._clip_popup = ClipboardPopup(self)
        self._clip_popup.open()

    def take_screenshot(self) -> None:
        from app.ui.screenshot import ScreenshotTool
        if self._screenshots is None:
            self._screenshots = ScreenshotTool(self)
        if self.window.isVisible() and self.window.isActiveWindow():
            self.window.showMinimized()  # don't photograph Study Hub itself
            QTimer.singleShot(350, self._screenshots.start)
        else:
            self._screenshots.start()

    def open_pdf(self, path: str | None = None, page: int | None = None):
        """The PDF reader (its own window). path=None shows the start screen with recent files."""
        from app.ui.pdf_reader import PdfReaders
        if self._pdf_readers is None:
            self._pdf_readers = PdfReaders(self)
        return self._pdf_readers.open(path, page)

    def open_pdf_library(self, tab: str = "search", query: str = ""):
        """PDF Library: search in every PDF, all highlights, all bookmarks (one window)."""
        from app.ui.pdf_library import PdfLibrary
        lib = self._pdf_library
        try:
            alive = lib is not None and lib.isVisible()
        except RuntimeError:
            alive = False
        if not alive:
            lib = self._pdf_library = PdfLibrary(self, tab, query)
            lib.destroyed.connect(lambda *_: setattr(self, "_pdf_library", None))
        else:
            lib.show_tab(tab)
            if query:
                lib.query.setText(query)
        lib.show()
        lib.showNormal() if lib.isMinimized() else None
        lib.raise_()
        lib.activateWindow()
        return lib

    def set_clipboard_history(self, on: bool) -> None:
        self.db.set_setting("clipboard_history", on)

    def set_pdf_open_with(self, on: bool) -> bool:
        self.db.set_setting("pdf_open_with", on)
        return self._safe(lambda: shell_integration.register_pdf_handler(on))

    def set_autostart(self, on: bool) -> bool:
        return self._safe(lambda: shell_integration.set_autostart(on, self.autostart_shows_window()))

    def autostart_shows_window(self) -> bool:
        return bool(self.db.get_setting("autostart_show_window", True))

    def set_autostart_show_window(self, show: bool) -> None:
        """Start with Windows: open the window (True) or stay in the tray (False)."""
        self.db.set_setting("autostart_show_window", show)
        if shell_integration.is_autostart_enabled():
            self._safe(lambda: shell_integration.set_autostart(True, show))

    # ============================================================ phone link
    def phone_folder(self) -> str:
        from PyQt6.QtCore import QStandardPaths
        default = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)) / "Study Hub"
        return self.db.get_setting("phone_link_folder") or str(default)

    def _sync_phone_state(self) -> None:
        """Hands the server thread fresh snapshots (it never reads the database itself)."""
        phone = self.phone
        phone.folder = self.phone_folder()
        phone.max_mb = int(self.db.get_setting("phone_link_max_mb", 50))
        phone.allow_pairing = bool(self.db.get_setting("phone_link_allow_pairing", True))
        phone.lang = i18n.language()
        phone.set_devices(d["token_hash"] for d in self.db.list_phone_devices())
        from app.core.phone_bridge import caps, shared_folders
        phone.set_access(caps(self.db), shared_folders(self.db))
        subjects = []
        for s in self.db.list_subjects():
            folders = [f for f in self.subject_folders(s["id"]) if os.path.isdir(f)]
            subjects.append({"id": s["id"], "name": s["name"], "color": s["color"],
                             "has_folder": bool(folders), "folder": folders[0] if folders else ""})
        phone.set_subjects(subjects)
        fetched_by = {e["id"]: e.get("fetched_by", set()) for e in phone.outbox}
        phone.set_outbox([{**t, "fetched_by": fetched_by.get(t["id"], set())}
                          for t in self.db.list_transfers("out", limit=50)])

    def set_phone_link(self, on: bool, quiet: bool = False) -> bool:
        """Starts/stops the local web server. Returns False if it couldn't start."""
        if on:
            self._sync_phone_state()
            try:
                port = self.phone.start(int(self.db.get_setting("phone_link_port", 8765)))
            except OSError as exc:
                log.warning("phone link failed to start: %s", exc)
                self.notify(tr("phone_link_failed", error=exc.strerror or exc), "error")
                self.db.set_setting("phone_link_enabled", False)
                self.data_changed.emit("phone")
                return False
            self.db.set_setting("phone_link_port", port)
        else:
            self.phone.stop()
        self.db.set_setting("phone_link_enabled", on)
        self.data_changed.emit("phone")
        if not quiet:
            self.notify(tr("phone_link_on") if on else tr("phone_link_off"), "success")
        return True

    def quick_share(self, path: str, minutes: int = 10) -> tuple[str, str] | None:
        """A QR-code link for one file (PDF reader › Quick share). Starts the local server for the time it is
        needed if the phone link is off, and stops it again when the last share ends."""
        from app.core.lan_share import local_addresses
        ips = local_addresses()
        if not ips or not os.path.isfile(path):
            return None
        if not self.phone.running:
            try:
                self._sync_phone_state()
                self.phone.start(int(self.db.get_setting("phone_link_port", 8765)))
            except OSError as exc:
                self.notify(tr("phone_link_failed", error=exc.strerror or exc), "error")
                return None
            self._share_only_server = True
            if not hasattr(self, "_share_watch"):
                self._share_watch = QTimer(self, interval=30_000, timeout=self._end_share_server)
            self._share_watch.start()
        token = self.phone.add_share(path, minutes)
        log.info("quick share: %s for %s min", Path(path).name, minutes)
        return token, self.phone.share_url(token, ips[0])

    def _end_share_server(self) -> None:
        if getattr(self, "_share_only_server", False) and not self.phone.active_shares():
            if not self.db.get_setting("phone_link_enabled", False):
                self.phone.stop()
            self._share_only_server = False
            self._share_watch.stop()

    def set_phone_option(self, key: str, value) -> None:
        self.db.set_setting(key, value)
        self._sync_phone_state()
        self.data_changed.emit("phone")

    def set_phone_cap(self, key: str, on: bool) -> None:
        """Phone › Access: what the phone app may do on this laptop."""
        self.set_phone_option(f"phone_cap_{key}", on)

    def add_phone_shared_folder(self, parent=None) -> None:
        from PyQt6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(parent or self._dialog_parent(), tr("phone_share_folder"),
                                                  str(Path.home()))
        if not folder:
            return
        folders = list(self.db.get_setting("phone_shared_folders", []) or [])
        folder = os.path.normpath(folder)
        if folder not in folders:
            folders.append(folder)
            self.set_phone_option("phone_shared_folders", folders)

    def remove_phone_shared_folder(self, folder: str) -> None:
        folders = [f for f in self.db.get_setting("phone_shared_folders", []) or [] if f != folder]
        self.set_phone_option("phone_shared_folders", folders)

    def _on_update_checked(self, result: dict) -> None:
        import time
        self.db.set_setting("update_last_check", time.time())
        self.updates.last = result
        if result.get("available") and not result.get("manual"):
            self.notify(tr("update_ready", version=result.get("version", "")), "info")
        elif result.get("manual"):
            if not result.get("ok"):
                self.notify(tr("update_check_failed", error=result.get("error", "")), "warning")
            elif not result.get("available"):
                self.notify(tr("update_none"), "success")
        self.data_changed.emit("updates")

    # ----------------------------------------------------------- continue where you left off
    def set_context(self, kind: str, **data) -> None:
        """The PDF page / note open on the laptop, offered to the phone ("continue on the phone")."""
        self.context[kind] = {**data, "at": time.time()}
        public = {}
        if "pdf" in self.context:
            c = self.context["pdf"]
            public["pdf"] = {"name": c["name"], "page": c["page"], "pages": c["pages"], "at": c["at"]}
        if "note" in self.context:
            c = self.context["note"]
            public["note"] = {"id": c["id"], "title": c["title"], "at": c["at"]}
        self.phone.set_handoff(public)

    def set_phone_context(self, data: dict) -> None:
        self.phone_context = {**data, "at": time.time()}

    def continue_from_phone(self) -> None:
        """The top bar's 📱 chip: open what the phone was on."""
        c, self.phone_context = self.phone_context, {}
        if c.get("kind") == "pdf" and c.get("path"):
            self.open_pdf(c["path"], c.get("page"))
        elif c.get("kind") == "note" and c.get("note_id"):
            self.open_notebook(c["note_id"])

    def phone_mirror(self):
        """The phone's screen on the laptop (scrcpy)."""
        if self._mirror is None:
            from app.core.phone_mirror import Mirror
            self._mirror = Mirror(self)
            self._mirror.message.connect(
                lambda text, kind: self.notify(tr(text) if text.startswith("mirror_") else text, kind))
        return self._mirror

    def new_phone_code(self) -> None:
        self.phone.code = new_code()
        self.db.set_setting("phone_link_code", self.phone.code)
        self.phone.unblock_all()
        self.data_changed.emit("phone")
        self.notify(tr("phone_link_new_code_msg"), "info")

    def choose_phone_folder(self, parent=None) -> None:
        from PyQt6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(parent or self._dialog_parent(), tr("phone_link_folder"),
                                                  self.phone_folder())
        if folder:
            self.set_phone_option("phone_link_folder", os.path.normpath(folder))

    def open_phone_page(self, tab: str | None = None) -> None:
        page = self.apps.open("phone")
        if tab and page is not None:
            page.show_tab(tab)

    # devices ----------------------------------------------------------------
    def _on_phone_paired(self, digest: str, name: str, ip: str) -> None:
        self.db.add_phone_device(name, digest, ip)
        self.notify(tr("phone_paired", name=name), "success")
        self.data_changed.emit("phone")

    def _on_phone_seen(self, digest: str, ip: str) -> None:
        device = self.db.device_by_hash(digest)
        if device:
            self.db.update_phone_device(device["id"], last_seen=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        last_ip=ip)
            self.data_changed.emit("phone_devices")

    def rename_phone_device(self, device_id: int, parent=None) -> None:
        from app.ui.dialogs.base import PromptDialog
        device = next((d for d in self.db.list_phone_devices() if d["id"] == device_id), None)
        if not device:
            return
        name = PromptDialog.ask(tr("rename"), device["name"], tr("phone_device_name"), parent or self._dialog_parent())
        if name:
            self.db.update_phone_device(device_id, name=name.strip()[:60])
            self.data_changed.emit("phone")

    def revoke_phone_device(self, device_id: int | None, parent=None) -> None:
        """Unpairs one phone (or all with None): its token stops working at once."""
        from app.ui.dialogs.base import ConfirmDialog
        title, msg = (tr("phone_unpair"), tr("phone_unpair_msg")) if device_id else \
                     (tr("phone_unpair_all"), tr("phone_unpair_all_msg"))
        if ConfirmDialog.ask(title, msg, tr("phone_unpair"), parent or self._dialog_parent()):
            self.db.delete_phone_device(device_id)
            self._sync_phone_state()
            self.data_changed.emit("phone")

    # phone -> laptop ----------------------------------------------------------
    def _on_phone_item(self, item: dict) -> None:
        """Something arrived from the phone (runs in the GUI thread)."""
        kind, text, title = item["kind"], item.get("text", ""), item.get("title", "")
        subject_id = item.get("subject_id")
        device = self.db.device_by_hash(item.get("device", ""))
        device_id = device["id"] if device else None
        pin = self.db.get_setting("phone_link_pin", True)
        content, size = text, len(text.encode("utf-8"))
        if kind == "note":
            note_id = self.save_note(text, title, source="phone", source_ref=device["name"] if device else "")
            if subject_id:
                self.db.update_note(note_id, subject_id=subject_id)
            self.notify(tr("phone_got_note"), "success")
        elif kind == "link":
            url = normalize_url(text)
            if url and pin:
                self.add_url(url, title or None, notify=False)
            elif not url:  # not a web address: keep it as a note instead
                kind = "note"
                self.save_note(text, title, source="phone")
            self.notify(tr("phone_got_link", name=title or (title_from_target(url, "url") if url else text[:40])),
                        "success")
        elif kind == "open":  # "Open on laptop" from the phone (share menu)
            self.open_url(text)
            self.notify(tr("handoff_opened", name=title or text[:60]), "success")
            kind = "link"
        elif kind == "clip":
            QApplication.clipboard().setText(text)
            self.notify(tr("phone_got_clip"), "success")
        elif kind == "task":
            due = {"today": date.today(), "tomorrow": date.today() + timedelta(days=1)}.get(item.get("due"))
            self.db.add_task(title, item.get("priority", 2), due.isoformat() if due else None, subject_id,
                             item.get("details", ""))
            self.data_changed.emit("tasks")
            self.notify(tr("phone_got_task", name=title[:50]), "success")
        elif kind == "file":
            content, size = item["path"], item.get("size", 0)
            for_lab = item.get("lab") and getattr(self, "lab", None) and self.lab.active
            if item.get("camera"):  # asked for from Phone > Home: clipboard / scan window, not the desktop
                self.phone_hub.camera_received(content, item["camera"])
            elif pin and not for_lab:  # a lab photo goes into the report, not onto the desktop
                self.add_path(content, notify=False)
            if not item.get("camera"):
                self.notify(tr("lab_phone_photo_in") if for_lab else tr("phone_got_file", name=title), "success")
        if getattr(self, "lab", None) and self.lab.active and not item.get("camera"):  # during a lab: photos / notes join the report
            if kind == "file" and Path(item["path"]).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
                self.lab.add_file("phone_file", item["path"], caption=item.get("caption") or title)
            elif kind in ("note", "clip") and text.strip():
                self.lab.add("phone_note", text=text.strip())
        log.info("phone link: received %s", kind)
        self.db.add_transfer("in", kind, title, content, size, device_id, subject_id)
        self.data_changed.emit("phone")

    def _sync_lab_to_phone(self) -> None:
        """While a lab records, the phone app shows a "lab running" banner with a camera button."""
        s = self.lab.session if getattr(self, "lab", None) else None
        self.phone.set_lab({"title": s.data.get("title", "") or tr("lab_app")} if s else {})

    def delete_phone_transfers(self, ids: list[int]) -> None:
        self.db.delete_transfers(ids)
        self._sync_phone_state()
        self.data_changed.emit("phone")

    def clear_phone_history(self, direction: str, parent=None) -> None:
        from app.ui.dialogs.base import ConfirmDialog
        if ConfirmDialog.ask(tr("phone_clear"), tr("phone_clear_msg"), tr("delete"), parent or self._dialog_parent()):
            self.db.delete_transfers(direction=direction)
            self._sync_phone_state()
            self.data_changed.emit("phone")

    # laptop -> phone ----------------------------------------------------------
    def send_to_phone(self, kind: str, content: str, title: str = "", quiet: bool = False) -> bool:
        """Puts text / a link / a file in the outbox; paired phones pick it up from their 'From laptop' tab."""
        content = content.strip() if kind != "file" else os.path.normpath(content)
        if not content:
            return False
        size = 0
        if kind == "file":
            if not os.path.isfile(content):
                return False
            size = os.path.getsize(content)
            if size > int(self.db.get_setting("phone_link_max_mb", 50)) * 1024 * 1024:
                self.notify(tr("phone_too_big", name=Path(content).name), "warning")
                return False
            title = title or Path(content).name
        elif kind == "text" and normalize_url(content) and "\n" not in content:
            kind = "link"
        self.db.add_transfer("out", kind, title, content, size)
        self.phone.push_event("outbox", title or content[:120])
        self._sync_phone_state()
        self.data_changed.emit("phone")
        if not quiet:
            self.notify(tr("phone_queued") if self.phone.running else tr("phone_queued_off"),
                        "success" if self.phone.running else "warning")
        return True

    def send_paths_to_phone(self, paths: list[str]) -> None:
        sent = sum(self.send_to_phone("file", p, quiet=True) for p in paths if os.path.isfile(p))
        if sent:
            self.notify(tr("phone_queued_n", n=sent) if self.phone.running else tr("phone_queued_off"),
                        "success" if self.phone.running else "warning")

    def send_items_to_phone(self, ids: list[int]) -> None:
        """Desktop icons -> phone: websites as links, files as files, sticky notes as text."""
        sent = 0
        for item_id in ids:
            item = self.db.get_item(item_id)
            if not item:
                continue
            if item["kind"] == "url":
                sent += self.send_to_phone("link", item["target"], item["title"], quiet=True)
            elif item["kind"] == "file":
                sent += self.send_to_phone("file", item["target"], quiet=True)
            elif item["kind"] == "note":
                note = self.db.get_note(int(item["target"])) if str(item["target"]).isdigit() else None
                if note:
                    sent += self.send_to_phone("text", note["content"], note["title"] or item["title"], quiet=True)
        if sent:
            self.notify(tr("phone_queued_n", n=sent) if self.phone.running else tr("phone_queued_off"),
                        "success" if self.phone.running else "warning")

    def send_clipboard_to_phone(self) -> None:
        md = QApplication.clipboard().mimeData()
        files = [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md is not None and md.hasUrls() else []
        if files:
            self.send_paths_to_phone(files)
        elif md is not None and md.text().strip():
            self.send_to_phone("text", md.text())
        else:
            self.notify(tr("clipboard_empty"), "warning")

    def send_files_to_phone_dialog(self, parent=None) -> None:
        from PyQt6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(parent or self._dialog_parent(), tr("phone_send_files"))
        if paths:
            self.send_paths_to_phone(paths)

    def _on_phone_fetched(self, transfer_id: int, digest: str) -> None:
        self.db.set_transfer_fetched(transfer_id)
        self.data_changed.emit("phone")

    # ================================================================ storage
    def compact_database(self) -> int:
        """VACUUM: gives back the space of deleted rows. Returns bytes freed."""
        files = lambda: sum(p.stat().st_size for p in DATA_DIR.glob("studyhub.db*") if p.is_file())
        before = files()
        try:
            self.db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.conn.execute("VACUUM")
            self.db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as exc:
            log.warning("compact failed: %s", exc)
            return 0
        freed = max(0, before - files())
        self.add_space_freed(freed)
        return freed

    def add_space_freed(self, size: int) -> None:
        if size > 0:
            self.db.set_setting("space_freed_total", int(self.db.get_setting("space_freed_total", 0)) + int(size))

    def maybe_auto_clean(self) -> None:
        """Weekly, if turned on: temp files, crash dumps, old backups, leftover attachments (never the
        Recycle Bin, browser caches or your own files)."""
        if not self.db.get_setting("auto_clean", False):
            return
        last = float(self.db.get_setting("auto_clean_last", 0))
        if time.time() - last < 7 * 86400:
            return
        import threading
        from app.core.cleanup import Cleaner
        note_ids = {r["id"] for r in self.db.query("SELECT id FROM notes")}
        keep = int(self.db.get_setting("backups_keep", 7))
        cleaner = Cleaner(lambda: note_ids, lambda: keep)
        self.db.set_setting("auto_clean_last", time.time())

        def work():
            freed = sum(cleaner.clean(c.key) for c in Cleaner.CATEGORIES if c.safe_auto)
            self._auto_clean_done.emit(freed)
        threading.Thread(target=work, daemon=True).start()

    def _on_auto_clean_done(self, freed: int) -> None:
        self.add_space_freed(freed)
        log.info("weekly clean freed %s bytes", freed)
        if freed > 50 * 1024 * 1024:
            from app.core.cleanup import human
            self.notify(tr("auto_cleaned", size=human(freed)), "success")

    def backup_now(self) -> None:
        target = BACKUP_DIR / f"studyhub-{datetime.now():%Y%m%d-%H%M%S}.db"
        if self._safe(lambda: self.db.backup(target)):
            self.notify(tr("backup_done", path=target.name), "success")

    # ================================================================= notes
    def open_quick_note(self, text: str = "", source: str = "quick", source_ref: str = "",
                        note_id: int | None = None) -> None:
        self.quick_note.open(text, source, source_ref, note_id)

    def save_note(self, content: str, title: str = "", source: str = "quick", source_ref: str = "",
                  note_id: int | None = None) -> int:
        existing = self.db.get_note(note_id) if note_id else None
        if existing and existing["locked"]:
            self.autosave_note(note_id, title, content)  # stays encrypted (needs the vault open)
            return note_id
        if note_id:
            self.db.update_note(note_id, title=title, content=content)
        else:
            note_id = self.db.add_note(content, title, source, source_ref)
        self.index_note(note_id, content)
        self.data_changed.emit("notes")
        return note_id

    def delete_note(self, note_id: int, confirm_parent=None) -> None:
        """Moves the note to the notebook's trash (kept 30 days, restorable)."""
        note = self.db.get_note(note_id)
        if not note:
            return
        self.snapshot_note(note_id, force=True)
        self.db.trash_note(note_id)
        for item in self.db.list_items():  # its sticky notes leave the desktop
            if item["kind"] == "note" and item["target"] == str(note_id):
                self.db.delete_item(item["id"])
        self.data_changed.emit("notes")
        self.data_changed.emit("launcher")
        self.notify(tr("note_trashed"), "success")

    def restore_note(self, note_id: int) -> None:
        self.db.restore_note(note_id)
        self.data_changed.emit("notes")
        self.notify(tr("note_restored"), "success")

    def delete_note_forever(self, note_ids: list[int], confirm_parent=None, ask: bool = True) -> bool:
        from app.ui.dialogs.base import ConfirmDialog
        if ask and not ConfirmDialog.ask(tr("delete_forever_title"), tr("delete_forever_msg", n=len(note_ids)),
                                         tr("delete_forever"), confirm_parent):
            return False
        from app.core import fileops
        from app.ui.widgets.note_editor import attachment_dir
        folders = [str(attachment_dir(i)) for i in note_ids if attachment_dir(i).exists()]
        for note_id in note_ids:
            self.db.delete_note(note_id)
        if folders:  # pasted images / files -> Recycle Bin (still recoverable there)
            fileops.recycle(folders)
        self.data_changed.emit("notes")
        return True

    def empty_trash(self, parent=None) -> None:
        ids = [n["id"] for n in self.db.list_notes("trash")]
        if ids and self.delete_note_forever(ids, parent):
            self.notify(tr("trash_emptied"), "success")

    def purge_old_trash(self) -> None:
        ids = self.db.expired_trash(30)
        if ids:
            log.info("notebook: %s note(s) left the trash after 30 days", len(ids))
            self.delete_note_forever(ids, ask=False)

    def file_note(self, note_id: int) -> None:
        self.db.update_note(note_id, is_inbox=0)
        self.data_changed.emit("notes")
        self.notify(tr("note_filed"), "success")

    def capture_selection(self) -> None:
        self.capture.capture()

    def _on_captured(self, text: str, source_window: str) -> None:
        if not text:
            self.notify(tr("nothing_selected"), "warning")
            return
        self.save_note(text, source="selection", source_ref=source_window)
        self.notify(tr("selection_saved"), "success")

    def clipboard_note(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            self.notify(tr("clipboard_empty"), "warning")
            return
        self.save_note(text, source="clipboard")
        self.notify(tr("clipboard_saved"), "success")

    def copy_text(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self.notify(tr("copied"), "success")

    # ============================================================== launcher
    def launch_item(self, item_id: int, admin: bool = False) -> None:
        item = self.db.get_item(item_id)
        if not item:
            return
        if item["kind"] == "note":  # sticky note -> floating editor
            if item["target"].isdigit() and self.db.get_note(int(item["target"])):
                self.open_quick_note(note_id=int(item["target"]))
            return
        if item["kind"] == "widget":  # live window: double-click opens it in its full program
            kind = item["args"]
            if kind == "web":
                self.open_url(item["target"])
            elif kind == "pdf":
                self.open_path(item["target"])
            elif kind == "focus":
                self.apps.open("timer")
            elif kind in ("todo", "classes"):
                self.open_study_tab("tasks" if kind == "todo" else "timetable")
            else:
                self.edit_widget(item_id)
            return
        if item["kind"] == "folder" and self.use_builtin_explorer():
            self.open_explorer(item["target"])
            self.db.record_launch(item_id)
            return
        if open_target(item["kind"], item["target"], item["args"], admin):
            self.db.record_launch(item_id)
        else:
            self.notify(tr("open_failed", name=item["title"]), "error")

    def _new_item(self, kind: str, title: str, target: str, group_id: int | None = None, args: str = "",
                  cell: tuple | None = None, color: str = "") -> int:
        """Creates a launcher/desktop item. cell = (desk, (col, row)) places it where it was dropped."""
        item_id = self.db.add_item(kind, title, target, group_id, args, color=color)
        if cell and cell[1] is not None:
            desk, (c, r) = cell
            from app.ui.pages.desktop_page import default_cells
            size = default_cells({"kind": kind, "args": args})
            self.db.set_positions(desk, {item_id: (c, r, *size)})
        if kind == "url":
            self.favicons.fetch(item_id, target)
        return item_id

    def _on_favicon(self, item_id: int, path: str) -> None:
        if self.db.get_item(item_id):
            self.db.update_item(item_id, icon_path=path)
            self._logos_timer.start()  # many logos arrive together: refresh the UI once

    def _emit_logos(self) -> None:
        self.data_changed.emit("logos")

    def refresh_logo(self, item_id: int) -> None:
        item = self.db.get_item(item_id)
        if item and item["kind"] == "url":
            cached = self.favicons.cached_path(item["target"])
            cached.unlink(missing_ok=True)
            self.db.update_item(item_id, icon_path="")
            self.favicons.fetch(item_id, item["target"])
            self.data_changed.emit("launcher")

    def _backfill_logos(self) -> None:
        """Websites added before logos existed get theirs now (one after another, in the background)."""
        for item in self.db.list_items():
            if item["kind"] == "url" and not item["icon_path"]:
                self.favicons.fetch(item["id"], item["target"])

    def rename_item(self, item_id: int, title: str) -> None:
        self.db.update_item(item_id, title=title)
        self.data_changed.emit("launcher")

    def sticky_item(self, note_id: int | None) -> dict | None:
        """The desktop sticky note that shows this note (if any)."""
        if not note_id:
            return None
        return next((i for i in self.db.list_items() if i["kind"] == "note" and i["target"] == str(note_id)), None)

    def set_sticky_font(self, item_id: int, delta: float = 0, size: float | None = None) -> float:
        import json
        item = self.db.get_item(item_id)
        if not item:
            return 9.3
        try:
            conf = json.loads(item.get("icon_path") or "{}")
        except ValueError:
            conf = {}
        new = size if size is not None else float(conf.get("font", 9.3)) + delta
        conf["font"] = max(7.0, min(24.0, round(new, 1)))
        self.db.update_item(item_id, icon_path=json.dumps(conf))
        self.data_changed.emit("launcher")
        return conf["font"]

    def set_item_color(self, item_id: int, color: str) -> None:
        self.db.update_item(item_id, color=color)
        self.data_changed.emit("launcher")

    def remove_items(self, ids: list[int], parent=None) -> None:
        """Removes items from the desktop (files and notes themselves are never deleted)."""
        from app.ui.dialogs.base import ConfirmDialog
        if len(ids) > 1 and not ConfirmDialog.ask(tr("remove_n", n=len(ids)), tr("remove_items_msg"),
                                                  tr("remove"), parent or self._dialog_parent()):
            return
        for i in ids:
            self.db.delete_item(i)
        self.data_changed.emit("launcher")
        self.notify(tr("removed_n", n=len(ids)), "info")

    def new_sticky_note(self, desk: str = "all", cell=None, text: str = "", open_editor: bool = True) -> int:
        from app.ui.pages.desktop_page import NOTE_COLORS
        note_id = self.db.add_note(text, "", "manual", is_inbox=False)
        count = sum(1 for i in self.db.list_items() if i["kind"] == "note")
        title = (text.strip().splitlines() or [tr("sticky_note")])[0][:40]
        group_id = None if desk == "all" else int(desk)
        self._new_item("note", title, str(note_id), group_id, cell=(desk, cell),
                       color=NOTE_COLORS[count % len(NOTE_COLORS)])
        self.data_changed.emit("launcher")
        if open_editor:
            self.open_quick_note(note_id=note_id)
        return note_id

    # ------------------------------------------------------ copy / paste
    def copy_items(self, ids: list[int]) -> None:
        """Files/folders go to the clipboard as real files (paste them in Explorer);
        links and notes as text."""
        from PyQt6.QtCore import QMimeData, QUrl
        urls, lines = [], []
        for i in ids:
            item = self.db.get_item(i)
            if not item:
                continue
            if item["kind"] in ("app", "file", "folder") and os.path.exists(item["target"]):
                urls.append(QUrl.fromLocalFile(item["target"]))
                lines.append(item["target"])
            elif item["kind"] == "note" and item["target"].isdigit():
                note = self.db.get_note(int(item["target"]))
                if note:
                    lines.append(((note["title"] + "\n") if note["title"] else "") + note["content"])
            else:
                lines.append(item["target"])
        md = QMimeData()
        if urls:
            md.setUrls(urls)
        md.setText("\n".join(lines))
        QApplication.clipboard().setMimeData(md)
        self.notify(tr("copied_items", n=len(ids)), "success")

    def clipboard_has_content(self) -> bool:
        md = QApplication.clipboard().mimeData()
        return md is not None and (md.hasUrls() or bool(md.text().strip()))

    def paste_to_desktop(self, desk: str = "all", cell=None) -> None:
        md = QApplication.clipboard().mimeData()
        if md is None or not (md.hasUrls() or md.text().strip()):
            self.notify(tr("clipboard_empty"), "warning")
            return
        self.drop_to_desktop(md, desk, cell)

    def drop_to_desktop(self, md, desk: str = "all", cell=None) -> None:
        """Copied/dropped files -> icons, links -> websites (with logo), any other text -> sticky note."""
        group_id = None if desk == "all" else int(desk)
        added, notes = 0, 0
        local = [u.toLocalFile() for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []
        web = [u.toString() for u in md.urls() if not u.isLocalFile()] if md.hasUrls() else []
        text = md.text().strip() if md.hasText() else ""
        if not local and not web and text:
            lines = [ln.strip().strip('"') for ln in text.splitlines() if ln.strip()]
            if lines and all(os.path.exists(ln) for ln in lines):
                local = lines            # copied paths ("Copy as path" in Explorer)
            elif lines and all(normalize_url(ln) for ln in lines):
                web = lines
        for path in local:
            path = os.path.normpath(path)
            if os.path.exists(path):
                kind = detect_kind(path)
                self._new_item(kind, title_from_target(path, kind), path, group_id, cell=(desk, cell))
                added += 1
        for link in web:
            url = normalize_url(link)
            if url:
                self._new_item("url", title_from_target(url, "url"), url, group_id, cell=(desk, cell))
                added += 1
        if not added and text:
            self.new_sticky_note(desk, cell, text, open_editor=False)
            notes = 1
        self.data_changed.emit("launcher")
        if added:
            self.notify(tr("pinned_n", n=added), "success")
        elif notes:
            self.notify(tr("sticky_created"), "success")

    def paste_hotkey(self) -> None:
        """Global shortcut: whatever is in the clipboard goes onto the main desktop."""
        self.paste_to_desktop("all")

    def reveal_item(self, item_id: int) -> None:
        item = self.db.get_item(item_id)
        if item:
            reveal_in_explorer(item["target"])

    def add_path(self, path: str, group_id: int | None = None, notify: bool = True) -> int:
        """Pins a program / file / folder (Explorer menu, drag & drop). Returns 1 if added."""
        path = path.strip().strip('"')
        if not Path(path).exists():
            if notify:
                self.notify(tr("error_path_missing"), "error")
            return 0
        kind = detect_kind(path)
        title = title_from_target(path, kind)
        self._new_item(kind, title, path, group_id)
        self.data_changed.emit("launcher")
        if notify:
            self.notify(tr("pinned", name=title), "success")
        return 1

    def show_add_gallery(self, desk: str | None = None, paths: list[str] | None = None, parent=None) -> None:
        """"Add to Study Hub": programs / websites / files from ready lists (app/ui/dialogs/add_gallery.py)."""
        from app.ui.dialogs.add_gallery import AddGallery
        AddGallery(self, desk, paths, parent or self._dialog_parent()).exec()

    def add_entries(self, entries: list[dict], desk: str = "all", pin_taskbar: bool = False) -> list[int]:
        """Adds what the gallery picked to one desktop (and, if asked, to the taskbar) in one go."""
        group_id = None if str(desk) == "all" else int(desk)
        ids = []
        for e in entries:
            kind = e["kind"] if e["kind"] in ("app", "url", "folder", "file") else "file"
            ids.append(self._new_item(kind, e["name"], e["target"], group_id))
        self.data_changed.emit("launcher")
        if pin_taskbar and ids:
            own = self.desk_look(str(desk)).get("pinned")
            pinned = list(own if own is not None else self.taskbar_conf(global_only=True)["pinned"])
            pinned += [f"item:{i}" for i in ids if f"item:{i}" not in pinned]
            if own is not None:
                self.set_desk_look(str(desk), pinned=pinned)
            else:
                self.set_taskbar(pinned=pinned)
        names = ", ".join(e["name"] for e in entries[:3]) + ("…" if len(entries) > 3 else "")
        self.notify(tr("addg_added", n=len(ids), names=names), "success")
        if str(desk) != self.current_desk() and hasattr(self.window, "desktop"):
            self.window.desktop.show_desk(str(desk))
        return ids

    def add_url(self, text: str, title: str | None = None, group_id: int | None = None,
                notify: bool = True) -> int:
        url = normalize_url(text)
        if not url:
            return 0
        title = title or title_from_target(url, "url")
        self._new_item("url", title, url, group_id)
        self.data_changed.emit("launcher")
        if notify:
            self.notify(tr("pinned", name=title), "success")
        return 1

    def clipboard_link(self, group_id: int | None = None) -> None:
        text = QApplication.clipboard().text().strip()
        if normalize_url(text):
            self.add_url(text, group_id=group_id)
        else:  # nothing usable in the clipboard: let the user type the link
            self.add_item_dialog("url", group_id=group_id)

    def _dialog_parent(self):
        return self.window if self.window.isVisible() else None

    def add_item_dialog(self, kind: str | None = None, target: str = "", group_id: int | None = None,
                        cell=None) -> None:
        from app.ui.dialogs.add_item import AddItemDialog
        dlg = AddItemDialog(self.db.list_groups(), kind=kind, target=target, group_id=group_id,
                            parent=self._dialog_parent())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.result_data()
            self._new_item(data["kind"], data["title"], data["target"], data["group_id"], data["args"], cell)
            self.data_changed.emit("launcher")
            self.notify(tr("pinned", name=data["title"]), "success")

    def edit_item(self, item_id: int, parent=None) -> None:
        from app.ui.dialogs.add_item import AddItemDialog
        item = self.db.get_item(item_id)
        if not item:
            return
        if item["kind"] == "note":
            return self.launch_item(item_id)
        if item["kind"] == "widget":  # desktop widgets: look, size and content (app/ui/dialogs/widget_edit.py)
            desk = self.window.desktop.canvas.desk if hasattr(self.window, "desktop") else "all"
            return self.edit_widget(item_id, desk, parent)
        dlg = AddItemDialog(self.db.list_groups(), item=item, parent=parent or self._dialog_parent())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.result_data()
            if data["target"] != item["target"]:
                data["icon_path"] = ""  # target changed: old icon/logo no longer applies
            self.db.update_item(item_id, **data)
            if data["kind"] == "url" and data["target"] != item["target"]:
                self.favicons.fetch(item_id, data["target"])
            self.data_changed.emit("launcher")

    def move_item(self, item_id: int, group_id: int | None) -> None:
        self.db.update_item(item_id, group_id=group_id)
        self.data_changed.emit("launcher")

    def remove_item(self, item_id: int) -> None:
        item = self.db.get_item(item_id)
        if item:
            self.db.delete_item(item_id)
            self.data_changed.emit("launcher")
            self.notify(tr("removed", name=item["title"]), "info")

    def add_group(self, parent=None) -> None:
        from app.ui.dialogs.base import PromptDialog
        name = PromptDialog.ask(tr("new_group"), placeholder=tr("group_name"), parent=parent or self._dialog_parent())
        if name:
            self.db.add_group(name)
            self.data_changed.emit("launcher")

    def rename_group(self, group_id: int, parent=None) -> None:
        from app.ui.dialogs.base import PromptDialog
        group = next((g for g in self.db.list_groups() if g["id"] == group_id), None)
        if group:
            name = PromptDialog.ask(tr("rename"), group["name"], parent=parent or self._dialog_parent())
            if name:
                self.db.rename_group(group_id, name)
                self.data_changed.emit("launcher")

    def delete_group(self, group_id: int, parent=None) -> None:
        from app.ui.dialogs.base import ConfirmDialog
        if ConfirmDialog.ask(tr("delete_group"), tr("delete_group_msg"), tr("delete"),
                             parent or self._dialog_parent()):
            self.db.delete_group(group_id)
            self.data_changed.emit("launcher")

    # ================================================================= tasks
    def add_task(self, title: str, due_date: str | None = "today", subject_id: int | None = None,
                 priority: int = 2) -> None:
        if due_date == "today":
            due_date = date.today().isoformat()
        self.db.add_task(title, priority, due_date, subject_id)
        self.data_changed.emit("tasks")

    def add_task_dialog(self, due: str | None = None, subject_id: int | None = None, parent=None) -> None:
        from app.ui.dialogs.study_dialogs import TaskDialog
        dlg = TaskDialog(self.db.list_subjects(), due=due, subject_id=subject_id,
                         parent=parent or self._dialog_parent())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            d = dlg.result_data()
            self.db.add_task(d["title"], d["priority"], d["due_date"], d["subject_id"], d["details"])
            self.data_changed.emit("tasks")

    def edit_task(self, task_id: int, parent=None) -> None:
        from app.ui.dialogs.study_dialogs import TaskDialog
        task = self.db.get_task(task_id)
        if not task:
            return
        dlg = TaskDialog(self.db.list_subjects(), task=task, parent=parent or self._dialog_parent())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.update_task(task_id, **dlg.result_data())
            self.data_changed.emit("tasks")

    def set_task_due(self, task_id: int, due: str | None) -> None:
        self.db.update_task(task_id, due_date=due)
        self.data_changed.emit("tasks")

    def focus_on_task(self, task_id: int) -> None:
        task = self.db.get_task(task_id)
        if task:
            self.pomodoro_task_id = task_id
            self.pomodoro_subject_id = task["subject_id"] or self.pomodoro_subject_id
            self.open_study_tab("focus")
            self.data_changed.emit("tasks")  # refreshes the focus view's selectors

    def set_task_done(self, task_id: int, done: bool) -> None:
        self.db.set_task_done(task_id, done)
        self.data_changed.emit("tasks")
        if done:
            self.notify(tr("task_done"), "success")

    def set_task_priority(self, task_id: int, priority: int) -> None:
        self.db.update_task(task_id, priority=priority)
        self.data_changed.emit("tasks")

    def postpone_task(self, task_id: int) -> None:
        self.db.update_task(task_id, due_date=(date.today() + timedelta(days=1)).isoformat())
        self.data_changed.emit("tasks")

    def delete_task(self, task_id: int) -> None:
        self.db.delete_task(task_id)
        self.data_changed.emit("tasks")

    # ============================================================== subjects
    def week_start(self) -> int:
        """First day of the week shown in the timetable (0 = Monday ... 5 = Saturday)."""
        return int(self.db.get_setting("week_start", 5))

    def set_week_start(self, day: int) -> None:
        self.db.set_setting("week_start", int(day))
        self.data_changed.emit("settings")

    def add_subject_dialog(self, parent=None) -> None:
        from app.ui.dialogs.study_dialogs import SubjectDialog
        dlg = SubjectDialog(self.db.list_items(), parent=parent or self._dialog_parent())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            d = dlg.result_data()
            sid = self.db.add_subject(d["name"], d["color"], d["code"], d["teacher"], d["folder_path"])
            self.db.set_subject_items(sid, dlg.linked_items())
            self.data_changed.emit("subjects")
            self.notify(tr("subject_added", name=d["name"]), "success")

    def edit_subject(self, subject_id: int, parent=None) -> None:
        from app.ui.dialogs.study_dialogs import SubjectDialog
        subject = self.db.get_subject(subject_id)
        if not subject:
            return
        dlg = SubjectDialog(self.db.list_items(), subject, self.db.subject_item_ids(subject_id),
                            parent=parent or self._dialog_parent())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.update_subject(subject_id, **dlg.result_data())
            self.db.set_subject_items(subject_id, dlg.linked_items())
            self.data_changed.emit("subjects")

    def delete_subject(self, subject_id: int, parent=None) -> None:
        from app.ui.dialogs.base import ConfirmDialog
        subject = self.db.get_subject(subject_id)
        if subject and ConfirmDialog.ask(tr("delete_subject"), tr("delete_subject_msg", name=subject["name"]),
                                         tr("delete"), parent or self._dialog_parent()):
            self.db.delete_subject(subject_id)
            self.data_changed.emit("subjects")

    def start_study_mode(self, subject_id: int) -> None:
        """Opens the subject's apps/sites/folder, selects it in the timer and starts focusing."""
        subject = self.db.get_subject(subject_id)
        if not subject:
            return
        for item_id in self.db.subject_item_ids(subject_id):
            self.launch_item(item_id)
        if subject["folder_path"]:
            self.open_folder(subject["folder_path"])
        self.pomodoro_subject_id = subject_id
        self.pomodoro_task_id = None
        if self.pomodoro.phase != "focus" and self.pomodoro.state != "running":
            self.pomodoro.set_phase("focus")
        self.show_window()
        self.open_study_tab("focus")
        self.data_changed.emit("subjects")
        if self.pomodoro.state != "running":
            self.pomodoro.start()
        self.notify(tr("study_mode_started", name=subject["name"]), "success")

    def open_folder(self, path: str) -> None:
        if not open_target("folder", path):
            self.notify(tr("error_path_missing"), "error")
        elif hasattr(self, "recents"):
            self.recents.add("files", "folder", os.path.normpath(path), Path(path).name or path)

    def open_study_tab(self, tab: str) -> None:
        if tab == "focus":  # the focus timer is its own app
            self.apps.open("timer")
            return
        page = self.apps.open("study")
        if page is not None:
            page.show_tab(tab)

    # ============================================================== schedule
    def add_slot_dialog(self, day: int | None = None, start: str = "08:00", parent=None,
                        subject_id: int | None = None) -> None:
        from app.ui.dialogs.study_dialogs import SlotDialog
        subjects = self.db.list_subjects()
        if not subjects:
            self.notify(tr("error_no_subject"), "warning")
            return self.add_subject_dialog(parent)
        slot = {"subject_id": subject_id} if subject_id else None
        dlg = SlotDialog(subjects, self.week_start(), slot=slot, day=day if day is not None else date.today().weekday(),
                         start=start, parent=parent or self._dialog_parent())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            d = dlg.result_data()
            self.db.add_slot(d["subject_id"], d["day_of_week"], d["start_time"], d["end_time"], d["location"], d["kind"])
            self.data_changed.emit("schedule")

    def edit_slot(self, slot_id: int, parent=None) -> None:
        from app.ui.dialogs.study_dialogs import SlotDialog
        slot = self.db.get_slot(slot_id)
        if not slot:
            return
        dlg = SlotDialog(self.db.list_subjects(), self.week_start(), slot=slot, parent=parent or self._dialog_parent())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.update_slot(slot_id, **dlg.result_data())
            self.data_changed.emit("schedule")

    def duplicate_slot(self, slot_id: int) -> None:
        slot = self.db.get_slot(slot_id)
        if slot:
            self.db.add_slot(slot["subject_id"], (slot["day_of_week"] + 1) % 7, slot["start_time"],
                             slot["end_time"], slot["location"], slot["kind"])
            self.data_changed.emit("schedule")

    def delete_slot(self, slot_id: int) -> None:
        self.db.delete_slot(slot_id)
        self.data_changed.emit("schedule")

    # ============================================================== pomodoro
    def pomodoro_settings(self) -> dict:
        conf = {**DEFAULT_DURATIONS, "long_every": 4, "auto_start": False}
        conf.update(self.db.get_setting("pomodoro", {}) or {})
        return conf

    def save_pomodoro_settings(self, conf: dict) -> None:
        self.db.set_setting("pomodoro", conf)
        self.pomodoro.auto_start = bool(conf.get("auto_start"))
        self.pomodoro.set_durations({k: conf[k] for k in ("focus", "short_break", "long_break") if k in conf},
                                    conf.get("long_every"))

    def _on_pomodoro_finished(self, phase: str, minutes: int) -> None:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
        if phase == "focus":
            self.db.add_pomodoro(minutes, "focus", self.pomodoro_subject_id, self.pomodoro_task_id)
            self.data_changed.emit("pomodoro")
            self.notify(tr("focus_done", n=minutes), "success")
            self.phone.push_event("timer", tr("focus_done", n=minutes))
        else:
            self.notify(tr("break_done"), "info")
            self.phone.push_event("timer", tr("break_done"))

    # ================================================================ notebook
    def open_notebook(self, note_id: int | None = None, subject_id: int | None = None) -> None:
        page = self.apps.open("notes")
        if page is not None:
            page.select_note(note_id, subject_id)

    def open_notes_action(self, action: str) -> None:
        """Palette: today's note / go to note."""
        page = self.apps.open("notes")
        if page is not None:
            QTimer.singleShot(50, page.open_daily if action == "daily" else page.quick_switch)

    def create_note(self, subject_id: int | None = None, is_inbox: bool = False, title: str = "",
                    content: str = "", folder_id: int | None = None, is_template: bool = False) -> int:
        note_id = self.db.add_note(content, title, "manual", is_inbox=is_inbox, subject_id=subject_id)
        if folder_id or is_template:
            self.db.update_note(note_id, folder_id=folder_id, is_template=int(is_template))
        self.index_note(note_id, content)
        self.data_changed.emit("notes")
        return note_id

    def autosave_note(self, note_id: int, title: str, content: str) -> bool:
        note = self.db.get_note(note_id)
        if not note:
            return False
        if note["locked"]:
            if not self.vault.unlocked:
                self.notify(tr("note_locked_unsaved"), "warning")
                return False
            self.db.update_note(note_id, title=title, content="", secret=self.vault.encrypt({"content": content}))
            self.vault_activity()
        else:
            self.db.update_note(note_id, title=title, content=content)
            self.index_note(note_id, content)
            self.snapshot_note(note_id)
        self.data_changed.emit("notes")
        return True

    # ------------------------------------------------------------- notebook
    def index_note(self, note_id: int, content: str) -> None:
        from app.core import notes_index
        self.db.set_note_index(note_id, notes_index.tags(content), notes_index.links(content))

    SNAPSHOT_MINUTES = 10

    def snapshot_note(self, note_id: int, force: bool = False) -> None:
        """Keeps a version every 10 minutes of editing (and on demand). Locked notes never get plaintext copies."""
        note = self.db.get_note(note_id)
        if not note or note["locked"] or not (note["content"].strip() or note["title"].strip()):
            return
        last = self.db.last_version(note_id)
        if last and last["content"] == note["content"] and last["title"] == note["title"]:
            return
        if not force and last:
            age = datetime.now() - datetime.strptime(last["created_at"], "%Y-%m-%d %H:%M:%S")
            if age < timedelta(minutes=self.SNAPSHOT_MINUTES):
                return
        self.db.add_version(note_id, note["title"], note["content"])

    def restore_version(self, note_id: int, version_id: int) -> None:
        version = next((v for v in self.db.list_versions(note_id) if v["id"] == version_id), None)
        if not version:
            return
        self.snapshot_note(note_id, force=True)  # the current text stays in the history
        self.db.update_note(note_id, title=version["title"], content=version["content"])
        self.index_note(note_id, version["content"])
        self.data_changed.emit("notes")
        self.notify(tr("version_restored"), "success")

    def note_content(self, note: dict) -> str | None:
        """Plain text of a note; None for a locked note while the vault is closed."""
        if not note["locked"]:
            return note["content"]
        if not self.vault.unlocked or not note["secret"]:
            return None
        try:
            return self.vault.decrypt(note["secret"]).get("content", "")
        except Exception:
            return None

    def rename_links(self, old: str, new: str) -> int:
        """After a title change, [[Old]] in other notes becomes [[New]]."""
        from app.core import notes_index
        if not old.strip() or not new.strip() or old.strip().lower() == new.strip().lower():
            return 0
        changed = 0
        for note in self.db.notes_linking_to(old):
            if note["locked"] or note["deleted_at"]:
                continue
            text = notes_index.rename_links(note["content"], old, new)
            if text != note["content"]:
                self.db.update_note(note["id"], content=text)
                self.index_note(note["id"], text)
                changed += 1
        if changed:
            self.data_changed.emit("notes")
            self.notify(tr("links_updated", n=changed), "info")
        return changed

    def open_or_create_by_title(self, title: str) -> int:
        note = self.db.note_by_title(title)
        if note:
            return note["id"]
        return self.create_note(title=title.strip())

    # folders --------------------------------------------------------------
    def new_folder(self, parent_id: int | None = None, parent=None) -> int | None:
        from app.ui.dialogs.base import PromptDialog
        name = PromptDialog.ask(tr("new_folder"), "", tr("folder_name"), parent or self._dialog_parent())
        if not name:
            return None
        folder_id = self.db.add_folder(name.strip()[:80], parent_id)
        self.data_changed.emit("notes")
        return folder_id

    def rename_folder(self, folder_id: int, parent=None) -> None:
        from app.ui.dialogs.base import PromptDialog
        folder = self.db.get_folder(folder_id)
        name = folder and PromptDialog.ask(tr("rename"), folder["name"], tr("folder_name"),
                                           parent or self._dialog_parent())
        if name:
            self.db.update_folder(folder_id, name=name.strip()[:80])
            self.data_changed.emit("notes")

    def set_folder_color(self, folder_id: int, color: str) -> None:
        self.db.update_folder(folder_id, color=color)
        self.data_changed.emit("notes")

    def delete_folder(self, folder_id: int, parent=None) -> None:
        from app.ui.dialogs.base import ConfirmDialog
        folder = self.db.get_folder(folder_id)
        if folder and ConfirmDialog.ask(tr("delete_folder"), tr("delete_folder_msg", name=folder["name"]),
                                        tr("delete"), parent or self._dialog_parent()):
            self.db.delete_folder(folder_id)
            self.data_changed.emit("notes")

    def move_notes(self, note_ids: list[int], folder_id: int | None) -> None:
        for note_id in note_ids:
            self.db.update_note(note_id, folder_id=folder_id, is_inbox=0)
        self.data_changed.emit("notes")
        self.notify(tr("notes_moved", n=len(note_ids)), "success")

    # templates & daily note ---------------------------------------------
    def new_note_from_template(self, key, folder_id: int | None = None, subject_id: int | None = None) -> int:
        """key: a built-in template name, or the id of a template note."""
        from app.core import notes_index
        if isinstance(key, int):
            tpl = self.db.get_note(key)
            content = self.note_content(tpl) if tpl else ""
            title = notes_index.fill(tpl["title"]) if tpl else ""
            content = notes_index.fill(content or "", title)
        else:
            title, content = notes_index.builtin(key, i18n.language())
        return self.create_note(subject_id=subject_id, title=title, content=content, folder_id=folder_id)

    def save_as_template(self, note_id: int) -> None:
        note = self.db.get_note(note_id)
        content = self.note_content(note) if note else None
        if content is None:
            return
        self.create_note(title=note["title"], content=content, is_template=True)
        self.notify(tr("template_saved"), "success")

    def daily_note(self) -> int:
        """Today's note (Daily folder); created from your "Daily" template or the built-in one."""
        from app.core import notes_index
        today = date.today().isoformat()
        existing = self.db.note_by_title(today)
        if existing and not existing["is_template"]:
            return existing["id"]
        folder_id = self.db.folder_by_path([tr("daily_folder")])
        own = next((n for n in self.db.list_notes("templates")
                    if n["title"].strip().lower() in ("daily", "يومي", tr("daily_folder").lower())), None)
        if own:
            content = notes_index.fill(self.note_content(own) or "", today)
        else:
            content = notes_index.builtin("daily", i18n.language())[1]
        return self.create_note(title=today, content=content, folder_id=folder_id)

    # lock with the vault ------------------------------------------------
    def ensure_vault_unlocked(self, parent=None) -> bool:
        if self.vault.unlocked:
            return True
        if not self.vault.is_set_up():
            self.notify(tr("vault_needed_for_lock"), "warning")
            self.show_window("vault")
            return False
        from app.ui.dialogs.notes_dialogs import UnlockVaultDialog
        dlg = UnlockVaultDialog(self, parent or self._dialog_parent())
        return dlg.exec() == QDialog.DialogCode.Accepted and self.vault.unlocked

    def lock_note(self, note_id: int, parent=None) -> None:
        note = self.db.get_note(note_id)
        if not note or note["locked"] or not self.ensure_vault_unlocked(parent):
            return
        self.db.update_note(note_id, locked=1, content="", secret=self.vault.encrypt({"content": note["content"]}))
        self.db.delete_versions(note_id)        # no plaintext copies left behind
        self.db.set_note_index(note_id, [], [])
        self.data_changed.emit("notes")
        self.notify(tr("note_locked"), "success")

    def unlock_note(self, note_id: int, parent=None) -> None:
        note = self.db.get_note(note_id)
        if not note or not note["locked"] or not self.ensure_vault_unlocked(parent):
            return
        content = self.note_content(note) or ""
        self.db.update_note(note_id, locked=0, secret=None, content=content)
        self.index_note(note_id, content)
        self.data_changed.emit("notes")
        self.notify(tr("note_unlocked"), "success")

    # import / export ----------------------------------------------------
    def import_notes(self, paths: list[str]) -> None:
        from app.core.notes_io import import_markdown
        ids = import_markdown(self.db, paths, self.index_note)
        self.data_changed.emit("notes")
        self.notify(tr("notes_imported", n=len(ids)), "success" if ids else "warning")

    def export_notes_markdown(self, target: str, notes: list[dict] | None = None) -> None:
        from app.core.notes_io import export_markdown
        notes = notes if notes is not None else self.db.list_notes("all")
        count = export_markdown(self.db, notes, Path(target), self.note_content)
        skipped = len(notes) - count
        self.notify(tr("notes_exported", n=count) + (" " + tr("locked_skipped", n=skipped) if skipped else ""),
                    "success")

    def set_note_task(self, note_id: int, line: int) -> None:
        """Ticks / unticks a "- [ ]" line of a note (Tasks view)."""
        from app.core.md_render import toggle_task
        note = self.db.get_note(note_id)
        if not note or note["locked"]:
            return
        text = toggle_task(note["content"], line)
        if text is not None:
            self.autosave_note(note_id, note["title"], text)

    def update_note_fields(self, note_id: int, **fields) -> None:
        self.db.update_note(note_id, **fields)
        self.data_changed.emit("notes")

    # ================================================================== files
    def use_builtin_explorer(self) -> bool:
        return bool(self.db.get_setting("builtin_explorer", True))

    def open_path(self, path: str) -> None:
        """Folders open in Study Hub Explorer (unless turned off), files in their usual program."""
        if Path(path).is_dir() and self.use_builtin_explorer():
            self.open_explorer(path)
        elif not open_target("folder" if Path(path).is_dir() else "file", path):
            self.notify(tr("error_path_missing"), "error")

    def reveal_path(self, path: str) -> None:
        if self.use_builtin_explorer() and os.path.exists(path):
            self.open_explorer(os.path.dirname(path), select=path)
        else:
            reveal_in_explorer(path)

    def open_url(self, url: str) -> None:
        winapi.shell_execute(normalize_url(url) or url)

    def open_explorer(self, path: str | None = None, select: str | None = None) -> None:
        from app.ui.explorer import ExplorerWindow
        window = ExplorerWindow(self, path, select)
        self._explorers = [w for w in getattr(self, "_explorers", []) if not _deleted(w)] + [window]
        window.show()
        window.raise_()
        window.activateWindow()

    # ----------------------------------------------------------- live windows
    def new_widget_dialog(self, wtype: str, desk: str = "all", cell=None, parent=None) -> None:
        from PyQt6.QtWidgets import QFileDialog
        from app.ui.dialogs.base import PromptDialog
        if wtype == "web":
            url = PromptDialog.ask(tr("widget_web"), placeholder="https://…", parent=parent or self._dialog_parent())
            if url and normalize_url(url):
                self.pin_widget("web", normalize_url(url), desk, cell)
            elif url:
                self.notify(tr("error_invalid_url"), "error")
        elif wtype == "pdf":
            path, _ = QFileDialog.getOpenFileName(parent or self._dialog_parent(), tr("widget_pdf"), "", "PDF (*.pdf)")
            if path:
                self.pin_widget("pdf", os.path.normpath(path), desk, cell)
        elif wtype == "todo":
            self.pin_widget("todo", "", desk, cell)
        else:  # system / focus / classes / note / image / links: pin it, then set it up
            item_id = self.pin_widget(wtype, "", desk, cell)
            if wtype in ("note", "image", "links", "system"):
                QTimer.singleShot(350, lambda: self.edit_widget(item_id, desk))

    def edit_widget(self, item_id: int, desk: str = "all", parent=None) -> None:
        from app.ui.dialogs.widget_edit import WidgetEditDialog
        item = self.db.get_item(item_id)
        if item:
            WidgetEditDialog(self, item, desk, parent or self._dialog_parent()).exec()

    def pin_widget(self, wtype: str, target: str, desk: str = "all", cell=None) -> int:
        titles = {"web": title_from_target(target, "url") if target else "", "pdf": Path(target).stem if target else "",
                  "todo": tr("tasks_title")}
        titles.update({t: tr(f"widget_{t}") for t in ("system", "focus", "classes", "note", "image", "links")})
        group_id = None if desk == "all" else int(desk)
        item_id = self._new_item("widget", titles.get(wtype) or wtype, target, group_id, args=wtype,
                                 cell=(desk, cell))
        self.data_changed.emit("launcher")
        if not self.window.isVisible():
            self.show_window()
        self.notify(tr("window_pinned"), "success")
        return item_id

    def open_as_widget(self, item_id: int, desk: str = "all") -> None:
        item = self.db.get_item(item_id)
        if item:
            self.pin_widget("web" if item["kind"] == "url" else "pdf", item["target"], desk)

    def _edit_widget(self, item: dict, parent=None) -> None:
        from PyQt6.QtWidgets import QFileDialog
        from app.ui.dialogs.base import PromptDialog
        parent = parent or self._dialog_parent()
        wtype = item["args"]
        if wtype == "web":
            url = PromptDialog.ask(tr("widget_web"), item["target"], "https://…", parent)
            if url and normalize_url(url):
                self.db.update_item(item["id"], target=normalize_url(url))
            elif url:
                return self.notify(tr("error_invalid_url"), "error")
        elif wtype == "pdf":
            path, _ = QFileDialog.getOpenFileName(parent, tr("widget_pdf"), item["target"], "PDF (*.pdf)")
            if path:
                self.db.update_item(item["id"], target=os.path.normpath(path), title=Path(path).stem)
        else:
            return self.rename_item_dialog(item["id"], parent)
        self.data_changed.emit("launcher")

    def rename_item_dialog(self, item_id: int, parent=None) -> None:
        from app.ui.dialogs.base import PromptDialog
        item = self.db.get_item(item_id)
        if item:
            name = PromptDialog.ask(tr("rename"), item["title"], parent=parent or self._dialog_parent())
            if name:
                self.rename_item(item_id, name)

    # ---------------------------------------------------------------- archive
    def archive_root(self) -> Path:
        from app.core.archive import default_root
        return Path(self.db.get_setting("archive_root", "") or default_root())

    def choose_archive_root(self, parent=None) -> None:
        from PyQt6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(parent or self._dialog_parent(), tr("change_folder"),
                                                str(self.archive_root()))
        if path:
            self.db.set_setting("archive_root", os.path.normpath(path))
            self.data_changed.emit("archive")

    def current_semester(self) -> str:
        from app.core.archive import auto_semester
        return self.db.get_setting("semester", "") or auto_semester()

    def archive_dialog(self, paths: list[str], parent=None) -> None:
        from app.core import archive
        from app.ui.dialogs.archive_dialog import ArchiveDialog
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return
        tracked = self.db.tracked_by_path(paths[0])
        dlg = ArchiveDialog(paths, self.db.list_subjects(), self.current_semester(), str(self.archive_root()),
                            tracked["subject_id"] if tracked else None, parent or self._dialog_parent())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        d = dlg.result_data()
        try:
            ids = archive.archive_paths(self.db, paths, self.archive_root(), d["subject_id"], d["subject_name"],
                                        d["category"], d["semester"] or self.current_semester(), d["mode"], d["note"])
        except OSError as exc:
            self.notify(f"{tr('error')}: {exc}", "error")
            log.error("Archive failed: %s", exc)
            return
        self.data_changed.emit("archive")
        self.data_changed.emit("files")
        self.notify(tr("archived_n", n=len(ids)), "success")

    def restore_archived(self, entry_id: int) -> None:
        from app.core import archive
        entry = self.db.get_archive_entry(entry_id)
        if not entry:
            return
        try:
            dst = archive.restore(self.db, entry)
        except OSError as exc:
            self.notify(f"{tr('error')}: {exc}", "error")
            return
        self.data_changed.emit("archive")
        self.notify(tr("restored_to", path=os.path.dirname(dst)), "success")

    def forget_archived(self, entry_id: int) -> None:
        """Removes the record only — the archived file itself stays where it is."""
        self.db.delete_archive_entry(entry_id)
        self.data_changed.emit("archive")

    def stale_files(self) -> list[dict]:
        from app.core.archive import stale_files
        folders = [f for s in self.db.list_subjects() for f in self.subject_folders(s["id"]) if os.path.isdir(f)]
        return stale_files(folders, int(self.db.get_setting("stale_months", 6)))

    def open_files_tab(self, tab: str) -> None:
        page = self.apps.open("files")
        if page is not None:
            page.show_tab(tab)

    def subject_folders(self, subject_id: int) -> list[str]:
        """Main folder of the subject first, then its extra linked folders."""
        subject = self.db.get_subject(subject_id)
        folders = [subject["folder_path"]] if subject and subject["folder_path"] else []
        folders += [f["path"] for f in self.db.list_linked_folders(subject_id)
                    if os.path.normcase(f["path"]) not in {os.path.normcase(x) for x in folders}]
        return folders

    def destination_choices(self) -> list[tuple[int, str, str, str]]:
        """(subject_id, subject name, color, folder) for every existing subject folder."""
        return [(s["id"], s["name"], s["color"], folder) for s in self.db.list_subjects()
                for folder in self.subject_folders(s["id"]) if os.path.isdir(folder)]

    def link_folder(self, subject_id: int, parent=None, path: str | None = None) -> None:
        from PyQt6.QtWidgets import QFileDialog
        if path is None:
            path = QFileDialog.getExistingDirectory(parent or self._dialog_parent(), tr("link_folder"))
            if not path:
                return
        path = os.path.normpath(path)
        subject = self.db.get_subject(subject_id)
        if subject and not subject["folder_path"]:
            self.db.update_subject(subject_id, folder_path=path)  # first folder becomes the main one
        else:
            self.db.add_linked_folder(subject_id, path)
        self.data_changed.emit("folders")
        self.notify(tr("folder_linked", name=Path(path).name), "success")

    def unlink_folder(self, subject_id: int, path: str) -> None:
        subject = self.db.get_subject(subject_id)
        if subject and os.path.normcase(subject["folder_path"] or "") == os.path.normcase(path):
            extra = self.db.list_linked_folders(subject_id)
            self.db.update_subject(subject_id, folder_path=extra[0]["path"] if extra else "")
            if extra:
                self.db.remove_linked_folder(subject_id, extra[0]["path"])
        else:
            self.db.remove_linked_folder(subject_id, path)
        self.data_changed.emit("folders")

    def copy_into(self, paths: list[str], folder: str) -> None:
        import shutil
        copied = 0
        for p in paths:
            try:
                if os.path.isdir(p):
                    shutil.copytree(p, fs.unique_destination(folder, Path(p).name))
                else:
                    shutil.copy2(p, fs.unique_destination(folder, Path(p).name))
                copied += 1
            except OSError as exc:
                self.notify(f"{tr('error')}: {exc}", "error")
        if copied:
            self.notify(tr("copied_n", n=copied), "success")

    # ---------------------------------------------------------------- tags
    def tag_paths(self, paths: list[str], parent=None, preselect: list[int] | None = None) -> None:
        from app.ui.dialogs.tag_dialog import TagFileDialog
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return
        current = self.db.tracked_by_path(paths[0]) if len(paths) == 1 else None
        selected = set(self.db.file_tag_ids(current["id"])) if current else set(preselect or [])
        subject_id = current["subject_id"] if current else None
        dlg = TagFileDialog(paths, self.db.list_tags(), self.db.list_subjects(), selected, subject_id,
                            parent or self._dialog_parent())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.result_data()
        tag_ids = data["tag_ids"] + [self.db.add_tag(name, color) for name, color in data["new_tags"]]
        for p in paths:
            file_id = self.db.track_file(p, data["subject_id"])
            self.db.update_tracked(file_id, subject_id=data["subject_id"])
            if len(paths) == 1:
                self.db.set_file_tags(file_id, tag_ids)
            else:  # several files: add the chosen tags, keep the ones they already had
                self.db.set_file_tags(file_id, sorted(set(self.db.file_tag_ids(file_id)) | set(tag_ids)))
        self.data_changed.emit("files")
        self.notify(tr("tagged_n", n=len(paths)), "success")

    def untrack_paths(self, paths: list[str]) -> None:
        for p in paths:
            row = self.db.tracked_by_path(p)
            if row:
                self.db.untrack_file(row["id"])
        self.data_changed.emit("files")

    def new_tag(self, parent=None) -> None:
        from app.ui.dialogs.base import PromptDialog
        from app.ui.widgets.files import TAG_COLORS
        name = PromptDialog.ask(tr("new_tag"), placeholder=tr("new_tag_ph"), parent=parent or self._dialog_parent())
        if name:
            self.db.add_tag(name, TAG_COLORS[len(self.db.list_tags()) % len(TAG_COLORS)])
            self.data_changed.emit("tags")

    def rename_tag(self, tag_id: int, parent=None) -> None:
        from app.ui.dialogs.base import PromptDialog
        tag = next((t for t in self.db.list_tags() if t["id"] == tag_id), None)
        if tag:
            name = PromptDialog.ask(tr("rename"), tag["name"], parent=parent or self._dialog_parent())
            if name:
                self.db.update_tag(tag_id, name=name)
                self.data_changed.emit("tags")

    def recolor_tag(self, tag_id: int) -> None:
        from app.ui.widgets.files import TAG_COLORS
        tag = next((t for t in self.db.list_tags() if t["id"] == tag_id), None)
        if tag:
            index = TAG_COLORS.index(tag["color"]) + 1 if tag["color"] in TAG_COLORS else 0
            self.db.update_tag(tag_id, color=TAG_COLORS[index % len(TAG_COLORS)])
            self.data_changed.emit("tags")

    def delete_tag(self, tag_id: int, parent=None) -> None:
        from app.ui.dialogs.base import ConfirmDialog
        if ConfirmDialog.ask(tr("delete_tag"), tr("delete_tag_msg"), tr("delete"), parent or self._dialog_parent()):
            self.db.delete_tag(tag_id)
            self.data_changed.emit("tags")

    # --------------------------------------------------- downloads organizer
    def organizer_folder(self) -> str:
        return self.db.get_setting("organizer_folder", "") or fs.downloads_dir()

    def choose_organizer_folder(self, parent=None) -> None:
        from PyQt6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(parent or self._dialog_parent(), tr("change_folder"),
                                                self.organizer_folder())
        if path:
            self.db.set_setting("organizer_folder", os.path.normpath(path))
            self.set_watch_downloads(self.db.get_setting("watch_downloads", True), save=False)
            self.data_changed.emit("organizer")

    def organizer_suggestions(self) -> list:
        files = organizer.candidate_files(self.organizer_folder(), self.db.ignored_paths())
        destinations = {s["id"]: [f for f in self.subject_folders(s["id"]) if os.path.isdir(f)]
                        for s in self.db.list_subjects()}
        result = organizer.suggest(files, self.db.list_subjects(), destinations, self.db.organizer_rules())
        self.downloads_changed.emit(sum(1 for s in result if s.subject_id is not None))  # badge only
        return result

    def _move_one(self, src: str, subject_id: int, folder: str) -> str | None:
        try:
            dst = fs.move_file(src, folder)
        except OSError as exc:
            self.notify(f"{tr('error')}: {exc}", "error")
            return None
        self.db.record_move(src, dst)
        self.db.rename_tracked_path(src, dst)
        self.db.learn_tokens(organizer.tokens(Path(src).stem), subject_id)  # gets smarter with every move
        return dst

    def organize(self, src: str, subject_id: int, folder: str) -> None:
        dst = self._move_one(src, subject_id, folder)
        if dst:
            self.data_changed.emit("organizer")
            self.notify(tr("moved_to", name=Path(dst).name, folder=Path(folder).name), "success")

    def organize_many(self, moves: list[tuple[str, int, str]]) -> None:
        done = sum(1 for src, sid, folder in moves if self._move_one(src, sid, folder))
        self.data_changed.emit("organizer")
        if done:
            self.notify(tr("moved_n", n=done), "success")

    def undo_last_move(self) -> None:
        move = self.db.last_move()
        if not move:
            return
        if not os.path.exists(move["dst"]):
            self.db.delete_move(move["id"])
            self.notify(tr("undo_missing"), "warning")
            return
        try:
            restored = fs.move_file(move["dst"], str(Path(move["src"]).parent))
        except OSError as exc:
            self.notify(f"{tr('error')}: {exc}", "error")
            return
        self.db.delete_move(move["id"])
        self.db.rename_tracked_path(move["dst"], restored)
        self.data_changed.emit("organizer")
        self.notify(tr("move_undone", name=Path(restored).name), "info")

    def ignore_download(self, path: str) -> None:
        self.db.ignore_path(path)
        self.data_changed.emit("organizer")

    def set_watch_downloads(self, on: bool, save: bool = True) -> None:
        if save:
            self.db.set_setting("watch_downloads", on)
        dirs = self._downloads_watcher.directories()
        if dirs:
            self._downloads_watcher.removePaths(dirs)
        folder = self.organizer_folder()
        # remember what is already there, so only *new* downloads trigger a notification
        self._seen_downloads = {f["path"].lower() for f in organizer.candidate_files(folder, set())}
        if on and os.path.isdir(folder):
            self._downloads_watcher.addPath(folder)

    def _check_downloads(self) -> None:
        current = organizer.candidate_files(self.organizer_folder(), self.db.ignored_paths())
        new = [f for f in current if f["path"].lower() not in self._seen_downloads]
        self._seen_downloads |= {f["path"].lower() for f in current}
        if new:
            self.organizer_suggestions()      # updates the tab badge
            self.data_changed.emit("organizer")  # refreshes the list if it is open
            name = new[0]["name"] if len(new) == 1 else ""
            self.notify(tr("new_download", name=name) if name else tr("new_downloads", n=len(new)), "info")

    # ================================================================= clock
    def set_clock_format(self, twelve: bool) -> None:
        self.db.set_setting("clock_12h", twelve)
        i18n.set_clock_format(twelve)
        self.data_changed.emit("settings")

    @staticmethod
    def duration_text(days: int, hours: int, minutes: int) -> str:
        parts = []
        if days:
            parts.append(tr("dur_days", n=days))
        if hours:
            parts.append(tr("dur_hours", n=hours))
        if minutes or not parts:
            parts.append(tr("dur_minutes", n=minutes))
        return " ".join(parts)

    def next_alarm(self) -> tuple[dict, datetime] | None:
        upcoming = [(a, next_occurrence(a)) for a in self.db.list_alarms()]
        upcoming = [(a, at) for a, at in upcoming if at]
        return min(upcoming, key=lambda x: x[1]) if upcoming else None

    def add_alarm_dialog(self, parent=None) -> None:
        from app.ui.dialogs.alarm_dialog import AlarmDialog
        dlg = AlarmDialog(self, parent=parent or self._dialog_parent())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.add_alarm(**dlg.result_data())
            self.data_changed.emit("alarms")
            self._notify_next_alarm()

    def edit_alarm(self, alarm_id: int, parent=None) -> None:
        from app.ui.dialogs.alarm_dialog import AlarmDialog
        alarm = self.db.get_alarm(alarm_id)
        if not alarm:
            return
        dlg = AlarmDialog(self, alarm, parent=parent or self._dialog_parent())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.db.update_alarm(alarm_id, **dlg.result_data(), last_fired=None)
            self.data_changed.emit("alarms")
            self._notify_next_alarm()

    def _notify_next_alarm(self) -> None:
        nxt = self.next_alarm()
        if nxt:
            d, h, m = until_text(nxt[1])
            self.notify(tr("rings_in", text=self.duration_text(d, h, m)), "success")

    def set_alarm_enabled(self, alarm_id: int, on: bool) -> None:
        self.db.update_alarm(alarm_id, enabled=int(on), last_fired=None)
        if not on:
            self.alarms.cancel_snooze(alarm_id)
        self.data_changed.emit("alarms")
        if on:
            self._notify_next_alarm()

    def duplicate_alarm(self, alarm_id: int) -> None:
        alarm = self.db.get_alarm(alarm_id)
        if alarm:
            self.db.add_alarm(**alarm)
            self.data_changed.emit("alarms")

    def delete_alarm(self, alarm_id: int) -> None:
        self.db.delete_alarm(alarm_id)
        self.alarms.cancel_snooze(alarm_id)
        self.data_changed.emit("alarms")

    def ring_alarm(self, alarm: dict, snooze_count: int) -> None:
        """Full-screen alarm + sound (works even when Study Hub is hidden)."""
        if not alarm:
            return
        from app.ui.alarm_ring import AlarmRingWindow
        log.info("Alarm ringing: %s %s", alarm["time"], alarm["label"])
        window = AlarmRingWindow(alarm, snooze_count)
        window.snoozed.connect(self._on_snooze)
        window.dismissed.connect(self._on_dismiss)
        window.destroyed.connect(lambda *_: self._ring_windows.remove(window) if window in self._ring_windows else None)
        self._ring_windows.append(window)
        self.sound.play(alarm["sound"], int(alarm["volume"]), bool(alarm["gradual"]))
        window.ring()

    def _on_snooze(self, alarm: dict, count: int) -> None:
        at = self.alarms.snooze(alarm, count)
        self.sound.stop()
        self.data_changed.emit("alarms")
        self.notify(tr("snoozed_until", time=i18n.format_hm(at.hour, at.minute)), "info")

    def _on_dismiss(self, alarm: dict) -> None:
        self.sound.stop()
        self.data_changed.emit("alarms")

    def _class_reminder(self, slot: dict, minutes: int) -> None:
        self.sound.preview("chime", 70)
        text = tr("class_reminder", name=slot["subject_name"], n=minutes)
        if slot.get("location"):
            text += f" · {slot['location']}"
        self.tray.showMessage(APP_NAME, text, self.icon, 8000)
        if self.window.isVisible():
            self.window.toast(text, "info")

    # ----------------------------------------------------------- lock screen
    def lock_options(self) -> dict:
        from app.ui.lock_screen import DEFAULT_LOCK
        return {**DEFAULT_LOCK, **(self.db.get_setting("lock_screen", {}) or {})}

    def save_lock_options(self, opts: dict) -> None:
        self.db.set_setting("lock_screen", opts)

    def lock_state(self) -> dict:
        """Everything the lock screen shows, refreshed a few times per second."""
        now = datetime.now()
        state = {"now": now, "tasks": self.db.count_open_tasks_today(), "battery": winapi.battery()}
        nxt = self.next_alarm()
        if nxt and (nxt[1] - now).total_seconds() < 24 * 3600:
            state["next_alarm"] = i18n.format_hm(nxt[1].hour, nxt[1].minute) + (
                f" · {nxt[0]['label']}" if nxt[0]["label"] else "")
        classes = self.db.upcoming_classes(now)
        if classes:
            state["next_class"] = f"{classes[0]['subject_name']} · {i18n.format_hhmm(classes[0]['start_time'])}"
        if self.pomodoro.state != "idle":
            m, s = divmod(self.pomodoro.remaining, 60)
            state["pomodoro"] = f"{tr('phase_' + self.pomodoro.phase)} {m:02d}:{s:02d}"
        if not getattr(self, "_lock_quote", None) or not self._lock_windows:
            q = self.db.random_quote()
            self._lock_quote = (q["text_ar"] if i18n.language() == "ar" else q["text_en"]) if q else ""
        state["quote"] = self._lock_quote
        return state

    def show_lock_screen(self) -> None:
        if self._lock_windows:
            return
        if self.vault.unlocked:  # stepping away from the PC: close the vault too
            self.lock_vault(quiet=True)
        from app.ui.lock_screen import LockScreen
        opts = self.lock_options()
        self._lock_quote = None
        for screen in QApplication.screens():
            window = LockScreen(screen, self.lock_state, opts)
            window.closed.connect(lambda w=window: self._lock_closed(w, opts))
            self._lock_windows.append(window)
            window.show_animated()

    def _lock_closed(self, window, opts: dict) -> None:
        if window in self._lock_windows:
            self._lock_windows.remove(window)
        for other in list(self._lock_windows):  # leaving one screen closes all of them
            other.dismiss()
        if not self._lock_windows and opts.get("lock_windows"):
            winapi.lock_workstation()

    def _check_idle(self) -> None:
        minutes = int(self.lock_options().get("idle_min", 0) or 0)
        if (minutes and not self._lock_windows and not self._ring_windows
                and winapi.idle_seconds() >= minutes * 60 and not winapi.foreground_is_fullscreen()):
            self.show_lock_screen()

    # ================================================================== vault
    def vault_changed(self) -> None:
        self.vault_activity()
        self.data_changed.emit("vault")

    def vault_activity(self) -> None:
        self._vault_activity = time.monotonic()

    def lock_vault(self, quiet: bool = False) -> None:
        self.vault.lock()
        self._clear_secret_clipboard()
        self.data_changed.emit("vault")
        if not quiet:
            self.notify(tr("vault_locked_toast"), "info")

    def _vault_autolock(self) -> None:
        minutes = int(self.db.get_setting("vault_autolock", 5) or 0)
        if self.vault.unlocked and minutes and time.monotonic() - self._vault_activity >= minutes * 60:
            log.info("Vault auto-locked after %s min", minutes)
            self.lock_vault()

    def copy_secret(self, text: str, entry_id: int | None = None, seconds: int = 20) -> None:
        """Copies a password so that Windows' clipboard history (Win+V) and cloud clipboard skip it,
        then wipes it after `seconds` if it is still there."""
        from PyQt6.QtCore import QByteArray, QMimeData
        if not text:
            return
        md = QMimeData()
        md.setText(text)
        zero = QByteArray(b"\x00\x00\x00\x00")
        for fmt in ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory",
                    "CanUploadToCloudClipboard"):
            md.setData(f'application/x-qt-windows-mime;value="{fmt}"', zero)
        QApplication.clipboard().setMimeData(md)
        self._clip_secret = text
        QTimer.singleShot(seconds * 1000, self._clear_secret_clipboard)
        if entry_id:
            self.vault.touch(entry_id)
        self.vault_activity()
        self.notify(tr("secret_copied", n=seconds), "success")

    def _clear_secret_clipboard(self) -> None:
        if self._clip_secret and QApplication.clipboard().text() == self._clip_secret:
            QApplication.clipboard().clear()
        self._clip_secret = None

    def new_login(self, parent=None) -> None:
        from app.ui.dialogs.vault_dialogs import EntryDialog
        dlg = EntryDialog(self, parent=parent or self._dialog_parent())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.vault.add(**dlg.result_data())
            self.vault_changed()

    def edit_login(self, entry_id: int, parent=None) -> None:
        from app.ui.dialogs.vault_dialogs import EntryDialog
        entry = self.vault.get(entry_id)
        if not entry:
            return
        dlg = EntryDialog(self, entry, parent=parent or self._dialog_parent())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.vault.update(entry_id, **dlg.result_data())
            self.vault_changed()

    def delete_login(self, entry_id: int, parent=None) -> None:
        from app.ui.dialogs.base import ConfirmDialog
        entry = self.vault.get(entry_id)
        if entry and ConfirmDialog.ask(tr("delete_login"), tr("delete_login_msg", name=entry["title"]), tr("delete"),
                                       parent or self._dialog_parent()):
            self.vault.delete(entry_id)
            self.vault_changed()

    def set_login_favorite(self, entry_id: int, on: bool) -> None:
        self.vault.set_favorite(entry_id, on)
        self.vault_changed()

    def open_login(self, entry_id: int) -> None:
        """Opens the website and puts the password on the clipboard (auto-cleared)."""
        entry = self.vault.get(entry_id)
        if not entry:
            return
        if entry["url"]:
            self.open_url(entry["url"])
        self.copy_secret(entry["password"], entry_id)

    def open_generator(self, parent=None) -> None:
        from app.ui.dialogs.vault_dialogs import GeneratorDialog
        GeneratorDialog(self, parent=parent or self._dialog_parent()).exec()

    def import_chrome(self, parent=None) -> None:
        from app.core.vault import secure_delete
        from app.ui.dialogs.vault_dialogs import ImportDialog
        dlg = ImportDialog(parent or self._dialog_parent())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        path = dlg.path.text()
        try:
            added, skipped = self.vault.import_csv(path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self.notify(f"{tr('error')}: {exc}", "error")
            return
        wiped = False
        if dlg.wipe.isChecked():
            try:
                secure_delete(path)
                wiped = True
            except OSError as exc:
                log.error("Could not wipe CSV: %s", exc)
        self.vault_changed()
        self.notify(tr("imported_logins", n=added, skipped=skipped) + ("  " + tr("csv_wiped") if wiped else ""),
                    "success")

    def change_master(self, parent=None) -> None:
        from app.ui.dialogs.vault_dialogs import ChangeMasterDialog
        dlg = ChangeMasterDialog(parent or self._dialog_parent())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                ok = self.vault.change_master(dlg.old.text(), dlg.new.text())
            finally:
                QApplication.restoreOverrideCursor()
            self.vault_changed()
            self.notify(tr("master_changed") if ok else tr("wrong_master"), "success" if ok else "error")

    def export_vault(self, parent=None) -> None:
        """Plain-text export (to move to another PC / manager) — asks for the master password again."""
        from PyQt6.QtWidgets import QFileDialog
        from app.ui.dialogs.base import PromptDialog
        parent = parent or self._dialog_parent()
        dlg = PromptDialog(tr("export_csv"), placeholder=tr("master_password"), parent=parent)
        dlg.edit.setEchoMode(dlg.edit.EchoMode.Password)
        dlg.body.insertWidget(1, label_widget(tr("export_warning")))
        if dlg.exec() != QDialog.DialogCode.Accepted or not self.vault.unlock(dlg.value()):
            if dlg.result():
                self.notify(tr("wrong_master"), "error")
            self.vault_changed()
            return
        path, _ = QFileDialog.getSaveFileName(parent, tr("export_csv"), "StudyHub-passwords.csv", "CSV (*.csv)")
        if path:
            Path(path).write_text(self.vault.export_csv(), encoding="utf-8")
            self.notify(tr("exported_logins"), "warning")

    def _palette_new_note(self) -> None:
        self.open_notebook()
        page = self.apps.page("notes")
        if page is not None:
            page.new_note()

    # ================================================================ search
    def command_list(self) -> list[tuple]:
        """Every command of Study Hub as (glyph, key, action): the palette, the quick actions panel, the taskbar's
        jump lists and `StudyHub.exe --do KEY` all use this one list."""
        commands = [
            (Icons.NOTES, "quick_note", lambda: self.open_quick_note()),
            (Icons.EDIT, "new_note", self._palette_new_note),
            (Icons.CHECKLIST, "new_task", lambda: self.add_task_dialog(date.today().isoformat())),
            (Icons.TIMER, "start_focus", lambda: (self.open_study_tab("focus"), self.pomodoro.start())),
            (Icons.BOOK, "add_subject", lambda: self.add_subject_dialog()),
            (Icons.STUDY, "add_class", lambda: self.add_slot_dialog()),
            (Icons.DOWNLOAD, "tab_organizer", lambda: self.open_files_tab("organizer")),
            (Icons.TAG, "tags", lambda: self.open_files_tab("tags")),
            (Icons.PASTE, "note_from_clipboard", self.clipboard_note),
            (Icons.LINK, "link_from_clipboard", self.clipboard_link),
            (Icons.APPS, "add_app", lambda: self.add_item_dialog("app")),
            (Icons.GLOBE, "add_website", lambda: self.add_item_dialog("url")),
            (Icons.FOLDER, "add_folder", lambda: self.add_item_dialog("folder")),
            (Icons.FULLSCREEN, "toggle_fullscreen", lambda: self.window.toggle_fullscreen()),
            (Icons.GLOBE, "switch_language", lambda: self.set_language("en" if i18n.is_rtl() else "ar")),
            (Icons.SAVE, "backup_now", self.backup_now),
            (Icons.HOME, "nav_home", lambda: self.window.go_to("home")),
            (Icons.DESKTOP, "nav_desktop", lambda: self.window.go_to("desktop")),
            (Icons.BELL, "new_alarm", lambda: self.add_alarm_dialog()),
            (Icons.FOLDER, "explorer", lambda: self.open_explorer()),
            (Icons.KEY, "nav_vault", lambda: self.show_window("vault")),
            (Icons.PHONE, "nav_phone", lambda: self.open_phone_page()),
            (Icons.PASTE, "hk_clipboard_history", self.open_clipboard_history),
            (Icons.PREVIEW, "hk_screenshot", self.take_screenshot),
            (Icons.CHECKLIST, "lab_start", lambda: self.lab.start()),
            (Icons.OPEN_FOLDER, "pdf_open_task", lambda: self.open_pdf().choose_file()),
            (Icons.SEARCH, "pdf_library", lambda: self.open_pdf_library()),
            (Icons.PHONE, "handoff_clipboard", lambda: self.phone_hub.open_on_phone(QApplication.clipboard().text())),
            (Icons.ADD, "add_to_hub", lambda: self.show_add_gallery()),
            (Icons.KEYBOARD, "app_terminal", lambda: self.open_terminal()),
            (Icons.PASTE, "snip_paste", lambda: self.snippet_popup().open()),
            (Icons.TIMER, "app_timer", lambda: self.apps.open("timer")),
            (Icons.APPS, "start_menu", lambda: (self.show_window(), self.window.toggle_start())),
            (Icons.DOCUMENT, "pdf_app_name", lambda: self.open_pdf()),
            (Icons.DRIVE, "nav_storage", lambda: self.show_window("storage")),
            (Icons.CALENDAR_DAY, "daily_note", lambda: self.open_notes_action("daily")),
            (Icons.SEARCH, "quick_switcher", lambda: self.open_notes_action("switch")),
            (Icons.FORWARD, "phone_send_clipboard", self.send_clipboard_to_phone),
            (Icons.DOCUMENT, "phone_send_files", lambda: self.send_files_to_phone_dialog()),
            (Icons.LIGHTNING, "generator", lambda: self.open_generator()),
            (Icons.LOCK, "lock_now", lambda: self.lock_vault()),
            (Icons.ARCHIVE, "tab_archive", lambda: self.open_files_tab("archive")),
            (Icons.GLOBE, "widget_web", lambda: (self.window.go_to("desktop"), self.new_widget_dialog("web"))),
            (Icons.CHECKLIST, "widget_todo", lambda: self.pin_widget("todo", "")),
            (Icons.LOCK, "hk_lock_screen", self.show_lock_screen),
            (Icons.CLOCK, "nav_clock", lambda: self.window.go_to("clock")),
            (Icons.PASTE, "hk_paste_to_desktop", self.paste_hotkey),
            (Icons.NOTES, "sticky_note", lambda: (self.window.go_to("desktop"), self.new_sticky_note())),
            (Icons.STUDY, "nav_study", lambda: self.window.go_to("study")),
            (Icons.FOLDER, "nav_files", lambda: self.window.go_to("files")),
            (Icons.NOTES, "nav_notes", lambda: self.window.go_to("notes")),
            (Icons.SETTINGS, "nav_settings", lambda: self.window.go_to("settings")),
            (Icons.POWER, "quit", self.quit),
        ]
        commands += self.plugins.commands()
        return commands

    def run_command(self, key: str) -> bool:
        for _glyph, k, action in self.command_list():
            if k == key:
                action()
                return True
        return False

    def search(self, text: str) -> list[dict]:
        """Results for the command palette: items, notes, pages and actions."""
        from app.ui.widgets.tiles import item_pixmap
        q = text.lower()
        results = self._smart_results(text) if text.strip() else self._recent_results()
        for item in self.db.search_items(text, 6):
            results.append({"title": item["title"], "subtitle": item["target"], "kind": tr(f"kind_{item['kind']}"),
                            "pixmap": item_pixmap(item), "action": lambda i=item["id"]: self.launch_item(i)})
        if text:
            for note in self.db.search_notes(text, 4):
                preview = (note["title"] or note["content"]).strip().splitlines()
                results.append({"title": preview[0][:80] if preview else "…", "subtitle": note["updated_at"],
                                "kind": tr("note"), "glyph": Icons.NOTES, "color": "#FBBF24",
                                "action": lambda n=note["id"]: self.open_notebook(note_id=n)})
            if self.vault.unlocked:  # unlocked vault: type a site -> Enter copies its password
                for e in [e for e in self.vault.entries()
                          if q in f"{e['title']} {e['url']} {e['username']}".lower()][:4]:
                    results.append({"title": e["title"], "subtitle": e["username"], "kind": tr("copy_password"),
                                    "glyph": Icons.KEY, "color": "#FBBF24",
                                    "action": lambda eid=e["id"], pw=e["password"]: self.copy_secret(pw, eid)})
            from app.ui.widgets.files import file_icon
            for f in self.db.search_files(text, 4):
                results.append({"title": f["name"], "subtitle": f["path"], "kind": tr("file"),
                                "pixmap": file_icon(f["path"], os.path.isdir(f["path"]), 36),
                                "action": lambda p=f["path"]: self.open_path(p)})
        commands = self.command_list()
        usage = self.db.get_setting("palette_usage", {}) or {}
        commands = sorted(commands, key=lambda c: -(usage.get(f"cmd:{c[1]}", [0, 0])[0]))  # most used first
        seen = {r.get("id") for r in results}
        for glyph, key, action in commands:
            title = tr(key)
            if f"cmd:{key}" in seen:
                continue
            if not q or q in title.lower() or q in i18n.tr_in("en", key).lower():
                results.append({"title": title, "kind": tr("command"), "glyph": glyph, "id": f"cmd:{key}",
                                "color": Theme.accent(), "action": action})
        return results[:18]

    # ----------------------------------------------- smarter palette (app/core/smart_query.py)
    def snippet_popup(self):
        from app.ui.snippets import SnippetPopup
        if self._snippet_popup is None:
            self._snippet_popup = SnippetPopup(self)
        return self._snippet_popup

    def open_terminal(self, ssh_host: int | None = None, paste: str | None = None):
        return self.apps.open("terminal", ssh_host=ssh_host, paste=paste)

    def record_palette_use(self, rid: str) -> None:
        import time as _t
        usage = dict(self.db.get_setting("palette_usage", {}) or {})
        count, _last = usage.get(rid, [0, 0])
        usage[rid] = [count + 1, _t.time()]
        if len(usage) > 300:  # keep the ones used most recently
            usage = dict(sorted(usage.items(), key=lambda kv: -kv[1][1])[:250])
        self.db.set_setting("palette_usage", usage)

    def _recent_results(self) -> list[dict]:
        """Empty palette: what you used last (newest first)."""
        usage = self.db.get_setting("palette_usage", {}) or {}
        cmds = {f"cmd:{k}": (g, k, a) for g, k, a in self.command_list()}
        out = []
        for rid, _v in sorted(usage.items(), key=lambda kv: -kv[1][1]):
            if rid in cmds:
                g, k, a = cmds[rid]
                out.append({"title": tr(k), "kind": tr("pal_recent"), "glyph": g, "id": rid, "color": Theme.accent(),
                            "action": a})
            if len(out) >= 5:
                break
        return out

    def _copy_result(self, text: str) -> None:
        QApplication.clipboard().setText(str(text))
        self.notify(tr("pal_copied", text=str(text)[:60]), "success")

    def _smart_results(self, text: str) -> list[dict]:
        from app.core import smart_query
        out = []
        for snip in self.snippets.search(text)[:3]:  # snippets: Enter copies it
            out.append({"title": snip["title"], "subtitle": snip["code"].strip().split("\n")[0][:90],
                        "kind": tr("snip_kind"), "glyph": Icons.PASTE, "color": "#0E7490",
                        "action": lambda c=snip["code"], i=snip["id"]: (self.snippets.used(i), self._copy_result(c))})
        for m in smart_query.parse(text):
            t = m["type"]
            if t == "calc":
                out.append({"title": f"= {m['value']:,}" if isinstance(m["value"], int) else f"= {m['value']}",
                            "subtitle": m["expr"], "kind": tr("pal_calc"), "glyph": Icons.LIGHTNING, "color": "#22D3EE",
                            "action": lambda v=m["value"]: self._copy_result(v)})
            elif t in ("convert", "base", "subnet"):
                out.append({"title": m["value"], "kind": tr(f"pal_{t}"), "glyph": Icons.LIGHTNING, "color": "#22D3EE",
                            "action": lambda v=m["value"]: self._copy_result(v.split(" = ")[-1] if t != "subnet" else v)})
            elif t == "note":
                out.append({"title": tr("pal_save_note"), "subtitle": m["text"][:90], "kind": tr("note"),
                            "glyph": Icons.NOTES, "color": "#FBBF24",
                            "action": lambda s=m["text"]: (self.save_note(s, source="quick"),
                                                           self.notify(tr("pal_note_saved"), "success"))})
            elif t == "last_pdf":
                recent = self.recents.recent("pdf")
                if recent:
                    out.append({"title": tr("pal_open_last_pdf", name=recent[0]["title"]), "kind": "PDF",
                                "glyph": Icons.DOCUMENT, "color": "#D93036",
                                "action": lambda e=recent[0]: self.recents.open(e)})
            elif t == "folder":
                from PyQt6.QtCore import QStandardPaths as SP
                loc = {"download": SP.StandardLocation.DownloadLocation, "documents": SP.StandardLocation.DocumentsLocation,
                       "desktop": SP.StandardLocation.DesktopLocation, "pictures": SP.StandardLocation.PicturesLocation,
                       "music": SP.StandardLocation.MusicLocation, "movies": SP.StandardLocation.MoviesLocation}[m["which"]]
                path = SP.writableLocation(loc)
                out.append({"title": tr("pal_open_folder", name=Path(path).name), "subtitle": path, "kind": tr("kind_folder"),
                            "glyph": Icons.FOLDER, "color": "#FBBF24", "action": lambda p=path: self.open_folder(p)})
            elif t == "open":
                what = m["what"].lower()
                from app.ui import apps as app_registry
                for info in app_registry.all_apps(self):
                    if what in info.title.lower() or what in i18n.tr_in("en", info.title_key).lower() or what == info.key:
                        out.append({"title": tr("pal_open_app", name=info.title), "kind": tr("qa_kind_app"),
                                    "glyph": info.glyph, "color": info.color, "id": f"app:{info.key}",
                                    "action": lambda k=info.key: self.apps.open(k)})
                        break
            elif t == "ssh":
                hosts = []
                infra = getattr(self, "infra", None)
                if infra is not None:
                    hosts = [h for h in infra.store.hosts() if m["host"].lower() in (h["name"] + " " + h["address"]).lower()]
                for h in hosts[:3]:
                    out.append({"title": tr("pal_ssh", name=h["name"]), "subtitle": f"{h.get('username', '')}@{h['address']}",
                                "kind": "SSH", "glyph": Icons.KEYBOARD, "color": "#12A594",
                                "action": lambda hid=h["id"]: self.open_terminal(ssh_host=hid)})
        return out
