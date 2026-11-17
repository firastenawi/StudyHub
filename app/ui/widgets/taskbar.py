"""
The shell's taskbar: Start, search, desktops, pinned + running apps, and the tray (focus timer, phone,
next alarm, quick note, language, clock).

Everything about it is set in Settings › Taskbar (live preview there): position (bottom / top), alignment
(centre / start), size, transparency, auto-hide, names next to the icons, which apps are pinned and in what
order, and whether the desktops button shows.
"""
from __future__ import annotations

import time
from datetime import datetime

from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from app.core.i18n import format_hm, is_rtl, tr
from app.ui import apps as app_registry
from app.ui.animations import OUT, value_animation
from app.ui.theme import Icons, Palette, Theme, app_icon_pixmap, glyph_icon, icon_font, ink, qcolor, ui_font
from app.ui.widgets.common import add_action, add_submenu, make_menu

SIZES = {"small": (46, 38), "medium": (56, 46), "large": (68, 56)}  # bar height, button size

DEFAULTS = {
    "position": "bottom", "align": "center", "size": "medium", "opacity": 0.82, "autohide": False,
    "labels": False, "show_desks": True, "show_search": True, "pinned": None,  # None = the default set
    "tray": None, "tray_group": False,  # status icons (Wi-Fi, VPN, sound, brightness, battery): widgets/system_tray.py
}


def taskbar_settings(ctl, global_only: bool = False) -> dict:
    """The taskbar's settings; the apps pinned on it can belong to the current desktop (its own list)."""
    conf = {**DEFAULTS, **(ctl.db.get_setting("taskbar", {}) or {})}
    if conf["pinned"] is None:
        conf["pinned"] = [a.key for a in app_registry.BUILTIN if a.pinned]
    if not global_only and hasattr(ctl, "desk_look"):
        own = ctl.desk_look(ctl.current_desk()).get("pinned")
        if own is not None:
            conf["pinned"] = list(own)
            conf["desk_pinned"] = True
    return conf


def section(menu, text: str) -> None:
    """A small grey title inside a menu (Pinned / Recent / Tasks)."""
    act = menu.addAction(text)
    act.setEnabled(False)
    f = act.font()
    f.setBold(True)
    f.setPointSizeF(8.6)
    act.setFont(f)


class TaskButton(QAbstractButton):
    """One app on the bar: its tile, a line under it while it runs (longer when it is in front)."""
    menu_requested = pyqtSignal(object)

    def __init__(self, info, size: int, show_label: bool, parent=None):
        super().__init__(parent)
        self.info = info
        self.size_px = size
        self.show_label = show_label
        self.running = 0         # how many windows
        self.active = False
        self._hover = 0.0
        self._anim = value_animation(self, 0.0, 1.0, 140, self._set_hover, OUT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{info.title}\n{info.description}")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self.menu_requested.emit(self.mapToGlobal(pos)))
        self._fit()

    def _fit(self):
        w = self.size_px
        if self.show_label and self.running:
            self.setFont(ui_font(9.2))
            w += min(150, self.fontMetrics().horizontalAdvance(self.info.title) + 18)
        self.setFixedSize(w, self.size_px)

    def set_state(self, running: int, active: bool):
        if (running, active) != (self.running, self.active):
            self.running, self.active = running, active
            self._fit()
            self.update()

    def _set_hover(self, v):
        self._hover = float(v)
        self.update()

    def enterEvent(self, e):
        self._anim.setDirection(self._anim.Direction.Forward)
        self._anim.start()

    def leaveEvent(self, e):
        self._anim.setDirection(self._anim.Direction.Backward)
        self._anim.start()

    def sizeHint(self):
        return QSize(self.width(), self.height())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        bg = 0.06 * self._hover + (0.09 if self.active else (0.035 if self.running else 0))
        if bg > 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink(bg))
            p.drawRoundedRect(r, 9, 9)
        icon = int(self.size_px * 0.66)
        rtl = is_rtl()
        if self.show_label and self.running:
            x = r.right() - self.size_px + (self.size_px - icon) / 2 if rtl else r.left() + (self.size_px - icon) / 2 - 2
        else:
            x = (self.width() - icon) / 2
        y = (self.height() - icon) / 2 - 1
        p.drawPixmap(QRect(int(x), int(y), icon, icon), self.info.pixmap(icon * 2))
        if self.show_label and self.running:
            p.setPen(QColor(Palette.text))
            p.setFont(ui_font(9.2))
            tr_ = (QRectF(r.left() + 8, 0, r.width() - self.size_px - 6, self.height()) if rtl
                   else QRectF(x + icon + 8, 0, r.right() - x - icon - 12, self.height()))
            p.drawText(tr_, Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft),
                       p.fontMetrics().elidedText(self.info.title, Qt.TextElideMode.ElideRight, int(tr_.width())))
        if self.running:
            line_w = 16 if self.active else 6
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(Theme.accent()) if self.active else ink(0.45))
            cx = (x + icon / 2) if self.show_label else self.width() / 2
            p.drawRoundedRect(QRectF(cx - line_w / 2, self.height() - 5, line_w, 3), 1.5, 1.5)
            if self.running > 1:  # e.g. three PDFs
                p.setBrush(QColor(Theme.accent()))
                badge = QRectF(x + icon - 9, y - 3, 14, 14)
                p.drawEllipse(badge)
                p.setPen(QColor("#FFFFFF"))
                p.setFont(ui_font(7, QFont.Weight.Bold))
                p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(min(self.running, 9)))


class GlyphButton(QAbstractButton):
    """Start / search / desktops: a glyph (or the logo) with the same hover as the app buttons."""

    def __init__(self, glyph: str | None, tooltip: str, size: int, text: str = "", parent=None):
        super().__init__(parent)
        self.glyph, self.text_ = glyph, text
        self.size_px = size
        self._hover = 0.0
        self._anim = value_animation(self, 0.0, 1.0, 140, self._set_hover, OUT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setFont(ui_font(9.4))
        w = size + (self.fontMetrics().horizontalAdvance(text) + 16 if text else 0)
        self.setFixedSize(w, size)

    def _set_hover(self, v):
        self._hover = float(v)
        self.update()

    def enterEvent(self, e):
        self._anim.setDirection(self._anim.Direction.Forward)
        self._anim.start()

    def leaveEvent(self, e):
        self._anim.setDirection(self._anim.Direction.Backward)
        self._anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        alpha = 0.06 * self._hover + (0.09 if self.isDown() or self.isChecked() else 0)
        if self.text_:
            alpha += 0.05
        if alpha:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink(alpha))
            p.drawRoundedRect(r, 9 if not self.text_ else r.height() / 2, 9 if not self.text_ else r.height() / 2)
        rtl = is_rtl()
        icon_box = QRectF(r.right() - self.size_px + 4, 0, self.size_px - 4, self.height()) if (rtl and self.text_) \
            else QRectF(0 if not self.text_ else r.left() + 4, 0, self.size_px - 4 if self.text_ else self.width(), self.height())
        if self.glyph is None:  # Start: the Study Hub logo
            s = int(self.size_px * 0.58)
            p.drawPixmap(QRect(int(icon_box.center().x() - s / 2), int(icon_box.center().y() - s / 2), s, s),
                         app_icon_pixmap(s * 2))
        else:
            p.setPen(QColor(Palette.text if self._hover > 0.5 else Palette.text_dim))
            p.setFont(icon_font(self.size_px * 0.22))
            p.drawText(icon_box, Qt.AlignmentFlag.AlignCenter, self.glyph)
        if self.text_:
            p.setPen(QColor(Palette.text_faint if self._hover < 0.5 else Palette.text_dim))
            p.setFont(ui_font(9.4))
            box = QRectF(r.left() + 10, 0, r.width() - self.size_px - 6, self.height()) if rtl \
                else QRectF(icon_box.right(), 0, r.right() - icon_box.right() - 10, self.height())
            p.drawText(box, Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft),
                       self.text_)


class Taskbar(QFrame):
    start_requested = pyqtSignal()
    clock_requested = pyqtSignal()   # quick actions + notifications
    status_requested = pyqtSignal(list, object)  # status icon keys, the button (for the panel's position)
    desks_requested = pyqtSignal(object)  # the desktops button (for the popup's position)

    def __init__(self, ctl, preview_conf: dict | None = None, parent=None):
        """preview_conf: draw with these settings and do nothing on click (Settings' live preview)."""
        super().__init__(parent)
        self.ctl = ctl
        self.preview = preview_conf is not None
        self.conf = preview_conf or taskbar_settings(ctl)
        self.setObjectName("Taskbar")
        self.buttons: dict[str, TaskButton] = {}
        bar_h, btn = SIZES.get(self.conf["size"], SIZES["medium"])
        self.bar_h, self.btn = bar_h, btn
        self.setFixedHeight(bar_h)

        self.group = QWidget(self)    # Start, search, desktops, apps
        self.g_lay = QHBoxLayout(self.group)
        self.g_lay.setContentsMargins(0, 0, 0, 0)
        self.g_lay.setSpacing(3)
        self.start_btn = GlyphButton(None, tr("start_menu"), btn)
        self.start_btn.clicked.connect(self.start_requested.emit)
        self.g_lay.addWidget(self.start_btn)
        if self.conf.get("show_search", True):
            self.search_btn = GlyphButton(Icons.SEARCH, tr("search_placeholder") + "  (Ctrl+Space)", btn,
                                          tr("tb_search") if self.conf["size"] != "small" else "")
            self.search_btn.clicked.connect(lambda: None if self.preview else ctl.open_palette())
            self.g_lay.addWidget(self.search_btn)
        if self.conf.get("show_desks", True):
            self.desks_btn = GlyphButton(Icons.VIEW_GRID, tr("desks_title") + "  (Ctrl+Alt+← →)", btn)
            self.desks_btn.clicked.connect(lambda: None if self.preview else self.desks_requested.emit(self.desks_btn))
            self.g_lay.addWidget(self.desks_btn)
        sep = QFrame()
        sep.setFixedSize(1, int(btn * 0.5))
        sep.setStyleSheet(f"background: {ink(0.14).name(QColor.NameFormat.HexArgb)};")
        self.g_lay.addSpacing(4)
        self.g_lay.addWidget(sep)
        self.g_lay.addSpacing(4)
        self.apps_lay = QHBoxLayout()
        self.apps_lay.setSpacing(2)
        self.g_lay.addLayout(self.apps_lay)

        self.tray = QWidget(self)      # timer, phone, alarm, quick note, language, clock
        t = QHBoxLayout(self.tray)
        t.setContentsMargins(0, 0, 0, 0)
        t.setSpacing(4)
        self.focus_pill = self._chip(lambda: ctl.apps.open("timer"))
        self.focus_pill.setToolTip(tr("app_timer"))
        t.addWidget(self.focus_pill)
        self.handoff_chip = self._chip(ctl.continue_from_phone)
        t.addWidget(self.handoff_chip)
        self.alarm_chip = self._chip(lambda: ctl.apps.open("clock"))
        self.alarm_chip.setIcon(glyph_icon(Icons.BELL, Palette.text_dim, 16))
        t.addWidget(self.alarm_chip)
        for glyph, tip, slot in ((Icons.NOTES, tr("quick_note") + f"  ({ctl.hotkey_text('quick_note')})",
                                  lambda: ctl.open_quick_note()),):
            b = GlyphButton(glyph, tip, int(btn * 0.86))
            b.clicked.connect(lambda _=False, s=slot: None if self.preview else s())
            t.addWidget(b)
        # (the language is switched in Settings › General)
        from app.ui.widgets.system_tray import StatusButton, available, tray_conf
        self.status_buttons = []
        state = ctl.system.state if getattr(ctl, "system", None) else {}
        shown = [(i["key"], bool(i.get("text"))) for i in tray_conf(self.conf) if i.get("show") and available(i["key"], state)]
        groups = [shown] if self.conf.get("tray_group") and shown else [[k] for k in shown]
        for group in groups:
            if not group:
                continue
            sb = StatusButton(group, int(btn * 0.86), ctl)
            sb.clicked.connect(lambda _=False, g=group, b=sb: None if self.preview
                               else self.status_requested.emit([k for k, _t in g], b))
            t.addWidget(sb)
            self.status_buttons.append(sb)
        if self.status_buttons and getattr(ctl, "system", None) is not None:
            ctl.system.changed.connect(self._status_changed)
        self.clock = QPushButton()
        self.clock.setObjectName("TaskClock")
        self.clock.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clock.setFont(ui_font(9.2 if self.conf["size"] != "small" else 8.6, QFont.Weight.DemiBold))
        self.clock.setFlat(True)
        self.clock.clicked.connect(lambda: None if self.preview else self.clock_requested.emit())
        self.clock.setToolTip(tr("qa_title"))
        t.addWidget(self.clock)
        self.badge = QLabel(self.clock)  # unseen notifications
        self.badge.setObjectName("ClockBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setFont(ui_font(7, QFont.Weight.Bold))
        self.badge.setFixedSize(16, 16)
        self.badge.hide()
        if not self.preview:
            ctl.quick.notified.connect(self._badge)
        self._ticks = 0

        self.rebuild_apps()
        self._timer = QTimer(self, timeout=self._tick)
        if not self.preview:
            self._timer.start(1000)
            ctl.apps.changed.connect(self.refresh_state)
        self._tick()

    def _status_changed(self) -> None:
        try:
            for b in self.status_buttons:
                b.refresh()
            self._layout()
        except RuntimeError:  # this bar was replaced (settings changed)
            pass

    def _badge(self) -> None:
        n = self.ctl.quick.unseen
        self.badge.setVisible(n > 0)
        self.badge.setText(str(min(n, 9)) + ("+" if n > 9 else ""))
        self.badge.move(self.clock.width() - 16 if not is_rtl() else 0, 0)
        self.badge.raise_()

    def _chip(self, slot) -> QPushButton:
        chip = QPushButton()
        chip.setObjectName("Chip")
        chip.setFont(ui_font(9.2, QFont.Weight.DemiBold))
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.clicked.connect(lambda: None if self.preview else slot())
        chip.hide()
        return chip

    # ------------------------------------------------------------------ apps
    def rebuild_apps(self) -> None:
        while self.apps_lay.count():
            w = self.apps_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.buttons.clear()
        running = {} if self.preview else self.ctl.apps.running()
        for key in self._wanted(running):
            info = app_registry.find(self.ctl, key)
            b = TaskButton(info, self.btn, self.conf.get("labels", False))
            b.clicked.connect(lambda _=False, k=key: self._clicked(k))
            b.menu_requested.connect(lambda pos, k=key: self._app_menu(k, pos))
            self.apps_lay.addWidget(b)
            self.buttons[key] = b
        self.refresh_state()

    def _wanted(self, running: dict) -> list[str]:
        """Pinned apps, then the other running ones — only apps that exist (a disabled add-on has none)."""
        keys = list(self.conf["pinned"]) + [k for k in running if k not in self.conf["pinned"]]
        known = {a.key for a in app_registry.all_apps(self.ctl)}
        return list(dict.fromkeys(k for k in keys if k in known
                                  or k.startswith("item:") and app_registry.find(self.ctl, k) is not None))

    def refresh_state(self) -> None:
        if self.preview:
            for i, b in enumerate(self.buttons.values()):  # look alive in the preview: two running, one in front
                b.set_state(1 if i in (1, 2) else 0, i == 1)
            self._layout()
            return
        running = self.ctl.apps.running()
        if list(self.buttons) != self._wanted(running):
            self.rebuild_apps()
            return
        active = self.ctl.apps.active_key()
        for key, b in self.buttons.items():
            b.set_state(len(running.get(key, [])), key == active)
        self._layout()

    def _clicked(self, key: str) -> None:
        if self.preview:
            return
        wins = self.ctl.apps.running().get(key, [])
        if len(wins) > 1:  # several PDFs: pick one
            menu = make_menu(self)
            for w in wins:
                add_action(menu, Icons.DOCUMENT, w.windowTitle(), lambda win=w: self.ctl.apps.front(win))
            b = self.buttons[key]
            menu.exec(self._menu_pos(b, menu))
            return
        self.ctl.apps.activate(key)

    def _app_menu(self, key: str, pos) -> None:
        if self.preview:
            return
        info = app_registry.find(self.ctl, key)
        menu = make_menu(self)
        rec = self.ctl.recents
        # jump list: pinned items, recent items, the app's quick tasks (same lists as Windows' taskbar)
        pins, recent, tasks = rec.pinned(key), rec.recent(key), rec.tasks(key)
        glyph = {"pdf": Icons.DOCUMENT, "note": Icons.NOTES, "folder": Icons.FOLDER}
        if pins:
            section(menu, tr("jump_pinned"))
            for e in pins:
                add_action(menu, glyph.get(e["kind"], Icons.DOCUMENT), e["title"], lambda e=e: rec.open(e))
        if recent:
            section(menu, tr("jump_recent"))
            for e in recent:
                add_action(menu, glyph.get(e["kind"], Icons.DOCUMENT), e["title"], lambda e=e: rec.open(e))
        if pins or recent:
            sub = add_submenu(menu, Icons.PIN, tr("jump_pin_item"))
            for e in recent:
                add_action(sub, Icons.PIN, e["title"], lambda e=e: rec.set_pinned(key, e, True))
            if recent and pins:
                sub.addSeparator()
            for e in pins:
                add_action(sub, Icons.CLOSE, tr("jump_unpin_item", name=e["title"]), lambda e=e: rec.set_pinned(key, e, False))
        if tasks:
            section(menu, tr("jump_tasks"))
            for g, title, cmd in tasks:
                add_action(menu, g, title, lambda c=cmd: self.ctl.run_command(c))
        menu.addSeparator()
        add_action(menu, info.glyph, info.title, lambda: self.ctl.apps.open(key))
        add_action(menu, Icons.LIGHTNING, tr("qa_add"), lambda: self.ctl.quick.add({"type": "app", "key": key}))
        pinned = key in self.conf["pinned"]
        add_action(menu, Icons.PIN, tr("tb_unpin") if pinned else tr("tb_pin"),
                   lambda: self.ctl.set_taskbar_pinned(key, not pinned))
        if self.ctl.apps.running().get(key):
            menu.addSeparator()
            add_action(menu, Icons.CLOSE, tr("tb_close_window"), lambda: self.ctl.apps.close(key), danger=True)
        menu.exec(self._menu_pos(self.buttons.get(key, self), menu))

    def _menu_pos(self, anchor: QWidget, menu) -> QPoint:
        size = menu.sizeHint()
        top = anchor.mapToGlobal(QPoint(0, 0))
        y = top.y() - size.height() - 6 if self.conf["position"] == "bottom" else top.y() + anchor.height() + 6
        return QPoint(top.x() + (anchor.width() - size.width()) // 2, y)

    # ---------------------------------------------------------------- layout
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()

    def _layout(self) -> None:
        self.group.adjustSize()
        self.tray.adjustSize()
        gw, tw = self.group.sizeHint().width(), self.tray.sizeHint().width()
        h = self.height()
        rtl = is_rtl()
        pad = 10
        if self.conf["align"] == "center":
            gx = max(pad, (self.width() - gw) // 2)
            if gx + gw > self.width() - tw - pad * 2:  # too many apps: slide away from the tray
                gx = max(pad, self.width() - tw - gw - pad * 2) if not rtl else tw + pad * 2
        else:
            gx = self.width() - gw - pad if rtl else pad
        self.group.setGeometry(gx, (h - self.group.sizeHint().height()) // 2, gw, self.group.sizeHint().height())
        tx = pad if rtl else self.width() - tw - pad
        self.tray.setGeometry(tx, (h - self.tray.sizeHint().height()) // 2, tw, self.tray.sizeHint().height())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        base = QColor(Palette.chrome)
        base.setAlphaF(max(0.15, min(1.0, float(self.conf.get("opacity", 0.82)))))
        p.fillRect(self.rect(), base)
        p.setPen(ink(0.09))
        y = 0 if self.conf["position"] == "bottom" else self.height() - 1
        p.drawLine(0, y, self.width(), y)

    # ------------------------------------------------------------------ tray
    def _tick(self) -> None:
        now = datetime.now()
        self.clock.setText(f"{format_hm(now.hour, now.minute)}\n{now:%d/%m/%Y}")
        self.clock.setToolTip(now.strftime("%A %Y-%m-%d"))
        if self.preview:
            return
        ctl = self.ctl
        if self._ticks % 5 == 0:
            nxt = ctl.next_alarm()
            soon = nxt and (nxt[1] - now).total_seconds() < 24 * 3600
            self.alarm_chip.setVisible(bool(soon))
            if soon:
                self.alarm_chip.setText(" " + format_hm(nxt[1].hour, nxt[1].minute))
                self.alarm_chip.setToolTip(nxt[0]["label"] or tr("alarm"))
        self._ticks += 1
        pc = getattr(ctl, "phone_context", {}) or {}
        fresh = pc and time.time() - pc.get("at", 0) < 2 * 3600
        self.handoff_chip.setVisible(bool(fresh))
        if fresh:
            what = (tr("handoff_chip_pdf", page=pc.get("page", 0) + 1) if pc.get("kind") == "pdf"
                    else tr("handoff_chip_note"))
            self.handoff_chip.setText("📱 " + what)
            self.handoff_chip.setToolTip(tr("handoff_chip_tip", name=pc.get("title", "")))
        timer = ctl.pomodoro
        active = timer.state != "idle"
        was = self.focus_pill.isVisible()
        self.focus_pill.setVisible(active)
        if active:
            m, s = divmod(timer.remaining, 60)
            color = Palette.success if timer.phase != "focus" else Theme.accent()
            dot = "●" if timer.state == "running" else "❚❚"
            self.focus_pill.setText(f"{dot}  {tr('phase_' + timer.phase)}  {m:02d}:{s:02d}")
            if getattr(self, "_pill_color", None) != color:
                self._pill_color = color
                self.focus_pill.setStyleSheet(f"QPushButton#Chip {{ color: {color}; border-color: {color}; }}")
        if was != active or self._ticks % 5 == 1:
            self._layout()
