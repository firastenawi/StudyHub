"""
Clipboard history: everything you copy (text, links, images, files) is kept — the last 200 by
default, pinned ones forever — and comes back with Ctrl+Alt+C.

Privacy: copies marked "don't record" by password managers (the same Windows clipboard formats
Win+V respects) and the vault's own copies are never stored. History can be paused or cleared
in Settings.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from app.config import DATA_DIR

CLIP_DIR = DATA_DIR / "clipboard"
MAX_TEXT = 100_000
PRIVATE_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore",
                   "CanIncludeInClipboardHistory")


def _is_private(md) -> bool:
    formats = " ".join(md.formats())
    return any(f in formats for f in PRIVATE_FORMATS)


class ClipboardHistory(QObject):
    changed = pyqtSignal()

    def __init__(self, ctl):
        super().__init__(ctl)
        self.ctl = ctl
        self._suppress = 0          # sequence of our own clipboard writes to skip
        self._debounce = QTimer(self, singleShot=True, interval=150, timeout=self._record)
        QApplication.clipboard().dataChanged.connect(self._debounce.start)

    # ------------------------------------------------------------- settings
    @property
    def enabled(self) -> bool:
        return bool(self.ctl.db.get_setting("clipboard_history", True))

    @property
    def max_items(self) -> int:
        return int(self.ctl.db.get_setting("clipboard_max", 200))

    # -------------------------------------------------------------- record
    def _record(self) -> None:
        if self._suppress:
            self._suppress -= 1
            return
        if not self.enabled:
            return
        md = QApplication.clipboard().mimeData()
        if md is None or _is_private(md):
            return
        text = md.text() if md.hasText() else ""
        if getattr(self.ctl, "_clip_secret", None) and text == self.ctl._clip_secret:
            return
        db = self.ctl.db
        if md.hasUrls() and all(u.isLocalFile() for u in md.urls()) and md.urls():
            paths = [os.path.normpath(u.toLocalFile()) for u in md.urls()]
            preview = "\n".join(Path(p).name for p in paths[:20])
            db.add_clip("files", json.dumps(paths, ensure_ascii=False), preview)
        elif md.hasImage() and not text.strip():
            image = md.imageData()
            if not isinstance(image, QImage) or image.isNull():
                return
            CLIP_DIR.mkdir(parents=True, exist_ok=True)
            key = f"{image.cacheKey() & 0xFFFFFFFF:08x}-{image.width()}x{image.height()}"
            name = f"{key}.png"
            if not (CLIP_DIR / name).exists():
                image.save(str(CLIP_DIR / name), "PNG")
            db.add_clip("image", name, f"{image.width()} × {image.height()}")
        elif text.strip():
            text = text[:MAX_TEXT]
            stripped = text.strip()
            kind = "link" if "\n" not in stripped and stripped.startswith(("http://", "https://")) else "text"
            db.add_clip(kind, text, " ".join(stripped.split())[:300])
        else:
            return
        self._trim()
        self.changed.emit()

    def _trim(self) -> None:
        old = self.ctl.db.expired_clips(self.max_items)
        if old:
            self._forget(self.ctl.db.delete_clips([r["id"] for r in old]))

    @staticmethod
    def _forget(rows: list[dict]) -> None:
        for r in rows:
            if r["kind"] == "image":
                try:
                    (CLIP_DIR / r["content"]).unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------- actions
    def put_on_clipboard(self, clip_id: int) -> bool:
        """Back onto the clipboard (moves to the top of the history)."""
        from PyQt6.QtCore import QMimeData
        clip = self.ctl.db.get_clip(clip_id)
        if not clip:
            return False
        md = QMimeData()
        if clip["kind"] == "files":
            paths = [p for p in json.loads(clip["content"]) if os.path.exists(p)]
            md.setUrls([QUrl.fromLocalFile(p) for p in paths])
            md.setText("\n".join(paths))
        elif clip["kind"] == "image":
            image = QImage(str(CLIP_DIR / clip["content"]))
            if image.isNull():
                return False
            md.setImageData(image)
        else:
            md.setText(clip["content"])
        self._suppress += 1
        QApplication.clipboard().setMimeData(md)
        from app.core.database import now_str
        self.ctl.db.update_clip(clip_id, used=clip["used"] + 1, created_at=now_str())
        self.changed.emit()
        return True

    def set_pinned(self, clip_id: int, on: bool) -> None:
        self.ctl.db.update_clip(clip_id, pinned=int(on))
        self.changed.emit()

    def delete(self, ids: list[int]) -> None:
        self._forget(self.ctl.db.delete_clips(ids))
        self.changed.emit()

    def clear(self, keep_pinned: bool = True) -> None:
        self._forget(self.ctl.db.delete_clips(None, keep_pinned))
        self.changed.emit()
