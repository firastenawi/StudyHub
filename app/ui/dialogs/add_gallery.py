"""
"Add to Study Hub": pick programs, websites, files and folders from ready lists — no typing, no browsing —
tick as many as you like, choose the desktop (and whether they go on the taskbar too), Add.

Tabs: Programs (everything installed) · Websites (suggestions + paste any link) · Files & folders (recent files,
the usual folders, or pick your own) · Selected (what Explorer's right-click handed over).
"""
from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QRectF, QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QListView, QListWidget,
                             QListWidgetItem, QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from app.core import installed_apps as sources
from app.core.i18n import tr
from app.core.launcher import detect_kind, normalize_url, title_from_target
from app.ui.dialogs.base import StyledDialog
from app.ui.theme import Icons, Palette, Theme, icon_font, ink, qcolor, ui_font
from app.ui.widgets.common import button, label, primary_button
from app.ui.widgets.segmented import SegmentedTabs
from app.ui.widgets.tiles import item_pixmap

ROLE = Qt.ItemDataRole.UserRole
ROW_H = 52


def subtitle(e: dict) -> str:
    """Second line of a row: where a program comes from, a site's address, a file's folder."""
    if e["kind"] == "app":
        if e.get("source") == "store":
            return tr("addg_src_store")
        if e.get("source") == "start":
            return tr("addg_src_start")
        return e["target"]
    if e["kind"] == "url":
        return e["target"].split("://", 1)[-1].rstrip("/")
    return e["target"]


class RowDelegate(QStyledItemDelegate):
    """List row: tick circle · icon · name (bold) over a dim second line. Right-to-left aware."""

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ROW_H)

    def paint(self, p: QPainter, option, index):
        e = index.data(ROLE) or {}
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        rtl = option.direction == Qt.LayoutDirection.RightToLeft
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hover:
            p.setPen(QPen(qcolor(Theme.accent(), 0.55), 1) if selected else Qt.PenStyle.NoPen)
            p.setBrush(qcolor(Theme.accent(), 0.16) if selected else ink(0.05))
            p.drawRoundedRect(r, 10, 10)

        def at(x: float, w: float) -> QRectF:  # x measured from the start edge (right in Arabic)
            return QRectF(r.right() - x - w, r.top(), w, r.height()) if rtl else QRectF(r.left() + x, r.top(), w, r.height())

        # tick circle
        box = at(12, 20)
        circ = QRectF(box.center().x() - 10, box.center().y() - 10, 20, 20)
        if selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(Theme.accent()))
            p.drawEllipse(circ)
            p.setPen(QColor("#FFFFFF"))
            p.setFont(icon_font(8))
            p.drawText(circ, Qt.AlignmentFlag.AlignCenter, Icons.ACCEPT)
        else:
            p.setPen(QPen(ink(0.35), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(circ.adjusted(1, 1, -1, -1))
        # icon
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        ibox = at(44, 32)
        if isinstance(icon, QIcon):
            icon.paint(p, QRectF(ibox.center().x() - 16, ibox.center().y() - 16, 32, 32).toRect())
        # texts
        text_box = at(88, r.width() - 100)
        # absolute: the painter is right-to-left in Arabic and would flip a plain "right" back to the left
        align = ((Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter
                 | Qt.AlignmentFlag.AlignAbsolute)
        name = e.get("name", index.data(Qt.ItemDataRole.DisplayRole) or "")
        p.setPen(QColor(Palette.text))
        p.setFont(ui_font(10, QFont.Weight.DemiBold))
        top = QRectF(text_box.left(), r.top() + 6, text_box.width(), 20)
        p.drawText(top, align, p.fontMetrics().elidedText("\u2068" + name + "\u2069", Qt.TextElideMode.ElideRight,
                                                          int(top.width())))
        p.setPen(QColor(Palette.text_dim))
        p.setFont(ui_font(8.6))
        bottom = QRectF(text_box.left(), r.top() + 26, text_box.width(), 18)
        sub = subtitle(e) if e else ""
        p.drawText(bottom, align, p.fontMetrics().elidedText("\u2068" + sub + "\u2069", Qt.TextElideMode.ElideMiddle,
                                                             int(bottom.width())))
        p.restore()


class _AppsLoader(QThread):
    done = pyqtSignal(list)

    def run(self):
        try:
            self.done.emit(sources.installed_apps())
        except Exception:
            self.done.emit([])


class AddGallery(StyledDialog):
    def __init__(self, ctl, desk: str | None = None, paths: list[str] | None = None, parent=None):
        super().__init__(tr("addg_title"), parent, 860)
        self.ctl = ctl
        self.lists: dict[str, QListWidget] = {}
        self.body.addWidget(label(tr("addg_hint"), 9, role="Dim", wrap=True))
        top = QHBoxLayout()
        tabs = [("apps", Icons.APPS, tr("addg_apps")), ("sites", Icons.GLOBE, tr("addg_sites")),
                ("files", Icons.FOLDER, tr("addg_files"))]
        if paths:
            tabs.insert(0, ("picked", Icons.ACCEPT, tr("addg_picked")))
        self.tabs = SegmentedTabs(tabs, tabs[0][0])
        self.tabs.changed.connect(self._show)
        top.addWidget(self.tabs)
        top.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("addg_search"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search)
        self.body.addLayout(top)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        if paths:
            self._page("picked", [self._entry_for_path(p) for p in paths if os.path.exists(p)], select_all=True)
        apps_page = self._page("apps", [])
        self.apps_status = label(tr("addg_loading"), 9, role="Faint")
        apps_page.layout().insertWidget(0, self.apps_status)
        sites = self._page("sites", [{"name": n, "kind": "url", "target": u} for n, u in sources.SUGGESTED_SITES])
        row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText(tr("addg_paste_link"))
        self.url.returnPressed.connect(self._add_url)
        row.addWidget(self.url, 1)
        add_url = button(tr("add"), Icons.ADD, role="Ghost")
        add_url.clicked.connect(self._add_url)
        row.addWidget(add_url)
        sites.layout().insertLayout(0, row)
        files = self._page("files", sources.common_folders() + sources.recent_files())
        row = QHBoxLayout()
        pick_files = button(tr("addg_pick_files"), Icons.DOCUMENT, role="Ghost")
        pick_files.clicked.connect(self._pick_files)
        row.addWidget(pick_files)
        pick_folder = button(tr("addg_pick_folder"), Icons.FOLDER, role="Ghost")
        pick_folder.clicked.connect(self._pick_folder)
        row.addWidget(pick_folder)
        row.addStretch(1)
        files.layout().insertLayout(0, row)
        self.body.addWidget(self.stack, 1)
        self._show(tabs[0][0])

        # where it goes
        bottom = QHBoxLayout()
        bottom.addWidget(label(tr("addg_to_desk"), 9.5, role="Dim"))
        self.desk = QComboBox()
        desktop = getattr(ctl.window, "desktop", None)
        keys = desktop.desk_keys() if desktop is not None else [("all", tr("desk_main"))]
        for key, name in keys:
            self.desk.addItem(name, key)
        current = desk if desk is not None else ctl.current_desk()
        self.desk.setCurrentIndex(max(0, self.desk.findData(str(current))))
        bottom.addWidget(self.desk)
        bottom.addSpacing(12)
        self.pin = QCheckBox(tr("addg_pin_taskbar"))
        bottom.addWidget(self.pin)
        bottom.addStretch(1)
        self.count = label("", 9.5, role="Dim")
        bottom.addWidget(self.count)
        cancel = button(tr("cancel"), role="Ghost")
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        self.add_btn = primary_button(tr("add"), Icons.ADD)
        self.add_btn.clicked.connect(self._add)
        bottom.addWidget(self.add_btn)
        self.body.addLayout(bottom)
        self._update_count()

        self._loader = _AppsLoader(self)
        self._loader.done.connect(self._apps_loaded)
        self._loader.start()

    # ------------------------------------------------------------------ lists
    def _page(self, key: str, entries: list[dict], select_all: bool = False) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lst = QListWidget()
        lst.setObjectName("AddList")
        lst.setViewMode(QListView.ViewMode.ListMode)  # a list: name + where it comes from, easy to scan
        lst.setUniformItemSizes(True)
        lst.setMouseTracking(True)
        lst.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lst.setItemDelegate(RowDelegate(lst))
        lst.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        lst.setMinimumHeight(420)
        lst.itemSelectionChanged.connect(self._update_count)
        lay.addWidget(lst, 1)
        self.lists[key] = lst
        self.pages[key] = page
        self.stack.addWidget(page)
        for e in entries:
            self._add_entry(lst, e, selected=select_all)
        return page

    @staticmethod
    def _entry_for_path(path: str) -> dict:
        kind = detect_kind(path)
        return {"name": title_from_target(path, kind), "kind": kind, "target": os.path.normpath(path)}

    def _add_entry(self, lst: QListWidget, e: dict, selected: bool = False) -> QListWidgetItem:
        pm = item_pixmap({"kind": e["kind"], "target": e["target"], "title": e["name"], "icon_path": ""})
        if e["kind"] == "folder":  # the folder's own Windows icon (Downloads, Pictures…)
            from app.core.shell_icons import shell_pixmap
            real = shell_pixmap(e["target"], 48)
            if not real.isNull():
                pm = real
        item = QListWidgetItem(QIcon(pm), e["name"])
        item.setData(ROLE, e)
        item.setToolTip(e["target"])
        item.setSizeHint(QSize(0, ROW_H))
        lst.addItem(item)
        if selected:
            item.setSelected(True)
        return item

    def _apps_loaded(self, apps: list) -> None:
        lst = self.lists["apps"]
        for e in apps:
            self._add_entry(lst, e)
        self.apps_status.setText(tr("addg_apps_found", n=len(apps)))
        self._filter(self.search.text())

    def _show(self, key: str) -> None:
        self.stack.setCurrentWidget(self.pages[key])
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        q = text.strip().lower()
        lst = self.stack.currentWidget().findChild(QListWidget)
        if lst is None:
            return
        for i in range(lst.count()):
            it = lst.item(i)
            it.setHidden(bool(q) and q not in it.text().lower() and q not in it.toolTip().lower())

    # ------------------------------------------------------------ own entries
    def _add_url(self) -> None:
        url = normalize_url(self.url.text().strip())
        if not url:
            self.ctl.notify(tr("error_invalid_url"), "error")
            return
        self._add_entry(self.lists["sites"], {"name": title_from_target(url, "url"), "kind": "url", "target": url},
                        selected=True)
        self.lists["sites"].scrollToBottom()
        self.url.clear()

    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, tr("addg_pick_files"), str(Path.home()))
        for p in paths:
            self._add_entry(self.lists["files"], self._entry_for_path(p), selected=True)

    def _pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, tr("addg_pick_folder"), str(Path.home()))
        if path:
            self._add_entry(self.lists["files"], self._entry_for_path(path), selected=True)

    # -------------------------------------------------------------------- add
    def chosen(self) -> list[dict]:
        out, seen = [], set()
        for lst in self.lists.values():
            for it in lst.selectedItems():
                e = it.data(ROLE)
                key = (e["kind"], e["target"].lower())
                if key not in seen:
                    seen.add(key)
                    out.append(e)
        return out

    def _update_count(self) -> None:
        if not hasattr(self, "add_btn"):  # items ticked while the window is still being built
            return
        n = len(self.chosen())
        self.count.setText(tr("addg_n_selected", n=n) if n else "")
        self.add_btn.setEnabled(n > 0)
        self.add_btn.setText(tr("addg_add_n", n=n) if n else tr("add"))

    def _add(self) -> None:
        entries = self.chosen()
        if not entries:
            return
        self.ctl.add_entries(entries, self.desk.currentData(), self.pin.isChecked())
        self.accept()
