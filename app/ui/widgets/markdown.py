"""Markdown support for the editor: live syntax highlighting + formatting helpers."""
from __future__ import annotations

import re

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QPlainTextEdit

from app.ui.theme import Palette, Theme


def _fmt(color: str, bold: bool = False, italic: bool = False, size_delta: float = 0,
         mono: bool = False, strike: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    if italic:
        f.setFontItalic(True)
    if strike:
        f.setFontStrikeOut(True)
    if mono:
        f.setFontFamilies(["Cascadia Mono", "Consolas"])
    if size_delta:
        f.setProperty(QTextCharFormat.Property.FontSizeAdjustment, int(size_delta))
    return f


def auto_direction(editor: QPlainTextEdit) -> None:
    """Each paragraph follows its own language: Arabic lines align right, English lines left."""
    option = editor.document().defaultTextOption()
    option.setTextDirection(Qt.LayoutDirection.LayoutDirectionAuto)
    editor.document().setDefaultTextOption(option)


class MarkdownHighlighter(QSyntaxHighlighter):
    """Colors headings, emphasis, code, lists, quotes, links and checkboxes as you type."""

    def __init__(self, document):
        super().__init__(document)
        accent, accent2 = Theme.accent(), Theme.accent2()
        self.rules = [
            (re.compile(r"^#{1}\s.*$"), _fmt(Palette.text, bold=True, size_delta=3)),
            (re.compile(r"^#{2}\s.*$"), _fmt(Palette.text, bold=True, size_delta=2)),
            (re.compile(r"^#{3,6}\s.*$"), _fmt(Palette.text, bold=True, size_delta=1)),
            (re.compile(r"^\s*>.*$"), _fmt(Palette.text_dim, italic=True)),
            (re.compile(r"^\s*([-*+]|\d+\.)\s"), _fmt(accent, bold=True)),
            (re.compile(r"^\s*[-*+]\s\[[ xX]\]"), _fmt(accent2, bold=True)),
            (re.compile(r"\*\*[^*]+\*\*|__[^_]+__"), _fmt(Palette.text, bold=True)),
            (re.compile(r"(?<![*\w])\*[^*\n]+\*(?!\*)|(?<![_\w])_[^_\n]+_(?!_)"), _fmt(Palette.text, italic=True)),
            (re.compile(r"~~[^~]+~~"), _fmt(Palette.text_faint, strike=True)),
            (re.compile(r"`[^`]+`"), _fmt("#F5A97F", mono=True)),
            (re.compile(r"\[[^\]]+\]\([^)]+\)"), _fmt(accent2)),
            (re.compile(r"https?://\S+"), _fmt(accent2)),
            (re.compile(r"^\s*([-*_]\s*){3,}$"), _fmt(Palette.text_faint)),
            (re.compile(r"\[\[[^\[\]\n]+\]\]"), _fmt(accent2, bold=True)),
            (re.compile(r"(?<![\w#&/\\])#[A-Za-z\u0600-\u06FF_][\w\u0600-\u06FF\-/]*"), _fmt(accent)),
            (re.compile(r"^\s*>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]", re.IGNORECASE), _fmt(accent2, bold=True)),
        ]
        self.fence_fmt = _fmt("#F5A97F", mono=True)

    def highlightBlock(self, text: str) -> None:
        # fenced code blocks ``` ... ``` (state 1 = inside a fence)
        in_fence = self.previousBlockState() == 1
        if text.strip().startswith("```"):
            self.setFormat(0, len(text), self.fence_fmt)
            self.setCurrentBlockState(0 if in_fence else 1)
            return
        if in_fence:
            self.setFormat(0, len(text), self.fence_fmt)
            self.setCurrentBlockState(1)
            return
        self.setCurrentBlockState(0)
        for pattern, fmt in self.rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


# --------------------------------------------------------------- formatting
def wrap_selection(editor: QPlainTextEdit, left: str, right: str | None = None, placeholder: str = "") -> None:
    """**bold** style: wraps the selection (or inserts a placeholder)."""
    right = left if right is None else right
    cursor = editor.textCursor()
    text = cursor.selectedText() or placeholder
    cursor.insertText(f"{left}{text}{right}")
    if not cursor.hasSelection() and text == placeholder:
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, len(right))
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, len(text))
    editor.setTextCursor(cursor)
    editor.setFocus()


def prefix_lines(editor: QPlainTextEdit, prefix: str) -> None:
    """'# ', '- ', '- [ ] ', '> ' for every selected line (toggles if already there)."""
    cursor = editor.textCursor()
    start, end = sorted((cursor.selectionStart(), cursor.selectionEnd()))
    cursor.setPosition(start)
    cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
    cursor.beginEditBlock()
    while True:
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        line = cursor.block().text()
        if line.startswith(prefix):
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, len(prefix))
            cursor.removeSelectedText()
            end -= len(prefix)
        else:
            cursor.insertText(prefix)
            end += len(prefix)
        if not cursor.movePosition(QTextCursor.MoveOperation.NextBlock) or cursor.position() > end:
            break
    cursor.endEditBlock()
    editor.setFocus()


def continue_list(editor: QPlainTextEdit) -> bool:
    """On Enter inside a list item, start the next item automatically. Returns True if handled."""
    cursor = editor.textCursor()
    line = cursor.block().text()
    m = re.match(r"^(\s*)([-*+] \[[ xX]\] |[-*+] |(\d+)\. )(.*)$", line)
    if not m:
        return False
    indent, marker, number, rest = m.group(1), m.group(2), m.group(3), m.group(4)
    if not rest.strip():  # empty item -> end the list
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        return True
    if number:
        marker = f"{int(number) + 1}. "
    elif "[" in marker:
        marker = marker[0] + " [ ] "
    cursor.insertText("\n" + indent + marker)
    editor.setTextCursor(cursor)
    return True
