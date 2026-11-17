"""
Screenshot tool (Ctrl+Alt+P): select part of the screen, annotate it, then copy / save / put it in
a note / pin it on screen / send it to the phone — or pull the text out of it (OCR, Arabic + English).

  SelectOverlay  frozen picture of all monitors; drag a rectangle (Enter or double-click = whole
                 monitor under the mouse, Esc = cancel)
  ShotEditor     pen, highlighter, arrow, rectangle, text, blur; undo / redo; colors and sizes
  PinnedShot     the picture floating on top of everything (drag to move, wheel to zoom, Esc closes)
"""
from __future__ import annotations

import math
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QSize, QSizeF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QColor, QCursor, QFont, QGuiApplication, QImage, QKeySequence, QPainter, QPainterPath,
                         QPen, QPixmap, QPolygonF, QShortcut)
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QInputDialog, QLabel, QPlainTextEdit,
                             QPushButton, QScrollArea, QSlider, QSplitter, QVBoxLayout, QWidget)

from app.config import DATA_DIR
from app.core.i18n import tr
from app.ui.theme import Icons, Palette, Theme, ui_font
from app.ui.widgets.common import IconButton, button, label, primary_button

COLORS = ["#F87171", "#FBBF24", "#34D399", "#22D3EE", "#A78BFA", "#FFFFFF", "#111111"]


def grab_desktop() -> tuple[QPixmap, QRect]:
    """All monitors in one picture (logical coordinates of the virtual desktop)."""
    screens = QGuiApplication.screens()
    virtual = QRect()
    for s in screens:
        virtual = virtual.united(s.geometry())
    dpr = max(s.devicePixelRatio() for s in screens)
    canvas = QPixmap(virtual.size() * dpr)
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.GlobalColor.black)
    p = QPainter(canvas)
    for s in screens:
        shot = s.grabWindow(0)
        target = QRectF(s.geometry().translated(-virtual.topLeft()))
        p.drawPixmap(target, shot, QRectF(shot.rect()))
    p.end()
    return canvas, virtual


class SelectOverlay(QWidget):
    selected = pyqtSignal(QPixmap)

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.shot, self.virtual = grab_desktop()
        self.setGeometry(self.virtual)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.origin: QPoint | None = None
        self.rect_sel = QRect()

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.shot)
        p.fillRect(self.rect(), QColor(5, 8, 15, 120))
        if not self.rect_sel.isNull():
            r = self.rect_sel.normalized()
            src = QRectF(r.x() * self.shot.devicePixelRatio(), r.y() * self.shot.devicePixelRatio(),
                         r.width() * self.shot.devicePixelRatio(), r.height() * self.shot.devicePixelRatio())
            p.drawPixmap(QRectF(r), self.shot, src)
            p.setPen(QPen(QColor(Theme.accent2()), 2))
            p.drawRect(r)
            text = f"{r.width()} × {r.height()}"
            p.setFont(ui_font(9, QFont.Weight.DemiBold))
            box = QRect(r.left(), max(0, r.top() - 26), p.fontMetrics().horizontalAdvance(text) + 16, 22)
            p.fillRect(box, QColor(10, 13, 20, 220))
            p.setPen(QColor("#FFFFFF"))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        else:
            hint = tr("shot_hint")
            p.setFont(ui_font(11, QFont.Weight.DemiBold))
            cursor = self.mapFromGlobal(QCursor.pos())
            box = QRect(cursor.x() + 18, cursor.y() + 18, p.fontMetrics().horizontalAdvance(hint) + 24, 30)
            p.fillRect(box, QColor(10, 13, 20, 220))
            p.setPen(QColor("#FFFFFF"))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, hint)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            return self.close()
        self.origin = e.position().toPoint()
        self.rect_sel = QRect(self.origin, QSize())

    def mouseMoveEvent(self, e):
        if self.origin is not None:
            self.rect_sel = QRect(self.origin, e.position().toPoint())
        self.update()

    def mouseReleaseEvent(self, e):
        if self.origin is None:
            return
        r = self.rect_sel.normalized()
        self.origin = None
        if r.width() > 4 and r.height() > 4:
            self._finish(r)

    def mouseDoubleClickEvent(self, e):
        self._finish(self._screen_rect())

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish(self.rect_sel.normalized() if not self.rect_sel.isNull() else self._screen_rect())

    def _screen_rect(self) -> QRect:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        return screen.geometry().translated(-self.virtual.topLeft())

    def _finish(self, r: QRect) -> None:
        dpr = self.shot.devicePixelRatio()
        crop = self.shot.copy(QRect(int(r.x() * dpr), int(r.y() * dpr), int(r.width() * dpr), int(r.height() * dpr)))
        crop.setDevicePixelRatio(dpr)
        self.close()
        self.selected.emit(crop)


# ------------------------------------------------------------------ annotations
class Shape:
    def __init__(self, tool: str, color: str, size: int):
        self.tool, self.color, self.size = tool, QColor(color), size
        self.points: list[QPointF] = []
        self.text = ""

    def paint(self, p: QPainter, base: QImage | None = None) -> None:
        if self.tool == "blur":
            if base is None or len(self.points) < 2:
                return
            r = QRectF(self.points[0], self.points[-1]).normalized().toRect().intersected(base.rect())
            if r.width() < 2 or r.height() < 2:
                return
            block = max(6, self.size * 2)
            small = base.copy(r).scaled(max(1, r.width() // block), max(1, r.height() // block))
            p.drawImage(r, small.scaled(r.size()))
            return
        pen = QPen(self.color, self.size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        if self.tool == "highlight":
            c = QColor(self.color)
            c.setAlpha(90)
            pen = QPen(c, self.size * 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.tool in ("pen", "highlight") and len(self.points) > 1:
            path = QPainterPath(self.points[0])
            for pt in self.points[1:]:
                path.lineTo(pt)
            p.drawPath(path)
        elif self.tool == "rect" and len(self.points) > 1:
            p.drawRoundedRect(QRectF(self.points[0], self.points[-1]).normalized(), 4, 4)
        elif self.tool == "arrow" and len(self.points) > 1:
            a, b = self.points[0], self.points[-1]
            p.drawLine(a, b)
            angle = math.atan2(b.y() - a.y(), b.x() - a.x())
            head = 10 + self.size * 2.5
            left = QPointF(b.x() - head * math.cos(angle - 0.45), b.y() - head * math.sin(angle - 0.45))
            right = QPointF(b.x() - head * math.cos(angle + 0.45), b.y() - head * math.sin(angle + 0.45))
            p.setBrush(self.color)
            p.drawPolygon(QPolygonF([b, left, right]))
        elif self.tool == "text" and self.points and self.text:
            f = QFont("Segoe UI", 10 + self.size * 3, QFont.Weight.DemiBold)
            p.setFont(f)
            metrics = p.fontMetrics()
            box = QRectF(self.points[0], QSizeF(metrics.horizontalAdvance(self.text) + 16, metrics.height() + 8))
            bg = QColor("#000000") if self.color.lightness() > 128 else QColor("#FFFFFF")
            bg.setAlpha(150)
            p.fillRect(box, bg)
            p.setPen(self.color)
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, self.text)


class Canvas(QWidget):
    changed = pyqtSignal()

    def __init__(self, image: QImage, parent=None):
        super().__init__(parent)
        self.base = image
        self.shapes: list[Shape] = []
        self.redo_stack: list[Shape] = []
        self.tool, self.color, self.size = "arrow", COLORS[0], 3
        self.current: Shape | None = None
        self.scale = 1.0
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.fit(QSize(1100, 700))

    def fit(self, area: QSize) -> None:
        w, h = self.base.width(), self.base.height()
        self.scale = min(1.0, area.width() / max(1, w), area.height() / max(1, h))
        self.setFixedSize(int(w * self.scale), int(h * self.scale))

    def _to_image(self, pos) -> QPointF:
        return QPointF(pos.x() / self.scale, pos.y() / self.scale)

    def render_image(self) -> QImage:
        img = self.base.copy()
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for s in self.shapes:
            s.paint(p, self.base)
        p.end()
        return img

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.scale(self.scale, self.scale)
        p.drawImage(0, 0, self.base)
        for s in self.shapes + ([self.current] if self.current else []):
            s.paint(p, self.base)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pt = self._to_image(e.position())
        if self.tool == "text":
            text, ok = QInputDialog.getText(self, tr("shot_text"), tr("shot_text_prompt"))
            if ok and text.strip():
                s = Shape("text", self.color, self.size)
                s.points, s.text = [pt], text.strip()
                self._add(s)
            return
        self.current = Shape(self.tool, self.color, self.size)
        self.current.points = [pt]

    def mouseMoveEvent(self, e):
        if self.current:
            pt = self._to_image(e.position())
            if self.current.tool in ("pen", "highlight"):
                self.current.points.append(pt)
            else:
                self.current.points = [self.current.points[0], pt]
            self.update()

    def mouseReleaseEvent(self, e):
        if self.current and len(self.current.points) > 1:
            self._add(self.current)
        self.current = None
        self.update()

    def _add(self, s: Shape) -> None:
        self.shapes.append(s)
        self.redo_stack.clear()
        self.update()
        self.changed.emit()

    def undo(self) -> None:
        if self.shapes:
            self.redo_stack.append(self.shapes.pop())
            self.update()

    def redo(self) -> None:
        if self.redo_stack:
            self.shapes.append(self.redo_stack.pop())
            self.update()


class _OcrRunner(QObject):
    done = pyqtSignal(object)

    def run(self, image: QImage) -> None:
        # small screenshots are enlarged first: Windows OCR reads small text much better at 2x
        if image.height() < 900:
            image = image.scaled(image.width() * 2, image.height() * 2, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        path = Path(tempfile.gettempdir()) / f"studyhub-ocr-{datetime.now():%H%M%S%f}.png"
        image.save(str(path), "PNG")

        def work():
            from app.core.ocr import recognize
            try:
                result = recognize(str(path))
            except Exception as exc:
                result = exc
            finally:
                path.unlink(missing_ok=True)
            self.done.emit(result)
        threading.Thread(target=work, daemon=True).start()


class ShotEditor(QWidget):
    """Annotate the capture, then copy / save / note / pin / phone / OCR."""

    def __init__(self, ctl, pixmap: QPixmap):
        super().__init__(None, Qt.WindowType.Window)
        self.ctl = ctl
        self.setWindowTitle(tr("shot_title"))
        self.setWindowIcon(ctl.icon)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(f"QWidget#ShotRoot {{ background: {Palette.bg}; }}")
        self.setObjectName("ShotRoot")
        image = pixmap.toImage()
        image.setDevicePixelRatio(1.0)
        self.canvas = Canvas(image)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        tools = QHBoxLayout()
        tools.setSpacing(4)
        self.tool_group = QButtonGroup(self)
        for key, glyph, tip in (("arrow", Icons.FORWARD, tr("shot_arrow")), ("rect", Icons.SELECT, tr("shot_rect")),
                                ("pen", Icons.EDIT, tr("shot_pen")), ("highlight", Icons.COLOR, tr("shot_highlight")),
                                ("text", "A", tr("shot_text")), ("blur", Icons.EYE_OFF, tr("shot_blur"))):
            b = IconButton(glyph, tip, 36, 11)
            if len(glyph) == 1 and glyph == "A":
                b.setFont(ui_font(12, QFont.Weight.Bold))
            b.setCheckable(True)
            b.setChecked(key == "arrow")
            b.clicked.connect(lambda _=False, k=key: setattr(self.canvas, "tool", k))
            self.tool_group.addButton(b)
            tools.addWidget(b)
        tools.addSpacing(10)
        self.color_group = QButtonGroup(self)
        for c in COLORS:
            b = QPushButton()
            b.setCheckable(True)
            b.setFixedSize(24, 24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"QPushButton {{ background: {c}; border-radius: 12px; border: 2px solid rgba(255,255,255,0.15); }}"
                            f"QPushButton:checked {{ border: 3px solid #FFFFFF; }}")
            b.setChecked(c == COLORS[0])
            b.clicked.connect(lambda _=False, col=c: setattr(self.canvas, "color", col))
            self.color_group.addButton(b)
            tools.addWidget(b)
        tools.addSpacing(10)
        size = QSlider(Qt.Orientation.Horizontal)
        size.setRange(1, 8)
        size.setValue(3)
        size.setFixedWidth(100)
        size.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        size.valueChanged.connect(lambda v: setattr(self.canvas, "size", v))
        tools.addWidget(size)
        undo = IconButton(Icons.UNDO, tr("shot_undo") + " (Ctrl+Z)", 34, 10)
        undo.clicked.connect(self.canvas.undo)
        tools.addWidget(undo)
        tools.addStretch(1)
        root.addLayout(tools)

        split = QSplitter(Qt.Orientation.Horizontal)
        scroll = self.scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        split.addWidget(scroll)
        self.ocr_box = QWidget()
        ol = QVBoxLayout(self.ocr_box)
        ol.setContentsMargins(8, 0, 0, 0)
        ol.addWidget(label(tr("shot_ocr_title"), 10, QFont.Weight.DemiBold))
        self.ocr_text = QPlainTextEdit()
        self.ocr_text.setPlaceholderText(tr("shot_ocr_wait"))
        ol.addWidget(self.ocr_text, 1)
        orow = QHBoxLayout()
        copy_text = button(tr("copy"), Icons.COPY, role="Ghost")
        copy_text.clicked.connect(lambda: (QApplication.clipboard().setText(self.ocr_text.toPlainText()),
                                           self.ctl.notify(tr("copied"), "success")))
        orow.addWidget(copy_text)
        translate = button(tr("translate"), Icons.GLOBE, role="Ghost")
        translate.clicked.connect(self._translate)
        orow.addWidget(translate)
        note_text = button(tr("shot_text_to_note"), Icons.NOTES, role="Ghost")
        note_text.clicked.connect(self._text_to_note)
        orow.addWidget(note_text)
        ol.addLayout(orow)
        self.ocr_box.hide()
        split.addWidget(self.ocr_box)
        split.setSizes([900, 340])
        root.addWidget(split, 1)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        for text, glyph, slot in ((tr("shot_ocr"), Icons.SEARCH, self.run_ocr),
                                  (tr("shot_pin"), Icons.PIN, self.pin),
                                  (tr("shot_to_phone"), Icons.PHONE, self.to_phone),
                                  (tr("shot_save"), Icons.SAVE, self.save)):
            b = button(text, glyph, role="Ghost")
            b.clicked.connect(slot)
            actions.addWidget(b)
        actions.addStretch(1)
        to_note = button(tr("shot_to_note"), Icons.NOTES, role="Ghost")
        to_note.clicked.connect(self.to_note)
        actions.addWidget(to_note)
        copy = primary_button(tr("shot_copy") + "  (Ctrl+C)", Icons.COPY)
        copy.clicked.connect(self.copy)
        actions.addWidget(copy)
        root.addLayout(actions)

        for keys, slot in (("Ctrl+Z", self.canvas.undo), ("Ctrl+Y", self.canvas.redo), ("Ctrl+C", self.copy),
                           ("Ctrl+S", self.save), ("Escape", self.close)):
            QShortcut(QKeySequence(keys), self, activated=slot)
        self._ocr = _OcrRunner(self)
        self._ocr.done.connect(self._ocr_done)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.canvas.fit(QSize(int(area.width() * 0.7), int(area.height() * 0.62)))
        self.resize(min(area.width() - 80, max(760, self.canvas.width() + 60)),
                    min(area.height() - 80, self.canvas.height() + 170))
        self.move(area.center() - self.rect().center())
        from app.core import winapi
        winapi.dark_title_bar(self)

    # --------------------------------------------------------------- outputs
    def image(self) -> QImage:
        return self.canvas.render_image()

    def copy(self) -> None:
        QApplication.clipboard().setImage(self.image())
        self.ctl.notify(tr("shot_copied"), "success")
        self.close()

    def save(self) -> str:
        folder = Path.home() / "Pictures" / "Screenshots" / "Study Hub"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"shot-{datetime.now():%Y%m%d-%H%M%S}.png"
        self.image().save(str(path), "PNG")
        self.ctl.notify(tr("shot_saved", name=path.name), "success")
        return str(path)

    def to_note(self) -> None:
        from app.ui.widgets.note_editor import attachment_dir
        note_id = self.ctl.create_note(title=tr("shot_note_title", when=datetime.now().strftime("%Y-%m-%d %H:%M")),
                                       is_inbox=True)
        folder = attachment_dir(note_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"shot-{datetime.now():%Y%m%d-%H%M%S}.png"
        self.image().save(str(path), "PNG")
        text = self.ocr_text.toPlainText().strip()
        content = f"![]({path.relative_to(DATA_DIR).as_posix()})\n" + (f"\n{text}\n" if text else "")
        self.ctl.autosave_note(note_id, tr("shot_note_title", when=datetime.now().strftime("%Y-%m-%d %H:%M")), content)
        self.ctl.notify(tr("shot_noted"), "success")
        self.close()
        self.ctl.open_notebook(note_id)

    def to_phone(self) -> None:
        path = Path(tempfile.gettempdir()) / f"Screenshot-{datetime.now():%Y%m%d-%H%M%S}.png"
        self.image().save(str(path), "PNG")
        self.ctl.send_to_phone("file", str(path))

    def pin(self) -> None:
        PinnedShot(QPixmap.fromImage(self.image()), self.ctl)
        self.close()

    # ------------------------------------------------------------------ OCR
    def run_ocr(self) -> None:
        self.ocr_box.show()
        self.ocr_text.setPlainText("")
        self.ocr_text.setPlaceholderText(tr("shot_ocr_wait"))
        self._ocr.run(self.canvas.base)
        QTimer.singleShot(0, lambda: (self.canvas.fit(self.scroll.viewport().size() - QSize(8, 8)), self.canvas.update()))

    def _ocr_done(self, result) -> None:
        try:
            if isinstance(result, Exception):
                self.ocr_text.setPlaceholderText(tr("shot_ocr_failed", error=str(result)))
            elif not result:
                self.ocr_text.setPlaceholderText(tr("shot_ocr_none"))
            else:
                self.ocr_text.setPlainText(result)
        except RuntimeError:  # window closed meanwhile
            pass

    def _translate(self) -> None:
        text = self.ocr_text.textCursor().selectedText().replace("\u2029", "\n") or self.ocr_text.toPlainText()
        if not text.strip():
            return
        from app.ui.translate_popup import TranslatePopup
        if getattr(self, "_translator", None) is None:
            self._translator = TranslatePopup(
                self.ctl, self, note_callback=lambda src, out: (self.ctl.save_note(f"{src}\n\n---\n\n{out}", source="ocr"),
                                                                self.ctl.notify(tr("shot_text_noted"), "success")))
        self._translator.show_for(text)

    def _text_to_note(self) -> None:
        text = self.ocr_text.toPlainText().strip()
        if text:
            self.ctl.save_note(text, source="ocr")
            self.ctl.notify(tr("shot_text_noted"), "success")


class PinnedShot(QLabel):
    """A screenshot floating above all windows (drag to move, wheel to zoom, double-click / Esc closes)."""

    instances: list["PinnedShot"] = []

    def __init__(self, pixmap: QPixmap, ctl):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.ctl = ctl
        self.original = pixmap
        self.zoom = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(f"border: 2px solid {Theme.accent2()};")
        self._apply()
        self.move(QCursor.pos() - QPoint(self.width() // 2, self.height() // 2))
        self._drag: QPoint | None = None
        PinnedShot.instances.append(self)
        self.destroyed.connect(lambda *_: PinnedShot.instances.remove(self) if self in PinnedShot.instances else None)
        self.show()

    def _apply(self) -> None:
        size = self.original.size() * self.zoom / max(1.0, self.original.devicePixelRatio())
        self.setPixmap(self.original.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
        self.adjustSize()

    def wheelEvent(self, e):
        self.zoom = max(0.2, min(4.0, self.zoom * (1.1 if e.angleDelta().y() > 0 else 0.9)))
        self._apply()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        elif e.button() == Qt.MouseButton.RightButton:
            QApplication.clipboard().setPixmap(self.original)
            self.ctl.notify(tr("shot_copied"), "success")

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()


class ScreenshotTool(QObject):
    """Keeps windows alive and wires overlay -> editor."""

    def __init__(self, ctl):
        super().__init__(ctl)
        self.ctl = ctl
        self._overlay = None
        self._editors: list[ShotEditor] = []

    def start(self) -> None:
        if self._overlay is not None and self._overlay.isVisible():
            return
        self._overlay = SelectOverlay()
        self._overlay.selected.connect(self._edit)
        self._overlay.show()
        self._overlay.activateWindow()
        self._overlay.raise_()

    def _edit(self, pixmap: QPixmap) -> None:
        editor = ShotEditor(self.ctl, pixmap)
        self._editors = [e for e in self._editors if _alive(e)] + [editor]
        editor.show()
        editor.raise_()
        editor.activateWindow()


def _alive(w) -> bool:
    try:
        w.isVisible()
        return True
    except RuntimeError:
        return False
