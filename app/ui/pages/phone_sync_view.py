"""
Phone > Sync & backup: photo backup (phone photos, videos, screenshots -> a laptop folder by year / month) and
synced folders (a laptop folder <-> a phone folder). The rules live in app/core/phone_sync.py.
"""
from __future__ import annotations

import os
import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QComboBox, QFileDialog, QFrame, QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from app.core.i18n import ltr, tr
from app.ui.pages.base import Page
from app.ui.theme import Icons, Palette, Theme
from app.ui.widgets.common import Card, GlyphPlate, IconButton, ToggleSwitch, button, label, primary_button

DIRECTION_KEYS = (("both", "sync_dir_both"), ("to_laptop", "sync_dir_to_laptop"), ("to_phone", "sync_dir_to_phone"))


def ago(ts: float) -> str:
    if not ts:
        return tr("sync_never")
    from app.core.i18n import relative_time
    return relative_time(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)))


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w:
            w.hide()
            w.deleteLater()
        elif item.layout():
            _clear(item.layout())


class FolderRow(QFrame):
    def __init__(self, view: "SyncView", f: dict):
        super().__init__()
        self.setObjectName("SyncRow")
        sync = view.ctl.phone_sync
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)
        lay.addWidget(GlyphPlate(Icons.FOLDER, "#14B8A6", 38), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(f["name"], 10.5, QFont.Weight.DemiBold))
        path = label(ltr(f["laptop_path"]), 8.6, role="Dim")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(path)
        if f["linked"]:
            col.addWidget(label("📱  " + (f["phone_name"] or tr("sync_phone_folder")), 8.8))
        else:
            wait = label("⚠  " + tr("sync_pick_on_phone"), 8.8, wrap=True)
            wait.setStyleSheet(f"color: {Palette.warning};")
            col.addWidget(wait)
        from app.core.phone_sync import result_text
        done = result_text(f["last_counts"]) if f.get("last_counts") else ""
        last = tr("sync_last", when=ago(f["last_sync"])) + (f"  ·  {done}" if done else "")
        col.addWidget(label(last, 8.4, role="Faint", wrap=True))
        lay.addLayout(col, 1)
        direction = QComboBox()
        for key, text in DIRECTION_KEYS:
            direction.addItem(tr(text), key)
        direction.setCurrentIndex(max(0, direction.findData(f["direction"])))
        direction.currentIndexChanged.connect(lambda _: sync.update_folder(f["id"], direction=direction.currentData()))
        lay.addWidget(direction, 0, Qt.AlignmentFlag.AlignTop)
        now = button(tr("sync_now"), Icons.SYNC, role="Ghost")
        now.setEnabled(f["linked"])
        now.clicked.connect(sync.sync_now)
        lay.addWidget(now, 0, Qt.AlignmentFlag.AlignTop)
        opener = IconButton(Icons.OPEN_FOLDER, tr("sync_open_folder"), 32, 10.5)
        opener.clicked.connect(lambda: os.startfile(f["laptop_path"]) if os.path.isdir(f["laptop_path"]) else None)
        lay.addWidget(opener, 0, Qt.AlignmentFlag.AlignTop)
        remove = IconButton(Icons.DELETE, tr("sync_remove"), 32, 10.5)
        remove.clicked.connect(lambda: view.remove(f))
        lay.addWidget(remove, 0, Qt.AlignmentFlag.AlignTop)


class SyncView(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl = ctl
        sync = ctl.phone_sync
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        outer.addWidget(Page.scrollable(content))
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 6, 6)
        lay.setSpacing(14)

        # ---- photo backup
        self.backup_card = Card(padding=18)
        head = QHBoxLayout()
        head.addWidget(GlyphPlate(Icons.CAMERA, "#F59E0B", 42))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(tr("sync_backup"), 12, QFont.Weight.DemiBold))
        col.addWidget(label(tr("sync_backup_desc"), 8.8, role="Dim", wrap=True))
        head.addLayout(col, 1)
        self.backup_switch = ToggleSwitch(sync.backup()["on"])
        self.backup_switch.toggled.connect(lambda on: sync.set_backup(on=on))
        head.addWidget(self.backup_switch)
        self.backup_card.root.addLayout(head)
        frow = QHBoxLayout()
        frow.addWidget(label(tr("sync_backup_folder"), 9, role="Dim"))
        self.backup_folder = label("", 9)
        self.backup_folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        frow.addWidget(self.backup_folder, 1)
        change = button(tr("sync_change"), Icons.EDIT, role="Ghost")
        change.clicked.connect(self._choose_backup_folder)
        frow.addWidget(change)
        open_b = button(tr("sync_open_folder"), Icons.OPEN_FOLDER, role="Ghost")
        open_b.clicked.connect(self._open_backup)
        frow.addWidget(open_b)
        self.backup_card.root.addLayout(frow)
        self.backup_status = label("", 9, wrap=True)
        self.backup_card.root.addWidget(self.backup_status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.backup_card.root.addWidget(self.progress)
        self.progress_text = label("", 8.6, role="Faint")
        self.backup_card.root.addWidget(self.progress_text)
        brow = QHBoxLayout()
        self.backup_now = button(tr("sync_backup_now"), Icons.SYNC, role="Ghost")
        self.backup_now.clicked.connect(sync.sync_now)
        brow.addWidget(self.backup_now)
        brow.addStretch(1)
        self.backup_card.root.addLayout(brow)
        lay.addWidget(self.backup_card)

        # ---- synced folders
        self.folders_card = Card(tr("sync_folders"), Icons.SYNC, padding=18)
        self.folders_card.header.addStretch(1)
        add = primary_button(tr("sync_add_folder"), Icons.ADD)
        add.clicked.connect(self.add_folder)
        self.folders_card.header.addWidget(add)
        self.folders_card.root.addWidget(label(tr("sync_folders_desc"), 8.8, role="Dim", wrap=True))
        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        self.folders_card.root.addLayout(self.rows)
        lay.addWidget(self.folders_card)
        lay.addStretch(1)
        self.setStyleSheet(f"#SyncRow {{ background: rgba(127,127,127,0.07); border-radius: 14px; }}")
        sync.changed.connect(self._changed)
        ctl.phone_hub.changed.connect(lambda what: what == "state" and self._changed())
        self.refresh()

    # ---------------------------------------------------------------- actions
    def _choose_backup_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("sync_backup_folder"), self.ctl.phone_sync.backup()["folder"])
        if folder:
            self.ctl.phone_sync.set_backup(folder=os.path.normpath(folder))

    def _open_backup(self) -> None:
        folder = self.ctl.phone_sync.backup()["folder"]
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("sync_add_folder"), os.path.expanduser("~"))
        if folder:
            self.ctl.phone_sync.add_folder(folder)
            self.ctl.notify(tr("sync_added"), "info")

    def remove(self, f: dict) -> None:
        from app.ui.dialogs.base import ConfirmDialog
        if ConfirmDialog.ask(tr("sync_remove"), tr("sync_remove_msg", name=f["name"]), tr("sync_remove"), self):
            self.ctl.phone_sync.remove_folder(f["id"])

    # ---------------------------------------------------------------- refresh
    def _changed(self) -> None:
        try:
            self.refresh()
        except RuntimeError:
            pass

    def refresh(self) -> None:
        sync = self.ctl.phone_sync
        b = sync.backup()
        self.backup_switch.set_checked_silently(bool(b["on"]))
        self.backup_folder.setText(ltr(b["folder"]))
        st = sync.stats()
        phone = self.ctl.phone_hub.state()
        if b["on"] and phone and not phone.get("media_access", True):
            text, color = tr("sync_backup_need_access"), Palette.warning
        elif st["count"]:
            text = tr("sync_backup_count", n=st["count"]) + "  ·  " + tr("sync_last", when=ago(st["last"]))
            if st["last_new"]:
                text += "  ·  " + tr("sync_backup_new", n=st["last_new"])
            color = Palette.text
        else:
            text, color = (tr("sync_backup_waiting") if b["on"] else tr("sync_backup_off")), Palette.text_dim
        self.backup_status.setText(text)
        self.backup_status.setStyleSheet(f"color: {color};")
        pr = sync.progress
        live = bool(pr) and not pr.get("finished") and time.time() - pr.get("at", 0) < 120 and pr.get("total")
        self.progress.setVisible(bool(live))
        self.progress_text.setVisible(bool(live))
        if live:
            self.progress.setRange(0, pr["total"])
            self.progress.setValue(pr["done"])
            what = tr("sync_progress_backup") if pr["kind"] == "backup" else tr("sync_progress_sync")
            self.progress_text.setText(f"{what}  {pr['done']} / {pr['total']}  ·  {pr['name']}")
        self.backup_now.setEnabled(bool(b["on"]))
        _clear(self.rows)
        folders = sync.folders()
        for f in folders:
            self.rows.addWidget(FolderRow(self, f))
        if not folders:
            self.rows.addWidget(label(tr("sync_no_folders"), 9.5, role="Faint", wrap=True))
