"""Boot animation shown when Study Hub starts — the "OS is waking up" moment."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QRadialGradient

from app.config import APP_NAME
from app.core.i18n import tr
from app.ui.animations import IN_OUT, OUT, value_animation
from app.ui.theme import Palette, Theme, app_icon_pixmap, qcolor, ui_font, ink
from app.ui.widgets.common import Overlay


def _ease_out_back(t: float) -> float:
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


class BootSplash(Overlay):
    finished = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        self._progress = 0.0
        self._fade = 1.0
        self._logo = app_icon_pixmap(240)
        self._anim = value_animation(self, 0.0, 1.0, 1500, self._set_progress, IN_OUT, self._fade_out)
        self._fade_anim = value_animation(self, 1.0, 0.0, 420, self._set_fade, OUT, self._done)

    def start(self):
        self.show()
        self.raise_()
        self._anim.start()

    def _set_progress(self, v):
        self._progress = float(v)
        self.update()

    def _set_fade(self, v):
        self._fade = float(v)
        self.update()

    def _fade_out(self):
        self._fade_anim.start()
        self.finished.emit()  # let the UI start its entrance under the fading splash

    def _done(self):
        self.hide()
        self.deleteLater()

    def mousePressEvent(self, _):
        self._anim.stop()
        self._fade_out()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setOpacity(self._fade)
        p.fillRect(self.rect(), QColor(Palette.bg))
        c = QPointF(self.width() / 2, self.height() / 2 - 30)
        t = self._progress

        glow = QRadialGradient(c, 180 + 260 * t)
        glow.setColorAt(0, qcolor(Theme.accent(), 0.30))
        glow.setColorAt(0.6, qcolor(Theme.accent2(), 0.06))
        glow.setColorAt(1, qcolor(Theme.accent(), 0.0))
        p.fillRect(self.rect(), glow)

        appear = _ease_out_back(min(1.0, t / 0.45))
        size = 120 * (0.6 + 0.4 * appear)
        p.save()
        p.setOpacity(self._fade * min(1.0, t / 0.25))
        p.drawPixmap(QRectF(c.x() - size / 2, c.y() - size / 2 - 20, size, size).toRect(), self._logo)
        p.restore()

        text_alpha = max(0.0, min(1.0, (t - 0.25) / 0.3))
        p.setOpacity(self._fade * text_alpha)
        p.setPen(QColor(Palette.text))
        p.setFont(ui_font(26, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, c.y() + 56, self.width(), 50), Qt.AlignmentFlag.AlignCenter, APP_NAME)
        p.setPen(QColor(Palette.text_dim))
        p.setFont(ui_font(11))
        p.drawText(QRectF(0, c.y() + 104, self.width(), 30), Qt.AlignmentFlag.AlignCenter, tr("tagline"))

        bar = QRectF(c.x() - 110, c.y() + 160, 220, 4)
        p.setOpacity(self._fade)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink(0.08))
        p.drawRoundedRect(bar, 2, 2)
        fill = QLinearGradient(bar.topLeft(), bar.topRight())
        fill.setColorAt(0, QColor(Theme.accent()))
        fill.setColorAt(1, QColor(Theme.accent2()))
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * t, bar.height()), 2, 2)
