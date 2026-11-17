"""
More desktop widgets (live windows pinned on the Desktop) + the settings every widget has.

  system   battery, processor, memory, disk, network — the ones you choose
  focus    the focus timer: time left, phase, start / pause
  classes  the next class today and the ones after it
  note     one note from the notebook, always in view
  image    a picture (fit or fill)
  links    your own buttons: websites, files, folders, programs or Study Hub commands

Every widget (also web / PDF / tasks) can be edited: title, colour, background transparency, size, header on/off,
and its content. The look is stored with the item (`icon_path` holds a small JSON, `color` the accent), so a change
rebuilds just that widget.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.core.i18n import format_hhmm, tr
from app.ui.theme import Icons, Palette, Theme, ink, qcolor, ui_font
from app.ui.widgets.common import EmptyState, IconButton, label

NEW_TYPES = {"system": Icons.LIGHTNING, "focus": Icons.TIMER, "classes": Icons.STUDY, "note": Icons.NOTES,
             "image": Icons.PREVIEW, "links": Icons.LINK}
NEW_SIZES = {"system": (3, 3), "focus": (2, 3), "classes": (3, 3), "note": (3, 3), "image": (3, 3), "links": (3, 2)}
METRICS = ["battery", "cpu", "ram", "disk", "net"]


def widget_conf(item: dict) -> dict:
    """{"opacity": 0.3-1, "header": bool, ...type settings} stored in the item's icon_path."""
    try:
        conf = json.loads(item.get("icon_path") or "{}")
        return conf if isinstance(conf, dict) else {}
    except ValueError:
        return {}


def accent_of(item: dict) -> str:
    return item.get("color") or Theme.accent()


# ===================================================================== panes
class _Bar(QWidget):
    """A labelled meter: name, value text and a rounded bar."""

    def __init__(self, title: str, color: str, high_is_bad: bool = True):
        super().__init__()
        self.title, self.color, self.high_is_bad = title, color, high_is_bad
        self.value, self.text = 0.0, "—"
        self.setFixedHeight(34)

    def set(self, value: float, text: str) -> None:
        self.value, self.text = max(0.0, min(1.0, value)), text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        p.setPen(QColor(Palette.text_dim))
        p.setFont(ui_font(8.6))
        head = QRectF(0, 0, self.width(), 16)
        p.drawText(head, (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter, self.title)
        p.setPen(QColor(Palette.text))
        p.setFont(ui_font(8.6, QFont.Weight.DemiBold))
        # numbers and units read left-to-right even in Arabic (isolated, so "7.6 / 15 GB" stays in order)
        p.drawText(head, (Qt.AlignmentFlag.AlignLeft if rtl else Qt.AlignmentFlag.AlignRight) | Qt.AlignmentFlag.AlignVCenter,
                   "⁦" + self.text + "⁩")
        track = QRectF(0, 21, self.width(), 7)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink(0.08))
        p.drawRoundedRect(track, 3.5, 3.5)
        alarm = self.value > 0.9 if self.high_is_bad else self.value < 0.15  # full disk… or an almost empty battery
        color = QColor(Palette.danger if alarm else self.color)
        fill = QRectF(track.right() - track.width() * self.value if rtl else track.left(), track.top(),
                      track.width() * self.value, track.height())
        p.setBrush(color)
        p.drawRoundedRect(fill, 3.5, 3.5)


class SystemPane(QWidget):
    def __init__(self, item: dict, header, ctl, parent=None):
        super().__init__(parent)
        conf = widget_conf(item)
        self.metrics = [m for m in conf.get("metrics", METRICS) if m in METRICS] or METRICS
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 10)
        lay.setSpacing(6)
        color = accent_of(item)
        self.bars = {}
        for m in self.metrics:
            bar = _Bar(tr(f"wsys_{m}"), color, high_is_bad=m not in ("battery", "net"))
            self.bars[m] = bar
            lay.addWidget(bar)
        lay.addStretch(1)
        self._net = None
        self._timer = QTimer(self, interval=1500, timeout=self._update)
        self._timer.start()
        QTimer.singleShot(50, self._update)

    def _update(self):
        if not self.isVisible():
            return
        try:
            import psutil
        except ImportError:
            psutil = None
        if "battery" in self.bars:
            from app.core.winapi import battery
            b = battery()
            self.bars["battery"].set((b[0] / 100) if b else 0, (f"{b[0]}%" + (" ⚡" if b[1] else "")) if b else tr("wsys_no_battery"))
        if psutil is None:
            return
        if "cpu" in self.bars:
            v = psutil.cpu_percent(interval=None)
            self.bars["cpu"].set(v / 100, f"{v:.0f}%")
        if "ram" in self.bars:
            m = psutil.virtual_memory()
            self.bars["ram"].set(m.percent / 100, f"{m.used / 2**30:.1f} / {m.total / 2**30:.0f} GB")
        if "disk" in self.bars:
            d = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
            self.bars["disk"].set(d.percent / 100, f"{d.free / 2**30:.0f} GB {tr('wsys_free')}")
        if "net" in self.bars:
            now = time.time()
            io = psutil.net_io_counters()
            if self._net:
                t0, r0, s0 = self._net
                dt = max(0.5, now - t0)
                down, up = (io.bytes_recv - r0) / dt, (io.bytes_sent - s0) / dt
                self.bars["net"].set(min(1.0, down / (10 * 2**20)), f"↓ {_speed(down)}  ↑ {_speed(up)}")
            self._net = (now, io.bytes_recv, io.bytes_sent)

    def refresh(self, what: str):
        pass


def _speed(bps: float) -> str:
    return f"{bps / 2**20:.1f} MB/s" if bps >= 2**20 else f"{bps / 1024:.0f} KB/s"


class FocusPane(QWidget):
    def __init__(self, item: dict, header, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.color = accent_of(item)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 10)
        lay.setSpacing(6)
        self.time = label("25:00", 24, QFont.Weight.DemiBold)
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.phase = label("", 9, role="Dim")
        self.phase.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1)
        lay.addWidget(self.time)
        lay.addWidget(self.phase)
        row = QHBoxLayout()
        row.addStretch(1)
        self.play = QPushButton()
        self.play.setObjectName("Primary")
        self.play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play.clicked.connect(ctl.pomodoro.toggle)
        row.addWidget(self.play)
        reset = IconButton(Icons.REFRESH, tr("reset"), 32, 10)
        reset.clicked.connect(ctl.pomodoro.reset)
        row.addWidget(reset)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        header.add_button(Icons.FULLSCREEN, tr("app_timer"), lambda: ctl.apps.open("timer"))
        t = ctl.pomodoro
        for sig in (t.tick, t.state_changed, t.phase_changed):
            sig.connect(self._render)
        self.destroyed.connect(lambda *_: self._disconnect(t))
        self._render()

    def _disconnect(self, t):
        try:  # at shutdown the timer may already be gone
            for sig in (t.tick, t.state_changed, t.phase_changed):
                try:
                    sig.disconnect(self._render)
                except (TypeError, RuntimeError):
                    pass
        except RuntimeError:
            pass

    def _render(self, *_):
        try:
            t = self.ctl.pomodoro
            m, s = divmod(t.remaining, 60)
            self.time.setText(f"{m:02d}:{s:02d}")
            self.time.setStyleSheet(f"color: {self.color};" if t.state == "running" else "")
            self.phase.setText(tr(f"phase_{t.phase}") + (" · " + tr("paused") if t.state == "paused" else ""))
            self.play.setText(tr("pause") if t.state == "running" else tr("resume") if t.state == "paused" else tr("start"))
        except RuntimeError:  # widget already deleted
            pass

    def refresh(self, what: str):
        pass


class ClassesPane(QWidget):
    def __init__(self, item: dict, header, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.color = accent_of(item)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(12, 4, 12, 10)
        self.lay.setSpacing(4)
        header.add_button(Icons.STUDY, tr("tab_timetable"), lambda: ctl.open_study_tab("timetable"))
        self._timer = QTimer(self, interval=60_000, timeout=lambda: self.refresh(""))
        self._timer.start()
        self.refresh("")

    def refresh(self, what: str):
        if what not in ("", "schedule", "subjects"):
            return
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        classes = self.ctl.db.upcoming_classes()
        if not classes:
            self.lay.addWidget(EmptyState(Icons.ACCEPT, tr("no_more_classes")))
            return
        nxt = classes[0]
        big = label(nxt["subject_name"], 13, QFont.Weight.DemiBold, wrap=True)
        big.setStyleSheet(f"color: {nxt.get('subject_color') or self.color};")
        self.lay.addWidget(big)
        where = f"  ·  {nxt['location']}" if nxt.get("location") else ""
        self.lay.addWidget(label(f"{format_hhmm(nxt['start_time'])} – {format_hhmm(nxt['end_time'])}{where}", 9.5, role="Dim"))
        for c in classes[1:4]:
            self.lay.addWidget(label(f"{format_hhmm(c['start_time'])}   {c['subject_name']}", 9, role="Faint"))
        self.lay.addStretch(1)


class NotePane(QWidget):
    def __init__(self, item: dict, header, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.note_id = widget_conf(item).get("note_id")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 10)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.text.setTextFormat(Qt.TextFormat.MarkdownText)
        self.text.setOpenExternalLinks(True)
        lay.addWidget(self.text, 1)
        header.add_button(Icons.EDIT, tr("open"), lambda: ctl.open_notebook(self.note_id))
        self.refresh("")

    def refresh(self, what: str):
        if what not in ("", "notes"):
            return
        note = self.ctl.db.get_note(int(self.note_id)) if self.note_id else None
        if not note:
            self.text.setText(tr("wnote_pick"))
            return
        body = (note.get("content") or "").strip()
        self.text.setText(body[:3000] or (note.get("title") or ""))


class ImagePane(QWidget):
    def __init__(self, item: dict, header, ctl, parent=None):
        super().__init__(parent)
        conf = widget_conf(item)
        self.path = conf.get("path", "")
        self.fill = conf.get("fill", True)
        self.img = QImage(self.path) if self.path else QImage()
        if self.path:
            header.add_button(Icons.OPEN_FOLDER, tr("open_external"), lambda: ctl.open_path(self.path))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(6, 2, -6, -6)
        if self.img.isNull():
            p.setPen(QColor(Palette.text_faint))
            p.setFont(ui_font(9))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, tr("wimage_pick"))
            return
        mode = Qt.AspectRatioMode.KeepAspectRatioByExpanding if self.fill else Qt.AspectRatioMode.KeepAspectRatio
        scaled = self.img.scaled(r.size().toSize(), mode, Qt.TransformationMode.SmoothTransformation)
        x = r.left() + (r.width() - scaled.width()) / 2
        y = r.top() + (r.height() - scaled.height()) / 2
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        p.setClipPath(path)
        p.drawImage(int(x), int(y), scaled)

    def refresh(self, what: str):
        pass


class LinksPane(QWidget):
    def __init__(self, item: dict, header, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        links = widget_conf(item).get("links", [])
        grid = QGridLayout(self)
        grid.setContentsMargins(10, 4, 10, 10)
        grid.setSpacing(6)
        if not links:
            grid.addWidget(EmptyState(Icons.LINK, tr("wlinks_empty")), 0, 0)
            return
        cols = 2 if len(links) > 2 else 1
        for i, link in enumerate(links[:12]):
            b = QPushButton(link.get("title") or link.get("target", ""))
            b.setObjectName("Chip")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(link.get("target", ""))
            b.clicked.connect(lambda _=False, t=link.get("target", ""): self._open(t))
            grid.addWidget(b, i // cols, i % cols)

    def _open(self, target: str):
        target = target.strip()
        if target.startswith("cmd:"):
            self.ctl.run_command(target[4:])
        elif target.startswith("app:"):
            self.ctl.apps.open(target[4:])
        elif "://" in target or target.startswith("www."):
            self.ctl.open_url(target if "://" in target else "https://" + target)
        elif target:
            self.ctl.open_path(target)

    def refresh(self, what: str):
        pass


NEW_PANES = {"system": SystemPane, "focus": FocusPane, "classes": ClassesPane, "note": NotePane,
             "image": ImagePane, "links": LinksPane}


def paint_frame(p: QPainter, rect: QRectF, item: dict, selected: bool, hover: float) -> QPainterPath:
    """The widget's card: background transparency, accent line under the header (or no header)."""
    conf = widget_conf(item)
    opacity = float(conf.get("opacity", 1.0))
    header = conf.get("header", True)
    path = QPainterPath()
    path.addRoundedRect(rect, 14, 14)
    bg = QColor(Palette.surface_solid)
    bg.setAlphaF(max(0.0, min(1.0, opacity)))
    p.fillPath(path, bg)
    p.save()
    p.setClipPath(path)
    if header:
        p.fillRect(QRectF(rect.left(), rect.top(), rect.width(), 36), ink(0.05 + 0.03 * hover))
        if item.get("color"):
            p.fillRect(QRectF(rect.left(), rect.top() + 34, rect.width(), 2), qcolor(item["color"], 0.85))
    p.restore()
    p.setPen(QPen(qcolor(Theme.accent(), 0.9) if selected else ink((0.10 + 0.08 * hover) * max(0.35, opacity)),
                  1.6 if selected else 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    return path
