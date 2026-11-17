"""Focus timer as its own app: the Pomodoro dial, today's minutes and the week, in a window of its own.
(The timer itself lives in the controller — closing the window never stops a running session.)"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QMainWindow, QVBoxLayout

from app.core.i18n import tr
from app.ui import apps as app_registry
from app.ui.app_window import give_identity, place_window, remember_window
from app.ui.widgets.background import AuroraBackground
from app.ui.widgets.common import IconButton
from app.ui.theme import Icons


class TimerWindow(QMainWindow):
    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.info = app_registry.find(ctl, "timer")
        self._connections: list[tuple] = []
        self.view = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(760, 560)
        self.setWindowTitle(f"{self.info.title} — Study Hub")
        self.setWindowIcon(self.info.icon())
        self.build()
        place_window(self, "timer", ctl, (0.56, 0.7))
        self._identity_set = False
        QShortcut(QKeySequence("Space"), self, activated=ctl.pomodoro.toggle)
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self.close)
        self.on_top = bool(ctl.db.get_setting("timer_on_top", False))
        if self.on_top:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

    # FocusView talks to its "page" through these
    def listen(self, signal, slot) -> None:
        signal.connect(slot)
        self._connections.append((signal, slot))

    def _disconnect(self) -> None:
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._connections.clear()

    def build(self) -> None:
        from app.ui.pages.study.focus_view import FocusView
        self._disconnect()
        bg = AuroraBackground(animated=False, wallpaper=self.ctl.wallpaper())
        root = QVBoxLayout(bg)
        root.setContentsMargins(22, 14, 22, 20)
        root.setSpacing(10)
        pin = IconButton(Icons.PIN, tr("timer_on_top"), 34, 10)
        pin.setCheckable(True)
        pin.setChecked(bool(self.ctl.db.get_setting("timer_on_top", False)))
        pin.toggled.connect(self._set_on_top)
        root.addWidget(pin, 0, Qt.AlignmentFlag.AlignRight if not self.isRightToLeft() else Qt.AlignmentFlag.AlignLeft)
        self.view = FocusView(self.ctl, self)
        root.addWidget(self.view, 1)
        self.setCentralWidget(bg)
        self.listen(self.ctl.data_changed, lambda what: self.view.refresh(what))

    def _set_on_top(self, on: bool) -> None:
        self.ctl.db.set_setting("timer_on_top", on)
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if visible:
            self.show()  # changing a window flag hides the window (and makes a new native one)
            give_identity(self, self.info)

    def showEvent(self, e):
        super().showEvent(e)
        if not self._identity_set:
            self._identity_set = True
            give_identity(self, self.info)
        if self.view is not None:
            self.view.on_shown()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (e.Type.ActivationChange, e.Type.WindowStateChange) and hasattr(self.ctl, "apps"):
            self.ctl.apps.changed.emit()

    def closeEvent(self, e):
        remember_window(self, "timer", self.ctl)
        self._disconnect()
        super().closeEvent(e)
