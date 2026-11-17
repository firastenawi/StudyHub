"""
Motion helpers.

Rules followed everywhere in the app (UI/UX):
  * entrances: 300-450 ms, OutCubic (fast start, soft landing)
  * hovers: 140-180 ms
  * lists animate with a small stagger (40-70 ms) so the eye can follow
  * opacity effects are removed when an animation ends (they are expensive and
    must never be nested)
"""
from __future__ import annotations

from PyQt6.QtCore import (QAbstractAnimation, QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation,
                          QSequentialAnimationGroup, QTimer, QVariantAnimation)
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QStackedWidget, QWidget

OUT = QEasingCurve.Type.OutCubic
OUT_BACK = QEasingCurve.Type.OutBack
IN_OUT = QEasingCurve.Type.InOutCubic


def value_animation(parent, start, end, duration: int, on_value, easing=OUT,
                    on_done=None) -> QVariantAnimation:
    """Generic tween: calls on_value(v) every frame."""
    anim = QVariantAnimation(parent)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(duration)
    anim.setEasingCurve(easing)
    anim.valueChanged.connect(on_value)
    if on_done:
        anim.finished.connect(on_done)
    return anim


def quiesce(widget: QWidget) -> None:
    """Silences and stops every animation inside `widget` (recursively), so none of them can
    call back into a half-destroyed widget while Qt deletes it."""
    try:
        for anim in widget.findChildren(QAbstractAnimation):
            anim.blockSignals(True)
            anim.stop()
    except RuntimeError:
        pass  # already gone


def dispose(widget: QWidget) -> None:
    """Safely removes a widget that may still be animating."""
    quiesce(widget)
    try:
        widget.hide()
        widget.deleteLater()
    except RuntimeError:
        pass


def clear_list(list_widget) -> None:
    """QListWidget.clear() deletes row widgets immediately: quiesce them first."""
    for i in range(list_widget.count()):
        row = list_widget.itemWidget(list_widget.item(i))
        if row is not None:
            quiesce(row)
    list_widget.clear()


def start_delayed(owner, animation: QVariantAnimation, delay: int) -> None:
    """Starts `animation` after `delay` ms. The pause lives in a Qt animation group owned by
    `owner`, so nothing can fire after the owner is deleted (unlike QTimer.singleShot + lambda)."""
    old = getattr(owner, "_delayed_group", None)
    if old is not None:
        old.stop()
        taken = old.takeAnimation(old.animationCount() - 1)  # take the animation back before dropping the group
        if taken is not None:
            taken.setParent(owner)
        old.deleteLater()
    animation.stop()
    animation.setDirection(animation.Direction.Forward)
    group = QSequentialAnimationGroup(owner)
    group.addPause(max(0, delay))
    group.addAnimation(animation)
    owner._delayed_group = group
    group.start()


def fade_slide_in(widget: QWidget, delay: int = 0, duration: int = 420, dy: int = 18) -> None:
    """Card entrance: fade in while sliding up a few pixels."""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    end = widget.pos()

    fade = QPropertyAnimation(effect, b"opacity", widget)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setDuration(duration)
    fade.setEasingCurve(OUT)

    move = QPropertyAnimation(widget, b"pos", widget)
    move.setStartValue(end + QPoint(0, dy))
    move.setEndValue(end)
    move.setDuration(duration)
    move.setEasingCurve(OUT)

    group = QParallelAnimationGroup(widget)
    group.addAnimation(fade)
    group.addAnimation(move)
    sequence = QSequentialAnimationGroup(widget)
    sequence.addPause(delay)
    sequence.addAnimation(group)

    def done():
        widget.setGraphicsEffect(None)
        if widget.parentWidget() and widget.parentWidget().layout():
            widget.parentWidget().layout().invalidate()  # snap back to the exact layout position

    sequence.finished.connect(done)
    widget._entrance = sequence  # keep a Python reference alive
    sequence.start()


def stagger_in(widgets: list[QWidget], start_delay: int = 0, step: int = 60, **kwargs) -> None:
    """Runs fade_slide_in on each widget with an increasing delay."""
    for index, widget in enumerate(widgets):
        try:
            fade_slide_in(widget, delay=start_delay + index * step, **kwargs)
        except RuntimeError:
            pass  # the widget was deleted by a refresh before its entrance started


class AnimatedStack(QStackedWidget):
    """QStackedWidget whose pages fade + glide in when switching."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._anim: QParallelAnimationGroup | None = None
        self._animating: QWidget | None = None

    def switch_to(self, index: int, animate: bool = True) -> None:
        if index == self.currentIndex() and self._animating is None:
            return
        self._finish()
        self.setCurrentIndex(index)
        page = self.widget(index)
        if not animate:
            self._notify_shown(page)
            return

        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.0)
        page.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", page)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setDuration(280)
        fade.setEasingCurve(OUT)
        move = QPropertyAnimation(page, b"pos", page)
        move.setStartValue(QPoint(0, 26))
        move.setEndValue(QPoint(0, 0))
        move.setDuration(340)
        move.setEasingCurve(OUT)

        self._anim = QParallelAnimationGroup(self)
        self._anim.addAnimation(fade)
        self._anim.addAnimation(move)
        self._anim.finished.connect(self._finish)
        self._animating = page
        self._anim.start()

    def _finish(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        page, self._animating = self._animating, None
        if page is not None:
            page.setGraphicsEffect(None)
            page.move(0, 0)
            self._notify_shown(page)

    @staticmethod
    def _notify_shown(page: QWidget) -> None:
        def notify():
            try:
                if page.isVisible() and hasattr(page, "on_shown"):  # not before the window is shown
                    page.on_shown()
            except RuntimeError:
                pass  # page was destroyed in the meantime (UI rebuilt)
        QTimer.singleShot(0, notify)
