"""
Phone section: everything between the phone and this laptop over the local Wi-Fi.

  left  : on/off, QR code (with the pairing code inside its #fragment), addresses, pairing code
  right : stats, then tabs
          Received      history of notes / links / tasks / clipboard / files from phones (filter, search, open)
          Send to phone text, links, clipboard and files waiting for the phone (drag files in)
          Devices       paired phones: rename, unpair one, unpair all
          Access        what the phone app may do: desktops, study, shared folders, screen, remote, clipboard, sound
          Phone screen  the phone shown on the laptop with touch / mouse control (scrcpy, USB or Wi-Fi)
          Options       allow pairing, pin received items, folder, largest file, port
"""
from __future__ import annotations

import os
from pathlib import Path

import qrcode
from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                             QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget)

from app.core.files import human_size
from app.core.i18n import ltr, relative_time, tr
from app.core.lan_share import pretty_code
from app.ui.animations import AnimatedStack, clear_list, stagger_in
from app.ui.dialogs.study_dialogs import chip
from app.ui.pages.base import Page
from app.ui.theme import Icons, Palette, Theme, ui_font
from app.ui.widgets.common import (Card, EmptyState, GlyphPlate, IconButton, ToggleSwitch, add_action, button, label,
                                   make_menu, primary_button)
from app.ui.widgets.segmented import SegmentedTabs

KIND_STYLE = {  # glyph, color
    "note": (Icons.NOTES, "#A78BFA"), "text": (Icons.NOTES, "#A78BFA"), "link": (Icons.LINK, "#22D3EE"),
    "file": (Icons.DOCUMENT, "#F59E0B"), "task": (Icons.CHECKLIST, "#34D399"), "clip": (Icons.PASTE, "#60A5FA"),
}


class QrView(QWidget):
    """QR code with rounded modules on a white card (phones read dark-on-light best)."""

    def __init__(self, size: int = 236, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.matrix: list[list[bool]] = []

    def set_text(self, text: str) -> None:
        self.matrix = []
        if text:
            qr = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(text)
            qr.make(fit=True)
            self.matrix = qr.get_matrix()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF") if self.matrix else QColor(255, 255, 255, 14))
        p.drawRoundedRect(card, 22, 22)
        if not self.matrix:
            p.setPen(QColor(255, 255, 255, 90))
            p.setFont(QFont("Segoe Fluent Icons", 44))
            p.drawText(card, Qt.AlignmentFlag.AlignCenter, Icons.PHONE)
            return
        n = len(self.matrix)
        quiet = 16
        cell = (card.width() - 2 * quiet) / n
        path = QPainterPath()
        for y, row in enumerate(self.matrix):
            for x, on in enumerate(row):
                if on:
                    path.addRoundedRect(QRectF(card.x() + quiet + x * cell, card.y() + quiet + y * cell,
                                               cell + 0.4, cell + 0.4), cell * 0.32, cell * 0.32)
        p.fillPath(path, QColor("#14152A"))


class StatTile(Card):
    def __init__(self, glyph: str, color: str, title: str):
        super().__init__(padding=14)
        lay = QHBoxLayout()
        lay.setSpacing(12)
        lay.addWidget(GlyphPlate(glyph, color, 38))
        col = QVBoxLayout()
        col.setSpacing(0)
        self.value = label("0", 15, QFont.Weight.DemiBold)
        col.addWidget(self.value)
        col.addWidget(label(title, 8.8, role="Dim"))
        lay.addLayout(col, 1)
        self.root.addLayout(lay)


def _row_widget(glyph: str, color: str, title: str, meta: str, trailing: QWidget | None = None) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(10, 7, 12, 7)
    lay.setSpacing(12)
    lay.addWidget(GlyphPlate(glyph, color, 36))
    col = QVBoxLayout()
    col.setSpacing(1)
    t = label(title, 10.2, QFont.Weight.Medium)
    t.setMaximumWidth(720)
    col.addWidget(t)
    col.addWidget(label(meta, 8.6, role="Dim"))
    lay.addLayout(col, 1)
    if trailing is not None:
        lay.addWidget(trailing)
    return w


def _one_line(text: str, limit: int = 90) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= limit else line[:limit - 1] + "…"


def _list() -> QListWidget:
    lst = QListWidget()
    lst.setObjectName("CleanList")
    lst.setSpacing(3)
    lst.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
    lst.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    lst.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
    return lst


# ------------------------------------------------------------------ Received
class ReceivedView(QWidget):
    KINDS = ("", "note", "task", "file", "link", "clip")

    def __init__(self, ctl, page):
        super().__init__()
        self.ctl, self.page = ctl, page
        self.kind = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.group = QButtonGroup(self)
        for key in self.KINDS:
            c = chip(tr(f"kind_{key or 'all'}"))
            c.setChecked(not key)
            c.clicked.connect(lambda _=False, k=key: self._set_kind(k))
            self.group.addButton(c)
            bar.addWidget(c)
        bar.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("phone_search"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(230)
        self.search.textChanged.connect(lambda _: self.refresh())
        bar.addWidget(self.search)
        clear = IconButton(Icons.DELETE, tr("phone_clear"), 34, 10)
        clear.clicked.connect(lambda: ctl.clear_phone_history("in", self))
        bar.addWidget(clear)
        lay.addLayout(bar)
        self.list = _list()
        self.list.itemDoubleClicked.connect(lambda it: self._open(it.data(Qt.ItemDataRole.UserRole)))
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list, 1)
        self.empty = EmptyState(Icons.INBOX, tr("phone_received_empty"))
        lay.addWidget(self.empty)

    def _set_kind(self, kind: str) -> None:
        self.kind = kind
        self.refresh()

    def refresh(self) -> None:
        rows = self.ctl.db.list_transfers("in", self.kind or None, self.search.text().strip())
        clear_list(self.list)
        for r in rows:
            glyph, color = KIND_STYLE.get(r["kind"], (Icons.DOCUMENT, Theme.accent()))
            title = r["title"] or _one_line(r["content"])
            if r["kind"] == "note" and r["title"]:
                title = f"{r['title']} — {_one_line(r['content'], 60)}"
            meta = [r["device_name"] or tr("phone_unknown_device"), relative_time(r["created_at"])]
            if r["kind"] == "file":
                meta.append(ltr(human_size(r["size"])))
                if not os.path.exists(r["content"]):
                    meta.append("⚠")
            if r["subject_name"]:
                meta.append(r["subject_name"])
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, r)
            w = _row_widget(glyph, color, title, "  ·  ".join(meta))
            item.setSizeHint(QSize(0, 56))
            self.list.addItem(item)
            self.list.setItemWidget(item, w)
        self.empty.setVisible(not rows)
        self.list.setVisible(bool(rows))

    def _selected(self) -> list[dict]:
        return [it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems()]

    def _open(self, r: dict) -> None:
        if r["kind"] == "file":
            self.ctl.open_path(r["content"])
        elif r["kind"] == "link":
            self.ctl.open_url(r["content"])
        elif r["kind"] == "task":
            self.ctl.open_study_tab("tasks")
        elif r["kind"] == "note":
            self.ctl.show_window("notes")
        else:
            self.ctl.copy_text(r["content"])

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if not item:
            return
        r = item.data(Qt.ItemDataRole.UserRole)
        rows = self._selected() or [r]
        menu = make_menu(self)
        add_action(menu, Icons.OPEN_FOLDER, tr("phone_open_item"), lambda: self._open(r))
        add_action(menu, Icons.COPY, tr("phone_copy_item"), lambda: self.ctl.copy_text(r["content"]))
        if r["kind"] == "file":
            add_action(menu, Icons.FOLDER, tr("phone_reveal"), lambda: self.ctl.reveal_path(r["content"]))
            add_action(menu, Icons.PIN, tr("ctx_pin_file"), lambda: self.ctl.add_path(r["content"]))
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("phone_remove"),
                   lambda: self.ctl.delete_phone_transfers([x["id"] for x in rows]), danger=True)
        menu.exec(self.list.viewport().mapToGlobal(pos))


# --------------------------------------------------------------- Send to phone
class DropList(QListWidget):
    """The outbox list: files dropped here are sent to the phone."""

    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        links = [u.toString() for u in e.mimeData().urls() if not u.isLocalFile()]
        if paths:
            self.ctl.send_paths_to_phone(paths)
        for link in links:
            self.ctl.send_to_phone("link", link)
        e.acceptProposedAction()


class SendView(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        compose = Card(padding=14)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(tr("phone_send_placeholder"))
        self.text.setFixedHeight(84)
        compose.root.addWidget(self.text)
        row = QHBoxLayout()
        files = button(tr("phone_send_files"), Icons.DOCUMENT, role="Ghost")
        files.clicked.connect(lambda: ctl.send_files_to_phone_dialog(self))
        row.addWidget(files)
        clip = button(tr("phone_send_clipboard"), Icons.PASTE, role="Ghost")
        clip.clicked.connect(ctl.send_clipboard_to_phone)
        row.addWidget(clip)
        row.addStretch(1)
        send = primary_button(tr("phone_send_text"), Icons.FORWARD)
        send.clicked.connect(self._send_text)
        row.addWidget(send)
        compose.root.addLayout(row)
        lay.addWidget(compose)

        head = QHBoxLayout()
        head.addStretch(1)
        clear = IconButton(Icons.DELETE, tr("phone_clear"), 34, 10)
        clear.clicked.connect(lambda: ctl.clear_phone_history("out", self))
        head.addWidget(clear)
        lay.addLayout(head)
        self.list = DropList(ctl)
        self.list.setObjectName("CleanList")
        self.list.setSpacing(3)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list, 1)
        self.empty = EmptyState(Icons.PHONE, tr("phone_outbox_empty"))
        lay.addWidget(self.empty)

    def _send_text(self) -> None:
        text = self.text.toPlainText().strip()
        if text and self.ctl.send_to_phone("text", text):
            self.text.clear()

    def refresh(self) -> None:
        rows = self.ctl.db.list_transfers("out")
        clear_list(self.list)
        for r in rows:
            glyph, color = KIND_STYLE.get(r["kind"], (Icons.DOCUMENT, Theme.accent()))
            title = r["title"] or _one_line(r["content"])
            meta = [relative_time(r["created_at"])]
            if r["kind"] == "file":
                meta.append(ltr(human_size(r["size"])))
            status = label(("✓  " + tr("phone_fetched")) if r["fetched"] else tr("phone_waiting"), 8.8,
                           QFont.Weight.DemiBold, "" if r["fetched"] else "Faint")
            if r["fetched"]:
                status.setStyleSheet(f"color: {Palette.success};")
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, r)
            item.setSizeHint(QSize(0, 56))
            self.list.addItem(item)
            self.list.setItemWidget(item, _row_widget(glyph, color, title, "  ·  ".join(meta), status))
        self.empty.setVisible(not rows)

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if not item:
            return
        r = item.data(Qt.ItemDataRole.UserRole)
        rows = [it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems()] or [r]
        menu = make_menu(self)
        if r["kind"] == "file":
            add_action(menu, Icons.FOLDER, tr("phone_reveal"), lambda: self.ctl.reveal_path(r["content"]))
        else:
            add_action(menu, Icons.COPY, tr("phone_copy_item"), lambda: self.ctl.copy_text(r["content"]))
        add_action(menu, Icons.SYNC, tr("phone_resend"),
                   lambda: self.ctl.send_to_phone("file" if r["kind"] == "file" else "text", r["content"], r["title"]))
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("phone_remove"),
                   lambda: self.ctl.delete_phone_transfers([x["id"] for x in rows]), danger=True)
        menu.exec(self.list.viewport().mapToGlobal(pos))


# --------------------------------------------------------------------- Devices
class DevicesView(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.list = _list()
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        lay.addWidget(self.list, 1)
        self.empty = EmptyState(Icons.PHONE, tr("phone_devices_empty"))
        lay.addWidget(self.empty)
        row = QHBoxLayout()
        row.addStretch(1)
        self.unpair_all = button(tr("phone_unpair_all"), Icons.DELETE, role="Danger")
        self.unpair_all.clicked.connect(lambda: ctl.revoke_phone_device(None, self))
        row.addWidget(self.unpair_all)
        lay.addLayout(row)

    def refresh(self) -> None:
        devices = self.ctl.db.list_phone_devices()
        clear_list(self.list)
        for d in devices:
            box = QWidget()
            b = QHBoxLayout(box)
            b.setContentsMargins(0, 0, 0, 0)
            b.setSpacing(6)
            rename = IconButton(Icons.EDIT, tr("rename"), 32, 10)
            rename.clicked.connect(lambda _=False, i=d["id"]: self.ctl.rename_phone_device(i, self))
            b.addWidget(rename)
            unpair = button(tr("phone_unpair"), role="Ghost")
            unpair.clicked.connect(lambda _=False, i=d["id"]: self.ctl.revoke_phone_device(i, self))
            b.addWidget(unpair)
            meta = tr("phone_last_seen", when=relative_time(d["last_seen"] or d["created_at"]))
            if d["last_ip"]:
                meta += "  ·  " + ltr(d["last_ip"])
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 60))
            self.list.addItem(item)
            self.list.setItemWidget(item, _row_widget(Icons.PHONE, Theme.accent(), d["name"], meta, box))
        self.empty.setVisible(not devices)
        self.unpair_all.setVisible(len(devices) > 1)


# --------------------------------------------------------------------- Options
def _option_row(title: str, desc: str, widget: QWidget) -> QWidget:
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 4, 0, 4)
    lay.setSpacing(16)
    col = QVBoxLayout()
    col.setSpacing(2)
    col.addWidget(label(title, 10.3, QFont.Weight.Medium))
    if desc:
        col.addWidget(label(desc, 8.8, role="Dim", wrap=True))
    lay.addLayout(col, 1)
    lay.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class OptionsView(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        card = Card(padding=18)
        db = ctl.db
        self.pairing = ToggleSwitch(bool(db.get_setting("phone_link_allow_pairing", True)))
        self.pairing.toggled.connect(lambda on: ctl.set_phone_option("phone_link_allow_pairing", on))
        card.root.addWidget(_option_row(tr("phone_allow_pairing"), tr("phone_allow_pairing_desc"), self.pairing))
        pin = ToggleSwitch(bool(db.get_setting("phone_link_pin", True)))
        pin.toggled.connect(lambda on: ctl.set_phone_option("phone_link_pin", on))
        card.root.addWidget(_option_row(tr("phone_link_pin"), "", pin))

        folder_box = QWidget()
        fb = QHBoxLayout(folder_box)
        fb.setContentsMargins(0, 0, 0, 0)
        open_btn = IconButton(Icons.OPEN_FOLDER, tr("open"), 34, 10)
        open_btn.clicked.connect(self._open_folder)
        fb.addWidget(open_btn)
        browse = button(tr("browse"), role="Ghost")
        browse.clicked.connect(lambda: ctl.choose_phone_folder(self))
        fb.addWidget(browse)
        self.folder_row = _option_row(tr("phone_link_folder"), "", folder_box)
        card.root.addWidget(self.folder_row)
        self.folder_path = label("", 8.8, role="Dim")
        self.folder_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.root.addWidget(self.folder_path)

        self.size = QComboBox()
        for mb in (10, 25, 50, 100, 250, 500):
            self.size.addItem(ltr(f"{mb} MB"), mb)
        self.size.setCurrentIndex(max(0, self.size.findData(int(db.get_setting("phone_link_max_mb", 50)))))
        self.size.currentIndexChanged.connect(
            lambda _: ctl.set_phone_option("phone_link_max_mb", self.size.currentData()))
        card.root.addWidget(_option_row(tr("phone_max_mb"), tr("phone_max_mb_desc"), self.size))

        self.port = QSpinBox()
        self.port.setRange(1024, 65000)
        self.port.setValue(int(db.get_setting("phone_link_port", 8765)))
        self.port.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.port.editingFinished.connect(lambda: ctl.db.set_setting("phone_link_port", self.port.value()))
        card.root.addWidget(_option_row(tr("phone_port"), tr("phone_port_desc"), self.port))

        warn = label("⚠  " + tr("phone_link_warning"), 8.8, role="Faint", wrap=True)
        card.root.addWidget(warn)
        lay.addWidget(card)
        lay.addStretch(1)

    def _open_folder(self) -> None:
        folder = self.ctl.phone_folder()
        os.makedirs(folder, exist_ok=True)
        self.ctl.open_folder(folder)

    def refresh(self) -> None:
        self.folder_path.setText(ltr(self.ctl.phone_folder()))
        self.pairing.set_checked_silently(bool(self.ctl.db.get_setting("phone_link_allow_pairing", True)))
        if not self.port.hasFocus():
            self.port.setValue(int(self.ctl.db.get_setting("phone_link_port", 8765)))


class AccessView(QWidget):
    """What the phone app may do here. Files / screen / remote are off until switched on, and only work
    over the secure (HTTPS) app."""

    CAPS = [("desktop", Icons.DESKTOP, "#A78BFA"), ("study", Icons.NOTES, "#34D399"),
            ("files", Icons.FOLDER, "#F59E0B"), ("screen", Icons.PREVIEW, "#22D3EE"),
            ("remote", Icons.KEYBOARD, "#F472B6"), ("clipboard", Icons.PASTE, "#60A5FA"),
            ("sound", Icons.MUSIC, "#FB7185")]

    def __init__(self, ctl, page):
        super().__init__()
        self.ctl = ctl
        from app.core.phone_bridge import caps
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        outer.addWidget(Page.scrollable(content))
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(12)
        card = Card(padding=18)
        self.switches: dict[str, ToggleSwitch] = {}
        now = caps(ctl.db)
        for key, glyph, color in self.CAPS:
            sw = ToggleSwitch(now[key])
            sw.toggled.connect(lambda on, k=key: ctl.set_phone_cap(k, on))
            self.switches[key] = sw
            row = _option_row(tr(f"phone_cap_{key}"), tr(f"phone_cap_{key}_desc"), sw)
            row.layout().insertWidget(0, GlyphPlate(glyph, color, 34), 0, Qt.AlignmentFlag.AlignVCenter)
            card.root.addWidget(row)
        secure = label("🔒  " + tr("phone_cap_https"), 8.8, role="Faint", wrap=True)
        card.root.addWidget(secure)
        lay.addWidget(card)

        folders = Card(padding=18)
        head = QHBoxLayout()
        head.addWidget(label(tr("phone_shared_folders"), 10.5, QFont.Weight.DemiBold))
        head.addStretch(1)
        add = button(tr("phone_share_folder"), Icons.ADD, role="Ghost")
        add.clicked.connect(lambda: ctl.add_phone_shared_folder(self))
        head.addWidget(add)
        folders.root.addLayout(head)
        folders.root.addWidget(label(tr("phone_shared_folders_desc"), 8.8, role="Dim", wrap=True))
        self.folder_rows = QVBoxLayout()
        self.folder_rows.setSpacing(4)
        folders.root.addLayout(self.folder_rows)
        lay.addWidget(folders)
        lay.addStretch(1)

    def refresh(self) -> None:
        from app.core.phone_bridge import caps
        for key, on in caps(self.ctl.db).items():
            self.switches[key].set_checked_silently(on)
        while self.folder_rows.count():
            item = self.folder_rows.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        folders = self.ctl.db.get_setting("phone_shared_folders", []) or []
        if not folders:
            self.folder_rows.addWidget(label(tr("phone_no_shared"), 9, role="Faint"))
        for folder in folders:
            remove = IconButton(Icons.CLOSE, tr("remove"), 28, 9)
            remove.clicked.connect(lambda _=False, f=folder: self.ctl.remove_phone_shared_folder(f))
            missing = "" if os.path.isdir(folder) else "  ⚠"
            self.folder_rows.addWidget(_row_widget(Icons.FOLDER, "#F59E0B", Path(folder).name or folder,
                                                   ltr(folder) + missing, remove))


class MirrorView(QWidget):
    """The phone's screen in a window on the laptop, with touch / mouse / keyboard (scrcpy)."""

    def __init__(self, ctl, page):
        super().__init__()
        self.ctl = ctl
        self.mirror = ctl.phone_mirror()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        outer.addWidget(Page.scrollable(content))
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(12)

        # ---- the big button + status
        top = Card(padding=18)
        row = QHBoxLayout()
        row.addWidget(GlyphPlate(Icons.PHONE, "#22D3EE", 44))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(tr("mirror_title"), 12, QFont.Weight.DemiBold))
        col.addWidget(label(tr("mirror_desc"), 8.8, role="Dim", wrap=True))
        row.addLayout(col, 1)
        self.go = primary_button(tr("mirror_show"), Icons.PLAY)
        self.go.clicked.connect(self._go)
        row.addWidget(self.go)
        top.root.addLayout(row)
        self.status = label("", 8.8, role="Faint", wrap=True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.root.addWidget(self.status)
        self.get_box = QWidget()
        gb = QHBoxLayout(self.get_box)
        gb.setContentsMargins(0, 0, 0, 0)
        winget = button(tr("mirror_winget"), Icons.DOWNLOAD)
        winget.clicked.connect(self._winget)
        gb.addWidget(winget)
        page_btn = button(tr("mirror_download_page"), Icons.GLOBE, role="Ghost")
        page_btn.clicked.connect(self._download_page)
        gb.addWidget(page_btn)
        locate = button(tr("mirror_locate"), Icons.SEARCH, role="Ghost")
        locate.clicked.connect(self._locate)
        gb.addWidget(locate)
        gb.addStretch(1)
        top.root.addWidget(self.get_box)
        lay.addWidget(top)

        # ---- connection
        conn = Card(padding=18)
        conn.root.addWidget(label(tr("mirror_connection"), 10.5, QFont.Weight.DemiBold))
        self.mode = SegmentedTabs([("wifi", Icons.GLOBE, tr("mirror_wifi")), ("usb", Icons.DRIVE, tr("mirror_usb"))],
                                  self.mirror.options()["mode"])
        self.mode.changed.connect(lambda m: (self.mirror.save(mode=m), self.refresh()))
        mrow = QHBoxLayout()
        mrow.addWidget(self.mode)
        mrow.addStretch(1)
        conn.root.addLayout(mrow)
        self.wifi_box = QWidget()
        wl = QVBoxLayout(self.wifi_box)
        wl.setContentsMargins(0, 4, 0, 0)
        wl.setSpacing(8)
        self.addr = QLineEdit(self.mirror.options()["wifi_addr"])
        self.addr.setPlaceholderText("192.168.1.23:37123")
        self.addr.setMinimumWidth(230)
        self.addr.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.addr.editingFinished.connect(lambda: self.mirror.save(wifi_addr=self.addr.text().strip()))
        wl.addWidget(_option_row(tr("mirror_addr"), tr("mirror_addr_desc"), self.addr))
        pair_row = QHBoxLayout()
        self.pair_addr = QLineEdit()
        self.pair_addr.setPlaceholderText(tr("mirror_pair_addr_ph"))
        self.pair_addr.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.pair_code = QLineEdit()
        self.pair_code.setPlaceholderText(tr("mirror_pair_code_ph"))
        self.pair_code.setMaxLength(6)
        self.pair_code.setFixedWidth(130)
        self.pair_btn = button(tr("mirror_pair"), Icons.LINK, role="Ghost")
        self.pair_btn.clicked.connect(lambda: self.mirror.pair(self.pair_addr.text(), self.pair_code.text()))
        pair_row.addWidget(self.pair_addr, 1)
        pair_row.addWidget(self.pair_code)
        pair_row.addWidget(self.pair_btn)
        wl.addWidget(label(tr("mirror_pair_title"), 10, QFont.Weight.Medium))
        wl.addLayout(pair_row)
        wl.addWidget(label(tr("mirror_wifi_steps"), 8.8, role="Dim", wrap=True))
        conn.root.addWidget(self.wifi_box)
        self.usb_hint = label(tr("mirror_usb_steps"), 8.8, role="Dim", wrap=True)
        conn.root.addWidget(self.usb_hint)
        lay.addWidget(conn)

        # ---- options
        opts = Card(padding=18)
        o = self.mirror.options()
        self.switches = {}
        for key, title, desc in (("audio", "mirror_audio", "mirror_audio_desc"),
                                 ("screen_off", "mirror_screen_off", "mirror_screen_off_desc"),
                                 ("stay_awake", "mirror_stay_awake", ""), ("on_top", "mirror_on_top", "")):
            sw = ToggleSwitch(bool(o[key]))
            sw.toggled.connect(lambda on, k=key: self.mirror.save(**{k: on}))
            self.switches[key] = sw
            opts.root.addWidget(_option_row(tr(title), tr(desc) if desc else "", sw))
        opts.root.addWidget(label("🖐  " + tr("mirror_touch_hint"), 8.8, role="Faint", wrap=True))
        lay.addWidget(opts)
        lay.addStretch(1)
        self.mirror.changed.connect(self.refresh)

    def _go(self) -> None:
        if self.mirror.running:
            self.mirror.stop()
        else:
            self.mirror.save(wifi_addr=self.addr.text().strip())
            self.mirror.start()

    def _download_page(self) -> None:
        from app.core.phone_mirror import RELEASES_URL
        self.ctl.open_url(RELEASES_URL)

    def _winget(self) -> None:
        from app.core import phone_mirror
        from app.ui.dialogs.base import ConfirmDialog
        if ConfirmDialog.ask(tr("mirror_winget"), tr("mirror_winget_confirm"), tr("mirror_install"), self, danger=False):
            if not phone_mirror.install_with_winget():
                self.ctl.notify(tr("mirror_no_winget"), "error")

    def _locate(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, tr("mirror_locate"), str(Path.home()), "scrcpy.exe (scrcpy.exe)")
        if path:
            self.mirror.save(path=os.path.normpath(path))

    def refresh(self) -> None:
        try:
            self.isVisible()
        except RuntimeError:
            return
        m = self.mirror
        o = m.options()
        found = m.scrcpy()
        wifi = o["mode"] == "wifi"
        self.wifi_box.setVisible(wifi)
        self.usb_hint.setVisible(not wifi)
        self.get_box.setVisible(not found)
        self.go.setText(tr("mirror_close") if m.running else tr("mirror_show"))
        self.go.setEnabled(bool(found) and not m.busy)
        self.pair_btn.setEnabled(bool(found) and not m.busy)
        self.status.setText(("✓ " + ltr(found)) if found else "⚠  " + tr("mirror_no_scrcpy"))
        for key, sw in self.switches.items():
            sw.set_checked_silently(bool(o[key]))


# ------------------------------------------------------------------------ page
class PhonePage(Page):
    def __init__(self, ctl, main_window):
        super().__init__(ctl, main_window)
        self._chosen_url: str | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(18)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(tr("nav_phone"), 22, QFont.Weight.DemiBold))
        titles.addWidget(label(tr("phone_subtitle"), 10, role="Dim"))
        header.addLayout(titles)
        header.addStretch(1)
        self.status = label("", 10, QFont.Weight.DemiBold)
        header.addWidget(self.status)
        self.switch = ToggleSwitch(ctl.phone.running)
        self.switch.toggled.connect(self._toggle)
        header.addWidget(self.switch)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(18)

        # ---- left: connect card
        self.connect = Card(padding=20)
        self.connect.setFixedWidth(300)
        c = self.connect.root
        c.setSpacing(10)
        self.qr = QrView()
        c.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignHCenter)
        self.qr_hint = label(tr("phone_qr_hint"), 8.8, role="Faint")
        self.qr_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c.addWidget(self.qr_hint)
        self.turn_on = primary_button(tr("phone_turn_on"), Icons.PHONE)
        self.turn_on.clicked.connect(lambda: ctl.set_phone_link(True))
        c.addWidget(self.turn_on)
        c.addWidget(label(tr("phone_link_address"), 8.8, role="Faint"))
        self.address = label("", 11.5, QFont.Weight.DemiBold)
        self.address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        c.addWidget(self.address)
        self.others = QHBoxLayout()
        self.others.setSpacing(4)
        c.addLayout(self.others)
        c.addWidget(label(tr("phone_link_code"), 8.8, role="Faint"))
        code_row = QHBoxLayout()
        self.code = label("", 15, QFont.Weight.Bold)
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        code_row.addWidget(self.code)
        code_row.addStretch(1)
        renew = IconButton(Icons.REFRESH, tr("phone_link_new_code"), 32, 10)
        renew.clicked.connect(ctl.new_phone_code)
        code_row.addWidget(renew)
        c.addLayout(code_row)
        self.steps = label(tr("phone_link_steps"), 8.8, role="Dim", wrap=True)
        c.addWidget(self.steps)
        app_hint = label("📲  " + tr("phone_app_hint"), 8.8, wrap=True)
        app_hint.setStyleSheet(f"color: {Theme.accent2()};")
        c.addWidget(app_hint)
        try:  # what the Android app shows the first time: compare before trusting
            from app.core.local_ca import ca_fingerprint
            fp = ca_fingerprint()
        except Exception:
            fp = "—"
        fp_label = label("🔐  " + tr("phone_ca_fp") + "\n" + fp, 8.2, role="Faint", wrap=True)
        fp_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        fp_label.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        c.addWidget(fp_label)
        c.addStretch(1)
        body.addWidget(self.connect)

        # ---- right: stats + tabs
        right = QVBoxLayout()
        right.setSpacing(14)
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.tiles = {
            "today": StatTile(Icons.INBOX, "#22D3EE", tr("phone_stat_today")),
            "received": StatTile(Icons.DOWNLOAD, "#A78BFA", tr("phone_stat_received")),
            "bytes": StatTile(Icons.DOCUMENT, "#F59E0B", tr("phone_stat_files")),
            "sent": StatTile(Icons.FORWARD, "#34D399", tr("phone_stat_sent")),
            "devices": StatTile(Icons.PHONE, "#60A5FA", tr("phone_stat_devices")),
        }
        for tile in self.tiles.values():
            stats.addWidget(tile)
        right.addLayout(stats)
        self.tabs = SegmentedTabs([("received", Icons.INBOX, tr("tab_received")),
                                   ("send", Icons.FORWARD, tr("tab_send")),
                                   ("devices", Icons.PHONE, tr("tab_devices")),
                                   ("access", Icons.SHIELD, tr("tab_phone_access")),
                                   ("mirror", Icons.PREVIEW, tr("tab_phone_mirror")),
                                   ("options", Icons.SETTINGS, tr("tab_phone_settings"))], "received")
        self.tabs.changed.connect(self.show_tab)
        tabs_row = QHBoxLayout()
        tabs_row.addWidget(self.tabs)
        tabs_row.addStretch(1)
        right.addLayout(tabs_row)
        self.stack = AnimatedStack()
        self.views = {"received": ReceivedView(ctl, self), "send": SendView(ctl, self),
                      "devices": DevicesView(ctl, self), "access": AccessView(ctl, self),
                      "mirror": MirrorView(ctl, self),
                      "options": OptionsView(ctl, self)}
        for v in self.views.values():
            self.stack.addWidget(v)
        right.addWidget(self.stack, 1)
        self.current = "received"
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        self.listen(ctl.data_changed, self.refresh)
        self.refresh("phone")

    # --------------------------------------------------------------- actions
    def _toggle(self, on: bool) -> None:
        if not self.ctl.set_phone_link(on):
            self.switch.set_checked_silently(False)

    def show_tab(self, key: str) -> None:
        if key in self.views:
            self.current = key
            self.tabs.set_current(key)
            self.stack.switch_to(self.stack.indexOf(self.views[key]), animate=self.isVisible())

    def _choose(self, url: str) -> None:
        self._chosen_url = url
        self._refresh_connect()

    # --------------------------------------------------------------- refresh
    def refresh(self, what: str = "") -> None:
        if what not in ("phone", "phone_devices", "subjects", ""):
            return
        self._refresh_connect()
        stats = self.ctl.db.transfer_stats()
        devices = self.ctl.db.list_phone_devices()
        self.tiles["today"].value.setText(str(stats["today"]))
        self.tiles["received"].value.setText(str(stats["received"]))
        self.tiles["bytes"].value.setText(ltr(human_size(stats["bytes"])))
        self.tiles["sent"].value.setText(str(stats["sent"]))
        self.tiles["devices"].value.setText(str(len(devices)))
        waiting = sum(1 for r in self.ctl.db.list_transfers("out", limit=50) if not r["fetched"])
        self.tabs.set_badges({"send": waiting})
        if what == "phone_devices":
            self.views["devices"].refresh()
            return
        for v in self.views.values():
            v.refresh()

    def _refresh_connect(self) -> None:
        ctl = self.ctl
        running = ctl.phone.running
        self.switch.set_checked_silently(running)
        self.status.setText(tr("phone_status_on") if running else tr("phone_status_off"))
        self.status.setStyleSheet(f"color: {Palette.success if running else Palette.text_faint};")
        code = ctl.phone.code
        self.code.setText(ltr(pretty_code(code)))
        while self.others.count():
            item = self.others.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        urls = ctl.phone.urls()
        self.turn_on.setVisible(not running)
        self.qr_hint.setVisible(running and bool(urls))
        if running and urls:
            url = self._chosen_url if self._chosen_url in urls else urls[0]
            self.qr.set_text(f"{url}#k={code}")
            self.address.setText(ltr(url.removeprefix("http://").rstrip("/")))
            for other in urls[:4]:
                if other != url:
                    b = button(ltr(other.removeprefix("http://").rsplit(":", 1)[0]), role="Ghost")
                    b.setFont(ui_font(8))
                    b.setToolTip(other)
                    b.clicked.connect(lambda _=False, u=other: self._choose(u))
                    self.others.addWidget(b)
            self.others.addStretch(1)
        else:
            self.qr.set_text("")
            self.address.setText(tr("phone_link_no_network") if running else "—")

    def on_shown(self) -> None:
        stagger_in([self.connect, *self.tiles.values()], step=40)
