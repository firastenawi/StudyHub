"""
Lock screen / Always On Display.

Five styles (all drawn by paint_lock, which the Settings preview reuses):
  minimal  - huge thin digits on pure black (OLED friendly)
  stacked  - hours over minutes, bold, accent minutes, info column
  gradient - slowly drifting aurora behind the clock
  analog   - clean watch face with a smooth second hand
  neon     - glowing outlined digits
Burn-in protection: the whole layout drifts a few pixels over time.
Any key, click or real mouse movement closes it (fading out).
"""
from __future__ import annotations

import math
import time
from datetime import datetime

from PyQt6.QtCore import QPointF, QPropertyAnimation, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QLinearGradient, QPainter,
                         QPainterPath, QPen, QRadialGradient)
from PyQt6.QtWidgets import QWidget

from app.core.i18n import format_long_date, is_rtl, meridiem, tr, uses_12h
from app.ui.theme import Palette, Theme, qcolor, ui_font

STYLES = ["minimal", "stacked", "gradient", "analog", "neon"]
DEFAULT_LOCK = {"style": "stacked", "brightness": 85, "seconds": False, "date": True, "info": True,
                "quote": True, "battery": True, "idle_min": 0, "lock_windows": False}


def _font(px: float, weight=QFont.Weight.Normal) -> QFont:
    f = ui_font(10, weight)
    f.setPixelSize(max(6, int(px)))
    return f


def _time_parts(now: datetime) -> tuple[str, str, str]:
    """(hours, minutes, meridiem) respecting the 12/24-hour setting."""
    if uses_12h():
        return str(now.hour % 12 or 12), f"{now.minute:02d}", meridiem(now.hour)
    return f"{now.hour:02d}", f"{now.minute:02d}", ""


def _info_lines(state: dict, opts: dict) -> list[str]:
    lines = []
    if opts.get("info"):
        if state.get("pomodoro"):
            lines.append("⏱  " + state["pomodoro"])
        if state.get("next_alarm"):
            lines.append("⏰  " + state["next_alarm"])
        if state.get("next_class"):
            lines.append("📘  " + state["next_class"])
        if state.get("tasks"):
            lines.append("✓  " + tr("lock_tasks", n=state["tasks"]))
    if opts.get("battery") and state.get("battery"):
        pct, charging = state["battery"]
        lines.append(("⚡ " if charging else "🔋 ") + f"{pct}%")
    return lines


def paint_lock(p: QPainter, rect: QRectF, state: dict, opts: dict, t: float) -> None:
    """Draws the whole lock screen into `rect` (any size: full screen or a small preview)."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    style = opts.get("style", "stacked")
    s = rect.height() / 1080.0
    now: datetime = state["now"]
    p.fillRect(rect, QColor("#000000"))
    p.save()
    p.setOpacity(max(0.25, min(1.0, opts.get("brightness", 85) / 100)))
    # burn-in protection: everything drifts slowly (±~20 px over many minutes)
    p.translate(20 * s * math.sin(t / 97.0), 16 * s * math.cos(t / 131.0))
    accent, accent2 = QColor(Theme.accent()), QColor(Theme.accent2())
    cx, cy = rect.center().x(), rect.center().y()
    hours, minutes, ampm = _time_parts(now)
    rtl = is_rtl()

    if style == "gradient":
        for k, (col, ph) in enumerate(((accent, 0.0), (accent2, 2.1), (QColor("#1E2A78"), 4.2))):
            x = cx + rect.width() * 0.28 * math.sin(t / 23 + ph)
            y = cy + rect.height() * 0.22 * math.cos(t / 29 + ph)
            g = QRadialGradient(QPointF(x, y), rect.height() * (0.55 + 0.1 * k))
            g.setColorAt(0, qcolor(col.name(), 0.35))
            g.setColorAt(1, qcolor(col.name(), 0.0))
            p.fillRect(rect, g)

    date_text = format_long_date(now.date()) if opts.get("date") else ""
    info = _info_lines(state, opts)

    if style == "analog":
        r = rect.height() * 0.30
        c = QPointF(cx, cy - rect.height() * 0.06)
        p.setPen(QPen(qcolor("#FFFFFF", 0.10), 2 * s))
        p.drawEllipse(c, r, r)
        for i in range(60):
            a = math.radians(i * 6)
            inner = r * (0.86 if i % 5 == 0 else 0.93)
            p.setPen(QPen(qcolor("#FFFFFF", 0.85 if i % 5 == 0 else 0.25), (4 if i % 5 == 0 else 1.5) * s,
                          cap=Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(c.x() + inner * math.sin(a), c.y() - inner * math.cos(a)),
                       QPointF(c.x() + r * 0.97 * math.sin(a), c.y() - r * 0.97 * math.cos(a)))
        sec = now.second + (time.time() % 1)
        for angle, length, width, color in (
                ((now.hour % 12 + now.minute / 60) * 30, 0.52, 9, QColor("#FFFFFF")),
                ((now.minute + sec / 60) * 6, 0.76, 6, QColor("#FFFFFF")),
                (sec * 6, 0.84, 2.5, accent)):
            if color is accent and not opts.get("seconds", True):
                continue
            a = math.radians(angle)
            p.setPen(QPen(color, width * s, cap=Qt.PenCapStyle.RoundCap))
            p.drawLine(c, QPointF(c.x() + r * length * math.sin(a), c.y() - r * length * math.cos(a)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        p.drawEllipse(c, 8 * s, 8 * s)
        y = c.y() + r + 50 * s
        p.setPen(QColor(Palette.text))
        p.setFont(_font(34 * s, QFont.Weight.Medium))
        p.drawText(QRectF(rect.left(), y, rect.width(), 50 * s), Qt.AlignmentFlag.AlignCenter, date_text)
        _draw_info_row(p, rect, y + 64 * s, info, s)
    elif style == "stacked":
        x_main = rect.left() + rect.width() * (0.56 if rtl else 0.12)
        big = _font(300 * s, QFont.Weight.Bold)
        fm = QFontMetricsF(big)
        p.setFont(big)
        top = cy - fm.height() * 0.95
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QPointF(x_main, top + fm.ascent()), hours)
        grad = QLinearGradient(QPointF(x_main, 0), QPointF(x_main + fm.horizontalAdvance("00"), 0))
        grad.setColorAt(0, accent)
        grad.setColorAt(1, accent2)
        p.setPen(QPen(grad, 1))
        path = QPainterPath()
        path.addText(QPointF(x_main, top + fm.height() * 0.82 + fm.ascent()), big, minutes)
        p.fillPath(path, grad)
        if ampm:
            p.setFont(_font(56 * s, QFont.Weight.DemiBold))
            p.setPen(qcolor("#FFFFFF", 0.65))
            p.drawText(QPointF(x_main + fm.horizontalAdvance(minutes) + 24 * s,
                               top + fm.height() * 0.82 + fm.ascent()), ampm)
        col_x = rect.left() + rect.width() * (0.10 if rtl else 0.58)
        col = QRectF(col_x, cy - 150 * s, rect.width() * 0.34, 400 * s)
        align = (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignTop
        p.setPen(QColor(Palette.text))
        p.setFont(_font(40 * s, QFont.Weight.DemiBold))
        p.drawText(col, align, date_text)
        p.setFont(_font(28 * s))
        for i, line in enumerate(info):
            p.setPen(qcolor("#FFFFFF", 0.78))
            p.drawText(col.adjusted(0, 80 * s + i * 50 * s, 0, 0), align, line)
    else:  # minimal / gradient / neon: centered digital clock
        text = f"{hours}:{minutes}"
        weight = QFont.Weight.ExtraLight if style == "minimal" else QFont.Weight.DemiBold
        font = _font(250 * s, weight)
        fm = QFontMetricsF(font)
        tw = fm.horizontalAdvance(text)
        base = QPointF(cx - tw / 2, cy + fm.ascent() * 0.25)
        if style == "neon":
            path = QPainterPath()
            path.addText(base, font, text)
            for width, alpha in ((26, 0.06), (16, 0.12), (8, 0.25), (3, 0.9)):
                p.setPen(QPen(qcolor(accent2.name(), alpha), width * s, cap=Qt.PenCapStyle.RoundCap,
                              join=Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
            p.fillPath(path, qcolor("#FFFFFF", 0.92))
        else:
            p.setFont(font)
            p.setPen(qcolor("#FFFFFF", 0.95))
            p.drawText(base, text)
        if ampm or opts.get("seconds"):
            small = f":{now.second:02d}" if opts.get("seconds") else ""
            p.setFont(_font(54 * s, QFont.Weight.Medium))
            p.setPen(QColor(accent if style != "minimal" else qcolor("#FFFFFF", 0.6)))
            p.drawText(QPointF(base.x() + tw + 14 * s, base.y()), f"{small} {ampm}".strip())
        p.setPen(qcolor("#FFFFFF", 0.82))
        p.setFont(_font(36 * s, QFont.Weight.Medium))
        p.drawText(QRectF(rect.left(), base.y() + 40 * s, rect.width(), 60 * s), Qt.AlignmentFlag.AlignCenter,
                   date_text)
        _draw_info_row(p, rect, base.y() + 120 * s, info, s)

    if opts.get("quote") and state.get("quote"):
        p.setPen(qcolor("#FFFFFF", 0.45))
        p.setFont(_font(24 * s))
        p.drawText(QRectF(rect.left() + rect.width() * 0.15, rect.bottom() - 140 * s, rect.width() * 0.7, 80 * s),
                   Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, state["quote"])
    p.setPen(qcolor("#FFFFFF", 0.28))
    p.setFont(_font(20 * s))
    p.drawText(QRectF(rect.left(), rect.bottom() - 54 * s, rect.width(), 30 * s), Qt.AlignmentFlag.AlignCenter,
               tr("lock_hint"))
    p.restore()


def _draw_info_row(p: QPainter, rect: QRectF, y: float, info: list[str], s: float) -> None:
    if not info:
        return
    p.setFont(_font(27 * s))
    p.setPen(qcolor("#FFFFFF", 0.75))
    p.drawText(QRectF(rect.left(), y, rect.width(), 44 * s), Qt.AlignmentFlag.AlignCenter, "      ".join(info))


class LockScreen(QWidget):
    """One full-screen window per monitor."""
    closed = pyqtSignal()

    def __init__(self, screen, state_provider, opts: dict):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.state_provider, self.opts = state_provider, opts
        self.state = state_provider()
        self._start_cursor = QCursor.pos()
        self._closing = False
        self.setGeometry(screen.geometry())
        smooth = opts.get("style") in ("gradient", "analog") or opts.get("seconds")
        self._timer = QTimer(self, interval=50 if smooth else 500, timeout=self._tick)
        self._timer.start()
        self._mouse_check = QTimer(self, interval=150, timeout=self._check_mouse)
        self._mouse_check.start()

    def show_animated(self):
        self.setWindowOpacity(0.0)
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(600)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def _tick(self):
        self.state = self.state_provider()
        self.update()

    def _check_mouse(self):
        if (QCursor.pos() - self._start_cursor).manhattanLength() > 40:
            self.dismiss()

    def dismiss(self):
        if self._closing:
            return
        self._closing = True
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(300)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self._finish)
        self._fade.start()

    def _finish(self):
        self.closed.emit()
        self.close()

    def keyPressEvent(self, e):
        self.dismiss()

    def mousePressEvent(self, e):
        self.dismiss()

    def paintEvent(self, _):
        paint_lock(QPainter(self), QRectF(self.rect()), self.state, self.opts, time.time())


class LockPreview(QWidget):
    """Live miniature used in Settings."""

    def __init__(self, state_provider, opts: dict, parent=None):
        super().__init__(parent)
        self.state_provider, self.opts = state_provider, opts
        self.setMinimumSize(480, 270)
        self._timer = QTimer(self, interval=100, timeout=self.update)
        self._timer.start()

    def heightForWidth(self, w):
        return int(w * 9 / 16)

    def hasHeightForWidth(self):
        return True

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = min(self.height(), int(w * 9 / 16))
        rect = QRectF(0, (self.height() - h) / 2, w, h)
        path = QPainterPath()
        path.addRoundedRect(rect, 18, 18)
        p.setClipPath(path)
        paint_lock(p, rect, self.state_provider(), self.opts, time.time())
        p.setClipping(False)
        p.setPen(QPen(qcolor("#FFFFFF", 0.12), 1))
        p.drawPath(path)
