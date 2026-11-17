"""
Downloads organizer: lists new files in Downloads with a suggested subject folder.
The user confirms each move (or "Move all suggested"); every move can be undone,
and every confirmed move teaches the organizer for next time.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.core.files import human_size
from app.core.i18n import ltr, tr
from app.core.organizer import age_days
from app.ui.animations import stagger_in
from app.ui.animations import clear_list
from app.ui.pages.files.common import add_row, file_list, file_menu
from app.ui.theme import Icons, Palette, Theme, ui_font
from app.ui.widgets.common import (Card, EmptyState, GlyphLabel, IconButton, ToggleSwitch, add_action, button,
                                   label, primary_button)
from app.ui.widgets.files import FileRow, color_icon


def reason_text(reason: str) -> str:
    kind, _, value = reason.partition(":")
    return tr(f"reason_{kind}", value=value) if kind else ""


class SuggestionControls(QWidget):
    """Destination picker + Move / Ignore buttons shown at the end of a row."""

    def __init__(self, view, suggestion, destinations: list[tuple[int, str, str, str]]):
        super().__init__()
        self.view, self.s = view, suggestion
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        if suggestion.reason:
            why = label(reason_text(suggestion.reason), 8.4, role="Faint")
            lay.addWidget(GlyphLabel(Icons.LIGHTNING, 8.5, Theme.accent()))
            lay.addWidget(why)
        self.combo = QComboBox()
        self.combo.setFixedWidth(240)
        self.combo.addItem(tr("choose_destination"), None)
        for sid, name, color, folder in destinations:
            self.combo.addItem(color_icon(color), f"{name} — {Path(folder).name}", (sid, folder))
        if suggestion.subject_id is not None:
            index = next((i for i in range(self.combo.count())
                          if self.combo.itemData(i) == (suggestion.subject_id, suggestion.folder)), 0)
            self.combo.setCurrentIndex(index)
        lay.addWidget(self.combo)
        self.move = QPushButton(tr("move"))
        self.move.setObjectName("Primary")
        self.move.setFont(ui_font(9.5, QFont.Weight.DemiBold))
        self.move.clicked.connect(self._move)
        self.combo.currentIndexChanged.connect(lambda _: self.move.setEnabled(self.combo.currentData() is not None))
        self.move.setEnabled(self.combo.currentData() is not None)
        lay.addWidget(self.move)
        ignore = IconButton(Icons.CLOSE, tr("ignore"), 32, 9)
        ignore.clicked.connect(lambda: view.ctl.ignore_download(suggestion.path))
        lay.addWidget(ignore)

    def target(self):
        return self.combo.currentData()

    def _move(self):
        target = self.target()
        if target:
            self.view.ctl.organize(self.s.path, target[0], target[1])


class OrganizerView(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl, self.page = ctl, page
        self.controls: list[SuggestionControls] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)

        head = Card(padding=18)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(GlyphLabel(Icons.DOWNLOAD, 16, Theme.accent()))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(tr("downloads_title"), 14, QFont.Weight.DemiBold))
        self.folder_label = label("", 9, role="Dim")
        col.addWidget(self.folder_label)
        row.addLayout(col, 1)
        change = button(tr("change_folder"), Icons.OPEN_FOLDER)
        change.clicked.connect(lambda: ctl.choose_organizer_folder(self))
        row.addWidget(change)
        self.undo = button(tr("undo_move"), Icons.UNDO)
        self.undo.clicked.connect(ctl.undo_last_move)
        row.addWidget(self.undo)
        scan = button(tr("scan_now"), Icons.SYNC)
        scan.clicked.connect(lambda: self.refresh("organizer"))
        row.addWidget(scan)
        self.move_all = primary_button(tr("move_all"), Icons.ACCEPT)
        self.move_all.clicked.connect(self._move_all)
        row.addWidget(self.move_all)
        head.root.addLayout(row)
        watch_row = QHBoxLayout()
        watch_row.addWidget(label(tr("watch_downloads"), 9.5))
        watch_row.addWidget(label(tr("watch_downloads_desc"), 8.8, role="Faint"), 1)
        watch = ToggleSwitch(ctl.db.get_setting("watch_downloads", True))
        watch.toggled.connect(ctl.set_watch_downloads)
        watch_row.addWidget(watch)
        head.root.addLayout(watch_row)
        lay.addWidget(head)

        body = Card(padding=16)
        self.summary = label("", 9.5, role="Dim")
        body.root.addWidget(self.summary)
        self.list = file_list()
        self.list.setSelectionMode(self.list.SelectionMode.NoSelection)
        body.root.addWidget(self.list, 1)
        self.empty = EmptyState(Icons.ACCEPT, tr("downloads_tidy"))
        body.root.addWidget(self.empty)
        lay.addWidget(body, 1)
        self._loaded = False

    def refresh(self, what: str = "", animate: bool = True):
        if what not in ("organizer", "subjects", "folders") or not self.isVisible():
            return  # scanning touches the disk: only while visible (on_shown rescans)
        folder = self.ctl.organizer_folder()
        self.folder_label.setText(ltr(folder))
        last = self.ctl.db.last_move()
        self.undo.setEnabled(last is not None)
        self.undo.setToolTip(Path(last["dst"]).name if last else "")
        destinations = self.ctl.destination_choices()
        suggestions = self.ctl.organizer_suggestions()
        clear_list(self.list)  # rows animate on hover: stop them first
        self.controls = []
        rows = []
        for s in suggestions[:150]:
            age = age_days(s.mtime)
            meta = ltr(human_size(s.size)) + "  ·  " + (tr("age_today") if age == 0 else tr("age_days", n=age))
            controls = SuggestionControls(self, s, destinations)
            entry = {"path": s.path, "name": s.name, "is_dir": False}
            row = FileRow(entry, meta=meta, extra=controls)
            row.activated.connect(lambda e: self.ctl.open_path(e["path"]))
            row.menu_requested.connect(self._menu)
            add_row(self.list, row, entry)
            self.controls.append(controls)
            rows.append(row)
        matched = sum(1 for s in suggestions if s.subject_id is not None)
        self.summary.setText(tr("downloads_summary", n=len(suggestions), matched=matched)
                             if suggestions else "")
        self.move_all.setText(tr("move_all") + (f" ({matched})" if matched else ""))
        self.move_all.setEnabled(matched > 0)
        self.list.setVisible(bool(suggestions))
        self.empty.setVisible(not suggestions)
        if not destinations:
            self.summary.setText(tr("organizer_no_destinations"))
        if animate and rows and not self._loaded:
            self._loaded = True
            QTimer.singleShot(0, lambda: stagger_in(rows[:10], step=40, dy=10, duration=300))

    def _menu(self, entry: dict, pos):
        def extra(menu):
            add_action(menu, Icons.CLOSE, tr("ignore"), lambda: self.ctl.ignore_download(entry["path"]))
        file_menu(self, self.ctl, entry, [entry["path"]], extra).exec(pos)

    def _move_all(self):
        moves = [(c.s.path, *c.target()) for c in self.controls if c.target() is not None]
        self.ctl.organize_many(moves)

    def on_shown(self):
        self.refresh("organizer")
