"""
Terminal: a real terminal (Windows ConPTY through pywinpty, drawn by xterm.js) in tabs.

  + menu        PowerShell · Command Prompt · WSL (if installed) · SSH to any server saved in Infrastructure
  split         each tab splits in two (side by side or one above the other); click a pane to type in it
  snippets      the toolbar's snippet button pastes a snippet into the pane you're typing in
  lab           while a lab is recording, what happens in the terminal goes into the report (one block per command)
Interactive programs work (vim, nano, top, ssh password prompts, colours, Tab completion).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import QHBoxLayout, QMainWindow, QSplitter, QTabWidget, QToolButton, QVBoxLayout, QWidget

from app.core.i18n import tr
from app.core.logs import log
from app.ui import apps as app_registry
from app.ui.app_window import give_identity, place_window, remember_window
from app.ui.theme import Icons, Palette, Theme
from app.ui.widgets.common import IconButton, add_action, add_submenu, make_menu

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "xterm"
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b[@-_]|\r")


def profiles(ctl) -> list[dict]:
    """What the "+" menu offers. Each: {key, title, cmd (list), glyph}."""
    out = []
    pwsh = shutil.which("pwsh")
    out.append({"key": "powershell", "title": "PowerShell", "cmd": [pwsh or "powershell.exe", "-NoLogo"], "glyph": Icons.KEYBOARD})
    out.append({"key": "cmd", "title": tr("term_cmd"), "cmd": ["cmd.exe"], "glyph": Icons.KEYBOARD})
    wsl = shutil.which("wsl")
    if wsl:
        out.append({"key": "wsl", "title": "WSL (Linux)", "cmd": [wsl], "glyph": Icons.KEYBOARD})
    return out


def ssh_profile(ctl, host_id: int) -> dict | None:
    infra = getattr(ctl, "infra", None)
    host = infra.store.host(host_id) if infra else None
    if not host:
        return None
    from app.plugins.infra.ssh import ssh_args
    return {"key": f"ssh:{host_id}", "title": host["name"], "cmd": ssh_args(host), "glyph": Icons.GLOBE}


class Bridge(QObject):
    """What the xterm.js page talks to (QWebChannel)."""
    output = pyqtSignal(str)
    focus_request = pyqtSignal()

    def __init__(self, view: "TermView"):
        super().__init__(view)
        self.view = view

    @pyqtProperty(str, constant=True)
    def theme(self) -> str:
        light = Palette.light()
        return json.dumps({
            "background": "#FBFCFE" if light else "#0B0F17", "foreground": Palette.text,
            "cursor": Theme.accent(), "cursorAccent": "#FFFFFF", "selectionBackground": Theme.accent() + "66",
            "black": "#1E2230", "red": "#F87171", "green": "#34D399", "yellow": "#FBBF24", "blue": "#60A5FA",
            "magenta": "#C084FC", "cyan": "#22D3EE", "white": "#D7DBE5", "brightBlack": "#5B6375",
            "brightRed": "#FCA5A5", "brightGreen": "#6EE7B7", "brightYellow": "#FDE68A", "brightBlue": "#93C5FD",
            "brightMagenta": "#D8B4FE", "brightCyan": "#67E8F9", "brightWhite": "#FFFFFF",
            "fontSize": int(self.view.ctl.db.get_setting("terminal_font", 14)),
        })

    @pyqtSlot(str)
    def input(self, data: str) -> None:
        self.view.write(data)

    @pyqtSlot(int, int)
    def resize(self, cols: int, rows: int) -> None:
        self.view.set_size(cols, rows)

    @pyqtSlot(int, int)
    def ready(self, cols: int, rows: int) -> None:
        self.view.start(cols, rows)

    @pyqtSlot(str)
    def copy(self, text: str) -> None:
        if text:
            QGuiApplication.clipboard().setText(text)

    @pyqtSlot()
    def paste(self) -> None:
        text = QGuiApplication.clipboard().text()
        if text:
            self.view.write(text.replace("\r\n", "\r").replace("\n", "\r"))


class TermView(QWidget):
    """One terminal pane: an xterm.js page + one program in a ConPTY."""
    data = pyqtSignal(str)      # from the reader thread (queued into the GUI thread)
    exited = pyqtSignal()
    focused = pyqtSignal(object)

    def __init__(self, ctl, profile: dict, parent=None):
        super().__init__(parent)
        from PyQt6.QtWebChannel import QWebChannel
        from PyQt6.QtWebEngineCore import QWebEngineSettings
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        self.ctl, self.profile = ctl, profile
        self.pty = None
        self._size = (120, 30)
        self._alive = False
        self._lab_buf = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.web = QWebEngineView(self)
        s = self.web.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)
        self.web.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self.bridge = Bridge(self)
        self.channel = QWebChannel(self.web.page())
        self.channel.registerObject("bridge", self.bridge)
        self.web.page().setWebChannel(self.channel)
        self.web.load(QUrl.fromLocalFile(str(ASSETS / "term.html")))
        self.web.focusProxy() and self.web.focusProxy().installEventFilter(self)
        lay.addWidget(self.web)
        self.data.connect(self._out, Qt.ConnectionType.QueuedConnection)

    # ------------------------------------------------------------- program
    def start(self, cols: int, rows: int) -> None:
        if self.pty is not None:
            return
        try:
            from winpty import PtyProcess
        except ImportError:
            self.bridge.output.emit("\r\n  pywinpty is missing — the terminal cannot start.\r\n")
            return
        self._size = (max(20, cols), max(5, rows))
        cmd = self.profile["cmd"]
        try:
            self.pty = PtyProcess.spawn(cmd if isinstance(cmd, str) else subprocess_line(cmd),
                                        cwd=str(Path.home()), dimensions=(self._size[1], self._size[0]),
                                        env=dict(os.environ, TERM="xterm-256color"))
        except Exception as exc:
            log.exception("terminal: cannot start %s", cmd)
            self.bridge.output.emit(f"\r\n  {tr('term_cannot_start')}: {exc}\r\n")
            return
        self._alive = True
        threading.Thread(target=self._reader, name="terminal-reader", daemon=True).start()
        self.bridge.focus_request.emit()

    def _reader(self) -> None:
        pty = self.pty
        while self._alive and pty is not None:
            try:
                chunk = pty.read(8192)
            except EOFError:
                break
            except Exception:
                if not pty.isalive():
                    break
                time.sleep(0.02)
                continue
            if chunk:
                self.data.emit(chunk)
        self._alive = False
        self.data.emit("\r\n\x1b[90m[" + tr("term_exited") + "]\x1b[0m\r\n")
        QTimer.singleShot(0, self.exited.emit)

    def _out(self, chunk: str) -> None:
        self.bridge.output.emit(chunk)
        lab = getattr(self.ctl, "lab", None)
        if lab is not None and lab.active:
            self._lab_buf += chunk
            if len(self._lab_buf) > 200_000:
                self._lab_buf = self._lab_buf[-200_000:]

    def write(self, data: str) -> None:
        if self.pty is not None and self._alive:
            try:
                self.pty.write(data)
            except Exception:
                pass
        if "\r" in data:
            self.flush_lab()  # one report block per command

    def flush_lab(self) -> None:
        lab = getattr(self.ctl, "lab", None)
        if lab is None or not lab.active or not self._lab_buf.strip():
            self._lab_buf = ""
            return
        text = _ANSI.sub("", self._lab_buf).replace("\x07", "").rstrip()
        self._lab_buf = ""
        if text.strip():
            lab.add("terminal", host=self.profile["title"], out=text[-20_000:])

    def set_size(self, cols: int, rows: int) -> None:
        self._size = (cols, rows)
        if self.pty is not None and self._alive:
            try:
                self.pty.setwinsize(max(5, rows), max(20, cols))
            except Exception:
                pass

    def paste_text(self, text: str) -> None:
        self.write(text.replace("\r\n", "\r").replace("\n", "\r") if "\n" in text else text)
        self.bridge.focus_request.emit()

    def focus_term(self) -> None:
        self.web.setFocus()
        self.bridge.focus_request.emit()

    def eventFilter(self, obj, e):
        if e.type() == e.Type.FocusIn:
            self.focused.emit(self)
        return super().eventFilter(obj, e)

    def close_pty(self) -> None:
        self.flush_lab()
        self._alive = False
        if self.pty is not None:
            try:
                self.pty.terminate(force=True)
            except Exception:
                pass
            self.pty = None


def subprocess_line(cmd: list[str]) -> str:
    import subprocess
    return subprocess.list2cmdline(cmd)


class TermTab(QSplitter):
    """A tab: one pane, or two after a split."""

    def __init__(self, window: "TerminalWindow", profile: dict):
        super().__init__(Qt.Orientation.Horizontal)
        self.window_ = window
        self.profile = profile
        self.setChildrenCollapsible(False)
        self.setHandleWidth(4)
        self.add_view(profile)

    def views(self) -> list[TermView]:
        return [self.widget(i) for i in range(self.count()) if isinstance(self.widget(i), TermView)]

    def add_view(self, profile: dict) -> TermView:
        view = TermView(self.window_.ctl, profile)
        view.focused.connect(self.window_.set_active)
        view.exited.connect(lambda v=view: self.window_.view_exited(self, v))
        self.addWidget(view)
        self.window_.set_active(view)
        QTimer.singleShot(400, view.focus_term)
        return view

    def split(self, orientation: Qt.Orientation) -> None:
        if len(self.views()) >= 2:
            self.window_.ctl.notify(tr("term_split_max"), "info")
            return
        self.setOrientation(orientation)
        self.add_view(self.window_.active.profile if self.window_.active else self.profile)
        self.setSizes([1, 1])


class TerminalWindow(QMainWindow):
    def __init__(self, ctl, first: dict | None = None):
        super().__init__()
        self.ctl = ctl
        self.info = app_registry.find(ctl, "terminal")
        self.active: TermView | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{self.info.title} — Study Hub")
        self.setWindowIcon(self.info.icon())
        self.setMinimumSize(700, 420)
        central = QWidget()
        central.setObjectName("TermRoot")
        central.setStyleSheet(f"QWidget#TermRoot {{ background: {'#FBFCFE' if Palette.light() else '#0B0F17'}; }}")
        lay = QVBoxLayout(central)
        lay.setContentsMargins(6, 4, 6, 6)
        lay.setSpacing(4)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("TermTabs")
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(lambda _: self._tab_changed())
        corner = QWidget()
        cl = QHBoxLayout(corner)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(2)
        self.add_btn = IconButton(Icons.ADD, tr("term_new_tab") + "  (Ctrl+Shift+T)", 30, 10)
        self.add_btn.clicked.connect(lambda: self._new_menu(self.add_btn))
        cl.addWidget(self.add_btn)
        for glyph, tip, slot in ((Icons.VIEW_GRID, tr("term_split_right") + "  (Alt+Shift+=)", lambda: self._split(Qt.Orientation.Horizontal)),
                                 (Icons.VIEW_LIST, tr("term_split_down") + "  (Alt+Shift+-)", lambda: self._split(Qt.Orientation.Vertical)),
                                 (Icons.PASTE, tr("term_snippet") + "  (Ctrl+Shift+S)", self.paste_snippet)):
            b = IconButton(glyph, tip, 30, 10)
            b.clicked.connect(slot)
            cl.addWidget(b)
        self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        lay.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        place_window(self, "terminal", ctl, (0.6, 0.62))
        self._identity_set = False
        for keys, slot in (("Ctrl+Shift+T", lambda: self.new_tab(profiles(ctl)[0])), ("Ctrl+Shift+W", self.close_current),
                           ("Alt+Shift+=", lambda: self._split(Qt.Orientation.Horizontal)),
                           ("Alt+Shift+-", lambda: self._split(Qt.Orientation.Vertical)),
                           ("Ctrl+Shift+S", self.paste_snippet), ("Ctrl+Tab", lambda: self._cycle(1)),
                           ("Ctrl+Shift+Tab", lambda: self._cycle(-1))):
            QShortcut(QKeySequence(keys), self, activated=slot)
        self.new_tab(first or profiles(ctl)[0])

    # ---------------------------------------------------------------- tabs
    def new_tab(self, profile: dict) -> TermTab:
        tab = TermTab(self, profile)
        i = self.tabs.addTab(tab, profile.get("glyph_text", "") + profile["title"])
        close = IconButton(Icons.CLOSE, tr("close") + "  (Ctrl+Shift+W)", 22, 7.5)
        close.clicked.connect(lambda _=False, t=tab: self.close_tab(self.tabs.indexOf(t)))
        from PyQt6.QtWidgets import QTabBar
        self.tabs.tabBar().setTabButton(i, QTabBar.ButtonPosition.RightSide, close)
        self.tabs.setCurrentIndex(i)
        return tab

    def open_ssh(self, host_id: int) -> None:
        p = ssh_profile(self.ctl, host_id)
        if p:
            self.new_tab(p)

    def _new_menu(self, anchor) -> None:
        menu = make_menu(self)
        for p in profiles(self.ctl):
            add_action(menu, p["glyph"], p["title"], lambda p=p: self.new_tab(p))
        infra = getattr(self.ctl, "infra", None)
        hosts = infra.store.hosts() if infra else []
        if hosts:
            sub = add_submenu(menu, Icons.GLOBE, "SSH")
            for h in hosts:
                add_action(sub, Icons.GLOBE, f"{h['name']}  ·  {h['address']}", lambda hid=h["id"]: self.open_ssh(hid))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _split(self, orientation) -> None:
        tab = self.tabs.currentWidget()
        if isinstance(tab, TermTab):
            tab.split(orientation)

    def _cycle(self, d: int) -> None:
        if self.tabs.count():
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + d) % self.tabs.count())

    def _tab_changed(self) -> None:
        tab = self.tabs.currentWidget()
        if isinstance(tab, TermTab) and tab.views():
            self.set_active(tab.views()[0])
            QTimer.singleShot(50, tab.views()[0].focus_term)

    def set_active(self, view: TermView) -> None:
        self.active = view

    def close_tab(self, index: int) -> None:
        tab = self.tabs.widget(index)
        if isinstance(tab, TermTab):
            for v in tab.views():
                v.close_pty()
        self.tabs.removeTab(index)
        if tab is not None:
            tab.deleteLater()
        if self.tabs.count() == 0:
            self.close()

    def close_current(self) -> None:
        if self.tabs.count():
            self.close_tab(self.tabs.currentIndex())

    def view_exited(self, tab: TermTab, view: TermView) -> None:
        """The program ended (exit): close its pane, and the tab when it was the last one."""
        if len(tab.views()) > 1:
            view.close_pty()
            view.setParent(None)
            view.deleteLater()
        else:
            i = self.tabs.indexOf(tab)
            if i >= 0:
                self.close_tab(i)

    # ------------------------------------------------------------ snippets
    def paste_snippet(self) -> None:
        target = self.active
        if target is None:
            return
        self.ctl.snippet_popup().open(on_pick=lambda code, v=target: v.paste_text(code))

    def paste_text(self, text: str) -> None:
        if self.active is not None:
            self.active.paste_text(text)

    def flush_lab(self) -> None:
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, TermTab):
                for v in tab.views():
                    v.flush_lab()

    # ---------------------------------------------------------------- window
    def showEvent(self, e):
        super().showEvent(e)
        if not self._identity_set:
            self._identity_set = True
            give_identity(self, self.info)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (e.Type.ActivationChange, e.Type.WindowStateChange) and hasattr(self.ctl, "apps"):
            self.ctl.apps.changed.emit()

    def closeEvent(self, e):
        remember_window(self, "terminal", self.ctl)
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, TermTab):
                for v in tab.views():
                    v.close_pty()
        super().closeEvent(e)
        if getattr(self.ctl.apps, "terminal_window", None) is self:  # gone now, not when Qt deletes it later
            self.ctl.apps._terminal_gone()
