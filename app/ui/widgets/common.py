"""Small reusable building blocks: labels, cards, buttons, switches, menus, toasts."""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QKeySequence, QPainter, QPainterPath
from PyQt6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
                             QVBoxLayout, QWidget)

from app.ui.animations import IN_OUT, OUT, OUT_BACK, value_animation
from app.ui.theme import Icons, Palette, Theme, glyph_icon, icon_font, lerp_color, qcolor, ui_font, ink


# -------------------------------------------------------------------- text
def label(text: str = "", pt: float = 10.0, weight: QFont.Weight = QFont.Weight.Normal,
          role: str = "", wrap: bool = False, parent: QWidget | None = None) -> QLabel:
    """Creates a QLabel with the app font. role: '', 'Dim', 'Faint', 'Accent', 'Badge'."""
    lbl = QLabel(text, parent)
    lbl.setFont(ui_font(pt, weight))
    if role:
        lbl.setObjectName(role)
    lbl.setWordWrap(wrap)
    return lbl


class GlyphLabel(QLabel):
    """A single icon glyph rendered with the system icon font."""

    def __init__(self, glyph: str, pt: float = 13, color: str | None = None, parent=None):
        super().__init__(glyph, parent)
        self.setFont(icon_font(pt))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if color:
            self.setStyleSheet(f"color: {color};")


class GlyphPlate(QWidget):
    """Rounded tinted square with a glyph — used by stat cards and lists."""

    def __init__(self, glyph: str, color: str, size: int = 44, parent=None):
        super().__init__(parent)
        self.glyph, self.color = glyph, color
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(self.color, 0.16))
        p.drawRoundedRect(r, r.width() * 0.3, r.width() * 0.3)
        font = icon_font()
        font.setPixelSize(int(r.width() * 0.42))
        p.setFont(font)
        p.setPen(QColor(self.color))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.glyph)


# ------------------------------------------------------------------- cards
class Card(QFrame):
    """Glassy rounded container with an optional header (glyph + title)."""

    def __init__(self, title: str | None = None, glyph: str | None = None,
                 parent: QWidget | None = None, padding: int = 20):
        super().__init__(parent)
        self.setObjectName("Card")
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(padding, padding, padding, padding)
        self.root.setSpacing(14)
        self.header: QHBoxLayout | None = None
        if title is not None:
            self.header = QHBoxLayout()
            self.header.setSpacing(10)
            if glyph:
                self.header.addWidget(GlyphLabel(glyph, 12.5, Theme.accent()))
            self.title_label = label(title, 11.5, QFont.Weight.DemiBold)
            self.header.addWidget(self.title_label)
            self.header.addStretch(1)
            self.root.addLayout(self.header)

    def add_header_widget(self, widget: QWidget) -> None:
        if self.header is not None:
            self.header.addWidget(widget)


class Divider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Divider")


class EmptyState(QWidget):
    """Friendly placeholder shown when a list has nothing to display."""

    def __init__(self, glyph: str, text: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 14, 0, 14)
        lay.setSpacing(8)
        icon = GlyphLabel(glyph, 20, Palette.text_faint)
        lay.addWidget(icon)
        self.text_label = label(text, 9.5, role="Faint", wrap=True)
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.text_label)


# ----------------------------------------------------------------- buttons
class IconButton(QPushButton):
    def __init__(self, glyph: str, tooltip: str = "", size: int = 36, pt: float = 11.5, parent=None):
        super().__init__(glyph, parent)
        self.setObjectName("IconButton")
        self.setFont(icon_font(pt))
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)


def primary_button(text: str, glyph: str | None = None, parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("Primary")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFont(ui_font(10, QFont.Weight.DemiBold))
    if glyph:
        btn.setIcon(glyph_icon(glyph, "#FFFFFF", 16))
    return btn


def button(text: str, glyph: str | None = None, role: str = "", parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    if role:
        btn.setObjectName(role)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFont(ui_font(10))
    if glyph:
        btn.setIcon(glyph_icon(glyph, Palette.danger if role == "Danger" else Palette.text_dim, 16))
    return btn


class ToggleSwitch(QAbstractButton):
    """iOS-style animated switch."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 26)
        self._pos = 1.0 if checked else 0.0
        self._anim = value_animation(self, 0.0, 1.0, 200, self._set_pos, OUT)
        self.toggled.connect(self._animate)

    def _set_pos(self, value):
        self._pos = float(value)
        self.update()

    def _animate(self, checked: bool):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def set_checked_silently(self, checked: bool):
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)
        self._set_pos(1.0 if checked else 0.0)

    def sizeHint(self):
        return QSize(46, 26)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        track = lerp_color(ink(0.12), QColor(Theme.accent()), self._pos)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - d - 6) * self._pos
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class CountUpLabel(QLabel):
    """Number that counts up to its new value (stat cards)."""

    def __init__(self, pt: float = 22, parent=None):
        super().__init__("0", parent)
        self.setFont(ui_font(pt, QFont.Weight.DemiBold))
        self._value = 0
        self._suffix = ""
        self._anim = value_animation(self, 0, 0, 900, self._render, OUT)

    def _render(self, value):
        self.setText(f"{int(value)}{self._suffix}")

    def set_value(self, value: int, suffix: str = ""):
        self._suffix = suffix
        self._anim.stop()
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(int(value))
        self._value = int(value)
        self._anim.start()


# ------------------------------------------------------------------- menus
def make_menu(parent: QWidget | None = None, title: str = "") -> QMenu:
    """QMenu with real rounded corners (translucent frameless popup)."""
    menu = QMenu(title, parent)
    menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint
                        | Qt.WindowType.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    menu.setFont(ui_font(10))
    return menu


def add_action(menu: QMenu, glyph: str | None, text: str, slot=None, shortcut: str | None = None,
               danger: bool = False) -> QAction:
    color = Palette.danger if danger else Palette.text_dim
    action = menu.addAction(glyph_icon(glyph, color, 18) if glyph else glyph_icon(" ", color), text)
    if slot is not None:
        action.triggered.connect(lambda _checked=False, s=slot: s())
    if shortcut:
        action.setShortcut(QKeySequence(shortcut))
        action.setShortcutVisibleInContextMenu(True)
    return action


def add_submenu(menu: QMenu, glyph: str | None, text: str) -> QMenu:
    sub = make_menu(menu, text)
    if glyph:
        sub.setIcon(glyph_icon(glyph, Palette.text_dim, 18))
    menu.addMenu(sub)
    return sub


# ---------------------------------------------------------------- overlays
class Overlay(QWidget):
    """Child widget that always covers its parent (used by palette, splash)."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())
        self.hide()

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
            self.on_resized()
        return False

    def on_resized(self):
        pass


class Toast(QWidget):
    """Notification pill that springs up from the bottom and fades away."""

    KIND_STYLE = {
        "info": (Icons.INFO, Palette.info),
        "success": (Icons.ACCEPT, Palette.success),
        "warning": (Icons.WARNING, Palette.warning),
        "error": (Icons.WARNING, Palette.danger),
    }

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.host = host
        host.installEventFilter(self)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._text, self._kind, self._t = "", "info", 0.0
        self._anim = value_animation(self, 0.0, 1.0, 420, self._set_t, OUT_BACK)
        self._hide_timer = QTimer(self, singleShot=True, timeout=self._dismiss)
        self.hide()

    def show_message(self, text: str, kind: str = "info", duration: int = 2800) -> None:
        self._text, self._kind = text, kind
        self.setFont(ui_font(10, QFont.Weight.Medium))
        width = min(self.host.width() - 40, self.fontMetrics().horizontalAdvance(text) + 78)
        self.resize(max(220, width), 50)
        self._place()
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setEasingCurve(OUT_BACK)
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._hide_timer.start(duration)

    def _dismiss(self):
        self._anim.stop()
        self._anim.setEasingCurve(IN_OUT)
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _set_t(self, value):
        self._t = float(value)
        if self._t <= 0.001 and self._anim.endValue() == 0.0:
            self.hide()
        self._place()
        self.update()

    def _place(self):
        x = (self.host.width() - self.width()) // 2
        # above the shell's taskbar when there is one at the bottom
        bar = getattr(self.host.window(), "taskbar", None)
        lift = bar.height() if bar is not None and bar.isVisible() and bar.conf.get("position") == "bottom"             and not bar.conf.get("autohide") else 0
        y_end = self.host.height() - self.height() - 34 - lift
        self.move(x, int(y_end + (1 - self._t) * 40))

    def eventFilter(self, obj, event):
        if obj is self.host and event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False

    def mousePressEvent(self, _):
        self._dismiss()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(max(0.0, min(1.0, self._t)))
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        glyph, color = self.KIND_STYLE.get(self._kind, self.KIND_STYLE["info"])
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.fillPath(path, QColor(Palette.surface_solid))
        p.fillPath(path, qcolor(color, 0.08))
        p.setPen(qcolor(color, 0.45))
        p.drawPath(path)
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        icon_rect = QRectF(r.right() - 46, r.top(), 40, r.height()) if rtl else QRectF(r.left() + 8, r.top(), 40, r.height())
        p.setPen(QColor(color))
        p.setFont(icon_font(12))
        p.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, glyph)
        p.setPen(QColor(Palette.text))
        p.setFont(self.font())
        text_rect = r.adjusted(18, 0, -48, 0) if rtl else r.adjusted(48, 0, -18, 0)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
                   p.fontMetrics().elidedText(self._text, Qt.TextElideMode.ElideRight, int(text_rect.width())))


class DotPainter:
    """Helper to paint small colored dots (priorities, subjects)."""

    @staticmethod
    def paint(p: QPainter, center: QPointF, color: str, radius: float = 4.0):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(center, radius, radius)
