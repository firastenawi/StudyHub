"""
Code snippets: your library of commands and code you reuse.

  Snippets app       list + editor (title, language, code), import / export (Study Hub JSON or VS Code
                     *.code-snippets files)
  quick paste        Ctrl+Alt+J (Settings › Keyboard) over ANY program: type to find, Enter pastes it into the
                     program you were in — also from the command palette and the terminal's toolbar
Stored in the settings table as a small JSON list (no schema change).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer
from PyQt6.QtGui import QCursor, QFont, QGuiApplication
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                             QPlainTextEdit, QSplitter, QVBoxLayout, QWidget)

from app.core import winapi
from app.core.i18n import tr
from app.ui.dialogs.base import drop_shadow
from app.ui.pages.base import Page
from app.ui.theme import Icons, Palette, Theme, ui_font
from app.ui.widgets.common import GlyphLabel, IconButton, button, label, primary_button

LANGS = ["text", "bash", "powershell", "cmd", "python", "javascript", "html", "css", "sql", "json", "yaml",
         "c", "cpp", "java", "csharp", "go", "nginx", "dockerfile", "cisco"]


# ===================================================================== storage
class SnippetStore:
    def __init__(self, ctl):
        self.ctl = ctl

    def all(self) -> list[dict]:
        return list(self.ctl.db.get_setting("snippets", []) or [])

    def _save(self, items: list[dict]) -> None:
        self.ctl.db.set_setting("snippets", items)
        self.ctl.data_changed.emit("snippets")

    def get(self, sid: int) -> dict | None:
        return next((s for s in self.all() if s["id"] == sid), None)

    def add(self, title: str, code: str, lang: str = "text") -> int:
        items = self.all()
        sid = max([s["id"] for s in items] + [0]) + 1
        items.append({"id": sid, "title": title or tr("snip_untitled"), "code": code, "lang": lang, "used": 0,
                      "updated": time.time()})
        self._save(items)
        return sid

    def update(self, sid: int, **fields) -> None:
        items = self.all()
        for s in items:
            if s["id"] == sid:
                s.update(fields)
                s["updated"] = time.time()
        self._save(items)

    def delete(self, sid: int) -> None:
        self._save([s for s in self.all() if s["id"] != sid])

    def used(self, sid: int) -> None:
        items = self.all()
        for s in items:
            if s["id"] == sid:
                s["used"] = s.get("used", 0) + 1
        self.ctl.db.set_setting("snippets", items)  # no refresh signal: just the order next time

    def search(self, text: str) -> list[dict]:
        q = text.strip().lower()
        items = sorted(self.all(), key=lambda s: (-s.get("used", 0), s["title"].lower()))
        if not q:
            return items
        return [s for s in items if q in s["title"].lower() or q in s["code"].lower() or q == s.get("lang", "")]

    # ------------------------------------------------------------- files
    def export_to(self, path: str) -> int:
        items = self.all()
        if path.lower().endswith(".code-snippets"):  # VS Code format
            data = {s["title"]: {"prefix": s["title"].lower().replace(" ", "-"), "body": s["code"].split("\n"),
                                 "description": s.get("lang", "")} for s in items}
        else:
            data = {"studyhub_snippets": 1, "items": [{k: s[k] for k in ("title", "code", "lang")} for s in items]}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(items)

    def import_from(self, path: str) -> int:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        found = []
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            found = [(i.get("title", ""), i.get("code", ""), i.get("lang", "text")) for i in data["items"]]
        elif isinstance(data, dict):  # VS Code: {name: {prefix, body, description, scope}}
            for name, v in data.items():
                if isinstance(v, dict) and "body" in v:
                    body = v["body"]
                    code = "\n".join(body) if isinstance(body, list) else str(body)
                    desc = str(v.get("description") or "")
                    lang = (v.get("scope") or (desc if desc in LANGS else "") or "text").split(",")[0].strip() or "text"
                    found.append((name, code, lang))
        have = {(s["title"], s["code"]) for s in self.all()}
        items = self.all()
        sid = max([s["id"] for s in items] + [0])
        added = 0
        for title, code, lang in found:
            if code and (title, code) not in have:
                sid += 1
                added += 1
                items.append({"id": sid, "title": title or tr("snip_untitled"), "code": code, "lang": lang or "text",
                              "used": 0, "updated": time.time()})
        self._save(items)
        return added


def _row_text(s: dict) -> str:
    first = s["code"].strip().split("\n")[0][:70]
    return f"{s['title']}\n{s.get('lang', 'text')} · {first}"


# ===================================================================== quick paste popup
class SnippetPopup(QFrame):
    """Over any program: find a snippet, Enter pastes it there (Ctrl+Enter only copies)."""

    def __init__(self, ctl):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.ctl = ctl
        self.target_hwnd = 0
        self.on_pick = None  # the terminal pastes into itself instead
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        card = QFrame()
        card.setObjectName("DialogFrame")
        drop_shadow(card)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 12, 12, 10)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(GlyphLabel(Icons.KEYBOARD, 12, Theme.accent()))
        head.addWidget(label(tr("snip_title"), 11, QFont.Weight.DemiBold), 1)
        manage = IconButton(Icons.EDIT, tr("snip_manage"), 28, 9)
        manage.clicked.connect(lambda: (self.hide(), ctl.apps.open("snippets")))
        head.addWidget(manage)
        lay.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("snip_search"))
        self.search.textChanged.connect(lambda _: self.fill())
        self.search.installEventFilter(self)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _: self.paste())
        self.list.itemDoubleClicked.connect(lambda _: self.paste())
        lay.addWidget(self.list, 1)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("Cascadia Mono", 9))
        self.preview.setFixedHeight(110)
        lay.addWidget(self.preview)
        self.list.currentRowChanged.connect(self._show_preview)
        lay.addWidget(label(tr("snip_hint"), 8.2, role="Faint"))
        self.resize(560, 560)

    def open(self, on_pick=None) -> None:
        self.on_pick = on_pick
        self.target_hwnd = winapi.foreground_hwnd() if on_pick is None else 0
        self.search.clear()
        self.fill()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        pos = QCursor.pos() - QPoint(self.width() // 2, 60)
        pos.setX(max(area.left(), min(pos.x(), area.right() - self.width())))
        pos.setY(max(area.top(), min(pos.y(), area.bottom() - self.height())))
        self.move(pos)
        self.show()
        self.raise_()
        self.activateWindow()
        winapi.force_foreground(int(self.winId()))
        self.search.setFocus()

    def fill(self) -> None:
        self.list.clear()
        for s in self.ctl.snippets.search(self.search.text()):
            item = QListWidgetItem(_row_text(s))
            item.setData(Qt.ItemDataRole.UserRole, s["id"])
            item.setSizeHint(QSize(0, 50))  # two lines: name + language · first line of code
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.preview.setPlainText(tr("snip_empty"))

    def _show_preview(self, row: int) -> None:
        item = self.list.item(row)
        s = self.ctl.snippets.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        self.preview.setPlainText(s["code"] if s else "")

    def _current(self) -> dict | None:
        item = self.list.currentItem()
        return self.ctl.snippets.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def paste(self, copy_only: bool = False) -> None:
        s = self._current()
        if not s:
            return
        self.ctl.snippets.used(s["id"])
        self.hide()
        if self.on_pick is not None:
            self.on_pick(s["code"])
            return
        QGuiApplication.clipboard().setText(s["code"])
        if copy_only or not self.target_hwnd:
            self.ctl.notify(tr("copied"), "success")
            return
        winapi.force_foreground(self.target_hwnd)
        QTimer.singleShot(120, winapi.send_ctrl_v)

    def eventFilter(self, obj, e):
        if obj is self.search and e.type() == e.Type.KeyPress:
            key = e.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.list.count():
                step = 1 if key == Qt.Key.Key_Down else -1
                self.list.setCurrentRow((self.list.currentRow() + step) % self.list.count())
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.paste(copy_only=bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier))
                return True
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, e)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            QTimer.singleShot(150, lambda: (not self.isActiveWindow()) and self.hide())


# ===================================================================== the app
class SnippetsPage(Page):
    def __init__(self, ctl, main_window):
        super().__init__(ctl, main_window)
        self.store = ctl.snippets
        self.current: int | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 18)
        root.setSpacing(12)
        head = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(tr("snip_title"), 20, QFont.Weight.DemiBold))
        col.addWidget(label(tr("snip_subtitle", shortcut=ctl.hotkey_text("snippets")), 9.5, role="Dim"))
        head.addLayout(col, 1)
        for glyph, text, slot in ((Icons.DOWNLOAD, tr("snip_import"), self._import), (Icons.SAVE, tr("snip_export"), self._export)):
            b = button(text, glyph, role="Ghost")
            b.clicked.connect(slot)
            head.addWidget(b)
        new = primary_button(tr("snip_new"), Icons.ADD)
        new.clicked.connect(self._new)
        head.addWidget(new)
        root.addLayout(head)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("snip_search"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self._fill())
        ll.addWidget(self.search)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda cur, _prev: self._open(cur.data(Qt.ItemDataRole.UserRole) if cur else None))
        ll.addWidget(self.list, 1)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        row = QHBoxLayout()
        self.title = QLineEdit()
        self.title.setPlaceholderText(tr("snip_name"))
        row.addWidget(self.title, 1)
        self.lang = QComboBox()
        self.lang.addItems(LANGS)
        self.lang.setEditable(True)
        self.lang.setFixedWidth(150)
        row.addWidget(self.lang)
        rl.addLayout(row)
        self.code = QPlainTextEdit()
        self.code.setFont(QFont("Cascadia Mono", 10))
        self.code.setPlaceholderText(tr("snip_code_ph"))
        self.code.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        rl.addWidget(self.code, 1)
        row = QHBoxLayout()
        delete = button(tr("delete"), Icons.DELETE, role="Ghost")
        delete.clicked.connect(self._delete)
        row.addWidget(delete)
        copy = button(tr("copy"), Icons.COPY, role="Ghost")
        copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(self.code.toPlainText()),
                                      ctl.notify(tr("copied"), "success")))
        row.addWidget(copy)
        term = button(tr("snip_to_terminal"), Icons.KEYBOARD, role="Ghost")
        term.clicked.connect(lambda: ctl.open_terminal(paste=self.code.toPlainText()))
        row.addWidget(term)
        row.addStretch(1)
        save = primary_button(tr("save"), Icons.ACCEPT)
        save.clicked.connect(self._save)
        row.addWidget(save)
        rl.addLayout(row)
        split.addWidget(right)
        split.setSizes([320, 680])
        root.addWidget(split, 1)
        self.listen(ctl.data_changed, lambda what: what == "snippets" and self._fill())
        self._fill()

    def _fill(self) -> None:
        keep = self.current
        self.list.blockSignals(True)
        self.list.clear()
        for s in self.store.search(self.search.text()):
            item = QListWidgetItem(_row_text(s))
            item.setData(Qt.ItemDataRole.UserRole, s["id"])
            item.setSizeHint(QSize(0, 50))
            self.list.addItem(item)
            if s["id"] == keep:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if keep is None and self.list.count():
            self.list.setCurrentRow(0)
            self._open(self.list.item(0).data(Qt.ItemDataRole.UserRole))

    def _open(self, sid) -> None:
        s = self.store.get(sid) if sid else None
        self.current = s["id"] if s else None
        self.title.setText(s["title"] if s else "")
        self.lang.setCurrentText(s.get("lang", "text") if s else "text")
        self.code.setPlainText(s["code"] if s else "")

    def _new(self) -> None:
        self.current = self.store.add(tr("snip_untitled"), "", "text")
        self._fill()
        self._open(self.current)
        self.title.selectAll()
        self.title.setFocus()

    def _save(self) -> None:
        if self.current is None:
            self.current = self.store.add(self.title.text().strip(), self.code.toPlainText(), self.lang.currentText())
        else:
            self.store.update(self.current, title=self.title.text().strip() or tr("snip_untitled"),
                              code=self.code.toPlainText(), lang=self.lang.currentText() or "text")
        self.ctl.notify(tr("saved"), "success")

    def _delete(self) -> None:
        if self.current is not None:
            self.store.delete(self.current)
            self.current = None
            self._fill()

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("snip_import"), "", "Snippets (*.json *.code-snippets)")
        if path:
            try:
                n = self.store.import_from(path)
                self.ctl.notify(tr("snip_imported", n=n), "success")
            except (OSError, ValueError) as exc:
                self.ctl.notify(f"{tr('error')}: {exc}", "error")

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("snip_export"), "studyhub-snippets.json",
                                              "Study Hub (*.json);;VS Code (*.code-snippets)")
        if path:
            n = self.store.export_to(path)
            self.ctl.notify(tr("snip_exported", n=n), "success")
