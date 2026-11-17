"""
Study Hub PDF reader — works as its own program (StudyHub.exe --pdf "file.pdf", "Open with" in
Explorer, Start-menu shortcut) and from inside Study Hub.

  * continuous pages, zoom (fit width / page, Ctrl+wheel), page box, thumbnails, table of contents
  * select text with the mouse -> copy, translate (T), highlight in 4 colors, highlight with a note, send to a note
  * highlights are saved (also when the file is moved / renamed) and listed in the side panel
  * search with all hits marked, Enter / Shift+Enter to jump
  * night mode (inverted pages), full screen (F11), remembers page + zoom per file, recent files
  * "Highlights -> note": one note per PDF with every highlight and a link back to its page
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from PyQt6.QtCore import QModelIndex, QPointF, QRectF, QSize, QSizeF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QPixmap, QPolygonF, QShortcut
from PyQt6.QtPdf import QPdfBookmarkModel, QPdfDocument, QPdfSearchModel
from PyQt6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLineEdit, QListWidget,
                             QListWidgetItem, QMainWindow, QMenu, QScrollArea, QSpinBox, QSplitter, QStackedWidget,
                             QTreeView, QVBoxLayout, QWidget)

from app.core import winapi
from app.core.i18n import ltr, relative_time, tr
from app.ui.theme import Icons, Palette, Theme, ui_font, ink, ink_css
from app.ui.widgets.common import EmptyState, GlyphLabel, IconButton, add_action, button, label, make_menu, primary_button
from app.ui.widgets.segmented import SegmentedTabs

HL_COLORS = ["#FBBF24", "#34D399", "#60A5FA", "#F472B6"]
GAP = 14
DPI = 96 / 72  # PDF points -> screen pixels at 100 %


def file_key(path: str) -> str:
    """Identity that survives moving / renaming the file: size + hash of the first megabyte."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(1024 * 1024)
    except OSError:
        return "missing:" + os.path.normcase(path)
    return f"{size}-{hashlib.sha1(head).hexdigest()[:20]}"


def render_page(doc: QPdfDocument, page: int, size: QSize) -> QImage:
    """Page on white paper: PDFium leaves the background transparent when the PDF doesn't paint one,
    which showed dark text on the dark window."""
    img = QImage(size, QImage.Format.Format_RGB32)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    p.drawImage(0, 0, doc.render(page, size))
    p.end()
    return img


def line_rects(boxes: list[QRectF], page_boxes: list[QRectF] | None = None) -> list[QRectF]:
    """PDFium gives one tight box per glyph (none for spaces, "o" lower than "l"): merge them into one
    even bar per line, so a selection / highlight covers the text like in other readers. A big gap on
    the same line (another column) starts a new bar. page_boxes (all glyphs on the page) give every
    bar the full height of its line, also when only "aeo" was selected."""
    boxes = [b for b in boxes if b.width() > 0 and b.height() > 0]
    lines: list[list[QRectF]] = []
    for b in sorted(boxes, key=lambda r: r.center().y()):
        if lines:
            top = min(r.top() for r in lines[-1])
            bottom = max(r.bottom() for r in lines[-1])
            overlap = min(bottom, b.bottom()) - max(top, b.top())
            if overlap >= 0.5 * min(b.height(), bottom - top):
                lines[-1].append(b)
                continue
        lines.append([b])
    out: list[QRectF] = []
    for line in lines:
        top = min(r.top() for r in line)
        bottom = max(r.bottom() for r in line)
        for c in page_boxes or ():
            if c.height() > 0 and min(bottom, c.bottom()) - max(top, c.top()) >= 0.5 * min(c.height(), bottom - top)                     and c.height() < 2 * (bottom - top):
                top, bottom = min(top, c.top()), max(bottom, c.bottom())
        h = bottom - top
        line.sort(key=lambda r: r.left())
        start = prev = line[0]
        right = prev.right()
        for r in line[1:] + [None]:
            if r is None or r.left() - right > 1.5 * h:
                out.append(QRectF(start.left() - 0.8, top - 0.15 * h, right - start.left() + 1.6, h * 1.3))
                if r is not None:
                    start = r
            if r is not None:
                right = max(right, r.right())
    return out


def pdf_link(path: str, page: int) -> str:
    """Link used inside notes; clicking it opens the reader at that page."""
    return f"studyhub:pdf/{quote(path, safe='')}#{page + 1}"


QR_GLYPH = "\uED14"  # Segoe Fluent Icons: QR code

class PageView(QWidget):
    """All pages one under the other; paints only what is visible (with a small render cache)."""

    selection_made = pyqtSignal(int, str, list, QRectF)   # page, text, rects (points), anchor (widget coords)
    highlight_clicked = pyqtSignal(dict, QRectF)
    page_changed = pyqtSignal(int)

    def __init__(self, reader):
        super().__init__()
        self.reader = reader
        self.doc: QPdfDocument = reader.doc
        self.zoom = 1.0
        self.night = False
        self.rects: list[QRectF] = []          # page rectangles in widget coordinates
        self.cache: dict[tuple, QImage] = {}
        self.highlights: dict[int, list[dict]] = {}
        self.search_hits: dict[int, list[QRectF]] = {}
        self.current_hit: tuple[int, QRectF] | None = None
        self.sel_page = -1
        self.sel_polys: list[QPolygonF] = []
        self.sel_rects: list[QRectF] = []          # sel_polys merged into one bar per line
        self._hl_rects: dict[int, list[QRectF]] = {}
        self._press: tuple[int, QPointF] | None = None
        self._boxes: dict[int, list[QRectF]] = {}   # character boxes per page (for snapping)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.IBeamCursor)

    # ------------------------------------------------------------- layout
    def relayout(self) -> None:
        self._boxes = {}
        self.rects = []
        if getattr(self.reader, "spread", False):  # two pages side by side, like an open book
            self._relayout_spread()
            return
        y = GAP
        width = 0
        for i in range(self.doc.pageCount()):
            size = self.doc.pagePointSize(i) * DPI * self.zoom
            self.rects.append(QRectF(0, y, size.width(), size.height()))
            y += size.height() + GAP
            width = max(width, size.width())
        for r in self.rects:
            r.moveLeft((width - r.width()) / 2 + GAP)
        self.setFixedSize(int(width + 2 * GAP), int(y))
        self.cache.clear()
        self.update()

    def _relayout_spread(self) -> None:
        n = self.doc.pageCount()
        sizes = [self.doc.pagePointSize(i) * DPI * self.zoom for i in range(n)]
        y = GAP
        width = 0
        for i in range(0, n, 2):
            pair = sizes[i:i + 2]
            row_w = sum(s.width() for s in pair) + GAP * (len(pair) - 1)
            row_h = max(s.height() for s in pair)
            x = 0
            for s in pair:
                self.rects.append(QRectF(x, y, s.width(), s.height()))
                x += s.width() + GAP
            y += row_h + GAP
            width = max(width, row_w)
        for i in range(0, len(self.rects), 2):  # centre each pair
            pair = self.rects[i:i + 2]
            row_w = pair[-1].right() - pair[0].left()
            dx = (width - row_w) / 2 + GAP
            for r in pair:
                r.moveLeft(r.left() + dx)
        self.setFixedSize(int(width + 2 * GAP), int(y))
        self.cache.clear()
        self.update()

    def page_at(self, pos: QPointF) -> int:
        spread = getattr(self.reader, "spread", False)
        for i, r in enumerate(self.rects):
            if r.top() - GAP / 2 <= pos.y() <= r.bottom() + GAP / 2 and (
                    not spread or r.left() - GAP / 2 <= pos.x() <= r.right() + GAP / 2):
                return i
        return -1

    def to_points(self, page: int, pos: QPointF) -> QPointF:
        r = self.rects[page]
        return QPointF((pos.x() - r.left()) / (DPI * self.zoom), (pos.y() - r.top()) / (DPI * self.zoom))

    def to_widget(self, page: int, rect: QRectF) -> QRectF:
        r, k = self.rects[page], DPI * self.zoom
        return QRectF(r.left() + rect.x() * k, r.top() + rect.y() * k, rect.width() * k, rect.height() * k)

    def _poly_to_widget(self, page: int, poly: QPolygonF) -> QPolygonF:
        r, k = self.rects[page], DPI * self.zoom
        return QPolygonF([QPointF(r.left() + p.x() * k, r.top() + p.y() * k) for p in poly])

    # ------------------------------------------------------------- render
    def _image(self, page: int) -> QImage:
        key = (page, round(self.zoom, 3), self.night)
        img = self.cache.get(key)
        if img is None:
            dpr = self.devicePixelRatioF()
            size = (self.rects[page].size() * dpr).toSize()
            img = render_page(self.doc, page, size)
            if self.night:
                img.invertPixels()
            img.setDevicePixelRatio(dpr)
            if len(self.cache) > 24:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = img
        return img

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        visible = QRectF(e.rect())
        for i, r in enumerate(self.rects):
            if not r.intersects(visible):
                continue
            p.fillRect(r.adjusted(2, 3, 2, 3), QColor(0, 0, 0, 90))
            p.drawImage(r, self._image(i))
            for hl in self.highlights.get(i, []):
                color = QColor(hl["color"])
                color.setAlpha(95)
                bars = self.hl_rects(hl)
                for r in bars:
                    p.fillRect(self.to_widget(i, r), color)
                if hl["note"] and bars:
                    dot = self.to_widget(i, bars[0])
                    p.setBrush(QColor(hl["color"]))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(QPointF(dot.left() - 7, dot.top() + 6), 4, 4)
            for hit in self.search_hits.get(i, []):
                current = self.current_hit is not None and self.current_hit[0] == i and self.current_hit[1] == hit
                color = QColor("#F97316" if current else "#FDE047")
                color.setAlpha(150 if current else 90)
                p.fillRect(self.to_widget(i, hit), color)
            if i == self.sel_page:
                sel = QColor(Theme.accent2())
                sel.setAlpha(80)
                p.setBrush(sel)
                p.setPen(Qt.PenStyle.NoPen)
                for r in self.sel_rects:
                    p.drawRect(self.to_widget(i, r))

    # -------------------------------------------------------------- mouse
    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        page = self.page_at(pos)
        if page < 0:
            return
        for hl in self.highlights.get(page, []):  # click on a highlight
            for r in self.hl_rects(hl):
                if self.to_widget(page, r).adjusted(-2, -2, 2, 2).contains(pos):
                    self.highlight_clicked.emit(hl, self.to_widget(page, r))
                    return
        self._press = (page, self.to_points(page, pos))
        self.clear_selection()

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        page, start = self._press
        self.select_between(page, start, self.to_points(page, e.position()))

    def mouseReleaseEvent(self, e):
        if self._press is None:
            return
        page = self._press[0]
        self._press = None
        text = getattr(self, "_sel_text", "").strip()
        if text and self.sel_rects:
            rects = [q.boundingRect() for q in self.sel_polys]   # saved raw; drawn as line bars
            anchor = self.to_widget(page, self.sel_rects[-1])
            self.selection_made.emit(page, text, rects, anchor)

    def mouseDoubleClickEvent(self, e):
        page = self.page_at(e.position())
        if page < 0:
            return
        pt = self.to_points(page, e.position())
        sel = self.doc.getSelection(page, QPointF(pt.x() - 2, pt.y()), QPointF(pt.x() + 2, pt.y()))
        if sel.isValid() and sel.text().strip():
            self._set_selection(page, sel)
            if self.sel_rects:
                self.selection_made.emit(page, sel.text().strip(), [q.boundingRect() for q in self.sel_polys],
                                         self.to_widget(page, self.sel_rects[-1]))

    # ---------------------------------------------------------- selection
    def _char_boxes(self, page: int) -> list[QRectF]:
        if page not in self._boxes:
            self._boxes[page] = [b.boundingRect() for b in self.doc.getAllText(page).bounds()]
        return self._boxes[page]

    def _snap(self, page: int, pt: QPointF) -> QPointF:
        """PDFium only selects when start / end land on a character: snap to the nearest one
        (vertical distance counts more, so the drag stays on the line you meant)."""
        boxes = self._char_boxes(page)
        if not boxes:
            return pt

        def distance(r: QRectF) -> float:
            dx = max(r.left() - pt.x(), 0, pt.x() - r.right())
            dy = max(r.top() - pt.y(), 0, pt.y() - r.bottom())
            return dx + 4 * dy
        return min(boxes, key=distance).center()

    def select_between(self, page: int, start: QPointF, end: QPointF):
        sel = self.doc.getSelection(page, self._snap(page, start), self._snap(page, end))
        self._set_selection(page, sel)
        return sel

    def _set_selection(self, page: int, sel) -> None:
        self.sel_page = page
        self.sel_polys = list(sel.bounds()) if sel.isValid() else []
        self.sel_rects = line_rects([q.boundingRect() for q in self.sel_polys], self._char_boxes(page))
        self._sel_text = sel.text() if sel.isValid() else ""
        self.update()

    def clear_selection(self) -> None:
        self.sel_page, self.sel_polys, self.sel_rects, self._sel_text = -1, [], [], ""
        self.update()

    def hl_rects(self, hl) -> list[QRectF]:
        """A saved highlight as even line bars (older highlights were saved glyph by glyph)."""
        rects = self._hl_rects.get(hl["id"])
        if rects is None:
            rects = line_rects([QRectF(x, y, w, h) for x, y, w, h in json.loads(hl["rects"])],
                               self._char_boxes(hl["page"]))
            self._hl_rects[hl["id"]] = rects
        return rects

    def selected_text(self) -> str:
        return getattr(self, "_sel_text", "").strip()


class SelectionBar(QFrame):
    """Small floating bar after selecting text: copy, highlight colors, highlight + note, to note."""

    def __init__(self, reader):
        super().__init__(reader.view_host)
        self.reader = reader
        self.setObjectName("SelBar")
        self.setStyleSheet(f"QFrame#SelBar {{ background: {Palette.surface_solid}; border: 1px solid {ink_css(0.12)}; "
                           "border-radius: 12px; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(4)
        copy = IconButton(Icons.COPY, tr("copy") + " (Ctrl+C)", 30, 9.5)
        copy.clicked.connect(reader.copy_selection)
        lay.addWidget(copy)
        trans = IconButton(Icons.GLOBE, tr("translate") + " (T)", 30, 9.5)
        trans.clicked.connect(reader.translate_selection)
        lay.addWidget(trans)
        for c in HL_COLORS:
            b = IconButton("●", tr("pdf_highlight") + " (H)", 30, 12)
            b.setStyleSheet(f"QPushButton {{ color: {c}; }}")
            b.clicked.connect(lambda _=False, col=c: reader.highlight_selection(col))
            lay.addWidget(b)
        note = IconButton(Icons.EDIT, tr("pdf_highlight_note"), 30, 9.5)
        note.clicked.connect(lambda: reader.highlight_selection(HL_COLORS[0], ask_note=True))
        lay.addWidget(note)
        to_note = IconButton(Icons.NOTES, tr("pdf_to_note"), 30, 9.5)
        to_note.clicked.connect(reader.selection_to_note)
        lay.addWidget(to_note)
        self.hide()


class PdfReader(QMainWindow):
    closed = pyqtSignal(object)

    def __init__(self, ctl, path: str | None = None, page: int | None = None):
        super().__init__()
        self.ctl = ctl
        self.path = ""
        self.key = ""
        self.doc = QPdfDocument(self)
        self.search_model = QPdfSearchModel(self)
        self.search_model.setDocument(self.doc)
        self.bookmarks = QPdfBookmarkModel(self)
        self.bookmarks.setDocument(self.doc)
        self.hits: list[tuple[int, QRectF]] = []
        self.hit_index = -1
        self.fit = self.ctl.db.get_setting("pdf_fit", "width")   # width | page | none
        self.spread = bool(self.ctl.db.get_setting("pdf_spread", False))  # two pages side by side
        self.reading = False                                                # reading mode: no toolbars
        self._sel: tuple[int, str, list] | None = None
        from app.ui import apps as app_registry
        self._info = app_registry.find(ctl, "pdf")
        self.setWindowIcon(self._info.icon())  # its own icon (red tile), its own taskbar button
        self._identity_set = False
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(760, 520)
        self.setStyleSheet(f"QMainWindow {{ background: {Palette.bg}; }}")
        self._build()
        winapi.dark_title_bar(self)
        self.resize(1280, 860)
        if path:
            self.open_file(path, page)
        else:
            self._show_start()

    # ================================================================ UI
    def _build(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.side_btn = IconButton(Icons.MENU, tr("pdf_sidebar"), 34, 10.5)
        self.side_btn.setCheckable(True)
        self.side_btn.setChecked(True)
        self.side_btn.toggled.connect(lambda on: self.sidebar.setVisible(on))
        bar.addWidget(self.side_btn)
        open_btn = IconButton(Icons.OPEN_FOLDER, tr("pdf_open") + " (Ctrl+O)", 34, 10.5)
        open_btn.clicked.connect(self.choose_file)
        bar.addWidget(open_btn)
        recent = IconButton(Icons.CLOCK, tr("pdf_recent"), 34, 10.5)
        recent.clicked.connect(lambda: self._recent_menu(recent))
        bar.addWidget(recent)
        bar.addSpacing(8)
        prev = IconButton(Icons.UP, tr("pdf_prev_page"), 34, 10)
        prev.clicked.connect(lambda: self.go_page(self.current_page() - 1))
        bar.addWidget(prev)
        self.page_box = QSpinBox()
        self.page_box.setMinimum(1)
        self.page_box.setFixedWidth(70)
        self.page_box.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.page_box.editingFinished.connect(lambda: self.go_page(self.page_box.value() - 1))
        bar.addWidget(self.page_box)
        self.page_count = label("", 9.5, role="Dim")
        bar.addWidget(self.page_count)
        nxt = IconButton(Icons.CHEVRON, tr("pdf_next_page"), 34, 10)
        nxt.clicked.connect(lambda: self.go_page(self.current_page() + 1))
        bar.addWidget(nxt)
        bar.addSpacing(8)
        out = IconButton("−", tr("pdf_zoom_out") + " (Ctrl+-)", 34, 13)
        out.clicked.connect(lambda: self.set_zoom(self.view.zoom / 1.15))
        bar.addWidget(out)
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(False)
        for text, data in ((tr("pdf_fit_width"), "width"), (tr("pdf_fit_page"), "page"), ("50%", 0.5), ("75%", 0.75),
                           ("100%", 1.0), ("125%", 1.25), ("150%", 1.5), ("200%", 2.0), ("300%", 3.0)):
            self.zoom_box.addItem(ltr(text), data)
        self.zoom_box.activated.connect(self._zoom_chosen)
        self.zoom_box.setFixedWidth(130)
        bar.addWidget(self.zoom_box)
        inn = IconButton("+", tr("pdf_zoom_in") + " (Ctrl+=)", 34, 13)
        inn.clicked.connect(lambda: self.set_zoom(self.view.zoom * 1.15))
        bar.addWidget(inn)
        bar.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("pdf_search") + " (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        self.search.returnPressed.connect(lambda: self.next_hit(1))
        self.search.textChanged.connect(self._search_changed)
        bar.addWidget(self.search)
        self.hit_label = label("", 9, role="Faint")
        self.hit_label.setMinimumWidth(50)
        bar.addWidget(self.hit_label)
        bar.addSpacing(6)
        self.night_btn = IconButton("☾", tr("pdf_night"), 34, 12)
        self.night_btn.setCheckable(True)
        self.night_btn.setChecked(bool(self.ctl.db.get_setting("pdf_night", False)))
        self.night_btn.toggled.connect(self._night)
        bar.addWidget(self.night_btn)
        notes = IconButton(Icons.NOTES, tr("pdf_highlights_to_note"), 34, 10.5)
        notes.clicked.connect(self.highlights_to_note)
        bar.addWidget(notes)
        bar.addSpacing(6)
        # sharing: Windows' "Open with", send to the paired phone, quick share with a QR code
        self.share_buttons = []
        for glyph, tip, slot in ((Icons.APPS, tr("pdf_open_with_btn"), self.open_with),
                                 (Icons.PHONE, tr("pdf_share_phone"), self.share_to_phone),
                                 (QR_GLYPH, tr("pdf_quick_share"), self.quick_share)):
            b = IconButton(glyph, tip, 34, 10.5)
            b.clicked.connect(slot)
            bar.addWidget(b)
            self.share_buttons.append(b)
        bar.addSpacing(6)
        lib = IconButton(Icons.SEARCH, tr("pdfl_title") + " (Ctrl+Shift+F)", 34, 10.5)
        lib.clicked.connect(lambda: self.ctl.open_pdf_library(query=self.search.text().strip()))
        bar.addWidget(lib)
        view_btn = IconButton(Icons.EYE, tr("pdf_view_menu"), 34, 10.5)
        view_btn.clicked.connect(lambda: self._view_menu(view_btn))
        bar.addWidget(view_btn)
        full = IconButton(Icons.FULLSCREEN, tr("pdf_fullscreen") + " (F11)", 34, 10.5)
        full.clicked.connect(self.toggle_fullscreen)
        bar.addWidget(full)
        self._wide_only = [recent, lib, full, self.night_btn]  # hidden when two readers share a window
        more = IconButton(Icons.MENU, tr("more"), 34, 10.5)
        more.clicked.connect(lambda: self._more_menu(more))
        bar.addWidget(more)
        self.toolbar_w = QWidget()  # hidden in reading mode
        self.toolbar_w.setLayout(bar)
        bar.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.toolbar_w)

        self.stack = QStackedWidget()
        # start screen (no file)
        self.start = QWidget()
        sl = QVBoxLayout(self.start)
        sl.addStretch(1)
        sl.addWidget(EmptyState(Icons.DOCUMENT, tr("pdf_start")))
        ob = primary_button(tr("pdf_open"), Icons.OPEN_FOLDER)
        ob.clicked.connect(self.choose_file)
        lb = button(tr("pdf_library"), Icons.SEARCH, role="Ghost")
        lb.clicked.connect(lambda: self.ctl.open_pdf_library())
        srow = QHBoxLayout()
        srow.addStretch(1)
        srow.addWidget(ob)
        srow.addWidget(lb)
        srow.addStretch(1)
        sl.addLayout(srow)
        self.start_recent = QListWidget()
        self.start_recent.setMaximumWidth(620)
        self.start_recent.itemClicked.connect(lambda it: self.open_file(it.data(Qt.ItemDataRole.UserRole)))
        sl.addWidget(self.start_recent, 0, Qt.AlignmentFlag.AlignHCenter)
        sl.addStretch(2)
        self.stack.addWidget(self.start)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.sidebar = QWidget()
        sb = QVBoxLayout(self.sidebar)
        sb.setContentsMargins(0, 0, 6, 0)
        self.side_tabs = SegmentedTabs([("thumbs", Icons.VIEW_GRID, ""), ("toc", Icons.VIEW_LIST, ""),
                                        ("marks", Icons.COLOR, ""), ("bookmarks", Icons.FLAG, "")], "thumbs")
        self.side_tabs.changed.connect(self._side_tab)
        sb.addWidget(self.side_tabs, 0, Qt.AlignmentFlag.AlignHCenter)
        self.side_stack = QStackedWidget()
        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumbs.setIconSize(QSize(120, 160))
        self.thumbs.setFlow(QListWidget.Flow.TopToBottom)
        self.thumbs.setWrapping(False)
        self.thumbs.setSpacing(6)
        self.thumbs.setMovement(QListWidget.Movement.Static)
        self.thumbs.itemClicked.connect(lambda it: self.go_page(it.data(Qt.ItemDataRole.UserRole)))
        self.thumbs.verticalScrollBar().valueChanged.connect(lambda _: self._thumb_timer.start())
        self.side_stack.addWidget(self.thumbs)
        self.toc = QTreeView()
        self.toc.setHeaderHidden(True)
        self.toc.setModel(self.bookmarks)
        self.toc.clicked.connect(self._toc_clicked)
        self.side_stack.addWidget(self.toc)
        self.marks = QListWidget()
        self.marks.itemClicked.connect(self._mark_clicked)
        self.marks.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.marks.customContextMenuRequested.connect(self._mark_menu)
        self.side_stack.addWidget(self.marks)
        bm = QWidget()
        bl = QVBoxLayout(bm)
        bl.setContentsMargins(0, 0, 0, 0)
        add_bm = button(tr("pdf_add_bookmark") + "  (Ctrl+D)", Icons.ADD, role="Ghost")
        add_bm.clicked.connect(self.add_bookmark)
        bl.addWidget(add_bm)
        self.bm_list = QListWidget()
        self.bm_list.itemClicked.connect(lambda it: self.go_page(it.data(Qt.ItemDataRole.UserRole)["page"]))
        self.bm_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bm_list.customContextMenuRequested.connect(self._bookmark_menu)
        bl.addWidget(self.bm_list, 1)
        self.side_stack.addWidget(bm)
        sb.addWidget(self.side_stack, 1)
        self.sidebar.setMinimumWidth(220)
        self.sidebar.setMaximumWidth(320)
        split.addWidget(self.sidebar)

        self.view_host = QWidget()
        vh = QVBoxLayout(self.view_host)
        vh.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.scroll.setStyleSheet(f"QScrollArea {{ background: {Palette.bg}; }}")
        self.view = PageView(self)
        self.view.night = self.night_btn.isChecked()
        self.scroll.setWidget(self.view)
        self.scroll.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.scroll.verticalScrollBar().valueChanged.connect(self._scrolled)
        vh.addWidget(self.scroll)
        split.addWidget(self.view_host)
        split.setStretchFactor(1, 1)
        split.setSizes([250, 1000])
        self.stack.addWidget(split)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.selbar = SelectionBar(self)
        self.view.selection_made.connect(self._selected)
        self.view.highlight_clicked.connect(self._highlight_menu)
        self._thumb_timer = QTimer(self, singleShot=True, interval=120, timeout=self._render_thumbs)
        self._save_timer = QTimer(self, singleShot=True, interval=800, timeout=self._save_state)
        for keys, slot in (("Ctrl+O", self.choose_file), ("Ctrl+F", lambda: (self.search.setFocus(), self.search.selectAll())),
                           ("Ctrl+=", lambda: self.set_zoom(self.view.zoom * 1.15)), ("Ctrl++", lambda: self.set_zoom(self.view.zoom * 1.15)),
                           ("Ctrl+-", lambda: self.set_zoom(self.view.zoom / 1.15)), ("Ctrl+0", lambda: self._set_fit("width")),
                           ("F11", self.toggle_fullscreen), ("Ctrl+C", self.copy_selection),
                           ("H", lambda: self.highlight_selection(HL_COLORS[0])), ("Escape", self._escape),
                           ("T", self.translate_selection),
                           ("PgDown", lambda: self.go_page(self.current_page() + 1)),
                           ("PgUp", lambda: self.go_page(self.current_page() - 1)),
                           ("Home", lambda: self.go_page(0)), ("End", lambda: self.go_page(self.doc.pageCount() - 1)),
                           ("Shift+Return", lambda: self.next_hit(-1)), ("Ctrl+W", self.close),
                           ("Ctrl+D", self.add_bookmark), ("R", self.toggle_reading),
                           ("Ctrl+Shift+F", lambda: self.ctl.open_pdf_library(query=self.search.text().strip()))):
            QShortcut(QKeySequence(keys), self, activated=slot)

    # ============================================================ opening
    def choose_file(self) -> None:
        start = str(Path(self.path).parent) if self.path else str(Path.home() / "Documents")
        path, _ = QFileDialog.getOpenFileName(self, tr("pdf_open"), start, "PDF (*.pdf)")
        if path:
            self.open_file(path)

    def open_file(self, path: str, page: int | None = None) -> bool:
        path = os.path.normpath(path)
        if self.doc.load(path) != QPdfDocument.Error.None_:
            self.ctl.notify(tr("pdf_cant_open", name=Path(path).name), "error")
            return False
        self._save_state()
        self.path, self.key = path, file_key(path)
        self.setWindowTitle(f"{Path(path).stem} — {tr('pdf_app_name')}")
        self.stack.setCurrentIndex(1)
        state = (self.ctl.db.get_setting("pdf_state", {}) or {}).get(self.key, {})
        self.view.relayout()
        if state.get("zoom") and self.fit == "none":
            self.view.zoom = float(state["zoom"])
        self.page_box.setMaximum(max(1, self.doc.pageCount()))
        self.page_count.setText(ltr(f"/ {self.doc.pageCount()}"))
        self._apply_fit()
        self._load_highlights()
        self._fill_thumbs()
        self.toc.expandToDepth(0)
        target = page if page is not None else int(state.get("page", 0))
        QTimer.singleShot(0, lambda: self.go_page(target))
        self._remember_recent()
        self._fill_bookmarks()
        if self.search.text():
            self._search_changed(self.search.text())
        return True

    def _show_start(self) -> None:
        self.setWindowTitle(tr("pdf_app_name"))
        self.stack.setCurrentIndex(0)
        self.start_recent.clear()
        for r in self._recent():
            item = QListWidgetItem(f"{Path(r['path']).stem}   ·   {relative_time(r['opened'])}")
            item.setData(Qt.ItemDataRole.UserRole, r["path"])
            item.setToolTip(r["path"])
            self.start_recent.addItem(item)
        self.start_recent.setVisible(self.start_recent.count() > 0)
        self.start_recent.setFixedHeight(min(8, self.start_recent.count()) * 34 + 8)

    def _recent(self) -> list[dict]:
        return [r for r in (self.ctl.db.get_setting("pdf_recent", []) or []) if os.path.exists(r["path"])]

    def _remember_recent(self) -> None:
        recent = [r for r in self._recent() if os.path.normcase(r["path"]) != os.path.normcase(self.path)]
        recent.insert(0, {"path": self.path, "opened": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        self.ctl.db.set_setting("pdf_recent", recent[:20])
        if hasattr(self.ctl, "recents"):
            self.ctl.recents._touched("pdf")  # taskbar / Windows jump list

    def _recent_menu(self, anchor) -> None:
        menu = make_menu(self)
        for r in self._recent()[:15]:
            add_action(menu, Icons.DOCUMENT, Path(r["path"]).stem, lambda p=r["path"]: self.open_file(p))
        if menu.isEmpty():
            add_action(menu, None, tr("pdf_no_recent"), None).setEnabled(False)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    # ================================================================ sharing
    def _need_file(self) -> bool:
        if not self.path or not os.path.isfile(self.path):
            self.ctl.notify(tr("pdf_no_file"), "info")
            return False
        return True

    def open_with(self) -> None:
        """Windows' own "Open with" list (Edge, Acrobat, …)."""
        if self._need_file() and not winapi.open_with_dialog(self.path, self):
            os.startfile(self.path, "openas")

    def share_to_phone(self) -> None:
        """Into the paired phone's inbox (Phone › Send)."""
        if not self._need_file():
            return
        if not self.ctl.db.list_phone_devices():
            self.ctl.notify(tr("pdf_no_phone"), "warning")
            self.ctl.open_phone_page()
            return
        self.ctl.send_paths_to_phone([self.path])

    def quick_share(self) -> None:
        """A QR code anyone on this Wi-Fi can scan to download the file, for a few minutes."""
        if not self._need_file():
            return
        from app.ui.dialogs.quick_share import QuickShareDialog
        QuickShareDialog(self.ctl, self.path, self).exec()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._identity_set and self.isWindow():
            self._identity_set = True
            from app.ui.app_window import give_identity
            give_identity(self, self._info)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (e.Type.ActivationChange, e.Type.WindowStateChange) and hasattr(self.ctl, "apps"):
            self.ctl.apps.changed.emit()

    # ================================================================ view: two pages, reading, compare
    def _view_menu(self, anchor) -> None:
        menu = make_menu(self)
        a = add_action(menu, Icons.BOOK, tr("pdf_two_pages"), self.toggle_spread)
        a.setCheckable(True)
        a.setChecked(self.spread)
        add_action(menu, Icons.PREVIEW, tr("pdf_reading_mode") + "  (R)", self.toggle_reading)
        add_action(menu, Icons.COPY, tr("pdf_compare"), self.compare_with)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def compact(self) -> None:
        """A narrower toolbar (compare window: two readers side by side)."""
        for w in self._wide_only:
            w.hide()
        self.search.setFixedWidth(130)
        self.zoom_box.setFixedWidth(100)

    def toggle_spread(self) -> None:
        page = self.current_page()
        self.spread = not self.spread
        self.ctl.db.set_setting("pdf_spread", self.spread)
        self._apply_fit()
        self.view.relayout()
        QTimer.singleShot(0, lambda: self.go_page(page))

    def toggle_reading(self) -> None:
        """Only the pages: no toolbar, no side panel (R or Esc to leave)."""
        self.reading = not self.reading
        self.toolbar_w.setVisible(not self.reading)
        self.sidebar.setVisible(not self.reading and self.side_btn.isChecked())
        if self.reading:
            self.ctl.notify(tr("pdf_reading_hint"), "info") if not self.isWindow() else None
            if not hasattr(self, "_read_pill"):
                self._read_pill = button(tr("pdf_reading_exit"), Icons.CLOSE, role="Ghost", parent=self)
                self._read_pill.clicked.connect(self.toggle_reading)
            self._read_pill.adjustSize()
            self._read_pill.move(self.width() - self._read_pill.width() - 18, 10)
            self._read_pill.show()
            self._read_pill.raise_()
        elif hasattr(self, "_read_pill"):
            self._read_pill.hide()
        QTimer.singleShot(0, self._apply_fit)

    def compare_with(self) -> None:
        if not self._need_file():
            return
        from PyQt6.QtWidgets import QFileDialog
        other, _ = QFileDialog.getOpenFileName(self, tr("pdf_compare"), str(Path(self.path).parent), "PDF (*.pdf)")
        if other:
            win = PdfCompare(self.ctl, self.path, other, self.current_page())
            win.show()
            self.ctl.apps.changed.emit()

    # ================================================================ bookmarks
    def _bookmarks(self) -> list[dict]:
        return list((self.ctl.db.get_setting("pdf_bookmarks", {}) or {}).get(self.key, []))

    def _save_bookmarks(self, items: list[dict]) -> None:
        allb = dict(self.ctl.db.get_setting("pdf_bookmarks", {}) or {})
        if items:
            allb[self.key] = sorted(items, key=lambda b: b["page"])
        else:
            allb.pop(self.key, None)
        self.ctl.db.set_setting("pdf_bookmarks", allb)
        self._fill_bookmarks()

    def add_bookmark(self) -> None:
        if not self._need_file():
            return
        from app.ui.dialogs.base import PromptDialog
        page = self.current_page()
        name = PromptDialog.ask(tr("pdf_add_bookmark"), tr("pdfl_page", n=page + 1), tr("pdf_bookmark_ph"), self)
        if name:
            items = [b for b in self._bookmarks() if b["page"] != page]
            items.append({"page": page, "name": name.strip(), "path": self.path})
            self._save_bookmarks(items)
            self._side_tab("bookmarks")
            self.side_btn.setChecked(True)
            self.ctl.notify(tr("pdf_bookmarked"), "success")

    def _fill_bookmarks(self) -> None:
        self.bm_list.clear()
        for b in self._bookmarks():
            item = QListWidgetItem(f"{b['name']}\n{tr('pdfl_page', n=b['page'] + 1)}")
            item.setData(Qt.ItemDataRole.UserRole, b)
            self.bm_list.addItem(item)

    def _bookmark_menu(self, pos) -> None:
        item = self.bm_list.itemAt(pos)
        if item is None:
            return
        b = item.data(Qt.ItemDataRole.UserRole)
        menu = make_menu(self)

        def rename():
            from app.ui.dialogs.base import PromptDialog
            name = PromptDialog.ask(tr("rename"), b["name"], "", self)
            if name:
                self._save_bookmarks([dict(x, name=name.strip()) if x["page"] == b["page"] else x for x in self._bookmarks()])
        add_action(menu, Icons.EDIT, tr("rename"), rename)
        add_action(menu, Icons.DELETE, tr("delete"),
                   lambda: self._save_bookmarks([x for x in self._bookmarks() if x["page"] != b["page"]]), danger=True)
        menu.exec(self.bm_list.mapToGlobal(pos))

    def _more_menu(self, anchor) -> None:
        menu = make_menu(self)
        if self.path:
            add_action(menu, Icons.OPEN_FOLDER, tr("open_location"), lambda: self.ctl.reveal_path(self.path))
            add_action(menu, Icons.COPY, tr("copy_path"), lambda: self.ctl.copy_text(self.path))
            add_action(menu, Icons.PIN, tr("ctx_pin_file"), lambda: self.ctl.add_path(self.path))
            add_action(menu, Icons.PHONE, tr("pdf_share_phone"), self.share_to_phone)
            add_action(menu, QR_GLYPH, tr("pdf_quick_share"), self.quick_share)
            add_action(menu, Icons.APPS, tr("pdf_open_with_btn"), self.open_with)
            add_action(menu, Icons.FORWARD, tr("pdf_open_default"), lambda: os.startfile(self.path))
            menu.addSeparator()
        add_action(menu, Icons.SETTINGS, tr("pdf_make_default"), lambda: os.startfile("ms-settings:defaultapps"))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    # ============================================================ navigation
    def current_page(self) -> int:
        y = self.scroll.verticalScrollBar().value() + self.scroll.viewport().height() / 3
        for i, r in enumerate(self.view.rects):
            if r.bottom() + GAP >= y:
                return i
        return max(0, len(self.view.rects) - 1)

    def go_page(self, page: int, y_points: float | None = None) -> None:
        if not self.view.rects:
            return
        page = max(0, min(page, len(self.view.rects) - 1))
        top = self.view.rects[page].top() - GAP / 2
        if y_points is not None:
            top += y_points * DPI * self.view.zoom - self.scroll.viewport().height() / 3
        self.scroll.verticalScrollBar().setValue(int(top))
        self._scrolled()

    def _scrolled(self, *_):
        page = self.current_page()
        self.page_box.blockSignals(True)
        self.page_box.setValue(page + 1)
        self.page_box.blockSignals(False)
        if self.thumbs.count() > page:
            self.thumbs.blockSignals(True)
            self.thumbs.setCurrentRow(page)
            self.thumbs.blockSignals(False)
        self.selbar.hide()
        self._save_timer.start()

    def _save_state(self) -> None:
        if not self.key:
            return
        state = self.ctl.db.get_setting("pdf_state", {}) or {}
        state[self.key] = {"page": self.current_page(), "zoom": round(self.view.zoom, 3)}
        if len(state) > 300:
            state = dict(list(state.items())[-300:])
        self.ctl.db.set_setting("pdf_state", state)
        if hasattr(self.ctl, "set_context"):  # the phone can continue from here
            self.ctl.set_context("pdf", path=self.path, name=Path(self.path).name, page=self.current_page(),
                                 pages=self.doc.pageCount())

    # ================================================================ zoom
    def _zoom_chosen(self, index: int) -> None:
        data = self.zoom_box.itemData(index)
        if isinstance(data, str):
            self._set_fit(data)
        else:
            self.set_zoom(float(data))

    def _set_fit(self, fit: str) -> None:
        self.fit = fit
        self.ctl.db.set_setting("pdf_fit", fit)
        self._apply_fit()

    def _apply_fit(self) -> None:
        if not self.doc.pageCount():
            return
        if self.fit in ("width", "page"):
            size = self.doc.pagePointSize(self.current_page()) * DPI
            avail = self.scroll.viewport().size()
            pages_across = 2 if self.spread and self.doc.pageCount() > 1 else 1
            zoom = (avail.width() - (1 + pages_across) * GAP - 4) / (size.width() * pages_across)
            if self.fit == "page":
                zoom = min(zoom, (avail.height() - 2 * GAP) / size.height())
            self._zoom_to(max(0.2, zoom))
            self.zoom_box.setCurrentIndex(0 if self.fit == "width" else 1)

    def set_zoom(self, zoom: float) -> None:
        self.fit = "none"
        self.ctl.db.set_setting("pdf_fit", "none")
        self._zoom_to(max(0.25, min(5.0, zoom)))
        self.zoom_box.setCurrentText(ltr(f"{round(self.view.zoom * 100)}%"))
        if self.zoom_box.currentText() != ltr(f"{round(self.view.zoom * 100)}%"):
            self.zoom_box.setEditText(ltr(f"{round(self.view.zoom * 100)}%"))

    def _zoom_to(self, zoom: float) -> None:
        page = self.current_page()
        self.view.zoom = zoom
        self.view.relayout()
        self.go_page(page)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit in ("width", "page"):
            QTimer.singleShot(0, self._apply_fit)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_zoom(self.view.zoom * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1))
            e.accept()
        else:
            super().wheelEvent(e)

    def _night(self, on: bool) -> None:
        self.view.night = on
        self.view.cache.clear()
        self.view.update()
        self.ctl.db.set_setting("pdf_night", on)

    def toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _escape(self) -> None:
        if self.reading:
            self.toggle_reading()
        elif self.selbar.isVisible() or self.view.sel_polys:
            self.selbar.hide()
            self.view.clear_selection()
        elif self.isFullScreen():
            self.showNormal()

    # ============================================================ side panel
    def _side_tab(self, key: str) -> None:
        self.side_tabs.set_current(key)
        self.side_stack.setCurrentIndex({"thumbs": 0, "toc": 1, "marks": 2, "bookmarks": 3}[key])

    def _fill_thumbs(self) -> None:
        self.thumbs.clear()
        blank = QPixmap(120, 160)
        blank.fill(ink(25 / 255))
        for i in range(self.doc.pageCount()):
            item = QListWidgetItem(QIcon(blank), ltr(str(i + 1)))
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setData(Qt.ItemDataRole.UserRole + 1, False)
            self.thumbs.addItem(item)
        self._thumb_timer.start()

    def _render_thumbs(self) -> None:
        """Only the thumbnails that are on screen are drawn."""
        vp = self.thumbs.viewport().rect()
        for i in range(self.thumbs.count()):
            item = self.thumbs.item(i)
            if item.data(Qt.ItemDataRole.UserRole + 1) or not self.thumbs.visualItemRect(item).intersects(vp):
                continue
            size = self.doc.pagePointSize(i)
            scale = 150 / max(1, size.height())
            img = render_page(self.doc, i, QSize(int(size.width() * scale), int(size.height() * scale)))
            if self.view.night:
                img.invertPixels()
            item.setIcon(QIcon(QPixmap.fromImage(img)))
            item.setData(Qt.ItemDataRole.UserRole + 1, True)

    def _toc_clicked(self, index) -> None:
        page = self.bookmarks.data(index, QPdfBookmarkModel.Role.Page.value)
        location = self.bookmarks.data(index, QPdfBookmarkModel.Role.Location.value)
        if page is not None and page >= 0:
            self.go_page(int(page), location.y() if location is not None else None)

    # ============================================================== search
    def _search_changed(self, text: str) -> None:
        self.search_model.setSearchString(text.strip())
        QTimer.singleShot(250, self._collect_hits)

    def _collect_hits(self) -> None:
        self.hits = []
        hits_by_page: dict[int, list[QRectF]] = {}
        for i in range(self.search_model.rowCount(QModelIndex())):
            link = self.search_model.resultAtIndex(i)
            for rect in link.rectangles():
                self.hits.append((link.page(), rect))
                hits_by_page.setdefault(link.page(), []).append(rect)
        self.view.search_hits = hits_by_page if self.search.text().strip() else {}
        self.hit_index = -1
        self.view.current_hit = None
        n = len(self.hits)
        self.hit_label.setText(ltr(f"{n}") if self.search.text().strip() else "")
        if self.search.text().strip() and not n:
            self.hit_label.setText(tr("no_matches"))
        self.view.update()

    def next_hit(self, step: int) -> None:
        if not self.hits:
            self._collect_hits()
            if not self.hits:
                return
        self.hit_index = (self.hit_index + step) % len(self.hits)
        page, rect = self.hits[self.hit_index]
        self.view.current_hit = (page, rect)
        self.go_page(page, rect.y())
        self.hit_label.setText(ltr(f"{self.hit_index + 1}/{len(self.hits)}"))
        self.view.update()

    # =========================================================== selection
    def _selected(self, page: int, text: str, rects: list, anchor: QRectF) -> None:
        self._sel = (page, text, rects)
        pos = self.view.mapTo(self.view_host, anchor.bottomRight().toPoint())
        self.selbar.adjustSize()
        x = max(4, min(pos.x() - self.selbar.width() // 2, self.view_host.width() - self.selbar.width() - 4))
        y = min(pos.y() + 8, self.view_host.height() - self.selbar.height() - 4)
        self.selbar.move(x, y)
        self.selbar.show()
        self.selbar.raise_()

    def copy_selection(self) -> None:
        text = self.view.selected_text()
        if text:
            QApplication.clipboard().setText(text)
            self.ctl.notify(tr("copied"), "success")
            self.selbar.hide()

    def translate_selection(self) -> None:
        """Translation card under the selection; "to note" keeps it as a highlight whose note is the translation."""
        text = self.view.selected_text()
        if not text:
            return
        from app.ui.translate_popup import TranslatePopup
        if getattr(self, "_translator", None) is None:
            self._translator = TranslatePopup(self.ctl, self, note_callback=self._keep_translation)
        anchor = self.selbar.mapToGlobal(self.selbar.rect().bottomLeft()) if self.selbar.isVisible() else None
        self.selbar.hide()
        self._translator.show_for(text, anchor)

    def _keep_translation(self, source: str, translation: str) -> None:
        if self._sel:
            page, text, rects = self._sel
            self.ctl.db.add_pdf_highlight(self.key, self.path, page,
                                          json.dumps([[r.x(), r.y(), r.width(), r.height()] for r in rects]),
                                          text, HL_COLORS[2], translation)
            self.view.clear_selection()
            self._load_highlights()
            self.ctl.notify(tr("translate_kept"), "success")

    def highlight_selection(self, color: str, ask_note: bool = False) -> None:
        if not self._sel or not self.view.sel_polys:
            return
        page, text, rects = self._sel
        note = ""
        if ask_note:
            from app.ui.dialogs.base import PromptDialog
            note = PromptDialog.ask(tr("pdf_highlight_note"), "", tr("pdf_note_ph"), self) or ""
        self.ctl.db.add_pdf_highlight(self.key, self.path, page,
                                      json.dumps([[r.x(), r.y(), r.width(), r.height()] for r in rects]),
                                      text, color, note)
        self.selbar.hide()
        self.view.clear_selection()
        self._load_highlights()

    def selection_to_note(self) -> None:
        if not self._sel:
            return
        page, text, _ = self._sel
        self._append_to_note([{"page": page, "text": text, "note": ""}])
        self.selbar.hide()

    # ========================================================== highlights
    def _load_highlights(self) -> None:
        rows = self.ctl.db.pdf_highlights(self.key, self.path)
        self.view.highlights = {}
        self.view._hl_rects = {}
        for hl in rows:
            self.view.highlights.setdefault(hl["page"], []).append(hl)
        self.view.update()
        self.marks.clear()
        for hl in rows:
            text = hl["text"].replace("\n", " ")[:120]
            item = QListWidgetItem(f"{tr('pdf_page_short', n=hl['page'] + 1)} · {text}"
                                   + (f"\n✎ {hl['note']}" if hl["note"] else ""))
            item.setData(Qt.ItemDataRole.UserRole, hl)
            item.setForeground(QColor(hl["color"]))
            self.marks.addItem(item)
        if not rows:
            self.marks.addItem(QListWidgetItem(tr("pdf_no_highlights")))
        self.side_tabs.set_badges({"marks": len(rows)})

    def _mark_clicked(self, item) -> None:
        hl = item.data(Qt.ItemDataRole.UserRole)
        if hl:
            bars = self.view.hl_rects(hl)
            self.go_page(hl["page"], bars[0].top() if bars else None)

    def _mark_menu(self, pos) -> None:
        item = self.marks.itemAt(pos)
        hl = item.data(Qt.ItemDataRole.UserRole) if item else None
        if hl:
            self._highlight_menu(hl, None, self.marks.viewport().mapToGlobal(pos))

    def _highlight_menu(self, hl: dict, rect, global_pos=None) -> None:
        menu = make_menu(self)
        add_action(menu, Icons.EDIT, tr("pdf_edit_note"), lambda: self._edit_note(hl))
        add_action(menu, Icons.COPY, tr("copy"), lambda: self.ctl.copy_text(hl["text"]))
        add_action(menu, Icons.NOTES, tr("pdf_to_note"), lambda: self._append_to_note([hl]))
        colors = menu.addMenu(tr("color"))
        for c in HL_COLORS:
            act = colors.addAction("●  " + c)
            act.triggered.connect(lambda _=False, col=c: (self.ctl.db.update_pdf_highlight(hl["id"], color=col),
                                                          self._load_highlights()))
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("delete"), lambda: (self.ctl.db.delete_pdf_highlight(hl["id"]),
                                                              self._load_highlights()), danger=True)
        from PyQt6.QtGui import QCursor
        menu.exec(global_pos or QCursor.pos())

    def _edit_note(self, hl: dict) -> None:
        from app.ui.dialogs.base import PromptDialog
        note = PromptDialog.ask(tr("pdf_edit_note"), hl["note"], tr("pdf_note_ph"), self)
        if note is not None:
            self.ctl.db.update_pdf_highlight(hl["id"], note=note)
            self._load_highlights()

    def highlights_to_note(self) -> None:
        rows = self.ctl.db.pdf_highlights(self.key, self.path) if self.key else []
        if not rows:
            self.ctl.notify(tr("pdf_no_highlights"), "info")
            return
        self._append_to_note(rows, replace=True)

    def _append_to_note(self, items: list[dict], replace: bool = False) -> None:
        """One note per PDF (folder "PDF"); every quote links back to its page."""
        db = self.ctl.db
        title = f"📄 {Path(self.path).stem}"
        note = db.note_by_title(title)
        lines = []
        for it in items:
            quote_text = "\n".join("> " + line for line in it["text"].strip().splitlines())
            lines.append(f"{quote_text}\n> — [{tr('pdf_page_short', n=it['page'] + 1)}]({pdf_link(self.path, it['page'])})")
            if it.get("note"):
                lines.append(it["note"])
            lines.append("")
        body = "\n".join(lines)
        header = f"[{Path(self.path).name}]({pdf_link(self.path, 0)})\n\n"
        if note and not replace:
            content = (self.ctl.note_content(note) or "").rstrip() + "\n\n" + body
            self.ctl.autosave_note(note["id"], note["title"], content)
            note_id = note["id"]
        elif note:
            self.ctl.autosave_note(note["id"], note["title"], header + body)
            note_id = note["id"]
        else:
            note_id = self.ctl.create_note(title=title, content=header + body,
                                           folder_id=db.folder_by_path(["PDF"]))
        self.ctl.notify(tr("pdf_noted"), "success")
        self.ctl.data_changed.emit("notes")
        return note_id

    def closeEvent(self, e):
        self._save_state()
        self.closed.emit(self)
        super().closeEvent(e)


class PdfCompare(QMainWindow):
    """Two PDFs side by side in one window (each with its own toolbar); optionally scrolling together."""

    def __init__(self, ctl, left: str, right: str, page: int = 0):
        super().__init__()
        from app.ui import apps as app_registry
        self.ctl = ctl
        self.info = app_registry.find(ctl, "pdf")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{Path(left).stem}  ↔  {Path(right).stem} — {tr('pdf_compare')}")
        self.setWindowIcon(self.info.icon())
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(6, 6, 6, 6)
        top = QHBoxLayout()
        from PyQt6.QtWidgets import QCheckBox
        self.sync = QCheckBox(tr("pdf_sync_scroll"))
        self.sync.setChecked(True)
        top.addWidget(self.sync)
        top.addStretch(1)
        lay.addLayout(top)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.readers = []
        for path, pg in ((left, page), (right, 0)):
            r = PdfReader(ctl, path, pg)
            r.setWindowFlags(Qt.WindowType.Widget)
            r.side_btn.setChecked(False)
            r.compact()
            split.addWidget(r)
            self.readers.append(r)
        lay.addWidget(split, 1)
        self.setCentralWidget(central)
        self.setStyleSheet(f"QMainWindow {{ background: {Palette.bg}; }}")
        a, b = self.readers
        self._syncing = False
        a.scroll.verticalScrollBar().valueChanged.connect(lambda _: self._follow(a, b))
        b.scroll.verticalScrollBar().valueChanged.connect(lambda _: self._follow(b, a))
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen:
            self.setGeometry(screen.adjusted(30, 30, -30, -30))
        winapi.dark_title_bar(self)

    def _follow(self, src, dst) -> None:
        if self._syncing or not self.sync.isChecked():
            return
        self._syncing = True
        try:
            if src.current_page() != dst.current_page():
                dst.go_page(src.current_page())
        finally:
            self._syncing = False

    def showEvent(self, e):
        super().showEvent(e)
        from app.ui.app_window import give_identity
        give_identity(self, self.info)

    def closeEvent(self, e):
        for r in self.readers:
            r._save_state()
        super().closeEvent(e)


class PdfReaders:
    """Keeps reader windows alive; one window per file (opening it again brings it to the front)."""

    def __init__(self, ctl):
        self.ctl = ctl
        self.windows: list[PdfReader] = []

    def open(self, path: str | None = None, page: int | None = None) -> PdfReader:
        if path:
            norm = os.path.normcase(os.path.normpath(path))
            for w in self.windows:
                if w.path and os.path.normcase(w.path) == norm:
                    if page is not None:
                        w.go_page(page)
                    return self._front(w)
            empty = next((w for w in self.windows if not w.path), None)
            if empty:
                empty.open_file(path, page)
                return self._front(empty)
        w = PdfReader(self.ctl, path, page)
        w.closed.connect(self._closed)
        self.windows.append(w)
        if hasattr(self.ctl, "apps"):
            self.ctl.apps.changed.emit()
        return self._front(w)

    def _closed(self, win) -> None:
        if win in self.windows:
            self.windows.remove(win)
        if hasattr(self.ctl, "apps"):
            self.ctl.apps.changed.emit()

    @staticmethod
    def _front(w: PdfReader) -> PdfReader:
        if w.isMinimized():
            w.showNormal()
        w.show()
        w.raise_()
        w.activateWindow()
        wid = w.winId()
        if wid:  # can be empty for a moment while the window is being created
            winapi.force_foreground(int(wid))
        return w
