"""
The notebook's writing tools:

  NoteEditor   Markdown editor: "/" command menu, smart paste (images, files, links with their page
               title), Ctrl+Enter toggles a task, readable line width, find highlights
  SlashMenu    the "/" popup (filter as you type, ↑ ↓ Enter / Tab, Esc)
  LinkMenu     the "[[" popup: pick a note title to link
  FindBar      find & replace with match count, case toggle, replace one / all
  OutlinePanel headings of the note; follows the cursor, click to jump
"""
from __future__ import annotations

import re
import shutil
import threading
import urllib.request
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCursor, QTextDocument, QTextFormat
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                             QTextEdit, QVBoxLayout, QWidget)

from app.config import DATA_DIR
from app.core.i18n import ltr, tr
from app.core.md_render import outline
from app.ui.theme import Icons, Palette, Theme, ui_font, ink, ink_css
from app.ui.widgets.common import GlyphLabel, IconButton, label
from app.ui.widgets.markdown import MarkdownHighlighter, auto_direction, continue_list, wrap_selection

ATTACH_ROOT = DATA_DIR / "attachments"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
CURSOR = "§"  # where the caret lands after a snippet

# key, glyph, label key, extra search words, snippet ("§" = caret) or None for actions handled by the page
SLASH_COMMANDS: list[tuple[str, str, str, str, str | None]] = [
    ("h1", "H1", "slash_h1", "heading title عنوان", "# §"),
    ("h2", "H2", "slash_h2", "heading عنوان", "## §"),
    ("h3", "H3", "slash_h3", "heading عنوان", "### §"),
    ("todo", Icons.CHECKLIST, "slash_todo", "task checkbox مهمة مهام", "- [ ] §"),
    ("bullet", Icons.MENU, "slash_bullet", "list قائمة", "- §"),
    ("numbered", "1.", "slash_numbered", "list ordered مرقمة", "1. §"),
    ("quote", Icons.EDIT, "slash_quote", "blockquote اقتباس", "> §"),
    ("code", Icons.DOCUMENT, "slash_code", "code block كود", "```\n§\n```"),
    ("python", Icons.DOCUMENT, "slash_code_python", "code py كود", "```python\n§\n```"),
    ("csharp", Icons.DOCUMENT, "slash_code_csharp", "code c# cs dotnet كود", "```csharp\n§\n```"),
    ("sql", Icons.DOCUMENT, "slash_code_sql", "code database كود", "```sql\n§\n```"),
    ("bash", Icons.DOCUMENT, "slash_code_bash", "code shell linux terminal كود", "```bash\n§\n```"),
    ("js", Icons.DOCUMENT, "slash_code_js", "code javascript typescript كود", "```javascript\n§\n```"),
    ("json", Icons.DOCUMENT, "slash_code_json", "code yaml config كود", "```json\n§\n```"),
    ("table", Icons.VIEW_GRID, "slash_table", "grid جدول", "| § | |\n|---|---|\n|  |  |"),
    ("note", Icons.INFO, "slash_callout_note", "callout info ملاحظة تنبيه", "> [!NOTE]\n> §"),
    ("tip", Icons.LIGHTBULB, "slash_callout_tip", "callout hint نصيحة", "> [!TIP]\n> §"),
    ("important", Icons.STAR, "slash_callout_important", "callout مهم", "> [!IMPORTANT]\n> §"),
    ("warning", Icons.WARNING, "slash_callout_warning", "callout تحذير", "> [!WARNING]\n> §"),
    ("caution", Icons.WARNING, "slash_callout_caution", "callout danger خطر", "> [!CAUTION]\n> §"),
    ("math", "∑", "slash_math", "latex equation معادلة رياضيات", "$$\n§\n$$"),
    ("inline_math", "x²", "slash_inline_math", "latex equation معادلة", "$§$"),
    ("flowchart", Icons.SYNC, "slash_flowchart", "mermaid diagram graph مخطط", "```mermaid\nflowchart TD\n    A[§Start] --> B{Decision}\n    B -->|Yes| C[OK]\n    B -->|No| D[Retry]\n```"),
    ("sequence", Icons.SYNC, "slash_sequence", "mermaid diagram مخطط تسلسل", "```mermaid\nsequenceDiagram\n    Client->>Server: §Request\n    Server-->>Client: Response\n```"),
    ("divider", "—", "slash_divider", "line hr فاصل", "---\n§"),
    ("link", Icons.LINK, "slash_link", "url رابط", "[§](https://)"),
    ("date", Icons.CALENDAR_DAY, "slash_date", "today تاريخ اليوم", "{date}§"),
    ("time", Icons.CLOCK, "slash_time", "now وقت ساعة", "{time}§"),
    ("image", Icons.PREVIEW, "slash_image", "picture photo صورة", None),
    ("file", Icons.ATTACH, "slash_file", "attachment مرفق ملف", None),
]


def _rgba(color: str, alpha: int) -> str:
    c = QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def attachment_dir(note_id: int) -> Path:
    return ATTACH_ROOT / str(note_id)


def _unique(folder: Path, name: str) -> Path:
    path, n = folder / name, 2
    while path.exists():
        path = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return path


def _md_path(path: Path) -> str:
    """Path relative to the data folder (the preview's base URL), wrapped when it has spaces."""
    try:
        rel = path.relative_to(DATA_DIR).as_posix()
    except ValueError:
        rel = path.as_uri()
    return f"<{rel}>" if " " in rel else rel


class _TitleFetcher(QObject):
    """Fetches a web page's <title> in a thread (for pasted links)."""
    done = pyqtSignal(str, str)  # url, title

    def fetch(self, url: str) -> None:
        threading.Thread(target=self._run, args=(url,), daemon=True).start()

    def _run(self, url: str) -> None:
        title = ""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 StudyHub"})
            with urllib.request.urlopen(req, timeout=5) as r:
                if "html" in (r.headers.get("Content-Type") or ""):
                    raw = r.read(256 * 1024).decode(r.headers.get_content_charset() or "utf-8", "replace")
                    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
                    if m:
                        title = " ".join(unescape(m.group(1)).split())[:120]
        except Exception:
            pass
        self.done.emit(url, title)


# ------------------------------------------------------------------ slash menu
class SlashMenu(QFrame):
    chosen = pyqtSignal(str)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("SlashMenu")
        self.setStyleSheet(f"""
            QFrame#SlashMenu {{ background: {Palette.surface_solid}; border: 1px solid {ink_css(0.10)}; border-radius: 14px; }}
            QListWidget {{ background: transparent; border: none; outline: none; }}
            QListWidget::item {{ border-radius: 9px; padding: 0; }}
            QListWidget::item:selected {{ background: {_rgba(Theme.accent(), 70)}; }}""")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(lambda it: self.chosen.emit(it.data(Qt.ItemDataRole.UserRole)))
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay.addWidget(self.list)
        self.setFixedWidth(290)
        self.hide()

    @staticmethod
    def _score(q: str, key: str, text: str, words: str) -> int:
        if not q:
            return 1
        text, words = text.lower(), words.lower()
        if key.startswith(q):
            return 5
        if text.startswith(q) or any(w.startswith(q) for w in text.split()):
            return 4
        if q in key or q in text:
            return 3
        if any(w.startswith(q) for w in words.split()):
            return 2
        return 1 if q in words else 0

    def filter(self, query: str) -> int:
        q = query.lower()
        self.list.clear()
        ranked = []
        for order, (key, glyph, label_key, words, _) in enumerate(SLASH_COMMANDS):
            text = tr(label_key)
            score = self._score(q, key, text, words)
            if score:
                ranked.append((-score, order, key, glyph, text))
        for _, _, key, glyph, text in sorted(ranked):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, key)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 8, 4)
            h.setSpacing(10)
            plate = QWidget()
            plate.setFixedSize(30, 30)
            plate.setStyleSheet(f"background: {ink_css(0.06)}; border-radius: 8px;")
            pl = QHBoxLayout(plate)
            pl.setContentsMargins(0, 0, 0, 0)
            if len(glyph) == 1 and ord(glyph) >= 0xE000:
                pl.addWidget(GlyphLabel(glyph, 10.5, Theme.accent2()), 0, Qt.AlignmentFlag.AlignCenter)
            else:
                g = label(glyph, 9, QFont.Weight.Bold)
                g.setStyleSheet(f"color: {Theme.accent2()}; background: transparent;")
                pl.addWidget(g, 0, Qt.AlignmentFlag.AlignCenter)
            h.addWidget(plate)
            h.addWidget(label(text, 9.8), 1)
            item.setSizeHint(QSize(0, 40))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.setFixedHeight(min(8, self.list.count()) * 40 + 14)
        return self.list.count()

    def move_selection(self, step: int) -> None:
        n = self.list.count()
        if n:
            self.list.setCurrentRow((self.list.currentRow() + step) % n)

    def current_key(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


class LinkMenu(SlashMenu):
    """Same look as the slash menu, but lists note titles (newest first) for [[links]]."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.titles: list[str] = []

    def filter(self, query: str) -> int:
        q = query.strip().lower()
        self.list.clear()
        starts = [t for t in self.titles if t.lower().startswith(q)]
        contains = [t for t in self.titles if q in t.lower() and t not in starts]
        shown = (starts + contains)[:30]
        if q and not any(t.lower() == q for t in shown):
            shown.append("+ " + query.strip())  # create a link to a note that doesn't exist yet
        for title in shown:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, title[2:] if title.startswith("+ ") else title)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 4, 10, 4)
            h.setSpacing(10)
            h.addWidget(GlyphLabel(Icons.ADD if title.startswith("+ ") else Icons.NOTES, 9.5, Theme.accent2()))
            h.addWidget(label(title, 9.8), 1)
            item.setSizeHint(QSize(0, 36))
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.setFixedHeight(min(8, self.list.count()) * 36 + 14)
        return self.list.count()


# ---------------------------------------------------------------------- editor
class NoteEditor(QPlainTextEdit):
    command_requested = pyqtSignal(str)   # slash actions the page handles ("image", "file")
    escape_pressed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Editor")
        self.setFont(ui_font(11.5))
        self.setTabStopDistance(28)
        self.setCursorWidth(2)
        self.highlighter = MarkdownHighlighter(self.document())
        auto_direction(self)
        self.attachments: Callable[[], Path | None] = lambda: None
        self.escape_wanted: Callable[[], bool] = lambda: False  # the page has a find bar / focus mode to close
        self.max_line_width = 0
        self.find_selections: list[QTextEdit.ExtraSelection] = []
        self.menu = SlashMenu(self.viewport())
        self.menu.chosen.connect(self._apply_command)
        self._slash_pos: int | None = None
        self.note_titles: Callable[[], list[str]] = lambda: []
        self.link_menu = LinkMenu(self.viewport())
        self.link_menu.chosen.connect(self._apply_link)
        self._link_pos: int | None = None  # first character after "[["
        self._titles = _TitleFetcher(self)
        self._titles.done.connect(self._title_arrived)
        self.textChanged.connect(self._update_slash)
        self.cursorPositionChanged.connect(self._update_slash)
        self.textChanged.connect(self._update_link)
        self.cursorPositionChanged.connect(self._update_link)
        self.cursorPositionChanged.connect(self._refresh_selections)

    # ----------------------------------------------------------- layout
    def set_max_line_width(self, width: int) -> None:
        self.max_line_width = width
        self._apply_margins()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_margins()

    def _apply_margins(self) -> None:
        side = max(0, (self.width() - self.max_line_width) // 2) if self.max_line_width else 0
        self.setViewportMargins(side, 0, side, 0)

    # --------------------------------------------------------- highlights
    def _refresh_selections(self) -> None:
        line = QTextEdit.ExtraSelection()
        line.format.setBackground(ink(9 / 255))
        line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        line.cursor = self.textCursor()
        line.cursor.clearSelection()
        self.setExtraSelections([line, *self.find_selections])

    def set_find_selections(self, cursors: list[QTextCursor], current: int) -> None:
        sels = []
        for i, c in enumerate(cursors):
            s = QTextEdit.ExtraSelection()
            s.cursor = c
            s.format.setBackground(QColor(Theme.accent2()) if i == current else QColor(251, 191, 36, 90))
            if i == current:
                s.format.setForeground(QColor("#0A0D14"))
            sels.append(s)
        self.find_selections = sels
        self._refresh_selections()

    # ------------------------------------------------------------- keys
    def event(self, e):
        if (e.type() == QEvent.Type.ShortcutOverride and e.key() == Qt.Key.Key_Escape
                and (self.menu.isVisible() or self.link_menu.isVisible() or self.escape_wanted())):
            e.accept()  # deliver Esc as a key press instead of triggering the window shortcut
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        key, mods = e.key(), e.modifiers()
        if self.link_menu.isVisible():
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                return self.link_menu.move_selection(1 if key == Qt.Key.Key_Down else -1)
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                k = self.link_menu.current_key()
                if k:
                    return self._apply_link(k)
            if key == Qt.Key.Key_Escape:
                return self._close_link_menu()
        if self.menu.isVisible():
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                return self.menu.move_selection(1 if key == Qt.Key.Key_Down else -1)
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                k = self.menu.current_key()
                if k:
                    return self._apply_command(k)
            if key == Qt.Key.Key_Escape:
                return self._close_menu()
        if key == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if mods == Qt.KeyboardModifier.ControlModifier:
                self.toggle_task_at(self.textCursor().blockNumber())
                return
            if mods == Qt.KeyboardModifier.NoModifier and continue_list(self):
                return
        if mods == Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_B:
                return wrap_selection(self, "**", placeholder=tr("md_bold"))
            if key == Qt.Key.Key_I:
                return wrap_selection(self, "*", placeholder=tr("md_italic"))
            if key == Qt.Key.Key_K:
                return wrap_selection(self, "[", "](https://)", tr("md_link"))
        if mods == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier) and key == Qt.Key.Key_V:
            return self.insertPlainText(self._clipboard_text())  # paste as plain text
        super().keyPressEvent(e)
        if e.text() == "[":
            c = self.textCursor()
            if c.positionInBlock() >= 2 and c.block().text()[c.positionInBlock() - 2:c.positionInBlock()] == "[[":
                self.open_link_menu(c.position())
        if e.text() == "/":
            pos = self.textCursor().position()
            block_pos = self.textCursor().positionInBlock()
            before = self.textCursor().block().text()[:block_pos - 1]
            if not before or before[-1].isspace():
                self.open_menu_at(pos - 1)

    @staticmethod
    def _clipboard_text() -> str:
        from PyQt6.QtWidgets import QApplication
        return QApplication.clipboard().text()

    # ------------------------------------------------------------ tasks
    def toggle_task_at(self, line: int) -> bool:
        block = self.document().findBlockByNumber(line)
        m = re.match(r"^(\s*(?:[-*+]|\d+[.)])\s+\[)([ xX])(\])", block.text())
        if not block.isValid() or not m:
            return False
        c = QTextCursor(block)
        c.setPosition(block.position() + m.start(2))
        c.setPosition(block.position() + m.end(2), QTextCursor.MoveMode.KeepAnchor)
        c.insertText(" " if m.group(2) in "xX" else "x")
        return True

    # ------------------------------------------------------------ [[links]]
    def open_link_menu(self, start: int) -> None:
        self._close_menu()
        self._link_pos = start
        self.link_menu.titles = [t for t in self.note_titles() if t]
        if not self.link_menu.filter(""):
            self._link_pos = None
            return
        self._place(self.link_menu)
        self.link_menu.show()
        self.link_menu.raise_()

    def _close_link_menu(self) -> None:
        self._link_pos = None
        self.link_menu.hide()

    def _update_link(self) -> None:
        if self._link_pos is None:
            return
        pos = self.textCursor().position()
        text = self.toPlainText()
        if pos < self._link_pos or text[self._link_pos - 2:self._link_pos] != "[[":
            return self._close_link_menu()
        query = text[self._link_pos:pos]
        if len(query) > 80 or any(ch in query for ch in "]\n|#") or not self.link_menu.filter(query):
            return self._close_link_menu()

    def _apply_link(self, title: str) -> None:
        start = self._link_pos
        self._close_link_menu()
        if start is None:
            return
        c = self.textCursor()
        c.setPosition(start)
        c.setPosition(self.textCursor().position(), QTextCursor.MoveMode.KeepAnchor)
        rest = self.toPlainText()[self.textCursor().position():self.textCursor().position() + 2]
        c.insertText(title + ("" if rest == "]]" else "]]"))
        if rest == "]]":
            c.movePosition(QTextCursor.MoveOperation.Right, n=2)
        self.setTextCursor(c)

    def _place(self, menu: QWidget) -> None:
        rect = self.cursorRect()
        x = min(max(4, rect.left() - 10), self.viewport().width() - menu.width() - 4)
        y = rect.bottom() + 6
        if y + menu.height() > self.viewport().height():
            y = max(4, rect.top() - menu.height() - 6)
        menu.move(x, y)

    # ------------------------------------------------------------ slash
    def open_menu_at(self, slash_pos: int) -> None:
        self._slash_pos = slash_pos
        if not self.menu.filter(""):
            return
        self._place(self.menu)
        self.menu.show()
        self.menu.raise_()

    def _close_menu(self) -> None:
        self._slash_pos = None
        self.menu.hide()

    def _update_slash(self) -> None:
        if self._slash_pos is None:
            return
        pos = self.textCursor().position()
        text = self.toPlainText()
        if pos <= self._slash_pos or self._slash_pos >= len(text) or text[self._slash_pos] != "/":
            return self._close_menu()
        query = text[self._slash_pos + 1:pos]
        if len(query) > 24 or any(ch.isspace() for ch in query) or not self.menu.filter(query):
            return self._close_menu()

    def _apply_command(self, key: str) -> None:
        start = self._slash_pos
        self._close_menu()
        if start is None:
            return
        c = self.textCursor()
        c.setPosition(start)
        c.setPosition(self.textCursor().position(), QTextCursor.MoveMode.KeepAnchor)
        c.removeSelectedText()
        self.setTextCursor(c)
        snippet = next((s for k, _, _, _, s in SLASH_COMMANDS if k == key), None)
        if snippet is None:
            self.command_requested.emit(key)
            return
        now = datetime.now()
        snippet = snippet.replace("{date}", now.strftime("%Y-%m-%d")).replace("{time}", now.strftime("%H:%M"))
        self.insert_snippet(snippet)

    def insert_snippet(self, snippet: str) -> None:
        c = self.textCursor()
        # block-level snippets start on their own line
        if "\n" in snippet or snippet.startswith(("#", ">", "-", "|", "1.", "$$")):
            if c.positionInBlock() > 0 and c.block().text().strip():
                snippet = "\n" + snippet
        before, _, after = snippet.partition(CURSOR)
        c.beginEditBlock()
        c.insertText(before)
        caret = c.position()
        c.insertText(after)
        c.endEditBlock()
        c.setPosition(caret)
        self.setTextCursor(c)
        self.setFocus()

    # -------------------------------------------------------- smart paste
    def canInsertFromMimeData(self, source):
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        folder = self.attachments()
        if source.hasImage() and not source.hasUrls() and folder is not None:
            image = source.imageData()
            if image is not None and not image.isNull():
                folder.mkdir(parents=True, exist_ok=True)
                path = _unique(folder, f"image-{datetime.now():%Y%m%d-%H%M%S}.png")
                image.save(str(path), "PNG")
                return self.insert_snippet(f"![]({_md_path(path)})\n§")
        if source.hasUrls() and folder is not None:
            files = [Path(u.toLocalFile()) for u in source.urls() if u.isLocalFile() and Path(u.toLocalFile()).is_file()]
            if files:
                return self.attach_files([str(f) for f in files])
        text = source.text() if source.hasText() else ""
        url = text.strip()
        if re.fullmatch(r"https?://\S+", url) and "\n" not in url:
            selected = self.textCursor().selectedText()
            if selected:
                return self.insertPlainText(f"[{selected}]({url})")
            self.insertPlainText(f"[{url}]({url})")
            self._titles.fetch(url)
            return
        super().insertFromMimeData(source)

    def attach_files(self, paths: list[str]) -> None:
        folder = self.attachments()
        if folder is None:
            return
        folder.mkdir(parents=True, exist_ok=True)
        lines = []
        for p in paths:
            src = Path(p)
            dest = _unique(folder, src.name)
            shutil.copy2(src, dest)
            if src.suffix.lower() in IMAGE_EXT:
                lines.append(f"![{src.stem}]({_md_path(dest)})")
            else:
                lines.append(f"[📎 {src.name}]({_md_path(dest)})")
        self.insert_snippet("\n".join(lines) + "\n§")

    def _title_arrived(self, url: str, title: str) -> None:
        if not title:
            return
        placeholder = f"[{url}]({url})"
        doc = self.document()
        found = doc.find(placeholder)
        if not found.isNull():
            found.insertText(f"[{title.replace(']', '')}]({url})")


# -------------------------------------------------------------------- find bar
class FindBar(QFrame):
    closed = pyqtSignal()

    def __init__(self, editor: NoteEditor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.cursors: list[QTextCursor] = []
        self.current = -1
        self.setObjectName("FindBar")
        self.setStyleSheet(f"QFrame#FindBar {{ background: {ink_css(0.04)}; border: 1px solid "
                           f"{ink_css(0.08)}; border-radius: 12px; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 6, 6)
        outer.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(GlyphLabel(Icons.SEARCH, 10, Palette.text_dim))
        self.find = QLineEdit()
        self.find.setPlaceholderText(tr("find_placeholder"))
        self.find.textChanged.connect(lambda _: self.search(keep=False))
        self.find.returnPressed.connect(lambda: self.step(1))
        row.addWidget(self.find, 1)
        self.count = label("", 8.8, role="Faint")
        self.count.setMinimumWidth(56)
        row.addWidget(self.count)
        self.case = IconButton("Aa", tr("match_case"), 30, 9)
        self.case.setFont(ui_font(9, QFont.Weight.Bold))
        self.case.setCheckable(True)
        self.case.toggled.connect(lambda _: self.search(keep=False))
        row.addWidget(self.case)
        for glyph, tip, step in ((Icons.UP, tr("find_prev"), -1), (Icons.CHEVRON, tr("find_next"), 1)):
            b = IconButton(glyph, tip, 30, 9)
            b.clicked.connect(lambda _=False, s=step: self.step(s))
            row.addWidget(b)
        self.toggle_replace = IconButton(Icons.SYNC, tr("replace"), 30, 9)
        self.toggle_replace.setCheckable(True)
        self.toggle_replace.toggled.connect(lambda on: self.replace_row.setVisible(on))
        row.addWidget(self.toggle_replace)
        close = IconButton(Icons.CLOSE, tr("close"), 30, 9)
        close.clicked.connect(self.close_bar)
        row.addWidget(close)
        outer.addLayout(row)
        self.replace_row = QWidget()
        rr = QHBoxLayout(self.replace_row)
        rr.setContentsMargins(22, 0, 0, 0)
        rr.setSpacing(4)
        self.replace = QLineEdit()
        self.replace.setPlaceholderText(tr("replace_placeholder"))
        self.replace.returnPressed.connect(self.replace_one)
        rr.addWidget(self.replace, 1)
        from app.ui.widgets.common import button
        one = button(tr("replace"), role="Ghost")
        one.clicked.connect(self.replace_one)
        rr.addWidget(one)
        every = button(tr("replace_all"), role="Ghost")
        every.clicked.connect(self.replace_all)
        rr.addWidget(every)
        outer.addWidget(self.replace_row)
        self.replace_row.hide()
        self.find.installEventFilter(self)
        self.replace.installEventFilter(self)
        self.hide()
        editor.textChanged.connect(lambda: self.isVisible() and self.search(keep=True))

    def open(self, replace: bool = False) -> None:
        selected = self.editor.textCursor().selectedText()
        if selected and " " not in selected:
            self.find.setText(selected)
        self.toggle_replace.setChecked(replace)
        self.show()
        (self.replace if replace and self.find.text() else self.find).setFocus()
        self.find.selectAll()
        self.search(keep=False)

    def close_bar(self) -> None:
        self.hide()
        self.cursors = []
        self.editor.set_find_selections([], -1)
        self.editor.setFocus()
        self.closed.emit()

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.ShortcutOverride and e.key() == Qt.Key.Key_Escape:
            e.accept()
            return True
        if e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
            self.close_bar()
            return True
        return super().eventFilter(obj, e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            return self.close_bar()
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            return self.step(-1)
        super().keyPressEvent(e)

    def _flags(self):
        return QTextDocument.FindFlag.FindCaseSensitively if self.case.isChecked() else QTextDocument.FindFlag(0)

    def search(self, keep: bool) -> None:
        text = self.find.text()
        doc = self.editor.document()
        self.cursors = []
        if text:
            c = QTextCursor(doc)
            while len(self.cursors) < 2000:
                c = doc.find(text, c, self._flags())
                if c.isNull():
                    break
                self.cursors.append(QTextCursor(c))
        if not self.cursors:
            self.current = -1
        elif not keep or self.current >= len(self.cursors) or self.current < 0:
            pos = self.editor.textCursor().selectionStart()
            self.current = next((i for i, c in enumerate(self.cursors) if c.selectionStart() >= pos), 0)
        self._show()

    def _show(self) -> None:
        n = len(self.cursors)
        self.count.setText(ltr(f"{self.current + 1}/{n}") if n else (tr("no_matches") if self.find.text() else ""))
        self.find.setStyleSheet("" if n or not self.find.text() else f"color: {Palette.danger};")
        self.editor.set_find_selections(self.cursors, self.current)
        if 0 <= self.current < n:
            c = QTextCursor(self.cursors[self.current])
            c.setPosition(c.selectionStart())
            self.editor.setTextCursor(c)
            self.editor.centerCursor()

    def step(self, delta: int) -> None:
        if self.cursors:
            self.current = (self.current + delta) % len(self.cursors)
            self._show()

    def replace_one(self) -> None:
        if not (0 <= self.current < len(self.cursors)):
            return
        self.cursors[self.current].insertText(self.replace.text())
        self.search(keep=True)

    def replace_all(self) -> None:
        if not self.cursors:
            return
        edit = QTextCursor(self.editor.document())
        edit.beginEditBlock()
        for c in reversed(self.cursors):
            c.insertText(self.replace.text())
        edit.endEditBlock()
        self.search(keep=False)


# ---------------------------------------------------------------- outline panel
class OutlinePanel(QFrame):
    jump = pyqtSignal(int)  # source line

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Outline")
        self.setStyleSheet(f"QFrame#Outline {{ background: {ink_css(0.025)}; border-radius: 12px; }}"
                           "QListWidget { background: transparent; border: none; }")
        self.setMinimumWidth(170)
        self.setMaximumWidth(260)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 10, 8, 8)
        lay.setSpacing(6)
        lay.addWidget(label(tr("outline"), 8.8, QFont.Weight.DemiBold, "Faint"))
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.itemClicked.connect(lambda it: self.jump.emit(it.data(Qt.ItemDataRole.UserRole)))
        lay.addWidget(self.list, 1)
        self.empty = label(tr("outline_empty"), 8.8, role="Faint", wrap=True)
        lay.addWidget(self.empty)
        self.lines: list[int] = []
        self._signature = None

    def set_text(self, text: str) -> None:
        heads = outline(text)
        signature = tuple(heads)
        if signature == self._signature:
            return
        self._signature = signature
        self.list.clear()
        self.lines = [line for _, line, _ in heads]
        for level, line, title in heads:
            item = QListWidgetItem(("    " * (level - 1)) + title)
            item.setData(Qt.ItemDataRole.UserRole, line)
            item.setFont(ui_font(9.6 if level > 1 else 10, QFont.Weight.DemiBold if level == 1 else QFont.Weight.Normal))
            if level > 2:
                item.setForeground(QColor(Palette.text_dim))
            self.list.addItem(item)
        self.empty.setVisible(not heads)

    def follow(self, line: int) -> str:
        """Highlights the section the cursor is in; returns its title."""
        index = -1
        for i, l in enumerate(self.lines):
            if l <= line:
                index = i
        self.list.blockSignals(True)
        self.list.setCurrentRow(index)
        self.list.blockSignals(False)
        return self.list.item(index).text().strip() if index >= 0 else ""
