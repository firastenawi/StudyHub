"""Dialogs of the Study Planner: subject, timetable slot, task."""
from __future__ import annotations

import os
from datetime import date, timedelta

from PyQt6.QtCore import QDate, QLocale, QRectF, Qt, QTime
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractButton, QButtonGroup, QCheckBox, QComboBox, QDateEdit, QFileDialog,
                             QGridLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
                             QTimeEdit, QVBoxLayout, QWidget)

from app.core.i18n import day_name, ordered_days, tr, uses_12h
from app.ui.dialogs.base import StyledDialog
from app.ui.theme import Icons, ui_font
from app.ui.widgets.common import IconButton, label
from app.ui.widgets.tasks import PRIORITY_COLORS, PRIORITY_KEYS

SUBJECT_COLORS = ["#7C5CFF", "#3B82F6", "#06B6D4", "#10B981", "#84CC16",
                  "#F59E0B", "#F97316", "#F43F5E", "#EC4899", "#A78BFA"]
SLOT_KINDS = ["lecture", "lab", "tutorial", "exam", "other"]


def time_format() -> str:
    """QTimeEdit display format following the 12/24-hour setting."""
    return "h:mm AP" if uses_12h() else "HH:mm"


def style_time_edit(edit) -> None:
    """Times always read hour:minute left-to-right with Latin digits. (In Arabic layouts Qt
    mirrors the fields, and the Arabic locale's Indic digits get reordered by the bidi rules,
    so 10:17 would show as 17:10.)"""
    edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    inner = edit.findChild(QLineEdit)  # the text box inside keeps the app's RTL unless told otherwise
    if inner is not None:
        inner.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    edit.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    time_value = edit.time()
    edit.setDisplayFormat("HH:mm")       # re-applying the format after the direction change makes
    edit.setDisplayFormat(time_format())  # Qt rebuild its field order left-to-right
    edit.setTime(time_value)


def field(text: str) -> QWidget:
    return label(text, 9.5, role="Dim")


class ColorDot(QAbstractButton):
    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(QRectF(self.rect()).adjusted(6, 6, -6, -6))
        if self.isChecked():
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(self.rect()).adjusted(2, 2, -2, -2))


def chip(text: str, checkable: bool = True) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName("Chip")
    btn.setCheckable(checkable)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFont(ui_font(9.5))
    return btn


def subject_combo(subjects: list[dict], selected: int | None, none_text: str) -> QComboBox:
    combo = QComboBox()
    combo.addItem(none_text, None)
    for s in subjects:
        combo.addItem(s["name"], s["id"])
    combo.setCurrentIndex(max(0, combo.findData(selected)))
    return combo


# ===================================================================== subject
class SubjectDialog(StyledDialog):
    def __init__(self, launcher_items: list[dict], subject: dict | None = None,
                 linked_ids: list[int] | None = None, parent=None):
        super().__init__(tr("edit_subject") if subject else tr("add_subject"), parent, 520)
        subject = subject or {}

        self.body.addWidget(field(tr("subject_name")))
        self.name = QLineEdit(subject.get("name", ""))
        self.name.setPlaceholderText(tr("subject_name_ph"))
        self.body.addWidget(self.name)

        row = QHBoxLayout()
        col1, col2 = QVBoxLayout(), QVBoxLayout()
        col1.addWidget(field(tr("subject_code")))
        self.code = QLineEdit(subject.get("code", ""))
        col1.addWidget(self.code)
        col2.addWidget(field(tr("subject_teacher")))
        self.teacher = QLineEdit(subject.get("teacher", ""))
        col2.addWidget(self.teacher)
        row.addLayout(col1)
        row.addLayout(col2)
        self.body.addLayout(row)

        self.body.addWidget(field(tr("color")))
        colors = QHBoxLayout()
        colors.setSpacing(2)
        self.color_group = QButtonGroup(self)
        current = subject.get("color") or SUBJECT_COLORS[len(launcher_items) % len(SUBJECT_COLORS)]
        for c in SUBJECT_COLORS:
            dot = ColorDot(c)
            dot.setChecked(c.lower() == current.lower())
            self.color_group.addButton(dot)
            colors.addWidget(dot)
        if self.color_group.checkedButton() is None:
            self.color_group.buttons()[0].setChecked(True)
        colors.addStretch(1)
        self.body.addLayout(colors)

        self.body.addWidget(field(tr("subject_folder")))
        folder_row = QHBoxLayout()
        self.folder = QLineEdit(subject.get("folder_path", ""))
        self.folder.setPlaceholderText(tr("optional"))
        folder_row.addWidget(self.folder, 1)
        browse = IconButton(Icons.OPEN_FOLDER, tr("browse"), 40)
        browse.clicked.connect(self._browse)
        folder_row.addWidget(browse)
        self.body.addLayout(folder_row)

        # study mode: which launcher items open together for this subject
        self.body.addWidget(field(tr("study_mode_items")))
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        self.item_checks: dict[int, QCheckBox] = {}
        linked = set(linked_ids or [])
        for i, item in enumerate(launcher_items):
            box = QCheckBox(item["title"])
            box.setFont(ui_font(9.5))
            box.setChecked(item["id"] in linked)
            grid.addWidget(box, i // 2, i % 2)
            self.item_checks[item["id"]] = box
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        scroll.setFixedHeight(min(150, 30 * ((len(launcher_items) + 1) // 2) + 6))
        self.body.addWidget(scroll)

        self.error = label("", 9)
        self.error.setStyleSheet("color: #F87171;")
        self.body.addWidget(self.error)
        self.add_buttons(tr("save"))

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, tr("browse"), self.folder.text())
        if path:
            self.folder.setText(os.path.normpath(path))

    def validate(self) -> bool:
        if not self.name.text().strip():
            self.error.setText(tr("error_name_required"))
            return False
        folder = self.folder.text().strip()
        if folder and not os.path.isdir(folder):
            self.error.setText(tr("error_path_missing"))
            return False
        return True

    def result_data(self) -> dict:
        return {"name": self.name.text().strip(), "code": self.code.text().strip(),
                "teacher": self.teacher.text().strip(), "color": self.color_group.checkedButton().color,
                "folder_path": self.folder.text().strip()}

    def linked_items(self) -> list[int]:
        return [i for i, box in self.item_checks.items() if box.isChecked()]


# ======================================================================= slot
class SlotDialog(StyledDialog):
    def __init__(self, subjects: list[dict], week_start: int, slot: dict | None = None,
                 day: int | None = None, start: str = "08:00", parent=None):
        super().__init__(tr("edit_class") if slot else tr("add_class"), parent, 480)
        slot = slot or {}
        self.body.addWidget(field(tr("subject")))
        self.subject = QComboBox()
        for s in subjects:
            self.subject.addItem(s["name"], s["id"])
        self.subject.setCurrentIndex(max(0, self.subject.findData(slot.get("subject_id"))))
        self.body.addWidget(self.subject)

        self.body.addWidget(field(tr("day")))
        days = QHBoxLayout()
        days.setSpacing(6)
        self.day_group = QButtonGroup(self)
        wanted = slot.get("day_of_week", day if day is not None else 0)
        for d in ordered_days(week_start):
            btn = chip(day_name(d, short=True))
            btn.setProperty("day", d)
            btn.setChecked(d == wanted)
            self.day_group.addButton(btn)
            days.addWidget(btn)
        self.body.addLayout(days)

        times = QHBoxLayout()
        c1, c2 = QVBoxLayout(), QVBoxLayout()
        c1.addWidget(field(tr("start_time")))
        self.start = QTimeEdit(QTime.fromString(slot.get("start_time", start), "HH:mm"))
        style_time_edit(self.start)
        c1.addWidget(self.start)
        c2.addWidget(field(tr("end_time")))
        default_end = QTime.fromString(slot.get("start_time", start), "HH:mm").addSecs(90 * 60)
        self.end = QTimeEdit(QTime.fromString(slot["end_time"], "HH:mm") if slot.get("end_time") else default_end)
        style_time_edit(self.end)
        c2.addWidget(self.end)
        times.addLayout(c1)
        times.addLayout(c2)
        self.body.addLayout(times)

        self.body.addWidget(field(tr("kind")))
        kinds = QHBoxLayout()
        self.kind_group = QButtonGroup(self)
        for k in SLOT_KINDS:
            btn = chip(tr(f"slot_{k}"))
            btn.setProperty("kind", k)
            btn.setChecked(k == slot.get("kind", "lecture"))
            self.kind_group.addButton(btn)
            kinds.addWidget(btn)
        kinds.addStretch(1)
        self.body.addLayout(kinds)

        self.body.addWidget(field(tr("location")))
        self.location = QLineEdit(slot.get("location", ""))
        self.location.setPlaceholderText(tr("location_ph"))
        self.body.addWidget(self.location)

        self.error = label("", 9)
        self.error.setStyleSheet("color: #F87171;")
        self.body.addWidget(self.error)
        self.add_buttons(tr("save"))

    def validate(self) -> bool:
        if self.subject.currentData() is None:
            self.error.setText(tr("error_no_subject"))
            return False
        if self.end.time() <= self.start.time():
            self.error.setText(tr("error_end_before_start"))
            return False
        return True

    def result_data(self) -> dict:
        return {"subject_id": self.subject.currentData(),
                "day_of_week": self.day_group.checkedButton().property("day"),
                "start_time": self.start.time().toString("HH:mm"),
                "end_time": self.end.time().toString("HH:mm"),
                "kind": self.kind_group.checkedButton().property("kind"),
                "location": self.location.text().strip()}


# ======================================================================= task
class TaskDialog(StyledDialog):
    def __init__(self, subjects: list[dict], task: dict | None = None, due: str | None = None,
                 subject_id: int | None = None, parent=None):
        super().__init__(tr("edit_task") if task else tr("new_task"), parent, 500)
        task = task or {}
        self.body.addWidget(field(tr("task_title")))
        self.title = QLineEdit(task.get("title", ""))
        self.title.setPlaceholderText(tr("task_placeholder"))
        self.body.addWidget(self.title)

        self.body.addWidget(field(tr("details")))
        self.details = QPlainTextEdit(task.get("details", ""))
        self.details.setFixedHeight(80)
        self.details.setPlaceholderText(tr("optional"))
        self.body.addWidget(self.details)

        row = QHBoxLayout()
        c1, c2 = QVBoxLayout(), QVBoxLayout()
        c1.addWidget(field(tr("subject")))
        self.subject = subject_combo(subjects, task.get("subject_id", subject_id), tr("no_subject"))
        c1.addWidget(self.subject)
        c2.addWidget(field(tr("priority")))
        prio = QHBoxLayout()
        self.prio_group = QButtonGroup(self)
        for level in (1, 2, 3):
            btn = chip(tr(PRIORITY_KEYS[level]))
            btn.setProperty("level", level)
            btn.setChecked(level == task.get("priority", 2))
            btn.setStyleSheet(f"QPushButton#Chip:checked {{ border-color: {PRIORITY_COLORS[level]}; }}")
            self.prio_group.addButton(btn)
            prio.addWidget(btn)
        c2.addLayout(prio)
        row.addLayout(c1, 1)
        row.addLayout(c2, 1)
        self.body.addLayout(row)

        self.body.addWidget(field(tr("due_date")))
        due_row = QHBoxLayout()
        due_row.setSpacing(6)
        self.has_due = (task.get("due_date") if task else due) is not None
        start = task.get("due_date") if task else due
        self.date = QDateEdit(QDate.fromString(start, "yyyy-MM-dd") if start else QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.dateChanged.connect(lambda _: self._set_due_enabled(True))
        today = date.today()
        for text, value in ((tr("due_today"), today), (tr("due_tomorrow"), today + timedelta(days=1)),
                            (tr("next_week"), today + timedelta(days=7))):
            b = chip(text, checkable=False)
            b.clicked.connect(lambda _=False, v=value: (self.date.setDate(QDate(v.year, v.month, v.day)),
                                                        self._set_due_enabled(True)))
            due_row.addWidget(b)
        self.no_due = chip(tr("no_due"))
        self.no_due.setChecked(not self.has_due)
        self.no_due.toggled.connect(lambda on: self._set_due_enabled(not on))
        due_row.addWidget(self.no_due)
        due_row.addWidget(self.date, 1)
        self.body.addLayout(due_row)
        self._set_due_enabled(self.has_due)

        self.error = label("", 9)
        self.error.setStyleSheet("color: #F87171;")
        self.body.addWidget(self.error)
        self.add_buttons(tr("save"))
        self.title.setFocus()

    def _set_due_enabled(self, on: bool):
        self.has_due = on
        self.date.setEnabled(on)
        self.no_due.blockSignals(True)
        self.no_due.setChecked(not on)
        self.no_due.blockSignals(False)

    def validate(self) -> bool:
        if not self.title.text().strip():
            self.error.setText(tr("error_name_required"))
            return False
        return True

    def result_data(self) -> dict:
        return {"title": self.title.text().strip(), "details": self.details.toPlainText().strip(),
                "subject_id": self.subject.currentData(),
                "priority": self.prio_group.checkedButton().property("level"),
                "due_date": self.date.date().toString("yyyy-MM-dd") if self.has_due else None}
