"""Dialog to add / edit a launcher item (program, website, folder or file)."""
from __future__ import annotations

import os

from PyQt6.QtWidgets import QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QPushButton

from app.core.i18n import tr
from app.core.launcher import detect_kind, normalize_url, title_from_target
from app.ui.dialogs.base import StyledDialog
from app.ui.theme import Icons, glyph_icon
from app.ui.widgets.common import IconButton, label

KINDS = [("app", Icons.APPS, "kind_app"), ("url", Icons.GLOBE, "kind_url"),
         ("folder", Icons.FOLDER, "kind_folder"), ("file", Icons.DOCUMENT, "kind_file")]


class AddItemDialog(StyledDialog):
    def __init__(self, groups: list[dict], item: dict | None = None, kind: str | None = None,
                 target: str = "", group_id: int | None = None, parent=None):
        super().__init__(tr("edit_item") if item else tr("add_item"), parent, 500)
        item = item or {}
        self._title_touched = bool(item.get("title"))

        # kind selector (segmented chips)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.kind_group = QButtonGroup(self)
        self.kind_buttons: dict[str, QPushButton] = {}
        for key, glyph, text_key in KINDS:
            btn = QPushButton(glyph_icon(glyph, "#C9CFDD", 16), " " + tr(text_key))
            btn.setObjectName("Chip")
            btn.setCheckable(True)
            self.kind_group.addButton(btn)
            self.kind_buttons[key] = btn
            btn.toggled.connect(lambda on, k=key: on and self._on_kind(k))
            row.addWidget(btn)
        row.addStretch(1)
        self.body.addLayout(row)

        self.body.addWidget(label(tr("field_target"), 9.5, role="Dim"))
        target_row = QHBoxLayout()
        self.target = QLineEdit(item.get("target", target))
        self.target.textChanged.connect(self._on_target_changed)
        target_row.addWidget(self.target, 1)
        self.browse = IconButton(Icons.OPEN_FOLDER, tr("browse"), 40)
        self.browse.clicked.connect(self._browse)
        target_row.addWidget(self.browse)
        self.body.addLayout(target_row)

        self.body.addWidget(label(tr("field_title"), 9.5, role="Dim"))
        self.title = QLineEdit(item.get("title", ""))
        self.title.textEdited.connect(lambda _: setattr(self, "_title_touched", True))
        self.body.addWidget(self.title)

        self.args_label = label(tr("field_args"), 9.5, role="Dim")
        self.args = QLineEdit(item.get("args", ""))
        self.args.setPlaceholderText(tr("optional"))
        self.body.addWidget(self.args_label)
        self.body.addWidget(self.args)

        self.body.addWidget(label(tr("field_group"), 9.5, role="Dim"))
        self.group = QComboBox()
        self.group.addItem(tr("no_group"), None)
        for g in groups:
            self.group.addItem(g["name"], g["id"])
        wanted = item.get("group_id", group_id)
        index = self.group.findData(wanted)
        self.group.setCurrentIndex(max(0, index))
        self.body.addWidget(self.group)

        self.error = label("", 9, role="Faint")
        self.error.setStyleSheet("color: #F87171;")
        self.body.addWidget(self.error)
        self.add_buttons(tr("save"))

        initial = item.get("kind") or kind or (detect_kind(target) if target else "app")
        self.kind_buttons[initial].setChecked(True)
        if target and not item:
            self._on_target_changed(target)

    def _kind(self) -> str:
        return next(k for k, b in self.kind_buttons.items() if b.isChecked())

    def _on_kind(self, kind: str) -> None:
        self.browse.setVisible(kind != "url")
        self.args.setVisible(kind == "app")
        self.args_label.setVisible(kind == "app")
        self.target.setPlaceholderText(tr(f"placeholder_{kind}"))
        self.adjustSize()

    def _on_target_changed(self, text: str) -> None:
        text = text.strip().strip('"')
        if not text:
            return
        if not self._title_touched:
            kind = self._kind()
            self.title.setText(title_from_target(text, kind))
        if normalize_url(text) and not os.path.exists(text):
            self.kind_buttons["url"].setChecked(True)

    def _browse(self) -> None:
        kind = self._kind()
        if kind == "folder":
            path = QFileDialog.getExistingDirectory(self, tr("browse"))
        else:
            filters = tr("filter_programs") + " (*.exe *.lnk *.bat *.cmd);;* (*)" if kind == "app" else "* (*)"
            path, _ = QFileDialog.getOpenFileName(self, tr("browse"), "", filters)
        if path:
            self.target.setText(os.path.normpath(path))

    def validate(self) -> bool:
        kind, target = self._kind(), self.target.text().strip().strip('"')
        if not target:
            self.error.setText(tr("error_target_required"))
            return False
        if kind == "url" and not normalize_url(target):
            self.error.setText(tr("error_invalid_url"))
            return False
        if kind in ("folder", "file") and not os.path.exists(target):
            self.error.setText(tr("error_path_missing"))
            return False
        return True

    def result_data(self) -> dict:
        kind = self._kind()
        target = self.target.text().strip().strip('"')
        if kind == "url":
            target = normalize_url(target) or target
        return {
            "kind": kind,
            "target": target,
            "title": self.title.text().strip() or title_from_target(target, kind),
            "args": self.args.text().strip() if kind == "app" else "",
            "group_id": self.group.currentData(),
        }
