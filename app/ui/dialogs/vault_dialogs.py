"""Vault dialogs: entry editor, password generator, master-password change, Chrome import."""
from __future__ import annotations

import os

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QPlainTextEdit, QSlider,
                             QVBoxLayout, QWidget)

from app.core import vault as V
from app.core.i18n import tr
from app.ui.dialogs.base import StyledDialog
from app.ui.dialogs.study_dialogs import field
from app.ui.theme import Icons, Palette, qcolor, ui_font, ink
from app.ui.widgets.common import IconButton, ToggleSwitch, button, label, primary_button

STRENGTH_COLORS = [Palette.danger, "#FB923C", Palette.warning, "#A3E635", Palette.success]


class StrengthBar(QWidget):
    """4 segments that fill and color with the password's strength, plus a word."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.score, self.bits, self.empty = 0, 0.0, True

    def set_password(self, password: str):
        self.empty = not password
        self.score, self.bits = V.strength(password)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        text_w, gap = 170, 6
        seg_w = (self.width() - text_w - 12 - 3 * gap) / 4
        bars_x = text_w + 12 if rtl else 0
        color = QColor(STRENGTH_COLORS[self.score])
        for i in range(4):
            slot = 3 - i if rtl else i  # fills from the reading-start side
            r = QRectF(bars_x + slot * (seg_w + gap), 8, seg_w, 6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color if not self.empty and i < max(1, self.score) else ink(0.1))
            p.drawRoundedRect(r, 3, 3)
        if not self.empty:
            p.setPen(color)
            p.setFont(ui_font(9, QFont.Weight.DemiBold))
            text_rect = QRectF(0 if rtl else self.width() - text_w, 0, text_w, 22)
            align = (Qt.AlignmentFlag.AlignLeft if rtl else Qt.AlignmentFlag.AlignRight) | Qt.AlignmentFlag.AlignVCenter
            p.drawText(text_rect, align, f"{tr(f'strength_{self.score}')} · {int(self.bits)} bit")


class PasswordField(QWidget):
    """Password input with show/hide eye (always left-to-right)."""

    def __init__(self, text: str = "", placeholder: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.edit = QLineEdit(text)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.edit.setFont(ui_font(11))
        lay.addWidget(self.edit, 1)
        self.eye = IconButton(Icons.EYE, tr("show_password"), 38, 11)
        self.eye.setCheckable(True)
        self.eye.toggled.connect(lambda on: (self.edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password),
            self.eye.setText(Icons.EYE_OFF if on else Icons.EYE)))
        lay.addWidget(self.eye)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str):
        self.edit.setText(text)


class GeneratorDialog(StyledDialog):
    def __init__(self, ctl, use_button: bool = False, parent=None):
        super().__init__(tr("generator"), parent, 520)
        self.ctl = ctl
        self.output = QLineEdit()
        self.output.setReadOnly(True)
        self.output.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.output.setFont(ui_font(15, QFont.Weight.DemiBold))
        self.output.setMinimumHeight(52)
        row = QHBoxLayout()
        row.addWidget(self.output, 1)
        again = IconButton(Icons.REFRESH, tr("regenerate"), 44, 12)
        again.clicked.connect(self._generate)
        row.addWidget(again)
        copy = IconButton(Icons.COPY, tr("copy"), 44, 12)
        copy.clicked.connect(lambda: ctl.copy_secret(self.output.text()))
        row.addWidget(copy)
        self.body.addLayout(row)
        self.bar = StrengthBar()
        self.body.addWidget(self.bar)

        len_row = QHBoxLayout()
        len_row.addWidget(label(tr("length"), 10))
        self.length = QSlider(Qt.Orientation.Horizontal)
        self.length.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.length.setRange(8, 64)
        self.length.setValue(20)
        self.length_label = label("20", 10, QFont.Weight.DemiBold)
        self.length.valueChanged.connect(lambda v: (self.length_label.setText(str(v)), self._generate()))
        len_row.addWidget(self.length, 1)
        len_row.addWidget(self.length_label)
        self.body.addLayout(len_row)
        self.options = {}
        for key, default in (("upper", True), ("lower", True), ("digits", True), ("symbols", True),
                             ("avoid_ambiguous", True)):
            line = QHBoxLayout()
            line.addWidget(label(tr(f"gen_{key}"), 10), 1)
            sw = ToggleSwitch(default)
            sw.toggled.connect(lambda _: self._generate())
            line.addWidget(sw)
            self.options[key] = sw
            self.body.addLayout(line)
        if use_button:
            self.add_buttons(tr("use_password"))
        else:
            self.add_buttons(tr("close"))
            self.ok_button.hide()
        self._generate()

    def _generate(self):
        pw = V.generate(self.length.value(), **{k: sw.isChecked() for k, sw in self.options.items()})
        self.output.setText(pw)
        self.bar.set_password(pw)

    def password(self) -> str:
        return self.output.text()


class EntryDialog(StyledDialog):
    def __init__(self, ctl, entry: dict | None = None, parent=None):
        super().__init__(tr("edit_login") if entry else tr("new_login"), parent, 540)
        self.ctl = ctl
        entry = entry or {}
        self.body.addWidget(field(tr("login_title")))
        self.title = QLineEdit(entry.get("title", ""))
        self.title.setPlaceholderText(tr("login_title_ph"))
        self.body.addWidget(self.title)
        self.body.addWidget(field(tr("website")))
        self.url = QLineEdit(entry.get("url", ""))
        self.url.setPlaceholderText("https://")
        self.url.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.url.textEdited.connect(self._auto_title)
        self.body.addWidget(self.url)
        self.body.addWidget(field(tr("username")))
        self.username = QLineEdit(entry.get("username", ""))
        self.username.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.body.addWidget(self.username)
        self.body.addWidget(field(tr("password")))
        pw_row = QHBoxLayout()
        self.password = PasswordField(entry.get("password", ""))
        pw_row.addWidget(self.password, 1)
        gen = IconButton(Icons.LIGHTNING, tr("generate"), 38, 11)
        gen.clicked.connect(self._quick_generate)
        pw_row.addWidget(gen)
        opts = IconButton(Icons.SETTINGS, tr("generator"), 38, 11)
        opts.clicked.connect(self._generator)
        pw_row.addWidget(opts)
        self.body.addLayout(pw_row)
        self.bar = StrengthBar()
        self.password.edit.textChanged.connect(self.bar.set_password)
        self.bar.set_password(self.password.text())
        self.body.addWidget(self.bar)

        row = QHBoxLayout()
        row.addWidget(label(tr("category"), 9.5, role="Dim"))
        self.category = QComboBox()
        for c in V.CATEGORIES:
            self.category.addItem(tr(f"vcat_{c}"), c)
        self.category.setCurrentIndex(max(0, self.category.findData(entry.get("category", "other"))))
        row.addWidget(self.category, 1)
        row.addSpacing(16)
        row.addWidget(label(tr("favorite"), 9.5, role="Dim"))
        self.favorite = ToggleSwitch(bool(entry.get("favorite")))
        row.addWidget(self.favorite)
        self.body.addLayout(row)
        self.body.addWidget(field(tr("secure_notes")))
        self.notes = QPlainTextEdit(entry.get("notes", ""))
        self.notes.setFixedHeight(70)
        self.body.addWidget(self.notes)
        self.error = label("", 9)
        self.error.setStyleSheet("color: #F87171;")
        self.body.addWidget(self.error)
        self.add_buttons(tr("save"))

    def _auto_title(self, text: str):
        if not self.title.text().strip():
            self.title.setText(V.site_name(text))
        self.category.setCurrentIndex(max(0, self.category.findData(V.guess_category(text))))

    def _quick_generate(self):
        pw = V.generate(20)
        self.password.setText(pw)
        self.password.eye.setChecked(True)

    def _generator(self):
        dlg = GeneratorDialog(self.ctl, use_button=True, parent=self)
        if dlg.exec():
            self.password.setText(dlg.password())
            self.password.eye.setChecked(True)

    def validate(self) -> bool:
        if not self.title.text().strip() or not self.password.text():
            self.error.setText(tr("error_login_required"))
            return False
        return True

    def result_data(self) -> dict:
        return {"title": self.title.text().strip(), "url": self.url.text().strip(),
                "username": self.username.text().strip(), "password": self.password.text(),
                "notes": self.notes.toPlainText().strip(), "category": self.category.currentData(),
                "favorite": self.favorite.isChecked()}


class ChangeMasterDialog(StyledDialog):
    def __init__(self, parent=None):
        super().__init__(tr("change_master"), parent, 460)
        self.old = PasswordField(placeholder=tr("current_master"))
        self.new = PasswordField(placeholder=tr("new_master"))
        self.confirm = PasswordField(placeholder=tr("confirm_master"))
        for w in (self.old, self.new, self.confirm):
            self.body.addWidget(w)
        self.bar = StrengthBar()
        self.new.edit.textChanged.connect(self.bar.set_password)
        self.body.addWidget(self.bar)
        self.error = label("", 9)
        self.error.setStyleSheet("color: #F87171;")
        self.body.addWidget(self.error)
        self.add_buttons(tr("save"))

    def validate(self) -> bool:
        problem = master_problem(self.new.text(), self.confirm.text())
        self.error.setText(problem or "")
        return problem is None


def master_problem(new: str, confirm: str) -> str | None:
    if len(new) < 10:
        return tr("master_too_short")
    if new != confirm:
        return tr("master_mismatch")
    if V.strength(new)[0] < 2:
        return tr("master_too_weak")
    return None


class ImportDialog(StyledDialog):
    """Explains how to export from Chrome, picks the CSV, offers to wipe it afterwards."""

    def __init__(self, parent=None):
        super().__init__(tr("import_chrome"), parent, 560)
        steps = QVBoxLayout()
        steps.setSpacing(6)
        for i in range(1, 5):
            steps.addWidget(label(f"{i}.  " + tr(f"import_step_{i}"), 10, wrap=True))
        self.body.addLayout(steps)
        pick = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.path.setPlaceholderText("Chrome Passwords.csv")
        pick.addWidget(self.path, 1)
        browse = button(tr("browse"), Icons.OPEN_FOLDER)
        browse.clicked.connect(self._browse)
        pick.addWidget(browse)
        self.body.addLayout(pick)
        self.wipe = QCheckBox(tr("wipe_csv"))
        self.wipe.setChecked(True)
        self.wipe.setFont(ui_font(10))
        self.body.addWidget(self.wipe)
        self.body.addWidget(label(tr("wipe_csv_desc"), 8.8, role="Faint", wrap=True))
        self.error = label("", 9)
        self.error.setStyleSheet("color: #F87171;")
        self.body.addWidget(self.error)
        self.add_buttons(tr("import_now"))

    def _browse(self):
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        path, _ = QFileDialog.getOpenFileName(self, tr("import_chrome"), downloads, "CSV (*.csv)")
        if path:
            self.path.setText(os.path.normpath(path))

    def validate(self) -> bool:
        if not os.path.isfile(self.path.text()):
            self.error.setText(tr("pick_csv"))
            return False
        return True
