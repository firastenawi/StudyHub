"""
The shell: a frameless full-screen window that behaves like a small OS.

  ┌──────────────────────────────────────────────────────────┐
  │                 Desktop (the whole screen)                 │
  │                     [floating desk bar]                    │
  ├──────────────────────────────────────────────────────────┤
  │ ⊞  🔍  ▦ │ pinned + running apps …            tray · clock │  ← Taskbar (Settings › Taskbar)
  └──────────────────────────────────────────────────────────┘
  + overlays: Start menu, command palette, toasts, boot splash

Every other feature is an app in its own window (app/ui/app_window.py) with its own Windows taskbar button
and Start-menu shortcut; this window only hosts the desktop, the taskbar and the Start menu.
"""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QMainWindow, QVBoxLayout, QWidget

from app.config import APP_NAME
from app.core import winapi
from app.core.i18n import tr
from app.ui.animations import quiesce
from app.ui.command_palette import CommandPalette
from app.ui.pages.desktop_page import DesktopPage
from app.ui.start_menu import StartMenu
from app.ui.theme import Icons
from app.ui.widgets.background import AuroraBackground
from app.ui.widgets.common import Toast, add_action, make_menu
from app.ui.widgets.splash import BootSplash
from app.ui.widgets.taskbar import Taskbar


class MainWindow(QMainWindow):
    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
                            | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setMinimumSize(1000, 640)
        self.fullscreen = bool(ctl.db.get_setting("fullscreen", True))
        self._intro_played = False
        self._tray_hint_shown = False
        self._bar_shown = True
        self._bar_anim: QPropertyAnimation | None = None
        self._bar_watch = QTimer(self, interval=250, timeout=self._watch_bar)
        self.build()
        self._install_shortcuts()

    # ------------------------------------------------------------------ build
    def build(self) -> None:
        """(Re)creates the whole shell — also used when the language, theme or taskbar settings change."""
        if getattr(self, "desktop", None) is not None:
            self.desktop.dispose()
        if self.centralWidget() is not None:
            quiesce(self.centralWidget())
        if self._bar_anim:
            self._bar_anim.stop()
            self._bar_anim = None
        self.bg = AuroraBackground(animated=self.ctl.db.get_setting("animated_background", True),
                                   wallpaper=self.ctl.desk_wallpaper())  # this desktop's own one (desk_changed)
        self._root = QVBoxLayout(self.bg)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)
        self.desktop = DesktopPage(self.ctl, self)
        self.taskbar = Taskbar(self.ctl, parent=self.bg)
        self.taskbar.start_requested.connect(self.toggle_start)
        self.taskbar.desks_requested.connect(self._desks_popup)
        self.taskbar.clock_requested.connect(self.toggle_action_center)
        self.topbar = self.taskbar  # older code (and tests) look for the chips here
        self._root.addWidget(self.desktop, 1)
        self.hot_edge = QWidget(self.bg)  # auto-hide: touching this edge brings the taskbar back
        self.hot_edge.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.hot_edge.enterEvent = lambda _e: self._reveal_bar()
        self.hot_edge.hide()

        self.start = StartMenu(self.ctl, self.bg)
        from app.ui.action_center import ActionCenter
        self.action_center = ActionCenter(self.ctl, self.bg)
        from app.ui.widgets.system_tray import SystemPanel
        self.system_panel = SystemPanel(self.ctl, self.bg)
        self.taskbar.status_requested.connect(self.system_panel.open_for)
        self.palette = CommandPalette(self.ctl, self.bg)
        self.toaster = Toast(self.bg)
        self.setCentralWidget(self.bg)
        self.bg.installEventFilter(self)
        self._apply_bar_mode()

    def _install_shortcuts(self) -> None:
        def sc(keys, slot):
            QShortcut(QKeySequence(keys), self, activated=slot)

        sc("Ctrl+Space", self.open_palette)
        sc("Ctrl+K", self.open_palette)
        sc("Ctrl+N", lambda: self.ctl.open_quick_note())
        sc("Ctrl+Esc", self.toggle_start)
        sc("Alt+S", self.toggle_start)
        sc("Alt+A", self.toggle_action_center)
        sc("F11", self.toggle_fullscreen)
        sc("Escape", self._on_escape)
        # switching desktops (also from the desktops button / the floating desk bar)
        sc("Ctrl+Alt+Right", lambda: self.desktop.next_desk(-1 if self.isRightToLeft() else 1))
        sc("Ctrl+Alt+Left", lambda: self.desktop.next_desk(1 if self.isRightToLeft() else -1))
        sc("Ctrl+PgDown", lambda: self.desktop.next_desk(1))
        sc("Ctrl+PgUp", lambda: self.desktop.next_desk(-1))
        for n in range(1, 10):
            sc(f"Ctrl+Alt+{n}", lambda i=n - 1: self.desktop.desk_number(i))

    # ------------------------------------------------- compatibility API
    # (the controller and pages still say window.go_to("notes") / window.pages["notes"])
    def go_to(self, key: str, animate: bool = True) -> None:
        if key == "desktop":
            self.show_os()
        else:
            self.ctl.apps.open(key)

    @property
    def pages(self) -> dict:
        out = {"desktop": self.desktop}
        out.update({k: w.page for k, w in self.ctl.apps.windows.items() if w.page is not None})
        return out

    @property
    def current_page(self) -> str:
        return self.ctl.apps.active_key() or "desktop"

    def desk_changed(self, desk: str) -> None:
        """Another desktop: its own wallpaper and its own taskbar apps (or the general ones)."""
        self.bg.set_wallpaper(self.ctl.desk_wallpaper())
        self.apply_taskbar_settings()

    # ------------------------------------------------------------- Start / desks
    def toggle_start(self) -> None:
        self.palette.close_palette()
        self.action_center.close_panel()
        self.start.toggle()

    def toggle_action_center(self) -> None:
        self.start.close_menu()
        self.action_center.toggle()

    def _desks_popup(self, anchor) -> None:
        menu = make_menu(self)
        current = self.desktop.canvas.desk
        for i, (key, name) in enumerate(self.desktop.desk_keys()):
            act = add_action(menu, Icons.ACCEPT if key == current else Icons.DESKTOP, name,
                             lambda k=key: self.desktop.show_desk(k), f"Ctrl+Alt+{i + 1}" if i < 9 else None)
        menu.addSeparator()
        add_action(menu, Icons.COLOR, tr("desk_look_menu"), lambda: self.ctl.edit_desk_look(parent=self))
        add_action(menu, Icons.ADD, tr("new_group"), lambda: self.ctl.add_group(self))
        size = menu.sizeHint()
        top = anchor.mapToGlobal(QPoint(0, 0))
        y = top.y() - size.height() - 6 if self.taskbar.conf["position"] == "bottom" else top.y() + anchor.height() + 6
        menu.exec(QPoint(top.x(), y))

    # ------------------------------------------------- taskbar: docked / auto-hide
    def _apply_bar_mode(self) -> None:
        bar = self.taskbar
        conf = bar.conf
        top = conf["position"] == "top"
        if self._root.indexOf(bar) >= 0:
            self._root.removeWidget(bar)
        if not conf.get("autohide"):
            self._bar_watch.stop()
            self.hot_edge.hide()
            bar.setGraphicsEffect(None)
            self._root.insertWidget(0 if top else self._root.count(), bar)
            bar.show()
        else:
            bar.setParent(self.bg)
            shadow = QGraphicsDropShadowEffect(bar)
            shadow.setBlurRadius(28)
            shadow.setOffset(0, 6 if top else -6)
            shadow.setColor(QColor(0, 0, 0, 140))
            bar.setGraphicsEffect(shadow)
            bar.show()
            self._layout_floating()
            bar.move(0, self._hidden_y())
            self._bar_shown = False
            self.hot_edge.show()
            self.hot_edge.raise_()

    def _hidden_y(self) -> int:
        bar = self.taskbar
        return -bar.height() - 8 if bar.conf["position"] == "top" else self.bg.height() + 8

    def _shown_y(self) -> int:
        bar = self.taskbar
        return 0 if bar.conf["position"] == "top" else self.bg.height() - bar.height()

    def _layout_floating(self) -> None:
        if not self.taskbar.conf.get("autohide"):
            return
        w, h = self.bg.width(), self.bg.height()
        self.taskbar.setFixedWidth(w)
        top = self.taskbar.conf["position"] == "top"
        self.hot_edge.setGeometry(0, 0 if top else h - 3, w, 3)
        if not self._bar_shown:
            self.taskbar.move(0, self._hidden_y())
        else:
            self.taskbar.move(0, self._shown_y())

    def _animate_bar(self, show: bool) -> None:
        bar = self.taskbar
        self._bar_shown = show
        if self._bar_anim:
            self._bar_anim.stop()
        bar.raise_()
        self._bar_anim = QPropertyAnimation(bar, b"pos", self)
        self._bar_anim.setDuration(170 if show else 220)
        self._bar_anim.setEasingCurve(QEasingCurve.Type.OutCubic if show else QEasingCurve.Type.InCubic)
        self._bar_anim.setEndValue(QPoint(0, self._shown_y() if show else self._hidden_y()))
        self._bar_anim.start()

    def _reveal_bar(self) -> None:
        if not self.taskbar.conf.get("autohide") or self._bar_shown:
            return
        self._animate_bar(True)
        self._bar_away = 0
        self._bar_watch.start()

    def _watch_bar(self) -> None:
        if not self.taskbar.conf.get("autohide") or not self._bar_shown:
            self._bar_watch.stop()
            return
        bar = self.taskbar
        area = QRect(bar.mapToGlobal(QPoint(0, 0)), bar.size()).adjusted(0, -24, 0, 24)
        busy = QApplication.activePopupWidget() is not None or self.start.isVisible()
        if area.contains(QCursor.pos()) or busy:
            self._bar_away = 0
            return
        self._bar_away = getattr(self, "_bar_away", 0) + 1
        if self._bar_away >= 3:
            self._bar_watch.stop()
            self._animate_bar(False)

    def apply_taskbar_settings(self) -> None:
        """Settings › Taskbar changed: a new bar with the new look (the desktop stays as it is)."""
        old = self.taskbar
        if self._root.indexOf(old) >= 0:
            self._root.removeWidget(old)
        old.hide()
        old.deleteLater()
        self.taskbar = Taskbar(self.ctl, parent=self.bg)
        self.taskbar.start_requested.connect(self.toggle_start)
        self.taskbar.desks_requested.connect(self._desks_popup)
        self.taskbar.clock_requested.connect(self.toggle_action_center)
        if hasattr(self, "system_panel"):
            self.taskbar.status_requested.connect(self.system_panel.open_for)
        self.topbar = self.taskbar
        self._bar_shown = True
        self._apply_bar_mode()
        self.start.raise_()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.bg and event.type() == event.Type.Resize:
            self._layout_floating()
        return super().eventFilter(obj, event)

    # --------------------------------------------------------------- overlays
    def open_palette(self, text: str = "") -> None:
        self.start.close_menu()
        self.palette.open(text)

    def toast(self, text: str, kind: str = "info") -> None:
        self.toaster.show_message(text, kind)

    def _on_escape(self) -> None:
        if self.action_center.isVisible():
            self.action_center.close_panel()
        elif self.start.isVisible():
            self.start.close_menu()
        elif self.palette.isVisible():
            self.palette.close_palette()

    # ---------------------------------------------------------------- showing
    def show_os(self, splash: bool = False) -> None:
        if self.fullscreen:
            self.showFullScreen()
        else:
            self.showMaximized()
        self.raise_()
        self.activateWindow()
        winapi.force_foreground(int(self.winId()))
        if not self._intro_played:
            self._intro_played = True
            if splash:
                boot = BootSplash(self.bg)
                boot.finished.connect(self._play_intro)
                boot.start()
            else:
                QTimer.singleShot(60, self._play_intro)

    def _play_intro(self) -> None:
        self.desktop.on_shown()

    def set_fullscreen(self, on: bool) -> None:
        self.fullscreen = on
        self.ctl.db.set_setting("fullscreen", on)
        if self.isVisible():
            if on:
                self.showFullScreen()
            else:
                self.showNormal()
                self.showMaximized()

    def toggle_fullscreen(self) -> None:
        self.set_fullscreen(not self.fullscreen)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.ActivationChange and hasattr(self.ctl, "apps"):
            self.ctl.apps.changed.emit()

    def closeEvent(self, event) -> None:
        """Alt+F4 hides to the tray: hotkeys, quick notes and the app windows keep working."""
        if self.ctl.quitting:
            event.accept()
            return
        event.ignore()
        self.hide()
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.ctl.notify(tr("still_running"), "info")
