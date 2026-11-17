"""
Password vault page.

  setup  : create the master password (strength meter + the honest warnings)
  locked : master password -> unlock (wrong attempts shake; short delay after repeated failures)
  open   : search, categories, favorites, health filters | details with copy / reveal / open site
"""
from __future__ import annotations

import os
import time

from PyQt6.QtCore import QPoint, QPropertyAnimation, QSequentialAnimationGroup, QSize, Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                             QStackedWidget, QVBoxLayout, QWidget)

from app.core import vault as V
from app.core.i18n import ltr, relative_time, tr
from app.ui.dialogs.vault_dialogs import PasswordField, StrengthBar, master_problem
from app.ui.pages.base import Page
from app.ui.theme import Icons, Palette, Theme, ui_font
from app.ui.widgets.common import (Card, EmptyState, GlyphLabel, GlyphPlate, IconButton, add_action, button, label,
                                   make_menu, primary_button)
from app.ui.widgets.tiles import item_pixmap


def _shake(widget: QWidget):
    origin = widget.pos()
    seq = QSequentialAnimationGroup(widget)
    for dx in (12, -10, 8, -5, 0):
        a = QPropertyAnimation(widget, b"pos", widget)
        a.setEndValue(origin + QPoint(dx, 0))
        a.setDuration(45)
        seq.addAnimation(a)
    widget._shake = seq
    seq.start()


class CenterCard(QWidget):
    """A centered card used by the setup and locked screens."""

    def __init__(self, glyph: str, title: str, subtitle: str):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.card = Card(padding=30)
        self.card.setFixedWidth(500)
        self.card.root.setSpacing(12)
        self.card.root.addWidget(GlyphPlate(glyph, Theme.accent(), 64), 0, Qt.AlignmentFlag.AlignHCenter)
        t = label(title, 18, QFont.Weight.DemiBold)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card.root.addWidget(t)
        s = label(subtitle, 10, role="Dim", wrap=True)
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card.root.addWidget(s)
        row.addWidget(self.card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)


class EntryRow(QWidget):
    def __init__(self, entry: dict, flags: set[str]):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 12, 6)
        lay.setSpacing(12)
        # the site's logo (already downloaded for the launcher) or a letter avatar
        icon = QLabel()
        icon.setFixedSize(36, 36)
        icon.setScaledContents(True)
        icon.setPixmap(item_pixmap({"kind": "url", "target": entry["url"] or entry["title"], "title": entry["title"],
                                    "icon_path": entry.get("icon_path", "")}))
        lay.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(entry["title"], 10.4, QFont.Weight.DemiBold))
        col.addWidget(label(ltr(entry["username"]) or "—", 8.8, role="Dim"))
        lay.addLayout(col, 1)
        for flag, color in (("weak", Palette.danger), ("reused", Palette.warning)):
            if flag in flags:
                lay.addWidget(GlyphLabel(Icons.WARNING, 9, color))
        if entry["favorite"]:
            lay.addWidget(GlyphLabel(Icons.STAR, 9.5, Palette.warning))


class VaultPage(Page):
    AUTOLOCK_CHOICES = (1, 5, 15, 30, 0)

    def __init__(self, ctl, main_window):
        super().__init__(ctl, main_window)
        self.vault: V.Vault = ctl.vault
        self.filter_key = "all"
        self.selected_id: int | None = None
        self.entries: list[dict] = []
        self.flags: dict[int, set] = {}
        self._fails = 0
        self._blocked_until = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._setup_view())
        self.stack.addWidget(self._locked_view())
        self.stack.addWidget(self._main_view())
        root.addWidget(self.stack)
        self.listen(ctl.data_changed, self.refresh)
        self.refresh("vault")

    # =============================================================== setup
    def _setup_view(self) -> QWidget:
        view = CenterCard(Icons.SHIELD, tr("vault_setup_title"), tr("vault_setup_sub"))
        c = view.card.root
        self.setup_pw = PasswordField(placeholder=tr("new_master"))
        self.setup_confirm = PasswordField(placeholder=tr("confirm_master"))
        c.addWidget(self.setup_pw)
        c.addWidget(self.setup_confirm)
        self.setup_bar = StrengthBar()
        self.setup_pw.edit.textChanged.connect(self.setup_bar.set_password)
        c.addWidget(self.setup_bar)
        for key in ("vault_warn_1", "vault_warn_2", "vault_warn_3"):
            c.addWidget(label("•  " + tr(key), 9, role="Dim", wrap=True))
        self.setup_error = label("", 9.5)
        self.setup_error.setStyleSheet("color: #F87171;")
        c.addWidget(self.setup_error)
        create = primary_button(tr("create_vault"), Icons.SHIELD)
        create.clicked.connect(self._create)
        self.setup_confirm.edit.returnPressed.connect(self._create)
        c.addWidget(create)
        self._setup_card = view.card
        return view

    def _create(self):
        problem = master_problem(self.setup_pw.text(), self.setup_confirm.text())
        if problem:
            self.setup_error.setText(problem)
            _shake(self._setup_card)
            return
        self.vault.setup(self.setup_pw.text())
        self.setup_pw.setText("")
        self.setup_confirm.setText("")
        self.ctl.vault_changed()

    # ============================================================== locked
    def _locked_view(self) -> QWidget:
        view = CenterCard(Icons.LOCK, tr("vault_locked"), tr("vault_locked_sub"))
        c = view.card.root
        self.unlock_pw = PasswordField(placeholder=tr("master_password"))
        self.unlock_pw.edit.returnPressed.connect(self._unlock)
        c.addWidget(self.unlock_pw)
        self.unlock_error = label("", 9.5)
        self.unlock_error.setStyleSheet("color: #F87171;")
        c.addWidget(self.unlock_error)
        self.unlock_btn = primary_button(tr("unlock"), Icons.KEY)
        self.unlock_btn.clicked.connect(self._unlock)
        c.addWidget(self.unlock_btn)
        self._lock_card = view.card
        return view

    def _unlock(self):
        if time.monotonic() < self._blocked_until:
            self.unlock_error.setText(tr("wait_seconds", n=int(self._blocked_until - time.monotonic()) + 1))
            return
        self.unlock_btn.setEnabled(False)
        self.unlock_btn.setText(tr("unlocking"))
        QTimer.singleShot(30, self._do_unlock)  # let the button repaint before PBKDF2 runs

    def _do_unlock(self):
        ok = self.vault.unlock(self.unlock_pw.text())
        self.unlock_btn.setEnabled(True)
        self.unlock_btn.setText(tr("unlock"))
        if ok:
            self._fails = 0
            self.unlock_pw.setText("")
            self.unlock_error.setText("")
            self.ctl.vault_changed()
        else:
            self._fails += 1
            if self._fails >= 3:  # slow down guessing at the keyboard
                self._blocked_until = time.monotonic() + min(60, 5 * 2 ** (self._fails - 3))
            self.unlock_error.setText(tr("wrong_master"))
            _shake(self._lock_card)
            self.unlock_pw.edit.selectAll()

    # ================================================================ main
    def _main_view(self) -> QWidget:
        view = QWidget()
        lay = QVBoxLayout(view)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(tr("nav_vault"), 22, QFont.Weight.DemiBold))
        self.subtitle = label("", 10, role="Dim")
        titles.addWidget(self.subtitle)
        head.addLayout(titles)
        head.addStretch(1)
        head.addWidget(label(tr("auto_lock"), 9.5, role="Dim"))
        self.autolock = QComboBox()
        for minutes in self.AUTOLOCK_CHOICES:
            self.autolock.addItem(tr("never") if not minutes else tr("after_min", n=minutes), minutes)
        self.autolock.setCurrentIndex(max(0, self.autolock.findData(int(self.ctl.db.get_setting("vault_autolock", 5)))))
        self.autolock.currentIndexChanged.connect(
            lambda _: self.ctl.db.set_setting("vault_autolock", self.autolock.currentData()))
        head.addWidget(self.autolock)
        more = IconButton(Icons.MENU, tr("more"), 38, 11)
        more.clicked.connect(lambda: self._more_menu(more))
        head.addWidget(more)
        lock = button(tr("lock_now"), Icons.LOCK)
        lock.clicked.connect(self.ctl.lock_vault)
        head.addWidget(lock)
        lay.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("search_vault"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self._render_list())
        bar.addWidget(self.search, 1)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        bar.addLayout(self.chips)
        gen = button(tr("generator"), Icons.LIGHTNING)
        gen.clicked.connect(lambda: self.ctl.open_generator(self))
        bar.addWidget(gen)
        imp = button(tr("import_chrome_short"), Icons.DOWNLOAD)
        imp.clicked.connect(lambda: self.ctl.import_chrome(self))
        bar.addWidget(imp)
        new = primary_button(tr("new_login"), Icons.ADD)
        new.clicked.connect(lambda: self.ctl.new_login(self))
        bar.addWidget(new)
        lay.addLayout(bar)

        body = QHBoxLayout()
        body.setSpacing(18)
        list_card = Card(padding=10)
        list_card.setFixedWidth(400)
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.currentItemChanged.connect(self._selected)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        list_card.root.addWidget(self.list, 1)
        body.addWidget(list_card)
        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(EmptyState(Icons.KEY, tr("vault_pick")))
        self.detail_stack.addWidget(self._detail_card())
        body.addWidget(self.detail_stack, 1)
        lay.addLayout(body, 1)
        return view

    def _detail_card(self) -> QWidget:
        card = Card(padding=26)
        c = card.root
        c.setSpacing(12)
        head = QHBoxLayout()
        self.d_icon = GlyphPlate(Icons.GLOBE, Theme.accent(), 56)
        head.addWidget(self.d_icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.d_title = label("", 17, QFont.Weight.DemiBold)
        col.addWidget(self.d_title)
        self.d_url = QPushButton()
        self.d_url.setObjectName("Ghost")
        self.d_url.setCursor(Qt.CursorShape.PointingHandCursor)
        self.d_url.clicked.connect(lambda: self.selected_id and self.ctl.open_login(self.selected_id))
        col.addWidget(self.d_url, 0, Qt.AlignmentFlag.AlignLeft)
        head.addLayout(col, 1)
        self.d_fav = IconButton(Icons.STAR, tr("favorite"), 38, 12)
        self.d_fav.setCheckable(True)
        self.d_fav.clicked.connect(lambda on: self.selected_id and self.ctl.set_login_favorite(self.selected_id, on))
        head.addWidget(self.d_fav)
        c.addLayout(head)

        def field_row(title, glyph):
            box = Card(padding=12)
            row = QHBoxLayout()
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(label(title, 8.8, role="Faint"))
            value = label("", 12, QFont.Weight.Medium)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            col.addWidget(value)
            row.addLayout(col, 1)
            box.root.addLayout(row)
            c.addWidget(box)
            return row, value

        row, self.d_user = field_row(tr("username"), Icons.PERSON)
        copy_user = IconButton(Icons.COPY, tr("copy_username"), 36, 11)
        copy_user.clicked.connect(lambda: self._copy("username"))
        row.addWidget(copy_user)
        row, self.d_pass = field_row(tr("password"), Icons.KEY)
        self.d_eye = IconButton(Icons.EYE, tr("show_password"), 36, 11)
        self.d_eye.setCheckable(True)
        self.d_eye.toggled.connect(lambda _: self._show_detail(self.selected_id, keep_eye=True))
        row.addWidget(self.d_eye)
        copy_pw = IconButton(Icons.COPY, tr("copy_password"), 36, 11)
        copy_pw.clicked.connect(lambda: self._copy("password"))
        row.addWidget(copy_pw)
        self.d_bar = StrengthBar()
        c.addWidget(self.d_bar)
        self.d_warn = label("", 9.5, wrap=True)
        self.d_warn.setStyleSheet(f"color: {Palette.warning};")
        c.addWidget(self.d_warn)
        self.d_notes = label("", 10, wrap=True)
        self.d_notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        c.addWidget(self.d_notes)
        self.d_meta = label("", 8.8, role="Faint")
        c.addWidget(self.d_meta)
        c.addStretch(1)
        actions = QHBoxLayout()
        go = primary_button(tr("open_and_copy"), Icons.GLOBE)
        go.clicked.connect(lambda: self.selected_id and self.ctl.open_login(self.selected_id))
        actions.addWidget(go)
        edit = button(tr("edit"), Icons.EDIT)
        edit.clicked.connect(lambda: self.selected_id and self.ctl.edit_login(self.selected_id, self))
        actions.addWidget(edit)
        actions.addStretch(1)
        delete = button(tr("delete"), Icons.DELETE, "Danger")
        delete.clicked.connect(lambda: self.selected_id and self.ctl.delete_login(self.selected_id, self))
        actions.addWidget(delete)
        c.addLayout(actions)
        return card

    # ------------------------------------------------------------ rendering
    def refresh(self, what: str = ""):
        if what not in ("", "vault", "logos"):
            return
        if not self.vault.is_set_up():
            self.stack.setCurrentIndex(0)
            return
        if not self.vault.unlocked:
            self.stack.setCurrentIndex(1)
            self.entries, self.flags = [], {}
            self.list.clear()
            return
        self.stack.setCurrentIndex(2)
        self.entries = self.vault.entries()
        for e in self.entries:
            cached = self.ctl.favicons.cached_path(e["url"]) if e["url"] else None
            e["icon_path"] = str(cached) if cached and cached.exists() else ""
        report = V.health(self.entries)
        self.flags = {e["id"]: {k for k, ids in report.items() if e["id"] in ids} for e in self.entries}
        self.subtitle.setText(tr("vault_summary", n=len(self.entries), weak=len(set(report["weak"])),
                                 reused=len(set(report["reused"])), old=len(set(report["old"]))))
        self._fill_chips(report)
        self._render_list()

    def _fill_chips(self, report: dict):
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        counts = {"all": len(self.entries), "favorites": sum(1 for e in self.entries if e["favorite"]),
                  "weak": len(set(report["weak"])), "reused": len(set(report["reused"]))}
        for c in V.CATEGORIES:
            counts[c] = sum(1 for e in self.entries if e["category"] == c)
        keys = ["all", "favorites", "weak", "reused"] + [c for c in V.CATEGORIES if counts[c]]
        if self.filter_key not in keys:
            self.filter_key = "all"
        combo = QComboBox()
        for k in keys:
            if k in ("all",) or counts[k]:
                combo.addItem(f"{tr(f'vfilter_{k}') if k in ('all', 'favorites', 'weak', 'reused') else tr(f'vcat_{k}')}"
                              f"  ({counts[k]})", k)
        combo.setCurrentIndex(max(0, combo.findData(self.filter_key)))
        combo.setFixedWidth(200)
        combo.currentIndexChanged.connect(lambda _: (setattr(self, "filter_key", combo.currentData()),
                                                     self._render_list()))
        self.chips.addWidget(combo)

    def _visible(self) -> list[dict]:
        q = self.search.text().strip().lower()
        out = []
        for e in self.entries:
            k = self.filter_key
            if k == "favorites" and not e["favorite"] or k in ("weak", "reused") and k not in self.flags[e["id"]] \
                    or k in V.CATEGORIES and e["category"] != k:
                continue
            if q and q not in f"{e['title']} {e['url']} {e['username']}".lower():
                continue
            out.append(e)
        return out

    def _render_list(self):
        self.list.blockSignals(True)
        self.list.clear()
        current = None
        for e in self._visible():
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, e["id"])
            row = EntryRow(e, self.flags.get(e["id"], set()))
            item.setSizeHint(QSize(0, 54))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            if e["id"] == self.selected_id:
                current = item
        self.list.blockSignals(False)
        if current is not None:
            self.list.setCurrentItem(current)
            self._show_detail(self.selected_id)
        elif self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.detail_stack.setCurrentIndex(0)

    def _selected(self, item, _prev=None):
        if item is not None:
            self.d_eye.blockSignals(True)
            self.d_eye.setChecked(False)  # a newly selected password always starts hidden
            self.d_eye.blockSignals(False)
            self._show_detail(item.data(Qt.ItemDataRole.UserRole))

    def _show_detail(self, entry_id, keep_eye: bool = False):
        e = next((x for x in self.entries if x["id"] == entry_id), None)
        if not e:
            self.detail_stack.setCurrentIndex(0)
            return
        self.selected_id = entry_id
        self.detail_stack.setCurrentIndex(1)
        self.d_title.setText(e["title"])
        self.d_url.setText(ltr(e["url"]) if e["url"] else tr("no_website"))
        self.d_fav.setChecked(bool(e["favorite"]))
        self.d_user.setText(e["username"] or "—")
        shown = self.d_eye.isChecked()
        self.d_pass.setText(e["password"] if shown else "•" * min(16, max(8, len(e["password"]))))
        self.d_pass.setFont(ui_font(12, QFont.Weight.Medium))
        self.d_eye.setText(Icons.EYE_OFF if shown else Icons.EYE)
        self.d_bar.set_password(e["password"])
        warns = [tr(f"warn_{k}") for k in ("weak", "reused", "old") if k in self.flags.get(e["id"], set())]
        self.d_warn.setText("  ".join("⚠ " + w for w in warns))
        self.d_warn.setVisible(bool(warns))
        self.d_notes.setText(e["notes"] if shown else (tr("notes_hidden") if e["notes"] else ""))
        self.d_meta.setText(tr("login_meta", changed=relative_time(e["pw_changed_at"]),
                               used=relative_time(e["last_used"]) if e["last_used"] else "—"))
        self.ctl.vault_activity()
        if shown:  # a revealed password hides itself again after 30 s
            QTimer.singleShot(30_000, lambda eid=entry_id: self.selected_id == eid and self.d_eye.setChecked(False))

    def _copy(self, field_name: str):
        e = next((x for x in self.entries if x["id"] == self.selected_id), None)
        if e:
            self.ctl.copy_secret(e[field_name], self.selected_id)

    def _list_menu(self, pos):
        item = self.list.itemAt(pos)
        if not item:
            return
        eid = item.data(Qt.ItemDataRole.UserRole)
        e = next((x for x in self.entries if x["id"] == eid), None)
        menu = make_menu(self)
        add_action(menu, Icons.COPY, tr("copy_password"), lambda: self.ctl.copy_secret(e["password"], eid))
        add_action(menu, Icons.PERSON, tr("copy_username"), lambda: self.ctl.copy_secret(e["username"], eid))
        add_action(menu, Icons.GLOBE, tr("open_and_copy"), lambda: self.ctl.open_login(eid))
        menu.addSeparator()
        add_action(menu, Icons.EDIT, tr("edit"), lambda: self.ctl.edit_login(eid, self))
        add_action(menu, Icons.DELETE, tr("delete"), lambda: self.ctl.delete_login(eid, self), danger=True)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _more_menu(self, anchor):
        menu = make_menu(self)
        add_action(menu, Icons.KEY, tr("change_master"), lambda: self.ctl.change_master(self))
        add_action(menu, Icons.SAVE, tr("export_csv"), lambda: self.ctl.export_vault(self))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def on_shown(self):
        self.refresh("vault")
        if self.stack.currentIndex() == 1:
            self.unlock_pw.edit.setFocus()
