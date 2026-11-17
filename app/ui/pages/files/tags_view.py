"""Tagged files: quick access to the files you marked, filtered by tag and search."""
from __future__ import annotations

import os

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from app.core.i18n import ltr, tr
from app.ui.animations import clear_list
from app.ui.pages.files.common import add_row, file_list, file_menu, selected_paths
from app.ui.theme import Icons, Theme
from app.ui.widgets.common import (Card, EmptyState, GlyphLabel, IconButton, add_action, label, make_menu,
                                   primary_button)
from app.ui.widgets.files import FileRow, tag_lookup, tags_from_ids


class TagsView(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl, self.page = ctl, page
        self.tag_id: int | None = None
        self.setAcceptDrops(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(18)

        side = Card(tr("tags"), Icons.TAG, padding=16)
        side.setFixedWidth(280)
        new = IconButton(Icons.ADD, tr("new_tag"), 30, 10)
        new.clicked.connect(lambda: ctl.new_tag(self))
        side.add_header_widget(new)
        self.tags = QListWidget()
        self.tags.itemClicked.connect(self._tag_clicked)
        self.tags.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tags.customContextMenuRequested.connect(self._tag_menu)
        side.root.addWidget(self.tags, 1)
        side.root.addWidget(label(tr("tags_hint"), 8.6, role="Faint", wrap=True))
        lay.addWidget(side)

        main = Card(padding=18)
        bar = QHBoxLayout()
        self.title = label("", 15, QFont.Weight.DemiBold)
        bar.addWidget(self.title)
        bar.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("search_files"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(lambda _: self._render())
        bar.addWidget(self.search)
        add = primary_button(tr("add_files"), Icons.ADD)
        add.clicked.connect(self._add_files)
        bar.addWidget(add)
        main.root.addLayout(bar)
        self.list = file_list()
        self.list.itemDoubleClicked.connect(lambda item: ctl.open_path(item.data(Qt.ItemDataRole.UserRole)["path"]))
        main.root.addWidget(self.list, 1)
        self.empty = EmptyState(Icons.TAG, tr("tagged_empty"))
        main.root.addWidget(self.empty)
        lay.addWidget(main, 1)
        self.refresh("")

    def refresh(self, what: str = ""):
        if what not in ("", "files", "tags", "subjects", "organizer"):
            return
        tags = self.ctl.db.list_tags()
        if self.tag_id not in {t["id"] for t in tags}:
            self.tag_id = None
        self.tags.clear()
        total = len(self.ctl.db.list_tracked())
        for key, name, count, color in [(None, tr("all_files"), total, Theme.accent())] + \
                [(t["id"], t["name"], t["files"], t["color"]) for t in tags]:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, key)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 0, 12, 0)
            h.setSpacing(10)
            h.addWidget(GlyphLabel(Icons.TAG if key else Icons.DOCUMENT, 10, color))
            h.addWidget(label(name, 10), 1)
            h.addWidget(label(str(count), 8.8, QFont.Weight.DemiBold, "Dim"))
            item.setSizeHint(QSize(0, 38))
            self.tags.addItem(item)
            self.tags.setItemWidget(item, row)
            if key == self.tag_id:
                self.tags.setCurrentItem(item)
        self._render()

    def _tag_clicked(self, item):
        self.tag_id = item.data(Qt.ItemDataRole.UserRole)
        self._render()

    def _render(self):
        tags = self.ctl.db.list_tags()
        lookup = tag_lookup(tags)
        self.title.setText(lookup[self.tag_id]["name"] if self.tag_id in lookup else tr("all_files"))
        clear_list(self.list)  # rows animate on hover: stop them first
        files = self.ctl.db.list_tracked(self.tag_id, self.search.text().strip())
        for f in files:
            entry = {"path": f["path"], "name": f["name"], "is_dir": os.path.isdir(f["path"]),
                     "id": f["id"], "missing": not os.path.exists(f["path"])}
            meta = ltr(f["path"]) if not entry["missing"] else tr("file_missing") + " · " + ltr(f["path"])
            if f["subject_name"]:
                meta = f"{f['subject_name']}  ·  {meta}"
            row = FileRow(entry, tags_from_ids(f["tag_ids"], lookup), meta=meta)
            row.activated.connect(lambda e: self.ctl.open_path(e["path"]))
            row.menu_requested.connect(self._file_menu)
            add_row(self.list, row, entry)
        self.list.setVisible(bool(files))
        self.empty.setVisible(not files)

    def _file_menu(self, entry: dict, pos):
        paths = selected_paths(self.list, entry["path"])

        def extra(menu):
            add_action(menu, Icons.DELETE, tr("untrack"), lambda: self.ctl.untrack_paths(paths), danger=True)
        file_menu(self, self.ctl, entry, paths, extra).exec(pos)

    def _tag_menu(self, pos):
        item = self.tags.itemAt(pos)
        tag_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if tag_id is None:
            return
        menu = make_menu(self)
        add_action(menu, Icons.EDIT, tr("rename"), lambda: self.ctl.rename_tag(tag_id, self))
        add_action(menu, Icons.COLOR, tr("change_color"), lambda: self.ctl.recolor_tag(tag_id))
        add_action(menu, Icons.DELETE, tr("delete"), lambda: self.ctl.delete_tag(tag_id, self), danger=True)
        menu.exec(self.tags.viewport().mapToGlobal(pos))

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, tr("add_files"))
        if paths:
            self.ctl.tag_paths([os.path.normpath(p) for p in paths], self,
                               preselect=[self.tag_id] if self.tag_id else None)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [os.path.normpath(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.ctl.tag_paths(paths, self, preselect=[self.tag_id] if self.tag_id else None)
        e.acceptProposedAction()

    def on_shown(self):
        self.refresh("")
