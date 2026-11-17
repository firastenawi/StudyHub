"""
Home = Dashboard + Launcher on one screen (the "desktop" of Study Hub).

Layout
  ┌──────── Hero (greeting, clock, date) ───────┬──── Quote ────┐
  ├── stat ──┬── stat ──┬── stat ──┬── stat ────┴───────────────┤
  ├──────────── Launcher (apps / sites / folders) ──┬── Tasks ──┤
  │                                                 ├─ Classes ─┤
  │                                                 ├─ Inbox ───┤
  └─────────────────────────────────────────────────┴───────────┘
Right-click anywhere for quick actions; drop files, folders or links from the
browser onto the page to pin them.
"""
from __future__ import annotations

from datetime import date, datetime

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import (QAbstractButton, QButtonGroup, QHBoxLayout, QLineEdit, QPushButton,
                             QVBoxLayout, QWidget)

from app.core.i18n import (format_hhmm, format_hm, format_long_date, format_range, greeting_key, is_rtl, language,
                           ltr, meridiem, relative_time, tr, uses_12h)
from app.core.launcher import normalize_url
from app.ui.animations import OUT, OUT_BACK, dispose, stagger_in, value_animation
from app.ui.dialogs.quick_note import SOURCE_GLYPHS
from app.ui.pages.base import Page
from app.ui.theme import Icons, Palette, Theme, icon_font, qcolor, ui_font, ink
from app.ui.widgets.common import (Card, CountUpLabel, EmptyState, GlyphLabel, GlyphPlate, IconButton,
                                   add_action, add_submenu, label, make_menu)
from app.ui.widgets.tasks import PRIORITY_COLORS, CheckCircle
from app.ui.widgets.tiles import FlowLayout, LauncherTile


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            dispose(item.widget())
        elif item.layout():
            _clear_layout(item.layout())


# ===================================================================== hero
class HeroCard(Card):
    def __init__(self, ctl):
        super().__init__(padding=28)
        self.ctl = ctl
        self.setMinimumHeight(220)
        self.root.setSpacing(4)
        self.greeting = label("", 13, QFont.Weight.Medium, "Dim")
        self.root.addWidget(self.greeting)

        clock_row = QHBoxLayout()
        clock_row.setSpacing(6)
        self.clock = label("", 50, QFont.Weight.DemiBold)
        self.seconds = label("", 16, QFont.Weight.Medium, "Accent")
        self.ampm = label("", 16, QFont.Weight.DemiBold, "Dim")  # ص / م in 12-hour mode
        clock_row.addWidget(self.clock)
        clock_row.addWidget(self.seconds, 0, Qt.AlignmentFlag.AlignBottom)
        clock_row.addWidget(self.ampm, 0, Qt.AlignmentFlag.AlignBottom)
        clock_row.addStretch(1)
        self.root.addLayout(clock_row)

        self.date = label("", 12, role="Dim")
        self.root.addWidget(self.date)
        self.root.addStretch(1)
        self.summary = label("", 10.5, wrap=True)
        self.root.addWidget(self.summary)

        self._timer = QTimer(self, timeout=self._tick)
        self._timer.start(1000)
        self._tick()

    def _tick(self):
        now = datetime.now()
        self.greeting.setText(tr(greeting_key(now.hour)))
        self.clock.setText(format_hm(now.hour, now.minute, suffix=False))
        self.seconds.setText(now.strftime(":%S"))
        self.ampm.setText(meridiem(now.hour) if uses_12h() else "")
        self.ampm.setVisible(uses_12h())
        self.date.setText(format_long_date(now.date()))

    def set_summary(self, tasks: int, classes: int):
        self.summary.setText(tr("hero_summary", tasks=tasks, classes=classes))

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QPainterPath()
        clip.addRoundedRect(r, 20, 20)
        p.setClipPath(clip)
        corner = r.topLeft() if is_rtl() else r.topRight()
        glow = QRadialGradient(corner, r.height() * 1.25)
        glow.setColorAt(0, qcolor(Theme.accent(), 0.32))
        glow.setColorAt(0.5, qcolor(Theme.accent2(), 0.07))
        glow.setColorAt(1, qcolor(Theme.accent(), 0.0))
        p.fillRect(r, glow)
        p.setPen(QPen(ink(0.05), 1))  # decorative orbit rings
        p.setBrush(Qt.BrushStyle.NoBrush)
        for radius in (70, 120, 170):
            p.drawEllipse(corner, radius, radius)


class QuoteCard(Card):
    def __init__(self, ctl):
        super().__init__(tr("quote_title"), Icons.LIGHTBULB)
        self.ctl = ctl
        self.setMinimumHeight(220)
        refresh = IconButton(Icons.REFRESH, tr("new_quote"), 30, 10)
        refresh.clicked.connect(self.next_quote)
        self.add_header_widget(refresh)
        self.text = label("", 14, QFont.Weight.Medium, wrap=True)
        self.author = label("", 10, role="Dim")
        self.root.addStretch(1)
        self.root.addWidget(self.text)
        self.root.addWidget(self.author)
        self.root.addStretch(1)
        self._fade = 1.0
        self._anim = value_animation(self, 1.0, 0.0, 180, self._set_fade, OUT, self._swap)
        self._pending = None
        self._show(ctl.db.random_quote())

    def _show(self, quote):
        if not quote:
            return
        ar = language() == "ar"
        self.text.setText(f"“{quote['text_ar' if ar else 'text_en']}”")
        author = quote["author_ar" if ar else "author_en"]
        self.author.setText(f"— {author}" if author else "")

    def next_quote(self):
        self._pending = self.ctl.db.random_quote()
        self._anim.setDirection(self._anim.Direction.Forward)
        self._anim.start()

    def _set_fade(self, v):
        self._fade = float(v)
        alpha = int(255 * self._fade)
        self.text.setStyleSheet(f"color: rgba(232,235,244,{alpha});")
        self.author.setStyleSheet(f"color: rgba(140,149,168,{alpha});")

    def _swap(self):
        if self._anim.direction() == self._anim.Direction.Forward:
            self._show(self._pending)
            self._anim.setDirection(self._anim.Direction.Backward)
            self._anim.start()


class StatCard(Card):
    def __init__(self, glyph: str, color: str, title: str, on_click=None):
        super().__init__(padding=18)
        self.setProperty("hoverable", True)
        self.setMinimumHeight(104)
        self.on_click = on_click
        if on_click:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(GlyphPlate(glyph, color, 46))
        col = QVBoxLayout()
        col.setSpacing(0)
        self.value = CountUpLabel(21)
        col.addWidget(self.value)
        self.title = label(title, 9.5, role="Dim")
        col.addWidget(self.title)
        self.sub = label("", 8.8, role="Faint")
        col.addWidget(self.sub)
        row.addLayout(col, 1)
        self.root.addLayout(row)

    def set_number(self, value: int, suffix: str = "", sub: str = ""):
        self.value.set_value(value, suffix)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))

    def set_text(self, text: str, sub: str = ""):
        self.value._anim.stop()
        self.value.setText(text)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))

    def mouseReleaseEvent(self, e):
        if self.on_click and e.button() == Qt.MouseButton.LeftButton:
            self.on_click()


# ================================================================= launcher
class LauncherCard(Card):
    def __init__(self, ctl, page):
        super().__init__(tr("launcher_title"), Icons.APPS)
        self.ctl, self.page = ctl, page
        self.group_filter: int | None = None
        self._drop_highlight = False
        self.setMinimumHeight(320)
        add = IconButton(Icons.ADD, tr("add_item"), 32, 11)
        add.clicked.connect(lambda: self.show_add_menu(add.mapToGlobal(add.rect().bottomLeft())))
        self.add_header_widget(add)
        desktop = IconButton(Icons.DESKTOP, tr("open_desktop"), 32, 11)
        desktop.clicked.connect(lambda: page.main.go_to("desktop"))
        self.add_header_widget(desktop)

        self.chips = QHBoxLayout()
        self.chips.setSpacing(8)
        self.root.addLayout(self.chips)
        self.tiles_host = QWidget()
        self.flow = FlowLayout(self.tiles_host, spacing=6)
        self.root.addWidget(self.tiles_host)
        self.root.addStretch(1)
        self.root.addWidget(label(tr("launcher_hint"), 8.8, role="Faint", wrap=True))
        self.refresh(animate=False)

    def refresh(self, animate: bool = True):
        groups = self.ctl.db.list_groups()
        if self.group_filter not in {g["id"] for g in groups}:
            self.group_filter = None
        _clear_layout(self.chips)
        self._chip_group = QButtonGroup(self)
        for gid, name in [(None, tr("all"))] + [(g["id"], g["name"]) for g in groups]:
            chip = QPushButton(name)
            chip.setObjectName("Chip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFont(ui_font(9.5))
            chip.setChecked(gid == self.group_filter)
            chip.clicked.connect(lambda _=False, g=gid: self._filter(g))
            if gid is not None:
                chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                chip.customContextMenuRequested.connect(
                    lambda pos, g=gid, c=chip: self._group_menu(g, c.mapToGlobal(pos)))
            self._chip_group.addButton(chip)
            self.chips.addWidget(chip)
        new_group = QPushButton("+")
        new_group.setObjectName("Chip")
        new_group.setToolTip(tr("new_group"))
        new_group.setCursor(Qt.CursorShape.PointingHandCursor)
        new_group.clicked.connect(lambda: self.ctl.add_group(self))
        self.chips.addWidget(new_group)
        self.chips.addStretch(1)

        _clear_layout(self.flow)
        tiles = []
        for item in self.ctl.db.list_items(self.group_filter):
            tile = LauncherTile(item, self.tiles_host)
            tile.activated.connect(self.ctl.launch_item)
            tile.menu_requested.connect(self._item_menu)
            self.flow.addWidget(tile)
            tiles.append(tile)
        add_tile = LauncherTile(None, self.tiles_host, tr("add"))
        add_tile.activated.connect(lambda _id: self.ctl.add_item_dialog(group_id=self.group_filter))
        self.flow.addWidget(add_tile)
        tiles.append(add_tile)
        self.tiles_host.updateGeometry()
        if animate:
            self.animate_tiles(tiles)

    def animate_tiles(self, tiles=None):
        tiles = tiles or [self.flow.itemAt(i).widget() for i in range(self.flow.count())]
        for index, tile in enumerate(tiles):
            tile.appear(index * 24)

    def _filter(self, group_id):
        self.group_filter = group_id
        self.refresh()

    # ---------------------------------------------------------------- menus
    def contextMenuEvent(self, e):
        self.show_add_menu(e.globalPos())
        e.accept()

    def show_add_menu(self, pos):
        ctl, g = self.ctl, self.group_filter
        menu = make_menu(self)
        add_action(menu, Icons.APPS, tr("add_app"), lambda: ctl.add_item_dialog("app", group_id=g))
        add_action(menu, Icons.GLOBE, tr("add_website"), lambda: ctl.add_item_dialog("url", group_id=g))
        add_action(menu, Icons.FOLDER, tr("add_folder"), lambda: ctl.add_item_dialog("folder", group_id=g))
        add_action(menu, Icons.DOCUMENT, tr("add_file"), lambda: ctl.add_item_dialog("file", group_id=g))
        menu.addSeparator()
        add_action(menu, Icons.LINK, tr("link_from_clipboard"), lambda: ctl.clipboard_link(group_id=g),
                   ctl.hotkey_text("clipboard_link"))
        add_action(menu, Icons.NOTES, tr("quick_note"), ctl.open_quick_note, ctl.hotkey_text("quick_note"))
        menu.addSeparator()
        add_action(menu, Icons.TAG, tr("new_group"), lambda: ctl.add_group(self))
        menu.exec(pos)

    def _item_menu(self, item_id: int, pos):
        ctl = self.ctl
        item = ctl.db.get_item(item_id)
        if not item:
            return
        menu = make_menu(self)
        add_action(menu, Icons.CHEVRON, tr("open"), lambda: ctl.launch_item(item_id))
        if item["kind"] == "app":
            add_action(menu, Icons.ADMIN, tr("run_as_admin"), lambda: ctl.launch_item(item_id, admin=True))
        if item["kind"] in ("app", "file", "folder"):
            add_action(menu, Icons.OPEN_FOLDER, tr("open_location"), lambda: ctl.reveal_item(item_id))
        add_action(menu, Icons.COPY, tr("copy_link") if item["kind"] == "url" else tr("copy_path"),
                   lambda: ctl.copy_text(item["target"]))
        menu.addSeparator()
        add_action(menu, Icons.EDIT, tr("edit"), lambda: ctl.edit_item(item_id, self))
        move = add_submenu(menu, Icons.MOVE, tr("move_to_group"))
        add_action(move, None, tr("no_group"), lambda: ctl.move_item(item_id, None))
        for group in ctl.db.list_groups():
            act = add_action(move, Icons.ACCEPT if group["id"] == item["group_id"] else None, group["name"],
                             lambda gid=group["id"]: ctl.move_item(item_id, gid))
            act.setEnabled(group["id"] != item["group_id"])
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("remove"), lambda: ctl.remove_item(item_id), danger=True)
        menu.exec(pos)

    def _group_menu(self, group_id: int, pos):
        menu = make_menu(self)
        add_action(menu, Icons.EDIT, tr("rename"), lambda: self.ctl.rename_group(group_id, self))
        add_action(menu, Icons.DELETE, tr("delete_group"), lambda: self.ctl.delete_group(group_id, self), danger=True)
        menu.exec(pos)

    # ------------------------------------------------------------ drop glow
    def set_drop_highlight(self, on: bool):
        self._drop_highlight = on
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._drop_highlight:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor(Theme.accent()), 2, Qt.PenStyle.DashLine))
            p.setBrush(qcolor(Theme.accent(), 0.06))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(3, 3, -3, -3), 18, 18)


# ==================================================================== tasks
class TaskRow(QWidget):
    def __init__(self, task: dict, ctl, parent=None):
        super().__init__(parent)
        self.task, self.ctl = task, ctl
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 4, 2, 4)
        lay.setSpacing(12)
        self.check = CheckCircle(PRIORITY_COLORS.get(task["priority"], Palette.info))
        self.check.toggled.connect(self._toggled)
        lay.addWidget(self.check)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.title = label(task["title"], 10.2)
        col.addWidget(self.title)
        meta = []
        if task["due_date"] and task["due_date"] < date.today().isoformat():
            meta.append(tr("overdue"))
        if task.get("subject_name"):
            meta.append(task["subject_name"])
        if meta:
            col.addWidget(label(" · ".join(meta), 8.6, role="Faint"))
        lay.addLayout(col, 1)

    def _toggled(self, done: bool):
        font = self.title.font()
        font.setStrikeOut(done)
        self.title.setFont(font)
        self.title.setObjectName("Faint" if done else "")
        self.title.style().polish(self.title)
        if done:  # give the check animation time to play before the row disappears
            QTimer.singleShot(700, lambda: self.check.isChecked() and self.ctl.set_task_done(self.task["id"], True))

    def contextMenuEvent(self, e):
        ctl, tid = self.ctl, self.task["id"]
        menu = make_menu(self)
        prio = add_submenu(menu, Icons.FLAG, tr("priority"))
        for level, key in ((1, "priority_high"), (2, "priority_medium"), (3, "priority_low")):
            add_action(prio, Icons.ACCEPT if self.task["priority"] == level else None, tr(key),
                       lambda lv=level: ctl.set_task_priority(tid, lv))
        add_action(menu, Icons.CALENDAR_DAY, tr("move_to_tomorrow"), lambda: ctl.postpone_task(tid))
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("delete"), lambda: ctl.delete_task(tid), danger=True)
        menu.exec(e.globalPos())
        e.accept()


class TasksCard(Card):
    MAX_ROWS = 6

    def __init__(self, ctl):
        super().__init__(tr("tasks_title"), Icons.CHECKLIST)
        self.ctl = ctl
        self.badge = label("0", 8.8, QFont.Weight.DemiBold, "Badge")
        self.add_header_widget(self.badge)
        self.input = QLineEdit()
        self.input.setPlaceholderText(tr("task_placeholder"))
        self.input.returnPressed.connect(self._add)
        self.root.addWidget(self.input)
        self.list = QVBoxLayout()
        self.list.setSpacing(2)
        self.root.addLayout(self.list)
        self.refresh()

    def _add(self):
        text = self.input.text().strip()
        if text:
            self.ctl.add_task(text)
            self.input.clear()

    def refresh(self):
        _clear_layout(self.list)
        tasks = self.ctl.db.tasks_for_today()
        self.badge.setText(str(len(tasks)))
        if not tasks:
            self.list.addWidget(EmptyState(Icons.ACCEPT, tr("tasks_empty")))
            return
        for task in tasks[:self.MAX_ROWS]:
            self.list.addWidget(TaskRow(task, self.ctl))
        if len(tasks) > self.MAX_ROWS:
            self.list.addWidget(label(tr("and_more", n=len(tasks) - self.MAX_ROWS), 8.8, role="Faint"))


# ================================================================== classes
class UpcomingCard(Card):
    def __init__(self, ctl):
        super().__init__(tr("classes_title"), Icons.STUDY)
        self.ctl = ctl
        self.list = QVBoxLayout()
        self.list.setSpacing(8)
        self.root.addLayout(self.list)
        self.refresh()

    def refresh(self):
        _clear_layout(self.list)
        classes = self.ctl.db.upcoming_classes()
        if not classes:
            self.list.addWidget(EmptyState(Icons.CALENDAR_DAY, tr("classes_empty")))
            return
        for slot in classes[:4]:
            row = QHBoxLayout()
            row.setSpacing(12)
            bar = QWidget()
            bar.setFixedSize(4, 36)
            bar.setStyleSheet(f"background: {slot['subject_color']}; border-radius: 2px;")
            row.addWidget(bar)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(label(slot["subject_name"], 10.2, QFont.Weight.DemiBold))
            meta = format_range(slot["start_time"], slot["end_time"])
            if slot["location"]:
                meta += f"  ·  {slot['location']}"
            col.addWidget(label(meta, 8.8, role="Dim"))
            row.addLayout(col, 1)
            self.list.addLayout(row)


# ==================================================================== inbox
def _note_preview(note: dict) -> str:
    """Title, or the first non-empty line of the content."""
    text = (note["title"] or note["content"]).strip()
    return text.splitlines()[0][:160] if text else "…"


class NoteRow(QWidget):
    def __init__(self, note: dict, ctl, parent=None):
        super().__init__(parent)
        self.note, self.ctl = note, ctl
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = 0.0
        self._anim = value_animation(self, 0.0, 1.0, 150, self._set, OUT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(12)
        lay.addWidget(GlyphLabel(SOURCE_GLYPHS.get(note["source"], Icons.NOTES), 11, Palette.text_dim))
        col = QVBoxLayout()
        col.setSpacing(1)
        self.preview = _note_preview(note)
        self.text = label(self.preview, 10)
        col.addWidget(self.text)
        col.addWidget(label(relative_time(note["updated_at"]), 8.4, role="Faint"))
        lay.addLayout(col, 1)

    def _set(self, v):
        self._hover = float(v)
        self.update()

    def enterEvent(self, e):
        self._anim.setDirection(self._anim.Direction.Forward)
        self._anim.start()

    def leaveEvent(self, e):
        self._anim.setDirection(self._anim.Direction.Backward)
        self._anim.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.text.setText(self.text.fontMetrics().elidedText(
            self.preview, Qt.TextElideMode.ElideRight, max(60, self.width() - 70)))

    def paintEvent(self, _):
        if self._hover > 0:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink(0.05 * self._hover))
            p.drawRoundedRect(QRectF(self.rect()), 12, 12)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.ctl.open_notebook(note_id=self.note["id"])

    def contextMenuEvent(self, e):
        ctl, nid = self.ctl, self.note["id"]
        menu = make_menu(self)
        add_action(menu, Icons.NOTES, tr("open_in_notebook"), lambda: ctl.open_notebook(note_id=nid))
        add_action(menu, Icons.EDIT, tr("quick_edit"), lambda: ctl.open_quick_note(note_id=nid))
        add_action(menu, Icons.COPY, tr("copy_text"), lambda: ctl.copy_text(self.note["content"]))
        add_action(menu, Icons.ACCEPT, tr("mark_filed"), lambda: ctl.file_note(nid))
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("delete"), lambda: ctl.delete_note(nid, self), danger=True)
        menu.exec(e.globalPos())
        e.accept()


class InboxCard(Card):
    def __init__(self, ctl):
        super().__init__(tr("inbox_title"), Icons.INBOX)
        self.ctl = ctl
        self.badge = label("0", 8.8, QFont.Weight.DemiBold, "Badge")
        self.add_header_widget(self.badge)
        add = IconButton(Icons.ADD, tr("quick_note"), 30, 10)
        add.clicked.connect(lambda: ctl.open_quick_note())
        self.add_header_widget(add)
        self.list = QVBoxLayout()
        self.list.setSpacing(0)
        self.root.addLayout(self.list)
        self.hint = label(tr("inbox_hint", hotkey=ctl.hotkey_text("capture_selection")), 8.6, role="Faint", wrap=True)
        self.root.addWidget(self.hint)
        self.refresh()

    def refresh(self):
        _clear_layout(self.list)
        self.badge.setText(str(self.ctl.db.count_inbox()))
        notes = self.ctl.db.recent_notes(5)
        if not notes:
            self.list.addWidget(EmptyState(Icons.NOTES, tr("inbox_empty", hotkey=self.ctl.hotkey_text("quick_note"))))
        for note in notes:
            self.list.addWidget(NoteRow(note, self.ctl))


# ===================================================================== page
class HomePage(Page):
    def __init__(self, ctl, main_window):
        super().__init__(ctl, main_window)
        self.setAcceptDrops(True)
        self._intro_done = False
        content = QWidget()
        grid = QVBoxLayout(content)
        grid.setContentsMargins(30, 26, 30, 30)
        grid.setSpacing(18)

        row1 = QHBoxLayout()
        row1.setSpacing(18)
        self.hero = HeroCard(ctl)
        self.quote = QuoteCard(ctl)
        row1.addWidget(self.hero, 3)
        row1.addWidget(self.quote, 2)
        grid.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(18)
        self.stat_tasks = StatCard(Icons.CHECKLIST, "#A78BFA", tr("stat_tasks"), lambda: ctl.open_study_tab("tasks"))
        self.stat_class = StatCard(Icons.CLOCK, "#22D3EE", tr("stat_next_class"),
                                   lambda: ctl.open_study_tab("timetable"))
        self.stat_focus = StatCard(Icons.TIMER, "#34D399", tr("stat_focus"), lambda: ctl.open_study_tab("focus"))
        self.stat_inbox = StatCard(Icons.INBOX, "#FBBF24", tr("stat_inbox"), lambda: ctl.open_notebook())
        self.stats = [self.stat_tasks, self.stat_class, self.stat_focus, self.stat_inbox]
        for card in self.stats:
            row2.addWidget(card, 1)
        grid.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(18)
        self.launcher = LauncherCard(ctl, self)
        row3.addWidget(self.launcher, 3)
        side = QVBoxLayout()
        side.setSpacing(18)
        self.tasks = TasksCard(ctl)
        self.classes = UpcomingCard(ctl)
        self.inbox = InboxCard(ctl)
        side.addWidget(self.tasks)
        side.addWidget(self.classes)
        side.addWidget(self.inbox)
        side.addStretch(1)
        row3.addLayout(side, 2)
        grid.addLayout(row3, 1)

        self.set_scroll_content(content)
        self.cards = [self.hero, self.quote, *self.stats, self.launcher, self.tasks, self.classes, self.inbox]
        self.listen(ctl.data_changed, self.refresh)
        self.refresh("")

    # ----------------------------------------------------------- lifecycle
    def on_shown(self):
        if not self._intro_done:
            self._intro_done = True
            stagger_in(self.cards, step=55)
            self.launcher.animate_tiles()
            for card in self.stats:
                card.value.set_value(card.value._value, card.value._suffix)  # replay the count-up

    def refresh(self, what: str = ""):
        db = self.ctl.db
        if what in ("", "launcher", "logos"):
            self.launcher.refresh(animate=what == "launcher")  # new logos swap in quietly
        if what in ("", "tasks"):
            self.tasks.refresh()
        if what in ("", "notes"):
            self.inbox.refresh()
        if what in ("", "schedule", "subjects", "settings"):
            self.classes.refresh()
        open_tasks = db.count_open_tasks_today()
        classes = db.upcoming_classes()
        self.stat_tasks.set_number(open_tasks)
        if classes:
            nxt = classes[0]
            self.stat_class.set_text(format_hhmm(nxt["start_time"]), nxt["subject_name"])
        else:
            self.stat_class.set_text("—", tr("no_more_classes"))
        self.stat_focus.set_number(db.focus_minutes_today(), " " + tr("minutes_short"))
        self.stat_inbox.set_number(db.count_inbox())
        self.hero.set_summary(open_tasks, len(classes))

    # ------------------------------------------------------- quick actions
    def contextMenuEvent(self, e):
        ctl = self.ctl
        menu = make_menu(self)
        add_action(menu, Icons.NOTES, tr("quick_note"), ctl.open_quick_note, ctl.hotkey_text("quick_note"))
        add_action(menu, Icons.PASTE, tr("note_from_clipboard"), ctl.clipboard_note)
        add_action(menu, Icons.LINK, tr("link_from_clipboard"), ctl.clipboard_link, ctl.hotkey_text("clipboard_link"))
        menu.addSeparator()
        add_action(menu, Icons.APPS, tr("add_app"), lambda: ctl.add_item_dialog("app"))
        add_action(menu, Icons.GLOBE, tr("add_website"), lambda: ctl.add_item_dialog("url"))
        menu.addSeparator()
        add_action(menu, Icons.SEARCH, tr("open_palette"), self.main.open_palette, "Ctrl+Space")
        add_action(menu, Icons.LIGHTBULB, tr("new_quote"), self.quote.next_quote)
        menu.exec(e.globalPos())

    # ---------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() or (md.hasText() and normalize_url(md.text())):
            e.acceptProposedAction()
            self.launcher.set_drop_highlight(True)

    def dragLeaveEvent(self, e):
        self.launcher.set_drop_highlight(False)

    def dropEvent(self, e):
        self.launcher.set_drop_highlight(False)
        md = e.mimeData()
        group = self.launcher.group_filter
        added = 0
        if md.hasUrls():
            for url in md.urls():
                if url.isLocalFile():
                    added += self.ctl.add_path(url.toLocalFile(), group_id=group, notify=False)
                else:
                    added += self.ctl.add_url(url.toString(), group_id=group, notify=False)
        elif md.hasText():
            added += self.ctl.add_url(md.text().strip(), group_id=group, notify=False)
        if added:
            self.ctl.notify(tr("pinned_n", n=added), "success")
        e.acceptProposedAction()
