"""
Notebook.

Side panel  : navigation tree (Inbox, All, Pinned, Tasks, nested Folders, Subjects, #Tags, Templates,
              Trash) + the note list (sort, multi-select, drag notes onto a folder)
Main area   : tabs of open notes, then the editor with live preview — or a card gallery, or every open
              "- [ ]" task across the notebook

Writing     : "/" menu, "[[" note links (+ backlinks), #tags, smart paste & attachments, outline,
              find & replace (Ctrl+F / Ctrl+H), focus mode (F8), Ctrl+Enter ticks a task
Getting around: Ctrl+P quick switcher, Ctrl+D today's note, Ctrl+W close tab, Ctrl+Tab next tab,
              "Open beside" shows a second note in the preview pane
Safety      : trash (30 days), version history with diff, lock a note with the vault,
              export Markdown / HTML / PDF, import Markdown folders (Obsidian)
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                             QLineEdit, QListWidget, QListWidgetItem, QScrollArea, QSplitter, QStackedWidget,
                             QTabBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from app.core import notes_index
from app.core.i18n import ltr, relative_time, tr
from app.core.md_render import outline as note_outline, stats
from app.ui.pages.base import Page
from app.ui.theme import Icons, Palette, Theme, glyph_icon, ui_font, ink_css
from app.ui.widgets.common import (Card, EmptyState, GlyphLabel, IconButton, add_action, add_submenu, button, label,
                                   make_menu, primary_button)
from app.ui.widgets.markdown import prefix_lines, wrap_selection
from app.ui.widgets.note_editor import FindBar, NoteEditor, OutlinePanel, attachment_dir
from app.ui.widgets.note_preview import NotePreview
from app.ui.widgets.segmented import SegmentedTabs

READABLE_WIDTH = 860   # max text width in "edit" mode
FOCUS_WIDTH = 760
MAX_TABS = 10
FOLDER_COLORS = ["#A78BFA", "#60A5FA", "#22D3EE", "#34D399", "#FBBF24", "#F97316", "#F87171", "#F472B6"]
ROLE = Qt.ItemDataRole.UserRole


def _rgba(color: str, alpha: int) -> str:
    c = QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def _note_title(note: dict) -> str:
    text = (note["title"] or ("" if note["locked"] else note["content"])).strip()
    return text.splitlines()[0][:80] if text else tr("untitled")


class NoteListRow(QWidget):
    def __init__(self, note: dict, subject: dict | None, folder: dict | None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(6)
        if note["locked"]:
            top.addWidget(GlyphLabel(Icons.LOCK, 8.5, Palette.warning))
        if note["is_template"]:
            top.addWidget(GlyphLabel(Icons.STAR, 8.5, Theme.accent2()))
        top.addWidget(label(_note_title(note), 10.2, QFont.Weight.DemiBold), 1)
        if note["is_pinned"]:
            top.addWidget(GlyphLabel(Icons.PIN, 8.5, Theme.accent()))
        lay.addLayout(top)
        if not note["locked"]:
            body = note["content"].strip().splitlines()
            preview = next((line for line in body[1 if not note["title"] else 0:] if line.strip()), "")
            if preview:
                lay.addWidget(label(preview.lstrip("#>-* ")[:90], 9, role="Dim"))
        meta = QHBoxLayout()
        meta.setSpacing(6)
        for color, text in ((folder["color"], folder["name"]) if folder else (None, None),
                            (subject["color"], subject["name"]) if subject else (None, None)):
            if text:
                dot = label("●", 8)
                dot.setStyleSheet(f"color: {color};")
                meta.addWidget(dot)
                meta.addWidget(label(text, 8.4, role="Faint"))
        if note["is_inbox"]:
            meta.addWidget(GlyphLabel(Icons.INBOX, 7.5, Palette.warning))
        meta.addStretch(1)
        stamp = note["deleted_at"] if note["deleted_at"] else note["updated_at"]
        meta.addWidget(label(relative_time(stamp), 8.4, role="Faint"))
        lay.addLayout(meta)


class NoteList(QListWidget):
    """The note list; notes can be dragged onto folders in the navigation tree."""

    def __init__(self):
        super().__init__()
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def selected_ids(self) -> list[int]:
        return [it.data(ROLE) for it in self.selectedItems()]


class NavTree(QTreeWidget):
    """Collections, folders, subjects and tags; accepts notes dropped on folders."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setHeaderHidden(True)
        self.setColumnCount(2)
        self.setIndentation(14)
        self.setRootIsDecorated(True)
        self.setAnimated(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(f"""
            QTreeWidget {{ background: transparent; border: none; outline: none; }}
            QTreeWidget::item {{ height: 32px; border-radius: 9px; padding-left: 4px; }}
            QTreeWidget::item:hover {{ background: {ink_css(0.05)}; }}
            QTreeWidget::item:selected {{ background: {_rgba(Theme.accent(), 70)}; color: #fff; }}""")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.setColumnWidth(1, 36)
        self.setColumnWidth(0, max(80, self.viewport().width() - 38))

    def _drop_target(self, pos):
        item = self.itemAt(pos)
        key = item.data(0, ROLE) if item else None
        if key and key[0] == "folder":
            return key[1]
        if key and key[0] == "folders_header":
            return 0  # root: no folder
        return None

    def dragEnterEvent(self, e):
        if e.source() is self.page.notes:
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.source() is self.page.notes and self._drop_target(e.position().toPoint()) is not None:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        target = self._drop_target(e.position().toPoint())
        ids = self.page.notes.selected_ids()
        if target is not None and ids:
            self.page.ctl.move_notes(ids, target or None)
        e.acceptProposedAction()


class NoteCard(Card):
    """Gallery card."""

    def __init__(self, note: dict, color: str, page):
        super().__init__(padding=16)
        self.note_id, self.page = note["id"], page
        self.setProperty("hoverable", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(190)
        self.setStyleSheet(f"QFrame#Card {{ border-top: 3px solid {color}; }}")
        top = QHBoxLayout()
        if note["locked"]:
            top.addWidget(GlyphLabel(Icons.LOCK, 9, Palette.warning))
        t = label(_note_title(note), 11, QFont.Weight.DemiBold)
        top.addWidget(t, 1)
        if note["is_pinned"]:
            top.addWidget(GlyphLabel(Icons.PIN, 9, Theme.accent()))
        self.root.addLayout(top)
        text = "" if note["locked"] else " ".join(
            line.lstrip("#>-*[]x ") for line in note["content"].splitlines() if line.strip())[:220]
        body = label(text or "…", 9.2, role="Dim", wrap=True)
        body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeading)
        self.root.addWidget(body, 1)
        tags = notes_index.tags(note["content"])[:3] if not note["locked"] else []
        meta = QHBoxLayout()
        for tag in tags:
            chip = label("#" + tag, 8.2)
            chip.setStyleSheet(f"color: {Theme.accent()}; background: {_rgba(Theme.accent(), 35)};"
                               "border-radius: 8px; padding: 1px 6px;")
            meta.addWidget(chip)
        meta.addStretch(1)
        meta.addWidget(label(relative_time(note["updated_at"]), 8.2, role="Faint"))
        self.root.addLayout(meta)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.page.open_note(self.note_id)
        super().mouseReleaseEvent(e)


class NotesPage(Page):
    def __init__(self, ctl, main_window):
        super().__init__(ctl, main_window)
        db = ctl.db
        self.note_id: int | None = None
        self.collection = ("inbox", None) if db.count_inbox() else ("all", None)
        self.sort = db.get_setting("notes_sort", "updated")
        self.view = "list"         # list (editor) | cards (gallery)
        self.tabs_ids: list[int] = [i for i in db.get_setting("notes_tabs", []) if db.get_note(i)]
        self.beside_id: int | None = None
        self._loading = False
        self._loaded_title = ""
        self._save_timer = QTimer(self, singleShot=True, interval=600, timeout=self._save_now)
        self._preview_timer = QTimer(self, singleShot=True, interval=250, timeout=self._update_preview)
        self._follow_timer = QTimer(self, singleShot=True, interval=160, timeout=self._follow_cursor)
        self._gallery_timer = QTimer(self, singleShot=True, interval=120, timeout=self._fill_gallery)
        self.focus_mode = False
        self._focus_restore = ("split", False)

        root = QHBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(18)
        self.side_panel = self._build_sidebar()
        root.addWidget(self.side_panel)
        self.main_stack = QStackedWidget()
        self.main_stack.addWidget(self._build_empty())      # 0
        self.main_stack.addWidget(self._build_editor())     # 1
        self.main_stack.addWidget(self._build_gallery())    # 2
        self.main_stack.addWidget(self._build_tasks())      # 3
        root.addWidget(self.main_stack, 1)

        for keys, slot in (("Ctrl+F", lambda: self._find(False)), ("Ctrl+H", lambda: self._find(True)),
                           ("Ctrl+Shift+F", lambda: (self.search.setFocus(), self.search.selectAll())),
                           ("F8", self.toggle_focus), ("Ctrl+Shift+O", lambda: self.outline_btn.toggle()),
                           ("Ctrl+P", self.quick_switch), ("Ctrl+D", self.open_daily),
                           ("Ctrl+W", self._close_current_tab), ("Ctrl+Tab", lambda: self._cycle_tab(1)),
                           ("Ctrl+Shift+Tab", lambda: self._cycle_tab(-1)), ("Ctrl+S", self._save_now)):
            QShortcut(QKeySequence(keys), self, activated=slot,
                      context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.listen(ctl.data_changed, self.refresh)
        self.refresh("")

    # ================================================================ sidebar
    def _build_sidebar(self) -> QWidget:
        panel = Card(padding=14)
        panel.setFixedWidth(330)
        lay = panel.root
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(4)
        head.addWidget(label(tr("nav_notes"), 15, QFont.Weight.DemiBold))
        head.addStretch(1)
        more = IconButton(Icons.MENU, tr("more"), 32, 10)
        more.clicked.connect(lambda: self._notebook_menu(more))
        head.addWidget(more)
        new = primary_button(tr("new_note"), Icons.ADD)
        new.clicked.connect(lambda: self.new_note())
        head.addWidget(new)
        arrow = IconButton(Icons.CHEVRON, tr("new_from_template"), 32, 9)
        arrow.clicked.connect(lambda: self._new_menu(arrow))
        head.addWidget(arrow)
        lay.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("search_notes"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self._reload_list())
        lay.addWidget(self.search)

        split = QSplitter(Qt.Orientation.Vertical)
        split.setChildrenCollapsible(False)
        self.nav = NavTree(self)
        self.nav.itemClicked.connect(self._nav_clicked)
        self.nav.customContextMenuRequested.connect(self._nav_menu)
        self.nav.itemExpanded.connect(self._remember_folders)
        self.nav.itemCollapsed.connect(self._remember_folders)
        split.addWidget(self.nav)

        listbox = QWidget()
        ll = QVBoxLayout(listbox)
        ll.setContentsMargins(0, 6, 0, 0)
        ll.setSpacing(6)
        lh = QHBoxLayout()
        self.list_title = label(tr("notes_label"), 8.8, QFont.Weight.DemiBold, "Faint")
        lh.addWidget(self.list_title, 1)
        self.sort_btn = IconButton(Icons.VIEW_LIST, tr("sort_by"), 28, 9)
        self.sort_btn.clicked.connect(lambda: self._sort_menu(self.sort_btn))
        lh.addWidget(self.sort_btn)
        self.cards_btn = IconButton(Icons.VIEW_GRID, tr("cards_view"), 28, 9)
        self.cards_btn.setCheckable(True)
        self.cards_btn.toggled.connect(self._toggle_cards)
        lh.addWidget(self.cards_btn)
        ll.addLayout(lh)
        self.notes = NoteList()
        self.notes.itemClicked.connect(self._note_clicked)
        self.notes.customContextMenuRequested.connect(self._list_menu)
        ll.addWidget(self.notes, 1)
        split.addWidget(listbox)
        split.setSizes([300, 420])
        lay.addWidget(split, 1)
        return panel

    def _nav_row(self, parent, key, glyph: str, text: str, count, color: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [text, "" if count in (None, "") else str(count)])
        item.setData(0, ROLE, key)
        item.setIcon(0, glyph_icon(glyph, color, 18))
        item.setFont(0, ui_font(9.8))
        item.setFont(1, ui_font(8.6, QFont.Weight.DemiBold))
        item.setForeground(1, QColor(Palette.text_dim))
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
        return item

    def _nav_header(self, key, text: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self.nav, [text.upper(), ""])
        item.setData(0, ROLE, (key, None))
        item.setFont(0, ui_font(8.4, QFont.Weight.DemiBold))
        item.setForeground(0, QColor(Palette.text_faint))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled)
        return item

    def _reload_nav(self):
        db = self.ctl.db
        counts = db.count_notes_by_view()
        open_folders = set(db.get_setting("notes_open_folders", []))
        self.nav.blockSignals(True)
        self.nav.clear()
        open_tasks = sum(1 for n in db.all_live_notes() if not n["locked"]
                         for _, done, _ in notes_index.tasks(n["content"]) if not done)
        for key, glyph, text, count, color in (
                (("inbox", None), Icons.INBOX, tr("inbox_title"), counts["inbox"], Palette.warning),
                (("all", None), Icons.NOTES, tr("all_notes"), counts["all"], Theme.accent()),
                (("pinned", None), Icons.PIN, tr("pinned_notes"), counts["pinned"], Theme.accent2()),
                (("tasks", None), Icons.CHECKLIST, tr("notes_tasks"), open_tasks, Palette.success)):
            self._nav_row(self.nav, key, glyph, text, count, color)

        folders_head = self._nav_header("folders_header", tr("folders"))
        folders = db.list_folders()
        items: dict[int, QTreeWidgetItem] = {}
        pending = list(folders)
        while pending:  # parents before children
            rest = []
            for f in pending:
                parent = folders_head if not f["parent_id"] else items.get(f["parent_id"])
                if parent is None and f["parent_id"] not in {x["id"] for x in folders}:
                    parent = folders_head
                if parent is None:
                    rest.append(f)
                    continue
                items[f["id"]] = self._nav_row(parent, ("folder", f["id"]), Icons.FOLDER, f["name"],
                                               counts.get(("folder", f["id"])) or "", f["color"])
            if len(rest) == len(pending):
                break
            pending = rest
        folders_head.setExpanded(True)
        for fid, item in items.items():
            item.setExpanded(fid in open_folders)

        subjects = db.list_subjects()
        if subjects:
            head = self._nav_header("subjects_header", tr("subjects_section"))
            for s in subjects:
                self._nav_row(head, ("subject", s["id"]), Icons.BOOK, s["name"], counts.get(s["id"]) or "", s["color"])
            head.setExpanded(True)
        tags = db.list_note_tags()
        if tags:
            head = self._nav_header("tags_header", tr("tags_section"))
            for t in tags[:60]:
                self._nav_row(head, ("tag", t["tag"]), Icons.TAG, "#" + t["tag"], t["count"], Theme.accent())
            head.setExpanded(db.get_setting("notes_tags_open", True))
        self._nav_header("other_header", "")
        self._nav_row(self.nav, ("templates", None), Icons.STAR, tr("templates"), counts["templates"] or "",
                      Theme.accent2())
        self._nav_row(self.nav, ("trash", None), Icons.DELETE, tr("trash"), counts["trash"] or "", Palette.danger)
        self._select_nav(self.collection)
        self.nav.blockSignals(False)

    def _remember_folders(self, *_):
        opened = []

        def walk(item):
            for i in range(item.childCount()):
                child = item.child(i)
                key = child.data(0, ROLE)
                if key and key[0] == "folder" and child.isExpanded():
                    opened.append(key[1])
                walk(child)
        walk(self.nav.invisibleRootItem())
        self.ctl.db.set_setting("notes_open_folders", opened)

    def _select_nav(self, key) -> None:
        def walk(item):
            for i in range(item.childCount()):
                child = item.child(i)
                if tuple(child.data(0, ROLE) or ()) == tuple(key):
                    self.nav.setCurrentItem(child)
                    return True
                if walk(child):
                    return True
            return False
        self.nav.clearSelection()
        walk(self.nav.invisibleRootItem())

    def _nav_clicked(self, item: QTreeWidgetItem, _col: int):
        key = item.data(0, ROLE)
        if not key or key[0].endswith("_header"):
            return
        self.set_collection(tuple(key))

    def set_collection(self, key) -> None:
        self.collection = tuple(key)
        self.search.clear()
        self._select_nav(self.collection)
        self._reload_list(select_first=self.collection[0] not in ("tasks",))
        if self.collection[0] == "tasks":
            self._show_main(3)
        elif self.view == "cards":
            self._show_main(2)

    def _collection_name(self) -> str:
        view, key = self.collection
        if view == "folder":
            f = self.ctl.db.get_folder(key)
            return f["name"] if f else tr("folders")
        if view == "subject":
            s = next((s for s in self.ctl.db.list_subjects(True) if s["id"] == key), None)
            return s["name"] if s else ""
        if view == "tag":
            return "#" + key
        return {"inbox": tr("inbox_title"), "all": tr("all_notes"), "pinned": tr("pinned_notes"),
                "tasks": tr("notes_tasks"), "templates": tr("templates"), "trash": tr("trash")}.get(view, "")

    def _current_notes(self) -> list[dict]:
        view, key = self.collection
        db = self.ctl.db
        if view == "tasks":
            ids = {n["id"] for n in db.all_live_notes() if not n["locked"]
                   and any(not done for _, done, _ in notes_index.tasks(n["content"]))}
            return [n for n in db.list_notes("all", sort=self.sort) if n["id"] in ids]
        return db.list_notes(view, key, self.search.text().strip(), self.sort)

    def _reload_list(self, select_first: bool = False):
        notes = self._current_notes()
        db = self.ctl.db
        subjects = {s["id"]: s for s in db.list_subjects(include_archived=True)}
        folders = {f["id"]: f for f in db.list_folders()}
        self.list_title.setText(f"{self._collection_name().upper()}  ·  {len(notes)}")
        self.notes.blockSignals(True)
        self.notes.clear()
        current_row = -1
        for i, note in enumerate(notes):
            item = QListWidgetItem()
            item.setData(ROLE, note["id"])
            row = NoteListRow(note, subjects.get(note["subject_id"]), folders.get(note["folder_id"]))
            item.setSizeHint(QSize(0, row.sizeHint().height() + 4))
            self.notes.addItem(item)
            self.notes.setItemWidget(item, row)
            if note["id"] == self.note_id:
                current_row = i
        if current_row >= 0:
            self.notes.setCurrentRow(current_row)
        self.notes.blockSignals(False)
        if self.main_stack.currentIndex() == 2:
            self._gallery_timer.start()
        if self.collection[0] == "tasks":
            self._fill_tasks()
        if select_first and current_row < 0 and notes and self.view == "list" and self.collection[0] != "tasks":
            self.open_note(notes[0]["id"])
        elif self.note_id is not None and not self._note_still_exists():
            self._load(None, show=False)

    def _note_still_exists(self) -> bool:
        return self.note_id is not None and self.ctl.db.get_note(self.note_id) is not None

    def _note_clicked(self, item: QListWidgetItem):
        self.open_note(item.data(ROLE))

    def _list_menu(self, pos):
        item = self.notes.itemAt(pos)
        if item:
            ids = self.notes.selected_ids() if item.isSelected() else [item.data(ROLE)]
            self._note_menu(item.data(ROLE), self.notes.viewport().mapToGlobal(pos), ids)

    def _nav_menu(self, pos):
        item = self.nav.itemAt(pos)
        key = item.data(0, ROLE) if item else None
        menu = make_menu(self)
        if key and key[0] == "folder":
            add_action(menu, Icons.NEW_FOLDER, tr("new_subfolder"), lambda: self.ctl.new_folder(key[1], self))
            add_action(menu, Icons.ADD, tr("new_note_here"), lambda: self.new_note(folder_id=key[1]))
            add_action(menu, Icons.EDIT, tr("rename"), lambda: self.ctl.rename_folder(key[1], self))
            colors = add_submenu(menu, Icons.COLOR, tr("color"))
            for c in FOLDER_COLORS:
                act = add_action(colors, None, "●", lambda col=c: self.ctl.set_folder_color(key[1], col))
                act.setIcon(glyph_icon("●", c, 16))
            menu.addSeparator()
            add_action(menu, Icons.DELETE, tr("delete_folder"), lambda: self.ctl.delete_folder(key[1], self),
                       danger=True)
        elif key and key[0] == "trash":
            add_action(menu, Icons.DELETE, tr("empty_trash"), lambda: self.ctl.empty_trash(self), danger=True)
        else:
            add_action(menu, Icons.NEW_FOLDER, tr("new_folder"), lambda: self.ctl.new_folder(None, self))
        menu.exec(self.nav.viewport().mapToGlobal(pos))

    def _sort_menu(self, anchor):
        menu = make_menu(self)
        for key in ("updated", "created", "title"):
            add_action(menu, Icons.ACCEPT if self.sort == key else None, tr(f"sort_{key}"),
                       lambda k=key: self._set_sort(k))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _set_sort(self, key: str):
        self.sort = key
        self.ctl.db.set_setting("notes_sort", key)
        self._reload_list()

    def _new_menu(self, anchor):
        menu = make_menu(self)
        add_action(menu, Icons.CALENDAR_DAY, tr("daily_note") + "  (Ctrl+D)", self.open_daily)
        menu.addSeparator()
        for key in ("lecture", "lab", "meeting", "project", "bug", "weekly"):
            add_action(menu, Icons.DOCUMENT, tr(f"tpl_{key}"), lambda k=key: self._from_template(k))
        own = self.ctl.db.list_notes("templates")
        if own:
            menu.addSeparator()
            for t in own[:15]:
                add_action(menu, Icons.STAR, _note_title(t), lambda i=t["id"]: self._from_template(i))
        menu.addSeparator()
        add_action(menu, Icons.NEW_FOLDER, tr("new_folder"), lambda: self.ctl.new_folder(self._folder_here(), self))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _notebook_menu(self, anchor):
        menu = make_menu(self)
        add_action(menu, Icons.SEARCH, tr("quick_switcher") + "  (Ctrl+P)", self.quick_switch)
        menu.addSeparator()
        add_action(menu, Icons.DOWNLOAD, tr("import_md_files"), lambda: self._import(False))
        add_action(menu, Icons.OPEN_FOLDER, tr("import_md_folder"), lambda: self._import(True))
        add_action(menu, Icons.SAVE, tr("export_all_md"), self._export_all)
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("empty_trash"), lambda: self.ctl.empty_trash(self), danger=True)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _folder_here(self) -> int | None:
        return self.collection[1] if self.collection[0] == "folder" else None

    # ================================================================= main area
    def _build_empty(self) -> QWidget:
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        el.addWidget(EmptyState(Icons.NOTES, tr("notebook_empty")))
        row = QHBoxLayout()
        row.addStretch(1)
        first = primary_button(tr("new_note"), Icons.ADD)
        first.clicked.connect(lambda: self.new_note())
        row.addWidget(first)
        daily = button(tr("daily_note"), Icons.CALENDAR_DAY, role="Ghost")
        daily.clicked.connect(self.open_daily)
        row.addWidget(daily)
        row.addStretch(1)
        el.addLayout(row)
        el.addWidget(label(tr("notebook_shortcuts"), 8.8, role="Faint"), 0, Qt.AlignmentFlag.AlignHCenter)
        el.addStretch(2)
        return empty

    def _show_main(self, index: int) -> None:
        if self.main_stack.currentIndex() != index:
            self.main_stack.setCurrentIndex(index)
        if index == 2:
            self._gallery_timer.start()
        elif index == 3:
            self._fill_tasks()

    def _build_editor(self) -> QWidget:
        card = Card(padding=18)
        lay = card.root
        lay.setSpacing(8)

        self.tabbar = QTabBar()
        self.tabbar.setTabsClosable(False)  # our own close buttons (the native ones look out of place)
        self.tabbar.setMovable(True)
        self.tabbar.setExpanding(False)
        self.tabbar.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabbar.setDocumentMode(True)
        self.tabbar.setStyleSheet(f"""
            QTabBar::tab {{ background: transparent; color: {Palette.text_dim}; padding: 6px 12px; margin-right: 2px;
                           border-radius: 9px; max-width: 200px; }}
            QTabBar::tab:selected {{ background: {ink_css(0.07)}; color: {Palette.text}; }}
            QTabBar::tab:hover {{ background: {ink_css(0.04)}; }}""")
        self.tabbar.currentChanged.connect(self._tab_changed)
        self.tabbar.tabMoved.connect(self._tabs_moved)
        self.tabbar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabbar.customContextMenuRequested.connect(self._tab_menu)
        lay.addWidget(self.tabbar)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.title = QLineEdit()
        self.title.setObjectName("TitleEdit")
        self.title.setFont(ui_font(18, QFont.Weight.DemiBold))
        self.title.setPlaceholderText(tr("untitled"))
        self.title.textChanged.connect(self._changed)
        self.title.editingFinished.connect(self._title_done)
        top.addWidget(self.title, 1)
        self.status = label("", 9, role="Faint")
        top.addWidget(self.status)
        self.mode = SegmentedTabs([("edit", Icons.EDIT, tr("mode_edit")), ("split", Icons.SELECT, tr("mode_split")),
                                   ("preview", Icons.DOCUMENT, tr("mode_preview"))],
                                  self.ctl.db.get_setting("notes_mode", "split"))
        self.mode.changed.connect(self._set_mode)
        top.addWidget(self.mode)
        lay.addLayout(top)

        self.tools_row = QWidget()
        tools = QHBoxLayout(self.tools_row)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(2)
        for glyph_text, tip, action in (
                ("H1", tr("md_heading"), lambda: prefix_lines(self.editor, "# ")),
                ("H2", tr("md_heading"), lambda: prefix_lines(self.editor, "## ")),
                ("B", tr("md_bold") + " (Ctrl+B)", lambda: wrap_selection(self.editor, "**", placeholder=tr("md_bold"))),
                ("I", tr("md_italic") + " (Ctrl+I)", lambda: wrap_selection(self.editor, "*", placeholder=tr("md_italic"))),
                ("S", tr("md_strike"), lambda: wrap_selection(self.editor, "~~", placeholder=tr("md_strike")))):
            b = button(glyph_text, role="Ghost")
            b.setFont(ui_font(10, QFont.Weight.Bold))
            b.setFixedSize(34, 34)
            b.setToolTip(tip)
            b.clicked.connect(action)
            tools.addWidget(b)
        for glyph, tip, action in (
                (Icons.MENU, tr("md_list"), lambda: prefix_lines(self.editor, "- ")),
                (Icons.CHECKLIST, tr("md_checklist"), lambda: prefix_lines(self.editor, "- [ ] ")),
                (Icons.EDIT, tr("md_quote"), lambda: prefix_lines(self.editor, "> ")),
                (Icons.DOCUMENT, tr("md_code"), lambda: wrap_selection(self.editor, "`", placeholder="code")),
                (Icons.LINK, tr("md_link") + " (Ctrl+K)",
                 lambda: wrap_selection(self.editor, "[", "](https://)", tr("md_link"))),
                (Icons.NOTES, tr("link_note") + " ( [[ )", self._insert_note_link),
                (Icons.ATTACH, tr("slash_file"), lambda: self._slash_action("file"))):
            b = IconButton(glyph, tip, 34, 10.5)
            b.clicked.connect(action)
            tools.addWidget(b)
        slash = button("/", role="Ghost")
        slash.setFont(ui_font(11, QFont.Weight.Bold))
        slash.setFixedSize(34, 34)
        slash.setToolTip(tr("slash_hint"))
        slash.clicked.connect(self._open_slash_menu)
        tools.addWidget(slash)
        tools.addStretch(1)
        self.outline_btn = IconButton(Icons.OUTLINE, tr("outline") + " (Ctrl+Shift+O)", 34, 10.5)
        self.outline_btn.setCheckable(True)
        self.outline_btn.toggled.connect(self._toggle_outline)
        tools.addWidget(self.outline_btn)
        find = IconButton(Icons.SEARCH, tr("find_in_note") + " (Ctrl+F)", 34, 10.5)
        find.clicked.connect(lambda: self._find(False))
        tools.addWidget(find)
        focus = IconButton(Icons.FOCUS, tr("focus_mode") + " (F8)", 34, 10.5)
        focus.clicked.connect(self.toggle_focus)
        tools.addWidget(focus)
        self.subject = QComboBox()
        self.subject.setFixedWidth(170)
        self.subject.currentIndexChanged.connect(self._subject_changed)
        tools.addWidget(self.subject)
        self.pin = IconButton(Icons.PIN, tr("pin"), 34, 11)
        self.pin.setCheckable(True)
        self.pin.toggled.connect(lambda on: self._set_field(is_pinned=int(on)))
        tools.addWidget(self.pin)
        more = IconButton(Icons.MENU, tr("more"), 34, 10.5)
        more.clicked.connect(lambda: self.note_id and self._note_menu(
            self.note_id, more.mapToGlobal(more.rect().bottomLeft()), [self.note_id]))
        tools.addWidget(more)
        lay.addWidget(self.tools_row)

        self.banner = self._banner(Palette.warning, Icons.INBOX)
        lay.addWidget(self.banner)
        self.find_bar = None  # created with the editor below

        self.body_stack = QStackedWidget()
        self.editor = NoteEditor()
        self.editor.setPlaceholderText(tr("note_placeholder_slash"))
        self.editor.textChanged.connect(self._changed)
        self.editor.attachments = lambda: attachment_dir(self.note_id) if self.note_id else None
        self.editor.note_titles = lambda: [n["title"] for n in self.ctl.db.note_titles() if n["id"] != self.note_id]
        self.editor.command_requested.connect(self._slash_action)
        self.editor.escape_pressed.connect(self._on_escape)
        self.editor.escape_wanted = lambda: self.find_bar.isVisible() or self.focus_mode
        self.editor.cursorPositionChanged.connect(self._follow_timer.start)
        self.find_bar = FindBar(self.editor)
        lay.addWidget(self.find_bar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.side_info = QWidget()
        si = QVBoxLayout(self.side_info)
        si.setContentsMargins(0, 0, 0, 0)
        si.setSpacing(8)
        self.outline = OutlinePanel()
        self.outline.jump.connect(self._jump_to_line)
        si.addWidget(self.outline, 3)
        self.backlinks_box = QFrame()
        self.backlinks_box.setObjectName("Backlinks")
        self.backlinks_box.setStyleSheet(f"QFrame#Backlinks {{ background: {ink_css(0.025)}; border-radius: 12px; }}"
                                         "QListWidget { background: transparent; border: none; }")
        bl = QVBoxLayout(self.backlinks_box)
        bl.setContentsMargins(8, 10, 8, 8)
        bl.addWidget(label(tr("backlinks"), 8.8, QFont.Weight.DemiBold, "Faint"))
        self.backlinks = QListWidget()
        self.backlinks.itemClicked.connect(lambda it: self.open_note(it.data(ROLE)))
        bl.addWidget(self.backlinks, 1)
        self.backlinks_empty = label(tr("backlinks_empty"), 8.6, role="Faint", wrap=True)
        bl.addWidget(self.backlinks_empty)
        si.addWidget(self.backlinks_box, 2)
        self.side_info.setMinimumWidth(180)
        self.side_info.setMaximumWidth(270)

        self.preview_box = QWidget()
        pb = QVBoxLayout(self.preview_box)
        pb.setContentsMargins(0, 0, 0, 0)
        pb.setSpacing(4)
        self.beside_bar = QFrame()
        self.beside_bar.setStyleSheet(f"QFrame {{ background: {_rgba(Theme.accent2(), 30)}; border-radius: 9px; }}")
        bb = QHBoxLayout(self.beside_bar)
        bb.setContentsMargins(10, 4, 4, 4)
        bb.addWidget(GlyphLabel(Icons.PREVIEW, 9, Theme.accent2()))
        self.beside_label = label("", 9, QFont.Weight.DemiBold)
        bb.addWidget(self.beside_label, 1)
        go = IconButton(Icons.OPEN_FOLDER, tr("open"), 26, 8.5)
        go.clicked.connect(lambda: self.beside_id and self.open_note(self.beside_id))
        bb.addWidget(go)
        close = IconButton(Icons.CLOSE, tr("close"), 26, 8.5)
        close.clicked.connect(lambda: self.open_beside(None))
        bb.addWidget(close)
        self.beside_bar.hide()
        pb.addWidget(self.beside_bar)
        self.preview = NotePreview()
        self.preview.task_toggled.connect(self._preview_task)
        self.preview.link_opened.connect(self.ctl.open_path)
        self.preview.note_link.connect(self._open_link)
        self.preview.tag_clicked.connect(lambda t: self.set_collection(("tag", t)))
        self.preview.pdf_link.connect(lambda path, page: self.ctl.open_pdf(path, page))
        pb.addWidget(self.preview, 1)

        self.splitter.addWidget(self.side_info)
        self.splitter.addWidget(self.editor)
        self.splitter.addWidget(self.preview_box)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 1)
        self.splitter.setSizes([210, 600, 600])
        self.body_stack.addWidget(self.splitter)          # 0
        self.body_stack.addWidget(self._build_locked())   # 1
        lay.addWidget(self.body_stack, 1)

        foot = QHBoxLayout()
        self.footer = label("", 8.6, role="Faint")
        foot.addWidget(self.footer, 1)
        self.section = label("", 8.6, role="Faint")
        foot.addWidget(self.section)
        self.focus_hint = label(tr("focus_exit_hint"), 8.6, role="Faint")
        self.focus_hint.hide()
        foot.addWidget(self.focus_hint)
        lay.addLayout(foot)
        self.side_info.setVisible(False)
        self.outline_btn.setChecked(bool(self.ctl.db.get_setting("notes_outline", False)))
        self._set_mode(self.mode.current, save=False)
        return card

    def _banner(self, color: str, glyph: str) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(f"QFrame {{ background: {_rgba(color, 30)}; border: 1px solid {_rgba(color, 90)};"
                            f" border-radius: 12px; }}")
        bl = QHBoxLayout(frame)
        bl.setContentsMargins(14, 6, 8, 6)
        frame.glyph = GlyphLabel(glyph, 10, color)
        bl.addWidget(frame.glyph)
        frame.text = label("", 9.5)
        bl.addWidget(frame.text, 1)
        frame.buttons = QHBoxLayout()
        bl.addLayout(frame.buttons)
        frame.hide()
        return frame

    def _set_banner(self, kind: str | None) -> None:
        b = self.banner
        while b.buttons.count():
            w = b.buttons.takeAt(0).widget()
            if w:
                w.deleteLater()
        if kind is None:
            b.hide()
            return

        def add(text, glyph, slot, role=""):
            btn = button(text, glyph, role=role)
            btn.clicked.connect(slot)
            b.buttons.addWidget(btn)
        nid = self.note_id
        if kind == "trash":
            b.text.setText(tr("trash_banner"))
            add(tr("restore"), Icons.UNDO, lambda: self.ctl.restore_note(nid))
            add(tr("delete_forever"), Icons.DELETE, lambda: self.ctl.delete_note_forever([nid], self), "Danger")
        elif kind == "template":
            b.text.setText(tr("template_banner"))
            add(tr("use_template"), Icons.ADD, lambda: self._from_template(nid))
        else:
            b.text.setText(tr("inbox_banner"))
            add(tr("mark_filed"), Icons.ACCEPT, lambda: self.ctl.file_note(nid))
        b.show()

    def _build_locked(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch(1)
        lay.addWidget(EmptyState(Icons.LOCK, tr("note_is_locked")))
        unlock = primary_button(tr("unlock"), Icons.KEY)
        unlock.clicked.connect(lambda: self.ctl.ensure_vault_unlocked(self) and self._load(self.note_id))
        lay.addWidget(unlock, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(2)
        return w

    # ------------------------------------------------------------- gallery
    def _build_gallery(self) -> QWidget:
        box = Card(padding=18)
        head = QHBoxLayout()
        self.gallery_title = label("", 15, QFont.Weight.DemiBold)
        head.addWidget(self.gallery_title, 1)
        back = button(tr("back_to_editor"), Icons.EDIT, role="Ghost")
        back.clicked.connect(lambda: self.cards_btn.setChecked(False))
        head.addWidget(back)
        box.root.addLayout(head)
        host = QWidget()
        self.gallery_grid = QGridLayout(host)
        self.gallery_grid.setSpacing(14)
        self.gallery_grid.setContentsMargins(0, 0, 6, 0)
        self.gallery_scroll = QScrollArea()
        self.gallery_scroll.setWidgetResizable(True)
        self.gallery_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.gallery_scroll.setWidget(host)
        self.gallery_scroll.viewport().setAutoFillBackground(False)
        host.setAutoFillBackground(False)
        box.root.addWidget(self.gallery_scroll, 1)
        return box

    def _fill_gallery(self) -> None:
        while self.gallery_grid.count():
            w = self.gallery_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        notes = self._current_notes()
        self.gallery_title.setText(f"{self._collection_name()}  ·  {len(notes)}")
        db = self.ctl.db
        folders = {f["id"]: f["color"] for f in db.list_folders()}
        subjects = {s["id"]: s["color"] for s in db.list_subjects(True)}
        cols = max(1, (self.gallery_scroll.viewport().width() - 10) // 270)
        cards = []
        for i, note in enumerate(notes[:200]):
            color = folders.get(note["folder_id"]) or subjects.get(note["subject_id"]) or Theme.accent()
            card = NoteCard(note, color, self)
            self.gallery_grid.addWidget(card, i // cols, i % cols)
            cards.append(card)
        if not notes:
            self.gallery_grid.addWidget(EmptyState(Icons.NOTES, tr("no_notes_here")), 0, 0)
        self.gallery_grid.setRowStretch(len(notes) // cols + 1, 1)

    def _toggle_cards(self, on: bool) -> None:
        self.view = "cards" if on else "list"
        if self.collection[0] == "tasks":
            return
        if on:
            self._save_now()
            self._show_main(2)
        else:
            self._show_main(1 if self.note_id else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.main_stack.currentIndex() == 2:
            self._gallery_timer.start()

    # --------------------------------------------------------------- tasks
    def _build_tasks(self) -> QWidget:
        box = Card(padding=18)
        head = QHBoxLayout()
        head.addWidget(label(tr("notes_tasks"), 15, QFont.Weight.DemiBold), 1)
        self.show_done = QCheckBox(tr("show_done"))
        self.show_done.toggled.connect(lambda _: self._fill_tasks())
        head.addWidget(self.show_done)
        box.root.addLayout(head)
        box.root.addWidget(label(tr("notes_tasks_hint"), 9, role="Dim"))
        host = QWidget()
        self.tasks_lay = QVBoxLayout(host)
        self.tasks_lay.setContentsMargins(0, 0, 6, 0)
        self.tasks_lay.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(host)
        scroll.viewport().setAutoFillBackground(False)
        host.setAutoFillBackground(False)
        box.root.addWidget(scroll, 1)
        return box

    def _fill_tasks(self) -> None:
        while self.tasks_lay.count():
            item = self.tasks_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        show_done = self.show_done.isChecked()
        any_task = False
        for note in self.ctl.db.all_live_notes():
            if note["locked"]:
                continue
            tasks = [t for t in notes_index.tasks(note["content"]) if show_done or not t[1]]
            if not tasks:
                continue
            any_task = True
            title = button(_note_title(note), Icons.NOTES, role="Ghost")
            title.setFont(ui_font(10.5, QFont.Weight.DemiBold))
            title.clicked.connect(lambda _=False, i=note["id"]: self.open_note(i))
            self.tasks_lay.addWidget(title, 0, Qt.AlignmentFlag.AlignLeading)
            for line, done, text in tasks:
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(26, 0, 0, 0)
                box = QCheckBox(text or "…")
                box.setChecked(done)
                box.setFont(ui_font(10))
                if done:
                    box.setStyleSheet(f"color: {Palette.text_faint}; text-decoration: line-through;")
                box.toggled.connect(lambda _=False, i=note["id"], l=line: self._toggle_task(i, l))
                h.addWidget(box, 1)
                jump = IconButton(Icons.FORWARD, tr("open"), 26, 8.5)
                jump.clicked.connect(lambda _=False, i=note["id"], l=line: (self.open_note(i), self._jump_to_line(l)))
                h.addWidget(jump)
                self.tasks_lay.addWidget(row)
        if not any_task:
            self.tasks_lay.addWidget(EmptyState(Icons.CHECKLIST, tr("no_note_tasks")))
        self.tasks_lay.addStretch(1)

    def _toggle_task(self, note_id: int, line: int) -> None:
        if note_id == self.note_id:
            self.editor.toggle_task_at(line)
            self._save_now(force=True)
        else:
            self.ctl.set_note_task(note_id, line)
        QTimer.singleShot(0, self._fill_tasks)

    # ================================================================== tabs
    def _sync_tabs(self) -> None:
        db = self.ctl.db
        self.tabs_ids = [i for i in self.tabs_ids if db.get_note(i)]
        self.tabbar.blockSignals(True)
        while self.tabbar.count():
            self.tabbar.removeTab(0)
        for nid in self.tabs_ids:
            note = db.get_note(nid)
            idx = self.tabbar.addTab(("🔒 " if note["locked"] else "") + _note_title(note))
            self.tabbar.setTabData(idx, nid)
            self.tabbar.setTabToolTip(idx, _note_title(note))
            close = IconButton(Icons.CLOSE, tr("close_tab") + " (Ctrl+W)", 20, 7.5)
            close.clicked.connect(lambda _=False, n=nid: n in self.tabs_ids and self._close_tab(self.tabs_ids.index(n)))
            self.tabbar.setTabButton(idx, QTabBar.ButtonPosition.RightSide, close)
        if self.note_id in self.tabs_ids:
            self.tabbar.setCurrentIndex(self.tabs_ids.index(self.note_id))
        self.tabbar.blockSignals(False)
        self.tabbar.setVisible(len(self.tabs_ids) > 1)
        db.set_setting("notes_tabs", self.tabs_ids)

    def _add_tab(self, note_id: int) -> None:
        if note_id not in self.tabs_ids:
            self.tabs_ids.append(note_id)
            while len(self.tabs_ids) > MAX_TABS:
                victim = next(i for i in self.tabs_ids if i != note_id)
                self.tabs_ids.remove(victim)
        self._sync_tabs()

    def _tab_changed(self, index: int) -> None:
        nid = self.tabbar.tabData(index)
        if nid is not None and nid != self.note_id:
            self.open_note(nid)

    def _close_tab(self, index: int) -> None:
        nid = self.tabbar.tabData(index)
        if nid is None:
            return
        self._save_now()
        pos = self.tabs_ids.index(nid)
        self.tabs_ids.remove(nid)
        if nid == self.note_id:
            nxt = self.tabs_ids[min(pos, len(self.tabs_ids) - 1)] if self.tabs_ids else None
            self._sync_tabs()
            if nxt:
                self.open_note(nxt)
            else:
                self._load(None)
        else:
            self._sync_tabs()

    def _close_current_tab(self) -> None:
        if self.note_id in self.tabs_ids:
            self._close_tab(self.tabs_ids.index(self.note_id))

    def _cycle_tab(self, step: int) -> None:
        if len(self.tabs_ids) > 1 and self.note_id in self.tabs_ids:
            self.open_note(self.tabs_ids[(self.tabs_ids.index(self.note_id) + step) % len(self.tabs_ids)])

    def _tabs_moved(self, *_):
        self.tabs_ids = [self.tabbar.tabData(i) for i in range(self.tabbar.count())]
        self.ctl.db.set_setting("notes_tabs", self.tabs_ids)

    def _tab_menu(self, pos):
        index = self.tabbar.tabAt(pos)
        nid = self.tabbar.tabData(index) if index >= 0 else None
        if nid is None:
            return
        menu = make_menu(self)
        add_action(menu, Icons.CLOSE, tr("close_tab"), lambda: self._close_tab(index))
        add_action(menu, Icons.CLOSE, tr("close_other_tabs"), lambda: self._close_others(nid))
        if nid != self.note_id:
            add_action(menu, Icons.PREVIEW, tr("open_beside"), lambda: self.open_beside(nid))
        menu.exec(self.tabbar.mapToGlobal(pos))

    def _close_others(self, keep: int) -> None:
        self.tabs_ids = [keep]
        self.open_note(keep)
        self._sync_tabs()

    # ================================================================ editor
    def _set_mode(self, mode: str, save: bool = True):
        self.editor.setVisible(mode in ("edit", "split"))
        self.preview_box.setVisible(mode in ("split", "preview") or self.beside_id is not None)
        self.editor.set_max_line_width(READABLE_WIDTH if mode == "edit" and self.beside_id is None else 0)
        self._update_preview()
        if save:
            self.ctl.db.set_setting("notes_mode", mode)

    def open_note(self, note_id: int | None) -> None:
        if note_id is None:
            return self._load(None)
        if self.cards_btn.isChecked() and self.collection[0] != "tasks":
            self.cards_btn.blockSignals(True)
            self.cards_btn.setChecked(False)
            self.cards_btn.blockSignals(False)
            self.view = "list"
        if note_id != self.note_id:
            self._save_now()
            if self.note_id:
                self.ctl.snapshot_note(self.note_id, force=True)
        self._load(note_id)
        note = self.ctl.db.get_note(note_id)
        if note and hasattr(self.ctl, "recents"):  # recent notes on the taskbar / Windows jump list
            title = (note.get("title") or (note.get("content") or "").strip().split("\n")[0] or tr("note"))[:80]
            self.ctl.recents.add("notes", "note", note_id, title)
        for i in range(self.notes.count()):
            if self.notes.item(i).data(ROLE) == note_id:
                self.notes.blockSignals(True)
                self.notes.setCurrentRow(i)
                self.notes.blockSignals(False)
                break

    def _load(self, note_id: int | None, show: bool = True):
        note = self.ctl.db.get_note(note_id) if note_id else None
        self.note_id = note["id"] if note else None
        if note and not note["locked"] and hasattr(self.ctl, "set_context"):  # the phone can continue this note
            first = next((ln.strip("# ").strip() for ln in note["content"].splitlines() if ln.strip()), "")
            self.ctl.set_context("note", id=note["id"], title=note["title"] or first[:60])
        if show or self.main_stack.currentIndex() in (0, 1):
            if self.collection[0] != "tasks" or (note and show):
                self._show_main(1 if note else 0)
        if not note:
            self._sync_tabs()
            return
        self._add_tab(note["id"])
        content = self.ctl.note_content(note)
        locked_closed = content is None
        self.body_stack.setCurrentIndex(1 if locked_closed else 0)
        trashed = bool(note["deleted_at"])
        self._loading = True
        self.title.setText(note["title"])
        self._loaded_title = note["title"]
        self.editor.setPlainText(content or "")
        self.editor.setReadOnly(trashed)
        self.title.setReadOnly(trashed)
        self.pin.setChecked(bool(note["is_pinned"]))
        self._fill_subjects(note["subject_id"])
        self._loading = False
        self._set_banner("trash" if trashed else "template" if note["is_template"] else
                         "inbox" if note["is_inbox"] else None)
        self.status.setText(tr("note_is_locked_short") if note["locked"] else tr("saved"))
        if self.find_bar.isVisible():
            self.find_bar.search(keep=False)
        self.outline.set_text(content or "")
        self._fill_backlinks(note)
        self._update_footer(note)
        self._update_preview()
        self._follow_cursor()

    def _fill_backlinks(self, note: dict) -> None:
        self.backlinks.clear()
        rows = [b for b in self.ctl.db.backlinks(note["title"]) if b["id"] != note["id"]]
        for b in rows:
            item = QListWidgetItem(glyph_icon(Icons.NOTES, Theme.accent2(), 16), b["title"] or tr("untitled"))
            item.setData(ROLE, b["id"])
            self.backlinks.addItem(item)
        self.backlinks_empty.setVisible(not rows)
        self.backlinks.setVisible(bool(rows))

    def _fill_subjects(self, selected):
        self.subject.blockSignals(True)
        self.subject.clear()
        self.subject.addItem(tr("no_subject"), None)
        for s in self.ctl.db.list_subjects():
            self.subject.addItem(s["name"], s["id"])
        self.subject.setCurrentIndex(max(0, self.subject.findData(selected)))
        self.subject.blockSignals(False)

    def _changed(self, *_):
        if self._loading or self.note_id is None or self.editor.isReadOnly():
            return
        self.status.setText(tr("saving"))
        self._save_timer.start()
        self._preview_timer.start()

    def _save_now(self, force: bool = False):
        if self.note_id is None or (not force and not self._save_timer.isActive() and self.status.text() != tr("saving")):
            return
        if self.body_stack.currentIndex() == 1 or self.editor.isReadOnly():
            return  # a locked note that isn't open, or a note in the trash
        self._save_timer.stop()
        if self.ctl.autosave_note(self.note_id, self.title.text().strip(), self.editor.toPlainText()):
            self.status.setText(tr("saved"))
        note = self.ctl.db.get_note(self.note_id)
        self._update_footer(note)
        if note and self.note_id in self.tabs_ids:
            i = self.tabs_ids.index(self.note_id)
            self.tabbar.setTabText(i, ("🔒 " if note["locked"] else "") + _note_title(note))

    def _title_done(self):
        new = self.title.text().strip()
        old = self._loaded_title.strip()
        if self.note_id and old and new and old.lower() != new.lower():
            self._save_now(force=True)
            self.ctl.rename_links(old, new)
        self._loaded_title = new

    def _set_field(self, **fields):
        if not self._loading and self.note_id is not None:
            self.ctl.update_note_fields(self.note_id, **fields)

    def _subject_changed(self, *_):
        sid = self.subject.currentData()
        self._set_field(subject_id=sid, **({"is_inbox": 0} if sid else {}))

    def _titles(self) -> set[str]:
        return {n["title"].strip().lower() for n in self.ctl.db.note_titles()}

    def _update_preview(self):
        text = self.editor.toPlainText()
        if self.side_info.isVisible():
            self.outline.set_text(text)
        if not self.preview_box.isVisible():
            return
        if self.beside_id is not None:
            other = self.ctl.db.get_note(self.beside_id)
            if other is None:
                return self.open_beside(None)
            self.preview.set_note(other["title"], self.ctl.note_content(other) or tr("note_is_locked"), self._titles())
        else:
            self.preview.set_note(self.title.text().strip(), text, self._titles())

    def _update_footer(self, note: dict | None):
        if note:
            st = stats(self.editor.toPlainText())
            self.footer.setText(tr("note_footer_full", words=ltr(str(st["words"])), chars=ltr(str(st["chars"])),
                                   minutes=st["minutes"], updated=relative_time(note["updated_at"])))

    # ------------------------------------------------------------ writing tools
    def _open_slash_menu(self):
        self.editor.setFocus()
        c = self.editor.textCursor()
        before = c.block().text()[:c.positionInBlock()]
        self.editor.insertPlainText(" /" if before and not before[-1].isspace() else "/")
        self.editor.open_menu_at(self.editor.textCursor().position() - 1)

    def _insert_note_link(self):
        self.editor.setFocus()
        self.editor.insertPlainText("[[")
        self.editor.open_link_menu(self.editor.textCursor().position())

    def _follow_cursor(self):
        line = self.editor.textCursor().blockNumber()
        if self.side_info.isVisible():
            section = self.outline.follow(line)
        else:
            section = next((t for _, l, t in reversed(note_outline(self.editor.toPlainText())) if l <= line), "")
        self.section.setText(("§ " + section[:50]) if section else "")
        if (self.preview_box.isVisible() and self.beside_id is None and self.editor.isVisible()
                and self.editor.hasFocus()):
            self.preview.scroll_to_line(line)

    def _jump_to_line(self, line: int):
        block = self.editor.document().findBlockByNumber(line)
        if block.isValid():
            c = self.editor.textCursor()
            c.setPosition(block.position())
            self.editor.setTextCursor(c)
            self.editor.centerCursor()
            self.editor.setFocus()
        if self.beside_id is None:
            self.preview.scroll_to_line(line, flash=True)

    def _toggle_outline(self, on: bool):
        self.side_info.setVisible(on)
        if on:
            self.outline.set_text(self.editor.toPlainText())
            self._follow_cursor()
        self.ctl.db.set_setting("notes_outline", on)

    def _find(self, replace: bool):
        if self.note_id is None or self.main_stack.currentIndex() != 1:
            self.search.setFocus()
            self.search.selectAll()
            return
        if not self.editor.isVisible():
            self.mode.set_current("split")
            self._set_mode("split")
        self.find_bar.open(replace)

    def _slash_action(self, key: str):
        if self.note_id is None:
            return
        if key == "image":
            paths, _ = QFileDialog.getOpenFileNames(self, tr("slash_image"), str(Path.home() / "Pictures"),
                                                    "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.svg)")
        else:
            paths, _ = QFileDialog.getOpenFileNames(self, tr("slash_file"), str(Path.home()))
        if paths:
            self.editor.attach_files(paths)

    def _preview_task(self, line: int):
        if self.beside_id is None and not self.editor.isReadOnly():
            self.editor.toggle_task_at(line)

    def _open_link(self, title: str):
        self._save_now()
        self.open_note(self.ctl.open_or_create_by_title(title))

    def _on_escape(self):
        if self.find_bar.isVisible():
            self.find_bar.close_bar()
        elif self.focus_mode:
            self.toggle_focus()

    def toggle_focus(self):
        """Only the text on screen; Esc or F8 brings everything back."""
        if (self.note_id is None or self.main_stack.currentIndex() != 1) and not self.focus_mode:
            return
        self.focus_mode = not self.focus_mode
        on = self.focus_mode
        for w in (self.side_panel, self.tools_row, self.mode, *getattr(self.main, "chrome", [])):
            w.setVisible(not on)
        if on:
            self._focus_restore = (self.mode.current, self.side_info.isVisible())
            self.tabbar.setVisible(False)
            self.side_info.setVisible(False)
            self.preview_box.setVisible(False)
            self.editor.setVisible(True)
            self.editor.set_max_line_width(FOCUS_WIDTH)
            self.editor.setFocus()
        else:
            mode, side_on = self._focus_restore
            self.side_info.setVisible(side_on)
            self.tabbar.setVisible(len(self.tabs_ids) > 1)
            self._set_mode(mode, save=False)
        self.focus_hint.setVisible(on)

    # --------------------------------------------------------------- actions
    def new_note(self, subject_id: int | None = None, folder_id: int | None = None):
        self._save_now()
        view, key = self.collection
        subject_id = subject_id or (key if view == "subject" else None)
        folder_id = folder_id or (key if view == "folder" else None)
        content = f"#{key} " if view == "tag" else ""
        note_id = self.ctl.create_note(subject_id=subject_id, is_inbox=view == "inbox", folder_id=folder_id,
                                       content=content, is_template=view == "templates")
        self.search.clear()
        if view in ("pinned", "trash", "tasks"):
            self.set_collection(("all", None))
        self.open_note(note_id)
        self._reload_list()
        self.title.setFocus()

    def _from_template(self, key) -> None:
        self._save_now()
        view, ck = self.collection
        note_id = self.ctl.new_note_from_template(key, folder_id=ck if view == "folder" else None,
                                                  subject_id=ck if view == "subject" else None)
        if view in ("templates", "trash", "pinned", "tasks", "inbox", "tag"):
            self.set_collection(("all", None))
        self.open_note(note_id)
        self._reload_list()

    def open_daily(self) -> None:
        self._save_now()
        note_id = self.ctl.daily_note()
        self.open_note(note_id)
        self._reload_nav()
        self._reload_list()

    def quick_switch(self) -> None:
        from app.ui.dialogs.notes_dialogs import QuickSwitcher
        dlg = QuickSwitcher(self.ctl, self)
        if dlg.exec() and dlg.result_id:
            self.open_note(dlg.result_id)

    def open_beside(self, note_id: int | None) -> None:
        self.beside_id = note_id if note_id != self.note_id else None
        note = self.ctl.db.get_note(self.beside_id) if self.beside_id else None
        self.beside_bar.setVisible(note is not None)
        if note:
            self.beside_label.setText(tr("beside_label", name=_note_title(note)))
            if self.mode.current == "preview":
                self.mode.set_current("split")
        self._set_mode(self.mode.current, save=False)

    def select_note(self, note_id: int | None = None, subject_id: int | None = None):
        if subject_id is not None:
            self.collection = ("subject", subject_id)
        elif note_id is not None:
            self.collection = ("all", None)
        self.search.clear()
        self._reload_nav()
        self._reload_list(select_first=note_id is None)
        if note_id is not None:
            self.open_note(note_id)

    def _note_menu(self, note_id: int, pos, ids: list[int] | None = None):
        note = self.ctl.db.get_note(note_id)
        if not note:
            return
        ids = ids or [note_id]
        ctl = self.ctl
        menu = make_menu(self)
        if note["deleted_at"]:
            add_action(menu, Icons.UNDO, tr("restore"), lambda: [ctl.restore_note(i) for i in ids])
            add_action(menu, Icons.DELETE, tr("delete_forever"), lambda: ctl.delete_note_forever(ids, self),
                       danger=True)
            return menu.exec(pos)
        if note_id != self.note_id:
            add_action(menu, Icons.PREVIEW, tr("open_beside"), lambda: self.open_beside(note_id))
        add_action(menu, Icons.PIN, tr("unpin") if note["is_pinned"] else tr("pin"),
                   lambda: [ctl.update_note_fields(i, is_pinned=0 if note["is_pinned"] else 1) for i in ids])
        move = add_submenu(menu, Icons.MOVE, tr("move_to_folder"))
        add_action(move, None, tr("no_folder"), lambda: ctl.move_notes(ids, None))
        for f in ctl.db.list_folders():
            act = add_action(move, Icons.FOLDER, f["name"], lambda fid=f["id"]: ctl.move_notes(ids, fid))
            act.setEnabled(not (len(ids) == 1 and note["folder_id"] == f["id"]))
        move.addSeparator()
        add_action(move, Icons.NEW_FOLDER, tr("new_folder") + "…",
                   lambda: (lambda fid: fid and ctl.move_notes(ids, fid))(ctl.new_folder(None, self)))
        if note["is_inbox"]:
            add_action(menu, Icons.ACCEPT, tr("mark_filed"), lambda: [ctl.file_note(i) for i in ids])
        if len(ids) == 1:
            menu.addSeparator()
            add_action(menu, Icons.CLOCK, tr("version_history") + "…", lambda: self._history(note_id))
            if not note["locked"]:
                add_action(menu, Icons.COPY, tr("copy_text"), lambda: ctl.copy_text(note["content"]))
            if not note["is_template"]:
                add_action(menu, Icons.STAR, tr("save_as_template"), lambda: ctl.save_as_template(note_id))
            add_action(menu, Icons.KEY if note["locked"] else Icons.LOCK,
                       tr("remove_lock") if note["locked"] else tr("lock_note"),
                       lambda: (ctl.unlock_note if note["locked"] else ctl.lock_note)(note_id, self))
            export = add_submenu(menu, Icons.SAVE, tr("export"))
            add_action(export, Icons.DOCUMENT, "Markdown (.md)", lambda: self._export(note, "md"))
            add_action(export, Icons.GLOBE, "HTML (.html)", lambda: self._export(note, "html"))
            add_action(export, Icons.DOCUMENT, "PDF (.pdf)", lambda: self._export(note, "pdf"))
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("move_to_trash"), lambda: [ctl.delete_note(i, self) for i in ids],
                   danger=True)
        menu.exec(pos)

    def _history(self, note_id: int) -> None:
        from app.ui.dialogs.notes_dialogs import HistoryDialog
        if note_id == self.note_id:
            self._save_now(force=True)
        note = self.ctl.db.get_note(note_id)
        if note["locked"]:
            return self.ctl.notify(tr("locked_no_history"), "info")
        if HistoryDialog(self.ctl, note_id, note["content"], self).exec() and note_id == self.note_id:
            self._load(note_id)

    # ----------------------------------------------------------- import/export
    def _export(self, note: dict, kind: str):
        if note["id"] == self.note_id:
            self._save_now(force=True)
        content = self.ctl.note_content(self.ctl.db.get_note(note["id"]))
        if content is None:
            if not self.ctl.ensure_vault_unlocked(self):
                return
            content = self.ctl.note_content(self.ctl.db.get_note(note["id"])) or ""
        name = (note["title"] or "note").strip()[:60] or "note"
        filters = {"md": "Markdown (*.md)", "html": "HTML (*.html)", "pdf": "PDF (*.pdf)"}
        path, _ = QFileDialog.getSaveFileName(self, tr("export"), str(Path.home() / "Documents" / f"{name}.{kind}"),
                                              filters[kind])
        if not path:
            return
        if kind == "md":
            folder = str(Path(path).parent)
            from app.core.notes_io import export_markdown
            tmp = dict(note)
            export_markdown(self.ctl.db, [tmp], Path(folder), lambda _n: content)
            self.ctl.notify(tr("exported"), "success")
            return
        # HTML / PDF are made from the rendered preview of that note
        self.preview.set_note(note["title"], content, self._titles())

        def after_render():
            if kind == "pdf":
                self.preview.export_pdf(path, lambda ok: (self.ctl.notify(tr("exported") if ok else tr("export_failed"),
                                                                          "success" if ok else "error"),
                                                          self._update_preview()))
            else:
                def write(html):
                    Path(path).write_text(html, encoding="utf-8")
                    self.ctl.notify(tr("exported"), "success")
                    self._update_preview()
                self.preview.export_html(note["title"], write)
        QTimer.singleShot(1800, after_render)  # let KaTeX / Mermaid finish drawing

    def _export_all(self):
        folder = QFileDialog.getExistingDirectory(self, tr("export_all_md"), str(Path.home() / "Documents"))
        if folder:
            self._save_now(force=True)
            target = Path(folder) / "Study Hub notes"
            self.ctl.export_notes_markdown(str(target))
            self.ctl.open_folder(str(target))

    def _import(self, folder: bool):
        if folder:
            path = QFileDialog.getExistingDirectory(self, tr("import_md_folder"), str(Path.home() / "Documents"))
            paths = [path] if path else []
        else:
            paths, _ = QFileDialog.getOpenFileNames(self, tr("import_md_files"), str(Path.home() / "Documents"),
                                                    "Markdown / Text (*.md *.markdown *.txt)")
        if paths:
            self.ctl.import_notes(paths)

    # --------------------------------------------------------------- refresh
    def refresh(self, what: str = ""):
        if what not in ("", "notes", "subjects", "vault"):
            return
        if what == "vault":
            note = self.ctl.db.get_note(self.note_id) if self.note_id else None
            if note and note["locked"]:
                self._save_now()
                self._load(self.note_id, show=False)
            return
        if self.note_id is not None and not self._note_still_exists():
            self.note_id = None
        self._reload_nav()
        self._reload_list()
        note = self.ctl.db.get_note(self.note_id) if self.note_id else None
        if note:  # keep the editor untouched, only sync the side controls
            self._loading = True
            self.pin.setChecked(bool(note["is_pinned"]))
            self._fill_subjects(note["subject_id"])
            self._loading = False
            trashed = bool(note["deleted_at"])
            if trashed != self.editor.isReadOnly():
                self._load(note["id"], show=False)
            else:
                self._set_banner("trash" if trashed else "template" if note["is_template"] else
                                 "inbox" if note["is_inbox"] else None)
            if self.status.text() != tr("saving") and note["content"] != self.editor.toPlainText() \
                    and not note["locked"] and not self._save_timer.isActive():
                self._load(note["id"], show=False)  # changed elsewhere (Tasks view, version restored…)
            self._fill_backlinks(note)
            self._sync_tabs()
        elif self.main_stack.currentIndex() == 1:
            self._load(None)
        if self.main_stack.currentIndex() == 3:
            self._fill_tasks()

    def on_shown(self):
        if self.note_id is None and self.collection[0] != "tasks":
            if self.tabs_ids:
                self.open_note(self.tabs_ids[-1])
            else:
                self._reload_list(select_first=True)

    def dispose(self):
        if self.focus_mode:
            self.toggle_focus()
        self._save_now()
        if self.note_id:
            self.ctl.snapshot_note(self.note_id, force=True)
        super().dispose()
