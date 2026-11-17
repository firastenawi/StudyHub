"""
Subject folders: every subject can be linked to one or more folders on the PC.
Browse them inside Study Hub (breadcrumbs, filter), open/tag/pin files, and drop
files from Explorer to copy them into the current folder.
"""
from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QStackedWidget,
                             QVBoxLayout, QWidget)

from app.core.files import list_dir
from app.core.i18n import tr
from app.ui.animations import clear_list
from app.ui.pages.files.common import add_row, file_list, file_menu, selected_paths
from app.ui.theme import Icons, ui_font
from app.ui.widgets.common import (Card, EmptyState, GlyphLabel, IconButton, add_action, button, label,
                                   primary_button)
from app.ui.widgets.files import FileRow, tag_lookup, tags_from_ids

MAX_ROWS = 400


class LibraryView(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl, self.page = ctl, page
        self.subject_id: int | None = None
        self.root: str | None = None      # linked folder being browsed
        self.path: str | None = None      # current folder (inside root)
        self.setAcceptDrops(True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(18)

        # ----------------------------------------------------- subjects column
        side = Card(tr("tab_subjects"), Icons.BOOK, padding=16)
        side.setFixedWidth(280)
        self.subjects = QListWidget()
        self.subjects.currentItemChanged.connect(self._subject_selected)
        side.root.addWidget(self.subjects, 1)
        add_subject = button(tr("add_subject"), Icons.ADD)
        add_subject.clicked.connect(lambda: ctl.add_subject_dialog(self))
        side.root.addWidget(add_subject)
        lay.addWidget(side)

        # ------------------------------------------------------ browser column
        self.stack = QStackedWidget()
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.addStretch(1)
        self.empty_state = EmptyState(Icons.FOLDER, "")
        el.addWidget(self.empty_state)
        self.empty_button = primary_button(tr("link_folder"), Icons.ADD)
        self.empty_button.clicked.connect(self._empty_action)
        el.addWidget(self.empty_button, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addStretch(2)
        self.stack.addWidget(self.empty)

        browser = Card(padding=18)
        b = browser.root
        b.setSpacing(10)
        head = QHBoxLayout()
        self.subject_title = label("", 15, QFont.Weight.DemiBold)
        head.addWidget(self.subject_title)
        head.addStretch(1)
        link = button(tr("link_folder"), Icons.ADD)
        link.clicked.connect(lambda: self.subject_id and ctl.link_folder(self.subject_id, self))
        head.addWidget(link)
        b.addLayout(head)
        self.roots = QHBoxLayout()
        self.roots.setSpacing(8)
        b.addLayout(self.roots)

        nav = QHBoxLayout()
        nav.setSpacing(4)
        self.up = IconButton(Icons.UP, tr("folder_up"), 34, 10.5)
        self.up.clicked.connect(self._go_up)
        nav.addWidget(self.up)
        self.crumbs = QHBoxLayout()
        self.crumbs.setSpacing(2)
        nav.addLayout(self.crumbs, 1)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(tr("filter_files"))
        self.filter.setClearButtonEnabled(True)
        self.filter.setFixedWidth(220)
        self.filter.textChanged.connect(lambda _: self._render())
        nav.addWidget(self.filter)
        explorer = IconButton(Icons.OPEN_FOLDER, tr("open_in_explorer"), 34, 10.5)
        explorer.clicked.connect(lambda: self.path and ctl.open_path(self.path))
        nav.addWidget(explorer)
        b.addLayout(nav)

        self.list = file_list()
        self.list.itemDoubleClicked.connect(lambda item: self._activate(item.data(Qt.ItemDataRole.UserRole)))
        b.addWidget(self.list, 1)
        self.footer = label("", 8.6, role="Faint")
        b.addWidget(self.footer)
        self.stack.addWidget(browser)
        lay.addWidget(self.stack, 1)
        self.refresh("")

    # ------------------------------------------------------------- subjects
    def refresh(self, what: str = ""):
        if what in ("", "subjects", "folders"):
            self._fill_subjects()
        if what in ("", "files", "tags", "folders", "subjects", "organizer"):
            self._show_subject()

    def _fill_subjects(self):
        current = self.subject_id
        self.subjects.blockSignals(True)
        self.subjects.clear()
        first_with_folders = None
        for s in self.ctl.db.list_subjects():
            folders = self.ctl.subject_folders(s["id"])
            if folders and first_with_folders is None:
                first_with_folders = s["id"]
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, s["id"])
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 0, 12, 0)
            h.setSpacing(10)
            h.addWidget(GlyphLabel(Icons.FOLDER, 10.5, s["color"]))
            h.addWidget(label(s["name"], 10), 1)
            h.addWidget(label(str(len(folders)), 8.8, QFont.Weight.DemiBold, "Dim"))
            item.setSizeHint(QSize(0, 40))
            self.subjects.addItem(item)
            self.subjects.setItemWidget(item, row)
            if s["id"] == current:
                self.subjects.setCurrentItem(item)
        if self.subjects.currentItem() is None and self.subjects.count():
            # start on the first subject that has folders, so the browser isn't empty
            target = next((self.subjects.item(i) for i in range(self.subjects.count())
                           if self.subjects.item(i).data(Qt.ItemDataRole.UserRole) == first_with_folders),
                          self.subjects.item(0))
            self.subjects.setCurrentItem(target)
            self.subject_id = target.data(Qt.ItemDataRole.UserRole)
        self.subjects.blockSignals(False)
        if not self.subjects.count():
            self.subject_id = None

    def _subject_selected(self, item, _prev=None):
        if item is None:
            return
        self.subject_id = item.data(Qt.ItemDataRole.UserRole)
        self.root = self.path = None
        self._show_subject()

    def _show_subject(self):
        if self.subject_id is None:
            return self._set_empty(tr("files_no_subjects"), tr("add_subject"))
        subject = self.ctl.db.get_subject(self.subject_id)
        if not subject:
            return self._set_empty(tr("files_no_subjects"), tr("add_subject"))
        folders = self.ctl.subject_folders(self.subject_id)
        if not folders:
            return self._set_empty(tr("files_no_folders", name=subject["name"]), tr("link_folder"))
        self.stack.setCurrentIndex(1)
        self.subject_title.setText(subject["name"])
        if self.root not in folders:
            self.root = folders[0]
            self.path = folders[0]
        while self.roots.count():
            w = self.roots.takeAt(0).widget()
            if w:
                w.deleteLater()
        for folder in folders:
            chip = QPushButton(Path(folder).name or folder)
            chip.setObjectName("Chip")
            chip.setCheckable(True)
            chip.setChecked(folder == self.root)
            chip.setToolTip(folder)
            chip.setFont(ui_font(9.5))
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _=False, f=folder: self._open_root(f))
            chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            chip.customContextMenuRequested.connect(lambda pos, f=folder, c=chip: self._root_menu(f, c.mapToGlobal(pos)))
            self.roots.addWidget(chip)
        self.roots.addStretch(1)
        self._render()

    def _set_empty(self, text: str, button_text: str):
        self.stack.setCurrentIndex(0)
        self.empty_state.text_label.setText(text)
        self.empty_button.setText(button_text)

    def _empty_action(self):
        if self.subject_id is None:
            self.ctl.add_subject_dialog(self)
        else:
            self.ctl.link_folder(self.subject_id, self)

    def _root_menu(self, folder: str, pos):
        from app.ui.widgets.common import make_menu
        menu = make_menu(self)
        add_action(menu, Icons.OPEN_FOLDER, tr("open_in_explorer"), lambda: self.ctl.open_path(folder))
        add_action(menu, Icons.DELETE, tr("unlink_folder"),
                   lambda: self.ctl.unlink_folder(self.subject_id, folder), danger=True)
        menu.exec(pos)

    # ---------------------------------------------------------------- browse
    def _open_root(self, folder: str):
        self.root = self.path = folder
        self.filter.clear()
        self._show_subject()

    def _go_up(self):
        if self.path and self.root and os.path.normcase(self.path) != os.path.normcase(self.root):
            self.path = str(Path(self.path).parent)
            self.filter.clear()
            self._render()

    def _activate(self, entry: dict):
        if entry["is_dir"]:
            self.path = entry["path"]
            self.filter.clear()
            self._render()
        else:
            self.ctl.open_path(entry["path"])

    def _render(self):
        if not self.path:
            return
        if not self.root or not os.path.normcase(self.path).startswith(os.path.normcase(self.root)):
            self.root = self.path  # never browse "above" the folder we started from
        self._render_crumbs()
        self.up.setEnabled(os.path.normcase(self.path) != os.path.normcase(self.root))
        clear_list(self.list)  # rows animate on hover: stop them first
        if not os.path.isdir(self.path):
            self.footer.setText(tr("folder_missing"))
            return
        entries = list_dir(self.path)
        query = self.filter.text().strip().lower()
        if query:
            entries = [e for e in entries if query in e["name"].lower()]
        lookup = tag_lookup(self.ctl.db.list_tags())
        by_path = self.ctl.db.tags_by_path()
        for entry in entries[:MAX_ROWS]:
            tags = tags_from_ids(by_path.get(entry["path"].lower()), lookup)
            row = FileRow(entry, tags)
            row.activated.connect(self._activate)
            row.menu_requested.connect(self._menu)
            add_row(self.list, row, entry)
        files = sum(1 for e in entries if not e["is_dir"])
        text = tr("folder_summary", folders=len(entries) - files, files=files)
        if len(entries) > MAX_ROWS:
            text += "  ·  " + tr("showing_first", n=MAX_ROWS)
        self.footer.setText(text + "  ·  " + tr("drop_to_copy"))

    def _render_crumbs(self):
        while self.crumbs.count():
            w = self.crumbs.takeAt(0).widget()
            if w:
                w.deleteLater()
        parts = [self.path]
        p = Path(self.path)
        while os.path.normcase(str(p)) != os.path.normcase(self.root) and p.parent != p:
            p = p.parent
            parts.insert(0, str(p))
        for i, part in enumerate(parts):
            if i:
                self.crumbs.addWidget(label("›", 10, role="Faint"))
            crumb = QPushButton(Path(part).name or part)
            crumb.setObjectName("Ghost")
            crumb.setFont(ui_font(9.8, QFont.Weight.DemiBold if i == len(parts) - 1 else QFont.Weight.Normal))
            crumb.setCursor(Qt.CursorShape.PointingHandCursor)
            crumb.clicked.connect(lambda _=False, target=part: self._jump(target))
            self.crumbs.addWidget(crumb)
        self.crumbs.addStretch(1)

    def _jump(self, target: str):
        self.path = target
        self.filter.clear()
        self._render()

    def _menu(self, entry: dict, pos):
        paths = selected_paths(self.list, entry["path"])
        if entry["is_dir"]:
            def extra(menu):
                add_action(menu, Icons.STUDY, tr("link_as_subject_folder"),
                           lambda: self.ctl.link_folder(self.subject_id, self, entry["path"]))
            file_menu(self, self.ctl, entry, paths, extra).exec(pos)
        else:
            file_menu(self, self.ctl, entry, paths).exec(pos)

    # ----------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and self.path and self.stack.currentIndex() == 1:
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        # skip files dropped back into the folder they already live in
        paths = [p for p in paths if os.path.normcase(os.path.dirname(os.path.normpath(p))) != os.path.normcase(self.path)]
        if paths:
            self.ctl.copy_into(paths, self.path)
            self._render()
        e.acceptProposedAction()

    def on_shown(self):
        self.refresh("")
