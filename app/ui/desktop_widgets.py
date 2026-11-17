"""
Live windows pinned on the Desktop (like sticky notes, but interactive):

  web   - a real web page (Qt WebEngine, logins are remembered)
  pdf   - a PDF with scrolling, page counter and zoom
  todo  - today's tasks, check them off / add new ones (same tasks as the Study Planner)

The header is the drag handle (mouse presses fall through to the desktop canvas);
everything inside the content area is fully interactive.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QRectF, Qt, QUrl
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from app.config import DATA_DIR
from app.core.i18n import tr
from app.ui.theme import Icons, Palette, Theme, qcolor, ink
from app.ui.widgets.common import EmptyState, GlyphLabel, IconButton, label, primary_button

try:  # optional component (PyQt6-WebEngine)
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAS_WEB = True
except ImportError:
    HAS_WEB = False

WIDGET_TYPES = {"web": Icons.GLOBE, "pdf": Icons.DOCUMENT, "todo": Icons.CHECKLIST}
DEFAULT_SIZE = {"web": (5, 4), "pdf": (4, 5), "todo": (3, 4)}
_profile = None


def web_profile():
    """One persistent browser profile: cookies / logins survive restarts."""
    global _profile
    if _profile is None and HAS_WEB:
        _profile = QWebEngineProfile("StudyHub")
        _profile.setPersistentStoragePath(str(DATA_DIR / "web"))
        _profile.setCachePath(str(DATA_DIR / "web-cache"))
        _profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
    return _profile


class WidgetHeader(QWidget):
    """Title strip. It ignores mouse presses on purpose, so the canvas can drag the window."""

    def __init__(self, glyph: str, title: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(6)
        lay.addWidget(GlyphLabel(glyph, 10, Theme.accent()))
        self.title = label(title, 9.6, QFont.Weight.DemiBold)
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.title, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(0)
        lay.addLayout(self.buttons)

    def add_button(self, glyph: str, tip: str, slot) -> IconButton:
        b = IconButton(glyph, tip, 28, 9)
        b.clicked.connect(slot)
        self.buttons.addWidget(b)
        return b

    def set_title(self, text: str):
        self.title.setText(self.title.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight,
                                                                max(40, self.title.width())))
        self.title.setToolTip(text)


# ------------------------------------------------------------------ contents
class WebPane(QWidget):
    def __init__(self, item: dict, header: WidgetHeader, ctl, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        url = item["target"]
        if not HAS_WEB:
            box = QVBoxLayout()
            box.addStretch(1)
            box.addWidget(EmptyState(Icons.GLOBE, tr("web_unavailable")))
            open_btn = primary_button(tr("open_in_browser"), Icons.GLOBE)
            open_btn.clicked.connect(lambda: ctl.open_url(url))
            box.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignHCenter)
            box.addStretch(1)
            lay.addLayout(box)
            return
        self.view = QWebEngineView(self)
        self.view.setPage(QWebEnginePage(web_profile(), self.view))
        self.view.setZoomFactor(0.9)  # small windows: show a bit more of the page
        self.view.load(QUrl(url))
        self.view.titleChanged.connect(header.set_title)
        lay.addWidget(self.view)
        header.add_button(Icons.BACK, tr("back"), self.view.back)
        header.add_button(Icons.REFRESH, tr("reload"), self.view.reload)
        header.add_button(Icons.GLOBE, tr("open_in_browser"), lambda: ctl.open_url(self.view.url().toString()))

    def refresh(self, what: str):
        pass


class PdfPane(QWidget):
    def __init__(self, item: dict, header: WidgetHeader, ctl, parent=None):
        super().__init__(parent)
        from PyQt6.QtPdf import QPdfDocument
        from PyQt6.QtPdfWidgets import QPdfView
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        path = item["target"]
        self.doc = QPdfDocument(self)
        self.view = QPdfView(self)
        self.view.setDocument(self.doc)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.view.setStyleSheet(f"background: {Palette.surface_solid}; border: none;")
        lay.addWidget(self.view)
        if os.path.exists(path):
            self.doc.load(path)
        self.page_label = QLabel()
        self.page_label.setObjectName("Dim")
        header.buttons.addWidget(self.page_label)
        nav = self.view.pageNavigator()
        nav.currentPageChanged.connect(self._update_page)
        self._update_page(0)
        header.add_button(Icons.ADD, tr("zoom_in"), lambda: self._zoom(1.2))
        header.add_button(Icons.MINIMIZE, tr("zoom_out"), lambda: self._zoom(1 / 1.2))
        header.add_button(Icons.OPEN_FOLDER, tr("open_external"), lambda: ctl.open_path(path))

    def _update_page(self, page: int):
        self.page_label.setText(f" {page + 1}/{max(1, self.doc.pageCount())} ")

    def _zoom(self, factor: float):
        from PyQt6.QtPdfWidgets import QPdfView
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.view.setZoomFactor(max(0.3, min(4.0, self.view.zoomFactor() * factor)))

    def refresh(self, what: str):
        pass


class TodoPane(QWidget):
    def __init__(self, item: dict, header: WidgetHeader, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 10)
        lay.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText(tr("task_placeholder"))
        self.input.returnPressed.connect(self._add)
        lay.addWidget(self.input)
        host = QWidget()
        self.rows = QVBoxLayout(host)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(host)
        lay.addWidget(scroll, 1)
        header.add_button(Icons.STUDY, tr("tab_tasks"), lambda: ctl.open_study_tab("tasks"))
        self.refresh("tasks")

    def _add(self):
        text = self.input.text().strip()
        if text:
            self.ctl.add_task(text)
            self.input.clear()

    def refresh(self, what: str):
        if what not in ("", "tasks"):
            return
        from app.ui.animations import dispose
        from app.ui.pages.home_page import TaskRow
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                dispose(w)
        tasks = self.ctl.db.tasks_for_today()
        if not tasks:
            self.rows.addWidget(EmptyState(Icons.ACCEPT, tr("tasks_empty")))
        for task in tasks:
            self.rows.addWidget(TaskRow(task, self.ctl))
        self.rows.addStretch(1)


PANES = {"web": WebPane, "pdf": PdfPane, "todo": TodoPane}

# more widget types (system, focus timer, classes, note, picture, links) live in desktop_widgets2
from app.ui.desktop_widgets2 import NEW_PANES, NEW_SIZES, NEW_TYPES  # noqa: E402
PANES.update(NEW_PANES)
WIDGET_TYPES.update(NEW_TYPES)
DEFAULT_SIZE.update(NEW_SIZES)


def paint_widget_frame(p: QPainter, rect: QRectF, selected: bool, hover: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, 14, 14)
    p.fillPath(path, QColor(Palette.surface_solid))
    p.save()
    p.setClipPath(path)
    p.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 36), ink(0.05 + 0.03 * hover))
    p.restore()
    p.setPen(QPen(qcolor(Theme.accent(), 0.9) if selected else ink(0.10 + 0.08 * hover),
                  1.6 if selected else 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    return path
