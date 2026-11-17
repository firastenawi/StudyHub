"""
The Desktop: a free-form surface for apps, websites, folders, files and sticky notes.

Works like the Windows desktop:
  * drag anything anywhere, it snaps to the grid (animated); select many with a
    rubber band or Ctrl+click and move them together
  * double-click / Enter opens, F2 renames in place, Delete removes,
    Ctrl+C copies (files can be pasted into Explorer), Ctrl+V pastes
    (copied files, a link, or any text -> a sticky note), Ctrl+A selects all
  * sticky notes are real notebook notes: resize them from the corner grip
  * every desk (Main + one per group) is separate: it shows only its own items and remembers its own layout
"""
from __future__ import annotations

import math
import re

from PyQt6.QtCore import QPoint, QPointF, QPropertyAnimation, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLineEdit, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from app.core.i18n import is_rtl, tr
from app.ui.animations import OUT, OUT_BACK, dispose, start_delayed, value_animation
from app.ui.desktop_widgets import DEFAULT_SIZE, PANES, WIDGET_TYPES, WidgetHeader, paint_widget_frame
from app.ui.pages.base import Page
from app.ui.theme import Icons, Palette, Theme, icon_font, qcolor, ui_font, ink
from app.ui.widgets.common import IconButton, add_action, add_submenu, label, make_menu, primary_button
from app.ui.widgets.smooth_scroll import SmoothScrollArea
from app.ui.widgets.tiles import item_pixmap, paint_item_icon

CELL_W, CELL_H, PAD = 112, 118, 22
NOTE_COLORS = ["#FBBF24", "#FB923C", "#F87171", "#F472B6", "#A78BFA", "#60A5FA", "#22D3EE", "#34D399",
               "#A3E635", "#94A3B8"]


def sticky_font(item: dict) -> float:
    """A sticky note's text size (A- / A+), kept with the desktop item."""
    import json
    try:
        return float(json.loads(item.get("icon_path") or "{}").get("font", 9.3))
    except (ValueError, TypeError, AttributeError):
        return 9.3


def sticky_html(lines: list[str], rtl_default: bool, color: str) -> str:
    """The note's lines as rich text: **bold**, *italic*, <u>underline</u>, ~~strike~~, `code`, ☐ / ☑, bullets —
    each line in its own direction (Arabic right-to-left, Latin left-to-right)."""
    import html as _h
    out = []
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            out.append("<p style='margin:0;line-height:60%'>&nbsp;</p>")
            continue
        line = re.sub(r"^#+\s*", "", line)
        box = ""
        m = re.match(r"^(\s*)[-*+] \[([ xX])\] (.*)$", line)
        if m:
            done = m.group(2) in "xX"
            box, line = ("☑ " if done else "☐ "), m.group(3)
        else:
            m = re.match(r"^(\s*)[-*+] (.*)$", line)
            if m:
                box, line = "• ", m.group(2)
        rtl = _starts_rtl(line, rtl_default)
        t = _h.escape(line)
        t = re.sub(r"&lt;u&gt;(.+?)&lt;/u&gt;", r"<u>\1</u>", t)       # underline (typed as <u>…</u>)
        t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<b>{m.group(1) or m.group(2)}</b>", t)
        t = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<i>\1</i>", t)
        t = re.sub(r"~~(.+?)~~", r"<s>\1</s>", t)
        t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
        if box:
            t = f"<span style='color:{color}'>{box}</span>{t}"
        out.append(f"<p dir='{'rtl' if rtl else 'ltr'}' align='{'right' if rtl else 'left'}' style='margin:0 0 3px 0'>{t}</p>")
    return "".join(out)
MAX_NOTE_CELLS = 8                                  # notes and live windows: up to 8x8 cells
RESIZABLE_KINDS = ("note", "widget")


# ==================================================================== items
class DesktopItem(QWidget):
    """Base for things on the desktop. Mouse presses fall through to the canvas,
    which handles selection, dragging and menus; items only paint themselves."""

    def __init__(self, item: dict, canvas):
        super().__init__(canvas)
        self.item = item
        self.item_id = item["id"]
        self.signature = None  # set by the canvas: unchanged items are reused, not rebuilt
        self.selected = False
        self.dimmed = False
        self._hover = 0.0
        self._appear = 1.0
        self._hover_anim = value_animation(self, 0.0, 1.0, 160, self._set_hover, OUT)
        self._appear_anim = value_animation(self, 0.0, 1.0, 480, self._set_appear, OUT_BACK)
        self.setMouseTracking(True)  # hover moves propagate to the canvas (resize cursor)
        self.setToolTip(item["title"] if item["kind"] == "note" else f"{item['title']}\n{item['target']}")

    def _set_hover(self, v):
        self._hover = float(v)
        self.update()

    def _set_appear(self, v):
        self._appear = float(v)
        self.update()

    def appear(self, delay: int):
        self._appear = 0.0
        start_delayed(self, self._appear_anim, delay)

    def set_selected(self, on: bool):
        if on != self.selected:
            self.selected = on
            self.update()

    def set_dimmed(self, on: bool):
        if on != self.dimmed:
            self.dimmed = on
            self.update()

    def enterEvent(self, e):
        self._hover_anim.setDirection(self._hover_anim.Direction.Forward)
        self._hover_anim.start()

    def leaveEvent(self, e):
        self._hover_anim.setDirection(self._hover_anim.Direction.Backward)
        self._hover_anim.start()

    def _begin(self, p: QPainter):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setOpacity(max(0.0, min(1.0, self._appear * 1.4)) * (0.22 if self.dimmed else 1.0))
        c = QRectF(self.rect()).center()
        s = 0.85 + 0.15 * self._appear
        p.translate(c)
        p.scale(s, s)
        p.translate(-c)


class IconItem(DesktopItem):
    def __init__(self, item: dict, canvas):
        super().__init__(item, canvas)
        self.pixmap = item_pixmap(item)
        self.setFixedSize(CELL_W - 8, CELL_H - 6)

    def label_rect(self) -> QRect:
        return QRect(4, 74, self.width() - 8, 36)

    def paintEvent(self, _):
        p = QPainter(self)
        self._begin(p)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        if self.selected:
            p.fillPath(path, qcolor(Theme.accent(), 0.20))
            p.setPen(QPen(qcolor(Theme.accent(), 0.85), 1.4))
            p.drawPath(path)
        elif self._hover > 0.01:
            p.fillPath(path, ink(0.06 * self._hover))
        plate = QRectF(r.center().x() - 27, r.top() + 10, 54, 54)
        paint_item_icon(p, plate, self.item, self.pixmap, self._hover)
        # title: up to two lines, with a soft shadow for legibility on the wallpaper
        p.setFont(ui_font(9, QFont.Weight.Medium))
        text = self._two_lines(p, self.item["title"], int(r.width() - 6))
        rect = QRectF(self.label_rect())
        p.setPen(qcolor("#FFFFFF", 0.7) if Palette.light() else qcolor("#000000", 0.55))
        p.drawText(rect.translated(0, 1), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)
        p.setPen(QColor(Palette.text))
        p.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)

    @staticmethod
    def _two_lines(p: QPainter, text: str, width: int) -> str:
        fm = p.fontMetrics()
        if fm.horizontalAdvance(text) <= width:
            return text
        words, first = text.split(), ""
        while words and fm.horizontalAdvance((first + " " + words[0]).strip()) <= width:
            first = (first + " " + words.pop(0)).strip()
        if not first:  # one long word
            return fm.elidedText(text, Qt.TextElideMode.ElideRight, width)
        return first + "\n" + fm.elidedText(" ".join(words), Qt.TextElideMode.ElideRight, width)


class NoteItem(DesktopItem):
    """Sticky note: shows the note's title and first lines, colored, resizable."""

    def __init__(self, item: dict, canvas, note: dict | None):
        super().__init__(item, canvas)
        self.note = note or {"title": item["title"], "content": ""}
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def color(self) -> str:
        return self.item.get("color") or NOTE_COLORS[0]

    def grip_rect(self) -> QRect:
        size = 18
        x = 4 if is_rtl() else self.width() - size - 4
        return QRect(x, self.height() - size - 4, size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        self._begin(p)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        color = self.color()
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        p.fillPath(path, QColor(Palette.surface_solid))
        p.fillPath(path, qcolor(color, 0.16 + 0.04 * self._hover))
        p.setPen(QPen(qcolor(Theme.accent(), 0.9) if self.selected else qcolor(color, 0.45), 1.6 if self.selected else 1))
        p.drawPath(path)
        p.save()
        p.setClipPath(path)
        p.fillRect(QRectF(r.left(), r.top(), r.width(), 6), QColor(color))
        p.restore()

        rtl = is_rtl()
        inner = r.adjusted(14, 16, -14, -26)

        def align_for(text: str):
            """Each line follows its own language: Arabic right-to-left, Latin left-to-right."""
            line_rtl = _starts_rtl(text, rtl)
            p.setLayoutDirection(Qt.LayoutDirection.RightToLeft if line_rtl else Qt.LayoutDirection.LeftToRight)
            return (Qt.AlignmentFlag.AlignRight if line_rtl else Qt.AlignmentFlag.AlignLeft) \
                | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignTop
        title = (self.note.get("title") or "").strip()
        lines = [ln for ln in (self.note.get("content") or "").splitlines()]
        if not title and lines:
            title, lines = lines[0], lines[1:]
        title = re.sub(r"^#+\s*", "", title) or tr("untitled")
        title = re.sub(r"</?u>|\*\*|__|~~|`", "", title)  # the title line shows plain (it is already bold)
        title = re.sub(r"(?<![\w*])\*(\S.*?)\*(?![\w*])", r"\1", title)
        p.setPen(QColor(Palette.text))
        p.setFont(ui_font(sticky_font(self.item) + 1.2, QFont.Weight.DemiBold))
        fm = p.fontMetrics()
        p.drawText(QRectF(inner.left(), inner.top(), inner.width(), fm.height() + 4), align_for(title),
                   fm.elidedText(title, Qt.TextElideMode.ElideRight, int(inner.width())))
        size = sticky_font(self.item)
        from PyQt6.QtGui import QTextDocument
        doc = QTextDocument()
        doc.setDefaultFont(ui_font(size))
        doc.setDocumentMargin(0)
        doc.setDefaultStyleSheet(f"p {{ color: {Palette.text_dim}; }} b {{ color: {Palette.text}; }} "
                                 f"code {{ background: rgba(127,127,127,0.18); }}")
        doc.setHtml(sticky_html(lines[:80], rtl, color))
        doc.setTextWidth(inner.width())
        body = QRectF(inner.left(), inner.top() + 22 + size * 0.6, inner.width(), inner.bottom() - inner.top() - 22 - size * 0.6)
        p.save()
        p.setLayoutDirection(Qt.LayoutDirection.LeftToRight)  # each paragraph carries its own direction
        p.translate(body.topLeft())
        p.setClipRect(QRectF(0, 0, body.width(), body.height()))
        doc.drawContents(p, QRectF(0, 0, body.width(), body.height()))
        p.restore()
        p.setLayoutDirection(self.layoutDirection())
        # resize grip
        g = QRectF(self.grip_rect())
        p.setPen(QPen(qcolor(color, 0.35 + 0.5 * self._hover), 1.4))
        for i in range(3):  # three diagonal strokes in the trailing bottom corner
            o = 5 + i * 4
            if rtl:
                p.drawLine(QPointF(g.left(), g.top() + o), QPointF(g.left() + o, g.bottom()))
            else:
                p.drawLine(QPointF(g.right(), g.top() + o), QPointF(g.right() - o, g.bottom()))

    @staticmethod
    def _pretty(line: str) -> str:
        """Markdown -> readable preview: checkboxes, bullets, no ** / # / `."""
        line = re.sub(r"^(\s*)[-*+] \[[xX]\] ", r"\1☑ ", line)
        line = re.sub(r"^(\s*)[-*+] \[ \] ", r"\1☐ ", line)
        line = re.sub(r"^(\s*)[-*+] ", r"\1• ", line)
        line = re.sub(r"^#+\s*", "", line)
        return line.replace("**", "").replace("`", "").replace("~~", "")


class WidgetItem(DesktopItem):
    """A live window (web page / PDF / task list) pinned on the desktop."""

    def __init__(self, item: dict, canvas):
        super().__init__(item, canvas)
        wtype = item["args"] or "web"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 16)  # bottom strip stays free for the resize grip
        lay.setSpacing(0)
        from app.ui.desktop_widgets2 import widget_conf
        self.header = WidgetHeader(WIDGET_TYPES.get(wtype, Icons.APPS), item["title"])
        lay.addWidget(self.header)
        pane_cls = PANES.get(wtype)
        self.pane = pane_cls(item, self.header, canvas.ctl) if pane_cls else QWidget()
        lay.addWidget(self.pane, 1)
        if not widget_conf(item).get("header", True):  # header off: a slim strip stays as the drag handle
            self.header.setFixedHeight(14)
            for w in self.header.findChildren(QWidget):
                w.hide()
        self.setToolTip("")

    def grip_rect(self) -> QRect:
        size = 16
        x = 4 if is_rtl() else self.width() - size - 4
        return QRect(x, self.height() - size - 1, size, size)

    def pane_refresh(self, what: str):
        if hasattr(self.pane, "refresh"):
            self.pane.refresh(what)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(max(0.0, min(1.0, self._appear * 1.4)) * (0.35 if self.dimmed else 1.0))
        from app.ui.desktop_widgets2 import paint_frame
        paint_frame(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), self.item, self.selected, self._hover)
        g = QRectF(self.grip_rect())
        p.setPen(QPen(ink(0.25 + 0.4 * self._hover), 1.4))
        for i in range(3):
            o = 4 + i * 4
            if is_rtl():
                p.drawLine(QPointF(g.left(), g.top() + o), QPointF(g.left() + o, g.bottom()))
            else:
                p.drawLine(QPointF(g.right(), g.top() + o), QPointF(g.right() - o, g.bottom()))


def default_cells(item: dict) -> tuple[int, int]:
    if item["kind"] == "note":
        return 2, 2
    if item["kind"] == "widget":
        return DEFAULT_SIZE.get(item["args"] or "web", (4, 3))
    return 1, 1


# =================================================================== canvas
class DesktopCanvas(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl, self.page = ctl, page
        self.desk = "all"
        self.widgets: dict[int, DesktopItem] = {}
        self.pos_map: dict[int, tuple[int, int, int, int]] = {}
        self.selection: set[int] = set()
        self.filter_text = ""
        self._mode: str | None = None
        self._press = QPoint()
        self._origins: dict[int, QPoint] = {}
        self._ghosts: list[QRect] = []
        self._band: QRect | None = None
        self._resize_id: int | None = None
        self._editor: QLineEdit | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

    # ------------------------------------------------------------- geometry
    def cols(self) -> int:
        viewport = self.parentWidget()
        width = viewport.width() if viewport else self.width()
        if width < 400:  # not laid out yet (startup): assume the screen width instead of ~100 px
            screen = self.screen() or QGuiApplication.primaryScreen()
            width = screen.geometry().width() if screen else 1280  # the desktop is the whole screen
        return max(MAX_NOTE_CELLS, (width - 2 * PAD) // CELL_W)  # the widest note always fits

    def cell_rect(self, c: int, r: int, w: int = 1, h: int = 1) -> QRect:
        x = PAD + c * CELL_W
        if is_rtl():
            x = self.width() - PAD - (c + w) * CELL_W
        return QRect(x, PAD + r * CELL_H, w * CELL_W, h * CELL_H)

    def cell_at(self, point: QPoint) -> tuple[int, int]:
        x = self.width() - point.x() if is_rtl() else point.x()
        return max(0, (x - PAD) // CELL_W), max(0, (point.y() - PAD) // CELL_H)

    def _widget_geometry(self, item_id: int) -> QRect:
        c, r, w, h = self.pos_map[item_id]
        cell = self.cell_rect(c, r, w, h)
        widget = self.widgets[item_id]
        if isinstance(widget, (NoteItem, WidgetItem)):
            return cell.adjusted(6, 6, -6, -6)
        return QRect(cell.x() + (cell.width() - widget.width()) // 2, cell.y() + 3, widget.width(), widget.height())

    def _occupied(self, exclude: set[int] = frozenset()) -> set[tuple[int, int]]:
        cells = set()
        for item_id, (c, r, w, h) in self.pos_map.items():
            if item_id not in exclude:
                cells |= {(c + i, r + j) for i in range(w) for j in range(h)}
        return cells

    def _fits(self, c: int, r: int, w: int, h: int, occupied: set, check_width: bool = True) -> bool:
        if c < 0 or r < 0 or (check_width and c + w > self.cols()):
            return False
        return all((c + i, r + j) not in occupied for i in range(w) for j in range(h))

    def _free_cell(self, w: int, h: int, occupied: set, near: tuple[int, int] | None = None) -> tuple[int, int]:
        cols = self.cols()
        w = min(w, cols)  # safety: something wider than the desk can never block the search
        if near is not None:  # closest free spot to where it was dropped
            nc, nr = near
            for radius in range(0, 60):
                ring = [(nc + dc, nr + dr) for dc in range(-radius, radius + 1) for dr in range(-radius, radius + 1)
                        if max(abs(dc), abs(dr)) == radius]
                ring.sort(key=lambda cr: (abs(cr[0] - nc) + abs(cr[1] - nr), cr[1], cr[0]))
                for c, r in ring:
                    if self._fits(c, r, w, h, occupied):
                        return c, r
        bottom = max((r for _, r in occupied), default=-1) + 1
        for r in range(0, bottom + 1):  # first free spot, reading order (bounded: never loops forever)
            for c in range(0, max(1, cols - w + 1)):
                if self._fits(c, r, w, h, occupied):
                    return c, r
        return 0, bottom  # below everything is always free

    def _update_size(self):
        rows = max((r + h for c, r, w, h in self.pos_map.values()), default=0) + 2
        cols = max((c + w for c, r, w, h in self.pos_map.values()), default=0)
        self.setMinimumSize(2 * PAD + max(cols, 4) * CELL_W, 2 * PAD + rows * CELL_H)

    # ---------------------------------------------------------------- data
    def load(self, desk: str | None = None, animate: bool = True):
        self._close_editor(commit=False)
        if desk is not None:
            if desk != self.desk:
                self.selection.clear()
            self.desk = desk
        items = self.ctl.db.desk_items(self.desk)
        stored = self.ctl.db.desk_positions(self.desk)
        notes = {}
        for it in items:
            if it["kind"] == "note" and it["target"].isdigit():
                notes[it["id"]] = self.ctl.db.get_note(int(it["target"]))
        # unchanged items keep their widget: an open web page or PDF must not reload on every refresh
        old, self.widgets = self.widgets, {}
        fresh: set[int] = set()
        self.pos_map = {}
        new_positions = {}
        # stored positions first (keeps the user's layout), then place the rest
        ordered = sorted(items, key=lambda it: (it["id"] not in stored, stored.get(it["id"], (0, 0))[1::-1]))
        for it in ordered:
            signature = (tuple(sorted(it.items())), tuple(sorted((notes.get(it["id"]) or {}).items())))
            widget = old.pop(it["id"], None)
            if widget is None or widget.signature != signature:
                if widget is not None:
                    dispose(widget)
                widget = self._create_widget(it, notes.get(it["id"]))
                widget.signature = signature
                fresh.add(it["id"])
            self.widgets[it["id"]] = widget
            default_size = default_cells(it)
            c, r, w, h = stored.get(it["id"], (-1, -1, *default_size))
            if it["kind"] not in RESIZABLE_KINDS:
                w = h = 1
            occupied = self._occupied()
            w, h = max(1, min(w, MAX_NOTE_CELLS)), max(1, min(h, MAX_NOTE_CELLS))
            # a saved spot is kept even if the window is narrower right now (it can scroll)
            if c < 0 or not self._fits(c, r, w, h, occupied, check_width=False):
                c, r = self._free_cell(w, h, occupied, near=(c, r) if c >= 0 else None)
                new_positions[it["id"]] = (c, r, w, h)
            self.pos_map[it["id"]] = (c, r, w, h)
            widget.set_selected(it["id"] in self.selection)
        for widget in old.values():  # removed from this desk
            dispose(widget)
        if new_positions:
            self.ctl.db.set_positions(self.desk, new_positions)
        self.selection &= set(self.widgets)
        self._update_size()
        self._relayout()
        self.set_filter(self.filter_text)
        for i, widget in enumerate(self._reading_order()):
            widget.show()
            if animate or widget.item_id in fresh and old:
                widget.appear(min(i, 30) * 18 if animate else 0)
        self.update()

    def _create_widget(self, item: dict, note: dict | None) -> "DesktopItem":
        if item["kind"] == "note":
            return NoteItem(item, self, note)
        if item["kind"] == "widget":
            return WidgetItem(item, self)
        return IconItem(item, self)

    def refresh_panes(self, what: str) -> None:
        """Live windows (task list…) update their content without being rebuilt."""
        for widget in self.widgets.values():
            if isinstance(widget, WidgetItem):
                widget.pane_refresh(what)

    def _reading_order(self) -> list[DesktopItem]:
        return [self.widgets[i] for i in sorted(self.pos_map, key=lambda i: (self.pos_map[i][1], self.pos_map[i][0]))]

    def _relayout(self):
        for item_id, widget in self.widgets.items():
            widget.setGeometry(self._widget_geometry(item_id))

    def _save(self, ids):
        self.ctl.db.set_positions(self.desk, {i: self.pos_map[i] for i in ids})
        self._update_size()

    def set_filter(self, text: str):
        self.filter_text = text.strip().lower()
        for widget in self.widgets.values():
            haystack = (widget.item["title"] + " " + widget.item["target"]).lower()
            if isinstance(widget, NoteItem):
                haystack += " " + (widget.note.get("content") or "").lower()
            widget.set_dimmed(bool(self.filter_text) and self.filter_text not in haystack)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._mode is None:
            self._relayout()

    # ----------------------------------------------------------- selection
    def _item_at(self, pos: QPoint) -> DesktopItem | None:
        child = self.childAt(pos)
        while child is not None and not isinstance(child, DesktopItem):
            child = child.parentWidget() if child.parentWidget() is not self else None
        return child

    def select(self, ids: set[int]):
        self.selection = set(ids)
        for item_id, widget in self.widgets.items():
            widget.set_selected(item_id in self.selection)
        self.page.update_status()

    # --------------------------------------------------------------- mouse
    def mousePressEvent(self, e):
        self.setFocus()
        self._close_editor()
        pos = e.position().toPoint()
        widget = self._item_at(pos)
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if e.button() == Qt.MouseButton.RightButton:
            if widget and widget.item_id not in self.selection:
                self.select({widget.item_id})
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._press = pos
        if widget is None:
            if not ctrl:
                self.select(set())
            self._mode, self._band = "rubber", QRect(pos, pos)
            self._band_base = set(self.selection)
            return
        if hasattr(widget, "grip_rect") and widget.grip_rect().translated(widget.pos()).contains(pos):
            self._mode, self._resize_id = "resize", widget.item_id
            self.select({widget.item_id})
            return
        if ctrl:
            self.select(self.selection ^ {widget.item_id})
        elif widget.item_id not in self.selection:
            self.select({widget.item_id})
        self._mode = "press"
        self._origins = {i: self.widgets[i].pos() for i in self.selection}

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._mode is None:
            widget = self._item_at(pos)
            grip = hasattr(widget, "grip_rect") and widget.grip_rect().translated(widget.pos()).contains(pos)
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if grip and not is_rtl() else
                           Qt.CursorShape.SizeBDiagCursor if grip else Qt.CursorShape.ArrowCursor)
            return
        if self._mode == "press" and (pos - self._press).manhattanLength() > 6 and self.selection:
            self._mode = "drag"
            for i in self.selection:
                self.widgets[i].raise_()
        if self._mode == "drag":
            delta = pos - self._press
            for i, origin in self._origins.items():
                self.widgets[i].move(origin + delta)
            self._ghosts = [self.cell_rect(*target).adjusted(4, 4, -4, -4)
                            for target in self._drop_targets(delta).values()]
            self.update()
        elif self._mode == "rubber":
            self._band = QRect(self._press, pos).normalized()
            hits = {i for i, w in self.widgets.items() if w.geometry().intersects(self._band)}
            self.select(self._band_base | hits if e.modifiers() & Qt.KeyboardModifier.ControlModifier else hits)
            self.update()
        elif self._mode == "resize":
            c, r, w, h = self.pos_map[self._resize_id]
            cell = self.cell_rect(c, r, 1, 1)
            dx = (cell.right() - pos.x()) if is_rtl() else (pos.x() - cell.left())
            new_w = max(1, min(MAX_NOTE_CELLS, math.ceil(dx / CELL_W)))
            new_h = max(1, min(MAX_NOTE_CELLS, math.ceil((pos.y() - cell.top()) / CELL_H)))
            self._ghosts = [self.cell_rect(c, r, new_w, new_h).adjusted(4, 4, -4, -4)]
            self._pending_size = (new_w, new_h)
            self.update()

    def mouseReleaseEvent(self, e):
        mode, self._mode = self._mode, None
        if mode == "drag":
            targets = self._drop_targets(e.position().toPoint() - self._press)
            self.pos_map.update(targets)
            for i in targets:
                self._animate_to(i)
            self._save(targets.keys())
        elif mode == "resize" and getattr(self, "_pending_size", None):
            i = self._resize_id
            c, r, _, _ = self.pos_map[i]
            w, h = self._pending_size
            if self._fits(c, r, w, h, self._occupied({i})):
                self.pos_map[i] = (c, r, w, h)
                self._animate_to(i, resize=True)
                self._save([i])
            self._pending_size = None
        self._ghosts, self._band = [], None
        self.update()

    def mouseDoubleClickEvent(self, e):
        widget = self._item_at(e.position().toPoint())
        if widget:
            self.ctl.launch_item(widget.item_id)
        elif e.button() == Qt.MouseButton.LeftButton:
            self.new_note_at(self.cell_at(e.position().toPoint()))  # like a fresh sticky on a corkboard

    def _drop_targets(self, delta: QPoint) -> dict[int, tuple[int, int, int, int]]:
        dc = round((-delta.x() if is_rtl() else delta.x()) / CELL_W)
        dr = round(delta.y() / CELL_H)
        moving = set(self._origins)
        occupied = self._occupied(moving)
        targets = {}
        for i in sorted(moving, key=lambda i: (self.pos_map[i][1], self.pos_map[i][0])):
            c, r, w, h = self.pos_map[i]
            nc, nr = c + dc, r + dr
            if not self._fits(nc, nr, w, h, occupied):
                nc, nr = self._free_cell(w, h, occupied, near=(max(0, nc), max(0, nr)))
            targets[i] = (nc, nr, w, h)
            occupied |= {(nc + x, nr + y) for x in range(w) for y in range(h)}
        return targets

    def _animate_to(self, item_id: int, resize: bool = False):
        widget = self.widgets[item_id]
        target = self._widget_geometry(item_id)
        anim = QPropertyAnimation(widget, b"geometry" if resize else b"pos", widget)
        anim.setDuration(260)
        anim.setEasingCurve(OUT)
        anim.setEndValue(target if resize else target.topLeft())
        widget._move_anim = anim
        anim.start()

    # -------------------------------------------------------------- keyboard
    def keyPressEvent(self, e):
        key, mods = e.key(), e.modifiers()
        ctrl = mods & Qt.KeyboardModifier.ControlModifier
        ids = self._ordered_selection()
        if key == Qt.Key.Key_Delete and ids:
            self.ctl.remove_items(ids, self)
        elif key == Qt.Key.Key_F2 and len(ids) == 1:
            self.rename(ids[0])
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and ids:
            for i in ids:
                self.ctl.launch_item(i)
        elif ctrl and key == Qt.Key.Key_A:
            self.select(set(self.widgets))
        elif ctrl and key == Qt.Key.Key_C and ids:
            self.ctl.copy_items(ids)
        elif ctrl and key == Qt.Key.Key_V:
            self.ctl.paste_to_desktop(self.desk, self._paste_cell())
        elif key == Qt.Key.Key_Escape:
            self.select(set())
        else:
            super().keyPressEvent(e)

    def _ordered_selection(self) -> list[int]:
        return sorted(self.selection, key=lambda i: (self.pos_map[i][1], self.pos_map[i][0]))

    def _paste_cell(self) -> tuple[int, int] | None:
        pos = self.mapFromGlobal(self.cursor().pos())
        return self.cell_at(pos) if self.rect().contains(pos) else None

    # ---------------------------------------------------------------- rename
    def rename(self, item_id: int):
        widget = self.widgets.get(item_id)
        if widget is None:
            return
        if isinstance(widget, NoteItem):
            self.ctl.launch_item(item_id)  # notes are renamed in the note editor
            return
        if isinstance(widget, WidgetItem):
            self.ctl.rename_item_dialog(item_id, self)
            return
        self._close_editor()
        editor = QLineEdit(widget.item["title"], self)
        editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        editor.setFont(ui_font(9))
        rect = widget.label_rect().translated(widget.pos())
        editor.setGeometry(rect.adjusted(-8, -2, 8, -6))
        editor.selectAll()
        editor.returnPressed.connect(lambda: self._close_editor(commit=True))
        editor.editingFinished.connect(lambda: self._close_editor(commit=True))
        editor._item_id = item_id
        self._editor = editor
        editor.show()
        editor.setFocus()

    def _close_editor(self, commit: bool = True):
        editor, self._editor = self._editor, None
        if editor is None:
            return
        text = editor.text().strip()
        if commit and text:
            self.ctl.rename_item(editor._item_id, text)
        editor.deleteLater()

    # ----------------------------------------------------------------- menus
    def contextMenuEvent(self, e):
        widget = self._item_at(e.pos())
        if widget is not None:
            self._item_menu(e.globalPos())
        else:
            self._desk_menu(e.globalPos(), self.cell_at(e.pos()))
        e.accept()

    def _item_menu(self, pos):
        ctl = self.ctl
        ids = self._ordered_selection()
        if not ids:
            return
        items = [self.widgets[i].item for i in ids]
        menu = make_menu(self)
        add_action(menu, Icons.CHEVRON, tr("open") if len(ids) == 1 else tr("open_n", n=len(ids)),
                   lambda: [ctl.launch_item(i) for i in ids], "Enter")
        if len(ids) == 1:
            item = items[0]
            if item["kind"] == "app":
                add_action(menu, Icons.ADMIN, tr("run_as_admin"), lambda: ctl.launch_item(item["id"], admin=True))
            if item["kind"] in ("app", "file", "folder"):
                add_action(menu, Icons.OPEN_FOLDER, tr("open_location"), lambda: ctl.reveal_item(item["id"]))
        add_action(menu, Icons.COPY, tr("copy"), lambda: ctl.copy_items(ids), "Ctrl+C")
        if len(ids) == 1 and items[0]["kind"] in ("app", "url", "folder", "file"):
            key = f"item:{items[0]['id']}"
            on_bar = key in ctl.taskbar_conf()["pinned"]
            add_action(menu, Icons.PIN, tr("tb_unpin") if on_bar else tr("tb_pin"), lambda: ctl.set_taskbar_pinned(key, not on_bar))
            on_start = key in (ctl.db.get_setting("start_items", []) or [])
            add_action(menu, Icons.APPS, tr("start_unpin") if on_start else tr("start_pin"),
                       lambda: ctl.set_start_item(key, not on_start))
        if any(i["kind"] in ("url", "file", "note") for i in items):
            add_action(menu, Icons.PHONE, tr("ctx_send_phone"), lambda: ctl.send_items_to_phone(ids))
        menu.addSeparator()
        if len(ids) == 1:
            item = items[0]
            add_action(menu, Icons.EDIT, tr("rename"), lambda: self.rename(item["id"]), "F2")
            if item["kind"] == "note":
                colors = add_submenu(menu, Icons.COLOR, tr("note_color"))
                for color in NOTE_COLORS:
                    act = add_action(colors, None, "●", lambda c=color: ctl.set_item_color(item["id"], c))
                    act.setIcon(_dot_icon(color))
                add_action(menu, Icons.ADD, tr("sticky_bigger"), lambda: ctl.set_sticky_font(item["id"], +1))
                add_action(menu, Icons.MINIMIZE, tr("sticky_smaller"), lambda: ctl.set_sticky_font(item["id"], -1))
            else:
                add_action(menu, Icons.SETTINGS, tr("edit_item"), lambda: ctl.edit_item(item["id"], self))
                if item["kind"] == "url":
                    add_action(menu, Icons.REFRESH, tr("refresh_logo"), lambda: ctl.refresh_logo(item["id"]))
                if item["kind"] == "file" and item["target"].lower().endswith(".pdf"):
                    add_action(menu, Icons.DOCUMENT, tr("pdf_open_in_reader"), lambda: ctl.open_pdf(item["target"]))
                if item["kind"] == "url" or (item["kind"] == "file" and item["target"].lower().endswith(".pdf")):
                    add_action(menu, Icons.PREVIEW, tr("open_as_window"),
                               lambda: ctl.open_as_widget(item["id"], self.desk))
        move = add_submenu(menu, Icons.MOVE, tr("move_to_group"))
        add_action(move, None, tr("no_group"), lambda: [ctl.move_item(i, None) for i in ids])
        for group in ctl.db.list_groups():
            add_action(move, None, group["name"], lambda gid=group["id"]: [ctl.move_item(i, gid) for i in ids])
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("remove_from_desktop") if len(ids) == 1 else tr("remove_n", n=len(ids)),
                   lambda: ctl.remove_items(ids, self), "Del", danger=True)
        menu.exec(pos)

    def _desk_menu(self, pos, cell):
        ctl = self.ctl
        menu = make_menu(self)
        add_action(menu, Icons.ADD, tr("addg_title"), lambda: self.ctl.show_add_gallery(self.desk, parent=self))
        menu.addSeparator()
        new = add_submenu(menu, Icons.ADD, tr("new"))
        add_action(new, Icons.NOTES, tr("sticky_note"), lambda: self.new_note_at(cell))
        add_action(new, Icons.GLOBE, tr("widget_web"), lambda: ctl.new_widget_dialog("web", self.desk, cell, self))
        add_action(new, Icons.DOCUMENT, tr("widget_pdf"), lambda: ctl.new_widget_dialog("pdf", self.desk, cell, self))
        add_action(new, Icons.CHECKLIST, tr("widget_todo"), lambda: ctl.new_widget_dialog("todo", self.desk, cell, self))
        widgets = add_submenu(new, Icons.APPS, tr("widget_more"))
        for wtype in ("system", "focus", "classes", "note", "image", "links"):
            add_action(widgets, WIDGET_TYPES[wtype], tr(f"widget_{wtype}"),
                       lambda t=wtype: ctl.new_widget_dialog(t, self.desk, cell, self))
        new.addSeparator()
        add_action(new, Icons.APPS, tr("kind_app"), lambda: ctl.add_item_dialog("app", group_id=self.group_id(), cell=(self.desk, cell)))
        add_action(new, Icons.GLOBE, tr("kind_url"), lambda: ctl.add_item_dialog("url", group_id=self.group_id(), cell=(self.desk, cell)))
        add_action(new, Icons.FOLDER, tr("kind_folder"), lambda: ctl.add_item_dialog("folder", group_id=self.group_id(), cell=(self.desk, cell)))
        add_action(new, Icons.DOCUMENT, tr("kind_file"), lambda: ctl.add_item_dialog("file", group_id=self.group_id(), cell=(self.desk, cell)))
        new.addSeparator()
        add_action(new, Icons.TAG, tr("new_group"), lambda: ctl.add_group(self))
        paste = add_action(menu, Icons.PASTE, tr("paste"), lambda: ctl.paste_to_desktop(self.desk, cell), "Ctrl+V")
        paste.setEnabled(ctl.clipboard_has_content())
        menu.addSeparator()
        arrange = add_submenu(menu, Icons.SELECT, tr("arrange"))
        add_action(arrange, None, tr("arrange_name"), lambda: self.arrange("name"))
        add_action(arrange, None, tr("arrange_type"), lambda: self.arrange("type"))
        add_action(arrange, None, tr("arrange_compact"), lambda: self.arrange("compact"))
        add_action(menu, Icons.SELECT, tr("select_all"), lambda: self.select(set(self.widgets)), "Ctrl+A")
        menu.exec(pos)

    def group_id(self) -> int | None:
        return None if self.desk == "all" else int(self.desk)

    def new_note_at(self, cell):
        self.ctl.new_sticky_note(self.desk, cell)

    def arrange(self, how: str):
        """Re-flows every item in reading order (notes keep their size)."""
        kinds = {"app": 0, "url": 1, "folder": 2, "file": 3, "note": 4}
        ids = list(self.widgets)
        if how == "name":
            ids.sort(key=lambda i: self.widgets[i].item["title"].lower())
        elif how == "type":
            ids.sort(key=lambda i: (kinds.get(self.widgets[i].item["kind"], 9), self.widgets[i].item["title"].lower()))
        else:
            ids.sort(key=lambda i: (self.pos_map[i][1], self.pos_map[i][0]))
        occupied: set = set()
        for i in ids:
            _, _, w, h = self.pos_map[i]
            c, r = self._free_cell(w, h, occupied)
            self.pos_map[i] = (c, r, w, h)
            occupied |= {(c + x, r + y) for x in range(w) for y in range(h)}
            self._animate_to(i)
        self._save(ids)

    # ----------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() or md.hasText():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        c, r = self.cell_at(e.position().toPoint())
        self._ghosts = [self.cell_rect(c, r).adjusted(4, 4, -4, -4)]
        self.update()
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._ghosts = []
        self.update()

    def dropEvent(self, e):
        self._ghosts = []
        self.ctl.drop_to_desktop(e.mimeData(), self.desk, self.cell_at(e.position().toPoint()))
        e.acceptProposedAction()

    # --------------------------------------------------------------- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._mode in ("drag", "resize") or self._ghosts:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink(0.07))
            rows = self.height() // CELL_H + 1
            for c in range(self.cols() + 1):
                for r in range(rows):
                    corner = self.cell_rect(c, r).topLeft()
                    p.drawEllipse(QRectF(corner.x() - 1.5, corner.y() - 1.5, 3, 3))
        for ghost in self._ghosts:
            p.setPen(QPen(qcolor(Theme.accent(), 0.8), 1.4, Qt.PenStyle.DashLine))
            p.setBrush(qcolor(Theme.accent(), 0.10))
            p.drawRoundedRect(QRectF(ghost), 16, 16)
        if self._band is not None:
            p.setPen(QPen(qcolor(Theme.accent(), 0.9), 1))
            p.setBrush(qcolor(Theme.accent(), 0.12))
            p.drawRoundedRect(QRectF(self._band), 6, 6)
        if not self.widgets:
            p.setPen(QColor(Palette.text_faint))
            p.setFont(ui_font(11))
            p.drawText(QRectF(self.rect()).adjusted(0, 0, 0, -80), Qt.AlignmentFlag.AlignCenter, tr("desktop_empty"))


def _starts_rtl(text: str, default: bool) -> bool:
    """True if the first letter of `text` is Arabic/Hebrew (bullets and digits are skipped)."""
    for ch in text:
        if ch.isalpha():
            return "֐" <= ch <= "ࣿ" or "יִ" <= ch <= "ﻼ"
    return default


def _dot_icon(color: str):
    from app.ui.widgets.files import color_icon
    return color_icon(color, 14)


# ===================================================================== page
class DeskBar(QFrame):
    """Floating bar at the top of the desktop: the desktops, filter, arrange, New. It shows when the mouse goes
    near the top edge (or right after switching desktops) and hides again; Settings > Desktop can turn it off."""

    def __init__(self, page: "DesktopPage"):
        super().__init__(page)
        self.page = page
        self.setObjectName("DeskBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 8, 6)
        lay.addWidget(page.toolbar)
        self._t = 0.0
        self._anim = value_animation(self, 0.0, 1.0, 200, self._set_t, OUT)
        self.hide()
        self._away = 0
        self._poll = QTimer(self, interval=160, timeout=self._watch)
        self._poll.start()
        self._hold = QTimer(self, singleShot=True, interval=1600)

    def enabled(self) -> bool:
        return bool(self.page.ctl.db.get_setting("desk_bar", True))

    def _set_t(self, v):
        self._t = float(v)
        self.setVisible(self._t > 0.01)
        self.place()

    def place(self):
        hint = self.sizeHint()
        w = min(hint.width(), self.page.width() - 40)
        self.resize(w, hint.height())
        self.move((self.page.width() - w) // 2, int(10 - (1 - self._t) * (hint.height() + 14)))

    def show_bar(self, hold: bool = False):
        if not self.enabled():
            return
        if hold:
            self._hold.start()
        if self._t < 1.0:
            self._anim.stop()
            self._anim.setStartValue(self._t)
            self._anim.setEndValue(1.0)
            self._anim.start()
        self.raise_()

    def hide_bar(self):
        if self._t <= 0:
            return
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _watch(self):
        page = self.page
        if not page.isVisible() or not page.window().isActiveWindow():
            if self._t > 0 and not self._hold.isActive():
                self.hide_bar()
            return
        pos = page.mapFromGlobal(QCursor.pos())
        inside = 0 <= pos.x() <= page.width() and 0 <= pos.y() <= page.height()
        near_top = inside and pos.y() < 34
        over_bar = self.isVisible() and self.geometry().adjusted(-20, -10, 20, 26).contains(pos)
        busy = page.search.hasFocus() or QApplication.activePopupWidget() is not None
        if near_top or over_bar or busy or self._hold.isActive():
            self._away = 0
            if near_top:
                self.show_bar()
        elif self._t > 0:
            self._away += 1
            if self._away > 4:
                self.hide_bar()


class DesktopPage(Page):
    def __init__(self, ctl, main_window):
        super().__init__(ctl, main_window)
        self._shown_once = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)  # the desktop is the whole screen, like the Windows one
        root.setSpacing(0)

        # desktops, filter, arrange, New: in the floating DeskBar (and the taskbar's desktops button)
        self.toolbar = QWidget()
        top = QHBoxLayout(self.toolbar)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(label(tr("desks_title"), 11, QFont.Weight.DemiBold))
        top.addSpacing(6)
        self.desks = QHBoxLayout()
        self.desks.setSpacing(6)
        top.addLayout(self.desks)
        top.addSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("desktop_filter"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(180)
        self.search.textChanged.connect(lambda t: self.canvas.set_filter(t))
        top.addWidget(self.search)
        arrange = IconButton(Icons.SELECT, tr("arrange"), 34, 11)
        arrange.clicked.connect(lambda: self._arrange_menu(arrange))
        top.addWidget(arrange)
        new = primary_button(tr("new"), Icons.ADD)
        new.clicked.connect(lambda: self.canvas._desk_menu(new.mapToGlobal(new.rect().bottomLeft()), None))
        top.addWidget(new)

        self.canvas = DesktopCanvas(ctl, self)
        self.scroll = SmoothScrollArea()
        self.scroll.setWidget(self.canvas)
        root.addWidget(self.scroll, 1)
        self.status = label("", 8.8, role="Faint")  # shown as the desk bar's tooltip
        self.status.hide()
        self.deskbar = DeskBar(self)

        self.listen(ctl.data_changed, self.refresh)
        self._fill_desks()
        self.canvas.load("all", animate=False)
        self.update_status()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.deskbar.isVisible():
            self.deskbar.place()

    # ------------------------------------------------------------ desktops
    def desk_keys(self) -> list[tuple[str, str]]:
        return [("all", tr("desk_main"))] + [(str(g["id"]), g["name"]) for g in self.ctl.db.list_groups()]

    def next_desk(self, delta: int) -> None:
        keys = [k for k, _ in self.desk_keys()]
        i = keys.index(self.canvas.desk) if self.canvas.desk in keys else 0
        self.show_desk(keys[(i + delta) % len(keys)])

    def desk_number(self, n: int) -> None:
        keys = [k for k, _ in self.desk_keys()]
        if 0 <= n < len(keys):
            self.show_desk(keys[n])

    def _fill_desks(self):
        while self.desks.count():
            w = self.desks.takeAt(0).widget()
            if w:
                w.hide()  # at once: until deleteLater runs it would sit on top of the new chips
                w.deleteLater()
        groups = self.ctl.db.list_groups()
        if self.canvas.desk != "all" and int(self.canvas.desk) not in {g["id"] for g in groups}:
            self.canvas.desk = "all"
        for key, name in [("all", tr("desk_main"))] + [(str(g["id"]), g["name"]) for g in groups]:
            chip = QPushButton(name)
            chip.setObjectName("Chip")
            chip.setCheckable(True)
            chip.setChecked(key == self.canvas.desk)
            chip.setFont(ui_font(9.5))
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _=False, k=key: self.show_desk(k))
            chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            chip.customContextMenuRequested.connect(
                lambda pos, k=key, c=chip: self._group_menu(k, c.mapToGlobal(pos)))
            self.desks.addWidget(chip)
        plus = QPushButton("+")
        plus.setObjectName("Chip")
        plus.setToolTip(tr("new_group"))
        plus.setCursor(Qt.CursorShape.PointingHandCursor)
        plus.clicked.connect(lambda: self.ctl.add_group(self))
        self.desks.addWidget(plus)

    def show_desk(self, key: str):
        if key == self.canvas.desk:
            self.deskbar.show_bar(hold=True)
            return
        self.canvas.load(key, animate=True)
        self._fill_desks()
        self.update_status()
        self.scroll.verticalScrollBar().setValue(0)
        if hasattr(self.main, "desk_changed"):
            self.main.desk_changed(key)  # its own wallpaper / taskbar apps
        self.deskbar.show_bar(hold=True)  # say which desktop this is
        self.main.toast(tr("desk_switched", name=dict(self.desk_keys()).get(key, "")), "info")

    def _group_menu(self, key: str, pos):
        menu = make_menu(self)
        add_action(menu, Icons.COLOR, tr("desk_look_menu"), lambda: self.ctl.edit_desk_look(key, self))
        if key != "all":
            group_id = int(key)
            menu.addSeparator()
            add_action(menu, Icons.EDIT, tr("rename"), lambda: self.ctl.rename_group(group_id, self))
            add_action(menu, Icons.DELETE, tr("delete_group"), lambda: self.ctl.delete_group(group_id, self), danger=True)
        menu.exec(pos)

    def _arrange_menu(self, anchor):
        menu = make_menu(self)
        add_action(menu, None, tr("arrange_name"), lambda: self.canvas.arrange("name"))
        add_action(menu, None, tr("arrange_type"), lambda: self.canvas.arrange("type"))
        add_action(menu, None, tr("arrange_compact"), lambda: self.canvas.arrange("compact"))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def update_status(self):
        n, sel = len(self.canvas.widgets), len(self.canvas.selection)
        text = tr("desktop_count", n=n) + (f"  ·  {tr('selected_n', n=sel)}" if sel else "")
        self.status.setText(text + "  ·  " + tr("desktop_hint"))
        self.toolbar.setToolTip(self.status.text())

    def refresh(self, what: str = ""):
        if what in ("", "tasks"):
            self.canvas.refresh_panes(what)
        if what in ("", "launcher", "notes", "logos"):
            if what in ("", "launcher"):
                self._fill_desks()
            self.canvas.load(animate=False)
            self.update_status()

    def on_shown(self):
        if not self._shown_once:
            self._shown_once = True
            self.canvas.load(animate=True)
        self.canvas.setFocus()
