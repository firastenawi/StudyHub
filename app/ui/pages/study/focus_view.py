"""Pomodoro: big animated dial + controls on the left, statistics and settings on the right."""
from __future__ import annotations

from datetime import date

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.core.i18n import day_name, tr
from app.ui.theme import Icons, ui_font
from app.ui.widgets.charts import FocusRing, HBars, WeekBars
from app.ui.widgets.common import Card, EmptyState, IconButton, button, label
from app.ui.widgets.segmented import SegmentedTabs

PHASE_TABS = [("focus", Icons.TIMER, "phase_focus"), ("short_break", Icons.LIGHTBULB, "phase_short_break"),
              ("long_break", Icons.STAR, "phase_long_break")]


class FocusView(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl, self.page = ctl, page
        timer = ctl.pomodoro
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        # ------------------------------------------------------- left: the dial
        dial_card = Card(padding=26)
        dl = dial_card.root
        dl.setSpacing(18)
        self.phases = SegmentedTabs([(k, g, tr(t)) for k, g, t in PHASE_TABS], timer.phase)
        self.phases.changed.connect(self._choose_phase)
        dl.addWidget(self.phases, 0, Qt.AlignmentFlag.AlignHCenter)
        self.ring = FocusRing(340)
        dl.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)

        controls = QHBoxLayout()
        controls.setSpacing(18)
        controls.addStretch(1)
        reset = IconButton(Icons.REFRESH, tr("reset"), 48, 13)
        reset.clicked.connect(timer.reset)
        controls.addWidget(reset)
        self.play = QPushButton()
        self.play.setObjectName("Primary")
        self.play.setFixedSize(150, 54)
        self.play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play.setFont(ui_font(12, QFont.Weight.DemiBold))
        self.play.setStyleSheet("border-radius: 27px;")
        self.play.clicked.connect(timer.toggle)
        controls.addWidget(self.play)
        skip = IconButton(Icons.CHEVRON, tr("skip"), 48, 13)
        skip.clicked.connect(timer.skip)
        controls.addWidget(skip)
        controls.addStretch(1)
        dl.addLayout(controls)

        targets = QGridLayout()
        targets.setHorizontalSpacing(12)
        targets.addWidget(label(tr("subject"), 9.5, role="Dim"), 0, 0)
        targets.addWidget(label(tr("task"), 9.5, role="Dim"), 0, 1)
        self.subject = QComboBox()
        self.subject.currentIndexChanged.connect(self._subject_changed)
        self.task = QComboBox()
        self.task.currentIndexChanged.connect(self._task_changed)
        targets.addWidget(self.subject, 1, 0)
        targets.addWidget(self.task, 1, 1)
        dl.addLayout(targets)
        dl.addStretch(1)
        root.addWidget(dial_card, 5)

        # ---------------------------------------------------- right: stats
        side = QVBoxLayout()
        side.setSpacing(18)
        today = Card(tr("focus_today"), Icons.CLOCK)
        row = QHBoxLayout()
        self.today_minutes = label("0", 26, QFont.Weight.DemiBold)
        self.today_sessions = label("", 10, role="Dim")
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(self.today_minutes)
        col.addWidget(self.today_sessions)
        row.addLayout(col)
        row.addStretch(1)
        today.root.addLayout(row)
        side.addWidget(today)

        week = Card(tr("focus_week"), Icons.STUDY)
        self.week_bars = WeekBars()
        week.root.addWidget(self.week_bars)
        side.addWidget(week)

        subjects = Card(tr("focus_by_subject"), Icons.BOOK)
        self.subject_bars = HBars()
        self.subject_empty = EmptyState(Icons.TIMER, tr("focus_no_data"))
        subjects.root.addWidget(self.subject_bars)
        subjects.root.addWidget(self.subject_empty)
        side.addWidget(subjects)

        # durations and auto-start live in Settings › Focus timer (all settings in one place)
        more = button(tr("timer_settings"), Icons.SETTINGS, role="Ghost")
        more.clicked.connect(lambda: ctl.open_settings("timer"))
        side.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)
        side.addStretch(1)
        root.addLayout(side, 4)

        page.listen(timer.tick, lambda *_: self._render())
        page.listen(timer.state_changed, lambda *_: self._render())
        page.listen(timer.phase_changed, self._phase_changed)
        self._fill_targets()
        self.refresh("", animate=False)
        self._render()

    # ---------------------------------------------------------------- timer
    def _render(self):
        t = self.ctl.pomodoro
        m, s = divmod(t.remaining, 60)
        caption = tr(f"phase_{t.phase}")
        if t.state == "paused":
            caption += " · " + tr("paused")
        dots_done = t.long_every if t.phase == "long_break" else t.completed_focus % t.long_every
        self.ring.set_state(t.progress, f"{m:02d}:{s:02d}", caption, t.state == "running", dots_done, t.long_every)
        self.play.setText(tr("pause") if t.state == "running" else (tr("resume") if t.state == "paused" else tr("start")))

    def _phase_changed(self, phase: str):
        self.phases.set_current(phase)
        self._render()

    def _choose_phase(self, phase: str):
        if self.ctl.pomodoro.state == "running":
            self.phases.set_current(self.ctl.pomodoro.phase)  # don't switch mid-session
            self.ctl.notify(tr("pause_first"), "info")
            return
        self.ctl.pomodoro.set_phase(phase)

    # -------------------------------------------------------------- targets
    def _fill_targets(self):
        for combo in (self.subject, self.task):
            combo.blockSignals(True)
            combo.clear()
        self.subject.addItem(tr("no_subject"), None)
        for s in self.ctl.db.list_subjects():
            self.subject.addItem(s["name"], s["id"])
        self.task.addItem(tr("no_task"), None)
        for task in self.ctl.db.list_tasks("open"):
            self.task.addItem(task["title"], task["id"])
        self.subject.setCurrentIndex(max(0, self.subject.findData(self.ctl.pomodoro_subject_id)))
        self.task.setCurrentIndex(max(0, self.task.findData(self.ctl.pomodoro_task_id)))
        for combo in (self.subject, self.task):
            combo.blockSignals(False)

    def _subject_changed(self, *_):
        self.ctl.pomodoro_subject_id = self.subject.currentData()

    def _task_changed(self, *_):
        task_id = self.task.currentData()
        self.ctl.pomodoro_task_id = task_id
        task = self.ctl.db.get_task(task_id) if task_id else None
        if task and task["subject_id"]:  # picking a task also picks its subject
            self.subject.setCurrentIndex(max(0, self.subject.findData(task["subject_id"])))

    # ---------------------------------------------------------------- stats
    def refresh(self, what: str = "", animate: bool = True):
        if what in ("", "subjects", "tasks"):
            self._fill_targets()
        if what not in ("", "pomodoro", "subjects"):
            return
        db = self.ctl.db
        minutes = db.focus_minutes_today()
        self.today_minutes.setText(f"{minutes} {tr('minutes_short')}")
        self.today_sessions.setText(tr("sessions_today", n=db.pomodoro_count_today()))
        today = date.today().isoformat()
        self.week_bars.set_data([(day_name(date.fromisoformat(d).weekday(), short=True), m, d == today)
                                 for d, m in db.focus_minutes_by_day(7)])
        rows = [(r["name"] or tr("no_subject"), r["minutes"], r["color"]) for r in db.focus_minutes_by_subject(7)]
        self.subject_bars.set_rows(rows, " " + tr("minutes_short"))
        self.subject_bars.setVisible(bool(rows))
        self.subject_empty.setVisible(not rows)

    def on_shown(self):
        self.refresh("")
        self._render()
