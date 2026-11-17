"""
Live note preview: a Qt WebEngine page that renders Markdown (app.core.md_render) with
Pygments-colored code + Copy, KaTeX math, Mermaid diagrams, clickable task boxes and callouts.

The page is loaded once; every update only swaps the content (no flicker, scroll position kept).
Clicks never navigate the page: web links open in the browser, studyhub: links become signals.
Falls back to a plain QTextBrowser when WebEngine isn't available.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import QApplication, QStackedWidget, QTextBrowser

from app.config import DATA_DIR
from app.core.i18n import tr
from app.core.md_render import code_css, render
from app.ui.theme import Palette, Theme

VENDOR = Path(__file__).resolve().parents[2] / "assets" / "vendor"

try:
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_WEBENGINE = False


def _css_ink(alpha: float) -> str:
    """Web CSS wants alpha 0..1 (Qt's rgba() helper writes 0..255)."""
    c = QColor(Palette.ink)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def _css() -> str:
    a1, a2 = Theme.accent(), Theme.accent2()
    light = Palette.light()
    hi = "#0B1020" if light else "#fff"          # headings / bold
    inline_code = "#C2410C" if light else "#F5A97F"
    return f"""
:root {{ --text:{Palette.text}; --dim:{Palette.text_dim}; --faint:{Palette.text_faint}; --a1:{a1}; --a2:{a2};
         --line:{_css_ink(.08)}; --code:#0d1117; }}
* {{ box-sizing: border-box; }}
html, body {{ margin:0; background:transparent; color:var(--text);
  font: 15px/1.75 "Segoe UI Variable Text","Segoe UI",Tahoma,"Noto Sans Arabic",sans-serif; }}
body {{ padding: 4px 22px 60px; }}
::selection {{ background: {a1}66; }}
::-webkit-scrollbar {{ width:10px; }} ::-webkit-scrollbar-thumb {{ background:{_css_ink(.12)}; border-radius:5px; }}
#title {{ font-size: 30px; font-weight: 700; margin: 6px 0 14px; line-height:1.3; color:{hi}; }}
p, li, h1, h2, h3, h4, h5, h6, blockquote, td, th, dt, dd {{ unicode-bidi: plaintext; text-align: start; }}
h1, h2, h3, h4 {{ color:{hi}; line-height:1.35; margin: 1.4em 0 .5em; font-weight:650; }}
h1 {{ font-size: 25px; }} h2 {{ font-size: 21px; padding-bottom:6px; border-bottom:1px solid var(--line); }}
h3 {{ font-size: 18px; }} h4 {{ font-size: 16px; }}
a {{ color: var(--a2); text-decoration: none; border-bottom: 1px solid {a2}55; }}
a:hover {{ border-bottom-color: var(--a2); }}
strong {{ color:{hi}; }} del {{ color: var(--faint); }}
hr {{ border:0; height:1px; background: var(--line); margin: 24px 0; }}
img {{ max-width: 100%; border-radius: 12px; box-shadow: 0 6px 24px #0007; }}
:not(pre) > code {{ font: 13px "Cascadia Code","Cascadia Mono",Consolas,monospace; background: {_css_ink(.07)};
  color:{inline_code}; padding: 2px 6px; border-radius: 6px; unicode-bidi: isolate; direction:ltr; }}
.code {{ direction:ltr; text-align:left; background: var(--code); border:1px solid var(--line); border-radius: 14px;
  margin: 14px 0; overflow:hidden; }}
.code-head {{ display:flex; justify-content:space-between; align-items:center; padding: 6px 12px;
  font: 11.5px "Segoe UI",sans-serif; color: var(--dim); background: {_css_ink(.03)}; border-bottom:1px solid var(--line); }}
.code-head span {{ text-transform: uppercase; letter-spacing: .06em; }}
.code-head a.copy {{ border:0; color: var(--dim); padding: 2px 8px; border-radius: 6px; }}
.code-head a.copy:hover {{ color:#fff; background: {_css_ink(.08)}; }}
.code pre {{ margin:0; padding: 12px 14px; overflow:auto; }}
.code code {{ font: 13px/1.6 "Cascadia Code","Cascadia Mono",Consolas,monospace; color:#e6edf3; }}
blockquote {{ margin: 12px 0; padding: 6px 16px; border-inline-start: 3px solid var(--a1); color: var(--dim);
  background: {_css_ink(.03)}; border-radius: 0 10px 10px 0; }}
.callout {{ color: var(--text); border-radius: 12px; border-inline-start-width: 4px; padding: 10px 16px; }}
.callout-title {{ display:block; font-weight:700; margin-bottom:2px; }}
.callout-note {{ border-color:#60A5FA; background:#60A5FA14; }} .callout-note .callout-title {{ color:#60A5FA; }}
.callout-tip {{ border-color:#34D399; background:#34D39914; }} .callout-tip .callout-title {{ color:#34D399; }}
.callout-important {{ border-color:#A78BFA; background:#A78BFA14; }} .callout-important .callout-title {{ color:#A78BFA; }}
.callout-warning {{ border-color:#FBBF24; background:#FBBF2414; }} .callout-warning .callout-title {{ color:#FBBF24; }}
.callout-caution {{ border-color:#F87171; background:#F8717114; }} .callout-caution .callout-title {{ color:#F87171; }}
ul, ol {{ padding-inline-start: 26px; }}
li.task-item {{ list-style: none; margin-inline-start: -22px; }}
li.task-item.done {{ color: var(--faint); text-decoration: line-through; }}
a.task {{ border:0; display:inline-block; vertical-align: -3px; margin-inline-end: 8px; }}
a.task .box {{ display:inline-block; width:17px; height:17px; border-radius:5px; border:2px solid var(--dim);
  transition: all .15s; }}
a.task:hover .box {{ border-color: var(--a2); }}
a.task.done .box {{ background: var(--a1); border-color: var(--a1);
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M3.5 8.5l3 3 6-7' fill='none' stroke='white' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-size: 13px; background-position: center; background-repeat: no-repeat; }}
table {{ border-collapse: collapse; margin: 14px 0; width: auto; min-width: 50%; }}
th, td {{ border: 1px solid var(--line); padding: 7px 12px; }}
th {{ background: {_css_ink(.05)}; color:{hi}; }}
tr:nth-child(even) td {{ background: {_css_ink(.02)}; }}
.math.block {{ overflow-x:auto; margin: 12px 0; direction: ltr; }}
.math.inline {{ direction: ltr; unicode-bidi: isolate; }}
.mermaid-wrap {{ direction:ltr; background: {_css_ink(.03)}; border:1px solid var(--line); border-radius: 14px;
  padding: 14px; margin: 14px 0; text-align:center; overflow:auto; }}
pre.mermaid {{ margin:0; color: var(--dim); font: 12px Consolas,monospace; }}
.footnotes {{ color: var(--dim); font-size: 13px; }}
a.wikilink {{ color: var(--a2); border-bottom: 1px dashed {a2}88; }}
a.wikilink.missing {{ color: var(--faint); border-bottom-color: var(--faint); }}
a.tag {{ border: 0; color: {a1}; background: {a1}1f; padding: 1px 8px; border-radius: 10px; font-size: .9em;
  white-space: nowrap; }}
a.tag:hover {{ background: {a1}38; }}
@media print {{
  html, body {{ background: #fff !important; color: #1f2328; }}
  #title, h1, h2, h3, h4, strong, th {{ color: #111 !important; }}
  blockquote, .footnotes {{ color: #444; }}
  .code, .mermaid-wrap {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; break-inside: avoid; }}
  .callout {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; color: #1f2328; }}
  a {{ color: #0b57d0; }} li.task-item.done {{ color: #777; }}
  th, td {{ border-color: #ccc; }} th {{ background: #f2f2f2 !important; }}
  :not(pre) > code {{ background: #f2f2f2; color: #b3361b; }}
}}
.empty {{ color: var(--faint); font-style: italic; }}
.flash {{ animation: flash 1.2s ease; }}
@keyframes flash {{ from {{ background: {a1}33; }} to {{ background: transparent; }} }}
{code_css()}
"""


def _shell() -> str:
    katex = QUrl.fromLocalFile(str(VENDOR / "katex" / "katex.min.js")).toString()
    katex_css = QUrl.fromLocalFile(str(VENDOR / "katex" / "katex.min.css")).toString()
    mermaid = QUrl.fromLocalFile(str(VENDOR / "mermaid" / "mermaid.min.js")).toString()
    labels = {"copy": tr("md_copy"), "copied": tr("md_copied"), "note": tr("callout_note"), "tip": tr("callout_tip"),
              "important": tr("callout_important"), "warning": tr("callout_warning"),
              "caution": tr("callout_caution"), "empty": tr("note_placeholder")}
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="{katex_css}"><style>{_css()}</style>
<script src="{katex}"></script><script src="{mermaid}"></script></head>
<body><div id="title"></div><div id="content"></div>
<script>
const T = {json.dumps(labels, ensure_ascii=False)};
if (window.mermaid) mermaid.initialize({{startOnLoad:false, theme:"dark", securityLevel:"strict",
  themeVariables:{{fontFamily:"Segoe UI, sans-serif", fontSize:"15px"}},
  flowchart:{{useMaxWidth:false}}, sequence:{{useMaxWidth:false}}, class:{{useMaxWidth:false}}, state:{{useMaxWidth:false}}}});
let seq = 0;
function renderNote(p) {{
  const y = window.scrollY, my = ++seq;
  const title = document.getElementById("title");
  title.textContent = p.title; title.style.display = p.title ? "" : "none";
  const box = document.getElementById("content");
  box.innerHTML = p.html || `<p class="empty">${{T.empty}}</p>`;
  box.querySelectorAll("a.copy").forEach(a => a.textContent = T.copy);
  box.querySelectorAll(".callout-title").forEach(s => s.textContent = T[s.dataset.kind] || "");
  if (window.katex) box.querySelectorAll(".math").forEach(el => {{
    try {{ katex.render(el.textContent, el, {{displayMode: el.classList.contains("block"), throwOnError: false}}); }} catch (e) {{}}
  }});
  const nodes = [...box.querySelectorAll("pre.mermaid")];
  if (nodes.length && window.mermaid) mermaid.run({{nodes, suppressErrors: true}}).catch(() => {{}});
  window.scrollTo(0, y);
}}
function copied(n) {{
  const a = document.querySelectorAll("a.copy")[n];
  if (a) {{ a.textContent = T.copied; setTimeout(() => a.textContent = T.copy, 1400); }}
}}
function scrollToLine(n, flash) {{
  let best = null;
  for (const el of document.querySelectorAll("#content [data-line]")) {{
    if (+el.dataset.line <= n) best = el; else break;
  }}
  if (!best) {{ window.scrollTo({{top: 0, behavior: "smooth"}}); return; }}
  best.scrollIntoView({{block: "start", behavior: "smooth"}});
  if (flash) {{ best.classList.remove("flash"); void best.offsetWidth; best.classList.add("flash"); }}
}}
</script></body></html>"""


if HAS_WEBENGINE:
    class _Page(QWebEnginePage):
        action = pyqtSignal(str, str)   # ("copy" | "task", argument)
        link = pyqtSignal(QUrl)

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):
            if url.scheme() == "studyhub":
                kind, _, arg = url.path().partition("/")
                self.fragment = url.fragment()
                self.action.emit(kind, arg)
                return False
            if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
                self.link.emit(url)
                return False
            return True

        def javaScriptConsoleMessage(self, level, message, line, source):  # keep the console quiet
            pass


class NotePreview(QStackedWidget):
    """set_note(title, markdown) / scroll_to_line(n). Emits task_toggled(line) and link_opened(url)."""

    task_toggled = pyqtSignal(int)
    link_opened = pyqtSignal(str)
    note_link = pyqtSignal(str)   # [[title]] clicked
    tag_clicked = pyqtSignal(str)
    pdf_link = pyqtSignal(str, int)   # file, page

    def __init__(self, parent=None):
        super().__init__(parent)
        self._codes: list[str] = []
        self._pending: dict | None = None
        self._ready = False
        self.web = None
        if HAS_WEBENGINE:
            self.web = QWebEngineView(self)
            page = _Page(self.web)
            self.web.setPage(page)
            page.setBackgroundColor(Qt.GlobalColor.transparent)
            s = page.settings()
            s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)  # web images
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
            page.action.connect(self._on_action)
            page.link.connect(self._on_link)
            self.web.loadFinished.connect(self._on_loaded)
            self.web.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.web.customContextMenuRequested.connect(self._menu)
            self.addWidget(self.web)
            self.web.setHtml(_shell(), QUrl.fromLocalFile(str(DATA_DIR) + "/"))
        else:
            self.text = QTextBrowser(self)
            self.text.setOpenExternalLinks(True)
            self.addWidget(self.text)

    # --------------------------------------------------------------- api
    def set_note(self, title: str, markdown: str, titles=()) -> None:
        body, self._codes = render(markdown, titles)
        payload = {"title": title, "html": body}
        if self.web is None:
            self.text.setHtml(f"<h1>{title}</h1>{body}" if title else body)
        elif self._ready:
            self.web.page().runJavaScript(f"renderNote({json.dumps(payload, ensure_ascii=False)})")
        else:
            self._pending = payload

    def scroll_to_line(self, line: int, flash: bool = False) -> None:
        if self.web is not None and self._ready:
            self.web.page().runJavaScript(f"scrollToLine({int(line)}, {str(bool(flash)).lower()})")

    # ----------------------------------------------------------- internals
    def _on_loaded(self, ok: bool) -> None:
        self._ready = True
        if self._pending is not None:
            payload, self._pending = self._pending, None
            self.web.page().runJavaScript(f"renderNote({json.dumps(payload, ensure_ascii=False)})")

    @property
    def _fragment(self) -> str:
        frag = getattr(self.web.page(), "fragment", "") if self.web else ""
        return frag if frag.isdigit() else ""

    def _on_action(self, kind: str, arg: str) -> None:
        if kind == "copy" and arg.isdigit() and int(arg) < len(self._codes):
            QApplication.clipboard().setText(self._codes[int(arg)].rstrip("\n"))
            self.web.page().runJavaScript(f"copied({int(arg)})")
        elif kind == "task" and arg.isdigit():
            self.task_toggled.emit(int(arg))
        elif kind == "note":
            self.note_link.emit(unquote(arg))
        elif kind == "tag":
            self.tag_clicked.emit(unquote(arg))
        elif kind == "pdf":  # studyhub:pdf/<path>#<page>
            self.pdf_link.emit(unquote(arg), max(0, int(self._fragment or 1) - 1))

    def _on_link(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
        elif url.isLocalFile():
            self.link_opened.emit(url.toLocalFile())

    # -------------------------------------------------------------- export
    def export_pdf(self, path: str, done) -> None:
        """Prints the current preview (light print colors) to a PDF; done(ok) when finished."""
        if self.web is None:
            return done(False)
        from PyQt6.QtCore import QMarginsF
        from PyQt6.QtGui import QPageLayout, QPageSize

        def finished(file_path, ok):
            self.web.page().pdfPrintingFinished.disconnect(finished)
            done(ok)
        self.web.page().pdfPrintingFinished.connect(finished)
        layout = QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                             QMarginsF(16, 16, 16, 16), QPageLayout.Unit.Millimeter)
        self.web.page().printToPdf(path, layout)

    def export_html(self, title: str, done) -> None:
        """Calls done(html) with one self-contained file: rendered math/diagrams, fonts and images inside."""
        if self.web is None:
            return done("")

        def got(body):
            done(standalone_html(title, body or ""))
        self.web.page().runJavaScript("document.getElementById('content').innerHTML", got)

    def _menu(self, pos) -> None:
        from app.ui.theme import Icons
        from app.ui.widgets.common import add_action, make_menu
        page = self.web.page()
        menu = make_menu(self)
        if page.hasSelection():
            add_action(menu, Icons.COPY, tr("copy"), lambda: page.triggerAction(QWebEnginePage.WebAction.Copy))
        add_action(menu, Icons.SELECT, tr("select_all"), lambda: page.triggerAction(QWebEnginePage.WebAction.SelectAll))
        menu.exec(self.web.mapToGlobal(pos))


def _data_uri(path: Path) -> str:
    known = {".woff2": "font/woff2", ".woff": "font/woff", ".svg": "image/svg+xml"}  # unknown to Windows' registry
    mime = known.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def standalone_html(title: str, body: str) -> str:
    """A single .html file that looks like the preview, readable anywhere (no internet, no Study Hub)."""
    katex_css = (VENDOR / "katex" / "katex.min.css").read_text(encoding="utf-8")
    fonts = VENDOR / "katex" / "fonts"
    katex_css = re.sub(r"url\(fonts/([\w\-]+\.woff2)\)",
                       lambda m: f"url({_data_uri(fonts / m.group(1))})" if (fonts / m.group(1)).exists() else m.group(0),
                       katex_css)

    def inline_image(m):
        src = unquote(m.group(2))
        local = DATA_DIR / src if not re.match(r"^[a-z]+:", src) else None
        if local is not None and local.is_file():
            return f'{m.group(1)}{_data_uri(local)}"'
        return m.group(0)
    body = re.sub(r'(<img[^>]*?src=")([^"]+)"', inline_image, body)
    body = re.sub(r'href="studyhub:[^"]*"', 'href="#"', body)  # in-app actions mean nothing outside
    safe_title = title.replace("<", "&lt;").replace(">", "&gt;")
    return (f'<!doctype html><html lang="ar"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width, initial-scale=1"><title>{safe_title or "Note"}</title>'
            f'<style>{katex_css}</style><style>{_css()}\nhtml,body{{background:{"#F6F8FC" if Palette.light() else "#0f1320"};}}'
            f'body{{max-width:860px;margin:0 auto;padding:32px 24px 80px;}}a.copy{{display:none}}</style></head>'
            f'<body><div id="title">{safe_title}</div><div id="content">{body}</div></body></html>')
