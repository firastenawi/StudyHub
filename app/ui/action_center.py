"""
Quick actions + notifications: a small panel that drops up from the taskbar clock.

Quick actions are yours to choose:
  * right-click ANY button in Study Hub (any app window, the taskbar, Start) › "Add to quick actions"
  * or "+" in the panel › pick from every Study Hub command, app and switch
Right-click a tile to move or remove it. Switches (phone link, light theme, focus timer…) light up when on.
Below them: the last notifications (Study Hub's toasts and phone events), newest first.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (QAbstractButton, QApplication, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
                             QLineEdit, QListWidget, QListWidgetItem, QMenu, QScrollArea, QVBoxLayout, QWidget)

from app.core.i18n import format_hm, is_rtl, tr
from app.ui.animations import OUT, value_animation
from app.ui.dialogs.base import StyledDialog, drop_shadow
from app.ui.theme import Icons, Palette, Theme, glyph_icon, icon_font, ink, qcolor, ui_font
from app.ui.widgets.common import IconButton, add_action, button, label, make_menu

KIND_GLYPH = {"info": Icons.INFO, "success": Icons.ACCEPT, "warning": Icons.WARNING, "error": Icons.WARNING}

# switches: (glyph, title key, is-on, flip)
SWITCHES = {
    "phone": (Icons.PHONE, "qa_sw_phone", lambda c: c.phone.running, lambda c: c.set_phone_link(not c.phone.running)),
    "theme": (Icons.LIGHTBULB, "qa_sw_light", lambda c: _light(), lambda c: c.set_theme_mode("dark" if _light() else "light")),
    "focus": (Icons.TIMER, "qa_sw_focus", lambda c: c.pomodoro.state == "running", lambda c: c.pomodoro.toggle()),
    "deskbar": (Icons.DESKTOP, "desk_bar_setting", lambda c: bool(c.db.get_setting("desk_bar", True)),
                lambda c: c.db.set_setting("desk_bar", not c.db.get_setting("desk_bar", True))),
    "clipboard": (Icons.PASTE, "qa_sw_clipboard", lambda c: bool(c.db.get_setting("clipboard_history", True)),
                  lambda c: c.set_clipboard_history(not c.db.get_setting("clipboard_history", True))),
    "autohide": (Icons.VIEW_LIST, "tb_autohide", lambda c: bool(c.taskbar_conf().get("autohide")),
                 lambda c: c.set_taskbar(autohide=not c.taskbar_conf().get("autohide"))),
}

DEFAULT = [{"type": "switch", "key": "phone"}, {"type": "switch", "key": "theme"}, {"type": "switch", "key": "focus"},
           {"type": "command", "key": "quick_note"}, {"type": "command", "key": "hk_screenshot"},
           {"type": "command", "key": "hk_clipboard_history"}, {"type": "app", "key": "clock"},
           {"type": "command", "key": "hk_lock_screen"}, {"type": "command", "key": "add_to_hub"}]


def _light() -> bool:
    return Palette.light()


def _is_glyph(text: str) -> bool:
    return len(text) == 1 and 0xE000 <= ord(text) <= 0xF8FF  # Segoe Fluent / MDL2 icon


class QuickActions(QObject):
    """ctl.quick — the chosen tiles + the notification history."""
    changed = pyqtSignal()
    notified = pyqtSignal()

    def __init__(self, ctl):
        super().__init__(ctl)
        self.ctl = ctl
        self.history: list[dict] = list(ctl.db.get_setting("notif_history", []) or [])[-60:]
        self.unseen = 0
        self._save_timer = QTimer(self, singleShot=True, interval=2000,
                                  timeout=lambda: ctl.db.set_setting("notif_history", self.history[-60:]))
        self._catcher = ButtonCatcher(self)
        QApplication.instance().installEventFilter(self._catcher)

    # ---------------------------------------------------------------- tiles
    def entries(self) -> list[dict]:
        saved = self.ctl.db.get_setting("quick_actions", None)
        return list(DEFAULT if saved is None else saved)

    def _save(self, entries: list[dict]) -> None:
        self.ctl.db.set_setting("quick_actions", entries)
        self.changed.emit()

    def add(self, entry: dict) -> None:
        entries = self.entries()
        if any(_same(e, entry) for e in entries):
            self.ctl.notify(tr("qa_already"), "info")
            return
        entries.append(entry)
        self._save(entries)
        self.ctl.notify(tr("qa_added", name=self.title(entry)), "success")

    def remove(self, index: int) -> None:
        entries = self.entries()
        if 0 <= index < len(entries):
            entries.pop(index)
            self._save(entries)

    def move(self, index: int, delta: int) -> None:
        entries = self.entries()
        j = index + delta
        if 0 <= index < len(entries) and 0 <= j < len(entries):
            entries[index], entries[j] = entries[j], entries[index]
            self._save(entries)

    def reset(self) -> None:
        self._save(list(DEFAULT))

    # ------------------------------------------------------------ describing
    def _command(self, key: str):
        return next(((g, k) for g, k, _a in self.ctl.command_list() if k == key), None)

    def title(self, e: dict) -> str:
        t = e.get("type")
        if t == "switch" and e["key"] in SWITCHES:
            return tr(SWITCHES[e["key"]][1])
        if t == "command":
            return tr(e["key"])
        if t == "app":
            from app.ui import apps as app_registry
            info = app_registry.find(self.ctl, e["key"])
            return info.title if info else e["key"]
        return e.get("label") or e.get("text") or "?"

    def glyph(self, e: dict) -> str:
        t = e.get("type")
        if t == "switch" and e["key"] in SWITCHES:
            return SWITCHES[e["key"]][0]
        if t == "command":
            c = self._command(e["key"])
            return c[0] if c else Icons.LIGHTNING
        if t == "app":
            from app.ui import apps as app_registry
            info = app_registry.find(self.ctl, e["key"])
            return info.glyph if info else Icons.APPS
        return e.get("glyph") or Icons.LIGHTNING

    def is_on(self, e: dict) -> bool | None:
        if e.get("type") == "switch" and e["key"] in SWITCHES:
            try:
                return bool(SWITCHES[e["key"]][2](self.ctl))
            except Exception:
                return None
        return None

    def available(self, e: dict) -> bool:
        t = e.get("type")
        if t == "switch":
            return e["key"] in SWITCHES
        if t == "command":
            return self._command(e["key"]) is not None
        if t == "app":
            from app.ui import apps as app_registry
            return app_registry.find(self.ctl, e["key"]) is not None
        return t == "button"

    # --------------------------------------------------------------- running
    def run(self, e: dict) -> None:
        t = e.get("type")
        if t == "switch" and e["key"] in SWITCHES:
            SWITCHES[e["key"]][3](self.ctl)
            self.changed.emit()
        elif t == "command":
            self.ctl.run_command(e["key"])
        elif t == "app":
            self.ctl.apps.open(e["key"])
        elif t == "button":
            self._press_button(e)

    def _press_button(self, e: dict) -> None:
        """A button you added by right-click: open its app, find the same button, press it."""
        app = e.get("app", "shell")
        if app == "shell":
            self.ctl.show_window()
            win = self.ctl.window
        else:
            win = self.ctl.apps.open(app)
            win = self._window_of(app) or win
        def press(tries=6):
            root = self._window_of(app) if app != "shell" else self.ctl.window
            target = None
            if root is not None:
                for b in root.findChildren(QAbstractButton):
                    if b.text() == e.get("text", "") and b.toolTip() == e.get("tip", ""):
                        target = b
                        if b.isVisible():
                            break
            if target is not None and target.isEnabled():
                target.click()
            elif tries:
                QTimer.singleShot(250, lambda: press(tries - 1))
            else:
                self.ctl.notify(tr("qa_button_gone"), "warning")
        QTimer.singleShot(200, press)

    def _window_of(self, app: str):
        run = self.ctl.apps.running().get(app)
        return run[0] if run else None

    # ---------------------------------------------------------- notifications
    def record(self, text: str, kind: str) -> None:
        self.history.append({"text": text, "kind": kind, "at": time.time()})
        self.history = self.history[-60:]
        self.unseen += 1
        self._save_timer.start()
        self.notified.emit()

    def clear_history(self) -> None:
        self.history = []
        self.unseen = 0
        self.ctl.db.set_setting("notif_history", [])
        self.notified.emit()


def _same(a: dict, b: dict) -> bool:
    keys = ("type", "key", "app", "text", "tip")
    return all(a.get(k) == b.get(k) for k in keys)


def window_key(widget) -> str | None:
    """Which app a widget belongs to (for "add this button")."""
    win = widget.window()
    from app.ui.app_window import AppWindow
    if isinstance(win, AppWindow):
        return win.key
    name = type(win).__name__
    return {"MainWindow": "shell", "TimerWindow": "timer", "LabWindow": "lab", "PdfReader": "pdf"}.get(name)


class ButtonCatcher(QObject):
    """App-wide: right-click on a plain button offers "Add to quick actions"."""

    def __init__(self, quick: QuickActions):
        super().__init__(quick)
        self.quick = quick

    def eventFilter(self, obj, event) -> bool:
        if event.type() != QEvent.Type.ContextMenu or not isinstance(obj, QAbstractButton):
            return False
        if obj.contextMenuPolicy() != Qt.ContextMenuPolicy.DefaultContextMenu or isinstance(obj.window(), QMenu):
            return False  # buttons with their own menu (taskbar, Start, desktop) keep it
        app = window_key(obj)
        if app is None or not (obj.text() or obj.toolTip()):
            return False
        text, tip = obj.text(), obj.toolTip()
        name = (tip.split("\n")[0].split("  (")[0] if tip else "") or text
        entry = {"type": "button", "app": app, "text": text, "tip": tip, "label": name[:40],
                 "glyph": text if _is_glyph(text) else Icons.LIGHTNING}
        menu = make_menu(obj)
        add_action(menu, Icons.LIGHTNING, tr("qa_add_button", name=name[:40]), lambda: self.quick.add(entry))
        menu.exec(event.globalPos())
        return True


# ===================================================================== the panel
class Tile(QAbstractButton):
    menu_requested = pyqtSignal(object)

    def __init__(self, glyph: str, text: str, on: bool | None, parent=None):
        super().__init__(parent)
        self.glyph, self.text_, self.on = glyph, text, on
        self._hover = False
        self.setFixedSize(84, 74)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(text)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self.menu_requested.emit(self.mapToGlobal(pos)))

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF((self.width() - 52) / 2, 4, 52, 40)
        if self.on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(Theme.accent()))
        else:
            p.setPen(ink(0.12))
            p.setBrush(ink(0.1 if self._hover else 0.055))
        p.drawRoundedRect(box, 10, 10)
        p.setPen(QColor("#FFFFFF") if self.on else QColor(Palette.text))
        p.setFont(icon_font(12))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, self.glyph)
        p.setPen(QColor(Palette.text_dim))
        p.setFont(ui_font(8.2))
        r = QRectF(2, 48, self.width() - 4, 24)
        p.drawText(r, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   p.fontMetrics().elidedText(self.text_, Qt.TextElideMode.ElideRight, int(r.width())))


class ActionCenter(QFrame):
    COLS = 4

    def __init__(self, ctl, host: QWidget):
        super().__init__(host)
        self.ctl = ctl
        self.quick: QuickActions = ctl.quick
        self.setObjectName("ActionCenter")
        self.setFixedWidth(400)
        drop_shadow(self, 40, 10, 150)
        self._t = 0.0
        self._anim = value_animation(self, 0.0, 1.0, 180, self._set_t, OUT, self._done)
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0)
        self.setGraphicsEffect(self._fx)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(label(tr("qa_title"), 11, QFont.Weight.DemiBold))
        head.addStretch(1)
        add = IconButton(Icons.ADD, tr("qa_add_more"), 30, 10)
        add.clicked.connect(self._pick)
        head.addWidget(add)
        lay.addLayout(head)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(4)
        lay.addWidget(self.grid_host)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {ink(0.1).name(QColor.NameFormat.HexArgb)};")
        lay.addWidget(sep)
        nh = QHBoxLayout()
        nh.addWidget(label(tr("qa_notifications"), 10, QFont.Weight.DemiBold))
        nh.addStretch(1)
        clear = button(tr("qa_clear"), role="Ghost")
        clear.clicked.connect(self.quick.clear_history)
        nh.addWidget(clear)
        lay.addLayout(nh)
        self.notes = QListWidget()
        self.notes.setObjectName("NotifList")
        self.notes.setWordWrap(True)
        self.notes.setFixedHeight(230)
        lay.addWidget(self.notes)
        self.empty = label(tr("qa_no_notifications"), 9, role="Faint")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty)
        self.quick.changed.connect(self._fill_tiles)
        self.quick.notified.connect(self._fill_notes)
        host.installEventFilter(self)
        self.hide()

    # ---------------------------------------------------------------- content
    def _fill_tiles(self) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.hide()  # gone at once (deleteLater happens later and would overlap the new tiles)
                w.deleteLater()
        entries = [e for e in self.quick.entries()]
        shown = 0
        for i, e in enumerate(entries):
            if not self.quick.available(e):
                continue
            tile = Tile(self.quick.glyph(e), self.quick.title(e), self.quick.is_on(e))
            tile.clicked.connect(lambda _=False, e=e: self._run(e))
            tile.menu_requested.connect(lambda pos, i=i: self._tile_menu(i, pos))
            self.grid.addWidget(tile, shown // self.COLS, shown % self.COLS)
            shown += 1
        plus = Tile(Icons.ADD, tr("qa_add_more"), None)
        plus.clicked.connect(self._pick)
        self.grid.addWidget(plus, shown // self.COLS, shown % self.COLS)
        self.adjustSize()

    def _fill_notes(self) -> None:
        self.notes.clear()
        for n in reversed(self.quick.history):
            t = time.localtime(n["at"])
            item = QListWidgetItem(glyph_icon(KIND_GLYPH.get(n["kind"], Icons.INFO),
                                              {"success": Palette.success, "warning": Palette.warning,
                                               "error": Palette.danger}.get(n["kind"], Palette.info), 18),
                                   f"{n['text']}\n{format_hm(t.tm_hour, t.tm_min)}")
            self.notes.addItem(item)
        self.empty.setVisible(not self.quick.history)
        self.notes.setVisible(bool(self.quick.history))

    def _run(self, e: dict) -> None:
        if e.get("type") != "switch":
            self.close_panel()
        self.quick.run(e)
        if e.get("type") == "switch":
            QTimer.singleShot(150, self._fill_tiles)

    def _tile_menu(self, index: int, pos) -> None:
        menu = make_menu(self)
        add_action(menu, Icons.FORWARD if not is_rtl() else Icons.BACK, tr("qa_move_later"), lambda: self.quick.move(index, 1))
        add_action(menu, Icons.BACK if not is_rtl() else Icons.FORWARD, tr("qa_move_earlier"), lambda: self.quick.move(index, -1))
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("qa_remove"), lambda: self.quick.remove(index), danger=True)
        add_action(menu, Icons.UNDO, tr("qa_reset"), self.quick.reset)
        menu.exec(pos)

    def _pick(self) -> None:
        self.close_panel()
        QuickPicker(self.ctl, self.ctl.window).exec()

    # ------------------------------------------------------------- open / close
    def toggle(self) -> None:
        if self.isVisible() and self._anim.direction() == self._anim.Direction.Forward:
            self.close_panel()
        else:
            self.open_panel()

    def open_panel(self) -> None:
        self._fill_tiles()
        self._fill_notes()
        self.quick.unseen = 0
        self.quick.notified.emit()
        self.show()
        self.raise_()
        QApplication.instance().installEventFilter(self)
        self._anim.stop()
        self._anim.setDirection(self._anim.Direction.Forward)
        self._anim.start()

    def close_panel(self) -> None:
        if not self.isVisible():
            return
        self._anim.stop()
        self._anim.setDirection(self._anim.Direction.Backward)
        self._anim.start()

    def _done(self) -> None:
        if self._anim.direction() == self._anim.Direction.Backward:
            self.hide()
            QApplication.instance().removeEventFilter(self)

    def _set_t(self, v) -> None:
        self._t = float(v)
        self._fx.setOpacity(self._t)
        self.place()

    def place(self) -> None:
        host = self.parentWidget()
        bar = getattr(self.ctl.window, "taskbar", None)
        top = bar is not None and bar.conf["position"] == "top"
        bar_h = bar.height() if bar is not None else 0
        self.adjustSize()
        x = 12 if is_rtl() else host.width() - self.width() - 12  # over the clock
        slide = int((1 - self._t) * 20)
        y = (bar_h + 10 - slide) if top else (host.height() - bar_h - self.height() - 10 + slide)
        self.move(x, y)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self.place()
        elif event.type() == QEvent.Type.MouseButtonPress and self.isVisible() and isinstance(obj, QWidget) \
                and not isinstance(obj.window(), QMenu):
            pos = event.globalPosition().toPoint()
            clock = getattr(getattr(self.ctl.window, "taskbar", None), "clock", None)
            on_clock = clock is not None and clock.rect().contains(clock.mapFromGlobal(pos))
            if not self.rect().contains(self.mapFromGlobal(pos)) and not on_clock:
                self.close_panel()
        return super().eventFilter(obj, event)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(ink(0.12))
        p.setBrush(QColor(Palette.surface_solid))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)


class QuickPicker(StyledDialog):
    """"+": every switch, app and command, searchable — pick to add it to the panel."""

    def __init__(self, ctl, parent=None):
        super().__init__(tr("qa_add_more"), parent, 480)
        self.ctl = ctl
        self.body.addWidget(label(tr("qa_picker_hint"), 9, role="Dim", wrap=True))
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("settings_search"))
        self.search.textChanged.connect(self._fill)
        self.body.addWidget(self.search)
        self.list = QListWidget()
        self.list.setMinimumHeight(380)
        self.list.itemActivated.connect(self._chosen)
        self.list.itemClicked.connect(self._chosen)
        self.body.addWidget(self.list)
        from app.ui import apps as app_registry
        q = ctl.quick
        self.options = [({"type": "switch", "key": k}, f"{tr('qa_kind_switch')}") for k in SWITCHES]
        self.options += [({"type": "app", "key": a.key}, tr("qa_kind_app")) for a in app_registry.all_apps(ctl)
                         if a.kind != "action"]
        self.options += [({"type": "command", "key": k}, tr("qa_kind_command")) for _g, k, _a in ctl.command_list()]
        self._fill("")

    def _fill(self, text: str) -> None:
        q = self.ctl.quick
        self.list.clear()
        needle = text.strip().lower()
        have = q.entries()
        for e, kind in self.options:
            title = q.title(e)
            if needle and needle not in title.lower():
                continue
            mark = "  ✓" if any(_same(e, h) for h in have) else ""
            item = QListWidgetItem(glyph_icon(q.glyph(e), Theme.accent(), 20), f"{title}{mark}    ·  {kind}")
            item.setData(Qt.ItemDataRole.UserRole, e)
            self.list.addItem(item)

    def _chosen(self, item) -> None:
        self.ctl.quick.add(item.data(Qt.ItemDataRole.UserRole))
        self._fill(self.search.text())
