"""Frameless, rounded, animated dialog base + small prompt/confirm dialogs."""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QPropertyAnimation, Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication
from PyQt6.QtWidgets import (QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLineEdit,
                             QVBoxLayout, QWidget)

from app.core.i18n import tr
from app.ui.animations import OUT
from app.ui.theme import Icons
from app.ui.widgets.common import IconButton, button, label, primary_button


def drop_shadow(widget: QWidget, blur: int = 50, dy: int = 14, alpha: int = 170) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)


class StyledDialog(QDialog):
    def __init__(self, title: str, parent: QWidget | None = None, width: int = 480):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 28)
        self.frame = QFrame()
        self.frame.setObjectName("DialogFrame")
        drop_shadow(self.frame)
        outer.addWidget(self.frame)
        self.setFixedWidth(width + 56)

        self.body = QVBoxLayout(self.frame)
        self.body.setContentsMargins(24, 20, 24, 22)
        self.body.setSpacing(14)
        header = QHBoxLayout()
        header.addWidget(label(title, 13.5, QFont.Weight.DemiBold))
        header.addStretch(1)
        close = IconButton(Icons.CLOSE, tr("close"), 32, 9.5)
        close.clicked.connect(self.reject)
        header.addWidget(close)
        self.body.addLayout(header)
        self._drag_from: QPoint | None = None
        self.ok_button = None

    def add_buttons(self, ok_text: str, cancel_text: str | None = None, danger: bool = False) -> None:
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button(cancel_text or tr("cancel"), role="Ghost")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.ok_button = button(ok_text, role="Danger") if danger else primary_button(ok_text)
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self._try_accept)
        row.addWidget(self.ok_button)
        self.body.addSpacing(4)
        self.body.addLayout(row)

    def validate(self) -> bool:
        return True

    def _try_accept(self):
        if self.validate():
            self.accept()

    # -------------------------------------------------------- entrance + drag
    def showEvent(self, e):
        super().showEvent(e)
        self.adjustSize()
        host = self.parentWidget()
        area = host.window().geometry() if host and host.isVisible() else \
            QGuiApplication.primaryScreen().availableGeometry()
        end = QPoint(area.center().x() - self.width() // 2, area.center().y() - self.height() // 2 - 40)
        self.setWindowOpacity(0.0)
        self._pos_anim = QPropertyAnimation(self, b"pos", self)
        self._pos_anim.setStartValue(end + QPoint(0, 22))
        self._pos_anim.setEndValue(end)
        self._pos_anim.setDuration(300)
        self._pos_anim.setEasingCurve(OUT)
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setDuration(220)
        self._pos_anim.start()
        self._fade_anim.start()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_from = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag_from is not None:
            self.move(e.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, _):
        self._drag_from = None


class PromptDialog(StyledDialog):
    """Asks for one line of text."""

    def __init__(self, title: str, text: str = "", placeholder: str = "", parent=None):
        super().__init__(title, parent, 420)
        self.edit = QLineEdit(text)
        self.edit.setPlaceholderText(placeholder)
        self.edit.selectAll()
        self.body.addWidget(self.edit)
        self.add_buttons(tr("save"))

    def validate(self) -> bool:
        return bool(self.edit.text().strip())

    def value(self) -> str:
        return self.edit.text().strip()

    @classmethod
    def ask(cls, title: str, text: str = "", placeholder: str = "", parent=None) -> str | None:
        dlg = cls(title, text, placeholder, parent)
        return dlg.value() if dlg.exec() == QDialog.DialogCode.Accepted else None


class ConfirmDialog(StyledDialog):
    def __init__(self, title: str, message: str, ok_text: str, parent=None, danger: bool = True):
        super().__init__(title, parent, 420)
        self.body.addWidget(label(message, 10, role="Dim", wrap=True))
        self.add_buttons(ok_text, danger=danger)

    @classmethod
    def ask(cls, title: str, message: str, ok_text: str, parent=None, danger: bool = True) -> bool:
        return cls(title, message, ok_text, parent, danger).exec() == QDialog.DialogCode.Accepted
