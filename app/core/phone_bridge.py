"""
GUI-thread side of the phone app: answers what the phone server's threads ask for
(lan_share.PhoneLinkServer.request -> answer). Only this side reads the database or the screen.

kinds: desktop, icon, item_file, open_item, open_path, screen, input, notes, note, save_note,
       tasks, task_done, timer, clips, clip_image, clip_action, music, mirror, notice
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from PyQt6.QtCore import QBuffer, QFileInfo, QIODevice, QSize, Qt
from PyQt6.QtGui import QGuiApplication, QPixmap
from PyQt6.QtWidgets import QFileIconProvider

from app.core.i18n import tr

log = logging.getLogger(__name__)

CAPS_DEFAULTS = {"desktop": True, "study": True, "files": False, "screen": False, "remote": False,
                 "clipboard": False, "sound": False}
WATCH_NOTICE_SECONDS = 600  # tell the laptop user again after 10 minutes


def caps(db) -> dict[str, bool]:
    return {k: bool(db.get_setting(f"phone_cap_{k}", v)) for k, v in CAPS_DEFAULTS.items()}


def shared_folders(db) -> list[dict]:
    out = []
    for path in db.get_setting("phone_shared_folders", []) or []:
        if Path(path).is_dir():
            out.append({"name": Path(path).name or path, "path": path})
    return out


def _png(pix: QPixmap) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pix.save(buf, "PNG")
    return bytes(buf.data())


class PhoneBridge:
    def __init__(self, ctl):
        self.ctl = ctl
        self.db = ctl.db
        self._icons: dict[tuple, bytes] = {}
        self._noticed: dict[tuple[str, str], float] = {}

    # ---------------------------------------------------------------- entry
    def handle(self, req_id: int, kind: str, payload, device: str) -> None:
        try:
            handler = self._request_notice if kind == "notice" else getattr(self, "_" + kind)
            result = handler(payload, device)
        except Exception as exc:  # never leave the phone waiting on a bug
            log.exception("phone request %s failed", kind)
            result = {"error": str(exc)[:200], "status": 500}
        self.ctl.phone.answer(req_id, result)

    def _device_name(self, device: str) -> str:
        return next((d["name"] for d in self.db.list_phone_devices() if d["token_hash"] == device), "📱")

    def _request_notice(self, what, device):
        self._notice(device, what)
        return {"ok": True}

    def _notice(self, device: str, what: str) -> None:
        """Screen / remote use is always visible on the laptop (once per 10 minutes per phone)."""
        now = time.monotonic()
        if now - self._noticed.get((device, what), -1e9) > WATCH_NOTICE_SECONDS:
            self._noticed[(device, what)] = now
            self.ctl.notify(tr(f"phone_notice_{what}", name=self._device_name(device)), "warning")

    # -------------------------------------------------------------- desktop
    def _desktop(self, _payload, _device):
        desks = [{"id": "all", "name": tr("desk_main")}] + \
                [{"id": str(g["id"]), "name": g["name"]} for g in self.db.list_groups()]
        items = []
        for desk in desks:
            for it in self.db.desk_items(desk["id"]):
                if it["kind"] == "widget":
                    continue
                entry = {"id": it["id"], "kind": it["kind"], "title": it["title"], "desk": desk["id"],
                         "color": it["color"]}
                if it["kind"] == "url":
                    entry["url"] = it["target"]
                elif it["kind"] == "note" and it["target"].isdigit():
                    note = self.db.get_note(int(it["target"]))
                    if note and not note["locked"] and not note["deleted_at"]:
                        entry["note_id"] = note["id"]
                        entry["text"] = note["content"][:400]
                elif it["kind"] == "file":
                    entry["file"] = Path(it["target"]).is_file()
                items.append(entry)
        return {"desks": desks, "items": items}

    def _icon(self, item_id, _device):
        it = self.db.get_item(int(item_id))
        if not it:
            return b""
        key = (it["id"], it["icon_path"], it["target"])
        if key in self._icons:
            return self._icons[key]
        pix = QPixmap()
        if it["icon_path"] and Path(it["icon_path"]).is_file():
            pix = QPixmap(it["icon_path"])
        elif it["kind"] in ("app", "file", "folder") and Path(it["target"]).exists():
            pix = QFileIconProvider().icon(QFileInfo(it["target"])).pixmap(QSize(64, 64))
        data = b""
        if not pix.isNull():
            data = _png(pix.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation))
        self._icons[key] = data
        return data

    def _item_file(self, item_id, _device):
        it = self.db.get_item(int(item_id))
        return it["target"] if it and it["kind"] == "file" and Path(it["target"]).is_file() else ""

    def _open_item(self, item_id, device):
        if not self.db.get_item(int(item_id)):
            return {"error": "gone", "status": 404}
        self.ctl.launch_item(int(item_id))
        return {"ok": True}

    def _open_path(self, path, _device):
        self.ctl.open_path(path)
        return {"ok": True}

    # --------------------------------------------------------------- screen
    def _screen(self, payload, device):
        screens = QGuiApplication.screens()
        screen = screens[payload["monitor"]] if 0 <= payload["monitor"] < len(screens) else \
            QGuiApplication.primaryScreen()
        self._notice(device, "screen")
        pix = screen.grabWindow(0)
        if pix.width() > payload["width"]:
            pix = pix.scaledToWidth(payload["width"], Qt.TransformationMode.SmoothTransformation)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pix.save(buf, "JPEG", 62)
        return {"jpeg": bytes(buf.data()), "monitors": len(screens)}

    def _input(self, data, device):
        from app.core import remote_input as ri
        kind = data.get("type")
        self._notice(device, "remote")
        num = lambda k: float(data.get(k) or 0)  # noqa: E731
        if kind == "key":
            if not ri.press(str(data.get("action", ""))):
                return {"error": "key", "status": 400}
        elif kind == "text":
            ri.type_text(str(data.get("text", "")))
        elif kind == "move":
            ri.move(num("dx"), num("dy"))
        elif kind == "click":
            ri.click("right" if data.get("button") == "right" else "left", bool(data.get("double")))
        elif kind == "scroll":
            ri.scroll(num("dy"), num("dx"))
        elif kind == "tap":  # tap on the screen picture
            screens = QGuiApplication.screens()
            m = int(num("m"))
            screen = screens[m] if 0 <= m < len(screens) else QGuiApplication.primaryScreen()
            rect = ri.monitors().get(screen.name())
            if rect is None:  # fall back to Qt's geometry in device pixels
                g, r = screen.geometry(), screen.devicePixelRatio()
                rect = (int(g.left() * r), int(g.top() * r), int(g.right() * r) + 1, int(g.bottom() * r) + 1)
            ri.click_at(rect, num("x"), num("y"), "right" if data.get("button") == "right" else "left")
        else:
            return {"error": "type", "status": 400}
        return {"ok": True}

    # ---------------------------------------------------------------- study
    def _notes(self, query, _device):
        rows = self.db.list_notes("all", search=query) if query else self.db.list_notes("all")
        subjects = {s["id"]: s["name"] for s in self.db.list_subjects()}
        return {"notes": [{"id": n["id"], "title": n["title"], "updated_at": n["updated_at"],
                           "locked": bool(n["locked"]), "pinned": bool(n["is_pinned"]),
                           "subject": subjects.get(n["subject_id"], ""),
                           "snippet": "" if n["locked"] else n["content"][:160]} for n in rows[:150]]}

    def _note(self, note_id, _device):
        note = self.db.get_note(int(note_id))
        if not note or note["deleted_at"]:
            return {"error": "gone", "status": 404}
        if note["locked"]:
            return {"error": "locked", "status": 423}
        return {"id": note["id"], "title": note["title"], "content": note["content"], "updated_at": note["updated_at"]}

    def _save_note(self, data, _device):
        note = self.db.get_note(data["id"])
        if not note or note["deleted_at"]:
            return {"error": "gone", "status": 404}
        if note["locked"]:
            return {"error": "locked", "status": 423}
        if data["base"] and data["base"] != note["updated_at"]:
            return {"error": "changed", "status": 409}  # edited on the laptop meanwhile
        self.ctl.autosave_note(note["id"], data["title"] or note["title"], data["content"])
        return {"ok": True, "updated_at": self.db.get_note(note["id"])["updated_at"]}

    def _tasks(self, _payload, _device):
        return {"tasks": [{"id": t["id"], "title": t["title"], "due": t["due_date"], "priority": t["priority"],
                           "subject": t["subject_name"] or "", "color": t["subject_color"] or ""}
                          for t in self.db.tasks_for_today(80)]}

    def _task_done(self, data, _device):
        if not self.db.get_task(data["id"]):
            return {"error": "gone", "status": 404}
        self.ctl.set_task_done(data["id"], data["done"])
        return {"ok": True}

    def _timer(self, action, _device):
        p = self.ctl.pomodoro
        if action in ("start", "pause", "toggle", "reset", "skip"):
            getattr(p, action)()
        remaining = p.remaining
        if p.state == "running":
            remaining = max(0, int(round(p._deadline - time.monotonic())))
        return {"state": p.state, "phase": p.phase, "remaining": remaining, "total": p.total,
                "done_today": self.db.pomodoro_count_today(), "minutes_today": self.db.focus_minutes_today() or 0}

    # ------------------------------------------- continue where you left off / stand
    def _handoff_page(self, data, _device):
        """A page of the PDF open on the laptop (only that one), rendered on white."""
        ctx = self.ctl.context.get("pdf")
        if not ctx or not Path(ctx["path"]).is_file():
            return {"error": "none", "status": 404}
        from PyQt6.QtGui import QImage
        from PyQt6.QtPdf import QPdfDocument
        from app.ui.pdf_reader import render_page
        doc = getattr(self, "_handoff_doc", None)
        if doc is None or getattr(self, "_handoff_path", "") != ctx["path"]:
            doc = QPdfDocument(None)
            if doc.load(ctx["path"]) != QPdfDocument.Error.None_:
                return {"error": "open", "status": 404}
            self._handoff_doc, self._handoff_path = doc, ctx["path"]
        n = max(0, min(int(data["n"]), doc.pageCount() - 1))
        pt = doc.pagePointSize(n)
        width = int(data["width"])
        size = QSize(width, int(width * pt.height() / max(1.0, pt.width())))
        img: QImage = render_page(doc, n, size)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPEG", 82)
        return {"jpeg": bytes(buf.data()), "pages": doc.pageCount()}

    def _handoff_set(self, data, device):
        """The phone says where it is now (PDF page / note): offered on the laptop's top bar."""
        if data["kind"] == "pdf":
            ctx = self.ctl.context.get("pdf")
            if not ctx:
                return {"error": "none", "status": 404}
            page = max(0, int(data.get("page") or 0))
            self.ctl.set_phone_context({"kind": "pdf", "path": ctx["path"], "page": page, "title": ctx["name"]})
        else:
            note = self.db.get_note(int(data.get("note_id") or 0))
            if not note or note["deleted_at"]:
                return {"error": "gone", "status": 404}
            self.ctl.set_phone_context({"kind": "note", "note_id": note["id"], "title": note["title"]})
        return {"ok": True}

    def _stand(self, _payload, _device):
        """Everything the stand screen shows, in one go."""
        from datetime import datetime
        from app.core import winapi
        out = {"timer": self._timer("", _device)}
        classes = self.db.upcoming_classes(datetime.now())
        out["next_class"] = ({"subject": classes[0]["subject_name"], "start": classes[0]["start_time"],
                              "end": classes[0]["end_time"], "location": classes[0]["location"],
                              "color": classes[0]["subject_color"]} if classes else None)
        out["tasks"] = [{"id": t["id"], "title": t["title"]} for t in self.db.tasks_for_today(5)]
        infra = getattr(self.ctl, "infra", None)
        if infra:
            checks = infra.store.checks()
            down = [f"{c['host_name'] or ''} · {c['name']}".strip(" ·") for c in checks if c["state"] == "down"]
            out["servers"] = {"total": len(checks), "up": sum(c["state"] == "up" for c in checks),
                              "warn": sum(c["state"] == "warn" for c in checks), "down": len(down), "down_names": down[:4]}
        bat = winapi.battery()
        out["battery"] = {"percent": bat[0], "charging": bat[1]} if bat else None
        player = getattr(self, "_player", None)
        if player is not None:
            st = player.state()
            if st["index"] >= 0:
                out["music"] = {"title": st["queue"][st["index"]], "playing": st["playing"]}
        lab = getattr(self.ctl, "lab", None)
        if lab and lab.active:
            out["lab"] = {"title": lab.session.data["title"], "since": lab.session.started}
        return out

    # ------------------------------------------------------------ clipboard
    def _clips(self, query, _device):
        import json as _json
        rows = self.db.list_clips(query or "", 150)
        out = []
        for r in rows:
            item = {"id": r["id"], "kind": r["kind"], "preview": r["preview"], "pinned": bool(r["pinned"]),
                    "at": r["created_at"]}
            if r["kind"] in ("text", "link"):
                item["text"] = r["content"]
            elif r["kind"] == "files":
                item["text"] = "\n".join(Path(x).name for x in _json.loads(r["content"]))
            out.append(item)
        return {"clips": out, "enabled": self.ctl.clipboard_history.enabled}

    def _clip_image(self, clip_id, _device):
        from app.core.clipboard_history import CLIP_DIR
        clip = self.db.get_clip(int(clip_id))
        if not clip or clip["kind"] != "image":
            return b""
        path = CLIP_DIR / clip["content"]
        return path.read_bytes() if path.is_file() else b""

    def _clip_action(self, data, _device):
        hist = self.ctl.clipboard_history
        if not self.db.get_clip(data["id"]):
            return {"error": "gone", "status": 404}
        action = data["action"]
        if action == "copy":
            if not hist.put_on_clipboard(data["id"]):
                return {"error": "gone", "status": 404}
        elif action in ("pin", "unpin"):
            hist.set_pinned(data["id"], action == "pin")
        elif action == "delete":
            hist.delete([data["id"]])
        else:
            return {"error": "action", "status": 400}
        return {"ok": True}

    # ---------------------------------------------------------------- sound
    def _music(self, data, device):
        if not hasattr(self, "_player"):
            from app.core.phone_music import PhoneMusic
            self._player = PhoneMusic(self.ctl)
        action = data.get("action")
        if action == "add":
            self._notice(device, "music")
            self._player.add(data["path"], data["title"])
        elif action in ("toggle", "next", "prev", "stop", "volume", "seek", "play", "clear"):
            self._player.action(action, data.get("value"))
        elif action != "state":
            return {"error": "action", "status": 400}
        return self._player.state()

    def _mirror(self, action, device):
        mirror = self.ctl.phone_mirror()
        if action == "stop":
            mirror.stop()
        else:
            self._notice(device, "mirror")
            mirror.start()
        return {"ok": True, "found": bool(mirror.scrcpy())}

