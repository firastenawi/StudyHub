"""Add / edit an alarm: time, repeat days, label, sound (+ preview), volume, gradual wake-up,
snooze length & count, wake-up challenge and color."""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from PyQt6.QtCore import QTime, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QSlider, QSpinBox,
                             QTimeEdit, QVBoxLayout)

from app.core.alarms import join_days, parse_days
from app.core.i18n import day_name, ordered_days, tr
from app.core.sounds import BUILTIN
from app.ui.dialogs.base import StyledDialog
from app.ui.dialogs.study_dialogs import SUBJECT_COLORS, ColorDot, chip, field, style_time_edit
from app.ui.theme import Icons, ui_font
from app.ui.widgets.common import IconButton, ToggleSwitch, label

CUSTOM = "__custom__"


class AlarmDialog(StyledDialog):
    def __init__(self, ctl, alarm: dict | None = None, parent=None):
        super().__init__(tr("edit_alarm") if alarm else tr("new_alarm"), parent, 560)
        self.ctl = ctl
        default_time = (datetime.now() + timedelta(hours=1)).strftime("%H:00")
        alarm = alarm or {"time": default_time, "days": "", "label": "", "sound": "gentle", "volume": 80,
                          "gradual": 1, "snooze_min": 5, "max_snoozes": 3, "challenge": "none", "color": ""}

        # big time picker
        self.time = QTimeEdit(QTime.fromString(alarm["time"], "HH:mm"))
        style_time_edit(self.time)
        self.time.setFont(ui_font(30, QFont.Weight.DemiBold))
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time.setMinimumHeight(70)
        self.body.addWidget(self.time)
        self.until = label("", 9.5, role="Dim")
        self.until.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(self.until)

        # repeat
        self.body.addWidget(field(tr("repeat")))
        days_row = QHBoxLayout()
        days_row.setSpacing(5)
        self.day_buttons = {}
        chosen = set(parse_days(alarm["days"]))
        for d in ordered_days(ctl.week_start()):
            b = chip(day_name(d, short=True))
            b.setChecked(d in chosen)
            b.toggled.connect(self._update_until)
            self.day_buttons[d] = b
            days_row.addWidget(b)
        self.body.addLayout(days_row)
        presets = QHBoxLayout()
        presets.setSpacing(6)
        weekend = {4, 5}  # Friday + Saturday
        for text, days in ((tr("repeat_once"), set()), (tr("repeat_daily"), set(range(7))),
                           (tr("repeat_weekdays"), set(range(7)) - weekend), (tr("repeat_weekend"), weekend)):
            b = chip(text, checkable=False)
            b.clicked.connect(lambda _=False, ds=days: self._set_days(ds))
            presets.addWidget(b)
        presets.addStretch(1)
        self.body.addLayout(presets)

        self.body.addWidget(field(tr("alarm_label")))
        self.label_edit = QLineEdit(alarm["label"])
        self.label_edit.setPlaceholderText(tr("alarm_label_ph"))
        self.body.addWidget(self.label_edit)

        # sound
        self.body.addWidget(field(tr("sound")))
        sound_row = QHBoxLayout()
        self.sound = QComboBox()
        for key in BUILTIN:
            self.sound.addItem(tr(f"sound_{key}"), key)
        if alarm["sound"] not in BUILTIN:
            self.sound.addItem(os.path.basename(alarm["sound"]), alarm["sound"])
        self.sound.addItem(tr("sound_custom"), CUSTOM)
        self.sound.setCurrentIndex(max(0, self.sound.findData(alarm["sound"])))
        self.sound.activated.connect(self._sound_chosen)
        sound_row.addWidget(self.sound, 1)
        play = IconButton(Icons.PLAY, tr("preview"), 40)
        play.clicked.connect(self._preview)
        sound_row.addWidget(play)
        self.body.addLayout(sound_row)

        vol_row = QHBoxLayout()
        vol_row.addWidget(label("🔈", 11))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(5, 100)
        self.volume.setLayoutDirection(Qt.LayoutDirection.LeftToRight)  # louder = right, always
        self.volume.setValue(int(alarm["volume"]))
        vol_row.addWidget(self.volume, 1)
        self.vol_label = label(f"{alarm['volume']}%", 9.5, role="Dim")
        self.volume.valueChanged.connect(lambda v: self.vol_label.setText(f"{v}%"))
        vol_row.addWidget(self.vol_label)
        self.body.addLayout(vol_row)
        grad_row = QHBoxLayout()
        grad_col = QVBoxLayout()
        grad_col.setSpacing(0)
        grad_col.addWidget(label(tr("gradual"), 10))
        grad_col.addWidget(label(tr("gradual_desc"), 8.6, role="Faint"))
        grad_row.addLayout(grad_col, 1)
        self.gradual = ToggleSwitch(bool(alarm["gradual"]))
        grad_row.addWidget(self.gradual)
        self.body.addLayout(grad_row)

        # snooze
        snooze_row = QHBoxLayout()
        snooze_row.addWidget(label(tr("snooze_for"), 10))
        self.snooze = QSpinBox()
        self.snooze.setRange(1, 30)
        self.snooze.setValue(int(alarm["snooze_min"]))
        self.snooze.setSuffix(" " + tr("minutes_short"))
        snooze_row.addWidget(self.snooze)
        snooze_row.addSpacing(16)
        snooze_row.addWidget(label(tr("max_snoozes"), 10))
        self.max_snoozes = QSpinBox()
        self.max_snoozes.setRange(0, 10)
        self.max_snoozes.setValue(int(alarm["max_snoozes"]))
        snooze_row.addWidget(self.max_snoozes)
        snooze_row.addStretch(1)
        self.body.addLayout(snooze_row)

        # wake-up challenge
        self.body.addWidget(field(tr("challenge")))
        ch_row = QHBoxLayout()
        self.challenge = QButtonGroup(self)
        for key in ("none", "math", "type"):
            b = chip(tr(f"challenge_{key}"))
            b.setProperty("key", key)
            b.setChecked(key == alarm["challenge"])
            self.challenge.addButton(b)
            ch_row.addWidget(b)
        ch_row.addStretch(1)
        self.body.addLayout(ch_row)

        colors = QHBoxLayout()
        colors.setSpacing(2)
        self.colors = QButtonGroup(self)
        for c in SUBJECT_COLORS[:8]:
            dot = ColorDot(c)
            dot.setChecked(c == (alarm["color"] or SUBJECT_COLORS[0]))
            self.colors.addButton(dot)
            colors.addWidget(dot)
        colors.addStretch(1)
        self.body.addLayout(colors)

        self.add_buttons(tr("save"))
        self.time.timeChanged.connect(self._update_until)
        self._update_until()

    # ------------------------------------------------------------------ ui
    def _set_days(self, days: set):
        for d, b in self.day_buttons.items():
            b.setChecked(d in days)

    def _days(self) -> list[int]:
        return [d for d, b in self.day_buttons.items() if b.isChecked()]

    def _update_until(self, *_):
        from app.core.alarms import next_occurrence, until_text
        nxt = next_occurrence({"enabled": 1, "time": self.time.time().toString("HH:mm"),
                               "days": join_days(self._days())})
        if nxt:
            d, h, m = until_text(nxt)
            self.until.setText(tr("rings_in", text=self.ctl.duration_text(d, h, m)))

    def _sound_chosen(self, index: int):
        if self.sound.itemData(index) != CUSTOM:
            return
        path, _ = QFileDialog.getOpenFileName(self, tr("sound_custom"), "",
                                              tr("audio_files") + " (*.mp3 *.wav *.ogg *.m4a *.flac)")
        if path:
            path = os.path.normpath(path)
            self.sound.insertItem(index, os.path.basename(path), path)
            self.sound.setCurrentIndex(index)
        else:
            self.sound.setCurrentIndex(0)

    def _preview(self):
        sound = self.sound.currentData()
        if sound and sound != CUSTOM:
            self.ctl.sound.preview(sound, self.volume.value())

    def reject(self):
        self.ctl.sound.stop()
        super().reject()

    def accept(self):
        self.ctl.sound.stop()
        super().accept()

    def result_data(self) -> dict:
        return {"time": self.time.time().toString("HH:mm"), "days": join_days(self._days()),
                "label": self.label_edit.text().strip(), "sound": self.sound.currentData(),
                "volume": self.volume.value(), "gradual": int(self.gradual.isChecked()),
                "snooze_min": self.snooze.value(), "max_snoozes": self.max_snoozes.value(),
                "challenge": self.challenge.checkedButton().property("key"),
                "color": self.colors.checkedButton().color, "enabled": 1}
