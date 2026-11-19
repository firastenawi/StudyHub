"""
Photo backup and synced folders with the phone (Phone > Sync & backup).

The phone starts everything (the laptop can't reach it): it asks what's missing, uploads in resumable chunks
(lan_share /api/xfer), downloads with HTTP Range, and reports back. This side decides and keeps the records:

Photo backup   new camera photos, videos and screenshots go to <folder>\\YYYY\\MM (by the date taken). An index
               remembers what came already, so nothing is copied twice; nothing is ever deleted.
Synced folder  a laptop folder <-> a phone folder, both ways or one way. Each sync compares both sides with the
               last sync's snapshot:
                 changed on one side            -> copied to the other
                 changed on both                -> both kept: the phone's copy comes as "name (phone DATE).ext"
                 deleted on one side, unchanged on the other -> laptop: Recycle Bin; phone: "Study Hub trash"
               One way (phone -> laptop / laptop -> phone): the target gets the source's new and changed files
               and its deletions; edits made on the target stay until the source changes that file again.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from app.config import DATA_DIR
from app.core.i18n import tr

log = logging.getLogger(__name__)

STATE_DIR = Path(DATA_DIR) / "phone_sync"
XFER_DIR = Path(DATA_DIR) / "xfer"          # partial uploads (resumable), cleaned after a week
PHONE_TRASH = "Study Hub trash"
DIRECTIONS = ("both", "to_laptop", "to_phone")
SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store", ".nomedia"}
_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')


def default_backup_folder() -> str:
    from PyQt6.QtCore import QStandardPaths
    return str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)) / "Phone backup")


def safe_rel(rel: str) -> str | None:
    """A relative path from the phone, made safe for this folder (None if it tries to leave it)."""
    rel = str(rel or "").replace("\\", "/").strip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or _BAD.search(p) or p.endswith((" ", ".")) for p in parts):
        return None
    return "/".join(parts)


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 10000):
        cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not cand.exists():
            return cand
    return path.with_name(f"{path.stem} ({secrets.token_hex(3)}){path.suffix}")


def conflict_name(rel: str) -> str:
    p = Path(rel)
    return str(p.with_name(f"{p.stem} (phone {datetime.now():%Y-%m-%d %H-%M}){p.suffix}")).replace("\\", "/")


def result_text(counts: dict) -> str:
    """'2 to the laptop, 1 to the phone · 1 kept both', in the language in use now."""
    text = tr("sync_result", up=counts.get("up", 0), down=counts.get("down", 0))
    if counts.get("conflicts"):
        text += "  ·  " + tr("sync_result_conflicts", n=counts["conflicts"])
    if counts.get("failed"):
        text += "  ·  " + tr("sync_result_failed", n=counts["failed"])
    return text


def list_laptop(root: Path) -> dict[str, tuple[int, float]]:
    out: dict[str, tuple[int, float]] = {}
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.lower() in SKIP_NAMES or name.startswith("~$") or name.endswith((".part", ".tmp")):
                continue
            full = Path(dirpath) / name
            try:
                st = full.stat()
            except OSError:
                continue
            out[full.relative_to(root).as_posix()] = (st.st_size, st.st_mtime)
    return out


def plan_sync(direction: str, phone: dict[str, tuple[int, float]], laptop: dict[str, tuple[int, float]],
              snap: dict[str, dict]) -> dict:
    """What to do, from both listings and the last snapshot. Pure function (tested on its own).
    phone/laptop: {rel: (size, mtime)}; snap: {rel: {"ps", "pm", "ls", "lm"}} (each side as last seen).
    Returns {upload: [{path, as}], download: [rel], trash_phone: [rel], recycle_laptop: [rel], same: [rel]}."""
    up_ok = direction in ("both", "to_laptop")
    down_ok = direction in ("both", "to_phone")
    out = {"upload": [], "download": [], "trash_phone": [], "recycle_laptop": [], "same": [], "conflicts": []}
    for rel in sorted(set(phone) | set(laptop) | set(snap)):
        p, lp, s = phone.get(rel), laptop.get(rel), snap.get(rel)
        p_changed = p is not None and (s is None or p[0] != s["ps"] or abs(p[1] - s["pm"]) > 2)
        l_changed = lp is not None and (s is None or lp[0] != s["ls"] or abs(lp[1] - s["lm"]) > 2)
        if p and lp:
            if s is None:
                if p[0] == lp[0]:
                    out["same"].append(rel)            # same file on both already (first sync)
                elif direction == "both":
                    out["conflicts"].append(rel)
                    out["upload"].append({"path": rel, "as": conflict_name(rel)})
                    out["download"].append(rel)
                elif up_ok:
                    out["upload"].append({"path": rel, "as": rel})
                else:
                    out["download"].append(rel)
            elif p_changed and l_changed:
                if direction == "both":
                    out["conflicts"].append(rel)
                    out["upload"].append({"path": rel, "as": conflict_name(rel)})
                    out["download"].append(rel)
                elif up_ok:
                    out["upload"].append({"path": rel, "as": rel})
                else:
                    out["download"].append(rel)
            elif p_changed and up_ok:
                out["upload"].append({"path": rel, "as": rel})
            elif l_changed and down_ok:
                out["download"].append(rel)
            else:
                out["same"].append(rel)
        elif p and not lp:
            if s is not None and not p_changed and down_ok:   # deleted on the laptop -> phone trash
                out["trash_phone"].append(rel)
            elif up_ok:
                out["upload"].append({"path": rel, "as": rel})
        elif lp and not p:
            if s is not None and not l_changed and up_ok:     # deleted on the phone -> Recycle Bin
                out["recycle_laptop"].append(rel)
            elif down_ok:
                out["download"].append(rel)
    return out


class PhoneSync(QObject):
    """ctl.phone_sync: settings, the backup index, sync snapshots; answers the phone (through PhoneBridge)."""
    changed = pyqtSignal()

    def __init__(self, ctl):
        super().__init__(ctl)
        self.ctl = ctl
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        XFER_DIR.mkdir(parents=True, exist_ok=True)
        self.progress: dict = {}  # live backup / sync progress the phone reports
        self._index = None
        self._clean_partials()

    # ---------------------------------------------------------------- settings
    def backup(self) -> dict:
        b = {"on": False, "folder": "", "kinds": "photos_videos"}
        b.update(self.ctl.db.get_setting("phone_backup", {}) or {})
        b["folder"] = b["folder"] or default_backup_folder()
        return b

    def set_backup(self, **changes) -> None:
        self.ctl.db.set_setting("phone_backup", {**self.backup(), **changes})
        self._push_config()
        self.changed.emit()

    def folders(self) -> list[dict]:
        return [dict(f) for f in (self.ctl.db.get_setting("phone_sync_folders", []) or [])]

    def _save_folders(self, folders: list[dict]) -> None:
        self.ctl.db.set_setting("phone_sync_folders", folders)
        self._push_config()
        self.changed.emit()

    def add_folder(self, laptop_path: str, direction: str = "both") -> dict:
        entry = {"id": secrets.token_hex(6), "name": Path(laptop_path).name or laptop_path,
                 "laptop_path": os.path.normpath(laptop_path), "direction": direction if direction in DIRECTIONS else "both",
                 "phone_name": "", "linked": False, "last_sync": 0, "last_result": ""}
        self._save_folders(self.folders() + [entry])
        return entry

    def update_folder(self, fid: str, **changes) -> None:
        self._save_folders([dict(f, **changes) if f["id"] == fid else f for f in self.folders()])

    def remove_folder(self, fid: str) -> None:
        self._save_folders([f for f in self.folders() if f["id"] != fid])
        try:
            (STATE_DIR / f"{fid}.json").unlink()
        except OSError:
            pass

    def folder(self, fid: str) -> dict | None:
        return next((f for f in self.folders() if f["id"] == fid), None)

    def config(self) -> dict:
        """What the phone needs to know (also in every /api/sync/config answer)."""
        b = self.backup()
        return {"backup": {"on": bool(b["on"]), "kinds": b["kinds"]},
                "folders": [{"id": f["id"], "name": f["name"], "direction": f["direction"], "linked": f["linked"]}
                            for f in self.folders()]}

    def _push_config(self) -> None:
        if getattr(self.ctl, "phone", None) is not None:
            self.ctl.phone.set_sync_config(self.config())

    def sync_now(self) -> None:
        """Ask the phone to back up / sync right away (it does it on its own every 15 minutes too)."""
        if getattr(self.ctl, "phone_hub", None) is not None:
            self.ctl.phone_hub.send("sync_now", tr("sync_now"))

    # ---------------------------------------------------------------- backup
    def _index_path(self) -> Path:
        return STATE_DIR / "backup_index.json"

    def index(self) -> dict:
        if self._index is None:
            try:
                self._index = json.loads(self._index_path().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._index = {}
        return self._index

    def _save_index(self) -> None:
        tmp = self._index_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(self.index()), encoding="utf-8")
        tmp.replace(self._index_path())

    def backup_check(self, items: list[dict]) -> list[str]:
        """The keys of these phone items that aren't backed up yet."""
        idx = self.index()
        return [str(i.get("key")) for i in items if str(i.get("key")) not in idx]

    def stats(self) -> dict:
        b = self.ctl.db.get_setting("phone_backup_stats", {}) or {}
        return {"count": len(self.index()), "last": b.get("last", 0), "last_new": b.get("last_new", 0)}

    def backup_progress(self, data: dict) -> None:
        self.progress = {"kind": data.get("kind", "backup"), "done": int(data.get("done", 0) or 0),
                         "total": int(data.get("total", 0) or 0), "name": str(data.get("name", ""))[:120],
                         "finished": bool(data.get("finished")), "at": time.time()}
        if self.progress["finished"] and self.progress["kind"] == "backup":
            self.ctl.db.set_setting("phone_backup_stats", {"last": time.time(), "last_new": self.progress["done"]})
            if self.progress["done"]:
                self.ctl.db.add_transfer("in", "backup", tr("sync_backup_done", n=self.progress["done"]),
                                         self.backup()["folder"], 0, None, None)
                self.ctl.data_changed.emit("phone")
        self.changed.emit()

    # ---------------------------------------------------------------- a finished upload lands here
    def xfer_done(self, part: Path, meta: dict) -> dict:
        purpose = meta.get("purpose")
        if purpose == "backup":
            return self._backup_done(part, meta)
        if purpose == "sync":
            return self._sync_file_done(part, meta)
        return {"error": "purpose", "status": 400}

    def _backup_done(self, part: Path, meta: dict) -> dict:
        key = str(meta.get("key", ""))[:200]
        name = Path(str(meta.get("name") or "photo")).name
        name = _BAD.sub("_", name).strip(" .") or "photo"
        try:
            taken = datetime.fromtimestamp(float(meta.get("taken") or 0) / 1000) if meta.get("taken") else datetime.now()
        except (ValueError, OSError):
            taken = datetime.now()
        folder = Path(self.backup()["folder"]) / f"{taken:%Y}" / f"{taken:%m}"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        if target.exists() and target.stat().st_size == part.stat().st_size:
            part.unlink(missing_ok=True)       # already there (same name and size)
        else:
            target = unique(target)
            shutil.move(str(part), str(target))
            try:
                ts = taken.timestamp()
                os.utime(target, (ts, ts))
            except OSError:
                pass
        if key:
            self.index()[key] = target.relative_to(Path(self.backup()["folder"])).as_posix()
            self._save_index()
        return {"ok": True}

    def _sync_file_done(self, part: Path, meta: dict) -> dict:
        f = self.folder(str(meta.get("sync_id", "")))
        rel = safe_rel(meta.get("as") or meta.get("path"))
        if not f or not rel:
            return {"error": "bad", "status": 400}
        root = Path(f["laptop_path"])
        target = (root / rel)
        if root.resolve() not in target.resolve().parents:
            return {"error": "bad", "status": 400}
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".sh-in")
        shutil.move(str(part), str(tmp))
        os.replace(tmp, target)
        return {"ok": True}

    # ---------------------------------------------------------------- synced folders
    def _snap_path(self, fid: str) -> Path:
        return STATE_DIR / f"{fid}.json"

    def _snapshot(self, fid: str) -> dict:
        try:
            return json.loads(self._snap_path(fid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _phone_list(files: list) -> dict[str, tuple[int, float]]:
        out = {}
        for item in files or []:
            rel = safe_rel(item.get("path"))
            if rel and not rel.startswith(PHONE_TRASH + "/"):
                try:
                    out[rel] = (int(item.get("size", 0)), float(item.get("mtime", 0)) / 1000)
                except (TypeError, ValueError):
                    continue
        return out

    def sync_plan(self, data: dict) -> dict:
        f = self.folder(str(data.get("id", "")))
        if not f:
            return {"error": "gone", "status": 404}
        root = Path(f["laptop_path"])
        if not root.is_dir():
            return {"error": "no_folder", "status": 409}
        phone = self._phone_list(data.get("files"))
        laptop = list_laptop(root)
        plan = plan_sync(f["direction"], phone, laptop, self._snapshot(f["id"]))
        if plan["recycle_laptop"]:
            from app.core.fileops import recycle
            recycle([str(root / rel) for rel in plan["recycle_laptop"]])
        downloads = [{"path": rel, "size": laptop[rel][0], "mtime": int(laptop[rel][1] * 1000)}
                     for rel in plan["download"] if rel in laptop]
        return {"upload": plan["upload"], "download": downloads, "trash": plan["trash_phone"],
                "conflicts": plan["conflicts"], "recycled": len(plan["recycle_laptop"])}

    def sync_file(self, fid: str, rel: str) -> Path | None:
        """The laptop file the phone downloads (inside the synced folder only)."""
        f = self.folder(fid)
        rel = safe_rel(rel)
        if not f or not rel:
            return None
        root = Path(f["laptop_path"]).resolve()
        target = (root / rel).resolve()
        return target if root in target.parents and target.is_file() else None

    def sync_done(self, data: dict) -> dict:
        f = self.folder(str(data.get("id", "")))
        if not f:
            return {"error": "gone", "status": 404}
        phone = self._phone_list(data.get("files"))
        laptop = list_laptop(Path(f["laptop_path"]))
        snap = {rel: {"ps": phone[rel][0], "pm": phone[rel][1], "ls": laptop[rel][0], "lm": laptop[rel][1]}
                for rel in phone if rel in laptop}
        tmp = self._snap_path(f["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(snap), encoding="utf-8")
        tmp.replace(self._snap_path(f["id"]))
        counts = {k: int(data.get(k, 0) or 0) for k in ("up", "down", "trashed", "conflicts", "failed")}
        result = result_text(counts)
        self.update_folder(f["id"], last_sync=time.time(), last_counts=counts, last_result=result)
        if counts["up"] or counts["down"]:
            self.ctl.db.add_transfer("in", "sync", f"{f['name']}: {result}", f["laptop_path"], 0, None, None)
            self.ctl.data_changed.emit("phone")
        return {"ok": True}

    def sync_linked(self, data: dict) -> dict:
        f = self.folder(str(data.get("id", "")))
        if not f:
            return {"error": "gone", "status": 404}
        self.update_folder(f["id"], linked=True, phone_name=str(data.get("name", ""))[:120])
        self.ctl.notify(tr("sync_linked", name=f["name"]), "success")
        return {"ok": True}

    # ---------------------------------------------------------------- housekeeping
    @staticmethod
    def _clean_partials(days: int = 7) -> None:
        limit = time.time() - days * 86400
        for part in XFER_DIR.glob("*.part"):
            try:
                if part.stat().st_mtime < limit:
                    part.unlink()
            except OSError:
                pass
