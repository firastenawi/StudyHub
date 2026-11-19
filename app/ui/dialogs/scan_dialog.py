"""
Scan window: a page photographed with the phone becomes a clean, flat scan.

Left: the photo with the page's four corners (found automatically; drag them if needed).
Right: the result, live: straightened, in the chosen look (document / original / grey), rotatable.
Then: copy it, save it as PDF, or save it as an image.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QKeySequence, QPainter, QPainterPath, QPen, QShortcut, QTransform
from PyQt6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from app.core import scan, winapi
from app.core.i18n import tr
from app.ui.theme import Icons, Palette, Theme
from app.ui.widgets.common import IconButton, button, label, primary_button
from app.ui.widgets.segmented import SegmentedTabs

PREVIEW_SIDE = 1400   # the live preview works on a smaller copy; Copy / Save use the full photo
HANDLE = 11


class QuadEditor(QWidget):
    """The photo with four draggable corners."""
    changed = pyqtSignal()

    def __init__(self, img: QImage, quad: list[QPointF]):
        super().__init__()
        self.img = img
        self.quad = [QPointF(p) for p in quad]
        self.drag = -1
        self.setMinimumSize(360, 420)
        self.setMouseTracking(True)

    def _frame(self) -> tuple[QRectF, float]:
        r = QRectF(self.rect()).adjusted(HANDLE + 4, HANDLE + 4, -HANDLE - 4, -HANDLE - 4)
        s = min(r.width() / self.img.width(), r.height() / self.img.height())
        w, h = self.img.width() * s, self.img.height() * s
        return QRectF(r.x() + (r.width() - w) / 2, r.y() + (r.height() - h) / 2, w, h), s

    def _to_screen(self, p: QPointF) -> QPointF:
        f, s = self._frame()
        return QPointF(f.x() + p.x() * s, f.y() + p.y() * s)

    def _to_image(self, p: QPointF) -> QPointF:
        f, s = self._frame()
        x = min(max((p.x() - f.x()) / s, 0), self.img.width())
        y = min(max((p.y() - f.y()) / s, 0), self.img.height())
        return QPointF(x, y)

    def set_quad(self, quad: list[QPointF]) -> None:
        self.quad = [QPointF(p) for p in quad]
        self.update()
        self.changed.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        f, _s = self._frame()
        p.drawImage(f, self.img)
        pts = [self._to_screen(q) for q in self.quad]
        shade = QPainterPath()
        shade.addRect(f)
        page = QPainterPath()
        page.moveTo(pts[0])
        for q in pts[1:]:
            page.lineTo(q)
        page.closeSubpath()
        p.fillPath(shade.subtracted(page), QColor(0, 0, 0, 120))  # outside the page: dimmed
        p.setPen(QPen(QColor(Theme.accent()), 2))
        p.drawPath(page)
        for i, q in enumerate(pts):
            p.setBrush(QColor("white") if i != self.drag else QColor(Theme.accent()))
            p.setPen(QPen(QColor(Theme.accent()), 2.5))
            p.drawEllipse(q, HANDLE, HANDLE)

    def _hit(self, pos: QPointF) -> int:
        for i, q in enumerate(self.quad):
            s = self._to_screen(q)
            if (s.x() - pos.x()) ** 2 + (s.y() - pos.y()) ** 2 <= (HANDLE + 8) ** 2:
                return i
        return -1

    def mousePressEvent(self, e):
        self.drag = self._hit(e.position())
        self.update()

    def mouseMoveEvent(self, e):
        if self.drag >= 0:
            self.quad[self.drag] = self._to_image(e.position())
            self.update()
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor if self._hit(e.position()) >= 0 else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self.drag >= 0:
            self.drag = -1
            self.update()
            self.changed.emit()


class Preview(QWidget):
    def __init__(self):
        super().__init__()
        self.img: QImage | None = None
        self.setMinimumSize(320, 420)

    def set_image(self, img: QImage) -> None:
        self.img = img
        self.update()

    def paintEvent(self, _):
        if self.img is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        s = min(r.width() / self.img.width(), r.height() / self.img.height())
        w, h = self.img.width() * s, self.img.height() * s
        target = QRectF(r.x() + (r.width() - w) / 2, r.y() + (r.height() - h) / 2, w, h)
        p.fillRect(target.adjusted(-1, -1, 1, 1), QColor(0, 0, 0, 60))
        p.drawImage(target, self.img)


class ScanDialog(QWidget):
    def __init__(self, ctl, path: str):
        super().__init__(None, Qt.WindowType.Window)
        self.ctl = ctl
        self.path = path
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{tr('scan_title')} — Study Hub")
        self.full = scan.limit(QImage(path))
        quad, found = scan.find_page(self.full)
        self.small = self.full.scaled(PREVIEW_SIDE, PREVIEW_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
        self.ratio = self.small.width() / max(1, self.full.width())
        self.look = self.ctl.db.get_setting("scan_look", "document")
        if self.look not in scan.LOOKS:
            self.look = "document"
        self.turns = 0
        self.setStyleSheet(f"ScanDialog {{ background: {Palette.bg}; }}")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)
        head = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(tr("scan_title"), 16, QFont.Weight.DemiBold))
        self.hint = label(tr("scan_found") if found else tr("scan_not_found"), 9, role="Dim", wrap=True)
        col.addWidget(self.hint)
        head.addLayout(col, 1)
        self.looks = SegmentedTabs([("document", Icons.DOCUMENT, tr("scan_look_document")),
                                    ("original", Icons.PREVIEW, tr("scan_look_original")),
                                    ("grey", Icons.COLOR, tr("scan_look_grey"))], self.look)
        self.looks.changed.connect(self._set_look)
        head.addWidget(self.looks)
        root.addLayout(head)
        body = QHBoxLayout()
        body.setSpacing(14)
        left = QVBoxLayout()
        self.editor = QuadEditor(self.small, [QPointF(p.x() * self.ratio, p.y() * self.ratio) for p in quad])
        self.editor.changed.connect(lambda: self._timer.start())
        left.addWidget(self.editor, 1)
        tools = QHBoxLayout()
        auto = button(tr("scan_auto"), Icons.REFRESH, role="Ghost")
        auto.clicked.connect(self._auto)
        whole = button(tr("scan_whole"), Icons.FULLSCREEN, role="Ghost")
        whole.clicked.connect(lambda: self.editor.set_quad(scan.default_quad(self.small.width(), self.small.height(), 0)))
        tools.addWidget(auto)
        tools.addWidget(whole)
        tools.addStretch(1)
        left.addLayout(tools)
        body.addLayout(left, 1)
        right = QVBoxLayout()
        self.preview = Preview()
        right.addWidget(self.preview, 1)
        rot = QHBoxLayout()
        rot.addStretch(1)
        for glyph, turn, tip in ((Icons.UNDO, -1, "scan_rotate_left"), (Icons.REFRESH, 1, "scan_rotate_right")):
            b = IconButton(glyph, tr(tip), 32, 11)
            b.clicked.connect(lambda _=False, d=turn: self._rotate(d))
            rot.addWidget(b)
        rot.addStretch(1)
        right.addLayout(rot)
        body.addLayout(right, 1)
        root.addLayout(body, 1)
        actions = QHBoxLayout()
        self.size_label = label("", 8.8, role="Faint")
        actions.addWidget(self.size_label)
        actions.addStretch(1)
        for text, glyph, fn in ((tr("scan_save_image"), Icons.SAVE, self.save_image),
                                (tr("scan_save_pdf"), Icons.DOCUMENT, self.save_pdf)):
            b = button(text, glyph, role="Ghost")
            b.clicked.connect(fn)
            actions.addWidget(b)
        copy = primary_button(tr("scan_copy") + "  (Ctrl+C)", Icons.COPY)
        copy.clicked.connect(self.copy)
        actions.addWidget(copy)
        root.addLayout(actions)
        for keys, slot in (("Ctrl+C", self.copy), ("Ctrl+S", self.save_pdf), ("Escape", self.close)):
            QShortcut(QKeySequence(keys), self, activated=slot)
        self._timer = QTimer(self, singleShot=True, interval=60, timeout=self._update_preview)
        self.resize(1180, 760)
        self._update_preview()

    # ---------------------------------------------------------------- building the result
    def _quad_full(self) -> list[QPointF]:
        return [QPointF(p.x() / self.ratio, p.y() / self.ratio) for p in self.editor.quad]

    def _make(self, img: QImage, quad: list[QPointF]) -> QImage:
        out = scan.apply_look(scan.straighten(img, quad), self.look)
        if self.turns % 4:
            out = out.transformed(QTransform().rotate(90 * (self.turns % 4)), Qt.TransformationMode.SmoothTransformation)
        return out

    def result(self) -> QImage:
        return self._make(self.full, self._quad_full())

    def _update_preview(self) -> None:
        img = self._make(self.small, self.editor.quad)
        self.preview.set_image(img)
        full = self._quad_full()
        w = int(max(scan._dist(full[0], full[1]), scan._dist(full[3], full[2])))
        h = int(max(scan._dist(full[0], full[3]), scan._dist(full[1], full[2])))
        if self.turns % 2:
            w, h = h, w
        self.size_label.setText(f"{w} × {h}")

    def _set_look(self, key: str) -> None:
        self.look = key
        self.ctl.db.set_setting("scan_look", key)
        self._update_preview()

    def _rotate(self, d: int) -> None:
        self.turns += d
        self._update_preview()

    def _auto(self) -> None:
        quad, found = scan.find_page(self.full)
        self.hint.setText(tr("scan_found") if found else tr("scan_not_found"))
        self.editor.set_quad([QPointF(p.x() * self.ratio, p.y() * self.ratio) for p in quad])

    # ---------------------------------------------------------------- output
    def copy(self) -> None:
        QApplication.clipboard().setImage(self.result())
        self.ctl.notify(tr("scan_copied"), "success")

    def _ask_path(self, ext: str, kind: str) -> str:
        from app.core.phone_mirror import media_path
        default = str(media_path("shot", ext)).replace("Phone ", "Scan ")
        path, _ = QFileDialog.getSaveFileName(self, tr("scan_title"), default, kind)
        return path

    def save_pdf(self) -> None:
        path = self._ask_path("pdf", "PDF (*.pdf)")
        if path:
            scan.save_pdf(self.result(), path)
            self.ctl.notify(tr("scan_saved", path=path), "success")

    def save_image(self) -> None:
        path = self._ask_path("png", "PNG (*.png);;JPEG (*.jpg)")
        if path:
            self.result().save(path)
            self.ctl.notify(tr("scan_saved", path=path), "success")

    def showEvent(self, e):
        super().showEvent(e)
        from app.ui import apps as app_registry
        from app.ui.app_window import give_identity
        info = app_registry.find(self.ctl, "phone")
        if info:
            give_identity(self, info)
        else:
            winapi.dark_title_bar(self)
