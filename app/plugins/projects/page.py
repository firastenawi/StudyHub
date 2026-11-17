"""Projects page (plugin): a card per code project with its Git state and quick actions."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QListWidget,
                             QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget)

from app.core.i18n import ltr, tr
from app.plugins.projects import scanner
from app.ui.dialogs.base import ConfirmDialog, StyledDialog
from app.ui.pages.base import Page
from app.ui.theme import Icons, Palette
from app.ui.widgets.common import (Card, EmptyState, GlyphPlate, IconButton, add_action, button, label, make_menu,
                                   primary_button)

KIND_STYLE = {  # color, label
    "dotnet": ("#A78BFA", ".NET / C#"), "node": ("#34D399", "Node.js"), "python": ("#60A5FA", "Python"),
    "rust": ("#F97316", "Rust"), "go": ("#22D3EE", "Go"), "java": ("#F87171", "Java"), "php": ("#818CF8", "PHP"),
    "cpp": ("#38BDF8", "C / C++"), "docker": ("#0EA5E9", "Docker"), "web": ("#FBBF24", "Web"),
    "other": ("#94A3B8", ""),
}


class _Worker(QObject):
    done = pyqtSignal(object, object)   # callback, result

    def __init__(self, parent=None):
        super().__init__(parent)
        self.done.connect(lambda cb, result: cb(result))

    def run(self, fn, callback) -> None:
        def work():
            try:
                result = fn()
            except Exception as exc:
                result = exc
            self.done.emit(callback, result)
        threading.Thread(target=work, daemon=True).start()


class OutputDialog(StyledDialog):
    def __init__(self, title: str, text: str, parent=None):
        super().__init__(title, parent, 720)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        f = QFont()
        f.setFamilies(["Cascadia Mono", "Consolas"])
        f.setPointSizeF(9.5)
        box.setFont(f)
        box.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        box.setMinimumHeight(360)
        self.body.addWidget(box)
        self.add_buttons(tr("close"))


class ProjectCard(Card):
    def __init__(self, info: dict, page):
        super().__init__(padding=16)
        self.info, self.page = info, page
        color, kind_label = KIND_STYLE.get(info["kind"], KIND_STYLE["other"])
        head = QHBoxLayout()
        head.addWidget(GlyphPlate(Icons.FOLDER, color, 40))
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(label(info["name"], 11.5, QFont.Weight.DemiBold))
        path = label(ltr(info["path"]), 8.4, role="Faint")
        path.setToolTip(info["path"])
        col.addWidget(path)
        head.addLayout(col, 1)
        more = IconButton(Icons.MENU, tr("prj_more"), 32, 10)
        more.clicked.connect(lambda: page.menu(info, more))
        head.addWidget(more)
        self.root.addLayout(head)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        if kind_label:
            chips.addWidget(self._chip(kind_label, color))
        if info["git"]:
            chips.addWidget(self._chip("⎇ " + info.get("branch", "—"), Palette.text_dim))
            n = info.get("changes", 0)
            chips.addWidget(self._chip(tr("prj_changes", n=n) if n else tr("prj_clean"),
                                       Palette.warning if n else Palette.success))
            if info.get("ahead"):
                chips.addWidget(self._chip(f"↑ {info['ahead']}", Palette.info))
            if info.get("behind"):
                chips.addWidget(self._chip(f"↓ {info['behind']}", Palette.warning))
        else:
            chips.addWidget(self._chip(tr("prj_no_git"), Palette.text_faint))
        chips.addStretch(1)
        self.root.addLayout(chips)
        last = info.get("last")
        if last:
            self.root.addWidget(label(f"{last['subject'][:80]}  ·  {last['when']}", 9, role="Dim"))

        acts = QHBoxLayout()
        acts.setSpacing(6)
        if info["sln"] and page.vs:
            b = button("Visual Studio", Icons.APPS)
            b.clicked.connect(lambda: page.open_vs(info))
            acts.addWidget(b)
        if page.code:
            b = button("VS Code", Icons.EDIT, role="Ghost" if info["sln"] and page.vs else "")
            b.clicked.connect(lambda: page.open_code(info))
            acts.addWidget(b)
        for glyph, tip, slot in ((Icons.OPEN_FOLDER, tr("prj_folder"), page.open_folder),
                                 (Icons.KEYBOARD, tr("prj_terminal"), page.open_terminal)):
            b = IconButton(glyph, tip, 34, 10)
            b.clicked.connect(lambda _=False, s=slot: s(info))
            acts.addWidget(b)
        if info["git"] and info.get("behind"):
            b = button(tr("prj_pull"), Icons.DOWNLOAD)
            b.clicked.connect(lambda: page.pull(info))
            acts.addWidget(b)
        acts.addStretch(1)
        self.root.addLayout(acts)

    @staticmethod
    def _chip(text: str, color: str) -> QWidget:
        w = label(text, 8.6)
        w.setStyleSheet(f"color: {color}; border: 1px solid {color}55; border-radius: 9px; padding: 1px 8px;")
        return w


class ProjectsPage(Page):
    def __init__(self, ctl, main_window, api):
        super().__init__(ctl, main_window)
        self.api = api
        self.worker = _Worker(self)
        self.projects: list[dict] = []
        self.vs, self.code = "", ""
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(16)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(tr("nav_projects"), 22, QFont.Weight.DemiBold))
        self.subtitle = label(tr("prj_subtitle"), 10, role="Dim")
        titles.addWidget(self.subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("prj_search"))
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._fill)
        header.addWidget(self.search)
        refresh = IconButton(Icons.REFRESH, tr("reload"), 36, 11)
        refresh.clicked.connect(self.scan)
        header.addWidget(refresh)
        folders = primary_button(tr("prj_folders"), Icons.FOLDER)
        folders.clicked.connect(self.edit_roots)
        header.addWidget(folders)
        root.addLayout(header)
        self.tools = label("", 9, role="Faint")
        root.addWidget(self.tools)
        box = QWidget()
        self.grid = QGridLayout(box)
        self.grid.setContentsMargins(0, 0, 8, 0)
        self.grid.setSpacing(14)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(box)
        self.scroll.viewport().setAutoFillBackground(False)
        box.setAutoFillBackground(False)
        root.addWidget(self.scroll, 1)
        self.empty = EmptyState(Icons.FOLDER, tr("prj_empty"))
        root.addWidget(self.empty)
        self.empty.hide()
        self._scanned = False

    def on_shown(self) -> None:
        if not self._scanned:
            self.scan()

    def roots(self) -> list[str]:
        return list(self.api.setting("roots", scanner.default_roots()) or [])

    # ---------------------------------------------------------------- scanning
    def scan(self) -> None:
        self._scanned = True
        self.subtitle.setText(tr("prj_scanning"))
        roots = self.roots()

        def work():
            return scanner.visual_studio(), scanner.vscode(), scanner.git_exe(), \
                scanner.inspect_all(scanner.find_projects(roots))

        def done(result):
            try:
                self.isVisible()
            except RuntimeError:
                return
            if isinstance(result, Exception):
                self.subtitle.setText(str(result))
                return
            self.vs, self.code, git, projects = result
            self.projects = sorted(projects, key=lambda p: p.get("modified", 0), reverse=True)
            self.subtitle.setText(tr("prj_count", n=len(self.projects)))
            found = [("Visual Studio", self.vs), ("VS Code", self.code), ("Git", git)]
            self.tools.setText("   ".join(f"{'✓' if path else '✗'} {name}" for name, path in found))
            self._fill()
        self.worker.run(work, done)

    def _fill(self, *_):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        q = self.search.text().strip().lower()
        shown = [p for p in self.projects if not q or q in (p["name"] + " " + p["path"]).lower()]
        cols = max(1, min(3, self.scroll.viewport().width() // 420))
        for i, info in enumerate(shown):
            self.grid.addWidget(ProjectCard(info, self), i // cols, i % cols)
        self.grid.setRowStretch(len(shown) // cols + 1, 1)
        self.empty.setVisible(not shown and self._scanned)
        self.scroll.setVisible(bool(shown))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.projects:
            cols = max(1, min(3, self.scroll.viewport().width() // 420))
            if cols != getattr(self, "_cols", 0):
                self._cols = cols
                self._fill()

    # ----------------------------------------------------------------- actions
    def open_vs(self, info: dict) -> None:
        import subprocess
        subprocess.Popen([self.vs, info["sln"]], creationflags=scanner.NO_WINDOW)

    def open_code(self, info: dict) -> None:
        import subprocess
        subprocess.Popen([self.code, info["path"]], creationflags=scanner.NO_WINDOW)

    def open_folder(self, info: dict) -> None:
        self.ctl.open_path(info["path"])

    def open_terminal(self, info: dict) -> None:
        scanner.open_terminal(info["path"])

    def _git_action(self, info: dict, fn, title: str, show_always: bool = False) -> None:
        self.api.notify(f"{title}…", "info")

        def done(result):
            code, out = result if not isinstance(result, Exception) else (1, str(result))
            if code == 0:
                self.api.notify(f"{title} ✓  {info['name']}", "success")
                if show_always and out:
                    OutputDialog(f"{title} · {info['name']}", out, self).exec()
            else:
                OutputDialog(f"{title} · {info['name']}", out, self).exec()
            self.scan()
        self.worker.run(lambda: fn(info["path"]), done)

    def pull(self, info: dict) -> None:
        self._git_action(info, scanner.pull, tr("prj_pull"))

    def menu(self, info: dict, anchor) -> None:
        menu = make_menu(self)
        if info["git"]:
            add_action(menu, Icons.DOWNLOAD, tr("prj_pull"), lambda: self.pull(info))
            add_action(menu, Icons.SYNC, tr("prj_fetch"), lambda: self._git_action(info, scanner.fetch, tr("prj_fetch")))
            add_action(menu, Icons.VIEW_LIST, tr("prj_status"),
                       lambda: OutputDialog(tr("prj_status"), scanner.changed_files(info["path"]), self).exec())
            add_action(menu, Icons.CLOCK, tr("prj_commits"),
                       lambda: OutputDialog(tr("prj_commits"), scanner.recent_commits(info["path"]), self).exec())
            url = scanner.web_url(info.get("remote", ""))
            if url:
                add_action(menu, Icons.GLOBE, tr("prj_remote"), lambda: QDesktopServices.openUrl(QUrl(url)))
        elif scanner.git_exe():
            add_action(menu, Icons.ADD, tr("prj_init"), lambda: self._init(info))
        menu.addSeparator()
        add_action(menu, Icons.COPY, tr("prj_copy_path"), lambda: self.ctl.copy_text(info["path"]))
        if info["sln"]:
            add_action(menu, Icons.DOCUMENT, Path(info["sln"]).name, lambda: self.ctl.open_path(info["sln"]))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _init(self, info: dict) -> None:
        if ConfirmDialog.ask(tr("prj_init"), tr("prj_init_msg", name=info["name"]), tr("prj_init"), self, danger=False):
            self._git_action(info, scanner.init, tr("prj_init"))

    def edit_roots(self) -> None:
        dlg = RootsDialog(self.roots(), self)
        if dlg.exec() == StyledDialog.DialogCode.Accepted:
            self.api.set_setting("roots", dlg.roots())
            self.scan()


class RootsDialog(StyledDialog):
    def __init__(self, roots: list[str], parent=None):
        super().__init__(tr("prj_folders"), parent, 560)
        self.body.addWidget(label(tr("prj_folders_hint"), 9, role="Dim", wrap=True))
        self.list = QListWidget()
        self.list.setMinimumHeight(180)
        for r in roots:
            self.list.addItem(r)
        self.body.addWidget(self.list)
        row = QHBoxLayout()
        add = button(tr("prj_add_folder"), Icons.ADD)
        add.clicked.connect(self._add)
        row.addWidget(add)
        rm = button(tr("remove"), Icons.DELETE, role="Ghost")
        rm.clicked.connect(lambda: self.list.takeItem(self.list.currentRow()))
        row.addWidget(rm)
        row.addStretch(1)
        self.body.addLayout(row)
        self.add_buttons(tr("save"))

    def _add(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("prj_add_folder"), str(Path.home()))
        if folder:
            self.list.addItem(os.path.normpath(folder))

    def roots(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]
