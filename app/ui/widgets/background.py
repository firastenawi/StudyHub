"""
The wallpaper behind the whole shell.

  aurora   soft blobs in the accent colors drifting slowly, like an OS lock screen (default)
  preset   the same, in a fixed color set: nebula, dawn, forest, ocean
  plain    just the background color
  image    the user's picture (cover-scaled, optional blur), copied to %APPDATA%\\StudyHub\\wallpapers

For performance the blobs are rendered into a tiny image (192x108) and scaled up smoothly: the blur
is free and each frame costs almost nothing. It slows down when the window is not active and stops
when hidden. A veil (darker on the dark theme, lighter on the light one) keeps text readable; its
strength is the "dim" setting.
"""
from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QRadialGradient
from PyQt6.QtWidgets import QWidget

from app.ui.theme import Palette, Theme, qcolor

_W, _H = 192, 108
PRESETS: dict[str, tuple[str, str, str]] = {   # accent, accent2, deep
    "nebula": ("#8B5CF6", "#EC4899", "#1E3A8A"),
    "dawn": ("#F97316", "#FBBF24", "#DB2777"),
    "forest": ("#10B981", "#84CC16", "#065F46"),
    "ocean": ("#0EA5E9", "#22D3EE", "#1E40AF"),
}
DEFAULT_WALLPAPER = {"kind": "aurora", "preset": "", "path": "", "blur": True, "dim": 50}


def blob_colors(wallpaper: dict) -> tuple[str, str, str]:
    if wallpaper.get("kind") == "preset" and wallpaper.get("preset") in PRESETS:
        return PRESETS[wallpaper["preset"]]
    return Theme.accent(), Theme.accent2(), Palette.deep


class AuroraBackground(QWidget):
    def __init__(self, parent: QWidget | None = None, animated: bool = True, wallpaper: dict | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)  # we paint every pixel
        self._t = random.uniform(0, 100)
        self._img = QImage(_W, _H, QImage.Format.Format_ARGB32_Premultiplied)
        self.wallpaper = {**DEFAULT_WALLPAPER, **(wallpaper or {})}
        self._picture: QImage | None = None
        self._scaled: tuple | None = None   # (size, image) cache of the cover-scaled picture
        # (x, y, radius, speed, phase, color-slot, alpha)
        self._blobs = [
            (0.18, 0.22, 0.75, 0.55, 0.0, 0, 0.42),
            (0.82, 0.30, 0.65, 0.42, 2.1, 1, 0.30),
            (0.55, 0.88, 0.80, 0.35, 4.2, 2, 0.55),
            (0.92, 0.95, 0.55, 0.60, 1.3, 0, 0.22),
        ]
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._animated = animated
        self.set_wallpaper(self.wallpaper)

    # ------------------------------------------------------------- settings
    def set_animated(self, enabled: bool) -> None:
        self._animated = enabled
        moving = enabled and self.wallpaper["kind"] in ("aurora", "preset")
        if moving:
            self._timer.start(66)  # ~15 fps is plenty for slow motion
        else:
            self._timer.stop()
        self._render()
        self.update()

    def set_wallpaper(self, wallpaper: dict) -> None:
        self.wallpaper = {**DEFAULT_WALLPAPER, **wallpaper}
        self._picture, self._scaled = None, None
        if self.wallpaper["kind"] == "image":
            img = QImage(self.wallpaper.get("path", ""))
            if img.isNull():
                self.wallpaper["kind"] = "aurora"  # the picture is gone: fall back quietly
            else:
                self._picture = img
        self.set_animated(self._animated)

    def refresh_colors(self) -> None:
        self._render()
        self.update()

    # ------------------------------------------------------------- drawing
    def _tick(self) -> None:
        window = self.window()
        if not self.isVisible() or window.isMinimized():
            return
        self._t += 0.035 if window.isActiveWindow() else 0.012
        self._render()
        self.update()

    def _render(self) -> None:
        self._img.fill(QColor(Palette.bg))
        if self.wallpaper["kind"] not in ("aurora", "preset"):
            return
        colors = blob_colors(self.wallpaper)
        light = Palette.light()
        p = QPainter(self._img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # adding light works on a dark base; on a light base it would wash out to white
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver if light
                             else QPainter.CompositionMode.CompositionMode_Plus)
        t = self._t
        for bx, by, rad, speed, phase, slot, alpha in self._blobs:
            alpha = alpha * (0.45 if light else 1.0)
            cx = _W * (bx + 0.16 * math.sin(t * speed + phase))
            cy = _H * (by + 0.14 * math.cos(t * speed * 0.8 + phase))
            r = _H * rad * (1 + 0.12 * math.sin(t * 0.5 + phase))
            grad = QRadialGradient(QPointF(cx, cy), r)
            grad.setColorAt(0.0, qcolor(colors[slot], alpha))
            grad.setColorAt(0.55, qcolor(colors[slot], alpha * 0.35))
            grad.setColorAt(1.0, qcolor(colors[slot], 0.0))
            p.fillRect(self._img.rect(), grad)
        p.end()

    def _picture_for(self, size) -> QImage:
        """The user's picture scaled to cover the window (cached; optional blur via a tiny copy)."""
        if self._scaled and self._scaled[0] == size:
            return self._scaled[1]
        pic = self._picture.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
        x, y = (pic.width() - size.width()) // 2, (pic.height() - size.height()) // 2
        pic = pic.copy(x, y, size.width(), size.height())
        if self.wallpaper.get("blur"):
            small = pic.scaled(max(1, size.width() // 18), max(1, size.height() // 18),
                               Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            pic = small.scaled(size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._scaled = (size, pic)
        return pic

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        kind = self.wallpaper["kind"]
        if kind == "image" and self._picture is not None:
            p.drawImage(0, 0, self._picture_for(self.size()))
        else:
            p.drawImage(self.rect(), self._img)
        # veil keeps text readable over the colors / the picture
        dim = max(0, min(90, int(self.wallpaper.get("dim", 50)))) / 100
        top, bottom = (0.35, 0.70) if kind != "image" else (0.25 + 0.6 * dim, 0.45 + 0.5 * dim)
        if Palette.light():
            top, bottom = top * 0.7, bottom * 0.8
        veil = QLinearGradient(0, 0, 0, self.height())
        veil.setColorAt(0.0, qcolor(Palette.bg, min(top, 0.95)))
        veil.setColorAt(1.0, qcolor(Palette.bg, min(bottom, 0.97)))
        p.fillRect(self.rect(), veil)


def paint_preview(p: QPainter, rect: QRectF, wallpaper: dict) -> None:
    """Small still picture of a wallpaper (Settings' picker)."""
    p.save()
    p.fillRect(rect, QColor(Palette.bg))
    kind = wallpaper.get("kind")
    if kind in ("aurora", "preset"):
        colors = blob_colors(wallpaper)
        light = Palette.light()
        for (bx, by, rad), slot, alpha in (((0.2, 0.25, 0.9), 0, 0.55), ((0.85, 0.3, 0.8), 1, 0.4),
                                           ((0.55, 0.95, 0.9), 2, 0.6)):
            alpha *= 0.5 if light else 1
            grad = QRadialGradient(QPointF(rect.left() + rect.width() * bx, rect.top() + rect.height() * by),
                                   rect.height() * rad)
            grad.setColorAt(0.0, qcolor(colors[slot], alpha))
            grad.setColorAt(1.0, qcolor(colors[slot], 0.0))
            p.fillRect(rect, grad)
    elif kind == "image":
        img = QImage(wallpaper.get("path", ""))
        if not img.isNull():
            img = img.scaled(rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                             Qt.TransformationMode.SmoothTransformation)
            src = QRectF((img.width() - rect.width()) / 2, (img.height() - rect.height()) / 2, rect.width(), rect.height())
            p.drawImage(rect, img, src)
    p.restore()
