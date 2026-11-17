"""
Launcher tiles (the "app icons" of the Study Hub desktop) + a flow layout.

Tiles are fully custom-painted (no graphics effects) so dozens of them can
animate at once cheaply:
  appear  -> pop in with a little overshoot (staggered)
  hover   -> lift up, glow border
  press   -> sink slightly, ripple spreads from the click point
"""
from __future__ import annotations

import hashlib

from PyQt6.QtCore import QFileInfo, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QFileIconProvider, QLayout, QStyle, QWidget

from app.ui.animations import OUT, OUT_BACK, start_delayed, value_animation
from app.ui.theme import Icons, Palette, Theme, icon_font, qcolor, ui_font, ink

TILE_SIZE = QSize(116, 120)
_PLATE = 56
_AVATAR_COLORS = ["#7C5CFF", "#22D3EE", "#F472B6", "#34D399", "#FBBF24", "#60A5FA", "#FB7185", "#A78BFA"]
_pixmap_cache: dict[tuple, QPixmap] = {}
_icon_provider: QFileIconProvider | None = None


def _avatar(text: str, seed: str, glyph: str | None = None, size: int = _PLATE) -> QPixmap:
    """Gradient squircle with a letter (websites) or a glyph (folders)."""
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.setDevicePixelRatio(scale)
    pm.fill(Qt.GlobalColor.transparent)
    color = _AVATAR_COLORS[int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(_AVATAR_COLORS)]
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(0, 0, size, size)
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.3, size * 0.3)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0, QColor(color).lighter(115))
    grad.setColorAt(1, QColor(color).darker(135))
    p.fillPath(path, grad)
    p.setPen(QColor("#FFFFFF"))
    if glyph:
        font = icon_font()
        font.setPixelSize(int(size * 0.44))
    else:
        font = ui_font(10, QFont.Weight.Bold)
        font.setPixelSize(int(size * 0.44))
    p.setFont(font)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph or text)
    p.end()
    return pm


def item_pixmap(item: dict) -> QPixmap:
    """Icon for a launcher item: real Windows icon for apps/files, avatar otherwise."""
    global _icon_provider
    key = (item["kind"], item["target"], item.get("title", ""), item.get("icon_path", ""))
    if key in _pixmap_cache:
        return _pixmap_cache[key]
    kind, target, title = item["kind"], item["target"], item.get("title") or "?"
    pm = QPixmap()
    if item.get("icon_path"):
        pm = QPixmap(item["icon_path"])
    if pm.isNull() and kind == "app" and target.lower().startswith("shell:"):  # Store apps
        from app.core.shell_icons import shell_pixmap
        pm = shell_pixmap(target, 48)
    if pm.isNull() and kind in ("app", "file"):
        _icon_provider = _icon_provider or QFileIconProvider()
        icon = _icon_provider.icon(QFileInfo(target))
        if not icon.isNull():
            pm = icon.pixmap(QSize(48, 48))
    if pm.isNull():
        if kind == "folder":
            pm = _avatar("", target, Icons.FOLDER)
        elif kind == "note":
            pm = _avatar("", item.get("color") or target, Icons.NOTES)
        else:
            letter = next((ch for ch in title if ch.isalnum()), "?").upper()
            pm = _avatar(letter, target)
    _pixmap_cache[key] = pm
    return pm


def clear_icon_cache() -> None:
    _pixmap_cache.clear()


def paint_item_icon(p: QPainter, plate: QRectF, item: dict, pixmap: QPixmap | None, hover: float) -> None:
    """Draws an item's icon inside `plate`. Program/file icons and website logos sit on a
    rounded plate (light for logos, like phone apps); generated avatars fill the plate."""
    if pixmap is None:
        return
    has_logo = item["kind"] == "url" and bool(item.get("icon_path"))
    if item["kind"] in ("app", "file") or has_logo:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor("#F4F6FB", 0.95) if has_logo else ink(0.06 + 0.04 * hover))
        p.drawRoundedRect(plate, plate.width() * 0.3, plate.width() * 0.3)
        pad = plate.width() * (0.2 if has_logo else 0.18)
        icon_rect = plate.adjusted(pad, pad, -pad, -pad)
    else:
        icon_rect = plate
    p.drawPixmap(icon_rect.toRect(), pixmap)


class LauncherTile(QWidget):
    activated = pyqtSignal(int)              # item id (-1 for the "add" tile)
    menu_requested = pyqtSignal(int, QPoint)  # item id, global position

    def __init__(self, item: dict | None, parent=None, add_label: str = ""):
        super().__init__(parent)
        self.item = item
        self.item_id = item["id"] if item else -1
        self.title = item["title"] if item else add_label
        self.pixmap = item_pixmap(item) if item else None
        self.setFixedSize(TILE_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if item:
            self.setToolTip(f"{item['title']}\n{item['target']}")
        self._hover = self._press = 0.0
        self._appear = 1.0
        self._ripple, self._ripple_at = 0.0, QPointF()
        self._hover_anim = value_animation(self, 0.0, 1.0, 180, lambda v: self._set("_hover", v), OUT)
        self._press_anim = value_animation(self, 0.0, 1.0, 120, lambda v: self._set("_press", v), OUT)
        self._appear_anim = value_animation(self, 0.0, 1.0, 520, lambda v: self._set("_appear", v), OUT_BACK)
        self._ripple_anim = value_animation(self, 0.0, 1.0, 520, lambda v: self._set("_ripple", v), OUT)

    def _set(self, name: str, value) -> None:
        setattr(self, name, float(value))
        self.update()

    @staticmethod
    def _run(anim, start, end):
        anim.stop()
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.start()

    def appear(self, delay: int = 0) -> None:
        self._appear = 0.0
        self.update()
        start_delayed(self, self._appear_anim, delay)

    # ----------------------------------------------------------------- events
    def enterEvent(self, e):
        self._run(self._hover_anim, self._hover, 1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._run(self._hover_anim, self._hover, 0.0)
        self._run(self._press_anim, self._press, 0.0)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._run(self._press_anim, self._press, 1.0)
            self._ripple_at = e.position()
            self._run(self._ripple_anim, 0.0, 1.0)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._run(self._press_anim, self._press, 0.0)
        if self.rect().contains(e.position().toPoint()):
            QTimer.singleShot(110, lambda: self.activated.emit(self.item_id))  # let the ripple show

    def contextMenuEvent(self, e):
        if self.item_id != -1:
            self.menu_requested.emit(self.item_id, e.globalPos())
            e.accept()
        else:
            e.ignore()

    # --------------------------------------------------------------- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        a = self._appear
        p.setOpacity(max(0.0, min(1.0, a * 1.3)))
        center = QPointF(self.width() / 2, self.height() / 2)
        scale = (0.82 + 0.18 * a) * (1 - 0.04 * self._press)
        p.translate(center)
        p.scale(scale, scale)
        p.translate(-center + QPointF(0, -4 * self._hover + 2 * self._press))

        card = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        path = QPainterPath()
        path.addRoundedRect(card, 18, 18)
        is_add = self.item is None

        # soft glow halo on hover (a few expanding strokes = cheap fake blur)
        if self._hover > 0.01:
            for i in range(1, 5):
                glow = QPainterPath()
                glow.addRoundedRect(card.adjusted(-i, -i, i, i), 18 + i, 18 + i)
                p.setPen(QPen(qcolor(Theme.accent(), 0.10 * self._hover / i), 2))
                p.drawPath(glow)

        p.fillPath(path, ink(0.035 + 0.05 * self._hover))
        if is_add:
            pen = QPen(ink(0.18 + 0.3 * self._hover), 1.4, Qt.PenStyle.DashLine)
        else:
            pen = QPen(qcolor(Theme.accent(), 0.65 * self._hover) if self._hover > 0.01
                       else ink(0.06), 1.2)
        p.setPen(pen)
        p.drawPath(path)

        # ripple
        if 0 < self._ripple < 1:
            p.save()
            p.setClipPath(path)
            radius = self._ripple * card.width() * 1.2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qcolor(Theme.accent(), 0.28 * (1 - self._ripple)))
            p.drawEllipse(self._ripple_at, radius, radius)
            p.restore()

        plate = QRectF(card.center().x() - _PLATE / 2, card.top() + 12, _PLATE, _PLATE)
        if is_add:
            p.setPen(qcolor(Theme.accent() if self._hover > 0.5 else Palette.text_dim, 1.0))
            p.setFont(icon_font(18))
            p.drawText(plate, Qt.AlignmentFlag.AlignCenter, Icons.ADD)
        else:
            paint_item_icon(p, plate, self.item, self.pixmap, self._hover)

        p.setPen(QColor(Palette.text if not is_add else Palette.text_dim))
        p.setFont(ui_font(9.2, QFont.Weight.Medium))
        text_rect = QRectF(card.left() + 6, plate.bottom() + 6, card.width() - 12, 22)
        text = p.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideRight, int(text_rect.width()))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)


class FlowLayout(QLayout):
    """Wraps children onto new lines like text (mirrored in right-to-left mode)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_height = area.x(), area.y(), 0
        parent = self.parentWidget()
        direction = parent.layoutDirection() if parent else Qt.LayoutDirection.LeftToRight
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > area.right() + 1 and line_height > 0:
                x, y = area.x(), y + line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test_only:
                geo = QStyle.visualRect(direction, area, QRect(QPoint(x, y), hint))
                item.setGeometry(geo)
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()
