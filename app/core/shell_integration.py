"""
Windows Explorer integration (per-user, no admin rights needed):

* "Study Hub" sub-menu in the right-click menu of files, folders, folder background
  and the desktop:
      files   : Pin to desktop · Tag · Copy to subject ▸ · Move to subject ▸ · Note about it · Send to phone · Quick note
      folders : Pin to desktop · Tag · Link to subject ▸ · Copy to subject ▸ · Quick note
      empty   : Quick note · Paste clipboard to Study Hub · Note / link from clipboard · Pin this folder · Open
  The subject sub-menus are rebuilt whenever subjects change.
* "Send to > Study Hub" shortcut: handles many files in one go.
* "Start with Windows" entry in the HKCU Run key.

On Windows 11 these entries appear under "Show more options" (Shift+F10).
Everything is written under HKEY_CURRENT_USER and removed cleanly on disable.
"""
from __future__ import annotations

import os
import subprocess
import winreg
from pathlib import Path

from app.config import APP_ID, APP_NAME, launch_command
from app.core.i18n import tr

HKCU = winreg.HKEY_CURRENT_USER
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
INSTALLED_EXE = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "StudyHub" / "StudyHub.exe"


def _launch() -> list[str]:
    """What Windows should start: always the installed copy when there is one (whichever copy writes the
    entry), otherwise the copy that is running now."""
    return [str(INSTALLED_EXE)] if INSTALLED_EXE.is_file() else launch_command()


_ROOTS = {
    "file": rf"Software\Classes\*\shell\{APP_ID}",
    "folder": rf"Software\Classes\Directory\shell\{APP_ID}",
    "background": rf"Software\Classes\Directory\Background\shell\{APP_ID}",
    "desktop": rf"Software\Classes\DesktopBackground\shell\{APP_ID}",
}

# (sub-key, translation key, command-line arguments). "@copy"/"@move"/"@link" = per-subject sub-menu
_ENTRIES = {
    "file": [
        ("01pin", "ctx_pin_file", '--add-path "%1"'),
        ("02tag", "ctx_tag_file", '--track-path "%1"'),
        ("03copy", "ctx_copy_to", "@copy"),
        ("04move", "ctx_move_to", "@move"),
        ("05note", "ctx_note_for", '--note-for "%1"'),
        ("06phone", "ctx_send_phone", '--send-phone "%1"'),
        ("07quick", "ctx_quick_note", "--quick-note"),
        ("08vault", "ctx_passwords", "--app vault"),
    ],
    "folder": [
        ("01pin", "ctx_pin_folder", '--add-path "%1"'),
        ("02tag", "ctx_tag_file", '--track-path "%1"'),
        ("03link", "ctx_link_to", "@link"),
        ("04copy", "ctx_copy_to", "@copy"),
        ("05quick", "ctx_quick_note", "--quick-note"),
        ("06vault", "ctx_passwords", "--app vault"),
    ],
    "background": [
        ("01quick", "ctx_quick_note", "--quick-note"),
        ("02paste", "ctx_paste", "--paste"),
        ("03clipnote", "ctx_clip_note", "--clipboard-note"),
        ("04cliplink", "ctx_clip_link", "--clipboard-link"),
        ("05pin", "ctx_pin_this_folder", '--add-path "%V"'),
        ("06open", "ctx_open", "--show"),
        ("07vault", "ctx_passwords", "--app vault"),
    ],
    "desktop": [
        ("01quick", "ctx_quick_note", "--quick-note"),
        ("02paste", "ctx_paste", "--paste"),
        ("03clipnote", "ctx_clip_note", "--clipboard-note"),
        ("04cliplink", "ctx_clip_link", "--clipboard-link"),
        ("05open", "ctx_open", "--show"),
        ("06vault", "ctx_passwords", "--app vault"),
    ],
}
_SUBJECT_ARGS = {"@copy": '--copy-to {id} "%1"', "@move": '--move-to {id} "%1"', "@link": '--link-to {id} "%1"'}


def _base_command() -> str:
    return " ".join(f'"{part}"' for part in _launch())


def _set(key, name: str, value: str) -> None:
    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _delete_tree(path: str) -> None:
    """winreg can only delete empty keys, so delete children first."""
    try:
        with winreg.OpenKey(HKCU, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(path + "\\" + child)
        winreg.DeleteKey(HKCU, path)
    except FileNotFoundError:
        pass


def _verb(path: str, label: str, command: str | None, icon: str | None = None, cascade: bool = False) -> None:
    with winreg.CreateKeyEx(HKCU, path, 0, winreg.KEY_WRITE) as key:
        _set(key, "MUIVerb", label)
        _set(key, "MultiSelectModel", "Player")  # also shown when many files are selected
        if icon:
            _set(key, "Icon", icon)
        if cascade:
            _set(key, "SubCommands", "")
    if command:
        with winreg.CreateKeyEx(HKCU, path + r"\command", 0, winreg.KEY_WRITE) as key:
            _set(key, "", command)


# ----------------------------------------------------------------- context menu
def register_context_menu(icon_path: str | None = None, subjects: list[tuple[int, str, bool]] | None = None) -> None:
    """subjects: (id, name, has_folder). Subjects without a folder only appear in 'Link to subject'."""
    unregister_context_menu()  # start clean (labels/subjects may have changed)
    base = _base_command()
    subjects = subjects or []
    for kind, root in _ROOTS.items():
        _verb(root, APP_NAME, None, icon_path, cascade=True)
        for sub, label_key, args in _ENTRIES[kind]:
            path = rf"{root}\shell\{sub}"
            if args.startswith("@"):
                targets = [s for s in subjects if s[2] or args == "@link"]
                if not targets:
                    continue
                _verb(path, tr(label_key), None, cascade=True)
                for sid, name, _ in targets:
                    _verb(rf"{path}\shell\s{sid:04d}", name, f"{base} {_SUBJECT_ARGS[args].format(id=sid)}")
            else:
                _verb(path, tr(label_key), f"{base} {args}")
    _create_send_to(icon_path)


def unregister_context_menu() -> None:
    for root in _ROOTS.values():
        _delete_tree(root)
    _remove_send_to()


def is_context_menu_registered() -> bool:
    try:
        winreg.CloseKey(winreg.OpenKey(HKCU, _ROOTS["file"]))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------- Send to
def _send_to_path() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "SendTo" / f"{APP_NAME}.lnk"


def _create_send_to(icon_path: str | None) -> None:
    """Creates the .lnk with Windows' own COM object (via PowerShell, no extra packages)."""
    target, *rest = _launch()
    args = " ".join(f'"{a}"' for a in rest) + " --send-to"
    link = _send_to_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
              f"$s.TargetPath='{target}';$s.Arguments='{args}';"
              + (f"$s.IconLocation='{icon_path}';" if icon_path else "") + "$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _remove_send_to() -> None:
    try:
        _send_to_path().unlink()
    except FileNotFoundError:
        pass


# ------------------------------------------------------------- PDF "Open with"
PDF_PROGID = "StudyHub.PdfDocument"


def register_pdf_handler(enabled: bool) -> None:
    """Adds "Study Hub PDF" to Explorer's "Open with" for .pdf (making it the default stays the user's
    choice: Windows protects defaults — Settings › Default apps)."""
    progid = rf"Software\Classes\{PDF_PROGID}"
    open_with = r"Software\Classes\.pdf\OpenWithProgids"
    # Windows' "Open with" list remembers programs by file name ("StudyHub.exe"); without this key it
    # resolves the name to whichever copy it saw last (once: the dist\ build folder instead of the install)
    app_key = r"Software\Classes\Applications\StudyHub.exe"
    if enabled:
        exe = _launch()[0]
        icon = f'"{exe}",0' if exe.lower().endswith("studyhub.exe") else ""
        pdf_ico = Path(exe).parent / "icons" / "pdf.ico"  # the reader's own red icon on .pdf files
        if icon and pdf_ico.is_file():
            icon = f'"{pdf_ico}",0'
        with winreg.CreateKeyEx(HKCU, progid, 0, winreg.KEY_WRITE) as key:
            _set(key, "", "PDF — Study Hub")
            _set(key, "FriendlyTypeName", "PDF — Study Hub")
        if icon:
            with winreg.CreateKeyEx(HKCU, progid + r"\DefaultIcon", 0, winreg.KEY_WRITE) as key:
                _set(key, "", icon)
        with winreg.CreateKeyEx(HKCU, progid + r"\shell\open", 0, winreg.KEY_WRITE) as key:
            _set(key, "FriendlyAppName", tr("pdf_open_with_label"))
        with winreg.CreateKeyEx(HKCU, progid + r"\shell\open\command", 0, winreg.KEY_WRITE) as key:
            _set(key, "", f'{_base_command()} --pdf "%1"')
        with winreg.CreateKeyEx(HKCU, open_with, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, PDF_PROGID, 0, winreg.REG_NONE, b"")
        if icon:  # only for the exe (not python main.py)
            with winreg.CreateKeyEx(HKCU, app_key, 0, winreg.KEY_WRITE) as key:
                _set(key, "FriendlyAppName", tr("pdf_open_with_label"))
            with winreg.CreateKeyEx(HKCU, app_key + r"\DefaultIcon", 0, winreg.KEY_WRITE) as key:
                _set(key, "", icon)
            with winreg.CreateKeyEx(HKCU, app_key + r"\SupportedTypes", 0, winreg.KEY_WRITE) as key:
                _set(key, ".pdf", "")
            with winreg.CreateKeyEx(HKCU, app_key + r"\shell\open\command", 0, winreg.KEY_WRITE) as key:
                _set(key, "", f'{_base_command()} --pdf "%1"')
    else:
        _delete_tree(progid)
        _delete_tree(app_key)
        try:
            with winreg.OpenKey(HKCU, open_with, 0, winreg.KEY_WRITE) as key:
                winreg.DeleteValue(key, PDF_PROGID)
        except OSError:
            pass
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass


# ------------------------------------------------------------------- autostart
def set_autostart(enabled: bool, show_window: bool = True) -> None:
    """show_window=False starts hidden in the tray (--minimized), True opens the window."""
    with winreg.CreateKeyEx(HKCU, RUN_KEY, 0, winreg.KEY_WRITE) as key:
        if enabled:
            _set(key, APP_ID, _base_command() + ("" if show_window else " --minimized"))
        else:
            try:
                winreg.DeleteValue(key, APP_ID)
            except FileNotFoundError:
                pass


def owned_by_installed_copy() -> bool:
    """True when Study Hub is installed and this is another copy (the dist build folder, python main.py,
    a copy on a USB stick...): such a copy must not re-point autostart / the Explorer entries at itself —
    otherwise Windows opens that copy at the next start instead of the installed one (it happened)."""
    if not INSTALLED_EXE.is_file():
        return False  # not installed: whichever copy runs is the one to use
    try:
        me = Path(launch_command()[0]).resolve()
        return me != INSTALLED_EXE.resolve()
    except OSError:
        return True


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(HKCU, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_ID)
            return True
    except OSError:
        return False
