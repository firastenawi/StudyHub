"""Full to-do list: views (today / upcoming / overdue / all / done), search, quick add."""
from __future__ import annotations

from datetime import date, timedelta

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from app.core.i18n import tr
from app.ui.animations import OUT, dispose, stagger_in, value_animation
from app.ui.theme import Icons, Palette, qcolor, ink
from app.ui.widgets.common import EmptyState, GlyphLabel, add_action, add_submenu, label, make_menu, primary_button
from app.ui.widgets.segmented import SegmentedTabs
from app.ui.widgets.tasks import PRIORITY_COLORS, PRIORITY_KEYS, CheckCircle, due_text

VIEWS = [("today", Icons.CALENDAR_DAY, "view_today"), ("upcoming", Icons.CLOCK, "view_upcoming"),
         ("overdue", Icons.WARNING, "overdue"), ("open", Icons.CHECKLIST, "view_all"),
         ("done", Icons.ACCEPT, "view_done")]


class TaskItem(QWidget):
    """One task row: check circle, title, details, subject + due chips, priority flag."""

    def __init__(self, task: dict, ctl, parent=None):
        super().__init__(parent)
        self.task, self.ctl = task, ctl
        self._hover = 0.0
        self._anim = value_animation(self, 0.0, 1.0, 160, self._set, OUT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 16, 12)
        lay.setSpacing(14)
        done = task["status"] == "done"
        self.check = CheckCircle(PRIORITY_COLORS.get(task["priority"], Palette.info), size=24)
        self.check.set_checked_silently(done)
        self.check.toggled.connect(self._toggled)
        lay.addWidget(self.check, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(3)
        self.title = label(task["title"], 10.8, QFont.Weight.Medium, "Faint" if done else "", wrap=True)
        if done:
            f = self.title.font()
            f.setStrikeOut(True)
            self.title.setFont(f)
        col.addWidget(self.title)
        if task.get("details"):
            col.addWidget(label(task["details"].splitlines()[0][:140], 9, role="Dim"))
        meta = QHBoxLayout()
        meta.setSpacing(12)
        if task.get("subject_name"):
            dot = label("●", 8.5)
            dot.setStyleSheet(f"color: {task['subject_color']};")
            meta.addWidget(dot)
            meta.addWidget(label(task["subject_name"], 8.8, role="Dim"))
        text, color = due_text(task.get("due_date"))
        if text and not done:
            due = label(text, 8.8, QFont.Weight.Medium)
            due.setStyleSheet(f"color: {color};")
            meta.addWidget(GlyphLabel(Icons.CALENDAR_DAY, 8, color))
            meta.addWidget(due)
        meta.addStretch(1)
        if meta.count() > 1:
            col.addLayout(meta)
        lay.addLayout(col, 1)
        flag = GlyphLabel(Icons.FLAG, 10, PRIORITY_COLORS.get(task["priority"], Palette.info))
        flag.setToolTip(tr(PRIORITY_KEYS.get(task["priority"], "priority_medium")))
        lay.addWidget(flag, 0, Qt.AlignmentFlag.AlignTop)

    def _set(self, v):
        self._hover = float(v)
        self.update()

    def enterEvent(self, e):
        self._anim.setDirection(self._anim.Direction.Forward)
        self._anim.start()

    def leaveEvent(self, e):
        self._anim.setDirection(self._anim.Direction.Backward)
        self._anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(ink(0.06 + 0.06 * self._hover))
        p.setBrush(ink(0.03 + 0.03 * self._hover))
        p.drawRoundedRect(r, 14, 14)

    def _toggled(self, done: bool):
        f = self.title.font()
        f.setStrikeOut(done)
        self.title.setFont(f)
        QTimer.singleShot(650, lambda: self.check.isChecked() == done and self.ctl.set_task_done(self.task["id"], done))

    def mouseDoubleClickEvent(self, _):
        self.ctl.edit_task(self.task["id"], self)

    def contextMenuEvent(self, e):
        ctl, tid = self.ctl, self.task["id"]
        menu = make_menu(self)
        add_action(menu, Icons.EDIT, tr("edit"), lambda: ctl.edit_task(tid, self))
        prio = add_submenu(menu, Icons.FLAG, tr("priority"))
        for level in (1, 2, 3):
            add_action(prio, Icons.ACCEPT if self.task["priority"] == level else None, tr(PRIORITY_KEYS[level]),
                       lambda lv=level: ctl.set_task_priority(tid, lv))
        due = add_submenu(menu, Icons.CALENDAR_DAY, tr("due_date"))
        today = date.today()
        add_action(due, None, tr("due_today"), lambda: ctl.set_task_due(tid, today.isoformat()))
        add_action(due, None, tr("due_tomorrow"), lambda: ctl.set_task_due(tid, (today + timedelta(days=1)).isoformat()))
        add_action(due, None, tr("next_week"), lambda: ctl.set_task_due(tid, (today + timedelta(days=7)).isoformat()))
        add_action(due, None, tr("no_due"), lambda: ctl.set_task_due(tid, None))
        add_action(menu, Icons.TIMER, tr("focus_on_task"), lambda: ctl.focus_on_task(tid))
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("delete"), lambda: ctl.delete_task(tid), danger=True)
        menu.exec(e.globalPos())
        e.accept()


class TasksView(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl, self.page = ctl, page
        self.view = "today"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        top = QHBoxLayout()
        self.tabs = SegmentedTabs([(k, g, tr(t)) for k, g, t in VIEWS], "today")
        self.tabs.changed.connect(self._set_view)
        top.addWidget(self.tabs)
        top.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("search_tasks"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(lambda _: self.refresh("tasks", animate=False))
        top.addWidget(self.search)
        new = primary_button(tr("new_task"), Icons.ADD)
        new.clicked.connect(lambda: ctl.add_task_dialog(self._default_due(), parent=self))
        top.addWidget(new)
        lay.addLayout(top)

        quick = QHBoxLayout()
        quick.setSpacing(8)
        self.quick = QLineEdit()
        self.quick.setPlaceholderText(tr("quick_add_task"))
        self.quick.returnPressed.connect(self._quick_add)
        quick.addWidget(self.quick, 1)
        self.quick_subject = QComboBox()
        self.quick_subject.setFixedWidth(200)
        quick.addWidget(self.quick_subject)
        lay.addLayout(quick)

        self.list_host = QWidget()
        self.list = QVBoxLayout(self.list_host)
        self.list.setContentsMargins(0, 0, 6, 0)
        self.list.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.list_host)
        lay.addWidget(scroll, 1)
        self.refresh("", animate=False)

    def _default_due(self) -> str | None:
        if self.view in ("today", "overdue"):
            return date.today().isoformat()
        if self.view == "upcoming":
            return (date.today() + timedelta(days=1)).isoformat()
        return None

    def _quick_add(self):
        text = self.quick.text().strip()
        if text:
            self.ctl.add_task(text, due_date=self._default_due(), subject_id=self.quick_subject.currentData())
            self.quick.clear()

    def _set_view(self, view: str):
        self.view = view
        self.refresh("tasks")

    def refresh(self, what: str = "", animate: bool = True):
        if what not in ("", "tasks", "subjects"):
            return
        if what in ("", "subjects"):
            current = self.quick_subject.currentData()
            self.quick_subject.clear()
            self.quick_subject.addItem(tr("no_subject"), None)
            for s in self.ctl.db.list_subjects():
                self.quick_subject.addItem(s["name"], s["id"])
            self.quick_subject.setCurrentIndex(max(0, self.quick_subject.findData(current)))
        counts = self.ctl.db.count_tasks_by_view()
        self.tabs.set_badges({k: counts.get(k, 0) for k in ("today", "upcoming", "overdue")})
        while self.list.count():
            item = self.list.takeAt(0)
            if item.widget():
                dispose(item.widget())
        tasks = self.ctl.db.list_tasks(self.view, self.search.text().strip())
        rows = []
        if not tasks:
            self.list.addWidget(EmptyState(Icons.ACCEPT, tr(f"tasks_empty_{self.view}")))
        for task in tasks:
            row = TaskItem(task, self.ctl)
            self.list.addWidget(row)
            rows.append(row)
        self.list.addStretch(1)
        if animate and rows:
            QTimer.singleShot(0, lambda: stagger_in(rows[:12], step=35, dy=10, duration=300))

    def on_shown(self):
        self.refresh("tasks")
