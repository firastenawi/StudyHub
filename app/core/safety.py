"""
Keeps the user's data safe:

* open_database(): harmless problems (unused pages) are fixed in place with VACUUM (database.py).
  If the database really can't be opened, it is moved aside to %APPDATA%\\StudyHub\\broken-<time>\\
  (never deleted); first everything still readable is copied out of it (VACUUM INTO — keeps the
  newest data), and only if that fails the newest *healthy* backup is restored; with no backup, a
  fresh database is created.
* daily_backup(): one automatic backup per day, the last 7 are kept (changeable on the Storage page).
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.config import BACKUP_DIR, DATA_DIR, DB_PATH
from app.core.database import Database
from app.core.logs import log

KEEP_DAILY = 7


def _healthy(path: Path) -> bool:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            con.close()
    except sqlite3.Error:
        return False


def _salvage(old: Path, path: Path, backup: Path | None) -> tuple[Database, list[str]]:
    """A fresh database filled table by table from the damaged one (read without its indexes, so a
    broken index doesn't matter). A table that can't be read at all comes from the backup instead."""
    db = Database(path)
    try:
        return _fill(db, old, backup)
    except sqlite3.Error:
        db.conn.close()  # let the caller delete the half-made copy
        raise


def _fill(db: Database, old: Path, backup: Path | None) -> tuple[Database, list[str]]:
    con = db.conn
    con.execute("PRAGMA foreign_keys = OFF")
    con.execute("ATTACH DATABASE ? AS old", (str(old),))
    if backup is not None:
        con.execute("ATTACH DATABASE ? AS bak", (str(backup),))
    missing: list[str] = []
    tables = con.execute("SELECT name, sql FROM old.sqlite_master WHERE type = 'table' "
                         "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%fts%'").fetchall()
    for name, sql in tables:
        if not con.execute("SELECT 1 FROM main.sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone():
            con.execute(sql)  # e.g. a plugin's table
        main_cols = {r[1] for r in con.execute(f"PRAGMA main.table_info({name})")}
        for source in ("old", "bak") if backup is not None else ("old",):
            try:
                cols = ", ".join(c for c in (r[1] for r in con.execute(f"PRAGMA {source}.table_info({name})"))
                                 if c in main_cols)
                with con:
                    con.execute(f"DELETE FROM main.{name}")
                    con.execute(f"INSERT INTO main.{name} ({cols}) SELECT {cols} FROM {source}.{name} NOT INDEXED")
                if source == "bak":
                    missing.append(name)
                break
            except sqlite3.DatabaseError as exc:
                log.error("salvage: table %s unreadable in %s (%s)", name, source, exc)
        else:
            missing.append(name + " (empty)")
    for alias in ("old", "bak") if backup is not None else ("old",):
        con.execute(f"DETACH DATABASE {alias}")
    con.execute("PRAGMA foreign_keys = ON")
    if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise sqlite3.DatabaseError("salvaged copy failed quick_check")
    return db, missing


def open_database(path: Path = DB_PATH) -> tuple[Database, dict | None]:
    """Returns (database, recovery_info). recovery_info is None when everything was fine."""
    try:
        return Database(path), None
    except sqlite3.DatabaseError as exc:
        log.error("Database could not be opened: %s", exc)

    # 1) move the damaged files aside (kept for manual recovery)
    broken = DATA_DIR / f"broken-{datetime.now():%Y%m%d-%H%M%S}"
    broken.mkdir(parents=True, exist_ok=True)
    for f in path.parent.glob(path.name + "*"):
        shutil.move(str(f), broken / f.name)
    log.error("Damaged database moved to %s", broken)

    # 2) copy out everything still readable (newer than any backup)
    backups = sorted(BACKUP_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    newest_ok = next((b for b in backups if _healthy(b)), None)
    moved = broken / path.name
    if moved.exists():
        try:
            db, missing = _salvage(moved, path, newest_ok)
            log.info("Salvaged the damaged database (tables taken from the backup: %s)", missing or "none")
            return db, {"salvaged": True, "restored": None, "missing": missing, "broken": str(broken)}
        except sqlite3.Error as exc:
            log.error("Salvage failed: %s", exc)
            for f in path.parent.glob(path.name + "*"):  # a half-written copy must not be used
                f.unlink(missing_ok=True)

    # 3) restore the newest healthy backup
    for backup in backups:
        if _healthy(backup):
            shutil.copy2(backup, path)
            log.info("Restored backup %s", backup.name)
            return Database(path), {"restored": backup.name, "broken": str(broken)}
    log.info("No healthy backup found, starting with a fresh database")
    return Database(path), {"restored": None, "broken": str(broken)}


def daily_backup(db: Database) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    autos = sorted(BACKUP_DIR.glob("auto-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    if autos and datetime.fromtimestamp(autos[0].stat().st_mtime) > datetime.now() - timedelta(hours=20):
        return
    target = BACKUP_DIR / f"auto-{datetime.now():%Y%m%d-%H%M%S}.db"
    try:
        db.backup(target)
        log.info("Daily backup written: %s", target.name)
    except sqlite3.Error as exc:
        log.error("Daily backup failed: %s", exc)
        return
    keep = max(1, int(db.get_setting("backups_keep", KEEP_DAILY)))  # Storage page: how many to keep
    for old in sorted(BACKUP_DIR.glob("auto-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)[keep:]:
        old.unlink(missing_ok=True)
