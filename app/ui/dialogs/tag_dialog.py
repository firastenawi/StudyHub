"""Tag one or more files: pick existing tags, create new ones, assign a subject."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

from app.core.i18n import tr
from app.ui.dialogs.base import StyledDialog
from app.ui.dialogs.study_dialogs import subject_combo
from app.ui.theme import Icons, ui_font
from app.ui.widgets.common import IconButton, label
from app.ui.widgets.files import TAG_COLORS
from app.ui.widgets.tiles import FlowLayout


class TagFileDialog(StyledDialog):
    def __init__(self, paths: list[str], tags: list[dict], subjects: list[dict],
                 selected_tags: set[int], subject_id: int | None, parent=None):
        title = tr("tag_files_n", n=len(paths)) if len(paths) > 1 else tr("tag_file")
        super().__init__(title, parent, 520)
        names = ", ".join(Path(p).name for p in paths[:3]) + ("…" if len(paths) > 3 else "")
        self.body.addWidget(label(names, 9.5, role="Dim", wrap=True))
        self.new_tags: list[tuple[str, str]] = []

        self.body.addWidget(label(tr("tags"), 9.5, role="Dim"))
        self.chips_host = QWidget()
        self.flow = FlowLayout(self.chips_host, spacing=8)
        self.chips: dict = {}
        for t in tags:
            self._add_chip(t["id"], t["name"], t["color"], t["id"] in selected_tags)
        self.body.addWidget(self.chips_host)

        new_row = QHBoxLayout()
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText(tr("new_tag_ph"))
        self.new_name.returnPressed.connect(self._create)
        new_row.addWidget(self.new_name, 1)
        add = IconButton(Icons.ADD, tr("new_tag"), 40)
        add.clicked.connect(self._create)
        new_row.addWidget(add)
        self.body.addLayout(new_row)

        self.body.addWidget(label(tr("subject"), 9.5, role="Dim"))
        self.subject = subject_combo(subjects, subject_id, tr("no_subject"))
        self.body.addWidget(self.subject)
        self._color_index = len(tags)
        self.add_buttons(tr("save"))

    def _add_chip(self, key, name: str, color: str, checked: bool):
        chip = QPushButton(name)
        chip.setObjectName("Chip")
        chip.setCheckable(True)
        chip.setChecked(checked)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setFont(ui_font(9.5))
        chip.setStyleSheet(f"QPushButton#Chip:checked {{ border-color: {color}; color: {color}; }}")
        self.flow.addWidget(chip)
        self.chips[key] = chip
        self.chips_host.updateGeometry()

    def _create(self):
        name = self.new_name.text().strip()
        if not name or any(c.text().lower() == name.lower() for c in self.chips.values()):
            self.new_name.clear()
            return
        color = TAG_COLORS[self._color_index % len(TAG_COLORS)]
        self._color_index += 1
        self.new_tags.append((name, color))
        self._add_chip(("new", name), name, color, True)
        self.new_name.clear()
        self.adjustSize()

    def keyPressEvent(self, e):
        # Enter in the "new tag" field creates a tag instead of closing the dialog
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.new_name.hasFocus():
            return
        super().keyPressEvent(e)

    def result_data(self) -> dict:
        existing = [k for k, c in self.chips.items() if isinstance(k, int) and c.isChecked()]
        new = [(n, col) for n, col in self.new_tags if self.chips[("new", n)].isChecked()]
        return {"tag_ids": existing, "new_tags": new, "subject_id": self.subject.currentData()}
