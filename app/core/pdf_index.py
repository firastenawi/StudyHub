"""
Full-text search across all your PDFs.

Which files: PDFs you opened in the reader, every PDF in the subjects' folders (Study Planner), and folders you add
in Settings › PDF reader. The text of each page goes into a small SQLite FTS5 index next to the database
(pdf_index.db); a file is read again only when it changed (size + modified time). Indexing runs in a background
thread at low priority; searching is instant.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from app.config import DATA_DIR
from app.core.logs import log

INDEX_PATH = DATA_DIR / "pdf_index.db"
MAX_PAGES = 1500
MAX_FILES = 3000


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(INDEX_PATH), timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, pages INTEGER, indexed REAL)")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(path UNINDEXED, page UNINDEXED, text, "
                "tokenize = 'unicode61 remove_diacritics 2')")
    return con


def fts_query(text: str) -> str:
    """User words -> a safe FTS5 query (every word must appear; the last one as a prefix)."""
    words = [w.replace('"', "") for w in text.split() if w.replace('"', "")]
    if not words:
        return ""
    parts = [f'"{w}"' for w in words[:-1]] + [f'"{words[-1]}"*']
    return " ".join(parts)


class PdfIndex(QObject):
    """ctl.pdf_index — keeps the index fresh and answers searches."""
    progress = pyqtSignal(int, int)   # done, total (while indexing)
    finished = pyqtSignal(int)        # files indexed this round

    def __init__(self, ctl):
        super().__init__(ctl)
        self.ctl = ctl
        self._busy = False
        self._stop = False

    # ------------------------------------------------------------ sources
    def folders(self) -> list[str]:
        return [f for f in (self.ctl.db.get_setting("pdf_index_folders", []) or []) if os.path.isdir(f)]

    def set_folders(self, folders: list[str]) -> None:
        self.ctl.db.set_setting("pdf_index_folders", list(dict.fromkeys(os.path.normpath(f) for f in folders)))

    def sources(self) -> list[str]:
        files: dict[str, None] = {}
        for r in self.ctl.db.get_setting("pdf_recent", []) or []:
            if r.get("path", "").lower().endswith(".pdf") and os.path.isfile(r["path"]):
                files[os.path.normpath(r["path"])] = None
        roots = list(self.folders())
        try:
            for s in self.ctl.db.list_subjects():
                roots += [f for f in self.ctl.subject_folders(s["id"]) if os.path.isdir(f)]
        except Exception:
            pass
        for root in dict.fromkeys(roots):
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in ("node_modules", "$recycle.bin")]
                for name in filenames:
                    if name.lower().endswith(".pdf"):
                        files[os.path.normpath(os.path.join(dirpath, name))] = None
                if len(files) >= MAX_FILES:
                    break
        return list(files)[:MAX_FILES]

    # ------------------------------------------------------------ indexing
    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        paths = self.sources()
        threading.Thread(target=self._work, args=(paths,), name="pdf-index", daemon=True).start()

    def _work(self, paths: list[str]) -> None:
        done = 0
        try:
            from PyQt6.QtPdf import QPdfDocument
            con = _connect()
            known = {r[0]: (r[1], r[2]) for r in con.execute("SELECT path, size, mtime FROM files")}
            todo = []
            for p in paths:
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if known.get(p) != (st.st_size, st.st_mtime):
                    todo.append((p, st.st_size, st.st_mtime))
            gone = [p for p in known if p not in set(paths) and not os.path.exists(p)]
            for p in gone:
                con.execute("DELETE FROM pages WHERE path = ?", (p,))
                con.execute("DELETE FROM files WHERE path = ?", (p,))
            con.commit()
            for i, (p, size, mtime) in enumerate(todo):
                if self._stop:
                    break
                self.progress.emit(i, len(todo))
                doc = QPdfDocument(None)
                if doc.load(p) != QPdfDocument.Error.None_:
                    con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)", (p, size, mtime, 0, time.time()))
                    con.commit()
                    continue
                n = min(doc.pageCount(), MAX_PAGES)
                rows = []
                for page in range(n):
                    try:
                        text = doc.getAllText(page).text()
                    except Exception:
                        text = ""
                    if text.strip():
                        rows.append((p, page, " ".join(text.split())))
                doc.close()
                con.execute("DELETE FROM pages WHERE path = ?", (p,))
                con.executemany("INSERT INTO pages(path, page, text) VALUES (?,?,?)", rows)
                con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?)", (p, size, mtime, n, time.time()))
                con.commit()
                done += 1
                time.sleep(0.01)  # leave the CPU to you
            con.close()
        except Exception:
            log.exception("pdf index")
        finally:
            self._busy = False
            self.finished.emit(done)

    @property
    def busy(self) -> bool:
        return self._busy

    def stats(self) -> tuple[int, int]:
        """(files, pages with text)"""
        try:
            con = _connect()
            files = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            pages = con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            con.close()
            return files, pages
        except sqlite3.Error:
            return 0, 0

    # ------------------------------------------------------------ search
    def search(self, text: str, limit: int = 200) -> list[dict]:
        q = fts_query(text)
        if not q:
            return []
        try:
            con = _connect()
            rows = con.execute(
                "SELECT path, page, snippet(pages, 2, '[[', ']]', ' … ', 14) FROM pages WHERE pages MATCH ? "
                "ORDER BY rank LIMIT ?", (q, limit)).fetchall()
            con.close()
        except sqlite3.Error as exc:
            log.info("pdf search: %s", exc)
            return []
        return [{"path": p, "page": int(pg), "snippet": sn} for p, pg, sn in rows if os.path.exists(p)]
