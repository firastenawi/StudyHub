"""
Every Study Hub feature as an "app": what the Start menu lists, what the taskbar pins and shows while it runs,
what `StudyHub.exe --app KEY` opens and what gets its own Start-menu shortcut and Windows taskbar button.

kind:
  page    a page in its own window (app_window.AppWindow) — data shared with everything else
  timer   the focus timer window
  lab     the lab recorder
  pdf     the PDF reader (one window per file)
  action  a tool that does one thing (quick note, screenshot…) — listed in Start, never "running"
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap

from app.core.i18n import tr
from app.ui.theme import Icons, icon_font, qcolor

# groups in the Start menu's "All apps" list, in this order
GROUPS = ["study", "files", "tools", "phone", "system"]


@dataclass
class AppInfo:
    key: str
    glyph: str
    color: str          # tile colour (rounded square, white glyph — Windows 11 style)
    title_key: str
    desc_key: str
    group: str
    kind: str = "page"
    action: str = ""    # controller method for kind "action"
    pinned: bool = False  # on the taskbar by default
    extra: dict = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.title_key if self.kind == "item" else tr(self.title_key)

    @property
    def description(self) -> str:
        if self.kind == "item":
            return self.extra["item"]["target"]
        return tr(self.desc_key)

    @property
    def app_id(self) -> str:
        """Windows taskbar identity (also set on its Start-menu shortcut by the installer)."""
        return f"StudyHub.App.{self.key}"

    def icon(self) -> QIcon:
        if self.kind == "item":
            from app.ui.widgets.tiles import item_pixmap
            return QIcon(item_pixmap(self.extra["item"]))
        return app_tile_icon(self.color, self.glyph)

    def pixmap(self, size: int):
        """The picture the taskbar / Start draw for this app."""
        if self.kind == "item":
            from app.ui.widgets.tiles import item_pixmap
            return item_pixmap(self.extra["item"]).scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                                          Qt.TransformationMode.SmoothTransformation)
        return app_tile_pixmap(self.color, self.glyph, size)


BUILTIN = [
    AppInfo("today", Icons.HOME, "#7C5CFF", "app_today", "appdesc_today", "study", pinned=True),
    AppInfo("study", Icons.STUDY, "#4F7DF3", "nav_study", "appdesc_study", "study", pinned=True),
    AppInfo("notes", Icons.NOTES, "#F5A524", "nav_notes", "appdesc_notes", "study", pinned=True),
    AppInfo("timer", Icons.TIMER, "#E5484D", "app_timer", "appdesc_timer", "study", kind="timer", pinned=True),
    AppInfo("lab", Icons.CHECKLIST, "#12A594", "lab_app", "appdesc_lab", "study", kind="lab", pinned=True),
    AppInfo("pdf", Icons.DOCUMENT, "#D93036", "pdf_app_name", "appdesc_pdf", "files", kind="pdf", pinned=True),
    AppInfo("files", Icons.FOLDER, "#3E8EDE", "nav_files", "appdesc_files", "files", pinned=True),
    AppInfo("explorer", Icons.OPEN_FOLDER, "#2F6FB8", "explorer", "appdesc_explorer", "files", kind="action",
            action="open_explorer"),
    AppInfo("storage", Icons.DRIVE, "#6E56CF", "nav_storage", "appdesc_storage", "files"),
    AppInfo("clock", Icons.CLOCK, "#0091FF", "nav_clock", "appdesc_clock", "tools"),
    AppInfo("vault", Icons.KEY, "#30A46C", "nav_vault", "appdesc_vault", "tools"),
    AppInfo("quick_note", Icons.EDIT, "#F76B15", "quick_note", "appdesc_quick_note", "tools", kind="action",
            action="open_quick_note"),
    AppInfo("screenshot", Icons.PREVIEW, "#D6409F", "hk_screenshot", "appdesc_screenshot", "tools", kind="action",
            action="take_screenshot"),
    AppInfo("clipboard", Icons.PASTE, "#8E4EC6", "hk_clipboard_history", "appdesc_clipboard", "tools",
            kind="action", action="open_clipboard_history"),
    AppInfo("terminal", Icons.KEYBOARD, "#334155", "app_terminal", "appdesc_terminal", "tools", kind="terminal"),
    AppInfo("snippets", Icons.PASTE, "#0E7490", "snip_title", "appdesc_snippets", "tools"),
    AppInfo("phone", Icons.PHONE, "#00A2C7", "nav_phone", "appdesc_phone", "phone", pinned=True),
    AppInfo("settings", Icons.SETTINGS, "#687076", "nav_settings", "appdesc_settings", "system", pinned=True),
]

# plugin pages: their own colour / group / description (unknown plugins get a neutral tile)
PLUGIN_STYLE = {
    "infra": ("#E54666", "system", "appdesc_infra"),
    "projects": ("#46A758", "system", "appdesc_projects"),
}


def all_apps(ctl) -> list[AppInfo]:
    apps = list(BUILTIN)
    for key, glyph, title_key, _factory in ctl.plugins.pages():
        color, group, desc = PLUGIN_STYLE.get(key, ("#5B6B8C", "system", "appdesc_plugin"))
        apps.append(AppInfo(key, glyph, color, title_key, desc, group))
    return apps


def find(ctl, key: str) -> AppInfo | None:
    if key.startswith("item:"):  # a program / website / file you pinned on the taskbar
        return item_app(ctl, key)
    return next((a for a in all_apps(ctl) if a.key == key), None)


def item_app(ctl, key: str) -> AppInfo | None:
    try:
        item = ctl.db.get_item(int(key[5:]))
    except (ValueError, TypeError):
        return None
    if not item:
        return None
    glyph = {"url": Icons.GLOBE, "folder": Icons.FOLDER, "file": Icons.DOCUMENT}.get(item["kind"], Icons.APPS)
    info = AppInfo(key, glyph, "#5B6B8C", item["title"], "", "files", kind="item")
    info.extra["item"] = item
    return info


def plugin_factory(ctl, key: str):
    return next((f for k, _g, _t, f in ctl.plugins.pages() if k == key), None)


# ----------------------------------------------------------------- icons
def app_tile_pixmap(color: str, glyph: str, size: int = 256) -> QPixmap:
    """Rounded square in the app's colour with a white glyph and a soft top light."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    m = size * 0.06
    rect = QRectF(m, m, size - 2 * m, size - 2 * m)
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.22, size * 0.22)
    base = QColor(color)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0, base.lighter(118))
    grad.setColorAt(1, base.darker(112))
    p.fillPath(path, grad)
    gloss = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gloss.setColorAt(0, qcolor("#FFFFFF", 0.22))
    gloss.setColorAt(0.55, qcolor("#FFFFFF", 0.0))
    p.fillPath(path, gloss)
    font = icon_font()
    font.setPixelSize(max(6, int(size * 0.46)))
    p.setFont(font)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)
    p.end()
    return pm


_ICON_CACHE: dict[tuple[str, str], QIcon] = {}


def app_tile_icon(color: str, glyph: str) -> QIcon:
    key = (color, glyph)
    if key not in _ICON_CACHE:
        icon = QIcon()
        for size in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256):
            icon.addPixmap(app_tile_pixmap(color, glyph, size))
        _ICON_CACHE[key] = icon
    return _ICON_CACHE[key]


def pdf_icon() -> QIcon:
    info = next(a for a in BUILTIN if a.key == "pdf")
    return info.icon()
