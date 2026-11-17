"""'Archive…' dialog: subject, category, semester, mode (move / copy / zip) and a note."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QLineEdit

from app.core.archive import CATEGORIES
from app.core.i18n import ltr, tr
from app.ui.dialogs.base import StyledDialog
from app.ui.dialogs.study_dialogs import chip, field, subject_combo
from app.ui.widgets.common import label


class ArchiveDialog(StyledDialog):
    def __init__(self, paths: list[str], subjects: list[dict], semester: str, root: str,
                 subject_id: int | None = None, parent=None):
        super().__init__(tr("archive_title_n", n=len(paths)) if len(paths) > 1 else tr("archive_title"), parent, 540)
        names = ", ".join(Path(p).name for p in paths[:3]) + (f" +{len(paths) - 3}" if len(paths) > 3 else "")
        self.body.addWidget(label(ltr(names), 9.5, role="Dim", wrap=True))

        self.body.addWidget(field(tr("subject")))
        self.subject = subject_combo(subjects, subject_id, tr("no_subject"))
        self.body.addWidget(self.subject)

        self.body.addWidget(field(tr("category")))
        row = QHBoxLayout()
        self.category = QButtonGroup(self)
        for i, key in enumerate(CATEGORIES):
            b = chip(tr(f"cat_{key}"))
            b.setProperty("key", key)
            b.setChecked(i == len(CATEGORIES) - 1)
            self.category.addButton(b)
            row.addWidget(b)
        row.addStretch(1)
        self.body.addLayout(row)

        self.body.addWidget(field(tr("semester")))
        self.semester = QLineEdit(semester)
        self.body.addWidget(self.semester)

        self.body.addWidget(field(tr("archive_mode")))
        modes = QHBoxLayout()
        self.mode = QButtonGroup(self)
        self.mode_hint = label("", 8.8, role="Faint", wrap=True)
        for key in ("move", "copy", "zip"):
            b = chip(tr(f"mode_{key}"))
            b.setProperty("key", key)
            b.setChecked(key == "move")
            b.toggled.connect(lambda on, k=key: on and self.mode_hint.setText(tr(f"mode_{k}_desc")))
            self.mode.addButton(b)
            modes.addWidget(b)
        modes.addStretch(1)
        self.body.addLayout(modes)
        self.mode_hint.setText(tr("mode_move_desc"))
        self.body.addWidget(self.mode_hint)

        self.body.addWidget(field(tr("archive_note")))
        self.note = QLineEdit()
        self.note.setPlaceholderText(tr("optional"))
        self.body.addWidget(self.note)
        self.body.addWidget(label(tr("archive_goes_to", path=ltr(root)), 8.6, role="Faint", wrap=True))
        self.add_buttons(tr("archive_action"))

    def result_data(self) -> dict:
        return {"subject_id": self.subject.currentData(), "subject_name": self.subject.currentText()
                if self.subject.currentData() else None,
                "category": self.category.checkedButton().property("key"),
                "semester": self.semester.text().strip(), "mode": self.mode.checkedButton().property("key"),
                "note": self.note.text().strip()}
