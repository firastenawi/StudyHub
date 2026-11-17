"""
Full-screen alarm: swinging bell, pulsing rings, big time, Snooze / Dismiss.

Optional wake-up challenge before Dismiss works:
  math : solve a small sum           type : type the phrase shown
Esc never dismisses (no accidental "off"); Space snoozes. After 10 minutes of ringing
the alarm snoozes by itself (or stops when no snoozes are left).
"""
from __future__ import annotations

import math
import random
from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app.core import winapi
from app.core.i18n import format_hm, tr
from app.ui.theme import Icons, Palette, Theme, icon_font, qcolor, ui_font
from app.ui.widgets.common import label

AUTO_TIMEOUT_MIN = 10


class AlarmRingWindow(QWidget):
    snoozed = pyqtSignal(dict, int)   # alarm, snooze count so far
    dismissed = pyqtSignal(dict)

    def __init__(self, alarm: dict, snooze_count: int):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.alarm, self.count = alarm, snooze_count
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._t = 0.0
        self._answer = None
        self._done = False

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 40, 40, 70)
        root.addStretch(5)
        now = datetime.now()
        self.time_label = label(format_hm(now.hour, now.minute), 66, QFont.Weight.DemiBold)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.time_label)
        title = alarm["label"] or tr("alarm")
        name = label(title, 22, QFont.Weight.Medium)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(name)
        if snooze_count:
            info = label(tr("snoozed_times", n=snooze_count, max=alarm["max_snoozes"]), 11, role="Dim")
            info.setAlignment(Qt.AlignmentFlag.AlignCenter)
            root.addWidget(info)
        root.addSpacing(26)

        # wake-up challenge
        self.answer = None
        challenge = alarm.get("challenge", "none")
        if challenge in ("math", "type"):
            if challenge == "math":
                a, b = random.randint(12, 59), random.randint(12, 49)
                question, self._answer = f"{a} + {b} = ?", str(a + b)
            else:
                self._answer = tr("wake_phrase")
                question = tr("type_phrase", phrase=self._answer)
            q = label(question, 15, QFont.Weight.DemiBold)
            q.setAlignment(Qt.AlignmentFlag.AlignCenter)
            root.addWidget(q)
            self.answer = QLineEdit()
            self.answer.setFixedWidth(320)
            self.answer.setFont(ui_font(14))
            self.answer.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.answer.textChanged.connect(self._check_answer)
            self.answer.returnPressed.connect(self._dismiss)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(self.answer)
            row.addStretch(1)
            root.addLayout(row)
            root.addSpacing(16)

        buttons = QHBoxLayout()
        buttons.setSpacing(22)
        buttons.addStretch(1)
        can_snooze = snooze_count < int(alarm["max_snoozes"])
        self.snooze_btn = self._pill(tr("snooze_n", n=alarm["snooze_min"]), primary=False)
        self.snooze_btn.setVisible(can_snooze)
        self.snooze_btn.clicked.connect(self._snooze)
        buttons.addWidget(self.snooze_btn)
        self.dismiss_btn = self._pill(tr("dismiss"), primary=True)
        self.dismiss_btn.clicked.connect(self._dismiss)
        self.dismiss_btn.setEnabled(self._answer is None)
        buttons.addWidget(self.dismiss_btn)
        buttons.addStretch(1)
        root.addLayout(buttons)
        root.addStretch(2)

        self._anim = QTimer(self, interval=33, timeout=self._tick)
        self._anim.start()
        self._timeout = QTimer(self, singleShot=True, interval=AUTO_TIMEOUT_MIN * 60_000, timeout=self._timed_out)
        self._timeout.start()

    @staticmethod
    def _pill(text: str, primary: bool) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("Primary" if primary else "")
        btn.setFont(ui_font(14, QFont.Weight.DemiBold))
        btn.setFixedSize(220, 64)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("border-radius: 32px;")
        return btn

    # ------------------------------------------------------------- show
    def ring(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        winapi.force_foreground(int(self.winId()))
        if self.answer is not None:
            self.answer.setFocus()

    def _tick(self):
        self._t += 0.033
        now = datetime.now()
        self.time_label.setText(format_hm(now.hour, now.minute))
        self.update()

    def _check_answer(self, text: str):
        ok = text.strip().replace(" ", "") == (self._answer or "").replace(" ", "")
        self.dismiss_btn.setEnabled(ok)

    # ----------------------------------------------------------- actions
    def _finish(self):
        self._done = True
        self._anim.stop()
        self._timeout.stop()
        self.close()

    def _snooze(self):
        if not self._done and self.snooze_btn.isVisible():
            self._finish()
            self.snoozed.emit(self.alarm, self.count)

    def _dismiss(self):
        if not self._done and self.dismiss_btn.isEnabled():
            self._finish()
            self.dismissed.emit(self.alarm)

    def _timed_out(self):
        if self.snooze_btn.isVisible():
            self._snooze()
        else:
            self._finish()
            self.dismissed.emit(self.alarm)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space and (self.answer is None or not self.answer.hasFocus()):
            self._snooze()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._dismiss()
        # Esc is deliberately ignored

    def closeEvent(self, e):
        if not self._done:  # Alt+F4 counts as snooze (or dismiss when none left)
            self._timed_out()
        super().closeEvent(e)

    # ----------------------------------------------------------- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(Palette.bg))
        c = QPointF(self.width() / 2, self.height() * 0.27)
        pulse = 0.5 + 0.5 * math.sin(self._t * 3)
        glow = QRadialGradient(c, self.height() * 0.55)
        glow.setColorAt(0, qcolor(Theme.accent(), 0.30 + 0.12 * pulse))
        glow.setColorAt(0.5, qcolor(Theme.accent2(), 0.06))
        glow.setColorAt(1, qcolor(Theme.accent(), 0.0))
        p.fillRect(self.rect(), glow)
        # expanding ripples
        for k in range(3):
            phase = (self._t * 0.6 + k / 3) % 1.0
            radius = 70 + phase * 260
            p.setPen(QPen(qcolor(Theme.accent2(), 0.45 * (1 - phase)), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, radius, radius)
        # swinging bell
        p.save()
        p.translate(c)
        p.rotate(14 * math.sin(self._t * 9) * (0.6 + 0.4 * pulse))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(Palette.surface_solid, 0.9))
        p.drawEllipse(QPointF(0, 0), 64, 64)
        font = icon_font()
        font.setPixelSize(58)
        p.setFont(font)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRectF(-64, -64, 128, 128), Qt.AlignmentFlag.AlignCenter, Icons.BELL)
        p.restore()
