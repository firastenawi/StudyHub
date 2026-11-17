"""Study Planner page: one header + animated tabs (timetable, tasks, focus, subjects)."""
from __future__ import annotations

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout

from app.core.i18n import tr
from app.ui.animations import AnimatedStack
from app.ui.pages.base import Page
from app.ui.pages.study.subjects_view import SubjectsView
from app.ui.pages.study.tasks_view import TasksView
from app.ui.pages.study.timetable import TimetableView
from app.ui.theme import Icons
from app.ui.widgets.common import label
from app.ui.widgets.segmented import SegmentedTabs

# the focus timer is its own app now (app/ui/timer_window.py)
TABS = [("timetable", Icons.STUDY, "tab_timetable"), ("tasks", Icons.CHECKLIST, "tab_tasks"),
        ("subjects", Icons.BOOK, "tab_subjects")]


class StudyPage(Page):
    def __init__(self, ctl, main_window):
        super().__init__(ctl, main_window)
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(18)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(tr("nav_study"), 22, QFont.Weight.DemiBold))
        titles.addWidget(label(tr("study_subtitle"), 10, role="Dim"))
        header.addLayout(titles)
        header.addStretch(1)
        self.tabs = SegmentedTabs([(k, g, tr(t)) for k, g, t in TABS], "timetable")
        self.tabs.changed.connect(lambda key: self.show_tab(key))
        header.addWidget(self.tabs)
        root.addLayout(header)

        self.stack = AnimatedStack()
        self.views = {
            "timetable": TimetableView(ctl, self),
            "tasks": TasksView(ctl, self),
            "subjects": SubjectsView(ctl, self),
        }
        for view in self.views.values():
            self.stack.addWidget(view)
        root.addWidget(self.stack, 1)
        self.current = "timetable"
        self.listen(ctl.data_changed, self.refresh)

    def show_tab(self, key: str) -> None:
        if key == "focus":
            self.ctl.apps.open("timer")
            return
        if key not in self.views:
            return
        self.current = key
        self.tabs.set_current(key)
        self.stack.switch_to(self.stack.indexOf(self.views[key]), animate=self.isVisible())

    def refresh(self, what: str = "") -> None:
        for view in self.views.values():
            view.refresh(what)

    def on_shown(self) -> None:
        view = self.views[self.current]
        if hasattr(view, "on_shown"):
            view.on_shown()
