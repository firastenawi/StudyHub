"""
Weekly timetable: a custom-painted calendar grid.

* classes pop in one after another, glow on hover
* today's column is tinted, a pulsing line marks the current time
* click an empty cell -> add a class there; click a class -> edit; right-click -> menu
* overlapping classes share the column side by side
"""
from __future__ import annotations

import math
from datetime import datetime

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from app.core.i18n import day_name, format_hm, format_range, meridiem, ordered_days, tr, uses_12h
from app.ui.animations import IN_OUT, OUT, value_animation
from app.ui.theme import Icons, Palette, Theme, qcolor, ui_font, ink
from app.ui.widgets.common import EmptyState, add_action, button, label, make_menu, primary_button

GUTTER, HEADER, HOUR_H = 58, 50, 62


def _hour_label(h: int) -> str:
    """'8 ص' / '8 AM' (12-hour) or '08:00'."""
    return f"{h % 12 or 12} {meridiem(h)}" if uses_12h() else f"{h:02d}:00"


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


class WeekGrid(QWidget):
    slot_clicked = pyqtSignal(int)
    slot_menu = pyqtSignal(int, QPoint)
    empty_clicked = pyqtSignal(int, str)  # day_of_week, 'HH:MM'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.slots: list[dict] = []
        self.days: list[int] = list(range(7))
        self.start_h, self.end_h = 8, 18
        self._hover_id: int | None = None
        self._hover_cell: tuple[int, int] | None = None
        self._appear = 1.0
        self._pulse = 0.0
        self._rects: list[tuple[QRectF, dict]] = []
        self._appear_anim = value_animation(self, 0.0, 1.0, 900, self._set_appear, OUT)
        self._pulse_anim = value_animation(self, 0.0, 1.0, 1800, self._set_pulse, IN_OUT)
        self._pulse_anim.setLoopCount(-1)
        self._clock = QTimer(self, timeout=self.update)
        self._clock.start(30_000)

    def _set_appear(self, v):
        self._appear = float(v)
        self.update()

    def _set_pulse(self, v):
        self._pulse = float(v)
        self.update(self._now_line_rect().toAlignedRect())

    def showEvent(self, e):
        self._pulse_anim.start()

    def hideEvent(self, e):
        self._pulse_anim.stop()

    def set_data(self, slots: list[dict], week_start: int, animate: bool = True):
        self.slots = slots
        self.days = ordered_days(week_start)
        starts = [_minutes(s["start_time"]) for s in slots] or [8 * 60]
        ends = [_minutes(s["end_time"]) for s in slots] or [18 * 60]
        self.start_h = min(8, min(starts) // 60)
        self.end_h = max(18, math.ceil(max(ends) / 60))
        self.setMinimumHeight(HEADER + (self.end_h - self.start_h) * HOUR_H + 12)
        if animate:
            self._appear_anim.stop()
            self._appear_anim.start()
        self.update()

    # ------------------------------------------------------------- geometry
    def _rtl(self) -> bool:
        return self.layoutDirection() == Qt.LayoutDirection.RightToLeft

    def _col_w(self) -> float:
        return (self.width() - GUTTER) / 7

    def _col_x(self, index: int) -> float:
        cw = self._col_w()
        return (self.width() - GUTTER - (index + 1) * cw) if self._rtl() else GUTTER + index * cw

    def _y(self, minutes: int) -> float:
        return HEADER + (minutes - self.start_h * 60) / 60 * HOUR_H

    def _now_line_rect(self) -> QRectF:
        today = datetime.now().weekday()
        if today not in self.days:
            return QRectF()
        now = datetime.now()
        y = self._y(now.hour * 60 + now.minute)
        return QRectF(self._col_x(self.days.index(today)) - 10, y - 10, self._col_w() + 20, 20)

    def _layout_slots(self) -> list[tuple[QRectF, dict]]:
        result = []
        cw = self._col_w()
        for index, day in enumerate(self.days):
            day_slots = sorted((s for s in self.slots if s["day_of_week"] == day), key=lambda s: s["start_time"])
            lanes: list[int] = []  # end minute of the last slot in each lane
            placed = []
            for s in day_slots:
                st, en = _minutes(s["start_time"]), _minutes(s["end_time"])
                lane = next((i for i, end in enumerate(lanes) if end <= st), None)
                if lane is None:
                    lanes.append(en)
                    lane = len(lanes) - 1
                else:
                    lanes[lane] = en
                placed.append((s, lane, st, en))
            count = max(1, len(lanes))
            for s, lane, st, en in placed:
                w = (cw - 8) / count
                x = self._col_x(index) + 4 + (count - 1 - lane if self._rtl() else lane) * w
                result.append((QRectF(x, self._y(st) + 2, w - 3, self._y(en) - self._y(st) - 4), s))
        return result

    def _cell_at(self, pos: QPointF) -> tuple[int, int] | None:
        if pos.y() < self._view_top() + HEADER or pos.y() < HEADER:
            return None
        for index in range(7):
            x = self._col_x(index)
            if x <= pos.x() < x + self._col_w():
                minutes = self.start_h * 60 + int((pos.y() - HEADER) / HOUR_H * 60)
                return self.days[index], (minutes // 30) * 30
        return None

    # ---------------------------------------------------------------- mouse
    def mouseMoveEvent(self, e):
        pos = e.position()
        hover = next((s["id"] for r, s in self._rects if r.contains(pos)), None)
        cell = None if hover else self._cell_at(pos)
        if hover != self._hover_id or cell != self._hover_cell:
            self._hover_id, self._hover_cell = hover, cell
            self.setCursor(Qt.CursorShape.PointingHandCursor if (hover or cell) else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hover_id = self._hover_cell = None
        self.update()

    def mouseReleaseEvent(self, e):
        pos = e.position()
        if e.button() != Qt.MouseButton.LeftButton or pos.y() < self._view_top() + HEADER:
            return  # ignore clicks on the sticky header
        for r, s in self._rects:
            if r.contains(pos):
                self.slot_clicked.emit(s["id"])
                return
        cell = self._cell_at(pos)
        if cell:
            day, minutes = cell
            self.empty_clicked.emit(day, f"{minutes // 60:02d}:{minutes % 60:02d}")

    def contextMenuEvent(self, e):
        for r, s in self._rects:
            if r.contains(QPointF(e.pos())):
                self.slot_menu.emit(s["id"], e.globalPos())
                e.accept()
                return
        e.ignore()

    # -------------------------------------------------------------- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rtl, cw = self._rtl(), self._col_w()
        today = datetime.now().weekday()
        grid_bottom = self._y(self.end_h * 60)

        # today's column tint
        if today in self.days:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(Theme.accent(), 0.07))
            p.drawRoundedRect(QRectF(self._col_x(self.days.index(today)) + 2, 4, cw - 4, grid_bottom - 4), 14, 14)
            p.setBrush(Qt.BrushStyle.NoBrush)

        # hour lines + labels
        p.setFont(ui_font(8.5))
        for h in range(self.start_h, self.end_h + 1):
            y = self._y(h * 60)
            p.setPen(QPen(ink(0.06), 1))
            line_from, line_to = (0, self.width() - GUTTER) if rtl else (GUTTER, self.width())
            p.drawLine(QPointF(line_from, y), QPointF(line_to, y))
            p.setPen(QColor(Palette.text_faint))
            lbl_rect = QRectF(self.width() - GUTTER + 6, y - 9, GUTTER - 12, 18) if rtl else QRectF(0, y - 9, GUTTER - 10, 18)
            p.drawText(lbl_rect, (Qt.AlignmentFlag.AlignLeft if rtl else Qt.AlignmentFlag.AlignRight)
                       | Qt.AlignmentFlag.AlignVCenter, _hour_label(h))
        # column separators
        p.setPen(QPen(ink(0.04), 1))
        for index in range(8):
            x = (self.width() - GUTTER - index * cw) if rtl else GUTTER + index * cw
            p.drawLine(QPointF(x, HEADER), QPointF(x, grid_bottom))

        # hovered empty cell: "+ add" ghost
        if self._hover_cell:
            day, minutes = self._hover_cell
            index = self.days.index(day)
            r = QRectF(self._col_x(index) + 4, self._y(minutes) + 2, cw - 8, HOUR_H / 2 - 4)
            p.setPen(QPen(qcolor(Theme.accent(), 0.6), 1.2, Qt.PenStyle.DashLine))
            p.setBrush(qcolor(Theme.accent(), 0.08))
            p.drawRoundedRect(r, 9, 9)
            p.setPen(QColor(Theme.accent()))
            p.setFont(ui_font(8.5, QFont.Weight.DemiBold))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "+ " + format_hm(minutes // 60, minutes % 60))

        # classes
        self._rects = self._layout_slots()
        for i, (r, s) in enumerate(self._rects):
            t = max(0.0, min(1.0, (self._appear - i * 0.05) / 0.45))
            if t <= 0:
                continue
            hovered = s["id"] == self._hover_id
            p.save()
            p.setOpacity(t)
            c = r.center()
            p.translate(c)
            scale = 0.9 + 0.1 * t
            p.scale(scale, scale)
            p.translate(-c + QPointF(0, -2 if hovered else 0))
            color = s["subject_color"]
            path = QPainterPath()
            path.addRoundedRect(r, 10, 10)
            p.fillPath(path, QColor(Palette.surface_solid))
            p.fillPath(path, qcolor(color, 0.30 if hovered else 0.20))
            p.setPen(QPen(qcolor(color, 0.9 if hovered else 0.45), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)  # outline only
            p.drawPath(path)
            bar = QRectF(r.right() - 4, r.top() + 6, 3, r.height() - 12) if rtl else QRectF(r.left() + 1, r.top() + 6, 3, r.height() - 12)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(bar, 1.5, 1.5)
            inner = r.adjusted(10, 6, -8, -6) if not rtl else r.adjusted(8, 6, -10, -6)
            align = (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignTop
            p.setPen(QColor(Palette.text))
            p.setFont(ui_font(9.4, QFont.Weight.DemiBold))
            fm = p.fontMetrics()
            p.drawText(inner, align, fm.elidedText(s["subject_name"], Qt.TextElideMode.ElideRight, int(inner.width())))
            if inner.height() > 30:
                p.setPen(QColor(Palette.text_dim))
                p.setFont(ui_font(8.4))
                lines = [format_range(s["start_time"], s["end_time"])]
                if s.get("location"):
                    lines.append(s["location"])
                if s.get("kind") and s["kind"] != "lecture":
                    lines.append(tr(f"slot_{s['kind']}"))
                y = inner.top() + 20
                for line in lines:
                    if y + 14 > inner.bottom():
                        break
                    p.drawText(QRectF(inner.left(), y, inner.width(), 16), align,
                               p.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, int(inner.width())))
                    y += 16
            p.restore()

        # current time
        now_rect = self._now_line_rect()
        now = datetime.now()
        if not now_rect.isNull() and self.start_h * 60 <= now.hour * 60 + now.minute <= self.end_h * 60:
            y = now_rect.center().y()
            x0, x1 = now_rect.left() + 10, now_rect.right() - 10
            p.setPen(QPen(QColor(Palette.danger), 2))
            p.drawLine(QPointF(x0, y), QPointF(x1, y))
            dot_x = x1 if rtl else x0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(Palette.danger, 0.25 * (1 - self._pulse)))
            p.drawEllipse(QPointF(dot_x, y), 5 + 6 * self._pulse, 5 + 6 * self._pulse)
            p.setBrush(QColor(Palette.danger))
            p.drawEllipse(QPointF(dot_x, y), 4.5, 4.5)

        # sticky day header, painted last at the top of the visible area
        top = self._view_top()
        band = QRectF(0, top, self.width(), HEADER)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(Palette.bg, 0.88 if top > 0 else 0.0))
        p.drawRect(band)
        for index, day in enumerate(self.days):
            x = self._col_x(index)
            is_today = day == today
            if is_today:
                p.setBrush(qcolor(Theme.accent(), 0.9))
                p.drawRoundedRect(QRectF(x + cw / 2 - 48, top + 10, 96, 30), 15, 15)
            p.setPen(QColor("#FFFFFF") if is_today else QColor(Palette.text_dim))
            p.setFont(ui_font(10, QFont.Weight.DemiBold))
            p.drawText(QRectF(x, top + 10, cw, 30), Qt.AlignmentFlag.AlignCenter, day_name(day))
            p.setPen(Qt.PenStyle.NoPen)

    def _view_top(self) -> int:
        """Y of the first visible pixel (the grid lives in a scroll area)."""
        return max(0, self.visibleRegion().boundingRect().top())


class TimetableView(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl, self.page = ctl, page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.legend = QHBoxLayout()
        self.legend.setSpacing(14)
        bar.addLayout(self.legend)
        bar.addStretch(1)
        add_subject = button(tr("add_subject"), Icons.BOOK)
        add_subject.clicked.connect(lambda: ctl.add_subject_dialog(self))
        bar.addWidget(add_subject)
        self.add_class = primary_button(tr("add_class"), Icons.ADD)
        self.add_class.clicked.connect(lambda: ctl.add_slot_dialog(parent=self))
        bar.addWidget(self.add_class)
        lay.addLayout(bar)

        self.grid = WeekGrid()
        self.grid.slot_clicked.connect(lambda sid: ctl.edit_slot(sid, self))
        self.grid.empty_clicked.connect(lambda d, t: ctl.add_slot_dialog(d, t, self))
        self.grid.slot_menu.connect(self._slot_menu)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.grid)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _: self.grid.update())  # sticky header
        lay.addWidget(self.scroll, 1)

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.addStretch(1)
        el.addWidget(EmptyState(Icons.BOOK, tr("timetable_empty")))
        first = primary_button(tr("add_first_subject"), Icons.ADD)
        first.clicked.connect(lambda: ctl.add_subject_dialog(self))
        el.addWidget(first, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addStretch(2)
        lay.addWidget(self.empty, 1)
        self.refresh("", animate=False)

    def refresh(self, what: str = "", animate: bool = True):
        if what not in ("", "subjects", "schedule", "settings"):
            return
        subjects = self.ctl.db.list_subjects()
        has_subjects = bool(subjects)
        self.scroll.setVisible(has_subjects)
        self.empty.setVisible(not has_subjects)
        self.add_class.setEnabled(has_subjects)
        while self.legend.count():
            item = self.legend.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for s in subjects[:8]:
            dot = label("●", 10)
            dot.setStyleSheet(f"color: {s['color']};")
            self.legend.addWidget(dot)
            self.legend.addWidget(label(s["name"], 9.5, role="Dim"))
        self.grid.set_data(self.ctl.db.list_slots(), self.ctl.week_start(), animate)

    def on_shown(self):
        self.grid.set_data(self.grid.slots, self.ctl.week_start(), animate=True)
        now = datetime.now()  # scroll so the current hour is in view
        y = int(self.grid._y(now.hour * 60)) - 120
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(max(0, y)))

    def _slot_menu(self, slot_id: int, pos):
        menu = make_menu(self)
        add_action(menu, Icons.EDIT, tr("edit"), lambda: self.ctl.edit_slot(slot_id, self))
        add_action(menu, Icons.COPY, tr("duplicate"), lambda: self.ctl.duplicate_slot(slot_id))
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("delete"), lambda: self.ctl.delete_slot(slot_id), danger=True)
        menu.exec(pos)
