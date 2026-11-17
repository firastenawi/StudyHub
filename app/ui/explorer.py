"""
Study Hub Explorer — a compact file manager the app uses instead of Windows Explorer.

* sidebar: quick access (Desktop, Documents, Downloads…), your subject folders, drives with free space
* details / icons view (QFileSystemModel: native icons, live updates, fast on big folders)
* back / forward / up, editable address bar, filter, sort by any column
* preview pane: images, PDF first page, text files, folder info
* Ctrl+C / Ctrl+X / Ctrl+V (interoperable with Windows Explorer), F2 rename, Del -> Recycle Bin,
  Ctrl+Shift+N new folder, drag & drop, plus Study Hub actions: pin, tag, archive
"""
from __future__ import annotations

import ctypes
import os
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QDir, QModelIndex, QSize, QStorageInfo, Qt, QTimer
from PyQt6.QtGui import QFileSystemModel, QFont, QIcon, QImageReader, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView,
                             QListWidget, QListWidgetItem, QSplitter, QStackedWidget, QTreeView, QVBoxLayout,
                             QWidget)

from app.config import APP_NAME
from app.core import fileops
from app.core.files import format_mtime, human_size
from app.core.i18n import is_rtl, ltr, tr
from app.ui.theme import Icons, Palette, Theme, app_icon, ui_font
from app.ui.widgets.common import IconButton, add_action, add_submenu, label, make_menu
from app.ui.widgets.files import file_icon

TEXT_EXT = {".txt", ".md", ".py", ".json", ".csv", ".log", ".ini", ".xml", ".html", ".css", ".js", ".c", ".cpp",
            ".java", ".cs", ".sql", ".yaml", ".yml"}


def _dark_title_bar(widget: QWidget) -> None:
    """Windows 10/11 dark title bar (DWMWA_USE_IMMERSIVE_DARK_MODE)."""
    try:
        value = ctypes.c_int(1)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(int(widget.winId()), attr, ctypes.byref(value), 4)
    except Exception:
        pass


class PreviewPane(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumWidth(240)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(180)
        lay.addWidget(self.image)
        self.name = label("", 11, QFont.Weight.DemiBold, wrap=True)
        lay.addWidget(self.name)
        self.meta = label("", 9, role="Dim", wrap=True)
        lay.addWidget(self.meta)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.text.setFont(ui_font(8.8))
        self.text.setObjectName("Dim")
        lay.addWidget(self.text, 1)

    def show_path(self, path: str | None):
        self.text.clear()
        if not path or not os.path.exists(path):
            self.image.clear()
            self.name.setText("")
            self.meta.setText(tr("preview_hint"))
            return
        p = Path(path)
        st = p.stat()
        self.name.setText(ltr(p.name))
        width = max(200, self.width() - 30)
        if p.is_dir():
            try:
                count = len(os.listdir(path))
            except OSError:
                count = 0
            self.image.setPixmap(file_icon(path, True, 96))
            self.meta.setText(tr("folder_items", n=count) + "\n" + ltr(format_mtime(st.st_mtime)))
            return
        self.meta.setText(f"{ltr(human_size(st.st_size))} · {ltr(format_mtime(st.st_mtime))}")
        ext = p.suffix.lower()
        if ext == ".pdf" and self._show_pdf(path, width):
            return
        if ext != ".pdf" and bytes(ext[1:], "ascii", "ignore") in [bytes(f) for f in QImageReader.supportedImageFormats()]:
            pm = QPixmap(path)
            if not pm.isNull():
                self.image.setPixmap(pm.scaled(QSize(width, 260), Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
                self.meta.setText(self.meta.text() + f" · {pm.width()}×{pm.height()}")
                return
        self.image.setPixmap(file_icon(path, False, 96))
        if ext in TEXT_EXT and st.st_size < 2_000_000:
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    self.text.setText("".join(fh.readlines()[:40])[:2500])
            except OSError:
                pass


    def _show_pdf(self, path: str, width: int) -> bool:
        """First page rendered on white (PDF pages are transparent), sharp at any DPI."""
        try:
            from PyQt6.QtGui import QColor, QPainter
            from PyQt6.QtPdf import QPdfDocument
            doc = QPdfDocument(self)
            doc.load(path)
            if not doc.pageCount():
                return False
            page = doc.pagePointSize(0)
            target_h = 340
            scale = min(width / max(1.0, page.width()), target_h / max(1.0, page.height()))
            dpr = self.devicePixelRatioF()
            size = QSize(int(page.width() * scale * dpr), int(page.height() * scale * dpr))
            rendered = doc.render(0, size)
            sheet = QPixmap(size)
            sheet.fill(QColor("#FFFFFF"))
            p = QPainter(sheet)
            p.drawImage(0, 0, rendered)
            p.end()
            sheet.setDevicePixelRatio(dpr)
            self.image.setPixmap(sheet)
            self.meta.setText(self.meta.text() + " · " + tr("pages_n", n=doc.pageCount()))
            doc.close()
            return True
        except Exception:
            return False


class ExplorerWindow(QWidget):
    def __init__(self, ctl, start_path: str | None = None, select: str | None = None):
        super().__init__(None, Qt.WindowType.Window)
        self.ctl = ctl
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{APP_NAME} — {tr('explorer')}")
        self.setWindowIcon(app_icon())
        self.resize(1180, 720)
        self.setStyleSheet("ExplorerWindow { background: %s; }" % Palette.bg)
        self.history: list[str] = []
        self.hist_pos = -1
        self.show_hidden = False

        self.model = QFileSystemModel(self)
        self.model.setRootPath("")
        self.model.setReadOnly(False)  # rename in place + drag & drop
        self.model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.AllDirs)
        self.model.setNameFilterDisables(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 8)
        root.setSpacing(8)
        root.addLayout(self._toolbar())

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._sidebar())
        self.views = QStackedWidget()
        self.views.addWidget(self._details_view())
        self.views.addWidget(self._icons_view())
        split.addWidget(self.views)
        self.preview = PreviewPane()
        split.addWidget(self.preview)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 700, 260])
        root.addWidget(split, 1)
        self.status = label("", 8.8, role="Faint")
        root.addWidget(self.status)
        self._shortcuts()
        self.navigate(start_path or str(Path.home()))
        if select:
            QTimer.singleShot(300, lambda: self._select(select))

    # ------------------------------------------------------------ building
    def _toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(4)
        for glyph, tip, slot in ((Icons.BACK, tr("back"), self.back), (Icons.FORWARD, tr("forward"), self.forward),
                                 (Icons.UP, tr("folder_up"), self.up), (Icons.REFRESH, tr("reload"), self.refresh)):
            b = IconButton(glyph, tip, 34, 10.5)
            b.clicked.connect(slot)
            bar.addWidget(b)
            if glyph == Icons.BACK:
                self.back_btn = b
            elif glyph == Icons.FORWARD:
                self.fwd_btn = b
        self.address = QLineEdit()
        self.address.setFont(ui_font(10))
        self.address.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.address.returnPressed.connect(lambda: self.navigate(self.address.text().strip().strip('"')))
        bar.addWidget(self.address, 1)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(tr("filter_files"))
        self.filter.setClearButtonEnabled(True)
        self.filter.setFixedWidth(220)
        self.filter.textChanged.connect(self._apply_filter)
        bar.addWidget(self.filter)
        for glyph, tip, slot in ((Icons.NEW_FOLDER, tr("new_folder") + " (Ctrl+Shift+N)", self.new_folder),
                                 (Icons.VIEW_LIST, tr("view_details"), lambda: self._set_view(0)),
                                 (Icons.VIEW_GRID, tr("view_icons"), lambda: self._set_view(1)),
                                 (Icons.PREVIEW, tr("toggle_preview"), self._toggle_preview),
                                 (Icons.OPEN_FOLDER, tr("open_in_windows_explorer"),
                                  lambda: os.startfile(self.current))):
            b = IconButton(glyph, tip, 34, 10.5)
            b.clicked.connect(slot)
            bar.addWidget(b)
        return bar

    def _sidebar(self) -> QWidget:
        self.places = QListWidget()
        self.places.setMinimumWidth(190)
        self.places.itemClicked.connect(lambda it: it.data(Qt.ItemDataRole.UserRole) and
                                        self.navigate(it.data(Qt.ItemDataRole.UserRole)))
        self._fill_places()
        return self.places

    def _fill_places(self):
        self.places.clear()

        def section(text):
            it = QListWidgetItem(text)
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setFont(ui_font(8.4, QFont.Weight.DemiBold))
            self.places.addItem(it)

        def place(name, path, is_dir=True):
            it = QListWidgetItem(name)
            it.setIcon(QIcon(file_icon(path, is_dir, 20)))
            it.setData(Qt.ItemDataRole.UserRole, path)
            it.setToolTip(path)
            it.setSizeHint(QSize(0, 32))
            self.places.addItem(it)

        section(tr("quick_access"))
        for key, path in fileops.known_folders():
            place(tr(f"kf_{key}"), path)
        subjects = [(s["name"], f) for s in self.ctl.db.list_subjects() for f in self.ctl.subject_folders(s["id"])[:1]]
        if subjects:
            section(tr("tab_subjects"))
            for name, path in subjects:
                if os.path.isdir(path):
                    place(name, path)
        section(tr("this_pc"))
        for vol in QStorageInfo.mountedVolumes():
            if not vol.isValid() or not vol.isReady():
                continue
            root = vol.rootPath()
            free = human_size(vol.bytesAvailable())
            name = f"{vol.displayName() or root}  ·  {tr('free_space', size=free)}"
            place(name, root)

    def _details_view(self) -> QTreeView:
        v = QTreeView()
        v.setModel(self.model)
        v.setRootIsDecorated(False)
        v.setItemsExpandable(False)
        v.setSortingEnabled(True)
        v.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        v.setUniformRowHeights(True)
        v.setAlternatingRowColors(False)
        v.setIconSize(QSize(20, 20))
        v.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):  # size / type / date: as wide as their content
            v.header().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        v.header().setStretchLastSection(False)
        self._common_view(v)
        self.details = v
        return v

    def _icons_view(self) -> QListView:
        v = QListView()
        v.setModel(self.model)
        v.setViewMode(QListView.ViewMode.IconMode)
        v.setIconSize(QSize(56, 56))
        v.setGridSize(QSize(118, 104))
        v.setResizeMode(QListView.ResizeMode.Adjust)
        v.setWordWrap(True)
        v.setUniformItemSizes(True)
        self._common_view(v)
        self.icons = v
        return v

    def _common_view(self, v):
        # file lists read left-to-right like Windows (sizes "7.3 KB" and dates otherwise come out mirrored)
        v.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        v.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        v.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)  # F2 renames
        v.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        v.setDefaultDropAction(Qt.DropAction.CopyAction)
        v.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        v.customContextMenuRequested.connect(lambda pos, view=v: self._menu(view, pos))
        v.doubleClicked.connect(self._activate)
        v.setFont(ui_font(9.8))
        v.setStyleSheet("QTreeView, QListView { background: transparent; border: none; }"
                        "QTreeView::item, QListView::item { padding: 3px; border-radius: 6px; }"
                        "QTreeView::item:selected, QListView::item:selected { background: rgba(124,92,255,70); }"
                        "QHeaderView { background: transparent; border: none; }"
                        "QHeaderView::section { background: transparent; color: %s; border: none; padding: 6px; }"
                        % Palette.text_dim)

    def _shortcuts(self):
        def sc(keys, slot):
            QShortcut(QKeySequence(keys), self, activated=slot)

        sc("Alt+Left", self.back)
        sc("Backspace", self.back)
        sc("Alt+Right", self.forward)
        sc("Alt+Up", self.up)
        sc("F5", self.refresh)
        sc("Ctrl+L", lambda: (self.address.setFocus(), self.address.selectAll()))
        sc("Ctrl+F", lambda: (self.filter.setFocus(), self.filter.selectAll()))
        sc("Ctrl+C", lambda: self._clip(cut=False))
        sc("Ctrl+X", lambda: self._clip(cut=True))
        sc("Ctrl+V", self.paste)
        sc("Delete", self.delete)
        sc("Ctrl+Shift+N", self.new_folder)
        sc("Return", lambda: [self._activate(i) for i in self._selected_indexes()[:10]])

    # ------------------------------------------------------------ navigation
    @property
    def view(self):
        return self.views.currentWidget()

    def navigate(self, path: str, record: bool = True):
        path = os.path.normpath(os.path.expandvars(path)) if path else path
        if not path or not os.path.isdir(path):
            self.address.setText(getattr(self, "current", ""))
            self.ctl.notify(tr("error_path_missing"), "error")
            return
        self.current = path
        if record:
            self.history = self.history[:self.hist_pos + 1] + [path]
            self.hist_pos = len(self.history) - 1
        index = self.model.setRootPath(path)
        for v in (self.details, self.icons):
            v.setRootIndex(index)
            v.clearSelection()
        self.address.setText(path)
        self.filter.clear()
        self.setWindowTitle(f"{Path(path).name or path} — {tr('explorer')}")
        self.back_btn.setEnabled(self.hist_pos > 0)
        self.fwd_btn.setEnabled(self.hist_pos < len(self.history) - 1)
        self.preview.show_path(None)
        QTimer.singleShot(250, self._update_status)

    def back(self):
        if self.hist_pos > 0:
            self.hist_pos -= 1
            self.navigate(self.history[self.hist_pos], record=False)

    def forward(self):
        if self.hist_pos < len(self.history) - 1:
            self.hist_pos += 1
            self.navigate(self.history[self.hist_pos], record=False)

    def up(self):
        parent = os.path.dirname(self.current.rstrip("\\/"))
        if parent and parent != self.current:
            self.navigate(parent)

    def refresh(self):
        self.navigate(self.current, record=False)
        self._fill_places()

    def _activate(self, index: QModelIndex):
        path = self.model.filePath(index)
        if self.model.isDir(index):
            self.navigate(path)
        else:
            os.startfile(path)

    def _select(self, path: str):
        index = self.model.index(path)
        if index.isValid():
            self.view.setCurrentIndex(index)
            self.view.scrollTo(index)

    def _set_view(self, i: int):
        self.views.setCurrentIndex(i)

    def _toggle_preview(self):
        self.preview.setVisible(not self.preview.isVisible())

    def _apply_filter(self, text: str):
        self.model.setNameFilters([f"*{text}*"] if text else [])

    # ------------------------------------------------------------- selection
    def _selected_indexes(self) -> list[QModelIndex]:
        return [i for i in self.view.selectionModel().selectedIndexes() if i.column() == 0]

    def selected_paths(self) -> list[str]:
        return [self.model.filePath(i) for i in self._selected_indexes()]

    def _update_status(self):
        root = self.model.index(self.current)
        count = self.model.rowCount(root)
        paths = self.selected_paths() if self.view.selectionModel() else []
        text = tr("folder_items", n=count)
        if paths:
            size = sum(os.path.getsize(p) for p in paths if os.path.isfile(p))
            text += "  ·  " + tr("selected_n", n=len(paths)) + (f" ({human_size(size)})" if size else "")
        vol = QStorageInfo(self.current)
        if vol.isValid():
            text += "  ·  " + tr("free_space", size=human_size(vol.bytesAvailable()))
        self.status.setText(text)
        self.preview.show_path(paths[0] if len(paths) == 1 else None)

    def showEvent(self, e):
        super().showEvent(e)
        _dark_title_bar(self)
        for v in (self.details, self.icons):
            v.selectionModel().selectionChanged.connect(lambda *_: self._update_status())

    # ------------------------------------------------------------- operations
    def _clip(self, cut: bool):
        paths = self.selected_paths()
        if paths:
            fileops.clipboard_set_files(paths, cut)
            self.ctl.notify(tr("cut_n" if cut else "copied_items", n=len(paths)), "info")

    def paste(self):
        paths, cut = fileops.clipboard_get_files()
        if not paths:
            return
        try:
            (fileops.move_into if cut else fileops.copy_into)(paths, self.current)
            self.ctl.notify(tr("pasted_n", n=len(paths)), "success")
        except OSError as exc:
            self.ctl.notify(f"{tr('error')}: {exc}", "error")

    def delete(self):
        paths = self.selected_paths()
        if paths and fileops.recycle(paths):
            self.ctl.notify(tr("recycled_n", n=len(paths)), "info")

    def new_folder(self):
        try:
            path = fileops.new_folder(self.current, tr("new_folder"))
        except OSError as exc:
            self.ctl.notify(f"{tr('error')}: {exc}", "error")
            return
        QTimer.singleShot(250, lambda: (self._select(path), self.view.edit(self.model.index(path))))

    def _menu(self, view, pos):
        index = view.indexAt(pos)
        if index.isValid() and index not in view.selectionModel().selectedIndexes():
            view.setCurrentIndex(index)
        paths = self.selected_paths() if index.isValid() else []
        ctl = self.ctl
        menu = make_menu(self)
        if paths:
            first = paths[0]
            add_action(menu, Icons.CHEVRON, tr("open"), lambda: [self._activate(self.model.index(p)) for p in paths[:10]],
                       "Enter")
            if os.path.isdir(first):
                add_action(menu, Icons.OPEN_FOLDER, tr("open_new_window"), lambda: ctl.open_explorer(first))
            menu.addSeparator()
            add_action(menu, Icons.CUT, tr("cut"), lambda: self._clip(True), "Ctrl+X")
            add_action(menu, Icons.COPY, tr("copy"), lambda: self._clip(False), "Ctrl+C")
        paste = add_action(menu, Icons.PASTE, tr("paste"), self.paste, "Ctrl+V")
        paste.setEnabled(bool(fileops.clipboard_get_files()[0]))
        if paths:
            add_action(menu, Icons.EDIT, tr("rename"), lambda: view.edit(view.currentIndex()), "F2")
            add_action(menu, Icons.DELETE, tr("to_recycle_bin"), self.delete, "Del", danger=True)
            menu.addSeparator()
            sh = add_submenu(menu, Icons.EDUCATION, "Study Hub")
            add_action(sh, Icons.PIN, tr("pin_launcher"), lambda: [ctl.add_path(p) for p in paths])
            add_action(sh, Icons.TAG, tr("tag_file"), lambda: ctl.tag_paths(paths, self))
            add_action(sh, Icons.ARCHIVE, tr("archive_action"), lambda: ctl.archive_dialog(paths, self))
            if first.lower().endswith(".pdf"):
                add_action(sh, Icons.PREVIEW, tr("open_as_window"), lambda: ctl.pin_widget("pdf", first))
            add_action(sh, Icons.NOTES, tr("ctx_note_for"), lambda: ctl.handle_command({"action": "note_for", "paths": paths}))
            add_action(sh, Icons.PHONE, tr("ctx_send_phone"), lambda: ctl.send_paths_to_phone(paths))
            if first.lower().endswith(".pdf"):
                add_action(sh, Icons.DOCUMENT, tr("pdf_open_in_reader"), lambda: ctl.open_pdf(first))
            add_action(menu, Icons.COPY, tr("copy_path"), lambda: ctl.copy_text("\n".join(paths)))
        else:
            add_action(menu, Icons.NEW_FOLDER, tr("new_folder"), self.new_folder, "Ctrl+Shift+N")
            add_action(menu, Icons.PIN, tr("ctx_pin_this_folder"), lambda: ctl.add_path(self.current))
        menu.addSeparator()
        add_action(menu, Icons.OPEN_FOLDER, tr("open_in_windows_explorer"),
                   lambda: (ctl.reveal_path(paths[0]) if paths else os.startfile(self.current)))
        menu.exec(view.viewport().mapToGlobal(pos))
