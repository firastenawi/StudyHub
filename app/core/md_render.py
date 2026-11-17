"""
Markdown -> HTML for the note preview (no Qt here, so it is easy to test).

* CommonMark + tables, ~~strike~~, footnotes, $math$ / $$math$$ (rendered by KaTeX in the page)
* ```lang code blocks colored by Pygments, with a language label and a Copy link
* ```mermaid diagrams (rendered by Mermaid in the page)
* - [ ] tasks become clickable boxes that toggle the line in the editor
* > [!NOTE] / [!TIP] / [!WARNING] / [!IMPORTANT] / [!CAUTION] callouts (GitHub syntax)
* every block carries data-line="<source line>" so the preview can follow the editor cursor
* raw HTML in notes is shown as text, never executed

* [[Note title]] / [[title|shown text]] link to other notes (dimmed when the note doesn't exist yet)
* #tags become clickable chips

Links use the studyhub: scheme for in-page actions: studyhub:copy/<n>, studyhub:task/<line>,
studyhub:note/<title>, studyhub:tag/<tag>.
"""
from __future__ import annotations

import html
import re
from urllib.parse import quote

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from app.core.notes_index import TAG_RE

CALLOUTS = {"NOTE": "note", "TIP": "tip", "IMPORTANT": "important", "WARNING": "warning", "CAUTION": "caution"}
_TASK = re.compile(r"^\[([ xX])\](?:\s+|$)")  # "- [ ]" with nothing after it is still a task
_CALLOUT = re.compile(r"^\[!(\w+)\][ \t]*")
_FORMATTER = HtmlFormatter(nowrap=True, classprefix="tok-")


def code_css() -> str:
    """Pygments token colors (classprefix tok-) in a dark theme close to the app."""
    return HtmlFormatter(style="github-dark", classprefix="tok-").get_style_defs(".code")


def _strip_prefix(inline: Token, pattern: re.Pattern) -> re.Match | None:
    m = pattern.match(inline.content)
    if not m or not inline.children or inline.children[0].type != "text":
        return None
    first = inline.children[0]
    first.content = pattern.sub("", first.content, count=1)
    inline.content = inline.content[m.end():]
    return m


def _wikilink(state, silent: bool) -> bool:
    src, pos = state.src, state.pos
    if not src.startswith("[[", pos):
        return False
    end = src.find("]]", pos + 2)
    if end < 0 or "\n" in src[pos:end]:
        return False
    inner = src[pos + 2:end]
    target, _, shown = inner.partition("|")
    title = target.split("#", 1)[0].strip()
    if not title:
        return False
    if not silent:
        exists = title.lower() in state.env.get("titles", ())
        tok = state.push("html_inline", "", 0)
        tok.content = (f'<a class="wikilink{"" if exists else " missing"}" href="studyhub:note/{quote(title)}">'
                       f'{html.escape((shown or target).strip())}</a>')
    state.pos = end + 2
    return True


def _hashtags(state) -> None:
    """#tag inside plain text -> chip link (code spans and links are left alone)."""
    for block in state.tokens:
        if block.type != "inline" or not block.children:
            continue
        out, depth = [], 0
        for tok in block.children:
            if tok.type == "link_open":
                depth += 1
            elif tok.type == "link_close":
                depth -= 1
            if tok.type != "text" or depth or "#" not in tok.content:
                out.append(tok)
                continue
            last = 0
            for m in TAG_RE.finditer(tok.content):
                tag = m.group(1).rstrip("-/")
                if tag.isdigit() or re.fullmatch(r"[0-9a-fA-F]{3}|[0-9a-fA-F]{6}", tag):
                    continue
                if m.start() > last:
                    t = Token("text", "", 0)
                    t.content = tok.content[last:m.start()]
                    out.append(t)
                chip = Token("html_inline", "", 0)
                chip.content = f'<a class="tag" href="studyhub:tag/{quote(tag.lower())}">#{html.escape(tag)}</a>'
                out.append(chip)
                last = m.start() + 1 + len(tag)
            if last < len(tok.content):
                t = Token("text", "", 0)
                t.content = tok.content[last:]
                out.append(t)
        block.children = out


def _core_rule(state) -> None:
    tokens = state.tokens
    for i, tok in enumerate(tokens):
        if tok.map and tok.nesting == 1:
            tok.attrSet("data-line", str(tok.map[0]))
        if tok.type != "inline" or i < 2 or tokens[i - 1].type != "paragraph_open":
            continue
        opener = tokens[i - 2]
        if opener.type == "list_item_open" and tok.map:
            m = _strip_prefix(tok, _TASK)
            if m:
                done = m.group(1) in "xX"
                box = Token("html_inline", "", 0)
                box.content = (f'<a class="task{" done" if done else ""}" href="studyhub:task/{tok.map[0]}" '
                               f'title="toggle"><span class="box"></span></a>')
                tok.children.insert(0, box)
                opener.attrSet("class", "task-item" + (" done" if done else ""))
        elif opener.type == "blockquote_open":
            m = _CALLOUT.match(tok.content)
            kind = CALLOUTS.get(m.group(1).upper()) if m else None
            if kind and _strip_prefix(tok, _CALLOUT):
                while tok.children and (tok.children[0].type in ("softbreak", "hardbreak")
                                        or (tok.children[0].type == "text" and not tok.children[0].content)):
                    tok.children.pop(0)  # the line break after "[!NOTE]"
                opener.attrSet("class", f"callout callout-{kind}")
                head = Token("html_inline", "", 0)
                head.content = f'<span class="callout-title" data-kind="{kind}"></span>'
                tok.children.insert(0, head)


class NoteRenderer:
    def __init__(self):
        md = MarkdownIt("commonmark", {"html": False, "breaks": True})
        md.enable(["table", "strikethrough"])
        md.use(dollarmath_plugin, double_inline=True)
        md.use(footnote_plugin)
        md.inline.ruler.before("link", "wikilink", _wikilink)
        md.core.ruler.push("studyhub", _core_rule)
        md.core.ruler.push("hashtags", _hashtags)
        md.add_render_rule("fence", self._fence)
        md.add_render_rule("code_block", self._fence)
        md.add_render_rule("link_open", self._link_open)
        self.md = md

    # ------------------------------------------------------------- rules
    @staticmethod
    def _fence(renderer, tokens, idx, options, env):  # markdown-it binds rules to its renderer
        tok = tokens[idx]
        code = tok.content
        lang = (tok.info or "").strip().split()[0].lower() if tok.info else ""
        line = tok.map[0] if tok.map else 0
        if lang == "mermaid":
            return f'<div class="mermaid-wrap" data-line="{line}"><pre class="mermaid">{html.escape(code)}</pre></div>\n'
        if lang in ("math", "latex", "tex", "katex"):
            return f'<div class="math block" data-line="{line}">{html.escape(code)}</div>\n'
        codes = env.setdefault("codes", [])
        codes.append(code)
        number = len(codes) - 1
        try:
            lexer = get_lexer_by_name(lang) if lang else None
        except ClassNotFound:
            lexer = None
        body = highlight(code, lexer, _FORMATTER) if lexer else html.escape(code)
        label = html.escape(lang or "text")
        return (f'<div class="code" data-line="{line}"><div class="code-head"><span>{label}</span>'
                f'<a class="copy" href="studyhub:copy/{number}"></a></div>'
                f'<pre><code>{body}</code></pre></div>\n')

    @staticmethod
    def _link_open(renderer, tokens, idx, options, env):
        tok = tokens[idx]
        href = tok.attrGet("href") or ""
        if href.startswith(("http://", "https://")):
            tok.attrSet("title", href)
        return renderer.renderToken(tokens, idx, options, env)

    # -------------------------------------------------------------- api
    def render(self, text: str, titles=()) -> tuple[str, list[str]]:
        env: dict = {"codes": [], "titles": titles}
        body = self.md.render(text or "", env)
        return body, env["codes"]


_renderer: NoteRenderer | None = None


def render(text: str, titles=()) -> tuple[str, list[str]]:
    """Returns (html, code blocks in order) — the list backs the Copy links."""
    global _renderer
    if _renderer is None:
        _renderer = NoteRenderer()
    return _renderer.render(text, titles)


def toggle_task(text: str, line: int) -> str | None:
    """Flips '- [ ]' <-> '- [x]' on the given source line; None if that line has no task box."""
    lines = text.split("\n")
    if not 0 <= line < len(lines):
        return None
    m = re.match(r"^(\s*(?:[-*+]|\d+[.)])\s+\[)([ xX])(\].*)$", lines[line])
    if not m:
        return None
    lines[line] = m.group(1) + (" " if m.group(2) in "xX" else "x") + m.group(3)
    return "\n".join(lines)


def outline(text: str) -> list[tuple[int, int, str]]:
    """Headings as (level, line, text), skipping ``` fenced blocks."""
    result, fence = [], False
    for number, line in enumerate((text or "").split("\n")):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m:
            result.append((len(m.group(1)), number, m.group(2)))
    return result


def stats(text: str) -> dict:
    words = re.findall(r"[\w؀-ۿ]+(?:['’][\w]+)?", text or "")
    return {"words": len(words), "chars": len(text or ""), "minutes": max(1, round(len(words) / 200)) if words else 0}
