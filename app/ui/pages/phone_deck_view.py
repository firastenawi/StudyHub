"""
Phone > Deck: set up the grid of buttons the phone shows (Remote > Deck). Left: the buttons as on the phone
(drag to reorder, click to edit). Right: add a button (media, Study Hub, shortcut, app or site, OBS, power) and
OBS Studio's connection.
"""
from __future__ import annotations

import os
import threading

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QFont, QKeySequence, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QKeySequenceEdit, QLineEdit,
                             QListWidget, QListWidgetItem, QSpinBox, QStackedWidget, QStyle, QStyledItemDelegate,
                             QVBoxLayout, QWidget)

from app.core import deck as deck_mod
from app.core.i18n import tr
from app.ui.pages.base import Page
from app.ui.theme import Icons, Palette, Theme, ui_font
from app.ui.widgets.common import Card, button, label, primary_button
from app.ui.widgets.segmented import SegmentedTabs

ROLE = Qt.ItemDataRole.UserRole
COLOR_ROLE = Qt.ItemDataRole.UserRole + 1
EMOJI_ROLE = Qt.ItemDataRole.UserRole + 2


class TileDelegate(QStyledItemDelegate):
    """A Deck button as the phone shows it: coloured tile, big emoji, name under it."""

    def paint(self, p: QPainter, opt, index):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(opt.rect).adjusted(4, 4, -4, -4)
        base = QColor(index.data(COLOR_ROLE) or deck_mod.COLORS[0])
        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0, base)
        light = QColor(base)
        light.setAlpha(170)
        grad.setColorAt(1, light)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(r, 16, 16)
        if opt.state & QStyle.StateFlag.State_Selected:
            p.setPen(QPen(QColor("white"), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), 15, 15)
        f = QFont(ui_font(10))
        f.setPixelSize(26)
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(QRectF(r.left(), r.top() + 10, r.width(), 34), Qt.AlignmentFlag.AlignCenter, index.data(EMOJI_ROLE) or "")
        p.setFont(ui_font(8.6, QFont.Weight.DemiBold))
        text = p.fontMetrics().elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "", Qt.TextElideMode.ElideRight, int(r.width() - 10))
        p.drawText(QRectF(r.left() + 5, r.top() + 48, r.width() - 10, 22), Qt.AlignmentFlag.AlignCenter, text)
        p.restore()

    def sizeHint(self, opt, index):
        return QSize(96, 88)


class DeckGrid(QListWidget):
    """The buttons as tiles; drag to reorder."""
    reordered = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setMovement(QListWidget.Movement.Snap)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setGridSize(QSize(104, 96))
        self.setIconSize(QSize(1, 1))
        self.setSpacing(6)
        self.setWordWrap(True)
        self.setUniformItemSizes(True)
        self.setMinimumHeight(330)
        self.setItemDelegate(TileDelegate(self))
        self.setStyleSheet("QListWidget { background: transparent; border: none; }")

    def dropEvent(self, e):
        super().dropEvent(e)
        self.reordered.emit([self.item(i).data(ROLE) for i in range(self.count())])


class DeckView(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl = ctl
        self.page = page
        self.deck = ctl.deck
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        outer.addWidget(Page.scrollable(content))
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 6, 6)
        lay.setSpacing(14)

        self.need_remote = Card(padding=14)
        nr = QHBoxLayout()
        self.need_remote_text = label(tr("deck_need_remote"), 9.5, wrap=True)
        self.need_remote_text.setStyleSheet(f"color: {Palette.warning};")
        nr.addWidget(self.need_remote_text, 1)
        allow = button(tr("deck_allow_remote"), Icons.SHIELD, role="Ghost")
        allow.clicked.connect(lambda: (ctl.set_phone_cap("remote", True), self.refresh()))
        nr.addWidget(allow)
        self.need_remote.root.addLayout(nr)
        lay.addWidget(self.need_remote)

        body = QHBoxLayout()
        body.setSpacing(14)
        # ---- left: the deck
        left = Card(tr("deck_title"), Icons.VIEW_GRID, padding=18)
        left.header.addStretch(1)
        reset = button(tr("deck_reset"), Icons.REFRESH, role="Ghost")
        reset.clicked.connect(self._reset)
        left.header.addWidget(reset)
        left.root.addWidget(label(tr("deck_desc"), 8.8, role="Dim", wrap=True))
        self.grid = DeckGrid()
        self.grid.reordered.connect(self.deck.reorder)
        self.grid.currentItemChanged.connect(lambda *_: self._show_edit())
        left.root.addWidget(self.grid, 1)
        # edit the selected button
        self.edit = QWidget()
        el = QHBoxLayout(self.edit)
        el.setContentsMargins(0, 0, 0, 0)
        self.e_emoji = QLineEdit()
        self.e_emoji.setFixedWidth(54)
        self.e_emoji.setMaxLength(4)
        self.e_emoji.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.e_emoji.setToolTip(tr("deck_emoji"))
        self.e_label = QLineEdit()
        self.e_label.setPlaceholderText(tr("deck_label_ph"))
        self.e_color = QComboBox()
        for c in deck_mod.COLORS:
            from app.ui.theme import glyph_icon
            self.e_color.addItem(glyph_icon("●", c, 16), "", c)
        for w in (self.e_emoji, self.e_label):
            w.editingFinished.connect(self._save_edit)
        self.e_color.activated.connect(lambda _: self._save_edit())
        rm = button(tr("deck_remove"), Icons.DELETE, role="Ghost")
        rm.clicked.connect(self._remove)
        el.addWidget(self.e_emoji)
        el.addWidget(self.e_label, 1)
        el.addWidget(self.e_color)
        el.addWidget(rm)
        left.root.addWidget(self.edit)
        body.addWidget(left, 3)

        # ---- right: add a button
        right = QVBoxLayout()
        right.setSpacing(14)
        add = Card(tr("deck_add"), Icons.ADD, padding=18)
        self.cats = SegmentedTabs([("media", Icons.MUSIC, ""), ("command", Icons.LIGHTNING, ""), ("keys", Icons.KEYBOARD, ""),
                                   ("open", Icons.APPS, ""), ("obs", Icons.PREVIEW, ""), ("power", Icons.POWER, "")], "media")
        self.cats.changed.connect(self._show_cat)
        add.root.addWidget(self.cats)
        self.cat_title = label("", 10, QFont.Weight.DemiBold)
        add.root.addWidget(self.cat_title)
        self.cat_stack = QStackedWidget()
        self.choices = QListWidget()
        self.choices.itemDoubleClicked.connect(lambda _: self._add_choice())
        self.cat_stack.addWidget(self.choices)
        # shortcut recorder
        keys = QWidget()
        kl = QVBoxLayout(keys)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.addWidget(label(tr("deck_keys_hint"), 9, role="Dim", wrap=True))
        self.key_edit = QKeySequenceEdit()
        self.key_edit.setMaximumSequenceLength(1)
        kl.addWidget(self.key_edit)
        self.key_text = QLineEdit()
        self.key_text.setPlaceholderText("Win+D, Ctrl+Shift+Esc, Alt+Tab…")
        kl.addWidget(self.key_text)
        self.key_edit.keySequenceChanged.connect(lambda ks: self.key_text.setText(ks.toString(QKeySequence.SequenceFormat.PortableText)))
        kl.addStretch(1)
        self.cat_stack.addWidget(keys)
        # app / site
        opener = QWidget()
        ol = QVBoxLayout(opener)
        ol.setContentsMargins(0, 0, 0, 0)
        self.items = QListWidget()
        ol.addWidget(self.items, 1)
        orow = QHBoxLayout()
        pick = button(tr("deck_pick_file"), Icons.OPEN_FOLDER, role="Ghost")
        pick.clicked.connect(self._pick_file)
        orow.addWidget(pick)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://…")
        orow.addWidget(self.url, 1)
        ol.addLayout(orow)
        self.cat_stack.addWidget(opener)
        add.root.addWidget(self.cat_stack, 1)
        self.add_btn = primary_button(tr("deck_add_button"), Icons.ADD)
        self.add_btn.clicked.connect(self._add_choice)
        add.root.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignRight)
        right.addWidget(add, 1)

        # ---- OBS
        self.obs_card = Card(tr("deck_obs"), Icons.PREVIEW, padding=18)
        self.obs_card.root.addWidget(label(tr("deck_obs_desc"), 8.8, role="Dim", wrap=True))
        conf = ctl.db.get_setting("phone_obs", {}) or {}
        orow2 = QHBoxLayout()
        self.obs_host = QLineEdit(conf.get("host", "127.0.0.1"))
        self.obs_port = QSpinBox()
        self.obs_port.setRange(1, 65535)
        self.obs_port.setValue(int(conf.get("port", 4455)))
        from app.core.obs import load_password
        self.obs_pw = QLineEdit(load_password(ctl.db))
        self.obs_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.obs_pw.setPlaceholderText(tr("deck_obs_password"))
        orow2.addWidget(self.obs_host, 2)
        orow2.addWidget(self.obs_port, 1)
        orow2.addWidget(self.obs_pw, 2)
        self.obs_card.root.addLayout(orow2)
        trow = QHBoxLayout()
        test = button(tr("deck_obs_test"), Icons.LINK, role="Ghost")
        test.clicked.connect(self._test_obs)
        trow.addWidget(test)
        self.obs_status = label("", 9, wrap=True)
        trow.addWidget(self.obs_status, 1)
        self.obs_card.root.addLayout(trow)
        right.addWidget(self.obs_card)
        body.addLayout(right, 2)
        lay.addLayout(body, 1)
        self._obs_scenes: list[str] = []
        self.deck.changed.connect(self._changed)
        self._show_cat("media")
        self.refresh()

    # ---------------------------------------------------------------- the deck grid
    def refresh(self) -> None:
        from app.core.phone_bridge import caps
        self.need_remote.setVisible(not caps(self.ctl.db).get("remote"))
        current = self.grid.currentItem().data(ROLE) if self.grid.currentItem() else None
        self.grid.blockSignals(True)
        self.grid.clear()
        for b in self.deck.buttons():
            text, emoji = self.deck.describe(b)
            item = QListWidgetItem(text)
            item.setData(ROLE, b["id"])
            item.setData(COLOR_ROLE, b.get("color") or deck_mod.COLORS[0])
            item.setData(EMOJI_ROLE, emoji)
            item.setSizeHint(QSize(96, 88))
            self.grid.addItem(item)
            if b["id"] == current:
                self.grid.setCurrentItem(item)
        self.grid.blockSignals(False)
        self._show_edit()

    def _changed(self) -> None:
        try:
            self.refresh()
        except RuntimeError:
            pass

    def _selected(self) -> dict | None:
        item = self.grid.currentItem()
        return next((b for b in self.deck.buttons() if item and b["id"] == item.data(ROLE)), None)

    def _show_edit(self) -> None:
        b = self._selected()
        self.edit.setEnabled(b is not None)
        if b is None:
            self.e_label.clear()
            self.e_emoji.clear()
            return
        text, emoji = self.deck.describe(b)
        self.e_label.setText(b.get("label") or "")
        self.e_label.setPlaceholderText(text)
        self.e_emoji.setText(b.get("emoji") or "")
        self.e_emoji.setPlaceholderText(emoji)
        self.e_color.setCurrentIndex(max(0, self.e_color.findData(b.get("color"))))

    def _save_edit(self) -> None:
        b = self._selected()
        if b:
            self.deck.update(b["id"], label=self.e_label.text().strip(), emoji=self.e_emoji.text().strip(),
                             color=self.e_color.currentData())

    def _remove(self) -> None:
        b = self._selected()
        if b:
            self.deck.remove(b["id"])

    def _reset(self) -> None:
        from app.ui.dialogs.base import ConfirmDialog
        if ConfirmDialog.ask(tr("deck_reset"), tr("deck_reset_msg"), tr("deck_reset"), self):
            self.deck.reset()

    # ---------------------------------------------------------------- adding
    def _show_cat(self, key: str) -> None:
        self.cat = key
        self.cats.set_current(key)
        self.cat_title.setText(tr(f"deck_cat_{key}"))
        self.choices.clear()
        if key == "keys":
            self.cat_stack.setCurrentIndex(1)
            return
        if key == "open":
            self.cat_stack.setCurrentIndex(2)
            self.items.clear()
            for it in self.ctl.db.query("SELECT id, title FROM launcher_items ORDER BY title")[:300]:
                li = QListWidgetItem(it["title"])
                li.setData(ROLE, f"item:{it['id']}")
                self.items.addItem(li)
            return
        self.cat_stack.setCurrentIndex(0)
        if key == "media":
            options = [(ref, f"{e}  {tr(t)}") for ref, (e, t) in deck_mod.MEDIA.items()]
        elif key == "power":
            options = [(ref, f"{e}  {tr(t)}") for ref, (e, t) in deck_mod.POWER.items()]
        elif key == "command":
            options = [(k, f"⚡  {tr(k)}") for _, k, _ in self.ctl.command_list()]
        else:  # obs
            options = [(ref, f"{e}  {tr(t)}") for ref, (e, t) in deck_mod.OBS.items()]
            options += [(f"scene:{s}", f"🎬  {s}") for s in self._obs_scenes]
            if not self._obs_scenes:
                self.choices.addItem(tr("deck_obs_scenes_hint"))
                self.choices.item(0).setFlags(Qt.ItemFlag.NoItemFlags)
        for ref, text in options:
            li = QListWidgetItem(text)
            li.setData(ROLE, ref)
            self.choices.addItem(li)

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("deck_pick_file"), os.path.expanduser("~"))
        if path:
            self.deck.add("open", os.path.normpath(path))

    def _add_choice(self) -> None:
        if self.cat == "keys":
            text = self.key_text.text().strip()
            if not deck_mod.parse_shortcut(text):
                self.ctl.notify(tr("deck_keys_bad"), "warning")
                return
            self.deck.add("keys", text)
            self.key_text.clear()
            self.key_edit.clear()
            return
        if self.cat == "open":
            url = self.url.text().strip()
            if url:
                from app.core.launcher import normalize_url
                url = normalize_url(url)
                if url:
                    self.deck.add("open", url)
                    self.url.clear()
                return
            item = self.items.currentItem()
            if item:
                self.deck.add("open", item.data(ROLE))
            return
        item = self.choices.currentItem()
        if item and item.data(ROLE):
            self.deck.add(self.cat, item.data(ROLE))

    # ---------------------------------------------------------------- OBS
    def _test_obs(self) -> None:
        from app.core import obs
        conf = {"host": self.obs_host.text().strip() or "127.0.0.1", "port": self.obs_port.value()}
        self.ctl.db.set_setting("phone_obs", conf)
        obs.save_password(self.ctl.db, self.obs_pw.text())
        self.obs_status.setText(tr("deck_obs_testing"))
        self.obs_status.setStyleSheet(f"color: {Palette.text_dim};")
        client = obs.Obs(conf["host"], conf["port"], self.obs_pw.text())
        box = {}

        def work():
            try:
                box["st"] = client.status()
            except obs.ObsError as exc:
                box["err"] = str(exc)
            except OSError as exc:
                box["err"] = str(exc)
        th = threading.Thread(target=work, daemon=True)
        th.start()
        from PyQt6.QtCore import QTimer

        def check():
            if th.is_alive():
                QTimer.singleShot(100, check)
                return
            try:
                if "st" in box:
                    self._obs_scenes = box["st"]["scenes"]
                    self.obs_status.setText(tr("deck_obs_ok", n=len(self._obs_scenes)))
                    self.obs_status.setStyleSheet(f"color: {Palette.success};")
                    if self.cat == "obs":
                        self._show_cat("obs")
                else:
                    err = box.get("err", "")
                    self.obs_status.setText(tr({"not_running": "deck_obs_not_running", "password": "deck_obs_bad_password"}.get(err, "deck_obs_failed")))
                    self.obs_status.setStyleSheet(f"color: {Palette.danger};")
            except RuntimeError:
                pass
        check()
