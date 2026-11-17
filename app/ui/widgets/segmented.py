"""Segmented tab bar with a pill that glides to the selected tab."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter
from PyQt6.QtWidgets import QWidget

from app.ui.animations import OUT, value_animation
from app.ui.theme import Palette, Theme, icon_font, lerp_color, qcolor, ui_font, ink

_PAD_X, _GLYPH_W, _GAP, _HEIGHT = 16, 22, 4, 40


class SegmentedTabs(QWidget):
    changed = pyqtSignal(str)

    def __init__(self, items: list[tuple[str, str, str]], current: str | None = None, parent=None):
        """items: [(key, glyph, text)]"""
        super().__init__(parent)
        self.items = items
        self.current = current or items[0][0]
        self._badges: dict[str, int] = {}
        self._hover: str | None = None
        self._pill = QRectF()
        self._anim = value_animation(self, QRectF(), QRectF(), 360, self._set_pill, OUT)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(_HEIGHT + 8)

    # ------------------------------------------------------------- geometry
    def _text_font(self) -> QFont:
        return ui_font(10, QFont.Weight.DemiBold)

    def _item_width(self, key: str, text: str) -> int:
        width = QFontMetrics(self._text_font()).horizontalAdvance(text) + _PAD_X * 2 + _GLYPH_W
        if self._badges.get(key):
            width += 30
        return width

    def _rects(self) -> dict[str, QRectF]:
        rects, x = {}, 4.0
        widths = [(k, self._item_width(k, t)) for k, _, t in self.items]
        total = sum(w for _, w in widths) + _GAP * (len(widths) - 1) + 8
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        for key, w in widths:
            r = QRectF(x, 4, w, _HEIGHT)
            if rtl:
                r.moveLeft(total - x - w)
            rects[key] = r
            x += w + _GAP
        return rects

    def sizeHint(self) -> QSize:
        return QSize(int(sum(self._item_width(k, t) for k, _, t in self.items) + _GAP * len(self.items) + 8),
                     _HEIGHT + 8)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # ---------------------------------------------------------------- state
    def set_badges(self, badges: dict[str, int]) -> None:
        self._badges = badges
        self.updateGeometry()
        self._pill = self._rects().get(self.current, QRectF())
        self.update()

    def set_current(self, key: str, animate: bool = True, emit: bool = False) -> None:
        target = self._rects().get(key)
        if target is None:
            return
        self.current = key
        if animate and not self._pill.isNull():
            self._anim.stop()
            self._anim.setStartValue(self._pill)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._pill = target
            self.update()
        if emit:
            self.changed.emit(key)

    def _set_pill(self, r):
        self._pill = QRectF(r)
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._anim.state() != self._anim.State.Running:
            self._pill = self._rects().get(self.current, QRectF())

    # ---------------------------------------------------------------- mouse
    def _key_at(self, pos) -> str | None:
        for key, r in self._rects().items():
            if r.contains(pos):
                return key
        return None

    def mouseMoveEvent(self, e):
        key = self._key_at(e.position())
        if key != self._hover:
            self._hover = key
            self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        key = self._key_at(e.position())
        if key and key != self.current:
            self.set_current(key, emit=True)

    # -------------------------------------------------------------- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rects = self._rects()
        if rects:
            outer = QRectF(min(r.left() for r in rects.values()) - 4, 0,
                           sum(r.width() for r in rects.values()) + _GAP * (len(rects) - 1) + 8, _HEIGHT + 8)
            p.setPen(ink(0.08))
            p.setBrush(ink(0.04))
            p.drawRoundedRect(outer.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        if not self._pill.isNull():
            grad = QLinearGradient(self._pill.topLeft(), self._pill.bottomRight())
            grad.setColorAt(0, qcolor(Theme.accent(), 0.9))
            grad.setColorAt(1, qcolor(Theme.accent2(), 0.75))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(self._pill, 11, 11)

        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        for key, glyph, text in self.items:
            r = rects[key]
            active = key == self.current
            hovered = key == self._hover and not active
            color = QColor("#FFFFFF") if active else lerp_color(
                QColor(Palette.text_dim), QColor(Palette.text), 1.0 if hovered else 0.0)
            p.setPen(color)
            inner = r.adjusted(_PAD_X, 0, -_PAD_X, 0)
            glyph_rect = QRectF(inner.right() - _GLYPH_W if rtl else inner.left(), r.top(), _GLYPH_W, r.height())
            p.setFont(icon_font(10.5))
            p.drawText(glyph_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, glyph)
            text_rect = inner.adjusted(0, 0, -_GLYPH_W, 0) if rtl else inner.adjusted(_GLYPH_W, 0, 0, 0)
            badge = self._badges.get(key)
            if badge:
                bw = 26
                badge_rect = QRectF(text_rect.left() if rtl else text_rect.right() - bw,
                                    r.center().y() - 9, bw, 18)
                text_rect = text_rect.adjusted(bw + 4, 0, 0, 0) if rtl else text_rect.adjusted(0, 0, -bw - 4, 0)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(qcolor("#FFFFFF", 0.22) if active else qcolor(Theme.accent(), 0.25))
                p.drawRoundedRect(badge_rect, 9, 9)
                p.setPen(QColor("#FFFFFF") if active else QColor(Palette.text))
                p.setFont(ui_font(8.2, QFont.Weight.DemiBold))
                p.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, str(badge) if badge < 100 else "99+")
                p.setPen(color)
            p.setFont(self._text_font())
            p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)
