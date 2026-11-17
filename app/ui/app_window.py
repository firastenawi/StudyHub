"""
Apps in their own windows.

AppWindow   one Study Hub page (Notebook, Files, Settings…) in a real Windows window: own taskbar button and icon
            (its own AppUserModelID), resizable, snaps, remembers where it was. The data is the same database as
            everything else, so a change in one window shows up in all of them.
AppManager  ctl.apps — opens apps by key (also `StudyHub.exe --app KEY`), keeps one window per page app, and
            tells the shell's taskbar what is running / which one is active.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QMainWindow, QVBoxLayout, QWidget

from app.core import winapi
from app.core.i18n import tr
from app.core.logs import log
from app.ui import apps as app_registry
from app.ui.apps import AppInfo
from app.ui.widgets.background import AuroraBackground
from app.ui.widgets.common import Toast, label


def relaunch_command(key: str) -> str:
    """What a pinned taskbar button runs to open this app again."""
    from app.core.shell_integration import _launch
    parts = _launch() + ["--app", key]
    return " ".join(f'"{p}"' if " " in p else p for p in parts)


def icon_file(key: str) -> str:
    """The .ico the build writes for each app (icons\\<key>.ico next to the exe), if this copy has it."""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2] / "dist" / "StudyHub"
    path = base / "icons" / f"{key}.ico"
    return str(path) if path.is_file() else ""


def give_identity(window, info: AppInfo) -> None:
    """Own taskbar button + icon for this window (and what a pinned button starts)."""
    window.setWindowIcon(info.icon())
    ico = icon_file(info.key)
    winapi.set_window_app_id(window, info.app_id, relaunch_command(info.key), info.title,
                             f"{ico},0" if ico else "")
    winapi.dark_title_bar(window)


def place_window(window, key: str, ctl, default_size=(0.72, 0.78)) -> None:
    """Where the window was last time (if that screen is still there), otherwise centred and cascaded."""
    geo = (ctl.db.get_setting("app_geometry", {}) or {}).get(key)
    screens = [s.availableGeometry() for s in QGuiApplication.screens()]
    if geo:
        rect = QRect(*geo[:4])
        if any(s.intersects(rect) for s in screens):
            window.setGeometry(rect)
            if len(geo) > 4 and geo[4]:
                window.setWindowState(Qt.WindowState.WindowMaximized)
            return
    screen = (QGuiApplication.screenAt(QApplication.primaryScreen().geometry().center())
              or QApplication.primaryScreen()).availableGeometry()
    w, h = int(screen.width() * default_size[0]), int(screen.height() * default_size[1])
    offset = 28 * (len(ctl.apps.windows) % 6) if hasattr(ctl, "apps") else 0
    window.setGeometry(screen.x() + (screen.width() - w) // 2 + offset - 70,
                       screen.y() + (screen.height() - h) // 2 + offset - 50, w, h)


def remember_window(window, key: str, ctl) -> None:
    geo = dict(ctl.db.get_setting("app_geometry", {}) or {})
    r = window.normalGeometry() if window.isMaximized() else window.geometry()
    geo[key] = [r.x(), r.y(), r.width(), r.height(), bool(window.isMaximized())]
    ctl.db.set_setting("app_geometry", geo)


class AppWindow(QMainWindow):
    """A page as its own app. Pages talk to it the way they used to talk to the main window."""
    closed = pyqtSignal(str)

    def __init__(self, ctl, info: AppInfo, factory):
        super().__init__()
        self.ctl, self.info, self.factory = ctl, info, factory
        self.key = info.key
        self.page = None
        self.chrome: list[QWidget] = []  # hidden by the notebook's focus mode
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(880, 560)
        self.setWindowTitle(f"{info.title} — Study Hub")
        self.setWindowIcon(info.icon())
        self.build()
        place_window(self, self.key, ctl)
        self._identity_set = False
        for keys, slot in (("Ctrl+K", self.open_palette), ("Ctrl+Space", self.open_palette),
                           ("F11", self.toggle_fullscreen), ("Ctrl+W", self.close),
                           ("Ctrl+N", lambda: ctl.open_quick_note())):
            QShortcut(QKeySequence(keys), self, activated=slot)

    # ------------------------------------------------------------- building
    def build(self) -> None:
        """(Re)creates the page — also when the language or theme changes."""
        if self.page is not None:
            self.page.dispose()
        self.bg = AuroraBackground(animated=False, wallpaper=self.ctl.wallpaper())
        root = QVBoxLayout(self.bg)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        try:
            self.page = self.factory(self.ctl, self)
        except Exception:
            log.exception("app %s failed to build", self.key)
            self.page = None
        self.chrome = []
        toolbar = getattr(self.page, "toolbar", None)
        if toolbar is not None:  # the page's own row (title, tabs, New…) at the top of its window
            head = QWidget()
            head.setObjectName("AppHeader")
            hl = QHBoxLayout(head)
            hl.setContentsMargins(22, 10, 18, 4)
            hl.addWidget(toolbar, 1)
            toolbar.show()
            root.addWidget(head)
            self.chrome.append(head)
        if self.page is not None:
            root.addWidget(self.page, 1)
        else:
            root.addWidget(label("⚠  " + tr("error"), 12, QFont.Weight.DemiBold), 1, Qt.AlignmentFlag.AlignCenter)
        self.toaster = Toast(self.bg)
        from app.ui.command_palette import CommandPalette
        self.palette = CommandPalette(self.ctl, self.bg)  # Ctrl+K searches right here, in this window
        self.setCentralWidget(self.bg)
        if self.page is not None:
            QTimer.singleShot(60, self.page.on_shown)

    # -------------------------------------------- what pages expect from "main"
    def toast(self, text: str, kind: str = "info") -> None:
        self.toaster.show_message(text, kind)

    def go_to(self, key: str, animate: bool = True) -> None:
        self.ctl.apps.open(key)

    def open_palette(self, text: str = "") -> None:
        self.palette.open(text)

    @property
    def fullscreen(self) -> bool:
        return self.ctl.window.fullscreen

    def set_fullscreen(self, on: bool) -> None:
        self.ctl.window.set_fullscreen(on)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ---------------------------------------------------------------- events
    def showEvent(self, e):
        super().showEvent(e)
        if not self._identity_set:
            self._identity_set = True
            give_identity(self, self.info)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (e.Type.ActivationChange, e.Type.WindowStateChange):
            self.ctl.apps.changed.emit()

    def closeEvent(self, e):
        remember_window(self, self.key, self.ctl)
        if self.page is not None:
            self.page.dispose()
        self.closed.emit(self.key)
        super().closeEvent(e)


def page_factories() -> dict:
    """Built-in page apps (imported lazily: the theme and language must be set first)."""
    from app.ui.pages.clock_page import ClockPage
    from app.ui.pages.files.files_page import FilesPage
    from app.ui.pages.home_page import HomePage
    from app.ui.pages.notes_page import NotesPage
    from app.ui.pages.phone_page import PhonePage
    from app.ui.pages.settings_page import SettingsPage
    from app.ui.pages.storage_page import StoragePage
    from app.ui.pages.study.study_page import StudyPage
    from app.ui.pages.vault_page import VaultPage
    from app.ui.snippets import SnippetsPage
    return {"snippets": SnippetsPage, "today": HomePage, "study": StudyPage, "notes": NotesPage, "files": FilesPage, "clock": ClockPage,
            "vault": VaultPage, "phone": PhonePage, "storage": StoragePage, "settings": SettingsPage}


# old page keys (palette, tray, saved settings) → app keys
ALIASES = {"home": "today", "focus": "timer"}


class AppManager(QObject):
    changed = pyqtSignal()  # something opened, closed, got focus or was minimized

    def __init__(self, ctl):
        super().__init__(ctl)
        self.ctl = ctl
        self.windows: dict[str, AppWindow] = {}
        self.timer_window = None
        self.terminal_window = None
        QApplication.instance().focusWindowChanged.connect(lambda *_: self.changed.emit())

    # --------------------------------------------------------------- opening
    def info(self, key: str) -> AppInfo | None:
        return app_registry.find(self.ctl, ALIASES.get(key, key))

    def open(self, key: str, **kwargs):
        """Opens (or brings to the front) an app. Returns the page for page apps, else the window."""
        key = ALIASES.get(key, key)
        if key == "desktop":
            self.ctl.show_window()
            return None
        info = self.info(key)
        if info is None:
            log.warning("unknown app %s", key)
            return None
        if info.kind == "action":
            getattr(self.ctl, info.action)()
            return None
        if info.kind == "item":  # a pinned program / website / file
            self.ctl.launch_item(info.extra["item"]["id"])
            return None
        if info.kind == "pdf":
            return self.ctl.open_pdf(kwargs.get("path"))
        if info.kind == "lab":
            self.ctl.lab.start()
            return self.ctl.lab.window
        if info.kind == "timer":
            return self._open_timer()
        if info.kind == "terminal":
            return self._open_terminal(**kwargs)
        win = self.windows.get(key)
        if win is None:
            factory = page_factories().get(key) or app_registry.plugin_factory(self.ctl, key)
            if factory is None:
                return None
            win = AppWindow(self.ctl, info, factory)
            win.closed.connect(self._closed)
            self.windows[key] = win
        self.front(win)
        self.changed.emit()
        return win.page

    def _open_timer(self):
        from app.ui.timer_window import TimerWindow
        if self.timer_window is None:
            self.timer_window = TimerWindow(self.ctl)
            self.timer_window.destroyed.connect(lambda *_: self._timer_gone())
        self.front(self.timer_window)
        self.changed.emit()
        return self.timer_window

    def _open_terminal(self, ssh_host: int | None = None, paste: str | None = None):
        from app.ui.terminal_window import TerminalWindow, ssh_profile
        first = ssh_profile(self.ctl, ssh_host) if ssh_host else None
        if self.terminal_window is None:
            self.terminal_window = TerminalWindow(self.ctl, first)
            self.terminal_window.destroyed.connect(lambda *_: self._terminal_gone())
        elif first:
            self.terminal_window.new_tab(first)
        self.front(self.terminal_window)
        if paste:
            QTimer.singleShot(900, lambda t=paste: self.terminal_window and self.terminal_window.paste_text(t))
        self.changed.emit()
        return self.terminal_window

    def _terminal_gone(self):
        self.terminal_window = None
        self.changed.emit()

    def _timer_gone(self):
        self.timer_window = None
        self.changed.emit()

    def _closed(self, key: str) -> None:
        self.windows.pop(key, None)
        QTimer.singleShot(0, self.changed.emit)

    @staticmethod
    def front(win) -> None:
        if win.isMinimized():
            win.showNormal()
        win.show()
        win.raise_()
        win.activateWindow()
        wid = win.winId()
        if wid:
            winapi.force_foreground(int(wid))

    def page(self, key: str):
        win = self.windows.get(ALIASES.get(key, key))
        return win.page if win else None

    # ----------------------------------------------------------- taskbar API
    def running(self) -> dict[str, list]:
        """key → its open windows (PDF: one per file)."""
        out: dict[str, list] = {}
        for key, win in self.windows.items():
            out[key] = [win]
        if self.timer_window is not None:
            out["timer"] = [self.timer_window]
        if self.terminal_window is not None:
            out["terminal"] = [self.terminal_window]
        lab_win = getattr(getattr(self.ctl, "lab", None), "window", None)
        if lab_win is not None:
            out["lab"] = [lab_win]
        readers = getattr(self.ctl, "_pdf_readers", None)
        if readers is not None and readers.windows:
            out["pdf"] = list(readers.windows)
        return out

    def active_key(self) -> str | None:
        active = QApplication.activeWindow()
        for key, wins in self.running().items():
            if active in wins:
                return key
        return None

    def activate(self, key: str) -> None:
        """Taskbar click: open it, bring it up, or minimize it if it is already in front."""
        wins = self.running().get(key)
        if not wins:
            self.open(key)
            return
        win = wins[0]
        if len(wins) == 1 and QApplication.activeWindow() is win and not win.isMinimized():
            win.showMinimized()
        else:
            for w in wins:
                self.front(w)
        self.changed.emit()

    def close(self, key: str) -> None:
        for w in list(self.running().get(key, [])):
            w.close()

    # --------------------------------------------------- language / theme
    def rebuild_all(self) -> None:
        for win in list(self.windows.values()):
            win.setWindowTitle(f"{win.info.title} — Study Hub")
            win.build()
            winapi.dark_title_bar(win)
        if self.timer_window is not None:
            self.timer_window.build()
        self.changed.emit()
