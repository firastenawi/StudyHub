"""Smart file manager page: subject folders, tagged files, Downloads organizer."""
from __future__ import annotations

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout

from app.core.i18n import tr
from app.ui.animations import AnimatedStack
from app.ui.pages.base import Page
from app.ui.pages.files.archive_view import ArchiveView
from app.ui.pages.files.library_view import LibraryView
from app.ui.pages.files.organizer_view import OrganizerView
from app.ui.pages.files.tags_view import TagsView
from app.ui.theme import Icons
from app.ui.widgets.common import label
from app.ui.widgets.segmented import SegmentedTabs

TABS = [("library", Icons.FOLDER, "tab_library"), ("tags", Icons.TAG, "tags"),
        ("organizer", Icons.DOWNLOAD, "tab_organizer"), ("archive", Icons.ARCHIVE, "tab_archive")]


class FilesPage(Page):
    def __init__(self, ctl, main_window):
        super().__init__(ctl, main_window)
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(tr("nav_files"), 22, QFont.Weight.DemiBold))
        titles.addWidget(label(tr("files_subtitle"), 10, role="Dim"))
        header.addLayout(titles)
        header.addStretch(1)
        self.tabs = SegmentedTabs([(k, g, tr(t)) for k, g, t in TABS], "library")
        self.tabs.changed.connect(self.show_tab)
        header.addWidget(self.tabs)
        root.addLayout(header)

        self.stack = AnimatedStack()
        self.views = {"library": LibraryView(ctl, self), "tags": TagsView(ctl, self),
                      "organizer": OrganizerView(ctl, self), "archive": ArchiveView(ctl, self)}
        for view in self.views.values():
            self.stack.addWidget(view)
        root.addWidget(self.stack, 1)
        self.current = "library"
        self.listen(ctl.data_changed, self.refresh)
        self.listen(ctl.downloads_changed, self._downloads_changed)
        ctl.organizer_suggestions()  # initial badge (a quick scan of Downloads)

    def show_tab(self, key: str) -> None:
        if key in self.views:
            self.current = key
            self.tabs.set_current(key)
            self.stack.switch_to(self.stack.indexOf(self.views[key]), animate=self.isVisible())

    def set_organizer_badge(self, count: int) -> None:
        self.tabs.set_badges({"organizer": count})

    def _downloads_changed(self, count: int) -> None:
        self.set_organizer_badge(count)  # never rescans here: the scan is what emitted this

    def refresh(self, what: str = "") -> None:
        for view in self.views.values():
            view.refresh(what)

    def on_shown(self) -> None:
        view = self.views[self.current]
        if hasattr(view, "on_shown"):
            view.on_shown()
