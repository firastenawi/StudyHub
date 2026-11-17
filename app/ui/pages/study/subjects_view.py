"""Subject cards: color, teacher, weekly stats, folder and one-click 'study mode'."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QFont, QLinearGradient, QPainter, QPainterPath
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from app.core.i18n import tr
from app.ui.animations import dispose, stagger_in
from app.ui.theme import Icons, Palette, qcolor
from app.ui.widgets.common import (Card, EmptyState, IconButton, add_action, button, label, make_menu,
                                   primary_button)


class SubjectCard(Card):
    def __init__(self, subject: dict, stats: dict, ctl, parent=None):
        super().__init__(padding=20, parent=parent)
        self.subject, self.ctl = subject, ctl
        self.setProperty("hoverable", True)
        self.setMinimumHeight(200)
        self.root.setSpacing(8)
        self.root.addSpacing(26)  # room for the painted color banner

        head = QHBoxLayout()
        head.addWidget(label(subject["name"], 13, QFont.Weight.DemiBold))
        head.addStretch(1)
        more = IconButton(Icons.MENU, tr("more"), 30, 10)
        more.clicked.connect(lambda: self._menu(more.mapToGlobal(more.rect().bottomLeft())))
        head.addWidget(more)
        self.root.addLayout(head)
        sub = " · ".join(x for x in (subject.get("code"), subject.get("teacher")) if x)
        if sub:
            self.root.addWidget(label(sub, 9.3, role="Dim"))

        nums = QHBoxLayout()
        nums.setSpacing(18)
        for value, key in ((stats["slots"], "stat_classes_week"), (stats["open_tasks"], "stat_open_tasks"),
                           (stats["focus_week"], "stat_focus_week"), (stats["notes"], "stat_notes")):
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(label(str(value), 14, QFont.Weight.DemiBold))
            col.addWidget(label(tr(key), 8.4, role="Faint"))
            nums.addLayout(col)
        nums.addStretch(1)
        self.root.addLayout(nums)
        self.root.addStretch(1)

        actions = QHBoxLayout()
        study = primary_button(tr("study_mode"), Icons.LIGHTNING)
        study.setToolTip(tr("study_mode_tip"))
        study.clicked.connect(lambda: ctl.start_study_mode(subject["id"]))
        actions.addWidget(study)
        if subject.get("folder_path"):
            folder = button(tr("open_folder"), Icons.OPEN_FOLDER)
            folder.clicked.connect(lambda: ctl.open_folder(subject["folder_path"]))
            actions.addWidget(folder)
        actions.addStretch(1)
        self.root.addLayout(actions)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QPainterPath()
        clip.addRoundedRect(r, 20, 20)
        p.setClipPath(clip)
        banner = QRectF(r.left(), r.top(), r.width(), 34)
        grad = QLinearGradient(banner.topLeft(), banner.topRight())
        grad.setColorAt(0, qcolor(self.subject["color"], 0.85))
        grad.setColorAt(1, qcolor(self.subject["color"], 0.15))
        p.fillRect(banner, grad)

    def mouseDoubleClickEvent(self, _):
        self.ctl.edit_subject(self.subject["id"], self)

    def contextMenuEvent(self, e):
        self._menu(e.globalPos())
        e.accept()

    def _menu(self, pos):
        ctl, sid = self.ctl, self.subject["id"]
        menu = make_menu(self)
        add_action(menu, Icons.LIGHTNING, tr("study_mode"), lambda: ctl.start_study_mode(sid))
        add_action(menu, Icons.ADD, tr("add_class"), lambda: ctl.add_slot_dialog(parent=self, subject_id=sid))
        add_action(menu, Icons.CHECKLIST, tr("new_task"), lambda: ctl.add_task_dialog(subject_id=sid, parent=self))
        add_action(menu, Icons.NOTES, tr("open_subject_notes"), lambda: ctl.open_notebook(subject_id=sid))
        menu.addSeparator()
        add_action(menu, Icons.EDIT, tr("edit"), lambda: ctl.edit_subject(sid, self))
        add_action(menu, Icons.DELETE, tr("delete"), lambda: ctl.delete_subject(sid, self), danger=True)
        menu.exec(pos)


class SubjectsView(QWidget):
    COLUMNS = 3

    def __init__(self, ctl, page):
        super().__init__()
        self.ctl, self.page = ctl, page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        bar = QHBoxLayout()
        bar.addWidget(label(tr("subjects_hint"), 9.5, role="Dim"))
        bar.addStretch(1)
        add = primary_button(tr("add_subject"), Icons.ADD)
        add.clicked.connect(lambda: ctl.add_subject_dialog(self))
        bar.addWidget(add)
        lay.addLayout(bar)

        host = QWidget()
        self.grid = QGridLayout(host)
        self.grid.setContentsMargins(0, 0, 6, 0)
        self.grid.setSpacing(18)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)
        self.cards: list[QWidget] = []
        self.refresh("", animate=False)

    def refresh(self, what: str = "", animate: bool = True):
        if what not in ("", "subjects", "schedule", "tasks", "pomodoro", "notes"):
            return
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                dispose(item.widget())
        self.cards = []
        subjects = self.ctl.db.list_subjects()
        if not subjects:
            self.grid.addWidget(EmptyState(Icons.BOOK, tr("subjects_empty")), 0, 0, 1, self.COLUMNS)
        for i, s in enumerate(subjects):
            card = SubjectCard(s, self.ctl.db.subject_stats(s["id"]), self.ctl)
            self.grid.addWidget(card, i // self.COLUMNS, i % self.COLUMNS)
            self.cards.append(card)
        for c in range(self.COLUMNS):
            self.grid.setColumnStretch(c, 1)
        self.grid.setRowStretch(len(subjects) // self.COLUMNS + 1, 1)
        if animate and self.cards and what in ("", "subjects"):
            cards = list(self.cards)
            QTimer.singleShot(0, lambda: stagger_in(cards, step=60))

    def on_shown(self):
        self.refresh("subjects")
