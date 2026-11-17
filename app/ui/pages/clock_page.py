"""Clock page: alarms (cards with live 'rings in…') + lock-screen designer with live preview."""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QGridLayout, QHBoxLayout, QPushButton, QScrollArea, QSlider,
                             QVBoxLayout, QWidget)

from app.core.alarms import next_occurrence, parse_days, until_text
from app.core.i18n import day_name, format_hhmm, is_rtl, meridiem, ordered_days, tr, uses_12h
from app.ui.animations import AnimatedStack, dispose, stagger_in
from app.ui.lock_screen import STYLES, LockPreview
from app.ui.pages.base import Page
from app.ui.theme import Icons, Palette, Theme, qcolor, ui_font
from app.ui.widgets.common import (Card, EmptyState, ToggleSwitch, add_action, label, make_menu, primary_button,
                                   button)
from app.ui.widgets.segmented import SegmentedTabs


class AlarmCard(Card):
    def __init__(self, alarm: dict, ctl, parent=None):
        super().__init__(padding=18, parent=parent)
        self.alarm, self.ctl = alarm, ctl
        self.setProperty("hoverable", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(150)
        enabled = bool(alarm["enabled"])
        top = QHBoxLayout()
        h, m = (int(x) for x in alarm["time"].split(":"))
        time_text = f"{h % 12 or 12}:{m:02d}" if uses_12h() else alarm["time"]
        big = label(time_text, 34, QFont.Weight.DemiBold, "" if enabled else "Faint")
        top.addWidget(big)
        if uses_12h():
            top.addWidget(label(meridiem(h), 13, QFont.Weight.DemiBold, "Dim"), 0, Qt.AlignmentFlag.AlignBottom)
        top.addStretch(1)
        self.switch = ToggleSwitch(enabled)
        self.switch.toggled.connect(lambda on: ctl.set_alarm_enabled(alarm["id"], on))
        top.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignTop)
        self.root.addLayout(top)
        self.root.addWidget(label(alarm["label"] or tr("alarm"), 11, QFont.Weight.Medium,
                                  "" if enabled else "Faint"))
        days = parse_days(alarm["days"])
        days_row = QHBoxLayout()
        days_row.setSpacing(4)
        if days:
            for d in ordered_days(ctl.week_start()):
                lbl = label(day_name(d, short=True)[:2] if not is_rtl() else day_name(d, short=True)[:3], 8.6,
                            QFont.Weight.DemiBold if d in days else QFont.Weight.Normal,
                            "Accent" if d in days and enabled else "Faint")
                days_row.addWidget(lbl)
        else:
            days_row.addWidget(label(tr("repeat_once"), 8.8, role="Faint"))
        days_row.addStretch(1)
        self.root.addLayout(days_row)
        self.status = label("", 8.8, role="Dim")
        self.root.addWidget(self.status)
        self.refresh_status()

    def refresh_status(self):
        snoozed = self.ctl.alarms.snoozed_until(self.alarm["id"])
        nxt = next_occurrence(self.alarm)
        if snoozed:
            self.status.setText(tr("snoozed_until", time=format_hhmm(snoozed.strftime("%H:%M"))))
        elif nxt:
            d, h, m = until_text(nxt)
            self.status.setText(tr("rings_in", text=self.ctl.duration_text(d, h, m)))
        else:
            self.status.setText(tr("alarm_off"))

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QPainterPath()
        clip.addRoundedRect(r, 20, 20)
        p.setClipPath(clip)
        color = self.alarm["color"] or Theme.accent()
        bar = QRectF(r.right() - 5, r.top(), 5, r.height()) if is_rtl() else QRectF(r.left(), r.top(), 5, r.height())
        p.fillRect(bar, qcolor(color, 0.9 if self.alarm["enabled"] else 0.3))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.ctl.edit_alarm(self.alarm["id"], self)

    def contextMenuEvent(self, e):
        ctl, aid = self.ctl, self.alarm["id"]
        menu = make_menu(self)
        add_action(menu, Icons.EDIT, tr("edit"), lambda: ctl.edit_alarm(aid, self))
        add_action(menu, Icons.PLAY, tr("test_alarm"), lambda: ctl.ring_alarm(ctl.db.get_alarm(aid), 0))
        add_action(menu, Icons.COPY, tr("duplicate"), lambda: ctl.duplicate_alarm(aid))
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("delete"), lambda: ctl.delete_alarm(aid), danger=True)
        menu.exec(e.globalPos())
        e.accept()


class AlarmsView(QWidget):
    COLUMNS = 3

    def __init__(self, ctl, page):
        super().__init__()
        self.ctl, self.page = ctl, page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        head = QHBoxLayout()
        self.next_label = label("", 12, QFont.Weight.DemiBold)
        head.addWidget(self.next_label)
        head.addStretch(1)
        head.addWidget(label(tr("class_reminders"), 9.5, role="Dim"))
        self.reminder = QComboBox()
        for minutes in (0, 5, 10, 15, 30):
            self.reminder.addItem(tr("off") if not minutes else tr("min_before", n=minutes), minutes)
        self.reminder.setCurrentIndex(max(0, self.reminder.findData(int(ctl.db.get_setting("class_reminder_min", 0)))))
        self.reminder.currentIndexChanged.connect(
            lambda _: ctl.db.set_setting("class_reminder_min", self.reminder.currentData()))
        head.addWidget(self.reminder)
        add = primary_button(tr("new_alarm"), Icons.ADD)
        add.clicked.connect(lambda: ctl.add_alarm_dialog(self))
        head.addWidget(add)
        lay.addLayout(head)

        host = QWidget()
        self.grid = QGridLayout(host)
        self.grid.setContentsMargins(0, 0, 6, 0)
        self.grid.setSpacing(16)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)
        self.cards: list[AlarmCard] = []
        self._ticker = QTimer(self, interval=30_000, timeout=self._tick)  # keep "rings in…" fresh
        self._ticker.start()
        self.refresh("", animate=False)

    def _tick(self):
        for card in self.cards:
            card.refresh_status()
        self._update_next()

    def _update_next(self):
        nxt = self.ctl.next_alarm()
        if nxt:
            alarm, at = nxt
            d, h, m = until_text(at)
            self.next_label.setText(tr("next_alarm_in", time=format_hhmm(at.strftime("%H:%M")),
                                       text=self.ctl.duration_text(d, h, m)))
        else:
            self.next_label.setText(tr("no_alarms_on"))

    def refresh(self, what: str = "", animate: bool = True):
        if what not in ("", "alarms", "settings"):
            return
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                dispose(item.widget())
        alarms = self.ctl.db.list_alarms()
        self.cards = []
        if not alarms:
            self.grid.addWidget(EmptyState(Icons.BELL, tr("alarms_empty")), 0, 0, 1, self.COLUMNS)
        for i, alarm in enumerate(alarms):
            card = AlarmCard(alarm, self.ctl)
            self.grid.addWidget(card, i // self.COLUMNS, i % self.COLUMNS)
            self.cards.append(card)
        for c in range(self.COLUMNS):
            self.grid.setColumnStretch(c, 1)
        self.grid.setRowStretch(len(alarms) // self.COLUMNS + 1, 1)
        self._update_next()
        if animate and self.cards:
            cards = list(self.cards)
            QTimer.singleShot(0, lambda: stagger_in(cards, step=50))

    def on_shown(self):
        self.refresh("alarms")


class LockView(QWidget):
    """Designer: style chips, brightness, what to show, idle timer, live preview."""

    def __init__(self, ctl, page):
        super().__init__()
        self.ctl, self.page = ctl, page
        self.opts = ctl.lock_options()
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        preview_card = Card(padding=16)
        self.preview = LockPreview(ctl.lock_state, self.opts)
        preview_card.root.addWidget(self.preview, 1)
        show = primary_button(tr("show_lock_now"), Icons.LOCK)
        show.clicked.connect(ctl.show_lock_screen)
        row = QHBoxLayout()
        row.addWidget(label(tr("lock_hotkey_hint", hotkey=ctl.hotkey_text("lock_screen")), 9, role="Dim"))
        row.addStretch(1)
        row.addWidget(show)
        preview_card.root.addLayout(row)
        root.addWidget(preview_card, 3)

        side = Card(tr("lock_design"), Icons.COLOR, padding=18)
        side.setFixedWidth(380)
        side.root.addWidget(label(tr("lock_style"), 9.5, role="Dim"))
        styles = QGridLayout()
        styles.setSpacing(6)
        group = QButtonGroup(self)
        for i, key in enumerate(STYLES):
            b = QPushButton(tr(f"lock_style_{key}"))
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setChecked(self.opts["style"] == key)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(ui_font(9.5))
            b.clicked.connect(lambda _=False, k=key: self._set("style", k))
            group.addButton(b)
            styles.addWidget(b, i // 3, i % 3)
        side.root.addLayout(styles)

        side.root.addWidget(label(tr("brightness"), 9.5, role="Dim"))
        bright = QSlider(Qt.Orientation.Horizontal)
        bright.setRange(30, 100)
        bright.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        bright.setValue(int(self.opts["brightness"]))
        bright.valueChanged.connect(lambda v: self._set("brightness", v))
        side.root.addWidget(bright)

        for key in ("seconds", "date", "info", "quote", "battery"):
            line = QHBoxLayout()
            line.addWidget(label(tr(f"lock_show_{key}"), 10), 1)
            sw = ToggleSwitch(bool(self.opts.get(key)))
            sw.toggled.connect(lambda on, k=key: self._set(k, on))
            line.addWidget(sw)
            side.root.addLayout(line)

        side.root.addSpacing(6)
        side.root.addWidget(label(tr("lock_idle"), 9.5, role="Dim"))
        idle = QComboBox()
        for minutes in (0, 1, 2, 5, 10, 15, 30):
            idle.addItem(tr("never") if not minutes else tr("after_min", n=minutes), minutes)
        idle.setCurrentIndex(max(0, idle.findData(int(self.opts["idle_min"]))))
        idle.currentIndexChanged.connect(lambda _: self._set("idle_min", idle.currentData()))
        side.root.addWidget(idle)
        lock_row = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(tr("lock_windows"), 10))
        col.addWidget(label(tr("lock_windows_desc"), 8.6, role="Faint", wrap=True))
        lock_row.addLayout(col, 1)
        lw = ToggleSwitch(bool(self.opts["lock_windows"]))
        lw.toggled.connect(lambda on: self._set("lock_windows", on))
        lock_row.addWidget(lw)
        side.root.addLayout(lock_row)
        side.root.addStretch(1)
        root.addWidget(side)

    def _set(self, key: str, value):
        self.opts[key] = value
        self.ctl.save_lock_options(self.opts)
        self.preview.update()

    def refresh(self, what: str = ""):
        pass


class ClockPage(Page):
    def __init__(self, ctl, main_window):
        super().__init__(ctl, main_window)
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(tr("nav_clock"), 22, QFont.Weight.DemiBold))
        titles.addWidget(label(tr("clock_subtitle"), 10, role="Dim"))
        header.addLayout(titles)
        header.addStretch(1)
        self.tabs = SegmentedTabs([("alarms", Icons.BELL, tr("tab_alarms")), ("lock", Icons.LOCK, tr("tab_lock"))],
                                  "alarms")
        self.tabs.changed.connect(self.show_tab)
        header.addWidget(self.tabs)
        root.addLayout(header)
        self.stack = AnimatedStack()
        self.views = {"alarms": AlarmsView(ctl, self), "lock": LockView(ctl, self)}
        for v in self.views.values():
            self.stack.addWidget(v)
        root.addWidget(self.stack, 1)
        self.current = "alarms"
        self.listen(ctl.data_changed, self.refresh)

    def show_tab(self, key: str):
        if key in self.views:
            self.current = key
            self.tabs.set_current(key)
            self.stack.switch_to(self.stack.indexOf(self.views[key]), animate=self.isVisible())

    def refresh(self, what: str = ""):
        for v in self.views.values():
            v.refresh(what)

    def on_shown(self):
        view = self.views[self.current]
        if hasattr(view, "on_shown"):
            view.on_shown()
