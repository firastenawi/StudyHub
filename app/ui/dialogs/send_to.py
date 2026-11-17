"""'Send to > Study Hub' (Explorer): one window to decide what happens with the files."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.core.i18n import ltr, tr
from app.ui.dialogs.base import StyledDialog
from app.ui.theme import Icons, Theme, ui_font
from app.ui.widgets.common import GlyphPlate, label
from app.ui.widgets.files import color_icon


class ActionRow(QPushButton):
    """Big clickable row: colored glyph, title, description (+ optional widget at the end)."""

    def __init__(self, glyph: str, color: str, title: str, desc: str, extra: QWidget | None = None):
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(64)
        self.setStyleSheet("QPushButton { text-align: left; padding: 0; border-radius: 14px; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(14)
        lay.addWidget(GlyphPlate(glyph, color, 40))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(title, 10.5, QFont.Weight.DemiBold))
        col.addWidget(label(desc, 8.8, role="Dim"))
        lay.addLayout(col, 1)
        if extra is not None:
            lay.addWidget(extra)


class SendToDialog(StyledDialog):
    def __init__(self, paths: list[str], subjects: list[dict], ctl, parent=None):
        super().__init__(tr("send_to_title"), parent, 560)
        self.ctl, self.paths = ctl, paths
        names = ", ".join(Path(p).name for p in paths[:3]) + (f" +{len(paths) - 3}" if len(paths) > 3 else "")
        self.body.addWidget(label(ltr(names), 9.5, role="Dim", wrap=True))

        with_folders = [s for s in subjects if ctl.subject_folders(s["id"])]
        self.subject = QComboBox()
        self.subject.setFixedWidth(190)
        for s in with_folders:
            self.subject.addItem(color_icon(s["color"]), s["name"], s["id"])

        rows = [
            (Icons.APPS, Theme.accent(), "send_pin", "send_pin_desc", None, self._pin),
            (Icons.TAG, "#F472B6", "send_tag", "send_tag_desc", None, self._tag),
            (Icons.NOTES, "#FBBF24", "send_note", "send_note_desc", None, self._note),
        ]
        for glyph, color, title, desc, extra, slot in rows:
            row = ActionRow(glyph, color, tr(title), tr(desc), extra)
            row.clicked.connect(slot)
            self.body.addWidget(row)
        if with_folders:
            self.body.addWidget(label(tr("send_subject_label"), 9.5, role="Dim"))
            line = QHBoxLayout()
            line.addWidget(self.subject, 1)
            copy = QPushButton(tr("copy_here"))
            copy.setObjectName("Primary")
            copy.setFont(ui_font(9.5, QFont.Weight.DemiBold))
            copy.clicked.connect(lambda: self._to_subject("copy_to"))
            move = QPushButton(tr("move_here"))
            move.setFont(ui_font(9.5))
            move.clicked.connect(lambda: self._to_subject("move_to"))
            line.addWidget(copy)
            line.addWidget(move)
            self.body.addLayout(line)
        self.add_buttons(tr("close"))
        self.ok_button.hide()  # every action row closes the dialog by itself

    def _pin(self):
        self.accept()
        self.ctl.handle_command({"action": "add_path", "paths": self.paths})

    def _tag(self):
        self.accept()
        self.ctl.tag_paths(self.paths)

    def _note(self):
        self.accept()
        self.ctl.handle_command({"action": "note_for", "paths": self.paths})

    def _to_subject(self, action: str):
        sid = self.subject.currentData()
        if sid is not None:
            self.accept()
            self.ctl.handle_command({"action": action, "subject": sid, "paths": self.paths})
