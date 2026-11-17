"""
Start menu (Windows 11 style): search at the top, pinned apps as a grid, "All apps" as a list grouped by kind,
and a strip at the bottom that explains — in one simple sentence — whatever app the mouse is on.
Typing searches the apps; Enter opens the first one; "Search everything" hands the text to the palette.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (QAbstractButton, QApplication, QMenu, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLineEdit,
                             QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from app.config import APP_NAME, VERSION
from app.core import i18n
from app.core.i18n import is_rtl, tr
from app.ui import apps as app_registry
from app.ui.animations import OUT, value_animation
from app.ui.dialogs.base import drop_shadow
from app.ui.theme import Icons, Palette, Theme, ink, ui_font
from app.ui.widgets.common import GlyphLabel, IconButton, add_action, button, label, make_menu

GRID_COLS = 6


class AppTile(QAbstractButton):
    """Pinned app: tile + name under it."""
    hovered = pyqtSignal(object)
    menu_requested = pyqtSignal(object, object)

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self._hover = False
        self.setFixedSize(92, 88)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(info.description)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self.menu_requested.emit(self.info, self.mapToGlobal(pos)))

    def enterEvent(self, e):
        self._hover = True
        self.hovered.emit(self.info)
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hover or self.hasFocus():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink(0.07))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 10, 10)
        s = 40
        p.drawPixmap(int((self.width() - s) / 2), 10, s, s, self.info.pixmap(s * 2))
        p.setPen(QColor(Palette.text))
        p.setFont(ui_font(8.8))
        r = QRectF(4, 54, self.width() - 8, 30)
        p.drawText(r, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   p.fontMetrics().elidedText(self.info.title, Qt.TextElideMode.ElideRight, int(r.width())))


class AppRow(QAbstractButton):
    """All apps / search result: tile, name and the one-line explanation under it."""
    hovered = pyqtSignal(object)
    menu_requested = pyqtSignal(object, object)

    def __init__(self, info, parent=None, text: str | None = None, glyph: str | None = None):
        super().__init__(parent)
        self.info = info
        self.text_, self.glyph = text, glyph
        self._hover = False
        self.setFixedHeight(54)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if info is not None:
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.customContextMenuRequested.connect(lambda pos: self.menu_requested.emit(self.info, self.mapToGlobal(pos)))

    def enterEvent(self, e):
        self._hover = True
        if self.info is not None:
            self.hovered.emit(self.info)
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self._hover or self.hasFocus():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink(0.07))
            p.drawRoundedRect(r, 9, 9)
        rtl = is_rtl()
        s = 34
        x = r.right() - s - 8 if rtl else r.left() + 8
        if self.info is not None:
            p.drawPixmap(int(x), int((self.height() - s) / 2), s, s,
                         self.info.pixmap(s * 2))
        else:
            p.setPen(QColor(Theme.accent()))
            from app.ui.theme import icon_font
            p.setFont(icon_font(13))
            p.drawText(QRectF(x, 0, s, self.height()), Qt.AlignmentFlag.AlignCenter, self.glyph or Icons.SEARCH)
        tx = QRectF(r.left() + 8, 0, r.width() - s - 26, self.height()) if rtl else QRectF(x + s + 12, 0, r.right() - x - s - 20, self.height())
        align = Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft
        title = self.text_ if self.info is None else self.info.title
        p.setPen(QColor(Palette.text))
        p.setFont(ui_font(10, QFont.Weight.DemiBold))
        if self.info is None:
            p.drawText(tx, align | Qt.AlignmentFlag.AlignVCenter, p.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(tx.width())))
            return
        p.drawText(QRectF(tx.x(), 8, tx.width(), 20), align | Qt.AlignmentFlag.AlignVCenter, title)
        p.setPen(QColor(Palette.text_dim))
        p.setFont(ui_font(8.6))
        p.drawText(QRectF(tx.x(), 27, tx.width(), 20), align | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(self.info.description, Qt.TextElideMode.ElideRight, int(tx.width())))


class StartMenu(QFrame):
    """Overlay panel inside the shell; opens above (or under) the taskbar."""

    def __init__(self, ctl, host: QWidget):
        super().__init__(host)
        self.ctl = ctl
        self.setObjectName("StartMenu")
        self.setFixedSize(700, 640)
        drop_shadow(self, 44, 10, 150)
        self._t = 0.0
        self._from_top = False
        self._anim = value_animation(self, 0.0, 1.0, 200, self._set_t, OUT, self._done)
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._fx.setOpacity(0)
        host.installEventFilter(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 0)
        lay.setSpacing(14)
        search = QHBoxLayout()
        search.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("StartSearch")
        self.search.setPlaceholderText(tr("start_search_ph"))
        self.search.setFont(ui_font(10.5))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(40)
        self.search.textChanged.connect(self._search)
        self.search.returnPressed.connect(self._open_first)
        self.search.installEventFilter(self)
        search.addWidget(GlyphLabel(Icons.SEARCH, 11, Palette.text_dim))
        search.addWidget(self.search, 1)
        lay.addLayout(search)

        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.pinned_page = QWidget()
        self.all_page = QWidget()
        self.results_page = QWidget()
        for page in (self.pinned_page, self.all_page, self.results_page):
            self.stack.addWidget(page)

        # pinned
        pl = QVBoxLayout(self.pinned_page)
        pl.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(label(tr("start_pinned"), 10.5, QFont.Weight.DemiBold))
        head.addStretch(1)
        all_btn = button(tr("start_all_apps") + ("  ‹" if is_rtl() else "  ›"), role="Ghost")
        all_btn.clicked.connect(lambda: self._show(self.all_page))
        head.addWidget(all_btn)
        pl.addLayout(head)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 4, 0, 0)
        self.grid.setHorizontalSpacing(4)
        self.grid.setVerticalSpacing(4)
        pl.addWidget(self.grid_host)
        pl.addStretch(1)

        # all apps
        al = QVBoxLayout(self.all_page)
        al.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(label(tr("start_all_apps"), 10.5, QFont.Weight.DemiBold))
        head.addStretch(1)
        back = button(("›  " if is_rtl() else "‹  ") + tr("back"), role="Ghost")
        back.clicked.connect(lambda: self._show(self.pinned_page))
        head.addWidget(back)
        al.addLayout(head)
        self.all_list = QVBoxLayout()
        self.all_list.setSpacing(1)
        al.addWidget(self._scroll(self.all_list), 1)

        # search results
        rl = QVBoxLayout(self.results_page)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(label(tr("start_results"), 10.5, QFont.Weight.DemiBold))
        self.result_list = QVBoxLayout()
        self.result_list.setSpacing(1)
        rl.addWidget(self._scroll(self.result_list), 1)

        # "what is this?" strip + footer
        self.explain = QFrame()
        self.explain.setObjectName("StartExplain")
        ex = QHBoxLayout(self.explain)
        ex.setContentsMargins(14, 10, 14, 10)
        ex.setSpacing(10)
        self.explain_icon = GlyphLabel(Icons.INFO, 11, Theme.accent())
        ex.addWidget(self.explain_icon)
        self.explain_text = label(tr("start_explain_hint"), 9.2, role="Dim", wrap=True)
        ex.addWidget(self.explain_text, 1)
        lay.addWidget(self.explain)

        foot = QFrame()
        foot.setObjectName("StartFooter")
        foot.setFixedHeight(62)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(4, 0, 0, 0)
        fl.addWidget(label(f"{APP_NAME}  ·  {VERSION}", 9.5, QFont.Weight.DemiBold))
        fl.addStretch(1)
        add_btn = button(tr("addg_title"), Icons.ADD, role="Ghost")
        add_btn.clicked.connect(lambda: (self.close_menu(), ctl.show_add_gallery()))
        fl.addWidget(add_btn)
        for glyph, tip, slot in ((Icons.SETTINGS, tr("nav_settings"), lambda: self._launch("settings")),
                                 (Icons.LOCK, tr("hk_lock_screen"), ctl.show_lock_screen)):
            b = IconButton(glyph, tip, 38, 11)
            b.clicked.connect(lambda _=False, s=slot: (self.close_menu(), s()))
            fl.addWidget(b)
        power = IconButton(Icons.POWER, tr("power_menu"), 38, 11)
        power.clicked.connect(lambda: self._power_menu(power))
        fl.addWidget(power)
        lay.addWidget(foot)
        self.hide()

    @staticmethod
    def _scroll(inner: QVBoxLayout) -> QScrollArea:
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 6, 0)
        v.addLayout(inner)
        v.addStretch(1)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.viewport().setAutoFillBackground(False)
        host.setAutoFillBackground(False)
        area.setWidget(host)
        return area

    # ------------------------------------------------------------ content
    def _apps(self):
        return [a for a in app_registry.all_apps(self.ctl)]

    def _fill(self) -> None:
        for lay in (self.grid, self.all_list):
            while lay.count():
                w = lay.takeAt(0).widget()
                if w:
                    w.deleteLater()
        hidden = set(self.ctl.db.get_setting("start_hidden", []) or [])
        pinned = [a for a in self._apps() if a.key not in hidden]
        # programs / websites / files pinned from the desktop (right-click › Pin to Start)
        pinned += [i for i in (app_registry.find(self.ctl, k) for k in (self.ctl.db.get_setting("start_items", []) or [])) if i]
        for i, info in enumerate(pinned):
            tile = AppTile(info)
            self._wire(tile)
            self.grid.addWidget(tile, i // GRID_COLS, i % GRID_COLS)
        apps = self._apps()
        for group in app_registry.GROUPS:
            members = [a for a in apps if a.group == group]
            if not members:
                continue
            head = label(tr(f"start_group_{group}"), 9, QFont.Weight.DemiBold, role="Dim")
            head.setContentsMargins(8, 10, 8, 2)
            self.all_list.addWidget(head)
            for info in members:
                row = AppRow(info)
                self._wire(row)
                self.all_list.addWidget(row)

    def _wire(self, w) -> None:
        w.clicked.connect(lambda _=False, k=w.info.key: self._launch(k))
        w.hovered.connect(self._explain)
        w.menu_requested.connect(self._item_menu)

    def _explain(self, info) -> None:
        self.explain_icon.setText(info.glyph)
        self.explain_text.setText(f"<b>{info.title}</b> — {info.description}")

    def _search(self, text: str) -> None:
        q = text.strip().lower()
        if not q:
            self._show(self.pinned_page)
            return
        while self.result_list.count():
            w = self.result_list.takeAt(0).widget()
            if w:
                w.deleteLater()
        found = []
        for info in self._apps():
            hay = " ".join([info.title, info.description, i18n.tr_in("en", info.title_key),
                            i18n.tr_in("ar", info.title_key), info.key]).lower()
            if q in hay:
                found.append(info)
        for info in found[:8]:
            row = AppRow(info)
            self._wire(row)
            self.result_list.addWidget(row)
        everything = AppRow(None, text=tr("start_search_all", q=text.strip()), glyph=Icons.SEARCH)
        everything.clicked.connect(lambda: self._search_everything(text.strip()))
        self.result_list.addWidget(everything)
        self._found = found
        if found:
            self._explain(found[0])
        self._show(self.results_page)

    def _open_first(self) -> None:
        found = getattr(self, "_found", [])
        if self.search.text().strip() and found:
            self._launch(found[0].key)
        elif self.search.text().strip():
            self._search_everything(self.search.text().strip())

    def _search_everything(self, text: str) -> None:
        self.close_menu()
        self.ctl.window.open_palette(text)

    def _show(self, page) -> None:
        self.stack.setCurrentWidget(page)

    def _launch(self, key: str) -> None:
        self.close_menu()
        self.ctl.apps.open(key)

    def _item_menu(self, info, pos) -> None:
        menu = make_menu(self)
        add_action(menu, info.glyph, tr("start_open"), lambda: self._launch(info.key))
        add_action(menu, Icons.LIGHTNING, tr("qa_add"), lambda: self.ctl.quick.add({"type": "app", "key": info.key}))
        pinned = info.key in self.ctl.taskbar_conf()["pinned"]
        add_action(menu, Icons.PIN, tr("tb_unpin") if pinned else tr("tb_pin"),
                   lambda: self.ctl.set_taskbar_pinned(info.key, not pinned))
        if info.kind == "item":  # pinned from the desktop: unpin from Start
            add_action(menu, Icons.CLOSE, tr("start_unpin"), lambda: (self.ctl.set_start_item(info.key, False), self._fill()))
            menu.exec(pos)
            return
        hidden = set(self.ctl.db.get_setting("start_hidden", []) or [])
        if info.key in hidden:
            add_action(menu, Icons.ADD, tr("start_pin"), lambda: self._set_hidden(info.key, False))
        else:
            add_action(menu, Icons.CLOSE, tr("start_unpin"), lambda: self._set_hidden(info.key, True))
        menu.exec(pos)

    def _set_hidden(self, key: str, hide: bool) -> None:
        hidden = list(self.ctl.db.get_setting("start_hidden", []) or [])
        if hide and key not in hidden:
            hidden.append(key)
        elif not hide and key in hidden:
            hidden.remove(key)
        self.ctl.db.set_setting("start_hidden", hidden)
        self._fill()

    def _power_menu(self, anchor) -> None:
        menu = make_menu(self)
        add_action(menu, Icons.MINIMIZE, tr("hide_to_tray"), lambda: (self.close_menu(), self.ctl.window.hide()))
        add_action(menu, Icons.FULLSCREEN, tr("toggle_fullscreen"), lambda: (self.close_menu(), self.ctl.window.toggle_fullscreen()), "F11")
        menu.addSeparator()
        add_action(menu, Icons.POWER, tr("quit"), self.ctl.quit, danger=True)
        menu.exec(anchor.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 6)))

    # ------------------------------------------------------------ open / close
    def toggle(self) -> None:
        if self.isVisible() and self._anim.direction() == self._anim.Direction.Forward:
            self.close_menu()
        else:
            self.open_menu()

    def open_menu(self) -> None:
        self._fill()
        self.search.clear()
        self._show(self.pinned_page)
        self.explain_icon.setText(Icons.INFO)
        self.explain_text.setText(tr("start_explain_hint"))
        self.show()
        self.raise_()
        QApplication.instance().installEventFilter(self)
        self._anim.stop()
        self._anim.setDirection(self._anim.Direction.Forward)
        self._anim.start()
        self.search.setFocus()

    def close_menu(self) -> None:
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
        h = min(600, host.height() - bar_h - 24)
        self.setFixedHeight(max(420, h))
        align_center = bar is None or bar.conf["align"] == "center"
        if align_center:
            x = (host.width() - self.width()) // 2
        else:
            x = host.width() - self.width() - 12 if is_rtl() else 12
        slide = int((1 - self._t) * 26)
        y = (bar_h + 12 - slide) if top else (host.height() - bar_h - self.height() - 12 + slide)
        self.move(x, y)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self.place()
        elif event.type() == QEvent.Type.MouseButtonPress and self.isVisible() and isinstance(obj, QWidget)                 and obj.isWidgetType() and not isinstance(obj.window(), QMenu):
            pos = event.globalPosition().toPoint()
            start = getattr(getattr(self.ctl.window, "taskbar", None), "start_btn", None)
            on_start = start is not None and start.rect().contains(start.mapFromGlobal(pos))
            if not self.rect().contains(self.mapFromGlobal(pos)) and not on_start:
                self.close_menu()  # a click anywhere else closes the menu
        elif obj is self.search and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.close_menu()
            return True
        return super().eventFilter(obj, event)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        base = QColor(Palette.surface_solid)
        p.setPen(ink(0.12))
        p.setBrush(base)
        p.drawRoundedRect(r, 14, 14)
        foot_y = self.height() - 62
        clip = QPainterPath()
        clip.addRoundedRect(r, 14, 14)
        p.setClipPath(clip)
        p.fillRect(QRectF(0, foot_y, self.width(), 62), ink(0.035))
        p.setClipping(False)
        p.setPen(ink(0.08))
        p.drawLine(1, foot_y, self.width() - 1, foot_y)
