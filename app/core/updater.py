"""
Updates from inside Study Hub.

Where an update comes from (Settings › Updates):
  * a folder: a fresh build (dist\\StudyHub) — by default the folder this copy was installed from
    (install.ps1 writes it to install_source.txt)
  * a .zip of such a folder (e.g. copied from another PC / a USB stick)
  * an https:// link to a small JSON manifest {"version", "built", "url", "sha256", "notes"}: the zip is
    downloaded and installed only if its SHA-256 matches the manifest

Every build carries build_info.json {"version", "built", "notes"} next to StudyHub.exe; an update is
"newer" when its build time (then version) is later than ours. Installing = backing up the database,
then starting that build's install.ps1 detached: it closes Study Hub, copies the files and reopens it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from app.config import DATA_DIR, IS_FROZEN, VERSION

log = logging.getLogger(__name__)

UPDATES_DIR = DATA_DIR / "updates"
DETACHED = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
MAX_DOWNLOAD = 600 * 1024 * 1024


def app_dir() -> Path:
    return Path(sys.executable).parent if IS_FROZEN else Path(__file__).resolve().parents[2]


def current_build() -> dict:
    try:
        info = json.loads((app_dir() / "build_info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    return {"version": info.get("version", VERSION), "built": info.get("built", ""), "notes": info.get("notes", {})}


def default_source() -> str:
    try:
        return (app_dir() / "install_source.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _version_key(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in str(v).split("."))


def is_newer(candidate: dict, current: dict) -> bool:
    if candidate.get("built") and current.get("built"):
        return candidate["built"] > current["built"]
    return _version_key(candidate.get("version", "0")) > _version_key(current.get("version", "0"))


def kind_of(source: str) -> str:
    if source.lower().startswith("https://"):
        return "url"
    if source.lower().endswith(".zip"):
        return "zip"
    return "folder"


def check(source: str) -> dict:
    """{"ok", "available", "version", "built", "notes", "kind", "source", "error"} — reads only."""
    result = {"ok": False, "available": False, "source": source, "kind": kind_of(source)}
    if not source:
        result["error"] = "no source"
        return result
    try:
        if result["kind"] == "folder":
            folder = Path(source)
            if not (folder / "StudyHub.exe").is_file() or not (folder / "install.ps1").is_file():
                raise FileNotFoundError("no build in this folder")
            info = json.loads((folder / "build_info.json").read_text(encoding="utf-8"))
        elif result["kind"] == "zip":
            with zipfile.ZipFile(source) as z:
                name = next((n for n in z.namelist() if n.endswith("build_info.json")), None)
                if name is None or not any(n.endswith("install.ps1") for n in z.namelist()):
                    raise FileNotFoundError("not a Study Hub build")
                info = json.loads(z.read(name).decode("utf-8"))
        else:
            with urllib.request.urlopen(urllib.request.Request(source, headers={"User-Agent": "StudyHub-updater"}),
                                        timeout=15) as r:
                info = json.loads(r.read(256 * 1024).decode("utf-8"))
            if not str(info.get("url", "")).startswith("https://") or len(str(info.get("sha256", ""))) != 64:
                raise ValueError("manifest needs an https url and a sha256")
            result["download"], result["sha256"] = info["url"], info["sha256"].lower()
    except Exception as exc:  # report, the caller shows it
        result["error"] = str(exc)[:200]
        return result
    result.update(ok=True, version=info.get("version", "?"), built=info.get("built", ""), notes=info.get("notes", {}))
    result["available"] = is_newer(info, current_build())
    return result


def prepare(result: dict, progress=None) -> Path:
    """Folder holding the build's install.ps1 (downloads / extracts as needed). Runs off the GUI thread."""
    if result["kind"] == "folder":
        return Path(result["source"])
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    if result["kind"] == "url":
        archive = UPDATES_DIR / "download.zip"
        digest = hashlib.sha256()
        req = urllib.request.Request(result["download"], headers={"User-Agent": "StudyHub-updater"})
        with urllib.request.urlopen(req, timeout=30) as r, open(archive, "wb") as fh:
            total, done = int(r.headers.get("Content-Length") or 0), 0
            while chunk := r.read(256 * 1024):
                done += len(chunk)
                if done > MAX_DOWNLOAD:
                    raise ValueError("download too large")
                digest.update(chunk)
                fh.write(chunk)
                if progress and total:
                    progress(done / total)
        if digest.hexdigest() != result["sha256"]:
            archive.unlink(missing_ok=True)
            raise ValueError("SHA-256 mismatch — the download was not installed")
    else:
        archive = Path(result["source"])
    target = Path(tempfile.mkdtemp(prefix="build-", dir=UPDATES_DIR))
    with zipfile.ZipFile(archive) as z:
        for member in z.namelist():  # never write outside the target folder
            dest = (target / member).resolve()
            if target.resolve() not in dest.parents and dest != target.resolve():
                raise ValueError("unsafe path in the archive")
        z.extractall(target)
    found = next((p.parent for p in target.rglob("install.ps1") if (p.parent / "StudyHub.exe").is_file()), None)
    if found is None:
        raise FileNotFoundError("no StudyHub.exe + install.ps1 in the archive")
    return found


def launch_installer(build_dir: Path) -> None:
    """Starts install.ps1 on its own (it closes this Study Hub, copies the files and starts the new one)."""
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                      str(build_dir / "install.ps1")], cwd=str(build_dir), creationflags=DETACHED, close_fds=True)


def clean_downloads() -> None:
    shutil.rmtree(UPDATES_DIR, ignore_errors=True)
