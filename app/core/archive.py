"""
File archiving.

Archive layout (readable even without Study Hub):
    <archive root>/<semester>/<subject>/<category>/<files>
Modes:
    move : files leave their place (the classic "archive")
    copy : a safe copy goes to the archive, originals stay
    zip  : a compressed .zip goes to the archive, originals stay
Every entry is recorded, so it can be searched and restored to where it came from.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from app.core.files import unique_destination
from app.core.fileops import folder_size

CATEGORIES = ["lectures", "assignments", "exams", "projects", "other"]
_BAD = '\\/:*?"<>|'


def _safe(name: str) -> str:
    return "".join("_" if c in _BAD else c for c in name).strip() or "_"


def default_root() -> Path:
    from app.core.fileops import known_folders
    docs = dict(known_folders()).get("documents") or str(Path.home() / "Documents")
    return Path(docs) / "Study Hub Archive"


def auto_semester(today: date | None = None) -> str:
    """2025-2026 · 1 (Sep–Jan), · 2 (Feb–Jun), · Summer (Jul–Aug)."""
    d = today or date.today()
    start = d.year if d.month >= 9 else d.year - 1
    term = "1" if d.month >= 9 or d.month == 1 else ("2" if d.month <= 6 else "S")
    return f"{start}-{start + 1} · {term}"


def target_folder(root: Path, semester: str, subject: str | None, category: str) -> Path:
    return Path(root) / _safe(semester or auto_semester()) / _safe(subject or "General") / _safe(category)


def _size(path: str) -> int:
    return folder_size(path) if os.path.isdir(path) else os.path.getsize(path)


def _zip(path: str, folder: Path) -> str:
    dst = unique_destination(str(folder), Path(path).stem + ".zip" if os.path.isfile(path) else Path(path).name + ".zip")
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        if os.path.isdir(path):
            base = Path(path).parent
            for root, _, files in os.walk(path):
                for f in files:
                    full = Path(root) / f
                    z.write(full, full.relative_to(base))
        else:
            z.write(path, Path(path).name)
    return dst


def archive_paths(db, paths: list[str], root: Path, subject_id: int | None, subject_name: str | None,
                  category: str, semester: str, mode: str, note: str = "") -> list[int]:
    """Archives each path; returns the new entry ids. Raises OSError on the first failure."""
    folder = target_folder(root, semester, subject_name, category)
    folder.mkdir(parents=True, exist_ok=True)
    ids = []
    for path in paths:
        path = os.path.normpath(path)
        size = _size(path)
        if mode == "zip":
            dst = _zip(path, folder)
        elif mode == "copy":
            dst = unique_destination(str(folder), Path(path).name)
            (shutil.copytree if os.path.isdir(path) else shutil.copy2)(path, dst)
        else:
            dst = unique_destination(str(folder), Path(path).name)
            shutil.move(path, dst)
            db.rename_tracked_path(path, dst)  # tags follow the file
        ids.append(db.add_archive_entry(Path(path).name, path, dst, subject_id, category, semester, mode, size, note))
    return ids


def restore(db, entry: dict) -> str:
    """Puts an archived item back where it came from (a free name is used if that spot is taken)."""
    src, original = entry["archive_path"], entry["original_path"]
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    parent = os.path.dirname(original)
    os.makedirs(parent, exist_ok=True)
    if entry["mode"] == "zip":
        # a zipped file has one member "<name>"; a zipped folder has members "<folder>/<...>"
        dst = unique_destination(parent, Path(original).name)
        with zipfile.ZipFile(src) as z:
            for member in z.infolist():
                if member.is_dir():
                    continue
                parts = Path(member.filename).parts
                rel = Path(*parts[1:]) if len(parts) > 1 else None
                out = Path(dst) / rel if rel else Path(dst)
                if not str(out.resolve()).startswith(str(Path(dst).resolve())):
                    continue  # never write outside the restore folder ("zip slip")
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as fin, open(out, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
        return dst
    if entry["mode"] == "copy":
        dst = unique_destination(parent, Path(original).name)
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dst)
        return dst
    dst = unique_destination(parent, Path(original).name)
    shutil.move(src, dst)
    db.rename_tracked_path(src, dst)
    db.delete_archive_entry(entry["id"])  # it left the archive
    return dst


def stale_files(folders: list[str], months: int = 6, limit: int = 200) -> list[dict]:
    """Files in subject folders untouched for `months` — archive candidates (suggestions only)."""
    cutoff = (datetime.now() - timedelta(days=30 * months)).timestamp()
    out = []
    for folder in folders:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                full = os.path.join(root, f)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if st.st_mtime < cutoff and not f.startswith(("~$", ".")):
                    out.append({"path": full, "name": f, "size": st.st_size, "mtime": st.st_mtime})
                    if len(out) >= limit:
                        return out
    out.sort(key=lambda x: x["mtime"])
    return out
