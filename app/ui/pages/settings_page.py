"""
Settings: every setting of every Study Hub app in one place, in sections (list + search on the side).

Sections that change how things look (Appearance, Taskbar) show a live preview next to the controls: you try
things, the preview follows, and nothing changes for real until Apply (Reset goes back to what is in use).
Everything else applies at once.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QAbstractButton, QAbstractItemView, QBoxLayout, QButtonGroup, QComboBox, QFrame,
                             QHBoxLayout, QKeySequenceEdit, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                             QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from app.config import APP_NAME, DATA_DIR, DB_PATH, DEFAULT_HOTKEYS, VERSION
from app.core import i18n, shell_integration
from app.core.i18n import day_name, is_rtl, language, tr
from app.ui import apps as app_registry
from app.ui.pages.base import Page
from app.ui.theme import ACCENTS, Icons, Palette, Theme, glyph_icon, icon_font, ink, qcolor, ui_font
from app.ui.widgets.common import Card, IconButton, ToggleSwitch, button, label, primary_button


class Swatch(QAbstractButton):
    """Round accent-color picker with a ring when selected."""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self.setCheckable(True)
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(name.capitalize())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        c1, c2 = ACCENTS[self.name]
        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0, QColor(c1))
        grad.setColorAt(1, QColor(c2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(r)
        if self.isChecked():
            p.setPen(QPen(QColor(Palette.text), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(self.rect()).adjusted(2, 2, -2, -2))


class WallpaperSwatch(QAbstractButton):
    """Small picture of a wallpaper, with a ring when it is the chosen one."""

    def __init__(self, key: str, wallpaper: dict, tip: str, parent=None):
        super().__init__(parent)
        self.key, self.wallpaper = key, wallpaper
        self.setCheckable(True)
        self.setFixedSize(84, 54)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)

    def paintEvent(self, _):
        from app.ui.widgets.background import paint_preview
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        p.setClipPath(path)
        paint_preview(p, r, self.wallpaper)
        if self.wallpaper.get("kind") == "image" and not self.wallpaper.get("path"):
            p.setPen(QColor(Palette.text_dim))
            p.setFont(ui_font(16))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "+")
        p.setClipping(False)
        p.setPen(QPen(QColor(Theme.accent()), 2.5) if self.isChecked() else QPen(QColor(Palette.text_faint), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2) if self.isChecked() else r, 12, 10)


def setting_row(title: str, description: str, widget: QWidget) -> QWidget:
    row = QWidget()
    row.setProperty("search_text", f"{title} {description}".lower())
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 4, 0, 4)
    lay.setSpacing(16)
    col = QVBoxLayout()
    col.setSpacing(2)
    col.addWidget(label(title, 10.5, QFont.Weight.Medium))
    if description:
        col.addWidget(label(description, 9, role="Dim", wrap=True))
    lay.addLayout(col, 1)
    lay.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


def combo(items: list[tuple[str, object]], current, on_change) -> QComboBox:
    box = QComboBox()
    for text, data in items:
        box.addItem(text, data)
    box.setCurrentIndex(max(0, box.findData(current)))
    box.currentIndexChanged.connect(lambda _: on_change(box.currentData()))
    box.setMinimumWidth(170)
    return box


# ===================================================================== live preview
class ShellPreview(QWidget):
    """A small picture of the shell with the settings being tried (theme, accent, wallpaper, taskbar)."""

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.look: dict = {}
        self.bar: dict = {}
        self.setMinimumSize(300, 200)

    def set_state(self, look: dict | None = None, bar: dict | None = None) -> None:
        if look is not None:
            self.look = dict(look)
        if bar is not None:
            self.bar = dict(bar)
        self.update()

    def sizeHint(self):
        return QSize(340, 230)

    def heightForWidth(self, w: int) -> int:
        return int(w * 0.64)

    def paintEvent(self, _):
        from app.ui.widgets.background import paint_preview
        from app.ui.widgets.taskbar import taskbar_settings
        saved_mode, saved_accent = Palette.mode, Theme.accent_name
        look = {"mode": self.ctl.db.get_setting("theme_mode", "dark"), "accent": Theme.accent_name,
                "wallpaper": self.ctl.wallpaper(), **self.look}
        bar = {**taskbar_settings(self.ctl), **self.bar}
        try:  # paint with the colours being tried, then put the real ones back
            Palette.apply(look["mode"])
            Theme.set_accent(look["accent"])
            self._paint(look, bar, paint_preview)
        finally:
            Palette.apply(saved_mode)
            Theme.set_accent(saved_accent)

    def _paint(self, look, bar, paint_preview):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width() - 2
        h = min(self.height() - 2, int(w * 0.62))
        screen = QRectF(1, 1, w, h)
        clip = QPainterPath()
        clip.addRoundedRect(screen, 12, 12)
        p.setClipPath(clip)
        paint_preview(p, screen, look["wallpaper"])
        if look["wallpaper"].get("kind") == "image":  # the dim slider
            p.fillRect(screen, QColor(0, 0, 0, int(255 * int(look["wallpaper"].get("dim", 50)) / 100 * 0.6)))
        scale = w / 1300
        bar_h = {"small": 44, "medium": 54, "large": 64}.get(bar["size"], 54) * scale * 1.35
        top = bar["position"] == "top"
        hidden = bar.get("autohide")
        # desktop icons
        icon = 30 * scale * 1.3
        y0 = screen.top() + (bar_h if top and not hidden else 0) + 14 * scale * 1.3
        for i, info in enumerate(app_registry.BUILTIN[:6]):
            x = screen.right() - 18 * scale - (i % 2 + 1) * icon * 1.6 if is_rtl() else screen.left() + 18 * scale + (i % 2) * icon * 1.6
            y = y0 + (i // 2) * icon * 1.7
            p.drawPixmap(QRectF(x, y, icon, icon).toRect(), app_registry.app_tile_pixmap(info.color, info.glyph, int(icon * 2)))
        # an app window
        win = QRectF(screen.left() + w * 0.3, screen.top() + h * 0.16, w * 0.5, h * 0.5)
        p.setPen(QPen(ink(0.14), 1))
        p.setBrush(qcolor(Palette.surface_solid, 0.96))
        p.drawRoundedRect(win, 8, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(Theme.accent()))
        p.drawRoundedRect(QRectF(win.left() + 12, win.top() + 14, win.width() * 0.32, 7), 3.5, 3.5)
        p.setBrush(ink(0.12))
        for i in range(3):
            p.drawRoundedRect(QRectF(win.left() + 12, win.top() + 32 + i * 13, win.width() * (0.75 - i * 0.15), 6), 3, 3)
        grad = QLinearGradient(win.bottomLeft(), win.bottomRight())
        grad.setColorAt(0, QColor(Theme.accent()))
        grad.setColorAt(1, QColor(Theme.accent2()))
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(win.right() - 64, win.bottom() - 26, 52, 16), 8, 8)
        # taskbar
        if hidden:
            p.setPen(QPen(QColor(Theme.accent()), 2))
            yy = screen.top() + 2 if top else screen.bottom() - 2
            p.drawLine(int(screen.left() + w * 0.35), int(yy), int(screen.left() + w * 0.65), int(yy))
            p.setPen(QColor(Palette.text_dim))
            p.setFont(ui_font(8))
            p.drawText(QRectF(screen.left(), (screen.top() + 6) if top else (screen.bottom() - 22), w, 16),
                       Qt.AlignmentFlag.AlignCenter, tr("tb_autohide_preview"))
        else:
            rect = QRectF(screen.left(), screen.top() if top else screen.bottom() - bar_h, w, bar_h)
            base = QColor(Palette.chrome)
            base.setAlphaF(max(0.15, min(1.0, float(bar.get("opacity", 0.82)))))
            p.setPen(Qt.PenStyle.NoPen)
            p.fillRect(rect, base)
            btn = bar_h * 0.78
            keys = list(bar.get("pinned") or [])
            infos = [app_registry.find(self.ctl, k) for k in keys]
            infos = [i for i in infos if i is not None][:10]
            extras = 1 + (1 if bar.get("show_search", True) else 0) + (1 if bar.get("show_desks", True) else 0)
            labels = bar.get("labels")
            widths = [btn] * extras + [btn + (btn * 1.6 if labels and n in (1, 2) else 0) for n in range(len(infos))]
            total = sum(widths) + 8
            room = rect.width() - 90  # leave the clock its place
            if total > room:  # many pinned apps on a small preview: shrink them to fit
                k = room / total
                widths = [x * k for x in widths]
                btn *= k
                total = room
            if bar.get("align", "center") == "center":
                x = rect.left() + (rect.width() - total) / 2
            else:
                x = rect.right() - total - 8 if is_rtl() else rect.left() + 8
            cy = rect.center().y()
            from app.ui.theme import app_icon_pixmap
            ic = btn * 0.62
            order = list(range(extras + len(infos)))
            if is_rtl():
                order.reverse()
            xs = []
            cx = x
            for i in order:
                xs.append((i, cx))
                cx += widths[i]
            for i, bx in xs:
                if i == 0:
                    p.drawPixmap(QRectF(bx + (btn - ic) / 2, cy - ic / 2, ic, ic).toRect(), app_icon_pixmap(int(ic * 2)))
                elif i < extras:
                    p.setPen(QColor(Palette.text_dim))
                    p.setFont(icon_font(max(5.0, ic * 0.42)))
                    glyph = Icons.SEARCH if (i == 1 and bar.get("show_search", True)) else Icons.VIEW_GRID
                    p.drawText(QRectF(bx, cy - btn / 2, btn, btn), Qt.AlignmentFlag.AlignCenter, glyph)
                else:
                    n = i - extras
                    info = infos[n]
                    if n == 1:  # "in front"
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(ink(0.1))
                        p.drawRoundedRect(QRectF(bx + 1, cy - btn / 2 + 1, widths[i] - 2, btn - 2), 5, 5)
                    p.drawPixmap(QRectF(bx + (btn - ic) / 2, cy - ic / 2, ic, ic).toRect(),
                                 app_registry.app_tile_pixmap(info.color, info.glyph, int(ic * 2)))
                    if labels and n in (1, 2):
                        p.setPen(QColor(Palette.text))
                        p.setFont(ui_font(max(5.0, ic * 0.3)))
                        p.drawText(QRectF(bx + btn - 2, cy - btn / 2, btn * 1.5, btn), Qt.AlignmentFlag.AlignVCenter,
                                   p.fontMetrics().elidedText(info.title, Qt.TextElideMode.ElideRight, int(btn * 1.5)))
                    if n in (1, 2):
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(QColor(Theme.accent()) if n == 1 else ink(0.45))
                        lw = btn * (0.32 if n == 1 else 0.12)
                        p.drawRoundedRect(QRectF(bx + btn / 2 - lw / 2, cy + btn / 2 - 3, lw, 2.2), 1, 1)
            p.setPen(QColor(Palette.text))
            p.setFont(ui_font(max(5.0, bar_h * 0.2), QFont.Weight.DemiBold))
            clock_rect = QRectF(rect.left() + 8, rect.top(), 60, rect.height()) if is_rtl() else QRectF(rect.right() - 68, rect.top(), 60, rect.height())
            p.drawText(clock_rect, Qt.AlignmentFlag.AlignCenter, "10:24")
            from app.ui.widgets.system_tray import draw_icon, tray_conf
            keys = [i["key"] for i in tray_conf(bar) if i.get("show")]
            s = bar_h * 0.4
            gap = s * 0.35 if bar.get("tray_group") else s * 0.9
            st = getattr(self.ctl, "system").state if getattr(self.ctl, "system", None) else {}
            x = clock_rect.right() + 6 if is_rtl() else clock_rect.left() - 6 - len(keys) * (s + gap)
            if keys and bar.get("tray_group"):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(ink(0.08))
                p.drawRoundedRect(QRectF(x - 3, rect.center().y() - s * 0.9, len(keys) * (s + gap) + 3, s * 1.8), 4, 4)
            for k in keys:
                draw_icon(p, k, QRectF(x, rect.center().y() - s / 2, s, s), st, QColor(Palette.text))
                x += s + gap
        p.setClipping(False)
        p.setPen(QPen(ink(0.18), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(screen, 12, 12)


class PreviewPanel(Card):
    """The preview + Apply / Reset (shown next to the section's settings)."""

    def __init__(self, ctl, on_apply, on_reset):
        super().__init__(tr("preview_title"), Icons.PREVIEW)
        self.preview = ShellPreview(ctl)
        self.root.addWidget(self.preview, 1)
        self.state = label(tr("preview_same"), 9, role="Dim", wrap=True)
        self.root.addWidget(self.state)
        row = QHBoxLayout()
        row.addStretch(1)
        self.reset = button(tr("preview_reset"), Icons.UNDO, role="Ghost")
        self.reset.clicked.connect(lambda _checked=False: on_reset())
        row.addWidget(self.reset)
        self.apply = primary_button(tr("apply"), Icons.ACCEPT)
        self.apply.clicked.connect(lambda _checked=False: on_apply())
        row.addWidget(self.apply)
        self.root.addLayout(row)
        self.set_dirty(False)

    def set_dirty(self, dirty: bool) -> None:
        self.apply.setEnabled(dirty)
        self.reset.setEnabled(dirty)
        self.state.setText(tr("preview_changed") if dirty else tr("preview_same"))
        self.state.setStyleSheet(f"color: {Theme.accent()};" if dirty else "")


# ===================================================================== the page
SECTIONS = [
    ("general", Icons.GLOBE, "set_sec_general"),
    ("appearance", Icons.COLOR, "set_sec_appearance"),
    ("taskbar", Icons.VIEW_LIST, "set_sec_taskbar"),
    ("desktop", Icons.DESKTOP, "set_sec_desktop"),
    ("timer", Icons.TIMER, "set_sec_timer"),
    ("pdf", Icons.DOCUMENT, "set_sec_pdf"),
    ("phone", Icons.PHONE, "set_sec_phone"),
    ("study", Icons.STUDY, "set_sec_study"),
    ("tools", Icons.LIGHTNING, "set_sec_tools"),
    ("keyboard", Icons.KEYBOARD, "set_sec_keyboard"),
    ("windows", Icons.APPS, "set_sec_windows"),
    ("plugins", Icons.APPS, "set_sec_plugins"),
    ("updates", Icons.DOWNLOAD, "set_sec_updates"),
    ("data", Icons.SAVE, "set_sec_data"),
]
WITH_PREVIEW = {"appearance", "taskbar"}


class SettingsPage(Page):
    def __init__(self, ctl, main_window):
        super().__init__(ctl, main_window)
        root = QHBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(18)

        # ---- section list (with search)
        nav = QVBoxLayout()
        nav.setSpacing(10)
        nav.addWidget(label(tr("nav_settings"), 20, QFont.Weight.DemiBold))
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("settings_search"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        nav.addWidget(self.search)
        self.nav = QListWidget()
        self.nav.setObjectName("SettingsNav")
        self.nav.setFixedWidth(220)
        self.nav.setIconSize(QSize(20, 20))
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.currentRowChanged.connect(self._nav_changed)
        nav.addWidget(self.nav, 1)
        nav.addWidget(label(f"{APP_NAME} {VERSION}", 8.5, role="Faint"))
        root.addLayout(nav)

        # ---- content (+ preview)
        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        self.keys: list[str] = []
        self._look = self._current_look()
        self._bar = self._current_bar()
        for key, glyph, title_key in SECTIONS:
            page = self._section(key, glyph, title_key)
            if page is None:
                continue
            self.pages[key] = page
            self.keys.append(key)
            self.stack.addWidget(page)
            item = QListWidgetItem(glyph_icon(glyph, Palette.text_dim, 20), tr(title_key))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(0, 42))
            self.nav.addItem(item)
        root.addWidget(self.stack, 1)
        self.listen(ctl.data_changed, self._on_data)
        start = ctl.db.get_setting("settings_section", "general")
        self.show_section(start if start in self.pages else "general")

    # ------------------------------------------------------------ navigation
    def show_section(self, key: str) -> None:
        if key not in self.pages:
            return
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.ItemDataRole.UserRole) == key:
                self.nav.setCurrentRow(i)
                break
        self.stack.setCurrentWidget(self.pages[key])

    def _nav_changed(self, row: int) -> None:
        item = self.nav.item(row)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        self.stack.setCurrentWidget(self.pages[key])
        self.ctl.db.set_setting("settings_section", key)

    def _filter(self, text: str) -> None:
        """Shows the sections that have a matching setting (title or explanation, both languages)."""
        q = text.strip().lower()
        first = None
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            key = item.data(Qt.ItemDataRole.UserRole)
            page = self.pages[key]
            hay = (item.text() + " " + " ".join(w.property("search_text") or "" for w in page.findChildren(QWidget)
                                                if w.property("search_text"))).lower()
            hit = not q or q in hay
            item.setHidden(not hit)
            if hit and first is None:
                first = i
        if first is not None and (self.nav.currentItem() is None or self.nav.currentItem().isHidden()):
            self.nav.setCurrentRow(first)

    # ------------------------------------------------------------- sections
    def _section(self, key: str, glyph: str, title_key: str) -> QWidget | None:
        build = getattr(self, f"_sec_{key}", None)
        if build is None:
            return None
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(16)
        head = QHBoxLayout()
        head.addWidget(label(tr(title_key), 17, QFont.Weight.DemiBold))
        head.addStretch(1)
        col.addLayout(head)
        desc = tr(f"{title_key}_desc")
        if desc != f"{title_key}_desc":
            col.addWidget(label(desc, 9.5, role="Dim", wrap=True))
        for card in build():
            col.addWidget(card)
        col.addStretch(1)
        scroll = self.scrollable(body)
        if key not in WITH_PREVIEW:
            return scroll
        holder = QWidget()
        lay = QBoxLayout(QBoxLayout.Direction.LeftToRight, holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        lay.addWidget(scroll, 3)
        panel = PreviewPanel(self.ctl, lambda k=key: self._apply(k), lambda k=key: self._reset(k))  # (called with no args)
        panel.setMinimumWidth(320)
        panel.setMaximumWidth(460)
        lay.addWidget(panel, 2, Qt.AlignmentFlag.AlignTop)
        setattr(self, f"_panel_{key}", panel)
        holder._lay = lay
        holder._panel = panel
        self._update_preview(key)
        return holder

    def resizeEvent(self, e):
        super().resizeEvent(e)
        narrow = self.width() < 1180  # small window: the preview goes above the settings
        for key in WITH_PREVIEW:
            holder = self.pages.get(key)
            if holder is not None:
                holder._lay.setDirection(QBoxLayout.Direction.BottomToTop if narrow else QBoxLayout.Direction.LeftToRight)
                holder._panel.setMaximumWidth(16777215 if narrow else 460)
                holder._panel.preview.setMinimumHeight(190 if narrow else 220)

    # -------------------------------------------------- staged (preview) state
    def _current_look(self) -> dict:
        return {"accent": Theme.accent_name, "mode": self.ctl.db.get_setting("theme_mode", "dark"),
                "wallpaper": dict(self.ctl.wallpaper()),
                "animated": bool(self.ctl.db.get_setting("animated_background", True))}

    def _current_bar(self) -> dict:
        conf = dict(self.ctl.taskbar_conf(global_only=True))  # the general list (desktops can have their own)
        conf.pop("desk_pinned", None)
        return conf

    def _update_preview(self, key: str) -> None:
        panel = getattr(self, f"_panel_{key}", None)
        if panel is None:
            return
        panel.preview.set_state(self._look, self._bar)
        current = self._current_look() if key == "appearance" else self._current_bar()
        draft = self._look if key == "appearance" else self._bar
        panel.set_dirty(current != draft)
        other = "taskbar" if key == "appearance" else "appearance"
        other_panel = getattr(self, f"_panel_{other}", None)
        if other_panel is not None:
            other_panel.preview.set_state(self._look, self._bar)

    def _apply(self, key: str) -> None:
        ctl = self.ctl
        if key == "taskbar":
            ctl.set_taskbar(**self._bar)
            self._update_preview("taskbar")
            ctl.notify(tr("preview_applied"), "success")
            return
        look, now = dict(self._look), self._current_look()
        if look["wallpaper"] != now["wallpaper"]:
            ctl.set_wallpaper(**look["wallpaper"])
        if look["animated"] != now["animated"]:
            ctl.set_animated_background(look["animated"])
        if look["accent"] != now["accent"]:
            ctl.set_accent(look["accent"])
        if look["mode"] != now["mode"]:
            QTimer.singleShot(0, lambda m=look["mode"]: ctl.set_theme_mode(m))  # rebuilds this window too
        else:
            self._update_preview("appearance")
            ctl.notify(tr("preview_applied"), "success")

    def _reset(self, key: str) -> None:
        if key == "taskbar":
            self._bar = self._current_bar()
            self._sync_bar_controls()
        else:
            self._look = self._current_look()
            self._sync_look_controls()
        self._update_preview(key)

    def _on_data(self, what: str) -> None:
        if what == "wallpaper" and hasattr(self, "wall_row"):
            self._fill_wallpapers()
        if what == "phone" and hasattr(self, "_phone_toggle"):
            self._phone_toggle.set_checked_silently(self.ctl.phone.running)

    # ============================================================ General
    def _sec_general(self) -> list:
        card = Card(tr("settings_language"), Icons.GLOBE)
        row = QHBoxLayout()
        row.setSpacing(10)
        group = QButtonGroup(card)
        for code, name in (("ar", "العربية"), ("en", "English")):
            chip = QPushButton(name)
            chip.setObjectName("Chip")
            chip.setCheckable(True)
            chip.setChecked(language() == code)
            chip.setFont(ui_font(10.5))
            chip.setMinimumWidth(110)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _=False, c=code: self.ctl.set_language(c))
            group.addButton(chip)
            row.addWidget(chip)
        row.addStretch(1)
        card.root.addLayout(row)

        start = Card(tr("set_start_title"), Icons.POWER)
        full = ToggleSwitch(self.main.fullscreen)
        full.toggled.connect(lambda on: self.main.set_fullscreen(on))
        start.root.addWidget(setting_row(tr("start_fullscreen"), tr("start_fullscreen_desc"), full))
        autostart = ToggleSwitch(shell_integration.is_autostart_enabled())
        autostart.toggled.connect(lambda on: self.ctl.set_autostart(on) or autostart.set_checked_silently(not on))
        start.root.addWidget(setting_row(tr("autostart"), tr("autostart_desc"), autostart))
        show = ToggleSwitch(self.ctl.autostart_shows_window())
        show.toggled.connect(self.ctl.set_autostart_show_window)
        start.root.addWidget(setting_row(tr("autostart_show"), tr("autostart_show_desc"), show))
        clock12 = ToggleSwitch(bool(self.ctl.db.get_setting("clock_12h", True)))
        clock12.toggled.connect(self.ctl.set_clock_format)
        start.root.addWidget(setting_row(tr("clock_12h"), tr("clock_12h_desc"), clock12))
        return [card, start]

    # ============================================================ Appearance (preview)
    def _sec_appearance(self) -> list:
        card = Card(tr("settings_appearance"), Icons.COLOR)
        swatches = QWidget()
        lay = QHBoxLayout(swatches)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.accent_group = QButtonGroup(card)
        self.accent_swatches = {}
        for name in ACCENTS:
            sw = Swatch(name)
            sw.clicked.connect(lambda _=False, n=name: self._try_look(accent=n))
            self.accent_group.addButton(sw)
            self.accent_swatches[name] = sw
            lay.addWidget(sw)
        card.root.addWidget(setting_row(tr("accent_color"), tr("accent_color_desc"), swatches))
        self.mode_box = combo([(tr(f"theme_{k}"), k) for k in ("dark", "light", "system")], self._look["mode"],
                              lambda m: self._try_look(mode=m))
        card.root.addWidget(setting_row(tr("theme_mode"), tr("theme_mode_desc"), self.mode_box))
        self.anim_switch = ToggleSwitch(self._look["animated"])
        self.anim_switch.toggled.connect(lambda on: self._try_look(animated=on))
        card.root.addWidget(setting_row(tr("animated_bg"), tr("animated_bg_desc"), self.anim_switch))

        wall = Card(tr("wallpaper"), Icons.PREVIEW)
        wall.root.addWidget(label(tr("wallpaper_desc"), 9, role="Dim", wrap=True))
        self.wall_row = QHBoxLayout()
        self.wall_row.setSpacing(6)
        wall.root.addLayout(self.wall_row)
        self.wall_opts = QWidget()
        wo = QHBoxLayout(self.wall_opts)
        wo.setContentsMargins(0, 0, 0, 0)
        wo.setSpacing(12)
        wo.addWidget(label(tr("wallpaper_dim"), 9.5, role="Dim"))
        self.dim = QSlider(Qt.Orientation.Horizontal)
        self.dim.setRange(0, 90)
        self.dim.setFixedWidth(180)
        self.dim.valueChanged.connect(lambda v: self._try_wallpaper(dim=v))
        wo.addWidget(self.dim)
        wo.addSpacing(8)
        wo.addWidget(label(tr("wallpaper_blur"), 9.5, role="Dim"))
        self.blur = ToggleSwitch(True)
        self.blur.toggled.connect(lambda on: self._try_wallpaper(blur=on))
        wo.addWidget(self.blur)
        change = button(tr("wallpaper_change"), Icons.EDIT, role="Ghost")
        change.clicked.connect(self._pick_image)
        wo.addWidget(change)
        wo.addStretch(1)
        wall.root.addWidget(self.wall_opts)
        self._fill_wallpapers()
        self._sync_look_controls()
        return [card, wall]

    def _try_look(self, **changes) -> None:
        self._look.update(changes)
        self._sync_look_controls()
        self._update_preview("appearance")

    def _try_wallpaper(self, **changes) -> None:
        wp = dict(self._look["wallpaper"])
        wp.update(changes)
        self._look["wallpaper"] = wp
        self._sync_look_controls()
        self._update_preview("appearance")

    def _pick_image(self) -> None:
        path = self.ctl.import_wallpaper_image(self)
        if path:
            self._try_wallpaper(kind="image", path=path)
            self._fill_wallpapers()

    def _sync_look_controls(self) -> None:
        look = self._look
        for name, sw in getattr(self, "accent_swatches", {}).items():
            sw.setChecked(name == look["accent"])
        if hasattr(self, "mode_box"):
            self.mode_box.blockSignals(True)
            self.mode_box.setCurrentIndex(max(0, self.mode_box.findData(look["mode"])))
            self.mode_box.blockSignals(False)
            self.anim_switch.set_checked_silently(look["animated"])
        wp = look["wallpaper"]
        key_now = wp.get("preset") if wp.get("kind") == "preset" else wp.get("kind")
        for i in range(self.wall_row.count() if hasattr(self, "wall_row") else 0):
            w = self.wall_row.itemAt(i).widget()
            if isinstance(w, WallpaperSwatch):
                w.setChecked(w.key == key_now)
        if hasattr(self, "wall_opts"):
            self.wall_opts.setVisible(wp.get("kind") == "image")
            self.dim.blockSignals(True)
            self.dim.setValue(int(wp.get("dim", 50)))
            self.dim.blockSignals(False)
            self.blur.set_checked_silently(bool(wp.get("blur", True)))

    def _fill_wallpapers(self) -> None:
        from app.ui.widgets.background import PRESETS
        while self.wall_row.count():
            w = self.wall_row.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        wp = self._look["wallpaper"]
        image_path = wp.get("path", "") if wp.get("kind") == "image" else (self.ctl.wallpaper().get("path", "") or "")
        options = [("aurora", {"kind": "aurora"}, tr("wallpaper_aurora"))]
        options += [(name, {"kind": "preset", "preset": name}, tr(f"wallpaper_{name}")) for name in PRESETS]
        options += [("plain", {"kind": "plain"}, tr("wallpaper_plain")),
                    ("image", {"kind": "image", "path": image_path}, tr("wallpaper_pick"))]
        for key, data, tip in options:
            sw = WallpaperSwatch(key, data, tip)
            if key == "image":
                sw.clicked.connect(lambda _=False, d=data: self._try_wallpaper(kind="image", path=d["path"]) if d.get("path")
                                   else self._pick_image())
            elif key in ("aurora", "plain"):
                sw.clicked.connect(lambda _=False, k=key: self._try_wallpaper(kind=k))
            else:
                sw.clicked.connect(lambda _=False, k=key: self._try_wallpaper(kind="preset", preset=k))
            self.wall_row.addWidget(sw)
        self.wall_row.addStretch(1)
        self._sync_look_controls()

    # ============================================================ Taskbar (preview)
    def _sec_taskbar(self) -> list:
        look = Card(tr("tb_look"), Icons.VIEW_LIST)
        b = self._bar
        self.tb_position = combo([(tr("tb_bottom"), "bottom"), (tr("tb_top"), "top")], b["position"],
                                 lambda v: self._try_bar(position=v))
        look.root.addWidget(setting_row(tr("tb_position"), tr("tb_position_desc"), self.tb_position))
        self.tb_align = combo([(tr("tb_center"), "center"), (tr("tb_start"), "start")], b["align"],
                              lambda v: self._try_bar(align=v))
        look.root.addWidget(setting_row(tr("tb_align"), tr("tb_align_desc"), self.tb_align))
        self.tb_size = combo([(tr("tb_small"), "small"), (tr("tb_medium"), "medium"), (tr("tb_large"), "large")],
                             b["size"], lambda v: self._try_bar(size=v))
        look.root.addWidget(setting_row(tr("tb_size"), tr("tb_size_desc"), self.tb_size))
        self.tb_opacity = QSlider(Qt.Orientation.Horizontal)
        self.tb_opacity.setRange(20, 100)
        self.tb_opacity.setFixedWidth(180)
        self.tb_opacity.valueChanged.connect(lambda v: self._try_bar(opacity=v / 100))
        look.root.addWidget(setting_row(tr("tb_opacity"), tr("tb_opacity_desc"), self.tb_opacity))

        behave = Card(tr("tb_behaviour"), Icons.SETTINGS)
        self.tb_switches = {}
        for key, title, desc in (("autohide", "tb_autohide", "tb_autohide_desc"), ("labels", "tb_labels", "tb_labels_desc"),
                                 ("show_search", "tb_show_search", "tb_show_search_desc"),
                                 ("show_desks", "tb_show_desks", "tb_show_desks_desc")):
            sw = ToggleSwitch(bool(b.get(key)))
            sw.toggled.connect(lambda on, k=key: self._try_bar(**{k: on}))
            self.tb_switches[key] = sw
            behave.root.addWidget(setting_row(tr(title), tr(desc), sw))

        tray = Card(tr("sys_card"), Icons.LIGHTNING)
        tray.root.addWidget(label(tr("sys_card_desc"), 9, role="Dim", wrap=True))
        self.tray_list = QListWidget()
        self.tray_list.setObjectName("PinList")
        self.tray_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tray_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tray_list.setFixedHeight(5 * 40 + 8)
        self.tray_list.itemChanged.connect(lambda _: self._tray_changed())
        self.tray_list.model().rowsMoved.connect(lambda *_: self._tray_changed())
        tray.root.addWidget(self.tray_list)
        texts = QHBoxLayout()
        texts.addWidget(label(tr("sys_show_text"), 9.5, role="Dim"))
        self.tray_text = {}
        from PyQt6.QtWidgets import QCheckBox
        for key in ("wifi", "vpn", "sound", "brightness", "battery"):
            box = QCheckBox(tr("sys_" + key))
            box.toggled.connect(lambda _on: self._tray_changed())
            self.tray_text[key] = box
            texts.addWidget(box)
        texts.addStretch(1)
        tray.root.addLayout(texts)
        self.tray_group = ToggleSwitch(bool(b.get("tray_group")))
        self.tray_group.toggled.connect(lambda on: self._try_bar(tray_group=on))
        tray.root.addWidget(setting_row(tr("sys_group"), tr("sys_group_desc"), self.tray_group))

        pins = Card(tr("tb_pinned"), Icons.PIN)
        pins.root.addWidget(label(tr("tb_pinned_desc"), 9, role="Dim", wrap=True))
        self.tb_list = QListWidget()
        self.tb_list.setObjectName("PinList")
        self.tb_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tb_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tb_list.setIconSize(QSize(26, 26))
        self.tb_list.setMinimumHeight(330)
        self.tb_list.itemChanged.connect(lambda _: self._pins_changed())
        self.tb_list.model().rowsMoved.connect(lambda *_: self._pins_changed())
        pins.root.addWidget(self.tb_list)
        self._sync_bar_controls()
        return [look, behave, tray, pins]

    def _tray_changed(self) -> None:
        if getattr(self, "_filling_tray", False):
            return
        items = []
        for i in range(self.tray_list.count()):
            it = self.tray_list.item(i)
            key = it.data(Qt.ItemDataRole.UserRole)
            items.append({"key": key, "show": it.checkState() == Qt.CheckState.Checked,
                          "text": self.tray_text[key].isChecked()})
        self._try_bar(tray=items)

    def _try_bar(self, **changes) -> None:
        self._bar.update(changes)
        self._update_preview("taskbar")

    def _pins_changed(self) -> None:
        if getattr(self, "_filling_pins", False):
            return
        pinned = []
        for i in range(self.tb_list.count()):
            item = self.tb_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                pinned.append(item.data(Qt.ItemDataRole.UserRole))
        self._try_bar(pinned=pinned)

    def _sync_bar_controls(self) -> None:
        b = self._bar
        for box, key in ((self.tb_position, "position"), (self.tb_align, "align"), (self.tb_size, "size")):
            box.blockSignals(True)
            box.setCurrentIndex(max(0, box.findData(b[key])))
            box.blockSignals(False)
        self.tb_opacity.blockSignals(True)
        self.tb_opacity.setValue(int(float(b.get("opacity", 0.82)) * 100))
        self.tb_opacity.blockSignals(False)
        for key, sw in self.tb_switches.items():
            sw.set_checked_silently(bool(b.get(key)))
        from app.ui.widgets.system_tray import draw_icon, tray_conf
        self._filling_tray = True
        self.tray_list.clear()
        for entry in tray_conf(b):
            key = entry["key"]
            item = QListWidgetItem(tr("sys_" + key))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked if entry.get("show") else Qt.CheckState.Unchecked)
            item.setSizeHint(QSize(0, 40))
            from PyQt6.QtGui import QPixmap, QIcon
            pm = QPixmap(48, 48)
            pm.fill(Qt.GlobalColor.transparent)
            pp = QPainter(pm)
            draw_icon(pp, key, QRectF(8, 8, 32, 32), getattr(self.ctl, "system").state, QColor(Palette.text))
            pp.end()
            item.setIcon(QIcon(pm))
            self.tray_list.addItem(item)
            self.tray_text[key].blockSignals(True)
            self.tray_text[key].setChecked(bool(entry.get("text")))
            self.tray_text[key].blockSignals(False)
        self.tray_group.set_checked_silently(bool(b.get("tray_group")))
        self._filling_tray = False
        self._filling_pins = True
        self.tb_list.clear()
        apps = app_registry.all_apps(self.ctl)
        # programs / websites / files you pinned yourself ("item:<id>") are listed too
        apps += [i for i in (app_registry.find(self.ctl, k) for k in b["pinned"] if k.startswith("item:")) if i]
        by_key = {a.key: a for a in apps}
        order = [k for k in b["pinned"] if k in by_key] + [a.key for a in apps if a.key not in b["pinned"]]
        for key in order:
            info = by_key[key]
            item = QListWidgetItem(info.icon(), f"{info.title}    —    {info.description}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked if key in b["pinned"] else Qt.CheckState.Unchecked)
            item.setSizeHint(QSize(0, 40))
            self.tb_list.addItem(item)
        self._filling_pins = False

    # ============================================================ Desktop
    def _sec_desktop(self) -> list:
        card = Card(tr("set_sec_desktop"), Icons.DESKTOP)
        bar = ToggleSwitch(bool(self.ctl.db.get_setting("desk_bar", True)))
        bar.toggled.connect(lambda on: self.ctl.db.set_setting("desk_bar", on))
        card.root.addWidget(setting_row(tr("desk_bar_setting"), tr("desk_bar_setting_desc"), bar))
        desks = ToggleSwitch(bool(self.ctl.taskbar_conf().get("show_desks", True)))
        desks.toggled.connect(lambda on: (self.ctl.set_taskbar(show_desks=on), self._reset("taskbar")))
        card.root.addWidget(setting_row(tr("tb_show_desks"), tr("tb_show_desks_desc"), desks))
        keys = Card(tr("desk_shortcuts"), Icons.KEYBOARD)
        for combo_text, what in (("Ctrl + Alt + →  /  ←", tr("desk_sc_next")), ("Ctrl + PgDn  /  PgUp", tr("desk_sc_next")),
                                 ("Ctrl + Alt + 1 … 9", tr("desk_sc_number")), ("Ctrl + Esc  /  Alt + S", tr("start_menu")),
                                 ("Ctrl + Space", tr("search_placeholder"))):
            cap = label(combo_text, 9.5, QFont.Weight.Medium, "Keycap")
            cap.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            keys.root.addWidget(setting_row(what, "", cap))
        looks = Card(tr("desk_looks_title"), Icons.COLOR)
        looks.root.addWidget(label(tr("desk_look_hint"), 9, role="Dim", wrap=True))
        for key, name in self.ctl.window.desktop.desk_keys():
            look = self.ctl.desk_look(key)
            bits = [tr("wallpaper") if look.get("wallpaper") else "", tr("desk_own_apps") if look.get("pinned") is not None else ""]
            state = " · ".join(b for b in bits if b) or tr("desk_uses_general")
            b = button(tr("desk_look_menu"), Icons.COLOR, role="Ghost")
            b.clicked.connect(lambda _=False, k=key: self.ctl.edit_desk_look(k, self))
            looks.root.addWidget(setting_row(name, state, b))
        start = Card(tr("start_menu"), Icons.APPS)
        reset = button(tr("start_reset"), Icons.UNDO, role="Ghost")
        reset.clicked.connect(lambda: (self.ctl.db.set_setting("start_hidden", []), self.ctl.notify(tr("done"), "success")))
        start.root.addWidget(setting_row(tr("start_reset"), tr("start_reset_desc"), reset))
        return [card, looks, keys, start]

    # ============================================================ Focus timer
    def _sec_timer(self) -> list:
        card = Card(tr("pomodoro_settings"), Icons.TIMER)
        conf = self.ctl.pomodoro_settings()
        self.spins = {}
        for key, text_key, lo, hi in (("focus", "phase_focus", 5, 120), ("short_break", "phase_short_break", 1, 30),
                                      ("long_break", "phase_long_break", 5, 60), ("long_every", "long_every", 2, 8)):
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(int(conf[key]))
            spin.setFixedWidth(130)
            if key != "long_every":
                spin.setSuffix(" " + tr("minutes_short"))
            spin.valueChanged.connect(self._save_timer)
            self.spins[key] = spin
            card.root.addWidget(setting_row(tr(text_key), tr(f"{text_key}_desc") if tr(f"{text_key}_desc") != f"{text_key}_desc" else "", spin))
        self.auto = ToggleSwitch(bool(conf["auto_start"]))
        self.auto.toggled.connect(self._save_timer)
        card.root.addWidget(setting_row(tr("auto_start"), tr("auto_start_desc"), self.auto))
        top = ToggleSwitch(bool(self.ctl.db.get_setting("timer_on_top", False)))
        top.toggled.connect(lambda on: self.ctl.db.set_setting("timer_on_top", on))
        card.root.addWidget(setting_row(tr("timer_on_top"), tr("timer_on_top_desc"), top))
        open_btn = button(tr("app_timer"), Icons.TIMER, role="Ghost")
        open_btn.clicked.connect(lambda: self.ctl.apps.open("timer"))
        card.root.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return [card]

    def _save_timer(self, *_):
        self.ctl.save_pomodoro_settings({k: s.value() for k, s in self.spins.items()} | {"auto_start": self.auto.isChecked()})

    # ============================================================ PDF reader
    def _sec_pdf(self) -> list:
        card = Card(tr("pdf_app_name"), Icons.DOCUMENT)
        pdf = ToggleSwitch(bool(self.ctl.db.get_setting("pdf_open_with", True)))
        pdf.toggled.connect(lambda on: self.ctl.set_pdf_open_with(on) or pdf.set_checked_silently(not on))
        card.root.addWidget(setting_row(tr("pdf_open_with_setting"), tr("pdf_open_with_desc"), pdf))
        fit = combo([(tr("pdf_fit_width"), "width"), (tr("pdf_fit_page"), "page")], self.ctl.db.get_setting("pdf_fit", "width"),
                    lambda v: self.ctl.db.set_setting("pdf_fit", v))
        card.root.addWidget(setting_row(tr("pdf_fit_setting"), tr("pdf_fit_setting_desc"), fit))
        night = ToggleSwitch(bool(self.ctl.db.get_setting("pdf_night", False)))
        night.toggled.connect(lambda on: self.ctl.db.set_setting("pdf_night", on))
        card.root.addWidget(setting_row(tr("pdf_night"), tr("pdf_night_desc"), night))
        default = button(tr("pdf_make_default"), Icons.SETTINGS, role="Ghost")
        default.clicked.connect(lambda: os.startfile("ms-settings:defaultapps"))
        reader = button(tr("pdf_app_name"), Icons.DOCUMENT, role="Ghost")
        reader.clicked.connect(lambda: self.ctl.open_pdf())
        row = QHBoxLayout()
        row.addWidget(reader)
        row.addWidget(default)
        row.addStretch(1)
        card.root.addLayout(row)
        share = Card(tr("pdf_quick_share"), Icons.LINK)
        share.root.addWidget(label(tr("qs_settings_desc"), 9, role="Dim", wrap=True))
        return [card, self._pdf_library_card(), share]

    def _pdf_library_card(self):
        """Which folders the PDF Library searches (besides opened files and the subjects' folders)."""
        from PyQt6.QtWidgets import QFileDialog, QListWidget
        lib = Card(tr("pdfl_title"), Icons.SEARCH)
        lib.root.addWidget(label(tr("pdfl_settings_desc"), 9, role="Dim", wrap=True))
        self.pdf_folders = QListWidget()
        self.pdf_folders.setFixedHeight(120)
        lib.root.addWidget(self.pdf_folders)
        stats = label("", 9, role="Faint")

        def fill():
            self.pdf_folders.clear()
            for f in self.ctl.pdf_index.folders():
                self.pdf_folders.addItem(f)
            files, pages = self.ctl.pdf_index.stats()
            stats.setText(tr("pdfl_stats", files=files, pages=pages))

        def add():
            f = QFileDialog.getExistingDirectory(self, tr("pdfl_add_folder"))
            if f:
                self.ctl.pdf_index.set_folders(self.ctl.pdf_index.folders() + [f])
                fill()
                self.ctl.pdf_index.refresh()

        def remove():
            it = self.pdf_folders.currentItem()
            if it:
                self.ctl.pdf_index.set_folders([f for f in self.ctl.pdf_index.folders() if f != it.text()])
                fill()

        row = QHBoxLayout()
        for text, glyph, fn in ((tr("pdfl_add_folder"), Icons.ADD, add), (tr("pdfl_remove_folder"), Icons.DELETE, remove),
                                (tr("pdfl_reindex"), Icons.REFRESH, lambda: self.ctl.pdf_index.refresh()),
                                (tr("pdfl_title"), Icons.SEARCH, lambda: self.ctl.open_pdf_library())):
            btn = button(text, glyph, role="Ghost")
            btn.clicked.connect(fn)
            row.addWidget(btn)
        row.addStretch(1)
        lib.root.addLayout(row)
        lib.root.addWidget(stats)
        fill()
        return lib

    # ============================================================ Phone
    def _sec_phone(self) -> list:
        card = Card(tr("nav_phone"), Icons.PHONE)
        phone = ToggleSwitch(self.ctl.phone.running)
        phone.toggled.connect(lambda on: self.ctl.set_phone_link(on) or phone.set_checked_silently(not on))
        self._phone_toggle = phone
        card.root.addWidget(setting_row(tr("phone_link"), tr("phone_link_desc"), phone))
        more = button(tr("phone_open_settings"), Icons.PHONE, role="Ghost")
        more.clicked.connect(lambda: self.ctl.open_phone_page())
        card.root.addWidget(setting_row(tr("phone_access_setting"), tr("phone_access_setting_desc"), more))
        return [card]

    # ============================================================ Tools
    def _sec_tools(self) -> list:
        card = Card(tr("settings_tools"), Icons.LIGHTNING)
        clip = ToggleSwitch(bool(self.ctl.db.get_setting("clipboard_history", True)))
        clip.toggled.connect(self.ctl.set_clipboard_history)
        card.root.addWidget(setting_row(tr("clip_setting"), tr("clip_setting_desc"), clip))
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        size = combo([(tr("clip_keep_n", n=n), n) for n in (50, 100, 200, 500)],
                     int(self.ctl.db.get_setting("clipboard_max", 200)), lambda v: self.ctl.db.set_setting("clipboard_max", v))
        lay.addWidget(size)
        clear = button(tr("clip_clear"), Icons.DELETE, role="Ghost")
        clear.clicked.connect(lambda: (self.ctl.clipboard_history.clear(True), self.ctl.notify(tr("clip_cleared"), "success")))
        lay.addWidget(clear)
        card.root.addWidget(setting_row(tr("clip_keep"), "", box))
        target = combo([(tr("translate_auto"), "auto"), ("العربية", "ar"), ("English", "en"), ("Français", "fr"),
                        ("Deutsch", "de"), ("Türkçe", "tr")], self.ctl.db.get_setting("translate_target", "auto"),
                       lambda v: self.ctl.db.set_setting("translate_target", v))
        card.root.addWidget(setting_row(tr("translate_target"), tr("translate_target_desc"), target))
        return [card]

    # ============================================================ Windows integration
    def _sec_windows(self) -> list:
        card = Card(tr("settings_integration"), Icons.APPS)
        explorer = ToggleSwitch(shell_integration.is_context_menu_registered())
        explorer.toggled.connect(lambda on: self.ctl.set_explorer_menu(on) or explorer.set_checked_silently(not on))
        card.root.addWidget(setting_row(tr("explorer_menu"), tr("explorer_menu_desc"), explorer))
        builtin = ToggleSwitch(self.ctl.use_builtin_explorer())
        builtin.toggled.connect(lambda on: self.ctl.db.set_setting("builtin_explorer", on))
        card.root.addWidget(setting_row(tr("builtin_explorer"), tr("builtin_explorer_desc"), builtin))
        return [card]

    # ============================================================ Study / keyboard / plugins / updates / data
    def _sec_study(self) -> list:
        return [self._study_card()]

    def _sec_keyboard(self) -> list:
        return [self._hotkeys_card()]

    def _sec_plugins(self) -> list:
        return [self._plugins_card()]

    def _sec_updates(self) -> list:
        return [self._updates_card()]

    def _sec_data(self) -> list:
        return [self._data_card()]

    def _updates_card(self) -> Card:
        from app.core import updater
        card = Card(tr("settings_updates"), Icons.DOWNLOAD)
        cur = updater.current_build()
        built = cur["built"].replace("T", "  ")[:17] if cur["built"] else "—"
        card.root.addWidget(setting_row(tr("update_current"), f"{APP_NAME} {cur['version']} · {built}", QWidget()))
        self.upd_source = label("", 9, role="Dim", wrap=True)
        self.upd_source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        src_box = QWidget()
        sb = QHBoxLayout(src_box)
        sb.setContentsMargins(0, 0, 0, 0)
        change = button(tr("update_source_change"), Icons.EDIT, role="Ghost")
        change.clicked.connect(lambda: self._update_source_menu(change))
        sb.addWidget(change)
        card.root.addWidget(setting_row(tr("update_source"), tr("update_source_desc"), src_box))
        card.root.addWidget(self.upd_source)
        row = QHBoxLayout()
        self.upd_check = button(tr("update_check"), Icons.REFRESH)
        self.upd_check.clicked.connect(lambda: (self.upd_state.setText(tr("update_checking")), self.ctl.updates.check(True)))
        row.addWidget(self.upd_check)
        self.upd_install = primary_button(tr("update_install"), Icons.DOWNLOAD)
        self.upd_install.clicked.connect(self._install_update)
        row.addWidget(self.upd_install)
        row.addStretch(1)
        card.root.addLayout(row)
        self.upd_state = label("", 9.5, wrap=True)
        card.root.addWidget(self.upd_state)
        auto = ToggleSwitch(bool(self.ctl.db.get_setting("update_auto", True)))
        auto.toggled.connect(lambda on: self.ctl.db.set_setting("update_auto", on))
        card.root.addWidget(setting_row(tr("update_auto"), tr("update_auto_desc"), auto))
        self.listen(self.ctl.data_changed, lambda what: what == "updates" and self._refresh_updates())
        self._refresh_updates()
        return card

    def _refresh_updates(self) -> None:
        src = self.ctl.updates.source()
        self.upd_source.setText(src or tr("update_no_source"))
        res = self.ctl.updates.last
        self.upd_install.setVisible(bool(res and res.get("available")))
        self.upd_install.setEnabled(not self.ctl.updates.busy)
        if not res:
            self.upd_state.setText("")
        elif not res.get("ok"):
            self.upd_state.setText("⚠  " + tr("update_check_failed", error=res.get("error", "")))
        elif res.get("available"):
            notes = res.get("notes") or {}
            text = notes.get(language(), "") if isinstance(notes, dict) else str(notes)
            built = (res.get("built") or "").replace("T", "  ")[:17]
            self.upd_state.setText(tr("update_ready", version=res.get("version", "")) + f"  ·  {built}"
                                   + (f"\n\n{text}" if text else ""))
        else:
            self.upd_state.setText("✓  " + tr("update_none"))

    def _update_source_menu(self, anchor) -> None:
        from PyQt6.QtWidgets import QFileDialog
        from app.ui.dialogs.base import PromptDialog
        from app.ui.widgets.common import add_action, make_menu
        menu = make_menu(self)

        def pick_folder():
            folder = QFileDialog.getExistingDirectory(self, tr("update_source"), self.ctl.updates.source())
            if folder:
                self.ctl.updates.set_source(os.path.normpath(folder))

        def pick_zip():
            path, _ = QFileDialog.getOpenFileName(self, tr("update_source"), "", "Study Hub (*.zip)")
            if path:
                self.ctl.updates.set_source(os.path.normpath(path))

        def pick_url():
            url = PromptDialog.ask(tr("update_source_url"), self.ctl.db.get_setting("update_source", "")
                                   if str(self.ctl.db.get_setting("update_source", "")).startswith("https://") else "",
                                   "https://…/latest.json", self)
            if url:
                if not url.strip().lower().startswith("https://"):
                    self.ctl.notify(tr("update_https_only"), "warning")
                    return
                self.ctl.updates.set_source(url)

        add_action(menu, Icons.FOLDER, tr("update_source_folder"), pick_folder)
        add_action(menu, Icons.ARCHIVE, tr("update_source_zip"), pick_zip)
        add_action(menu, Icons.GLOBE, tr("update_source_url"), pick_url)
        menu.addSeparator()
        add_action(menu, Icons.UNDO, tr("update_source_default"), lambda: self.ctl.updates.set_source(""))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _install_update(self) -> None:
        from app.ui.dialogs.base import ConfirmDialog
        res = self.ctl.updates.last
        if res and ConfirmDialog.ask(tr("update_install"), tr("update_install_confirm"), tr("update_install"),
                                     self, danger=False):
            self.upd_install.setEnabled(False)
            self.upd_state.setText(tr("update_preparing"))
            self.ctl.updates.progress.connect(
                lambda f: self.upd_state.setText(tr("update_downloading", n=int(f * 100))))
            self.ctl.updates.install(res)

    def _study_card(self) -> Card:
        card = Card(tr("settings_study"), Icons.STUDY)
        combo = QComboBox()
        combo.setFixedWidth(200)
        for day in (5, 6, 0):  # Saturday, Sunday, Monday
            combo.addItem(day_name(day), day)
        combo.setCurrentIndex(max(0, combo.findData(self.ctl.week_start())))
        combo.currentIndexChanged.connect(lambda _: self.ctl.set_week_start(combo.currentData()))
        card.root.addWidget(setting_row(tr("week_start"), tr("week_start_desc"), combo))
        return card

    def _hotkeys_card(self) -> Card:
        card = Card(tr("settings_hotkeys"), Icons.KEYBOARD)
        card.root.addWidget(label(tr("hotkeys_desc"), 9, role="Dim", wrap=True))
        self.hotkey_edits: dict[str, QKeySequenceEdit] = {}
        current = self.ctl.hotkey_map()
        for action in DEFAULT_HOTKEYS:
            holder = QWidget()
            h = QHBoxLayout(holder)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            edit = QKeySequenceEdit(QKeySequence(current.get(action, "")))
            if hasattr(edit, "setMaximumSequenceLength"):
                edit.setMaximumSequenceLength(1)
            if hasattr(edit, "setClearButtonEnabled"):
                edit.setClearButtonEnabled(True)
            edit.setFixedWidth(200)
            edit.editingFinished.connect(lambda e=edit: self._trim(e))
            h.addWidget(edit)
            reset = IconButton(Icons.REFRESH, tr("reset_default"), 34, 10)
            reset.clicked.connect(lambda _=False, e=edit, a=action: e.setKeySequence(QKeySequence(DEFAULT_HOTKEYS[a])))
            h.addWidget(reset)
            self.hotkey_edits[action] = edit
            card.root.addWidget(setting_row(tr(f"hk_{action}"), tr(f"hk_{action}_desc"), holder))
        row = QHBoxLayout()
        self.hotkey_status = label("", 9, role="Dim")
        row.addWidget(self.hotkey_status, 1)
        apply = primary_button(tr("apply"), Icons.ACCEPT)
        apply.clicked.connect(self._apply_hotkeys)
        row.addWidget(apply)
        card.root.addLayout(row)
        return card

    @staticmethod
    def _trim(edit: QKeySequenceEdit):
        seq = edit.keySequence()
        if seq.count() > 1:  # Qt < 6.5 has no maximumSequenceLength
            edit.setKeySequence(QKeySequence(seq[0]))

    def _apply_hotkeys(self):
        mapping = {a: e.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
                   for a, e in self.hotkey_edits.items()}
        failed = self.ctl.apply_hotkeys(mapping)
        if failed:
            names = ", ".join(tr(f"hk_{a}") for a in failed)
            self.hotkey_status.setText(tr("hotkeys_failed", names=names))
            self.hotkey_status.setStyleSheet("color: #FBBF24;")
        else:
            self.hotkey_status.setText(tr("hotkeys_saved"))
            self.hotkey_status.setStyleSheet("color: #34D399;")

    def _plugins_card(self) -> Card:
        card = Card(tr("settings_plugins"), Icons.APPS)
        card.root.addWidget(label(tr("plugins_desc"), 9, role="Dim", wrap=True))
        for info in self.ctl.plugins.plugins.values():
            switch = ToggleSwitch(info.enabled and info.loaded)
            switch.toggled.connect(lambda on, pid=info.id, sw=switch: self.ctl.set_plugin_enabled(pid, on)
                                   or sw.set_checked_silently(not on))
            desc = info.text("description")
            meta = f"v{info.meta.get('version', '?')} · " + (tr("plugin_builtin") if info.builtin else tr("plugin_external"))
            if info.error:
                meta += " · ⚠ " + tr("plugin_error")
            card.root.addWidget(setting_row(info.text("name") or info.id, f"{desc}\n{meta}" if desc else meta, switch))
        row = QHBoxLayout()
        folder = button(tr("open_plugins_folder"), Icons.OPEN_FOLDER, role="Ghost")
        folder.clicked.connect(self._open_plugins_folder)
        row.addWidget(folder)
        row.addStretch(1)
        card.root.addLayout(row)
        return card

    def _open_plugins_folder(self):
        from app.core.plugins import PLUGIN_DIR
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(PLUGIN_DIR)

    def _data_card(self) -> Card:
        card = Card(tr("settings_data"), Icons.SAVE)
        path = label(str(DB_PATH), 9, role="Faint")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.root.addWidget(path)
        row = QHBoxLayout()
        backup = button(tr("backup_now"), Icons.SAVE)
        backup.clicked.connect(self.ctl.backup_now)
        row.addWidget(backup)
        folder = button(tr("open_data_folder"), Icons.OPEN_FOLDER)
        folder.clicked.connect(lambda: os.startfile(DATA_DIR))
        row.addWidget(folder)
        row.addStretch(1)
        row.addWidget(label(f"{APP_NAME} v{VERSION}", 9, role="Faint"))
        card.root.addLayout(row)
        return card
