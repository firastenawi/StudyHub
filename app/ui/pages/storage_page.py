"""
Storage page: see your drives and free up space.

  Clean up      Recycle Bin, temp files, browser caches, crash dumps, thumbnails, developer caches,
                old Study Hub backups, leftover note attachments — sizes are measured first, nothing is
                removed until you confirm; weekly automatic clean for the safe ones
  Large files   biggest files in your user folders -> Recycle Bin (recoverable)
  Old downloads things in Downloads you haven't touched for months -> Recycle Bin
  Study Hub     what the app itself uses, how many backups to keep, compact the database

All scanning / cleaning runs in a background thread so the window never freezes.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QListWidget, QListWidgetItem,
                             QScrollArea, QFrame, QVBoxLayout, QWidget)

from app.core import cleanup
from app.core.cleanup import Cleaner, human
from app.core.i18n import ltr, tr
from app.ui.animations import AnimatedStack
from app.ui.pages.base import Page
from app.ui.theme import Icons, Palette, Theme, ui_font, ink, ink_css
from app.ui.widgets.common import (Card, EmptyState, GlyphLabel, GlyphPlate, IconButton, ToggleSwitch, button, label,
                                   primary_button)
from app.ui.widgets.segmented import SegmentedTabs

ROLE = Qt.ItemDataRole.UserRole
COLORS = {"recycle_bin": "#F87171", "temp": "#FBBF24", "browser_cache": "#60A5FA", "crash_dumps": "#F97316",
          "thumbnails": "#A78BFA", "dev_cache": "#22D3EE", "backups": "#34D399", "orphans": "#F472B6"}


class _Bg(QObject):
    """Runs a function in a thread; the callback runs back in the GUI thread."""
    done = pyqtSignal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._callbacks: dict[int, callable] = {}
        self._next = 0
        self.done.connect(self._finish)

    def run(self, fn, callback) -> None:
        self._next += 1
        job = self._next
        self._callbacks[job] = callback

        def work():
            try:
                result = fn()
            except Exception as exc:  # report, never crash the app
                result = exc
            self.done.emit(job, result)
        threading.Thread(target=work, daemon=True).start()

    def _finish(self, job: int, result) -> None:
        callback = self._callbacks.pop(job, None)
        if callback:
            try:
                callback(result)
            except RuntimeError:  # page rebuilt meanwhile
                pass


class DriveBar(QWidget):
    def __init__(self, used_ratio: float, parent=None):
        super().__init__(parent)
        self.ratio = max(0.0, min(1.0, used_ratio))
        self.setFixedHeight(8)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink(22 / 255))
        p.drawRoundedRect(r, 4, 4)
        color = Palette.danger if self.ratio > 0.9 else Palette.warning if self.ratio > 0.75 else Theme.accent()
        fill = QRectF(r)
        fill.setWidth(r.width() * self.ratio)
        if self.layoutDirection() == Qt.LayoutDirection.RightToLeft:
            fill.moveRight(r.right())
        p.setBrush(QColor(color))
        p.drawRoundedRect(fill, 4, 4)


class DriveCard(Card):
    def __init__(self, d: dict):
        super().__init__(padding=14)
        self.setMinimumWidth(240)
        top = QHBoxLayout()
        top.addWidget(GlyphLabel(Icons.DRIVE, 14, Theme.accent2()))
        name = f"{d['label'] or tr('local_disk')} ({d['root'][:2]})"
        top.addWidget(label(name, 10.5, QFont.Weight.DemiBold), 1)
        if d["system"]:
            top.addWidget(label(tr("system_drive"), 8.4, role="Faint"))
        self.root.addLayout(top)
        self.root.addWidget(DriveBar(d["used"] / d["total"] if d["total"] else 0))
        self.root.addWidget(label(tr("drive_free", free=ltr(human(d["free"])), total=ltr(human(d["total"]))), 9,
                                  role="Dim"))


class CategoryRow(QFrame):
    def __init__(self, cat, page):
        super().__init__()
        self.cat, self.page = cat, page
        self.size = 0
        self.setObjectName("CatRow")
        self.setStyleSheet(f"QFrame#CatRow {{ background: {ink_css(0.03)}; border-radius: 14px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 12, 10)
        lay.setSpacing(12)
        self.check = QCheckBox()
        self.check.setChecked(cat.default_on)
        self.check.toggled.connect(lambda _: page.update_total())
        lay.addWidget(self.check)
        lay.addWidget(GlyphPlate(cat.glyph, COLORS[cat.key], 40))
        col = QVBoxLayout()
        col.setSpacing(1)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label(tr(f"clean_{cat.key}"), 10.6, QFont.Weight.DemiBold))
        badge = label(tr("badge_permanent") if cat.permanent else tr("badge_recycle"), 8)
        color = Palette.warning if cat.permanent else Palette.success
        badge.setStyleSheet(f"color: {color}; border: 1px solid {color}; border-radius: 7px; padding: 0 6px;")
        head.addWidget(badge)
        head.addStretch(1)
        col.addLayout(head)
        self.desc = label(tr(f"clean_{cat.key}_desc"), 8.8, role="Dim", wrap=True)
        col.addWidget(self.desc)
        self.note = label("", 8.6)
        self.note.setStyleSheet(f"color: {Palette.warning};")
        self.note.hide()
        col.addWidget(self.note)
        lay.addLayout(col, 1)
        self.size_label = label(tr("scanning"), 11, QFont.Weight.DemiBold)
        self.size_label.setMinimumWidth(90)
        self.size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.size_label)
        if page.cleaner.paths(cat.key):
            open_btn = IconButton(Icons.OPEN_FOLDER, tr("open_folder_short"), 32, 9.5)
            open_btn.clicked.connect(self._open)
            lay.addWidget(open_btn)
        self.clean_btn = button(tr("clean_one"), role="Ghost")
        self.clean_btn.clicked.connect(lambda: page.clean([cat.key]))
        lay.addWidget(self.clean_btn)

    def _open(self) -> None:
        for p in self.page.cleaner.paths(self.cat.key):
            if Path(p).is_dir():
                os.startfile(p)
                break

    def set_result(self, size: int, count: int, blocked: list[str]) -> None:
        self.size = size
        self.size_label.setText(ltr(human(size)) if size else tr("nothing_to_clean"))
        self.size_label.setStyleSheet(f"color: {COLORS[self.cat.key] if size else Palette.text_faint};")
        self.clean_btn.setEnabled(bool(size) and not blocked)
        self.note.setVisible(bool(blocked))
        if blocked:
            names = ", ".join(b.replace(".exe", "").capitalize() for b in blocked)
            self.note.setText(tr("close_first", apps=names))

    def set_busy(self, text: str) -> None:
        self.size_label.setText(text)
        self.size_label.setStyleSheet(f"color: {Palette.text_dim};")
        self.clean_btn.setEnabled(False)


class FileList(QWidget):
    """Large files / old downloads: checkable rows, select all, send selected to the Recycle Bin."""

    def __init__(self, page, kind: str):
        super().__init__()
        self.page, self.kind = page, kind
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        bar = QHBoxLayout()
        bar.addWidget(label(tr(f"{kind}_threshold"), 9.5, role="Dim"))
        self.combo = QComboBox()
        options = ((100, "100 MB"), (250, "250 MB"), (500, "500 MB"), (1024, "1 GB")) if kind == "large" else \
                  ((30, tr("days_n", n=30)), (90, tr("days_n", n=90)), (180, tr("days_n", n=180)), (365, tr("year_1")))
        for value, text in options:
            self.combo.addItem(ltr(text), value)
        self.combo.setCurrentIndex(1)
        bar.addWidget(self.combo)
        scan = button(tr("scan_now"), Icons.SEARCH, role="Ghost")
        scan.clicked.connect(self.scan)
        bar.addWidget(scan)
        bar.addStretch(1)
        self.status = label("", 9, role="Faint")
        bar.addWidget(self.status)
        lay.addLayout(bar)
        self.list = QListWidget()
        self.list.setObjectName("CleanList")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        lay.addWidget(self.list, 1)
        self.empty = EmptyState(Icons.DRIVE, tr(f"{kind}_hint"))
        lay.addWidget(self.empty)
        foot = QHBoxLayout()
        self.all = QCheckBox(tr("select_all"))
        self.all.toggled.connect(self._select_all)
        foot.addWidget(self.all)
        foot.addStretch(1)
        self.total = label("", 9.5, QFont.Weight.DemiBold)
        foot.addWidget(self.total)
        self.recycle = primary_button(tr("to_recycle_bin"), Icons.DELETE)
        self.recycle.clicked.connect(self._recycle)
        foot.addWidget(self.recycle)
        lay.addLayout(foot)
        self.items: list[dict] = []
        self.checks: list[QCheckBox] = []
        self._update_total()

    def scan(self) -> None:
        value = self.combo.currentData()
        self.status.setText(tr("scanning"))
        self.list.clear()
        self.checks = []
        if self.kind == "large":
            fn = lambda: cleanup.large_files(value * 1024 * 1024)
        else:
            fn = lambda: cleanup.old_downloads(value)
        self.page.bg.run(fn, self._show)

    def _show(self, items) -> None:
        if isinstance(items, Exception):
            self.status.setText(str(items))
            return
        self.items = items
        self.list.clear()
        self.checks = []
        for it in items:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 4, 10, 4)
            h.setSpacing(10)
            box = QCheckBox()
            box.toggled.connect(lambda _: self._update_total())
            h.addWidget(box)
            self.checks.append(box)
            h.addWidget(GlyphLabel(Icons.FOLDER if it.get("folder") else Icons.DOCUMENT, 11, Theme.accent2()))
            col = QVBoxLayout()
            col.setSpacing(0)
            p = Path(it["path"])
            col.addWidget(label(p.name, 10, QFont.Weight.Medium))
            meta = label(ltr(str(p.parent)) + "  ·  " + ltr(datetime.fromtimestamp(it["mtime"]).strftime("%Y-%m-%d")),
                         8.4, role="Faint")
            col.addWidget(meta)
            h.addLayout(col, 1)
            h.addWidget(label(ltr(human(it["size"])), 10, QFont.Weight.DemiBold))
            reveal = IconButton(Icons.OPEN_FOLDER, tr("open_location"), 28, 9)
            reveal.clicked.connect(lambda _=False, path=it["path"]: self.page.ctl.reveal_path(path))
            h.addWidget(reveal)
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 52))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        self.status.setText(tr("found_n", n=len(items)))
        self.empty.setVisible(not items)
        self.list.setVisible(bool(items))
        self._update_total()

    def _select_all(self, on: bool) -> None:
        for box in self.checks:
            box.setChecked(on)

    def _selected(self) -> list[dict]:
        return [it for it, box in zip(self.items, self.checks) if box.isChecked()]

    def _update_total(self) -> None:
        sel = self._selected()
        size = sum(it["size"] for it in sel)
        self.total.setText(tr("selected_size", n=len(sel), size=ltr(human(size))) if sel else "")
        self.recycle.setEnabled(bool(sel))

    def _recycle(self) -> None:
        from app.core import fileops
        from app.ui.dialogs.base import ConfirmDialog
        sel = self._selected()
        if not sel:
            return
        size = sum(it["size"] for it in sel)
        if not ConfirmDialog.ask(tr("to_recycle_bin"), tr("recycle_confirm", n=len(sel), size=ltr(human(size))),
                                 tr("to_recycle_bin"), self):
            return
        if fileops.recycle([it["path"] for it in sel]):
            self.page.ctl.notify(tr("moved_to_recycle", size=ltr(human(size))), "success")
        self.scan()
        self.page.scan_category("recycle_bin")
        self.page.refresh_drives()


class StoragePage(Page):
    def __init__(self, ctl, main_window):
        super().__init__(ctl, main_window)
        self.bg = _Bg(self)
        self.rows: dict[str, CategoryRow] = {}
        self.cleaner = self._make_cleaner()
        self._busy = False
        self._scanned_once = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 24, 30, 24)
        outer.setSpacing(16)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(tr("nav_storage"), 22, QFont.Weight.DemiBold))
        titles.addWidget(label(tr("storage_subtitle"), 10, role="Dim"))
        header.addLayout(titles)
        header.addStretch(1)
        self.freed_label = label("", 9.5, role="Dim")
        header.addWidget(self.freed_label)
        rescan = IconButton(Icons.REFRESH, tr("scan_again"), 36, 11)
        rescan.clicked.connect(self.scan_all)
        header.addWidget(rescan)
        self.clean_all = primary_button(tr("clean_selected"), Icons.ACCEPT)
        self.clean_all.clicked.connect(lambda: self.clean([k for k, r in self.rows.items() if r.check.isChecked()]))
        header.addWidget(self.clean_all)
        outer.addLayout(header)

        self.drives_row = QHBoxLayout()
        self.drives_row.setSpacing(12)
        outer.addLayout(self.drives_row)

        self.tabs = SegmentedTabs([("cleanup", Icons.ACCEPT, tr("tab_cleanup")),
                                   ("large", Icons.DOCUMENT, tr("tab_large_files")),
                                   ("downloads", Icons.DOWNLOAD, tr("tab_old_downloads")),
                                   ("app", Icons.EDUCATION, tr("tab_app_data"))], "cleanup")
        self.tabs.changed.connect(self.show_tab)
        trow = QHBoxLayout()
        trow.addWidget(self.tabs)
        trow.addStretch(1)
        outer.addLayout(trow)
        self.stack = AnimatedStack()
        self.views = {"cleanup": self._build_cleanup(), "large": FileList(self, "large"),
                      "downloads": FileList(self, "downloads"), "app": self._build_app()}
        for v in self.views.values():
            self.stack.addWidget(v)
        outer.addWidget(self.stack, 1)
        self.current = "cleanup"
        self.refresh_drives()
        self._update_freed()

    def _make_cleaner(self) -> Cleaner:
        # snapshots from the GUI thread: the scan threads never touch the database
        note_ids = {r["id"] for r in self.ctl.db.query("SELECT id FROM notes")}
        keep = int(self.ctl.db.get_setting("backups_keep", 7))
        return Cleaner(lambda: note_ids, lambda: keep)

    # ------------------------------------------------------------ cleanup tab
    def _build_cleanup(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(8)
        for cat in Cleaner.CATEGORIES:
            row = CategoryRow(cat, self)
            self.rows[cat.key] = row
            lay.addWidget(row)
        auto = QHBoxLayout()
        auto.setContentsMargins(4, 10, 4, 0)
        col = QVBoxLayout()
        col.addWidget(label(tr("auto_clean"), 10.3, QFont.Weight.Medium))
        col.addWidget(label(tr("auto_clean_desc"), 8.8, role="Dim", wrap=True))
        auto.addLayout(col, 1)
        self.auto = ToggleSwitch(bool(self.ctl.db.get_setting("auto_clean", False)))
        self.auto.toggled.connect(lambda on: self.ctl.db.set_setting("auto_clean", on))
        auto.addWidget(self.auto)
        lay.addLayout(auto)
        tools = QHBoxLayout()
        tools.setContentsMargins(4, 8, 4, 0)
        tools.addWidget(label(tr("windows_tools"), 9, role="Faint"))
        for text, target in ((tr("disk_cleanup"), "cleanmgr"), (tr("storage_sense"), "ms-settings:storagesense"),
                             (tr("uninstall_apps"), "ms-settings:appsfeatures")):
            b = button(text, Icons.FORWARD, role="Ghost")
            b.clicked.connect(lambda _=False, t=target: self._open_tool(t))
            tools.addWidget(b)
        tools.addStretch(1)
        lay.addLayout(tools)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(host)
        scroll.viewport().setAutoFillBackground(False)
        host.setAutoFillBackground(False)
        return scroll

    @staticmethod
    def _open_tool(target: str) -> None:
        if target == "cleanmgr":
            import subprocess
            subprocess.Popen(["cleanmgr.exe"], close_fds=True)
        else:
            os.startfile(target)

    # -------------------------------------------------------------- app tab
    def _build_app(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        card = Card(tr("app_data_title"), Icons.EDUCATION)
        self.app_rows = QVBoxLayout()
        self.app_rows.setSpacing(6)
        card.root.addLayout(self.app_rows)
        keep_row = QHBoxLayout()
        keep_col = QVBoxLayout()
        keep_col.addWidget(label(tr("backups_keep"), 10.3, QFont.Weight.Medium))
        keep_col.addWidget(label(tr("backups_keep_desc"), 8.8, role="Dim", wrap=True))
        keep_row.addLayout(keep_col, 1)
        self.keep = QComboBox()
        for n in (3, 7, 14, 30):
            self.keep.addItem(tr("backups_n", n=n), n)
        self.keep.setCurrentIndex(max(0, self.keep.findData(int(self.ctl.db.get_setting("backups_keep", 7)))))
        self.keep.currentIndexChanged.connect(self._keep_changed)
        keep_row.addWidget(self.keep)
        card.root.addLayout(keep_row)
        compact_row = QHBoxLayout()
        compact_col = QVBoxLayout()
        compact_col.addWidget(label(tr("compact_db"), 10.3, QFont.Weight.Medium))
        compact_col.addWidget(label(tr("compact_db_desc"), 8.8, role="Dim", wrap=True))
        compact_row.addLayout(compact_col, 1)
        compact = button(tr("compact_now"), Icons.SYNC, role="Ghost")
        compact.clicked.connect(self._compact)
        compact_row.addWidget(compact)
        card.root.addLayout(compact_row)
        lay.addWidget(card)
        lay.addStretch(1)
        return w

    def _fill_app(self, parts) -> None:
        while self.app_rows.count():
            item = self.app_rows.takeAt(0)
            if item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0).widget()
                    if sub:
                        sub.deleteLater()
        if isinstance(parts, Exception):
            return
        total = sum(size for _, size in parts) or 1
        for key, size in parts:
            row = QHBoxLayout()
            row.addWidget(label(tr(f"app_part_{key}"), 9.8), 1)
            bar = DriveBar(size / total)
            bar.setFixedWidth(220)
            row.addWidget(bar)
            v = label(ltr(human(size)), 9.8, QFont.Weight.DemiBold)
            v.setMinimumWidth(80)
            v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(v)
            self.app_rows.addLayout(row)

    def _keep_changed(self, *_):
        self.ctl.db.set_setting("backups_keep", self.keep.currentData())
        self.cleaner = self._make_cleaner()
        self.scan_category("backups")

    def _compact(self) -> None:
        freed = self.ctl.compact_database()
        self.ctl.notify(tr("compacted", size=ltr(human(freed))), "success")
        self.bg.run(cleanup.app_data_usage, self._fill_app)

    # --------------------------------------------------------------- scanning
    def refresh_drives(self) -> None:
        while self.drives_row.count():
            item = self.drives_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for d in cleanup.drives():
            self.drives_row.addWidget(DriveCard(d))
        self.drives_row.addStretch(1)

    def scan_all(self) -> None:
        self.cleaner = self._make_cleaner()
        for key in self.rows:
            self.scan_category(key)
        self.bg.run(cleanup.app_data_usage, self._fill_app)
        self.refresh_drives()

    def scan_category(self, key: str) -> None:
        row = self.rows[key]
        row.set_busy(tr("scanning"))
        cleaner = self.cleaner

        def work():
            size, count = cleaner.scan(key)
            return size, count, cleaner.blocked_by(key)
        self.bg.run(work, lambda result, k=key: self._scanned(k, result))

    def _scanned(self, key: str, result) -> None:
        row = self.rows[key]
        if isinstance(result, Exception):
            row.set_result(0, 0, [])
            return
        size, count, blocked = result
        row.set_result(size, count, blocked)
        self.update_total()

    def update_total(self) -> None:
        total = sum(r.size for r in self.rows.values() if r.check.isChecked())
        self.clean_all.setText(tr("clean_selected") + (f"  ·  {ltr(human(total))}" if total else ""))
        self.clean_all.setEnabled(bool(total) and not self._busy)

    def _update_freed(self) -> None:
        total = int(self.ctl.db.get_setting("space_freed_total", 0))
        self.freed_label.setText(tr("freed_so_far", size=ltr(human(total))) if total else "")

    # --------------------------------------------------------------- cleaning
    def clean(self, keys: list[str]) -> None:
        from app.ui.dialogs.base import ConfirmDialog
        keys = [k for k in keys if self.rows[k].size > 0]
        if not keys or self._busy:
            return
        size = sum(self.rows[k].size for k in keys)
        lines = "\n".join(f"•  {tr(f'clean_{k}')} — {ltr(human(self.rows[k].size))}" for k in keys)
        permanent = any(next(c for c in Cleaner.CATEGORIES if c.key == k).permanent for k in keys)
        message = tr("clean_confirm", size=ltr(human(size))) + "\n\n" + lines + \
            ("\n\n" + tr("clean_confirm_permanent") if permanent else "")
        if not ConfirmDialog.ask(tr("clean_selected"), message, tr("clean_now"), self):
            return
        self._busy = True
        self.update_total()
        for k in keys:
            self.rows[k].set_busy(tr("cleaning"))
        cleaner = self.cleaner
        self.bg.run(lambda: {k: cleaner.clean(k) for k in keys}, self._cleaned)

    def _cleaned(self, result) -> None:
        self._busy = False
        if isinstance(result, Exception):
            self.ctl.notify(str(result), "error")
            freed = 0
        else:
            freed = sum(result.values())
            self.ctl.add_space_freed(freed)
            self.ctl.notify(tr("space_freed", size=ltr(human(freed))), "success")
        self._update_freed()
        self.scan_all()

    # ---------------------------------------------------------------- page
    def show_tab(self, key: str) -> None:
        if key in self.views:
            self.current = key
            self.tabs.set_current(key)
            self.stack.switch_to(self.stack.indexOf(self.views[key]), animate=self.isVisible())
            view = self.views[key]
            if isinstance(view, FileList) and not view.items:
                view.scan()

    def on_shown(self) -> None:
        if not self._scanned_once:
            self._scanned_once = True
            self.scan_all()
        else:
            self.refresh_drives()
