"""
Design system: color tokens, fonts, icon glyphs and the global Qt stylesheet.

Everything visual derives from here, so re-skinning the app (e.g. changing the
accent color in Settings) is just regenerating one stylesheet. Dark / light: Palette holds the
current mode's colors; "ink" is the color translucent overlays are made of (white on dark,
near-black on light), so widgets draw ink(0.06) instead of a hard-coded white.
Icons are glyphs of the "Segoe Fluent Icons / Segoe MDL2 Assets" fonts that ship
with Windows 10/11: crisp at any size and no image files to manage.
"""
from __future__ import annotations

from string import Template

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap

# accent name -> (primary, secondary) used for gradients
ACCENTS: dict[str, tuple[str, str]] = {
    "violet": ("#7C5CFF", "#22D3EE"),
    "ocean": ("#3B82F6", "#06B6D4"),
    "emerald": ("#10B981", "#84CC16"),
    "sunset": ("#F97316", "#F43F5E"),
    "rose": ("#EC4899", "#8B5CF6"),
}


_MODES = {
    "dark": dict(bg="#0A0D14", surface_solid="#141926", text="#E8EBF4", text_dim="#8C95A8",
                 text_faint="#5B6375", success="#34D399", warning="#FBBF24", danger="#F87171",
                 info="#60A5FA", ink="#FFFFFF", chrome="#080B12", deep="#1E2A78"),
    "light": dict(bg="#EDF0F7", surface_solid="#FFFFFF", text="#141A2C", text_dim="#4B556B",
                  text_faint="#7B8499", success="#059669", warning="#B45309", danger="#DC2626",
                  info="#2563EB", ink="#0F172A", chrome="#FFFFFF", deep="#C7D2FE"),
}


class Palette:
    mode = "dark"
    bg = "#0A0D14"
    surface_solid = "#141926"
    text = "#E8EBF4"
    text_dim = "#8C95A8"
    text_faint = "#5B6375"
    success = "#34D399"
    warning = "#FBBF24"
    danger = "#F87171"
    info = "#60A5FA"
    ink = "#FFFFFF"        # overlays / hairlines are made of this
    chrome = "#080B12"     # top bar / sidebar tint
    deep = "#1E2A78"       # third wallpaper color

    @classmethod
    def apply(cls, mode: str) -> None:
        """dark | light | system (follows Windows' app mode)."""
        if mode == "system":
            mode = "light" if windows_light_mode() else "dark"
        cls.mode = mode if mode in _MODES else "dark"
        for key, value in _MODES[cls.mode].items():
            setattr(cls, key, value)

    @classmethod
    def light(cls) -> bool:
        return cls.mode == "light"


def windows_light_mode() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
    except OSError:
        return False


class Theme:
    accent_name = "violet"

    @classmethod
    def set_accent(cls, name: str) -> None:
        cls.accent_name = name if name in ACCENTS else "violet"

    @classmethod
    def accent(cls) -> str:
        return ACCENTS[cls.accent_name][0]

    @classmethod
    def accent2(cls) -> str:
        return ACCENTS[cls.accent_name][1]


class Icons:
    HOME = ""
    # phone dashboard
    MOON = ""
    FLASHLIGHT = ""
    WIFI = ""
    BLUETOOTH = ""
    RINGER = ""
    VIBRATE = ""
    MUTE = ""
    BATTERY = ""
    CHARGING = ""
    SEND = ""
    REPLY = ""
    MORE = ""
    CAMERA = ""
    STUDY = ""
    FOLDER = ""
    OPEN_FOLDER = ""
    NOTES = ""
    EDIT = ""
    SETTINGS = ""
    SEARCH = ""
    ADD = ""
    GLOBE = ""
    APPS = ""
    CLOCK = ""
    POWER = ""
    CLOSE = ""
    MINIMIZE = ""
    FULLSCREEN = ""
    WINDOWED = ""
    LIGHTBULB = ""
    CHECKLIST = ""
    BOOK = ""
    EDUCATION = ""
    LINK = ""
    PIN = ""
    DELETE = ""
    REFRESH = ""
    TIMER = ""
    INBOX = ""
    TAG = ""
    SAVE = ""
    COPY = ""
    PASTE = ""
    KEYBOARD = ""
    ADMIN = ""
    MENU = ""
    ACCEPT = ""
    INFO = ""
    WARNING = ""
    COLOR = ""
    LIGHTNING = ""
    DOCUMENT = ""
    CALENDAR_DAY = ""
    FLAG = ""
    MOVE = ""
    LOCATION = ""
    STAR = ""
    SELECT = ""
    CHEVRON = ""
    UP = ""
    BACK = ""
    FORWARD = ""
    VIEW_LIST = ""
    VIEW_GRID = ""
    PREVIEW = ""
    ARCHIVE = ""
    DRIVE = ""
    CUT = ""
    NEW_FOLDER = ""
    DESKTOP = ""
    KEY = ""
    EYE = ""
    EYE_OFF = ""
    SHIELD = ""
    PERSON = ""
    BELL = ""
    LOCK = ""
    PLAY = ""
    VOLUME = ""
    MUSIC = ""
    DOWNLOAD = ""
    UNDO = ""
    SYNC = ""
    PHONE = ""
    ATTACH = ""
    FOCUS = ""
    OUTLINE = ""


# ------------------------------------------------------------------ colors
def qcolor(hex_color: str, alpha: float = 1.0) -> QColor:
    color = QColor(hex_color)
    color.setAlphaF(max(0.0, min(1.0, alpha)))
    return color


def ink(alpha: float) -> QColor:
    """Translucent overlay color for the current mode (white on dark, near-black on light)."""
    return qcolor(Palette.ink, alpha)


def ink_css(alpha: float) -> str:
    return rgba(Palette.ink, alpha)


def rgba(hex_color: str, alpha: float) -> str:
    """QSS color with alpha. Qt reliably accepts alpha as an integer 0-255."""
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {int(max(0, min(1, alpha)) * 255)})"


def lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


# ------------------------------------------------------------------- fonts
UI_FAMILIES_LTR = ["Segoe UI Variable Text", "Segoe UI", "Tahoma", "Arial"]
UI_FAMILIES_RTL = ["Segoe UI", "Tahoma", "Arial"]  # best Arabic shaping on Windows
ICON_FAMILIES = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]
_ui_families = UI_FAMILIES_LTR


def set_rtl_fonts(rtl: bool) -> None:
    global _ui_families
    _ui_families = UI_FAMILIES_RTL if rtl else UI_FAMILIES_LTR


def ui_font(pt: float = 10.0, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies(_ui_families)
    font.setPointSizeF(pt)
    font.setWeight(weight)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


def icon_font(pt: float = 12.0) -> QFont:
    font = QFont()
    font.setFamilies(ICON_FAMILIES)
    font.setPointSizeF(pt)
    return font


# ------------------------------------------------------------------- icons
def glyph_pixmap(glyph: str, color: str | QColor, px: int = 20, scale: float = 2.0) -> QPixmap:
    pm = QPixmap(int(px * scale), int(px * scale))
    pm.setDevicePixelRatio(scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    font = icon_font()
    font.setPixelSize(int(px * 0.8))
    p.setFont(font)
    p.setPen(QColor(color))
    p.drawText(QRectF(0, 0, px, px), Qt.AlignmentFlag.AlignCenter, glyph)
    p.end()
    return pm


def glyph_icon(glyph: str, color: str | QColor | None = None, px: int = 20) -> QIcon:
    return QIcon(glyph_pixmap(glyph, Palette.text_dim if color is None else color, px))


def app_icon_pixmap(size: int = 256) -> QPixmap:
    """The Study Hub logo: gradient squircle + graduation cap glyph."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(size * 0.04, size * 0.04, size * 0.92, size * 0.92)
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.26, size * 0.26)
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0, QColor(Theme.accent()))
    grad.setColorAt(1, QColor(Theme.accent2()))
    p.fillPath(path, grad)
    gloss = QLinearGradient(rect.topLeft(), rect.bottomLeft())  # soft glossy highlight
    gloss.setColorAt(0, qcolor("#FFFFFF", 0.28))
    gloss.setColorAt(0.5, qcolor("#FFFFFF", 0.0))
    p.fillPath(path, gloss)
    font = icon_font()
    font.setPixelSize(int(size * 0.5))
    p.setFont(font)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, Icons.EDUCATION)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(size))
    return icon


# -------------------------------------------------------------- stylesheet
_QSS = Template("""
* { outline: 0; }
QWidget { color: $text; }
QToolTip { background: $surface_solid; color: $text; border: 1px solid $border_strong;
           border-radius: 8px; padding: 6px 10px; }

QFrame#Card { background: $surface; border: 1px solid $border; border-radius: 20px; }
QFrame#Card[hoverable="true"]:hover { border-color: $border_strong; background: $surface_hover; }
QFrame#DialogFrame { background: $surface_solid; border: 1px solid $border_strong; border-radius: 22px; }
QFrame#TopBar { background: $chrome; border: none; border-bottom: 1px solid $border; }
QFrame#TopBar[floating="true"] { background: $chrome_solid; border-bottom: 1px solid $accent_border; }
QFrame#DeskBar { background: $chrome_solid; border: 1px solid $border_strong; border-radius: 16px; }
QFrame#StartExplain { background: $surface; border: 1px solid $border; border-radius: 12px; }
QLineEdit#StartSearch { border-radius: 20px; padding: 0 14px; }
QPushButton#TaskClock { background: transparent; border: none; border-radius: 9px; padding: 2px 10px; color: $text; }
QPushButton#TaskClock:hover { background: $surface_hover; }
QLabel#ClockBadge { background: $accent; color: white; border-radius: 8px; }
QListWidget#NotifList::item { padding: 6px 8px; border-radius: 10px; }
QFrame#SysSection { background: $surface; border: 1px solid $border; border-radius: 12px; }
QTabWidget#TermTabs::pane { border: none; }
QTabWidget#TermTabs QTabBar::tab { background: transparent; color: $text_dim; padding: 7px 10px 7px 14px; margin: 2px 2px 4px 2px;
    border-radius: 9px; min-width: 110px; }
QTabWidget#TermTabs QTabBar::tab:selected { background: $surface; color: $text; border: 1px solid $border; }
QTabWidget#TermTabs QTabBar::tab:hover:!selected { background: $surface_hover; }
QListWidget#AddList::item { border: none; margin: 1px 0; }
QListWidget#AddList::item:selected, QListWidget#AddList::item:hover { background: transparent; }
QTableWidget { background: $input; color: $text; border: 1px solid $border; border-radius: 10px; gridline-color: $border; }
QTableWidget::item:selected { background: $accent_soft; color: $text; }
QHeaderView { background: $surface_solid; border: none; }
QHeaderView::section { background: $surface_solid; color: $text_dim; border: none; border-bottom: 1px solid $border; padding: 6px; }
QTableCornerButton::section { background: $surface; border: none; }
QListWidget#SettingsNav::item { padding: 0 10px; border-radius: 10px; }
QListWidget#SettingsNav::item:selected { background: $accent_soft; color: $text; }
QListWidget#PinList::item { padding: 0 8px; border-radius: 10px; }
QFrame#Divider { background: $border; border: none; max-height: 1px; min-height: 1px; }

QLabel { background: transparent; }
QLabel#Dim { color: $text_dim; }
QLabel#Faint { color: $text_faint; }
QLabel#Accent { color: $accent; }
QLabel#Badge { background: $accent_soft; color: $text; border-radius: 9px; padding: 1px 9px; }
QLabel#Keycap { background: $surface; color: $text_dim; border: 1px solid $border_strong;
                border-radius: 6px; padding: 1px 7px; }

QPushButton { background: $surface; border: 1px solid $border; border-radius: 11px;
              padding: 8px 16px; color: $text; }
QPushButton:hover { background: $surface_hover; border-color: $border_strong; }
QPushButton:pressed { background: $surface; }
QPushButton:disabled { color: $text_faint; }
QPushButton#Primary { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 $accent, stop:1 $accent2);
                      border: none; color: white; font-weight: 600; }
QPushButton#Primary:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 $accent_light, stop:1 $accent2); }
QPushButton#Primary:disabled { background: $surface; color: $text_faint; }
QPushButton#Danger { background: $danger_soft; border: 1px solid $danger_border; color: $danger; }
QPushButton#Danger:hover { background: $danger_border; }
QPushButton#Ghost { background: transparent; border: none; color: $text_dim; padding: 6px 10px; }
QPushButton#Ghost:hover { color: $text; background: $surface_hover; }
QPushButton#Chip { border-radius: 15px; padding: 5px 14px; color: $text_dim; }
QPushButton#Chip:hover { color: $text; }
QPushButton#Chip:checked { background: $accent_soft; border-color: $accent_border; color: $text; }
QPushButton#IconButton { background: transparent; border: none; border-radius: 10px; padding: 0; color: $text_dim; }
QPushButton#IconButton:hover { background: $surface_hover; color: $text; }
QPushButton#IconButton:checked { color: $accent; }
QPushButton#SearchPill { background: $surface; border: 1px solid $border; border-radius: 19px;
                         padding: 0 16px; color: $text_faint; text-align: left; }
QPushButton#SearchPill:hover { border-color: $accent_border; color: $text_dim; background: $surface_hover; }

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox {
    background: $input; border: 1px solid $border; border-radius: 11px; padding: 8px 12px;
    color: $text; selection-background-color: $accent; selection-color: white; }
QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover { border-color: $border_strong; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {
    border-color: $accent_border; background: $input_focus; }
QLineEdit#PaletteInput, QLineEdit#Bare { background: transparent; border: none; padding: 4px 2px; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: $surface_solid; border: 1px solid $border_strong;
    padding: 4px; selection-background-color: $accent_soft; outline: 0; }

QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 6px 2px; }
QScrollBar::handle:vertical { background: $scroll; border-radius: 3px; min-height: 40px; margin: 0 2px; }
QScrollBar::handle:vertical:hover { background: $scroll_hover; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QScrollBar:horizontal { height: 0; }

QMenu { background: $surface_solid; border: 1px solid $border_strong; border-radius: 12px; padding: 6px; }
QMenu::item { padding: 8px 30px 8px 12px; border-radius: 8px; color: $text; background: transparent; }
QMenu::item:selected { background: $accent_soft; }
QMenu::item:disabled { color: $text_faint; }
QMenu::separator { height: 1px; background: $border; margin: 5px 10px; }
QMenu::icon { padding-left: 10px; padding-right: 4px; }

QSpinBox, QTimeEdit, QDateEdit {
    background: $input; border: 1px solid $border; border-radius: 11px; padding: 7px 10px; color: $text;
    selection-background-color: $accent; }
QSpinBox:hover, QTimeEdit:hover, QDateEdit:hover { border-color: $border_strong; }
QSpinBox:focus, QTimeEdit:focus, QDateEdit:focus { border-color: $accent_border; background: $input_focus; }
QSpinBox::up-button, QSpinBox::down-button, QTimeEdit::up-button, QTimeEdit::down-button,
QDateEdit::up-button, QDateEdit::down-button { width: 0; border: none; }
QDateEdit::drop-down { border: none; width: 26px; }
QCalendarWidget QWidget { alternate-background-color: $surface_solid; background: $surface_solid; color: $text; }
QCalendarWidget QToolButton { color: $text; background: transparent; border: none; padding: 6px 10px; border-radius: 8px; }
QCalendarWidget QToolButton:hover { background: $surface_hover; }
QCalendarWidget QMenu { background: $surface_solid; }
QCalendarWidget QAbstractItemView:enabled { background: $surface_solid; color: $text;
    selection-background-color: $accent; selection-color: white; outline: 0; }
QCalendarWidget QAbstractItemView:disabled { color: $text_faint; }

QSlider::groove:horizontal { height: 6px; border-radius: 3px; background: $scroll; }
QSlider::sub-page:horizontal { border-radius: 3px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
    stop:0 $accent, stop:1 $accent2); }
QSlider::handle:horizontal { width: 18px; height: 18px; margin: -7px 0; border-radius: 9px; background: white; }
QCheckBox { spacing: 8px; }

QSplitter::handle { background: $border; }
QSplitter::handle:horizontal { width: 1px; }
QTextBrowser { background: transparent; border: none; color: $text; selection-background-color: $accent; }
QPlainTextEdit#Editor { background: transparent; border: none; padding: 4px 2px; }
QLineEdit#TitleEdit { background: transparent; border: none; padding: 2px 0; }

QListWidget { background: transparent; border: none; }
QListWidget::item { border-radius: 12px; padding: 0; margin: 1px 0; }
QListWidget::item:selected { background: $accent_soft; }
QListWidget::item:hover:!selected { background: $surface_hover; }
""")


def stylesheet() -> str:
    accent = Theme.accent()
    light = Palette.light()
    return _QSS.substitute(
        text=Palette.text, text_dim=Palette.text_dim, text_faint=Palette.text_faint,
        surface=rgba("#FFFFFF", 0.62) if light else ink_css(0.045),
        surface_hover=rgba("#FFFFFF", 0.85) if light else ink_css(0.08),
        surface_solid=Palette.surface_solid,
        chrome=rgba(Palette.chrome, 0.62 if light else 0.55), chrome_solid=rgba(Palette.chrome, 0.97),
        border=ink_css(0.10 if light else 0.08), border_strong=ink_css(0.18 if light else 0.15),
        input=rgba("#FFFFFF", 0.75) if light else ink_css(0.05),
        input_focus=rgba("#FFFFFF", 0.95) if light else ink_css(0.07),
        scroll=ink_css(0.16 if light else 0.12), scroll_hover=ink_css(0.30 if light else 0.24),
        accent=accent, accent2=Theme.accent2(), accent_light=QColor(accent).lighter(115).name(),
        accent_soft=rgba(accent, 0.22), accent_border=rgba(accent, 0.65),
        danger=Palette.danger, danger_soft=rgba(Palette.danger, 0.12), danger_border=rgba(Palette.danger, 0.35),
    )
