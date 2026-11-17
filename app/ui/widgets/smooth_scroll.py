"""
Scroll area that glides: the wheel / touchpad eases to its target instead of jumping, and a thin scrollbar
floats over the content — it shows while you scroll (or hover the edge) and fades away after, so it never
takes width from the content (no re-layout of the desktop when it appears).
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QScrollArea, QWidget

from app.ui.theme import Theme, ink


class _Indicator(QWidget):
    """The floating scrollbar: drag it, click the track to jump."""

    WIDTH = 12

    def __init__(self, area: "SmoothScrollArea"):
        super().__init__(area)
        self.area = area
        self.opacity = 0.0
        self._hover = False
        self._drag: tuple[float, int] | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._fade = QVariantAnimation(self, duration=260)
        self._fade.valueChanged.connect(self._set_opacity)
        self._idle = QTimer(self, singleShot=True, interval=1100, timeout=lambda: self.fade(False))

    def _set_opacity(self, v):
        self.opacity = float(v)
        self.setVisible(self.opacity > 0.01)
        self.update()

    def fade(self, show: bool) -> None:
        if show:
            self._idle.start()
            if self.opacity >= 0.99 and self._fade.state() != QVariantAnimation.State.Running:
                return
        elif self._hover or self._drag:
            self._idle.start()
            return
        self._fade.stop()
        self._fade.setStartValue(self.opacity)
        self._fade.setEndValue(1.0 if show else 0.0)
        self._fade.start()

    def handle(self) -> QRectF:
        bar = self.area.verticalScrollBar()
        total = bar.maximum() - bar.minimum() + bar.pageStep()
        h = self.height()
        if total <= 0 or bar.maximum() <= 0:
            return QRectF()
        length = max(36.0, h * bar.pageStep() / total)
        y = (h - length) * (bar.value() - bar.minimum()) / max(1, bar.maximum() - bar.minimum())
        return QRectF(0, y, self.width(), length)

    def enterEvent(self, e):
        self._hover = True
        self.fade(True)
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        self._idle.start()

    def mousePressEvent(self, e):
        h = self.handle()
        y = e.position().y()
        if h.contains(e.position()):
            self._drag = (y, self.area.verticalScrollBar().value())
        else:  # click on the track: glide there
            bar = self.area.verticalScrollBar()
            frac = y / max(1, self.height())
            self.area.scroll_to(int(bar.minimum() + frac * (bar.maximum() - bar.minimum())))

    def mouseMoveEvent(self, e):
        if self._drag:
            y0, v0 = self._drag
            bar = self.area.verticalScrollBar()
            h = self.handle()
            free = max(1.0, self.height() - h.height())
            self.area.stop_glide()
            bar.setValue(int(v0 + (e.position().y() - y0) / free * (bar.maximum() - bar.minimum())))

    def mouseReleaseEvent(self, e):
        self._drag = None
        self._idle.start()

    def paintEvent(self, _):
        h = self.handle()
        if h.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self.opacity)
        wide = self._hover or self._drag
        if wide:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink(0.06))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 5, 5)
        w = 8.0 if wide else 4.0
        r = QRectF(self.width() - w - 3, h.top() + 3, w, h.height() - 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(Theme.accent()) if self._drag else ink(0.42 if wide else 0.3))
        p.drawRoundedRect(r, w / 2, w / 2)


class SmoothScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # ours floats over the content
        self.viewport().setAutoFillBackground(False)
        self._target: float | None = None
        self._glide = QVariantAnimation(self, duration=340)
        self._glide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._glide.valueChanged.connect(lambda v: self.verticalScrollBar().setValue(int(v)))
        self._glide.finished.connect(lambda: setattr(self, "_target", None))
        self.indicator = _Indicator(self)
        self.indicator.hide()
        self.verticalScrollBar().valueChanged.connect(self._moved)
        self.verticalScrollBar().rangeChanged.connect(lambda *_: self.indicator.update())
        self.viewport().installEventFilter(self)
        self.viewport().setMouseTracking(True)

    def _moved(self, *_):
        self.indicator.update()
        if self.verticalScrollBar().maximum() > 0:
            self.indicator.fade(True)

    def stop_glide(self) -> None:
        self._glide.stop()
        self._target = None

    def scroll_to(self, value: int) -> None:
        bar = self.verticalScrollBar()
        value = max(bar.minimum(), min(bar.maximum(), value))
        self._target = float(value)
        self._glide.stop()
        self._glide.setStartValue(float(bar.value()))
        self._glide.setEndValue(float(value))
        self._glide.start()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return super().wheelEvent(e)
        bar = self.verticalScrollBar()
        if e.pixelDelta().y() and not e.angleDelta().y() % 120 == 0:  # precise touchpad: follow the fingers
            self.stop_glide()
            bar.setValue(bar.value() - e.pixelDelta().y())
            e.accept()
            return
        step = -e.angleDelta().y() / 120 * max(90, bar.singleStep() * 3, self.viewport().height() // 7)
        base = self._target if self._target is not None else bar.value()
        self.scroll_to(int(base + step))
        e.accept()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        w = _Indicator.WIDTH
        self.indicator.setGeometry(self.width() - w - 2, 4, w, self.height() - 8)
        if self.layoutDirection() == Qt.LayoutDirection.RightToLeft:
            self.indicator.move(2, 4)

    def eventFilter(self, obj, event):
        if obj is self.viewport() and event.type() == QEvent.Type.MouseMove:
            x = event.position().x()
            near = (x < 30) if self.layoutDirection() == Qt.LayoutDirection.RightToLeft else (x > self.viewport().width() - 30)
            if near and self.verticalScrollBar().maximum() > 0:
                self.indicator.fade(True)
        return super().eventFilter(obj, event)
