"""
Recent items, pinned items and quick tasks per app — shown when you right-click an app on Study Hub's taskbar and
on Windows' own taskbar (its "jump list", for the app's own taskbar button / pinned shortcut).

  pdf      recent PDFs (the reader's own list)         task: open a file
  notes    recently opened notes                        tasks: new note, today's note
  files    recently opened folders                      task: organizer
  lab      recent lab reports (notes)                   task: start a lab
  timer    —                                            task: start focusing
  study    —                                            tasks: new task, add a class
Pinned items stay at the top until unpinned. Windows' list is rebuilt a moment after something changes.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.core.i18n import tr
from app.core.logs import log

MAX_RECENT = 8

# app -> quick tasks (command keys of ctl.command_list())
TASKS = {
    "pdf": ["pdf_open_task", "pdf_library"],
    "notes": ["new_note", "daily_note", "quick_switcher"],
    "files": ["tab_organizer", "tags"],
    "lab": ["lab_start"],
    "timer": ["start_focus"],
    "study": ["new_task", "add_class"],
    "today": ["new_task", "quick_note"],
    "clock": ["new_alarm"],
    "vault": ["generator", "lock_now"],
    "phone": ["phone_send_clipboard", "phone_send_files"],
}


class Recents(QObject):
    changed = pyqtSignal(str)  # app key

    def __init__(self, ctl):
        super().__init__(ctl)
        self.ctl = ctl
        self._dirty: set[str] = set()
        self._timer = QTimer(self, singleShot=True, interval=1500, timeout=self._flush_jumplists)

    # ------------------------------------------------------------------ data
    def _store(self) -> dict:
        return dict(self.ctl.db.get_setting("recents", {}) or {})

    def add(self, app: str, kind: str, ref, title: str) -> None:
        data = self._store()
        items = [e for e in data.get(app, []) if not (e["kind"] == kind and str(e["ref"]) == str(ref))]
        items.insert(0, {"kind": kind, "ref": ref, "title": title})
        data[app] = items[:20]
        self.ctl.db.set_setting("recents", data)
        self._touched(app)

    def recent(self, app: str) -> list[dict]:
        if app == "pdf":  # the reader keeps its own list
            items = [{"kind": "pdf", "ref": r["path"], "title": Path(r["path"]).stem}
                     for r in (self.ctl.db.get_setting("pdf_recent", []) or [])]
        else:
            items = list(self._store().get(app, []))
        pinned = {(e["kind"], str(e["ref"])) for e in self.pinned(app)}
        return [e for e in items if self._exists(e) and (e["kind"], str(e["ref"])) not in pinned][:MAX_RECENT]

    def pinned(self, app: str) -> list[dict]:
        return [e for e in (self.ctl.db.get_setting("jump_pins", {}) or {}).get(app, []) if self._exists(e)]

    def set_pinned(self, app: str, entry: dict, on: bool) -> None:
        pins = dict(self.ctl.db.get_setting("jump_pins", {}) or {})
        items = [e for e in pins.get(app, []) if not (e["kind"] == entry["kind"] and str(e["ref"]) == str(entry["ref"]))]
        if on:
            items.insert(0, {k: entry[k] for k in ("kind", "ref", "title")})
        pins[app] = items[:12]
        self.ctl.db.set_setting("jump_pins", pins)
        self._touched(app)

    def tasks(self, app: str) -> list[tuple[str, str, str]]:
        """(glyph, title, command key) of the app's quick tasks that exist."""
        known = {k: g for g, k, _a in self.ctl.command_list()}
        return [(known[k], tr(k), k) for k in TASKS.get(app, []) if k in known]

    def _exists(self, e: dict) -> bool:
        if e["kind"] in ("pdf", "folder", "file"):
            return os.path.exists(str(e["ref"]))
        if e["kind"] == "note":
            try:
                note = self.ctl.db.get_note(int(e["ref"]))
            except (TypeError, ValueError):
                return False
            return bool(note) and not note.get("deleted_at")
        return True

    def open(self, e: dict) -> None:
        kind, ref = e["kind"], e["ref"]
        if kind == "pdf":
            self.ctl.open_pdf(str(ref))
        elif kind == "note":
            self.ctl.open_notebook(int(ref))
        elif kind == "folder":
            self.ctl.open_folder(str(ref))
        else:
            self.ctl.open_path(str(ref))

    def _touched(self, app: str) -> None:
        self.changed.emit(app)
        self._dirty.add(app)
        self._timer.start()

    # ----------------------------------------------------- Windows jump lists
    def refresh_all_jumplists(self) -> None:
        from app.ui import apps as app_registry
        self._dirty.update(a.key for a in app_registry.BUILTIN if a.kind != "action")
        self._timer.start()

    def _flush_jumplists(self) -> None:
        if os.environ.get("STUDYHUB_NO_SHELL"):
            self._dirty.clear()  # tests: never touch Windows' lists
            return
        from app.ui import apps as app_registry
        for app in list(self._dirty):
            info = app_registry.find(self.ctl, app)
            if info is None or info.kind == "action":
                continue
            try:
                write_jumplist(info.app_id, self._jump_entries(app))
            except Exception:
                log.exception("jump list for %s", app)
        self._dirty.clear()

    def _jump_entries(self, app: str) -> dict:
        def link(e: dict) -> tuple[str, str, str]:
            if e["kind"] == "pdf":
                return e["title"], f'--pdf "{e["ref"]}"', "pdf"
            if e["kind"] == "note":
                return e["title"] or tr("note"), f"--note {int(e['ref'])}", "notes"
            return e["title"], f'--open-path "{e["ref"]}"', "files"
        return {"pinned": [link(e) for e in self.pinned(app)],
                "recent": [link(e) for e in self.recent(app)],
                "tasks": [(title, f"--do {key}", app) for _g, title, key in self.tasks(app)]}


# --------------------------------------------------------------------- COM
_ole32 = ctypes.windll.ole32


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        import uuid
        u = uuid.UUID(text)
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        g.Data4[:] = list(u.bytes[8:])
        return g


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("pwszVal", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]


def _create(clsid: str, iid: str) -> ctypes.c_void_p:
    ptr = ctypes.c_void_p()
    hr = _ole32.CoCreateInstance(ctypes.byref(_GUID.of(clsid)), None, 1, ctypes.byref(_GUID.of(iid)), ctypes.byref(ptr))
    if hr != 0:
        raise OSError(f"CoCreateInstance {clsid}: {hr & 0xFFFFFFFF:#x}")
    return ptr


def _method(obj, index: int, *argtypes):
    vtbl = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])


def _release(obj) -> None:
    if obj:
        _method(obj, 2)(obj)


def _qi(obj, iid: str) -> ctypes.c_void_p:
    out = ctypes.c_void_p()
    if _method(obj, 0, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(obj, ctypes.byref(_GUID.of(iid)), ctypes.byref(out)):
        raise OSError("QueryInterface")
    return out


_IID_UNKNOWN = "{00000000-0000-0000-C000-000000000046}"
_IID_OBJECTARRAY = "{92CA9DCD-5622-4BBA-A805-5E9F541BD8C9}"
_IID_PROPERTYSTORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"


def _exe_and_icon(icon_key: str) -> tuple[str, str]:
    from app.core.shell_integration import _launch
    parts = _launch()
    exe = parts[0]
    base = Path(exe).parent if exe.lower().endswith(".exe") and "python" not in Path(exe).name.lower() else None
    icon = str(base / "icons" / f"{icon_key}.ico") if base and (base / "icons" / f"{icon_key}.ico").is_file() else exe
    return exe, icon


def _shell_link(title: str, args: str, icon_key: str):
    from app.core.shell_integration import _launch
    parts = _launch()
    exe, icon = _exe_and_icon(icon_key)
    pre = " ".join(f'"{p}"' for p in parts[1:])  # running from source: python main.py …
    link = _create("{00021401-0000-0000-C000-000000000046}", "{000214F9-0000-0000-C000-000000000046}")
    _method(link, 20, wintypes.LPCWSTR)(link, exe)                       # SetPath
    _method(link, 11, wintypes.LPCWSTR)(link, (pre + " " + args).strip())  # SetArguments
    _method(link, 17, wintypes.LPCWSTR, ctypes.c_int)(link, icon, 0)      # SetIconLocation
    _method(link, 7, wintypes.LPCWSTR)(link, title)                      # SetDescription
    store = _qi(link, _IID_PROPERTYSTORE)
    try:
        pv = _PROPVARIANT(vt=31, pwszVal=title)
        key = _PROPERTYKEY(_GUID.of("{F29F85E0-4FF9-1068-AB91-08002B27B3D9}"), 2)  # PKEY_Title
        _method(store, 6, ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT))(store, ctypes.byref(key), ctypes.byref(pv))
        _method(store, 7)(store)
    finally:
        _release(store)
    return link


def _collection(entries) -> ctypes.c_void_p:
    coll = _create("{2D3468C1-36A7-43B6-AC24-D3F02FD9607A}", "{5632B1A4-E38A-400A-928A-D4CD63230295}")
    for title, args, icon in entries:
        link = _shell_link(title[:120], args, icon)
        _method(coll, 5, ctypes.c_void_p)(coll, link)  # AddObject
        _release(link)
    return coll


def write_jumplist(app_id: str, entries: dict) -> None:
    """Replaces Windows' jump list of one app id: Pinned, Recent, and the app's Tasks."""
    if sys.platform != "win32":
        return
    dl = _create("{77F10CF0-3DB5-4966-B520-B7C54FD35ED6}", "{6332DEBF-87B5-4670-90C0-5E57B408A49E}")
    try:
        _method(dl, 3, wintypes.LPCWSTR)(dl, app_id)  # SetAppID
        slots = wintypes.UINT()
        removed = ctypes.c_void_p()
        hr = _method(dl, 4, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
            dl, ctypes.byref(slots), ctypes.byref(_GUID.of(_IID_OBJECTARRAY)), ctypes.byref(removed))  # BeginList
        if hr:
            raise OSError(f"BeginList {hr & 0xFFFFFFFF:#x}")
        _release(removed)
        room = max(4, int(slots.value))
        for name, key in ((tr("jump_pinned"), "pinned"), (tr("jump_recent"), "recent")):
            items = entries.get(key, [])[:room]
            if items:
                coll = _collection(items)
                arr = _qi(coll, _IID_OBJECTARRAY)
                _method(dl, 5, wintypes.LPCWSTR, ctypes.c_void_p)(dl, name, arr)  # AppendCategory
                _release(arr)
                _release(coll)
        if entries.get("tasks"):
            coll = _collection(entries["tasks"])
            arr = _qi(coll, _IID_OBJECTARRAY)
            _method(dl, 7, ctypes.c_void_p)(dl, arr)  # AddUserTasks
            _release(arr)
            _release(coll)
        hr = _method(dl, 8)(dl)  # CommitList
        if hr:
            raise OSError(f"CommitList {hr & 0xFFFFFFFF:#x}")
    finally:
        _release(dl)
