"""Archive tab: everything archived (search / filter / restore) + 'old files' suggestions."""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app.core.archive import CATEGORIES
from app.core.files import format_mtime, human_size
from app.core.i18n import ltr, tr
from app.ui.animations import clear_list
from app.ui.pages.files.common import add_row, file_list
from app.ui.theme import Icons, Theme, ui_font
from app.ui.widgets.common import (Card, EmptyState, GlyphLabel, IconButton, add_action, button, label, make_menu,
                                   primary_button)
from app.ui.widgets.files import FileRow


class ArchiveView(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl, self.page = ctl, page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        head = Card(padding=18)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(GlyphLabel(Icons.ARCHIVE, 16, Theme.accent()))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(tr("archive_title_page"), 14, QFont.Weight.DemiBold))
        self.root_label = label("", 9, role="Dim")
        col.addWidget(self.root_label)
        row.addLayout(col, 1)
        self.stats = label("", 9.5, role="Dim")
        row.addWidget(self.stats)
        change = button(tr("change_folder"), Icons.OPEN_FOLDER)
        change.clicked.connect(lambda: ctl.choose_archive_root(self))
        row.addWidget(change)
        open_root = button(tr("open_in_explorer"), Icons.OPEN_FOLDER)
        open_root.clicked.connect(lambda: ctl.open_explorer(str(ctl.archive_root())))
        row.addWidget(open_root)
        head.root.addLayout(row)
        sem_row = QHBoxLayout()
        sem_row.addWidget(label(tr("current_semester"), 9.5))
        self.semester_edit = QLineEdit(ctl.current_semester())
        self.semester_edit.setFixedWidth(200)
        self.semester_edit.editingFinished.connect(lambda: ctl.db.set_setting("semester", self.semester_edit.text().strip()))
        sem_row.addWidget(self.semester_edit)
        sem_row.addStretch(1)
        review = primary_button(tr("find_old_files"), Icons.CLOCK)
        review.clicked.connect(self._suggest)
        sem_row.addWidget(review)
        add = button(tr("archive_files_btn"), Icons.ADD)
        add.clicked.connect(self._pick_files)
        sem_row.addWidget(add)
        head.root.addLayout(sem_row)
        lay.addWidget(head)

        body = Card(padding=16)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("search_archive"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh("archive"))
        filters.addWidget(self.search, 1)
        self.subject = QComboBox()
        self.category = QComboBox()
        self.semester = QComboBox()
        for combo in (self.subject, self.category, self.semester):
            combo.setFixedWidth(170)
            combo.currentIndexChanged.connect(lambda _: self.refresh("archive"))
            filters.addWidget(combo)
        body.root.addLayout(filters)
        self.mode_label = label("", 9.5, QFont.Weight.DemiBold)
        body.root.addWidget(self.mode_label)
        self.list = file_list()
        body.root.addWidget(self.list, 1)
        self.empty = EmptyState(Icons.ARCHIVE, tr("archive_empty"))
        body.root.addWidget(self.empty)
        self.bulk = primary_button(tr("archive_selected"), Icons.ARCHIVE)
        self.bulk.clicked.connect(self._archive_selected)
        self.bulk.hide()
        body.root.addWidget(self.bulk, 0, Qt.AlignmentFlag.AlignRight if not self.isRightToLeft() else Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(body, 1)
        self._suggesting = False
        self._fill_filters()

    # --------------------------------------------------------------- filters
    def _fill_filters(self):
        for combo in (self.subject, self.category, self.semester):
            combo.blockSignals(True)
            combo.clear()
        self.subject.addItem(tr("all_subjects"), None)
        for s in self.ctl.db.list_subjects():
            self.subject.addItem(s["name"], s["id"])
        self.category.addItem(tr("all_categories"), None)
        for c in CATEGORIES:
            self.category.addItem(tr(f"cat_{c}"), c)
        self.semester.addItem(tr("all_semesters"), None)
        for sem in self.ctl.db.archive_semesters():
            self.semester.addItem(sem, sem)
        for combo in (self.subject, self.category, self.semester):
            combo.blockSignals(False)

    # ----------------------------------------------------------------- lists
    def refresh(self, what: str = ""):
        if what not in ("", "archive", "subjects"):
            return
        if what in ("", "subjects"):
            self._fill_filters()
        self._suggesting = False
        self.bulk.hide()
        self.root_label.setText(ltr(str(self.ctl.archive_root())))
        entries = self.ctl.db.list_archive(self.search.text().strip(), self.subject.currentData(),
                                           self.category.currentData(), self.semester.currentData())
        total = sum(e["size"] for e in entries)
        self.stats.setText(tr("archive_stats", n=len(entries), size=ltr(human_size(total))))
        self.mode_label.setText(tr("archived_items"))
        clear_list(self.list)
        for e in entries:
            exists = os.path.exists(e["archive_path"])
            entry = {"path": e["archive_path"], "name": e["name"], "is_dir": os.path.isdir(e["archive_path"]),
                     "missing": not exists, "entry": e}
            meta = " · ".join(x for x in (e["subject_name"] or "", tr(f"cat_{e['category']}"), e["semester"],
                                          tr(f"mode_{e['mode']}"), ltr(human_size(e["size"])),
                                          ltr(e["archived_at"][:10])) if x)
            if e["note"]:
                meta += f"  —  {e['note']}"
            restore = button(tr("restore"), Icons.UNDO)
            restore.clicked.connect(lambda _=False, entry_id=e["id"]: self.ctl.restore_archived(entry_id))
            row = FileRow(entry, meta=meta, extra=restore)
            row.activated.connect(lambda en: self.ctl.open_path(en["path"]))
            row.menu_requested.connect(self._menu)
            add_row(self.list, row, entry)
        self.list.setVisible(bool(entries))
        self.empty.setVisible(not entries)

    def _menu(self, entry: dict, pos):
        e = entry.get("entry")
        if not e:
            return
        menu = make_menu(self)
        add_action(menu, Icons.CHEVRON, tr("open"), lambda: self.ctl.open_path(e["archive_path"]))
        add_action(menu, Icons.OPEN_FOLDER, tr("open_location"),
                   lambda: self.ctl.open_explorer(os.path.dirname(e["archive_path"]), select=e["archive_path"]))
        add_action(menu, Icons.UNDO, tr("restore"), lambda: self.ctl.restore_archived(e["id"]))
        add_action(menu, Icons.COPY, tr("copy_path"), lambda: self.ctl.copy_text(e["archive_path"]))
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("forget_entry"), lambda: self.ctl.forget_archived(e["id"]), danger=True)
        menu.exec(pos)

    # ------------------------------------------------------------ suggestions
    def _suggest(self):
        """Files untouched for 6+ months in subject folders: candidates to archive."""
        files = self.ctl.stale_files()
        self._suggesting = True
        self.mode_label.setText(tr("old_files_found", n=len(files)))
        clear_list(self.list)
        for f in files:
            entry = {"path": f["path"], "name": f["name"], "is_dir": False}
            meta = ltr(human_size(f["size"])) + "  ·  " + tr("last_changed", date=ltr(format_mtime(f["mtime"])[:10]))
            row = FileRow(entry, meta=meta)
            row.activated.connect(lambda en: self.ctl.open_path(en["path"]))
            item = add_row(self.list, row, entry)
            item.setSelected(True)
        self.list.setVisible(bool(files))
        self.empty.setVisible(not files)
        self.bulk.setVisible(bool(files))

    def _archive_selected(self):
        paths = [i.data(Qt.ItemDataRole.UserRole)["path"] for i in self.list.selectedItems()]
        if paths:
            self.ctl.archive_dialog(paths, self)

    def _pick_files(self):
        from PyQt6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(self, tr("archive_files_btn"))
        if paths:
            self.ctl.archive_dialog([os.path.normpath(p) for p in paths], self)

    def on_shown(self):
        if not self._suggesting:
            self.refresh("archive")
