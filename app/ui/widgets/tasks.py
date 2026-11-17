"""Shared task widgets (used by the home page and the Study Planner)."""
from __future__ import annotations

from datetime import date, timedelta

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton

from app.core.i18n import ltr, tr
from app.ui.animations import OUT_BACK, value_animation
from app.ui.theme import Icons, Palette, icon_font, qcolor

PRIORITY_COLORS = {1: Palette.danger, 2: Palette.warning, 3: Palette.info}
PRIORITY_KEYS = {1: "priority_high", 2: "priority_medium", 3: "priority_low"}


def due_text(due: str | None) -> tuple[str, str]:
    """('Tomorrow', color) for a 'YYYY-MM-DD' due date."""
    if not due:
        return "", Palette.text_faint
    today = date.today()
    try:
        d = date.fromisoformat(due)
    except ValueError:
        return due, Palette.text_faint
    if d < today:
        return tr("overdue") + f" · {ltr(due)}", Palette.danger
    if d == today:
        return tr("due_today"), Palette.warning
    if d == today + timedelta(days=1):
        return tr("due_tomorrow"), Palette.info
    return ltr(due), Palette.text_dim


class CheckCircle(QAbstractButton):
    """Round checkbox that fills with a springy check mark."""

    def __init__(self, color: str, parent=None, size: int = 22):
        super().__init__(parent)
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._t = 0.0
        self._anim = value_animation(self, 0.0, 1.0, 360, self._set, OUT_BACK)
        self.toggled.connect(self._play)

    def _play(self, on: bool):
        self._anim.setDirection(self._anim.Direction.Forward if on else self._anim.Direction.Backward)
        self._anim.start()

    def set_checked_silently(self, on: bool):
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self._set(1.0 if on else 0.0)

    def _set(self, v):
        self._t = float(v)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor(self.color), 1.8))
        p.setBrush(qcolor(self.color, 0.9 * max(0.0, min(1.0, self._t))))
        p.drawEllipse(r)
        if self._t > 0.05:
            p.setPen(QColor("#0A0D14"))
            font = icon_font()
            font.setPixelSize(max(1, int(r.height() * 0.6 * max(0.0, self._t))))
            p.setFont(font)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, Icons.ACCEPT)
