"""Helpers shared by the Files page views."""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QWidget

from app.core.i18n import tr
from app.ui.theme import Icons
from app.ui.widgets.common import add_action, make_menu


def file_list() -> QListWidget:
    lst = QListWidget()
    lst.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
    lst.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
    lst.setSpacing(1)
    return lst


def add_row(lst: QListWidget, row: QWidget, data) -> QListWidgetItem:
    item = QListWidgetItem()
    item.setData(Qt.ItemDataRole.UserRole, data)
    item.setSizeHint(QSize(0, max(46, row.sizeHint().height())))
    lst.addItem(item)
    lst.setItemWidget(item, row)
    return item


def selected_paths(lst: QListWidget, fallback: str | None = None) -> list[str]:
    paths = [i.data(Qt.ItemDataRole.UserRole)["path"] for i in lst.selectedItems()]
    if fallback and fallback not in paths:
        return [fallback]
    return paths or ([fallback] if fallback else [])


def file_menu(parent, ctl, entry: dict, paths: list[str], extra=None):
    """Context menu shared by every file list."""
    menu = make_menu(parent)
    path = entry["path"]
    add_action(menu, Icons.CHEVRON, tr("open"), lambda: ctl.open_path(path))
    add_action(menu, Icons.OPEN_FOLDER, tr("open_location"), lambda: ctl.reveal_path(path))
    menu.addSeparator()
    add_action(menu, Icons.TAG, tr("tag_files_n", n=len(paths)) if len(paths) > 1 else tr("tag_file"),
               lambda: ctl.tag_paths(paths, parent))
    add_action(menu, Icons.PIN, tr("pin_launcher"), lambda: [ctl.add_path(p) for p in paths])
    add_action(menu, Icons.ARCHIVE, tr("archive_action"), lambda: ctl.archive_dialog(paths, parent))
    add_action(menu, Icons.PHONE, tr("ctx_send_phone"), lambda: ctl.send_paths_to_phone(paths))
    add_action(menu, Icons.COPY, tr("copy_path"), lambda: ctl.copy_text("\n".join(paths)))
    if extra:
        menu.addSeparator()
        extra(menu)
    return menu
