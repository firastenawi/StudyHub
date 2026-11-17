"""Runs the updater (app/core/updater.py) off the GUI thread and reports back with queued signals."""
from __future__ import annotations

import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.core import updater


class UpdateService(QObject):
    checked = pyqtSignal(dict)       # result of updater.check (+ "manual")
    progress = pyqtSignal(float)     # download 0..1
    failed = pyqtSignal(str)
    installing = pyqtSignal()

    def __init__(self, ctl):
        super().__init__(ctl)
        self.ctl = ctl
        self.last: dict | None = None
        self.busy = False
        QTimer.singleShot(5 * 60_000, updater.clean_downloads)  # a previous update's temporary files

    def source(self) -> str:
        return self.ctl.db.get_setting("update_source", "") or updater.default_source()

    def set_source(self, source: str) -> None:
        self.ctl.db.set_setting("update_source", source.strip())
        self.check(manual=True)

    def auto_check(self) -> None:
        """Once a day, quietly; a toast only when something newer is there."""
        if not self.ctl.db.get_setting("update_auto", True):
            return
        if time.time() - float(self.ctl.db.get_setting("update_last_check", 0) or 0) < 20 * 3600:
            return
        self.check(manual=False)

    def check(self, manual: bool = True) -> None:
        if self.busy:
            return
        self.busy = True
        source = self.source()

        def work():
            result = updater.check(source)
            result["manual"] = manual
            self.busy = False
            self.checked.emit(result)
        threading.Thread(target=work, name="update-check", daemon=True).start()

    def install(self, result: dict) -> None:
        if self.busy:
            return
        self.busy = True
        try:  # keep a copy of the data first, like before a schema upgrade
            from datetime import datetime
            from app.config import BACKUP_DIR
            self.ctl.db.backup(BACKUP_DIR / f"before-update-{datetime.now():%Y%m%d-%H%M%S}.db")
        except Exception as exc:
            self.busy = False
            self.failed.emit(str(exc))
            return

        def work():
            try:
                folder = updater.prepare(result, self.progress.emit)
                updater.launch_installer(folder)
            except Exception as exc:
                self.busy = False
                self.failed.emit(str(exc)[:200])
                return
            self.installing.emit()
        threading.Thread(target=work, name="update-install", daemon=True).start()
