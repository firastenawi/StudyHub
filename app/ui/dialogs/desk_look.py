"""One desktop's own look: its wallpaper (or the general one) and its own apps on the taskbar (or the general
ones). Switching desktops switches both — e.g. "Study": notebook + timer + PDF, "Lab": lab + servers."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QAbstractItemView, QHBoxLayout, QListWidget, QListWidgetItem, QWidget

from app.core.i18n import tr
from app.ui import apps as app_registry
from app.ui.dialogs.base import StyledDialog
from app.ui.pages.settings_page import WallpaperSwatch
from app.ui.theme import Icons, Palette, Theme, ui_font
from app.ui.widgets.common import ToggleSwitch, button, label, primary_button


class DeskLookDialog(StyledDialog):
    def __init__(self, ctl, desk: str, name: str, parent=None):
        super().__init__(tr("desk_look_title", name=name), parent, 600)
        self.ctl, self.desk = ctl, desk
        look = ctl.desk_look(desk)
        self.wallpaper = look.get("wallpaper")  # None = the general wallpaper
        self.body.addWidget(label(tr("desk_look_hint"), 9, role="Dim", wrap=True))

        # wallpaper
        self.body.addWidget(label(tr("wallpaper"), 10.5))
        self.walls = QHBoxLayout()
        self.walls.setSpacing(6)
        self.body.addLayout(self.walls)
        self._fill_walls()

        # taskbar apps
        row = QHBoxLayout()
        col_text = label(tr("desk_own_apps"), 10.5)
        row.addWidget(col_text, 1)
        self.own = ToggleSwitch(look.get("pinned") is not None)
        self.own.toggled.connect(lambda on: self.list.setEnabled(on))
        row.addWidget(self.own)
        self.body.addLayout(row)
        self.body.addWidget(label(tr("desk_own_apps_desc"), 8.8, role="Dim", wrap=True))
        self.list = QListWidget()
        self.list.setObjectName("PinList")
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setIconSize(QSize(24, 24))
        self.list.setMinimumHeight(260)
        pins = look.get("pinned") or ctl.taskbar_conf(global_only=True)["pinned"]
        apps = app_registry.all_apps(ctl)
        apps += [i for i in (app_registry.find(ctl, k) for k in pins if k.startswith("item:")) if i]
        by = {a.key: a for a in apps}
        for key in [k for k in pins if k in by] + [a.key for a in apps if a.key not in pins]:
            info = by[key]
            item = QListWidgetItem(info.icon(), info.title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked if key in pins else Qt.CheckState.Unchecked)
            item.setSizeHint(QSize(0, 36))
            self.list.addItem(item)
        self.list.setEnabled(self.own.isChecked())
        self.body.addWidget(self.list)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = button(tr("cancel"), role="Ghost")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = primary_button(tr("save"), Icons.ACCEPT)
        ok.clicked.connect(self._save)
        buttons.addWidget(ok)
        self.body.addLayout(buttons)

    def _fill_walls(self) -> None:
        from app.ui.widgets.background import PRESETS
        while self.walls.count():
            w = self.walls.takeAt(0).widget()
            if w:
                w.deleteLater()
        general = WallpaperSwatch("general", self.ctl.wallpaper(), tr("desk_general_wallpaper"))
        general.setChecked(self.wallpaper is None)
        general.clicked.connect(lambda: self._set_wall(None))
        self.walls.addWidget(general)
        current = self.wallpaper or {}
        key_now = current.get("preset") if current.get("kind") == "preset" else current.get("kind")
        options = [("aurora", {"kind": "aurora"})] + [(n, {"kind": "preset", "preset": n}) for n in PRESETS]
        options += [("plain", {"kind": "plain"}),
                    ("image", {"kind": "image", "path": current.get("path", "") if current.get("kind") == "image" else ""})]
        for key, wp in options:
            sw = WallpaperSwatch(key, wp, tr(f"wallpaper_{key}") if key not in ("image",) else tr("wallpaper_pick"))
            sw.setChecked(self.wallpaper is not None and key == key_now)
            if key == "image":
                sw.clicked.connect(self._pick_image)
            else:
                sw.clicked.connect(lambda _=False, w=wp: self._set_wall(dict(w)))
            self.walls.addWidget(sw)
        self.walls.addStretch(1)

    def _set_wall(self, wp) -> None:
        self.wallpaper = None if wp is None else {**self.ctl.wallpaper(), **wp}
        self._fill_walls()

    def _pick_image(self) -> None:
        path = self.ctl.import_wallpaper_image(self)
        if path:
            self._set_wall({"kind": "image", "path": path})

    def _save(self) -> None:
        pinned = None
        if self.own.isChecked():
            pinned = [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                      if self.list.item(i).checkState() == Qt.CheckState.Checked]
        self.ctl.set_desk_look(self.desk, wallpaper=self.wallpaper, pinned=pinned)
        self.accept()
