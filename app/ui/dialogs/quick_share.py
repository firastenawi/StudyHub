"""Quick share with a QR code: any phone / laptop on the same Wi-Fi scans it and downloads the file — no app,
no pairing. The link is random, works only on the local network, and stops after the chosen time (or Stop)."""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit

from app.core.i18n import tr
from app.ui.dialogs.base import StyledDialog
from app.ui.theme import Icons
from app.ui.widgets.common import IconButton, button, label, primary_button


class QuickShareDialog(StyledDialog):
    def __init__(self, ctl, path: str, parent=None):
        super().__init__(tr("qs_title"), parent, 460)
        from app.ui.pages.phone_page import QrView
        self.ctl, self.path = ctl, path
        self.token = ""
        self.body.addWidget(label("📄  " + Path(path).name, 10.5, QFont.Weight.DemiBold, wrap=True))
        self.body.addWidget(label(tr("qs_hint"), 9, role="Dim", wrap=True))
        self.qr = QrView(250)
        self.body.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignHCenter)
        row = QHBoxLayout()
        self.link = QLineEdit()
        self.link.setReadOnly(True)
        self.link.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        row.addWidget(self.link, 1)
        copy = IconButton(Icons.COPY, tr("copy"), 36, 10)
        copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(self.link.text()),
                                      self.ctl.notify(tr("copied"), "success")))
        row.addWidget(copy)
        self.body.addLayout(row)
        info = QHBoxLayout()
        self.state = label("", 9.5, role="Dim")
        info.addWidget(self.state, 1)
        info.addWidget(label(tr("qs_minutes"), 9.5, role="Dim"))
        self.minutes = QComboBox()
        for m in (5, 10, 30, 60):
            self.minutes.addItem(tr("qs_n_min", n=m), m)
        self.minutes.setCurrentIndex(1)
        self.minutes.currentIndexChanged.connect(lambda _: self._start())
        info.addWidget(self.minutes)
        self.body.addLayout(info)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        stop = button(tr("qs_stop"), Icons.CLOSE, role="Ghost")
        stop.clicked.connect(self._stop_and_close)
        buttons.addWidget(stop)
        done = primary_button(tr("qs_keep"), Icons.ACCEPT)
        done.clicked.connect(self.accept)
        buttons.addWidget(done)
        self.body.addLayout(buttons)
        self.downloads = 0
        ctl.phone.share_downloaded.connect(self._downloaded)
        self._tick_timer = QTimer(self, interval=1000, timeout=self._tick)
        self._start()

    def _start(self) -> None:
        if self.token:
            self.ctl.phone.stop_share(self.token)
        result = self.ctl.quick_share(self.path, int(self.minutes.currentData()))
        if not result:
            self.state.setText("⚠  " + tr("qs_no_network"))
            self.qr.set_text("")
            return
        self.token, url = result
        self.expires = time.time() + int(self.minutes.currentData()) * 60
        self.link.setText(url)
        self.link.setCursorPosition(0)
        self.qr.set_text(url)
        self._tick_timer.start()
        self._tick()

    def _tick(self) -> None:
        left = int(self.expires - time.time())
        if left <= 0:
            self._tick_timer.stop()
            self.state.setText(tr("qs_expired"))
            self.qr.set_text("")
            return
        m, s = divmod(left, 60)
        text = tr("qs_left", t=f"{m}:{s:02d}")
        if self.downloads:
            text += "  ·  " + tr("qs_downloads", n=self.downloads)
        self.state.setText(text)

    def _downloaded(self, token: str, _ip: str) -> None:
        if token == self.token:
            self.downloads += 1
            self._tick()

    def _stop_and_close(self) -> None:
        if self.token:
            self.ctl.phone.stop_share(self.token)
            self.ctl.notify(tr("qs_stopped"), "info")
        self.reject()

    def done(self, code) -> None:
        try:
            self.ctl.phone.share_downloaded.disconnect(self._downloaded)
        except (TypeError, RuntimeError):
            pass
        super().done(code)
