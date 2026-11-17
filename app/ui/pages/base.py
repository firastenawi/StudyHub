"""Base class for every page shown in the main area (modular: one page = one module)."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QScrollArea, QVBoxLayout, QWidget


class Page(QWidget):
    def __init__(self, ctl, main_window):
        super().__init__()
        self.ctl = ctl
        self.main = main_window
        self._connections: list[tuple] = []
        # controls shown in the window's top bar while this page is open (one row instead of two)
        self.toolbar: QWidget | None = None

    def listen(self, signal, slot) -> None:
        """Connect to a controller signal; disconnected automatically in dispose()."""
        signal.connect(slot)
        self._connections.append((signal, slot))

    def dispose(self) -> None:
        """Called before the page is destroyed (e.g. when the language changes)."""
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._connections.clear()

    def on_shown(self) -> None:
        """Called after the page became visible (entrance animations go here)."""

    @staticmethod
    def scrollable(content: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)
        area.setWidget(content)
        return area

    def set_scroll_content(self, content: QWidget) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.scrollable(content))
