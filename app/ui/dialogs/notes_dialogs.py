"""Notebook dialogs: unlock the vault, quick switcher (Ctrl+P), version history with a diff."""
from __future__ import annotations

import difflib
import html

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QSplitter, QTextBrowser,
                             QVBoxLayout, QWidget)

from app.core.i18n import ltr, relative_time, tr
from app.core.md_render import stats
from app.ui.dialogs.base import StyledDialog
from app.ui.theme import Icons, Palette, Theme
from app.ui.widgets.common import GlyphLabel, button, label, primary_button


class UnlockVaultDialog(StyledDialog):
    """Master password -> vault unlocked (for locked notes)."""

    def __init__(self, ctl, parent=None):
        super().__init__(tr("unlock_vault"), parent, 400)
        self.ctl = ctl
        self.body.addWidget(label(tr("unlock_for_note"), 9.5, role="Dim", wrap=True))
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText(tr("master_password"))
        self.edit.returnPressed.connect(self._try_accept)
        self.body.addWidget(self.edit)
        self.error = label("", 9, role="Dim")
        self.error.setStyleSheet(f"color: {Palette.danger};")
        self.body.addWidget(self.error)
        self.add_buttons(tr("unlock"))

    def validate(self) -> bool:
        if self.ctl.vault.unlock(self.edit.text()):
            self.ctl.vault_changed()
            return True
        self.error.setText(tr("wrong_master"))
        self.edit.selectAll()
        return False


class QuickSwitcher(StyledDialog):
    """Ctrl+P: type part of a title, Enter opens it; Ctrl+Enter creates a note with that title."""

    def __init__(self, ctl, parent=None):
        super().__init__(tr("quick_switcher"), parent, 560)
        self.ctl = ctl
        self.result_id: int | None = None
        self.notes = ctl.db.note_titles()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr("quick_switcher_ph"))
        self.edit.textChanged.connect(self._filter)
        self.edit.returnPressed.connect(self._open)
        self.edit.installEventFilter(self)
        self.body.addWidget(self.edit)
        self.list = QListWidget()
        self.list.setMinimumHeight(330)
        self.list.itemActivated.connect(lambda _: self._open())
        self.body.addWidget(self.list)
        self.body.addWidget(label(tr("quick_switcher_hint"), 8.6, role="Faint"))
        self._filter("")

    @staticmethod
    def _score(q: str, title: str) -> int:
        t = title.lower()
        if not q:
            return 1
        if t.startswith(q):
            return 4
        if any(w.startswith(q) for w in t.split()):
            return 3
        if q in t:
            return 2
        it = iter(t)  # letters in order (fuzzy)
        return 1 if all(ch in it for ch in q) else 0

    def _filter(self, text: str) -> None:
        q = text.strip().lower()
        ranked = sorted(((-self._score(q, n["title"]), i, n) for i, n in enumerate(self.notes)
                         if self._score(q, n["title"])), key=lambda r: (r[0], r[1]))
        self.list.clear()
        for _, _, n in ranked[:40]:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, n["id"])
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 4, 10, 4)
            h.addWidget(GlyphLabel(Icons.STAR if n["is_template"] else Icons.NOTES, 10, Theme.accent2()))
            h.addWidget(label(n["title"], 10), 1)
            h.addWidget(label(relative_time(n["updated_at"]), 8.4, role="Faint"))
            item.setSizeHint(QSize(0, 38))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, e):
        from PyQt6.QtCore import QEvent
        if obj is self.edit and e.type() == QEvent.Type.KeyPress:
            if e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                n = self.list.count()
                if n:
                    step = 1 if e.key() == Qt.Key.Key_Down else -1
                    self.list.setCurrentRow((self.list.currentRow() + step) % n)
                return True
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                title = self.edit.text().strip()
                if title:
                    self.result_id = self.ctl.open_or_create_by_title(title)
                    self.accept()
                return True
        return super().eventFilter(obj, e)

    def _open(self) -> None:
        item = self.list.currentItem()
        if item:
            self.result_id = item.data(Qt.ItemDataRole.UserRole)
            self.accept()
        elif self.edit.text().strip():
            self.result_id = self.ctl.open_or_create_by_title(self.edit.text().strip())
            self.accept()


def diff_html(old: str, new: str) -> str:
    """Line diff old -> new as colored HTML (removed red, added green, unchanged dim; long equal runs folded)."""
    rows = []
    sm = difflib.SequenceMatcher(None, old.split("\n"), new.split("\n"))
    a, b = old.split("\n"), new.split("\n")
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            chunk = a[i1:i2]
            if len(chunk) > 6:
                chunk = chunk[:2] + [None] + chunk[-2:]
            for line in chunk:
                rows.append(('<div class="fold">⋯</div>' if line is None else
                             f'<div class="eq">{html.escape(line) or "&nbsp;"}</div>'))
            continue
        for line in a[i1:i2]:
            rows.append(f'<div class="del">− {html.escape(line) or "&nbsp;"}</div>')
        for line in b[j1:j2]:
            rows.append(f'<div class="add">+ {html.escape(line) or "&nbsp;"}</div>')
    style = (f"<style>div{{font-family:'Cascadia Mono',Consolas;font-size:12.5px;padding:1px 8px;white-space:pre-wrap}}"
             f".eq{{color:{Palette.text_dim}}}.fold{{color:{Palette.text_faint}}}"
             f".del{{background:rgba(248,113,113,0.16);color:#FCA5A5}}"
             f".add{{background:rgba(52,211,153,0.16);color:#86EFAC}}</style>")
    return style + ("".join(rows) or f'<div class="eq">{tr("no_changes")}</div>')


class HistoryDialog(StyledDialog):
    """Versions of a note (newest first) with what changed compared to now; Restore brings one back."""

    def __init__(self, ctl, note_id: int, current: str, parent=None):
        super().__init__(tr("version_history"), parent, 940)
        self.ctl, self.note_id, self.current = ctl, note_id, current
        self.versions = ctl.db.list_versions(note_id)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.setMinimumWidth(250)
        cur_words = stats(current)["words"]
        for v in self.versions:
            words = stats(v["content"])["words"]
            delta = cur_words - words
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, v["id"])
            row = QWidget()
            col = QVBoxLayout(row)
            col.setContentsMargins(10, 6, 10, 6)
            col.setSpacing(0)
            col.addWidget(label(relative_time(v["created_at"]) + "  ·  " + ltr(v["created_at"][:16]), 9.6,
                                QFont.Weight.DemiBold))
            col.addWidget(label(tr("version_meta", words=words, delta=ltr(f"{delta:+d}")), 8.6, role="Dim"))
            item.setSizeHint(QSize(0, 50))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        self.list.currentRowChanged.connect(self._show)
        split.addWidget(self.list)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(label(tr("version_diff_hint"), 8.6, role="Faint"))
        self.diff = QTextBrowser()
        self.diff.setMinimumHeight(420)
        self.diff.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        rl.addWidget(self.diff, 1)
        split.addWidget(right)
        split.setSizes([260, 640])
        self.body.addWidget(split, 1)
        row = QHBoxLayout()
        copy = button(tr("copy_text"), Icons.COPY, role="Ghost")
        copy.clicked.connect(self._copy)
        row.addWidget(copy)
        row.addStretch(1)
        close = button(tr("close"), role="Ghost")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        self.restore = primary_button(tr("restore_version"), Icons.UNDO)
        self.restore.clicked.connect(self._restore)
        row.addWidget(self.restore)
        self.body.addLayout(row)
        if self.versions:
            self.list.setCurrentRow(0)
        else:
            self.diff.setHtml(f"<p style='color:{Palette.text_dim}'>{html.escape(tr('no_versions'))}</p>")
            self.restore.setEnabled(False)

    def _version(self):
        row = self.list.currentRow()
        return self.versions[row] if 0 <= row < len(self.versions) else None

    def _show(self, _row: int) -> None:
        v = self._version()
        if v:
            self.diff.setHtml(diff_html(v["content"], self.current))

    def _copy(self) -> None:
        v = self._version()
        if v:
            self.ctl.copy_text(v["content"])

    def _restore(self) -> None:
        v = self._version()
        if v:
            self.ctl.restore_version(self.note_id, v["id"])
            self.accept()
