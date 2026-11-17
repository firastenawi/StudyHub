"""Edit any desktop widget: title, colour, background transparency, header, size and its content —
with a small live preview of the card."""
from __future__ import annotations

import json
import os

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractButton, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout,
                             QHeaderView, QLineEdit, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from app.core.i18n import tr
from app.ui.desktop_widgets import WIDGET_TYPES
from app.ui.desktop_widgets2 import METRICS, paint_frame, widget_conf
from app.ui.dialogs.base import StyledDialog
from app.ui.theme import Icons, Palette, Theme, icon_font, ink, ui_font
from app.ui.widgets.common import ToggleSwitch, button, label, primary_button

COLORS = ["", "#7C5CFF", "#3B82F6", "#06B6D4", "#10B981", "#84CC16", "#FBBF24", "#F97316", "#F43F5E", "#EC4899"]


class ColorDot(QAbstractButton):
    def __init__(self, color: str):
        super().__init__()
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("wedit_theme_color") if not color else color)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(5, 5, -5, -5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.color or Theme.accent()))
        p.drawEllipse(r)
        if not self.color:  # "theme colour"
            p.setPen(QColor("#FFFFFF"))
            p.setFont(ui_font(7.5))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "A")
        if self.isChecked():
            p.setPen(QPen(QColor(Palette.text), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5))


class CardPreview(QWidget):
    def __init__(self, dialog: "WidgetEditDialog"):
        super().__init__()
        self.d = dialog
        self.setFixedSize(220, 150)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        from app.ui.widgets.background import paint_preview
        p.save()
        paint_preview(p, QRectF(self.rect()), self.d.ctl.wallpaper())
        p.restore()
        item = self.d.draft_item()
        r = QRectF(18, 16, self.width() - 36, self.height() - 32)
        paint_frame(p, r, item, False, 0)
        conf = widget_conf(item)
        if conf.get("header", True):
            p.setPen(QColor(item.get("color") or Theme.accent()))
            p.setFont(icon_font(9))
            p.drawText(QRectF(r.left() + 10, r.top(), 18, 36), Qt.AlignmentFlag.AlignCenter,
                       WIDGET_TYPES.get(item["args"], Icons.APPS))
            p.setPen(QColor(Palette.text))
            p.setFont(ui_font(8.6))
            p.drawText(QRectF(r.left() + 32, r.top(), r.width() - 40, 36), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(item["title"], Qt.TextElideMode.ElideRight, int(r.width() - 44)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink(0.12))
        top = r.top() + (44 if conf.get("header", True) else 16)
        for i in range(3):
            p.drawRoundedRect(QRectF(r.left() + 12, top + i * 14, (r.width() - 24) * (0.9 - i * 0.2), 6), 3, 3)


class WidgetEditDialog(StyledDialog):
    def __init__(self, ctl, item: dict, desk: str = "all", parent=None):
        super().__init__(tr("wedit_title"), parent, 640)
        self.ctl, self.item, self.desk = ctl, dict(item), desk
        self.wtype = item["args"] or "web"
        self.conf = widget_conf(item)
        cols = QHBoxLayout()
        cols.setSpacing(18)
        form = QVBoxLayout()
        form.setSpacing(10)
        cols.addLayout(form, 1)
        side = QVBoxLayout()
        side.addWidget(label(tr("preview_title"), 9.5, role="Dim"))
        self.preview = CardPreview(self)
        side.addWidget(self.preview)
        side.addStretch(1)
        cols.addLayout(side)
        self.body.addLayout(cols)

        form.addWidget(label(tr("wedit_name"), 9.5, role="Dim"))
        self.title = QLineEdit(item["title"])
        self.title.textChanged.connect(self.preview.update)
        form.addWidget(self.title)

        form.addWidget(label(tr("wedit_color"), 9.5, role="Dim"))
        dots = QHBoxLayout()
        dots.setSpacing(2)
        self.colors = QButtonGroup(self)
        for c in COLORS:
            d = ColorDot(c)
            d.setChecked(c == (item.get("color") or ""))
            d.clicked.connect(self.preview.update)
            self.colors.addButton(d)
            dots.addWidget(d)
        dots.addStretch(1)
        form.addLayout(dots)

        row = QHBoxLayout()
        row.addWidget(label(tr("wedit_opacity"), 9.5, role="Dim"))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(int(float(self.conf.get("opacity", 1.0)) * 100))
        self.opacity.valueChanged.connect(self.preview.update)
        row.addWidget(self.opacity, 1)
        form.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(label(tr("wedit_header"), 9.5, role="Dim"))
        row.addStretch(1)
        self.header = ToggleSwitch(bool(self.conf.get("header", True)))
        self.header.toggled.connect(lambda _: self.preview.update())
        row.addWidget(self.header)
        form.addLayout(row)

        pos = ctl.db.desk_positions(desk).get(item["id"])
        row = QHBoxLayout()
        row.addWidget(label(tr("wedit_size"), 9.5, role="Dim"))
        row.addStretch(1)
        self.w = QSpinBox()
        self.h = QSpinBox()
        for spin, value in ((self.w, pos[2] if pos else 3), (self.h, pos[3] if pos else 3)):
            spin.setRange(1, 8)
            spin.setValue(int(value))
            spin.setFixedWidth(80)
        row.addWidget(self.w)
        row.addWidget(label("×", 10))
        row.addWidget(self.h)
        form.addLayout(row)

        # ---------------------------------------------------------- content
        self.body.addWidget(label(tr("wedit_content"), 10.5))
        self._content()
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = button(tr("cancel"), role="Ghost")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = primary_button(tr("save"), Icons.ACCEPT)
        ok.clicked.connect(self._save)
        buttons.addWidget(ok)
        self.body.addLayout(buttons)

    def _content(self) -> None:
        t = self.wtype
        b = self.body
        if t in ("web", "pdf", "image"):
            row = QHBoxLayout()
            self.target = QLineEdit(self.item["target"] if t != "image" else self.conf.get("path", ""))
            self.target.setPlaceholderText("https://…" if t == "web" else tr("wedit_file"))
            row.addWidget(self.target, 1)
            if t != "web":
                browse = button(tr("browse"), Icons.OPEN_FOLDER, role="Ghost")
                browse.clicked.connect(self._browse)
                row.addWidget(browse)
            b.addLayout(row)
            if t == "image":
                r2 = QHBoxLayout()
                r2.addWidget(label(tr("wedit_fill"), 9.5, role="Dim"))
                r2.addStretch(1)
                self.fill = ToggleSwitch(bool(self.conf.get("fill", True)))
                r2.addWidget(self.fill)
                b.addLayout(r2)
        elif t == "system":
            grid = QGridLayout()
            self.metric_boxes = {}
            chosen = self.conf.get("metrics", METRICS)
            for i, m in enumerate(METRICS):
                box = QCheckBox(tr(f"wsys_{m}"))
                box.setChecked(m in chosen)
                self.metric_boxes[m] = box
                grid.addWidget(box, i // 3, i % 3)
            b.addLayout(grid)
        elif t == "note":
            self.note = QComboBox()
            self.note.setEditable(False)
            for n in self.ctl.db.list_notes("all")[:300]:
                title = (n.get("title") or (n.get("content") or "").strip().split("\n")[0] or "…")[:70]
                self.note.addItem(title, n["id"])
            self.note.setCurrentIndex(max(0, self.note.findData(self.conf.get("note_id"))))
            b.addWidget(self.note)
        elif t == "links":
            b.addWidget(label(tr("wlinks_hint"), 8.8, role="Dim", wrap=True))
            self.table = QTableWidget(0, 2)
            self.table.setHorizontalHeaderLabels([tr("wedit_name"), tr("wlinks_target")])
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            self.table.verticalHeader().hide()
            self.table.setMinimumHeight(180)
            for link in self.conf.get("links", []):
                self._add_row(link.get("title", ""), link.get("target", ""))
            b.addWidget(self.table)
            row = QHBoxLayout()
            add = button(tr("wlinks_add"), Icons.ADD, role="Ghost")
            add.clicked.connect(lambda: self._add_row("", ""))
            row.addWidget(add)
            rem = button(tr("delete"), Icons.DELETE, role="Ghost")
            rem.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()) if self.table.currentRow() >= 0 else None)
            row.addWidget(rem)
            row.addStretch(1)
            b.addLayout(row)
        else:
            b.addWidget(label(tr("wedit_nothing"), 9, role="Faint"))

    def _add_row(self, title: str, target: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(title))
        self.table.setItem(r, 1, QTableWidgetItem(target))

    def _browse(self) -> None:
        if self.wtype == "pdf":
            path, _ = QFileDialog.getOpenFileName(self, tr("widget_pdf"), "", "PDF (*.pdf)")
        else:
            path, _ = QFileDialog.getOpenFileName(self, tr("widget_image"), "", "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        if path:
            self.target.setText(os.path.normpath(path))

    def _color(self) -> str:
        b = self.colors.checkedButton()
        return b.color if b else ""

    def draft_item(self) -> dict:
        conf = dict(self.conf)
        conf["opacity"] = self.opacity.value() / 100 if hasattr(self, "opacity") else conf.get("opacity", 1.0)
        conf["header"] = self.header.isChecked() if hasattr(self, "header") else conf.get("header", True)
        return {**self.item, "title": self.title.text().strip() or self.item["title"] if hasattr(self, "title") else self.item["title"],
                "color": self._color() if hasattr(self, "colors") else self.item.get("color", ""),
                "icon_path": json.dumps(conf)}

    def _save(self) -> None:
        item = self.draft_item()
        conf = json.loads(item["icon_path"])
        target = self.item["target"]
        t = self.wtype
        if t == "web":
            from app.core.launcher import normalize_url
            url = normalize_url(self.target.text().strip())
            if not url:
                self.ctl.notify(tr("error_invalid_url"), "error")
                return
            target = url
        elif t == "pdf":
            target = self.target.text().strip()
        elif t == "image":
            conf["path"] = self.target.text().strip()
            conf["fill"] = self.fill.isChecked()
        elif t == "system":
            conf["metrics"] = [m for m, box in self.metric_boxes.items() if box.isChecked()] or ["cpu"]
        elif t == "note":
            conf["note_id"] = self.note.currentData()
        elif t == "links":
            links = []
            for r in range(self.table.rowCount()):
                title = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
                tgt = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()
                if tgt:
                    links.append({"title": title or tgt, "target": tgt})
            conf["links"] = links
        self.ctl.db.update_item(self.item["id"], title=item["title"], color=item["color"],
                                icon_path=json.dumps(conf), target=target)
        pos = self.ctl.db.desk_positions(self.desk).get(self.item["id"])
        if pos and (pos[2], pos[3]) != (self.w.value(), self.h.value()):
            self.ctl.db.set_positions(self.desk, {self.item["id"]: (pos[0], pos[1], self.w.value(), self.h.value())})
        self.ctl.data_changed.emit("launcher")
        self.accept()
