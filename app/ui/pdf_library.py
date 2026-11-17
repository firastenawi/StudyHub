"""
PDF Library: everything across all your PDFs in one window.

  Search       the words you type, in every PDF Study Hub knows (opened files, subjects' folders, your folders);
               each result shows the file, the page and the sentence — click to open it there
  Highlights   all highlights and their notes from every file, by colour, searchable; send the chosen ones to a note
  Bookmarks    every named bookmark (Ctrl+D in the reader), click to jump
"""
from __future__ import annotations

import html
import json
import os
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                             QStackedWidget, QVBoxLayout, QWidget)

from app.core.i18n import tr
from app.ui import apps as app_registry
from app.ui.theme import Icons, Palette, Theme, glyph_icon, ui_font
from app.ui.widgets.background import AuroraBackground
from app.ui.widgets.common import IconButton, button, label, primary_button
from app.ui.widgets.segmented import SegmentedTabs

ROLE = Qt.ItemDataRole.UserRole


def bookmarks(ctl) -> list[dict]:
    """[{key, path, page, name}] from every file."""
    out = []
    for key, items in (ctl.db.get_setting("pdf_bookmarks", {}) or {}).items():
        for b in items:
            out.append({"key": key, **b})
    return out


def short_folder(path: str) -> str:
    """The last two folders instead of the whole path (the full one is in the tooltip)."""
    parts = Path(path).parent.parts
    return str(Path(*parts[-2:])) if len(parts) > 2 else str(Path(path).parent)


class ResultRow(QLabel):
    def __init__(self, title: str, sub: str, body_html: str, tip: str = ""):
        super().__init__()
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setContentsMargins(10, 6, 10, 6)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        align = "right" if self.isRightToLeft() else "left"  # one side for every line, whatever the language
        self.setText(f"<div align='{align}' style='font-weight:600'>{html.escape(title)}</div>"
                     f"<div align='{align}' style='color:{Palette.text_dim};font-size:8.5pt'>{html.escape(sub)}</div>"
                     + (f"<div align='{align}' style='margin-top:3px'>{body_html}</div>" if body_html else ""))
        if tip:
            self.setToolTip(tip)


class PdfLibrary(QMainWindow):
    def __init__(self, ctl, tab: str = "search", query: str = ""):
        super().__init__()
        self.ctl = ctl
        self.info = app_registry.find(ctl, "pdf")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{tr('pdfl_title')} — Study Hub")
        self.setWindowIcon(self.info.icon())
        self.resize(980, 720)
        bg = AuroraBackground(animated=False, wallpaper=ctl.wallpaper())
        root = QVBoxLayout(bg)
        root.setContentsMargins(22, 16, 22, 18)
        root.setSpacing(12)
        head = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(tr("pdfl_title"), 19, QFont.Weight.DemiBold))
        self.status = label("", 9, role="Dim")
        col.addWidget(self.status)
        head.addLayout(col, 1)
        self.tabs = SegmentedTabs([("search", Icons.SEARCH, tr("pdfl_search")), ("highlights", Icons.COLOR, tr("pdfl_highlights")),
                                   ("bookmarks", Icons.FLAG, tr("pdfl_bookmarks"))], tab)
        self.tabs.changed.connect(self.show_tab)
        head.addWidget(self.tabs)
        root.addLayout(head)
        row = QHBoxLayout()
        self.query = QLineEdit(query)
        self.query.setPlaceholderText(tr("pdfl_query_ph"))
        self.query.setClearButtonEnabled(True)
        self.query.setFixedHeight(40)
        self.query.textChanged.connect(lambda _: self._debounce.start())
        row.addWidget(self.query, 1)
        self.color = QComboBox()
        self.color.addItem(tr("pdfl_all_colors"), "")
        from app.ui.pdf_reader import HL_COLORS
        for c in HL_COLORS:
            self.color.addItem(glyph_icon("●", c, 16), "", c)
        self.color.currentIndexChanged.connect(lambda _: self._refresh())
        row.addWidget(self.color)
        self.reindex = button(tr("pdfl_reindex"), Icons.REFRESH, role="Ghost")
        self.reindex.clicked.connect(self._reindex)
        row.addWidget(self.reindex)
        self.to_note = button(tr("pdf_highlights_to_note"), Icons.NOTES, role="Ghost")
        self.to_note.clicked.connect(self._highlights_to_note)
        row.addWidget(self.to_note)
        root.addLayout(row)
        self.list = QListWidget()
        self.list.setObjectName("NotifList")
        self.list.setWordWrap(True)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.itemActivated.connect(self._open)
        self.list.itemDoubleClicked.connect(self._open)
        self.list.itemClicked.connect(self._open_single)
        root.addWidget(self.list, 1)
        self.hint = label("", 9, role="Faint", wrap=True)
        root.addWidget(self.hint)
        self.setCentralWidget(bg)
        self._debounce = QTimer(self, singleShot=True, interval=250, timeout=self._refresh)
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self.close)
        ctl.pdf_index.finished.connect(self._indexed)
        ctl.pdf_index.progress.connect(self._progress)
        self.current = tab
        self.show_tab(tab)
        if not ctl.pdf_index.busy:
            ctl.pdf_index.refresh()  # pick up new / changed files in the background

    # ---------------------------------------------------------------- tabs
    def show_tab(self, key: str) -> None:
        self.current = key
        self.tabs.set_current(key)
        self.color.setVisible(key == "highlights")
        self.to_note.setVisible(key == "highlights")
        self.reindex.setVisible(key == "search")
        self.query.setPlaceholderText(tr({"search": "pdfl_query_ph", "highlights": "pdfl_hl_ph", "bookmarks": "pdfl_bm_ph"}[key]))
        self._refresh()
        self.query.setFocus()

    def _status_text(self) -> None:
        files, pages = self.ctl.pdf_index.stats()
        busy = tr("pdfl_indexing") if self.ctl.pdf_index.busy else ""
        self.status.setText(tr("pdfl_stats", files=files, pages=pages) + (f"  ·  {busy}" if busy else ""))

    def _progress(self, done: int, total: int) -> None:
        try:
            self.status.setText(tr("pdfl_indexing_n", done=done, total=total))
        except RuntimeError:
            pass

    def _indexed(self, n: int) -> None:
        try:
            self._status_text()
            if self.current == "search" and self.query.text().strip():
                self._refresh()
        except RuntimeError:
            pass

    def _reindex(self) -> None:
        self.ctl.pdf_index.refresh()
        self._status_text()

    # ---------------------------------------------------------------- content
    def _refresh(self) -> None:
        self._status_text()
        self.list.clear()
        q = self.query.text().strip()
        if self.current == "search":
            self.hint.setText(tr("pdfl_search_hint"))
            if not q:
                return
            results = self.ctl.pdf_index.search(q)
            for r in results:
                snippet = html.escape(r["snippet"]).replace("[[", f"<b style='color:{Theme.accent()}'>").replace("]]", "</b>")
                self._row(Path(r["path"]).name, tr("pdfl_page", n=r["page"] + 1) + "  ·  " + short_folder(r["path"]), snippet,
                          {"path": r["path"], "page": r["page"], "query": q})
            if not results:
                self.list.addItem(tr("pdfl_none"))
        elif self.current == "highlights":
            self.hint.setText(tr("pdfl_hl_hint"))
            rows = self.ctl.db.query("SELECT * FROM pdf_highlights ORDER BY path, page, id")
            want = self.color.currentData()
            for h in rows:
                if want and h["color"] != want:
                    continue
                if q and q.lower() not in (h["text"] + " " + h["note"] + " " + h["path"]).lower():
                    continue
                body = f"<span style='background:{h['color']}55'>{html.escape(h['text'][:400])}</span>"
                if h["note"]:
                    body += f"<div style='color:{Palette.text_dim}'>✎ {html.escape(h['note'][:300])}</div>"
                self._row(Path(h["path"]).name, tr("pdfl_page", n=h["page"] + 1), body, {"path": h["path"], "page": h["page"], "hl": h})
            if not self.list.count():
                self.list.addItem(tr("pdfl_none"))
        else:
            self.hint.setText(tr("pdfl_bm_hint"))
            for b in bookmarks(self.ctl):
                if q and q.lower() not in (b["name"] + " " + b["path"]).lower():
                    continue
                self._row(b["name"], f"{Path(b['path']).name}  ·  " + tr("pdfl_page", n=b["page"] + 1), "",
                          {"path": b["path"], "page": b["page"]})
            if not self.list.count():
                self.list.addItem(tr("pdfl_none"))

    def _row(self, title: str, sub: str, body: str, data: dict) -> None:
        item = QListWidgetItem()
        item.setData(ROLE, data)
        w = ResultRow(title, sub, body, data.get("path", ""))
        width = max(300, self.list.viewport().width() - 24)
        item.setSizeHint(QSize(0, w.heightForWidth(width) + 22))
        self.list.addItem(item)
        self.list.setItemWidget(item, w)

    def _open_single(self, item) -> None:
        if len(self.list.selectedItems()) > 1 or self.current == "highlights":  # highlights: click selects, double-click opens
            return
        self._open(item)

    def _open(self, item) -> None:
        data = item.data(ROLE)
        if not data or not os.path.exists(data["path"]):
            return
        reader = self.ctl.open_pdf(data["path"], data["page"])
        if reader is not None and data.get("query"):
            reader.search.setText(data["query"])  # the words show up highlighted on the page

    def _highlights_to_note(self) -> None:
        items = [i.data(ROLE) for i in self.list.selectedItems() if i.data(ROLE) and i.data(ROLE).get("hl")]
        if not items:
            items = [self.list.item(i).data(ROLE) for i in range(self.list.count())
                     if self.list.item(i).data(ROLE) and self.list.item(i).data(ROLE).get("hl")]
        if not items:
            return
        from app.ui.pdf_reader import pdf_link
        lines, last = [], None
        for d in items:
            h = d["hl"]
            if h["path"] != last:
                lines += ["", f"## {Path(h['path']).stem}", ""]
                last = h["path"]
            lines.append(f"> {h['text'].strip()}  ")
            lines.append(f"> — [{tr('pdfl_page', n=h['page'] + 1)}]({pdf_link(h['path'], h['page'])})")
            if h["note"]:
                lines.append(f"\n{h['note'].strip()}")
            lines.append("")
        note_id = self.ctl.save_note("\n".join(lines).strip(), tr("pdfl_hl_note_title"), source="pdf")
        self.ctl.open_notebook(note_id)

    def showEvent(self, e):
        super().showEvent(e)
        from app.ui.app_window import give_identity
        give_identity(self, self.info)

    def closeEvent(self, e):
        for sig, slot in ((self.ctl.pdf_index.finished, self._indexed), (self.ctl.pdf_index.progress, self._progress)):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        super().closeEvent(e)
