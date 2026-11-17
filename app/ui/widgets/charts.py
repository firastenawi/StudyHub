"""Custom-painted, animated charts: focus ring, weekly bars, horizontal bars."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QConicalGradient, QFont, QLinearGradient, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QSizePolicy, QWidget

from app.ui.animations import IN_OUT, OUT, value_animation
from app.ui.theme import Palette, Theme, qcolor, ui_font, ink


class FocusRing(QWidget):
    """Big circular Pomodoro dial: gradient progress arc, time in the middle,
    session dots below, and a soft 'breathing' glow while running."""

    def __init__(self, size: int = 340, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._progress = 0.0         # displayed (smoothed) progress
        self._target = 0.0
        self._breath = 0.0
        self.time_text, self.caption = "25:00", ""
        self.dots_total, self.dots_done = 4, 0
        self.running = False
        self._smooth = value_animation(self, 0.0, 0.0, 400, self._set_progress, OUT)
        self._breathe = value_animation(self, 0.0, 1.0, 2400, self._set_breath, IN_OUT)
        self._breathe.setLoopCount(-1)

    def _set_progress(self, v):
        self._progress = float(v)
        self.update()

    def _set_breath(self, v):
        t = float(v)
        self._breath = t * 2 if t < 0.5 else (1 - t) * 2  # 0 -> 1 -> 0
        self.update()

    def set_state(self, progress: float, time_text: str, caption: str, running: bool,
                  dots_done: int, dots_total: int):
        self.time_text, self.caption = time_text, caption
        self.dots_done, self.dots_total = dots_done, dots_total
        if abs(progress - self._target) > 1e-4:
            self._target = progress
            self._smooth.stop()
            self._smooth.setStartValue(self._progress)
            self._smooth.setEndValue(progress)
            self._smooth.setDuration(250 if progress > self._progress else 600)
            self._smooth.start()
        if running != self.running:
            self.running = running
            if running:
                self._breathe.start()
            else:
                self._breathe.stop()
                self._breath = 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        radius = self.width() / 2 - 26

        glow = QRadialGradient(c, radius + 30)
        glow.setColorAt(0.65, qcolor(Theme.accent(), 0.0))
        glow.setColorAt(0.85, qcolor(Theme.accent(), 0.10 + 0.18 * self._breath))
        glow.setColorAt(1.0, qcolor(Theme.accent(), 0.0))
        p.fillRect(self.rect(), glow)

        ring = QRectF(c.x() - radius, c.y() - radius, radius * 2, radius * 2)
        p.setPen(QPen(ink(0.07), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawEllipse(ring)
        if self._progress > 0.001:
            grad = QConicalGradient(c, 90)
            grad.setColorAt(0.0, QColor(Theme.accent2()))
            grad.setColorAt(0.5, QColor(Theme.accent()))
            grad.setColorAt(1.0, QColor(Theme.accent2()))
            p.setPen(QPen(grad, 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ring, 90 * 16, int(-self._progress * 360 * 16))  # clockwise from 12 o'clock

        p.setPen(QColor(Palette.text))
        p.setFont(ui_font(46, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, c.y() - 50, self.width(), 80), Qt.AlignmentFlag.AlignCenter, self.time_text)
        p.setPen(QColor(Palette.text_dim))
        p.setFont(ui_font(11.5, QFont.Weight.Medium))
        p.drawText(QRectF(0, c.y() - 86, self.width(), 30), Qt.AlignmentFlag.AlignCenter, self.caption)

        gap, dot = 16, 9
        total_w = self.dots_total * dot + (self.dots_total - 1) * (gap - dot)
        x0 = c.x() - total_w / 2
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(self.dots_total):
            p.setBrush(QColor(Theme.accent()) if i < self.dots_done else ink(0.15))
            p.drawEllipse(QRectF(x0 + i * gap, c.y() + 46, dot, dot))


class WeekBars(QWidget):
    """Vertical bars (e.g. focus minutes per day) that grow when data changes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._data: list[tuple[str, int, bool]] = []  # label, value, highlight
        self._grow = 1.0
        self._anim = value_animation(self, 0.0, 1.0, 700, self._set_grow, OUT)

    def _set_grow(self, v):
        self._grow = float(v)
        self.update()

    def set_data(self, data: list[tuple[str, int, bool]]):
        self._data = data
        self._anim.stop()
        self._anim.start()

    def paintEvent(self, _):
        if not self._data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        data = list(reversed(self._data)) if rtl else self._data
        top, bottom = 22, self.height() - 26
        peak = max(30, max(v for _, v, _ in data))
        slot = self.width() / len(data)
        bar_w = min(34.0, slot * 0.5)
        for i, (lbl, value, hl) in enumerate(data):
            x = slot * i + (slot - bar_w) / 2
            full = (bottom - top) * value / peak * self._grow
            track = QRectF(x, top, bar_w, bottom - top)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink(0.04))
            p.drawRoundedRect(track, 8, 8)
            if value > 0:
                bar = QRectF(x, bottom - max(full, 6), bar_w, max(full, 6))
                grad = QLinearGradient(bar.topLeft(), bar.bottomLeft())
                grad.setColorAt(0, QColor(Theme.accent2()) if hl else qcolor(Theme.accent2(), 0.6))
                grad.setColorAt(1, QColor(Theme.accent()) if hl else qcolor(Theme.accent(), 0.6))
                p.setBrush(grad)
                p.drawRoundedRect(bar, 8, 8)
                p.setPen(QColor(Palette.text_dim))
                p.setFont(ui_font(8, QFont.Weight.DemiBold))
                p.drawText(QRectF(x - 12, bar.top() - 18, bar_w + 24, 16), Qt.AlignmentFlag.AlignCenter,
                           str(int(value * self._grow)))
            p.setPen(QColor(Palette.text if hl else Palette.text_faint))
            p.setFont(ui_font(8.6, QFont.Weight.DemiBold if hl else QFont.Weight.Normal))
            p.drawText(QRectF(slot * i, bottom + 6, slot, 18), Qt.AlignmentFlag.AlignCenter, lbl)


class HBars(QWidget):
    """Horizontal labelled bars (e.g. minutes per subject)."""

    ROW = 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[str, int, str]] = []  # label, value, color
        self._suffix = ""
        self._grow = 1.0
        self._anim = value_animation(self, 0.0, 1.0, 700, self._set_grow, OUT)

    def _set_grow(self, v):
        self._grow = float(v)
        self.update()

    def set_rows(self, rows: list[tuple[str, int, str]], suffix: str = ""):
        self._rows, self._suffix = rows, suffix
        self.setFixedHeight(max(1, len(rows)) * self.ROW)
        self._anim.stop()
        self._anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._rows:
            return
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        peak = max(v for _, v, _ in self._rows) or 1
        label_w = min(150, self.width() * 0.38)
        value_w = 64
        for i, (lbl, value, color) in enumerate(self._rows):
            y = i * self.ROW
            label_rect = QRectF(self.width() - label_w if rtl else 0, y, label_w, self.ROW)
            p.setPen(QColor(Palette.text))
            p.setFont(ui_font(9.5))
            align = (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter
            p.drawText(label_rect, align, p.fontMetrics().elidedText(lbl, Qt.TextElideMode.ElideRight, int(label_w - 8)))
            track_w = self.width() - label_w - value_w
            track_x = value_w if rtl else label_w
            track = QRectF(track_x, y + self.ROW / 2 - 5, track_w, 10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink(0.05))
            p.drawRoundedRect(track, 5, 5)
            w = track_w * value / peak * self._grow
            bar = QRectF(track.right() - w if rtl else track.left(), track.top(), w, 10)
            p.setBrush(QColor(color))
            p.drawRoundedRect(bar, 5, 5)
            p.setPen(QColor(Palette.text_dim))
            p.setFont(ui_font(9, QFont.Weight.DemiBold))
            value_rect = QRectF(0 if rtl else self.width() - value_w, y, value_w, self.ROW)
            p.drawText(value_rect, Qt.AlignmentFlag.AlignCenter, f"{int(value * self._grow)}{self._suffix}")
