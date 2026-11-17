"""
SQLite data layer.

* All tables for every module (dashboard, study planner, launcher, files, notes)
  are created up front, so later phases only add UI.
* Schema versions are tracked with PRAGMA user_version -> simple migrations.
* Notes get a full-text index (FTS5) for instant search in Arabic and English.
* Every public method returns plain dicts, so the UI never touches SQL.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import BACKUP_DIR, DB_PATH

SCHEMA_VERSION = 7  # v2: organizer tables · v3: note items + desktop layout · v4: desktop widgets + archive
                    # v5: phone link (paired devices + transfer history)
                    # v6: notebook folders, #tags, [[links]], version history, trash, locked notes, templates
                    # v7: clipboard history, PDF highlights

SCHEMA = """
-- Key/value store for settings (values are JSON)
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ------------------------------------------------------------- study planner
CREATE TABLE IF NOT EXISTS subjects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    code        TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#7C5CFF',
    icon        TEXT NOT NULL DEFAULT '',
    teacher     TEXT NOT NULL DEFAULT '',
    folder_path TEXT NOT NULL DEFAULT '',        -- main folder of the subject
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

-- Weekly timetable. day_of_week: 0 = Monday ... 6 = Sunday (Python weekday())
CREATE TABLE IF NOT EXISTS schedule_slots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id  INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    start_time  TEXT NOT NULL,                   -- 'HH:MM'
    end_time    TEXT NOT NULL,
    location    TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'lecture'  -- lecture / lab / tutorial ...
);

-- To-do list. priority: 1 = high, 2 = medium, 3 = low
CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    details      TEXT NOT NULL DEFAULT '',
    subject_id   INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    priority     INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 3),
    due_date     TEXT,                           -- 'YYYY-MM-DD' or NULL
    status       TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'doing', 'done')),
    created_at   TEXT NOT NULL,
    completed_at TEXT
);

-- Pomodoro history (for statistics)
CREATE TABLE IF NOT EXISTS pomodoro_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id   INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    task_id      INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    started_at   TEXT NOT NULL,
    duration_min INTEGER NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'focus',  -- focus / short_break / long_break
    completed    INTEGER NOT NULL DEFAULT 1
);

-- ------------------------------------------------------------------ launcher
CREATE TABLE IF NOT EXISTS launcher_groups (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    color    TEXT NOT NULL DEFAULT '',
    icon     TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS launcher_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id      INTEGER REFERENCES launcher_groups(id) ON DELETE SET NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('app', 'url', 'folder', 'file', 'note', 'widget')),
    title         TEXT NOT NULL,
    target        TEXT NOT NULL,                 -- path, URL, note id (note) or widget source
    args          TEXT NOT NULL DEFAULT '',      -- command-line arguments (apps)
    icon_path     TEXT NOT NULL DEFAULT '',      -- custom icon / downloaded website logo
    color         TEXT NOT NULL DEFAULT '',      -- sticky-note color
    position      INTEGER NOT NULL DEFAULT 0,
    launch_count  INTEGER NOT NULL DEFAULT 0,
    last_launched TEXT,
    created_at    TEXT NOT NULL
);

-- free placement on the Desktop page. desk = 'all' or a group id (as text);
-- each desk remembers its own layout, positions are in grid cells
CREATE TABLE IF NOT EXISTS desktop_positions (
    desk    TEXT NOT NULL,
    item_id INTEGER NOT NULL REFERENCES launcher_items(id) ON DELETE CASCADE,
    col     INTEGER NOT NULL,
    row     INTEGER NOT NULL,
    w       INTEGER NOT NULL DEFAULT 1,
    h       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (desk, item_id)
);

-- "Study mode": launcher items opened together for a subject
CREATE TABLE IF NOT EXISTS subject_items (
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    item_id    INTEGER NOT NULL REFERENCES launcher_items(id) ON DELETE CASCADE,
    PRIMARY KEY (subject_id, item_id)
);

-- --------------------------------------------------------------------- notes
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL DEFAULT '',
    format     TEXT NOT NULL DEFAULT 'markdown', -- markdown / text
    subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    is_inbox   INTEGER NOT NULL DEFAULT 1,       -- quick notes land in the inbox
    is_pinned  INTEGER NOT NULL DEFAULT 0,
    source     TEXT NOT NULL DEFAULT 'manual',   -- manual / quick / clipboard / selection
    source_ref TEXT NOT NULL DEFAULT '',         -- e.g. title of the window it came from
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    folder_id   INTEGER REFERENCES note_folders(id) ON DELETE SET NULL,
    is_template INTEGER NOT NULL DEFAULT 0,
    deleted_at  TEXT,                            -- in the trash since (purged after 30 days)
    locked      INTEGER NOT NULL DEFAULT 0,      -- 1 = content encrypted with the vault (content stays '')
    secret      BLOB
);

-- notebook folders (nested)
CREATE TABLE IF NOT EXISTS note_folders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id  INTEGER REFERENCES note_folders(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    color      TEXT NOT NULL DEFAULT '#A78BFA',
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder_id);
-- #tags written inside notes, and [[links]] between notes (target = lower-cased title), rebuilt on save
CREATE TABLE IF NOT EXISTS note_tags (
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag     TEXT NOT NULL,
    PRIMARY KEY (note_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_note_tags_tag ON note_tags(tag);
CREATE TABLE IF NOT EXISTS note_links (
    src_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    target TEXT NOT NULL,
    PRIMARY KEY (src_id, target)
);
CREATE INDEX IF NOT EXISTS idx_note_links_target ON note_links(target);
-- snapshots for the history / restore (never for locked notes)
CREATE TABLE IF NOT EXISTS note_versions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id    INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    title      TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_note_versions ON note_versions(note_id, id);

-- clipboard history (kind: text | link | image | files); images live in %APPDATA%/StudyHub/clipboard
CREATE TABLE IF NOT EXISTS clipboard_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    content    TEXT NOT NULL DEFAULT '',             -- text / url / JSON list of paths / image file name
    preview    TEXT NOT NULL DEFAULT '',
    pinned     INTEGER NOT NULL DEFAULT 0,
    used       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clipboard ON clipboard_items(pinned, created_at);

-- PDF reader highlights (file_key = size + hash of the first MB, so moved files keep their highlights)
CREATE TABLE IF NOT EXISTS pdf_highlights (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    file_key   TEXT NOT NULL,
    path       TEXT NOT NULL,
    page       INTEGER NOT NULL,
    rects      TEXT NOT NULL,                        -- JSON [[x, y, w, h], ...] in PDF points
    text       TEXT NOT NULL DEFAULT '',
    color      TEXT NOT NULL DEFAULT '#FBBF24',
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pdf_highlights ON pdf_highlights(file_key, page);

-- --------------------------------------------------------------------- files
CREATE TABLE IF NOT EXISTS linked_folders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    label      TEXT NOT NULL DEFAULT '',
    UNIQUE (subject_id, path)
);

CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tracked_files (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    added_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_tags (
    file_id INTEGER NOT NULL REFERENCES tracked_files(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (file_id, tag_id)
);

-- ------------------------------------------------------------------- archive
-- every file/folder put into the archive, so it can be found and restored
CREATE TABLE IF NOT EXISTS archive_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    original_path TEXT NOT NULL,
    archive_path  TEXT NOT NULL,
    subject_id    INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    category      TEXT NOT NULL DEFAULT 'other',   -- lectures / assignments / exams / projects / other
    semester      TEXT NOT NULL DEFAULT '',
    mode          TEXT NOT NULL DEFAULT 'move',    -- move / copy / zip
    size          INTEGER NOT NULL DEFAULT 0,
    note          TEXT NOT NULL DEFAULT '',
    archived_at   TEXT NOT NULL
);

-- ------------------------------------------------------------- password vault
-- title / url / category are plain (for listing); username, password and notes live in `secret`,
-- encrypted with Windows DPAPI + a key derived from the master password (see core/vault.py)
CREATE TABLE IF NOT EXISTS vault_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT 'other',
    favorite      INTEGER NOT NULL DEFAULT 0,
    secret        BLOB NOT NULL,
    pw_changed_at TEXT NOT NULL,
    last_used     TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- -------------------------------------------------------------------- alarms
CREATE TABLE IF NOT EXISTS alarms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL DEFAULT '',
    time        TEXT NOT NULL,                  -- 'HH:MM' (24 h, shown in 12 h if chosen)
    days        TEXT NOT NULL DEFAULT '',       -- '0,1,2' (0 = Monday); '' = rings once
    enabled     INTEGER NOT NULL DEFAULT 1,
    sound       TEXT NOT NULL DEFAULT 'gentle', -- built-in sound key or an audio file path
    volume      INTEGER NOT NULL DEFAULT 80,    -- 0..100
    gradual     INTEGER NOT NULL DEFAULT 1,     -- volume rises slowly over 30 s
    snooze_min  INTEGER NOT NULL DEFAULT 5,
    max_snoozes INTEGER NOT NULL DEFAULT 3,
    challenge   TEXT NOT NULL DEFAULT 'none',   -- none / math / type: what it takes to dismiss
    color       TEXT NOT NULL DEFAULT '',
    last_fired  TEXT,                           -- 'YYYY-MM-DD HH:MM' (never twice the same minute)
    created_at  TEXT NOT NULL
);

-- ------------------------------------------------- downloads organizer (v2)
-- learned "word in a file name -> subject" rules, strengthened by every move
CREATE TABLE IF NOT EXISTS organizer_rules (
    token      TEXT NOT NULL,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    hits       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (token, subject_id)
);

CREATE TABLE IF NOT EXISTS organizer_ignored (
    path       TEXT PRIMARY KEY,
    ignored_at TEXT NOT NULL
);

-- every move made by the organizer, so it can be undone
CREATE TABLE IF NOT EXISTS move_history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    src      TEXT NOT NULL,
    dst      TEXT NOT NULL,
    moved_at TEXT NOT NULL
);

-- ----------------------------------------------------------------- dashboard
-- ---------------------------------------------------------- phone link (LAN)
-- each paired phone has its own secret; only its SHA-256 is stored (revoking = deleting the row)
CREATE TABLE IF NOT EXISTS phone_devices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    last_seen   TEXT,
    last_ip     TEXT NOT NULL DEFAULT ''
);
-- everything that went phone -> laptop ('in') or laptop -> phone ('out')
-- kind: note | link | clip | file | task (in) · text | link | file (out); content = text, url or file path
CREATE TABLE IF NOT EXISTS phone_transfers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    direction   TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',
    size        INTEGER NOT NULL DEFAULT 0,
    device_id   INTEGER REFERENCES phone_devices(id) ON DELETE SET NULL,
    subject_id  INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    fetched     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_phone_transfers ON phone_transfers(direction, created_at);

CREATE TABLE IF NOT EXISTS quotes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    text_ar   TEXT NOT NULL,
    text_en   TEXT NOT NULL,
    author_ar TEXT NOT NULL DEFAULT '',
    author_en TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_due   ON tasks(status, due_date);
CREATE INDEX IF NOT EXISTS idx_notes_inbox        ON notes(is_inbox, updated_at);
CREATE INDEX IF NOT EXISTS idx_items_group        ON launcher_items(group_id, position);
CREATE INDEX IF NOT EXISTS idx_slots_day          ON schedule_slots(day_of_week, start_time);
CREATE INDEX IF NOT EXISTS idx_pomodoro_started   ON pomodoro_sessions(started_at);
"""

# Full-text search index kept in sync with `notes` by triggers
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title, content, content='notes', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, content) VALUES ('delete', old.id, old.title, old.content);
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE OF title, content ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, content) VALUES ('delete', old.id, old.title, old.content);
    INSERT INTO notes_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;
"""

SEED_QUOTES = [
    ("النجاح هو مجموع جهود صغيرة تتكرر يوماً بعد يوم.",
     "Success is the sum of small efforts, repeated day in and day out.", "روبرت كولير", "Robert Collier"),
    ("ابدأ من حيث أنت، واستخدم ما لديك، وافعل ما تستطيع.",
     "Start where you are. Use what you have. Do what you can.", "آرثر آش", "Arthur Ashe"),
    ("العلم في الصغر كالنقش على الحجر.",
     "Learning when young is like carving in stone.", "مثل عربي", "Arabic proverb"),
    ("من جدّ وجد، ومن زرع حصد.",
     "Whoever strives shall find, and whoever sows shall reap.", "مثل عربي", "Arabic proverb"),
    ("لا تؤجل عمل اليوم إلى الغد.",
     "Don't put off until tomorrow what you can do today.", "مثل", "Proverb"),
    ("ساعة تركيز حقيقي أفضل من يوم كامل من التشتت.",
     "One hour of real focus beats a whole day of distraction.", "", ""),
    ("التقدم البطيء يبقى تقدماً.",
     "Slow progress is still progress.", "", ""),
    ("الانضباط هو أن تختار ما تريده أكثر بدلاً مما تريده الآن.",
     "Discipline is choosing what you want most over what you want now.", "", ""),
    ("كل خبير كان يوماً ما مبتدئاً.",
     "Every expert was once a beginner.", "", ""),
    ("اطلبوا العلم من المهد إلى اللحد.",
     "Seek knowledge from the cradle to the grave.", "قول مأثور", "Saying"),
    ("لا تقارن بدايتك بمنتصف طريق غيرك.",
     "Don't compare your beginning to someone else's middle.", "", ""),
    ("قليلٌ دائم خيرٌ من كثيرٍ منقطع.",
     "A little that lasts is better than a lot that stops.", "قول مأثور", "Saying"),
]

# (group name, color, items[(kind, title, target)])
SEED_LAUNCHER = [
    ("الدراسة", "#7C5CFF", [
        ("url", "Google Scholar", "https://scholar.google.com"),
        ("url", "Wikipedia", "https://www.wikipedia.org"),
        ("url", "Google Translate", "https://translate.google.com"),
    ]),
    ("مواقع", "#22D3EE", [
        ("url", "Google", "https://www.google.com"),
        ("url", "YouTube", "https://www.youtube.com"),
        ("url", "Google Drive", "https://drive.google.com"),
    ]),
    ("أدوات", "#F59E0B", [
        ("app", "Calculator", r"C:\Windows\System32\calc.exe"),
        ("app", "Notepad", r"C:\Windows\System32\notepad.exe"),
        ("folder", "Downloads", str(Path.home() / "Downloads")),
    ]),
]


# quick_check messages that only mean "some space is lost" (pages no table uses): the data is all
# there and VACUUM rebuilds the file cleanly — no reason to treat the database as damaged
_HARMLESS = re.compile(r"^(\*\*\* in database \w+ \*\*\*|Page \d+:? (is )?never used|Freelist: .*)$", re.I)


def harmless_problems(messages: list[str]) -> bool:
    lines = [line.strip() for m in messages for line in m.splitlines() if line.strip()]
    return bool(lines) and all(_HARMLESS.match(line) for line in lines)


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path), timeout=15)  # wait for locks instead of failing
        self.conn.row_factory = sqlite3.Row
        self.has_fts = False
        try:
            self.conn.execute("PRAGMA foreign_keys = ON")
            problems = [r[0] for r in self.conn.execute("PRAGMA quick_check(100)")]
            if problems != ["ok"]:
                if not harmless_problems(problems):
                    raise sqlite3.DatabaseError("database failed quick_check: " + "; ".join(problems)[:300])
                logging.getLogger(__name__).warning("unused pages in the database, compacting: %s",
                                                    "; ".join(problems)[:300])
                self.conn.execute("VACUUM")  # rebuilds the file; every row is kept
                if self.conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError("database failed quick_check after VACUUM")
            self.conn.execute("PRAGMA journal_mode = WAL")  # faster, crash-safe writes
            self._migrate()
        except sqlite3.DatabaseError:
            self.conn.close()  # release the file so it can be moved aside / restored
            raise

    # ------------------------------------------------------------ low level
    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if 0 < version < SCHEMA_VERSION:
            # an upgrade is about to change the file: keep a copy of the old version first
            self.backup(BACKUP_DIR / f"before-v{SCHEMA_VERSION}-{datetime.now():%Y%m%d-%H%M%S}.db")
        if version > 0:
            self._add_missing_columns("notes", {
                "folder_id": "INTEGER REFERENCES note_folders(id) ON DELETE SET NULL",
                "is_template": "INTEGER NOT NULL DEFAULT 0",
                "deleted_at": "TEXT",
                "locked": "INTEGER NOT NULL DEFAULT 0",
                "secret": "BLOB",
            })
        with self.conn:
            self.conn.executescript(SCHEMA)
        try:
            with self.conn:
                self.conn.executescript(FTS_SCHEMA)
            self.has_fts = True
        except sqlite3.OperationalError:
            self.has_fts = False  # SQLite built without FTS5 -> LIKE search fallback
        if version < 1:
            self._seed()
        if 0 < version < 4:
            self._rebuild_launcher_items()
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _add_missing_columns(self, table: str, columns: dict[str, str]) -> None:
        have = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        with self.conn:
            for name, ddl in columns.items():
                if name not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def _rebuild_launcher_items(self) -> None:
        """Lets launcher_items accept new kinds ('note' in v3, 'widget' in v4). SQLite cannot change
        a CHECK constraint, so the table is rebuilt (the official 12-step recipe, ids are kept).
        A backup of the old file is always written first (see _migrate)."""
        sql = self.scalar("SELECT sql FROM sqlite_master WHERE name = 'launcher_items'", default="")
        if "'widget'" in sql:  # already allows every current kind
            return
        columns = ("id, group_id, kind, title, target, args, icon_path, color, position, launch_count, "
                   "last_launched, created_at")
        new_table = SCHEMA.split("CREATE TABLE IF NOT EXISTS launcher_items")[1].split(");")[0] + ");"
        self.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self.conn.executescript(
                "BEGIN;"
                f"CREATE TABLE launcher_items_v3 {new_table}"
                f"INSERT INTO launcher_items_v3 ({columns}) SELECT {columns} FROM launcher_items;"
                "DROP TABLE launcher_items;"
                "ALTER TABLE launcher_items_v3 RENAME TO launcher_items;"
                "CREATE INDEX IF NOT EXISTS idx_items_group ON launcher_items(group_id, position);"
                "COMMIT;")
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")

    def _seed(self) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO quotes(text_ar, text_en, author_ar, author_en) VALUES (?,?,?,?)", SEED_QUOTES)
            for position, (name, color, items) in enumerate(SEED_LAUNCHER):
                cur = self.conn.execute(
                    "INSERT INTO launcher_groups(name, color, position) VALUES (?,?,?)", (name, color, position))
                for item_pos, (kind, title, target) in enumerate(items):
                    self.conn.execute(
                        "INSERT INTO launcher_items(group_id, kind, title, target, position, created_at) "
                        "VALUES (?,?,?,?,?,?)", (cur.lastrowid, kind, title, target, item_pos, now_str()))

    def query(self, sql: str, params: tuple | list = ()) -> list[dict]:
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | list = ()) -> dict | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def scalar(self, sql: str, params: tuple | list = (), default: Any = None) -> Any:
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row and row[0] is not None else default

    def execute(self, sql: str, params: tuple | list = ()) -> int:
        """Runs a write statement in its own transaction, returns lastrowid."""
        with self.conn:
            return self.conn.execute(sql, params).lastrowid

    def _update(self, table: str, row_id: int, fields: dict) -> None:
        if not fields:
            return
        columns = ", ".join(f"{name} = ?" for name in fields)
        self.execute(f"UPDATE {table} SET {columns} WHERE id = ?", [*fields.values(), row_id])

    def close(self) -> None:
        """Folds the WAL back into the main file, so the .db alone is always complete."""
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        self.conn.close()

    def backup(self, destination: Path | str) -> None:
        """Consistent copy of the database, even while the app is running."""
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(destination))
        try:
            self.conn.backup(target)
        finally:
            target.close()

    # ------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        value = self.scalar("SELECT value FROM settings WHERE key = ?", (key,))
        if value is None:
            return default
        try:
            return json.loads(value)
        except ValueError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))

    # ---------------------------------------------------- launcher groups
    def list_groups(self) -> list[dict]:
        return self.query("SELECT * FROM launcher_groups ORDER BY position, id")

    def add_group(self, name: str, color: str = "") -> int:
        position = self.scalar("SELECT COALESCE(MAX(position), -1) + 1 FROM launcher_groups", default=0)
        return self.execute("INSERT INTO launcher_groups(name, color, position) VALUES (?,?,?)",
                            (name, color, position))

    def rename_group(self, group_id: int, name: str) -> None:
        self._update("launcher_groups", group_id, {"name": name})

    def delete_group(self, group_id: int) -> None:
        self.execute("DELETE FROM launcher_groups WHERE id = ?", (group_id,))  # items keep living, ungrouped

    # ----------------------------------------------------- launcher items
    def list_items(self, group_id: int | None = None) -> list[dict]:
        if group_id is None:
            return self.query("SELECT * FROM launcher_items ORDER BY COALESCE(group_id, 1e9), position, id")
        return self.query("SELECT * FROM launcher_items WHERE group_id = ? ORDER BY position, id", (group_id,))

    def get_item(self, item_id: int) -> dict | None:
        return self.one("SELECT * FROM launcher_items WHERE id = ?", (item_id,))

    def add_item(self, kind: str, title: str, target: str, group_id: int | None = None,
                 args: str = "", icon_path: str = "", color: str = "") -> int:
        position = self.scalar("SELECT COALESCE(MAX(position), -1) + 1 FROM launcher_items", default=0)
        return self.execute(
            "INSERT INTO launcher_items(group_id, kind, title, target, args, icon_path, color, position, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (group_id, kind, title, target, args, icon_path, color, position, now_str()))

    def update_item(self, item_id: int, **fields) -> None:
        self._update("launcher_items", item_id, fields)

    def delete_item(self, item_id: int) -> None:
        self.execute("DELETE FROM launcher_items WHERE id = ?", (item_id,))

    def record_launch(self, item_id: int) -> None:
        self.execute("UPDATE launcher_items SET launch_count = launch_count + 1, last_launched = ? "
                     "WHERE id = ?", (now_str(), item_id))

    def search_items(self, text: str, limit: int = 8) -> list[dict]:
        if not text:
            return self.query("SELECT * FROM launcher_items ORDER BY launch_count DESC, "
                              "last_launched DESC LIMIT ?", (limit,))
        like = f"%{text}%"
        return self.query("SELECT * FROM launcher_items WHERE title LIKE ? OR target LIKE ? "
                          "ORDER BY launch_count DESC LIMIT ?", (like, like, limit))

    # ------------------------------------------------------------ desktop
    def desk_items(self, desk: str) -> list[dict]:
        """Items shown on a desk: 'all' (the main desktop) = items without a group, otherwise one group's
        items. Every desktop is separate: adding to a group's desktop never shows up on the main one."""
        if desk == "all":
            return self.query("SELECT * FROM launcher_items WHERE group_id IS NULL ORDER BY position, id")
        return self.query("SELECT * FROM launcher_items WHERE group_id = ? ORDER BY position, id", (int(desk),))

    def desk_positions(self, desk: str) -> dict[int, tuple[int, int, int, int]]:
        return {r["item_id"]: (r["col"], r["row"], r["w"], r["h"])
                for r in self.query("SELECT * FROM desktop_positions WHERE desk = ?", (desk,))}

    def set_positions(self, desk: str, positions: dict[int, tuple[int, int, int, int]]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO desktop_positions(desk, item_id, col, row, w, h) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(desk, item_id) DO UPDATE SET col = excluded.col, row = excluded.row, "
                "w = excluded.w, h = excluded.h",
                [(desk, item_id, *pos) for item_id, pos in positions.items()])

    def clear_positions(self, desk: str) -> None:
        self.execute("DELETE FROM desktop_positions WHERE desk = ?", (desk,))

    # ---------------------------------------------------------------- notes
    def add_note(self, content: str, title: str = "", source: str = "manual",
                 source_ref: str = "", is_inbox: bool = True, subject_id: int | None = None) -> int:
        stamp = now_str()
        return self.execute(
            "INSERT INTO notes(title, content, source, source_ref, is_inbox, subject_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (title, content, source, source_ref, int(is_inbox), subject_id, stamp, stamp))

    def update_note(self, note_id: int, **fields) -> None:
        fields["updated_at"] = now_str()
        self._update("notes", note_id, fields)

    def get_note(self, note_id: int) -> dict | None:
        return self.one("SELECT * FROM notes WHERE id = ?", (note_id,))

    def delete_note(self, note_id: int) -> None:
        self.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    def recent_notes(self, limit: int = 5, inbox_only: bool = True) -> list[dict]:
        where = "WHERE deleted_at IS NULL AND is_template = 0" + (" AND is_inbox = 1" if inbox_only else "")
        return self.query(f"SELECT * FROM notes {where} ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,))

    def count_inbox(self) -> int:
        return self.scalar("SELECT COUNT(*) FROM notes WHERE is_inbox = 1 AND deleted_at IS NULL "
                           "AND is_template = 0", default=0)

    def search_notes(self, text: str, limit: int = 10) -> list[dict]:
        words = [w for w in text.split() if w]
        if not words:
            return []
        if self.has_fts:
            # every word as a prefix search: "word"*  (quotes escaped for FTS syntax)
            match = " ".join('"' + w.replace('"', '""') + '"*' for w in words)
            try:
                return self.query("SELECT n.* FROM notes_fts f JOIN notes n ON n.id = f.rowid "
                                  "WHERE notes_fts MATCH ? AND n.deleted_at IS NULL ORDER BY rank LIMIT ?",
                                  (match, limit))
            except sqlite3.OperationalError:
                pass
        like = f"%{text}%"
        return self.query("SELECT * FROM notes WHERE (title LIKE ? OR content LIKE ?) AND deleted_at IS NULL "
                          "ORDER BY updated_at DESC LIMIT ?", (like, like, limit))

    # ---------------------------------------------------------------- tasks
    def add_task(self, title: str, priority: int = 2, due_date: str | None = None,
                 subject_id: int | None = None, details: str = "") -> int:
        return self.execute(
            "INSERT INTO tasks(title, details, subject_id, priority, due_date, created_at) VALUES (?,?,?,?,?,?)",
            (title, details, subject_id, priority, due_date, now_str()))

    def update_task(self, task_id: int, **fields) -> None:
        self._update("tasks", task_id, fields)

    def set_task_done(self, task_id: int, done: bool) -> None:
        self._update("tasks", task_id, {"status": "done" if done else "todo",
                                        "completed_at": now_str() if done else None})

    def delete_task(self, task_id: int) -> None:
        self.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def tasks_for_today(self, limit: int = 50) -> list[dict]:
        """Open tasks that are due today, overdue, or have no due date."""
        today = date.today().isoformat()
        return self.query(
            "SELECT t.*, s.name AS subject_name, s.color AS subject_color FROM tasks t "
            "LEFT JOIN subjects s ON s.id = t.subject_id "
            "WHERE t.status != 'done' AND (t.due_date IS NULL OR t.due_date <= ?) "
            "ORDER BY t.priority, COALESCE(t.due_date, '9999'), t.id LIMIT ?", (today, limit))

    def count_open_tasks_today(self) -> int:
        today = date.today().isoformat()
        return self.scalar("SELECT COUNT(*) FROM tasks WHERE status != 'done' "
                           "AND (due_date IS NULL OR due_date <= ?)", (today,), default=0)

    # ------------------------------------------------------------- schedule
    def upcoming_classes(self, when: datetime | None = None) -> list[dict]:
        """Today's classes that have not finished yet."""
        when = when or datetime.now()
        return self.query(
            "SELECT sl.*, s.name AS subject_name, s.color AS subject_color FROM schedule_slots sl "
            "JOIN subjects s ON s.id = sl.subject_id "
            "WHERE sl.day_of_week = ? AND sl.end_time > ? AND s.archived = 0 ORDER BY sl.start_time",
            (when.weekday(), when.strftime("%H:%M")))

    # ------------------------------------------------------------- pomodoro
    def add_pomodoro(self, duration_min: int, kind: str = "focus", subject_id: int | None = None,
                     task_id: int | None = None, completed: bool = True) -> int:
        return self.execute(
            "INSERT INTO pomodoro_sessions(subject_id, task_id, started_at, duration_min, kind, completed) "
            "VALUES (?,?,?,?,?,?)", (subject_id, task_id, now_str(), duration_min, kind, int(completed)))

    def focus_minutes_today(self) -> int:
        return self.scalar("SELECT SUM(duration_min) FROM pomodoro_sessions WHERE kind = 'focus' "
                           "AND completed = 1 AND date(started_at) = ?", (date.today().isoformat(),), default=0)

    def pomodoro_count_today(self) -> int:
        return self.scalar("SELECT COUNT(*) FROM pomodoro_sessions WHERE kind = 'focus' "
                           "AND completed = 1 AND date(started_at) = ?", (date.today().isoformat(),), default=0)

    def focus_minutes_by_day(self, days: int = 7) -> list[tuple[str, int]]:
        """[(YYYY-MM-DD, minutes)] for the last `days` days, oldest first, zeros included."""
        start = date.today() - timedelta(days=days - 1)
        rows = {r["d"]: r["m"] for r in self.query(
            "SELECT date(started_at) AS d, SUM(duration_min) AS m FROM pomodoro_sessions "
            "WHERE kind = 'focus' AND completed = 1 AND date(started_at) >= ? GROUP BY d", (start.isoformat(),))}
        return [((start + timedelta(days=i)).isoformat(), rows.get((start + timedelta(days=i)).isoformat(), 0))
                for i in range(days)]

    def focus_minutes_by_subject(self, days: int = 7) -> list[dict]:
        start = (date.today() - timedelta(days=days - 1)).isoformat()
        return self.query(
            "SELECT COALESCE(s.name, '') AS name, COALESCE(s.color, '#8C95A8') AS color, "
            "SUM(p.duration_min) AS minutes FROM pomodoro_sessions p LEFT JOIN subjects s ON s.id = p.subject_id "
            "WHERE p.kind = 'focus' AND p.completed = 1 AND date(p.started_at) >= ? "
            "GROUP BY p.subject_id ORDER BY minutes DESC", (start,))

    # ------------------------------------------------------------- subjects
    def list_subjects(self, include_archived: bool = False) -> list[dict]:
        where = "" if include_archived else "WHERE archived = 0"
        return self.query(f"SELECT * FROM subjects {where} ORDER BY name COLLATE NOCASE")

    def get_subject(self, subject_id: int) -> dict | None:
        return self.one("SELECT * FROM subjects WHERE id = ?", (subject_id,))

    def add_subject(self, name: str, color: str = "#7C5CFF", code: str = "", teacher: str = "",
                    folder_path: str = "") -> int:
        return self.execute(
            "INSERT INTO subjects(name, color, code, teacher, folder_path, created_at) VALUES (?,?,?,?,?,?)",
            (name, color, code, teacher, folder_path, now_str()))

    def update_subject(self, subject_id: int, **fields) -> None:
        self._update("subjects", subject_id, fields)

    def delete_subject(self, subject_id: int) -> None:
        self.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))  # slots cascade, tasks/notes detach

    def subject_stats(self, subject_id: int) -> dict:
        week_start = (date.today() - timedelta(days=6)).isoformat()
        return {
            "slots": self.scalar("SELECT COUNT(*) FROM schedule_slots WHERE subject_id = ?", (subject_id,), default=0),
            "open_tasks": self.scalar("SELECT COUNT(*) FROM tasks WHERE subject_id = ? AND status != 'done'",
                                      (subject_id,), default=0),
            "notes": self.scalar("SELECT COUNT(*) FROM notes WHERE subject_id = ?", (subject_id,), default=0),
            "focus_week": self.scalar("SELECT SUM(duration_min) FROM pomodoro_sessions WHERE subject_id = ? "
                                      "AND kind = 'focus' AND completed = 1 AND date(started_at) >= ?",
                                      (subject_id, week_start), default=0),
        }

    def subject_item_ids(self, subject_id: int) -> list[int]:
        return [r["item_id"] for r in self.query(
            "SELECT item_id FROM subject_items WHERE subject_id = ?", (subject_id,))]

    def set_subject_items(self, subject_id: int, item_ids: list[int]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM subject_items WHERE subject_id = ?", (subject_id,))
            self.conn.executemany("INSERT INTO subject_items(subject_id, item_id) VALUES (?, ?)",
                                  [(subject_id, i) for i in item_ids])

    # ------------------------------------------------------- schedule slots
    def list_slots(self) -> list[dict]:
        return self.query(
            "SELECT sl.*, s.name AS subject_name, s.color AS subject_color FROM schedule_slots sl "
            "JOIN subjects s ON s.id = sl.subject_id WHERE s.archived = 0 "
            "ORDER BY sl.day_of_week, sl.start_time")

    def get_slot(self, slot_id: int) -> dict | None:
        return self.one("SELECT * FROM schedule_slots WHERE id = ?", (slot_id,))

    def add_slot(self, subject_id: int, day_of_week: int, start_time: str, end_time: str,
                 location: str = "", kind: str = "lecture") -> int:
        return self.execute(
            "INSERT INTO schedule_slots(subject_id, day_of_week, start_time, end_time, location, kind) "
            "VALUES (?,?,?,?,?,?)", (subject_id, day_of_week, start_time, end_time, location, kind))

    def update_slot(self, slot_id: int, **fields) -> None:
        self._update("schedule_slots", slot_id, fields)

    def delete_slot(self, slot_id: int) -> None:
        self.execute("DELETE FROM schedule_slots WHERE id = ?", (slot_id,))

    # ------------------------------------------------------- task views
    TASK_VIEWS = {
        "today": "t.status != 'done' AND (t.due_date IS NULL OR t.due_date <= :today)",
        "upcoming": "t.status != 'done' AND t.due_date > :today",
        "overdue": "t.status != 'done' AND t.due_date < :today",
        "open": "t.status != 'done'",
        "done": "t.status = 'done'",
    }

    def list_tasks(self, view: str = "open", search: str = "", subject_id: int | None = None) -> list[dict]:
        where = [self.TASK_VIEWS.get(view, self.TASK_VIEWS["open"])]
        params: dict = {"today": date.today().isoformat()}
        if search:
            where.append("(t.title LIKE :q OR t.details LIKE :q)")
            params["q"] = f"%{search}%"
        if subject_id is not None:
            where.append("t.subject_id = :sid")
            params["sid"] = subject_id
        order = "t.completed_at DESC" if view == "done" else "COALESCE(t.due_date, '9999'), t.priority, t.id"
        return self.query(
            "SELECT t.*, s.name AS subject_name, s.color AS subject_color FROM tasks t "
            f"LEFT JOIN subjects s ON s.id = t.subject_id WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 300",
            params)

    def count_tasks_by_view(self) -> dict[str, int]:
        today = {"today": date.today().isoformat()}
        return {view: self.scalar(f"SELECT COUNT(*) FROM tasks t WHERE {cond}", today, default=0)
                for view, cond in self.TASK_VIEWS.items()}

    def get_task(self, task_id: int) -> dict | None:
        return self.one("SELECT * FROM tasks WHERE id = ?", (task_id,))

    # ------------------------------------------------------- notebook views
    SORTS = {"updated": "updated_at DESC", "created": "created_at DESC", "title": "title COLLATE NOCASE"}

    def list_notes(self, view: str = "all", key=None, search: str = "", sort: str = "updated") -> list[dict]:
        """view: inbox | all | pinned | subject | folder (with sub-folders) | tag | templates | trash"""
        order = f"is_pinned DESC, {self.SORTS.get(sort, self.SORTS['updated'])}"
        live = "deleted_at IS NULL"
        if view == "trash":
            return self.query("SELECT * FROM notes WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC")
        if search:
            ids = [n["id"] for n in self.search_notes(search, 200)]
            if not ids:
                return []
            marks = ",".join("?" * len(ids))
            return self.query(f"SELECT * FROM notes WHERE id IN ({marks}) AND {live} ORDER BY {order}", ids)
        params: tuple = ()
        if view == "templates":
            where = f"{live} AND is_template = 1"
        elif view == "inbox":
            where = f"{live} AND is_template = 0 AND is_inbox = 1"
        elif view == "pinned":
            where = f"{live} AND is_template = 0 AND is_pinned = 1"
        elif view == "subject" and key is not None:
            where, params = f"{live} AND is_template = 0 AND subject_id = ?", (key,)
        elif view == "folder" and key is not None:
            ids = self.folder_tree_ids(key)
            where, params = f"{live} AND folder_id IN ({','.join('?' * len(ids))})", tuple(ids)
        elif view == "tag" and key:
            where, params = f"{live} AND id IN (SELECT note_id FROM note_tags WHERE tag = ?)", (key,)
        else:
            where = f"{live} AND is_template = 0"
        return self.query(f"SELECT * FROM notes WHERE {where} ORDER BY {order} LIMIT 1000", params)

    def count_notes_by_view(self) -> dict:
        live = "deleted_at IS NULL AND is_template = 0"
        counts = {"inbox": self.count_inbox(),
                  "all": self.scalar(f"SELECT COUNT(*) FROM notes WHERE {live}", default=0),
                  "pinned": self.scalar(f"SELECT COUNT(*) FROM notes WHERE {live} AND is_pinned = 1", default=0),
                  "templates": self.scalar("SELECT COUNT(*) FROM notes WHERE deleted_at IS NULL AND is_template = 1",
                                           default=0),
                  "trash": self.scalar("SELECT COUNT(*) FROM notes WHERE deleted_at IS NOT NULL", default=0)}
        for row in self.query(f"SELECT subject_id, COUNT(*) AS c FROM notes WHERE subject_id IS NOT NULL AND {live} "
                              "GROUP BY subject_id"):
            counts[row["subject_id"]] = row["c"]
        folder_counts = {r["folder_id"]: r["c"] for r in self.query(
            "SELECT folder_id, COUNT(*) AS c FROM notes WHERE folder_id IS NOT NULL AND deleted_at IS NULL "
            "GROUP BY folder_id")}
        for f in self.list_folders():
            counts[("folder", f["id"])] = sum(folder_counts.get(i, 0) for i in self.folder_tree_ids(f["id"]))
        return counts

    # ------------------------------------------------------- notebook folders
    def list_folders(self) -> list[dict]:
        return self.query("SELECT * FROM note_folders ORDER BY position, name COLLATE NOCASE")

    def get_folder(self, folder_id: int) -> dict | None:
        return self.one("SELECT * FROM note_folders WHERE id = ?", (folder_id,))

    def add_folder(self, name: str, parent_id: int | None = None, color: str = "#A78BFA") -> int:
        return self.execute("INSERT INTO note_folders(parent_id, name, color, created_at) VALUES (?,?,?,?)",
                            (parent_id, name, color, now_str()))

    def update_folder(self, folder_id: int, **fields) -> None:
        self._update("note_folders", folder_id, {k: v for k, v in fields.items()
                                                  if k in ("name", "color", "parent_id", "position")})

    def folder_tree_ids(self, folder_id: int) -> list[int]:
        rows = self.query("WITH RECURSIVE t(id) AS (SELECT ? UNION ALL "
                          "SELECT f.id FROM note_folders f JOIN t ON f.parent_id = t.id) SELECT id FROM t",
                          (folder_id,))
        return [r["id"] for r in rows]

    def delete_folder(self, folder_id: int) -> None:
        """Its notes and sub-folders move up one level; nothing is deleted with it."""
        folder = self.get_folder(folder_id)
        if not folder:
            return
        with self.conn:
            self.conn.execute("UPDATE notes SET folder_id = ? WHERE folder_id = ?", (folder["parent_id"], folder_id))
            self.conn.execute("UPDATE note_folders SET parent_id = ? WHERE parent_id = ?",
                              (folder["parent_id"], folder_id))
            self.conn.execute("DELETE FROM note_folders WHERE id = ?", (folder_id,))

    def folder_by_path(self, names: list[str]) -> int | None:
        """Finds or creates nested folders a/b/c; returns the last id."""
        parent = None
        for name in names:
            row = self.one("SELECT id FROM note_folders WHERE name = ? AND parent_id IS ?", (name, parent))
            parent = row["id"] if row else self.add_folder(name, parent)
        return parent

    # --------------------------------------------------------- tags & links
    def set_note_index(self, note_id: int, tags: list[str], links: list[str]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
            self.conn.execute("DELETE FROM note_links WHERE src_id = ?", (note_id,))
            self.conn.executemany("INSERT OR IGNORE INTO note_tags(note_id, tag) VALUES (?,?)",
                                  [(note_id, t) for t in tags])
            self.conn.executemany("INSERT OR IGNORE INTO note_links(src_id, target) VALUES (?,?)",
                                  [(note_id, l) for l in links])

    def list_note_tags(self) -> list[dict]:
        return self.query("SELECT t.tag, COUNT(*) AS count FROM note_tags t JOIN notes n ON n.id = t.note_id "
                          "WHERE n.deleted_at IS NULL GROUP BY t.tag ORDER BY count DESC, t.tag")

    def backlinks(self, title: str) -> list[dict]:
        if not title.strip():
            return []
        return self.query("SELECT n.id, n.title, n.updated_at FROM note_links l JOIN notes n ON n.id = l.src_id "
                          "WHERE l.target = ? AND n.deleted_at IS NULL ORDER BY n.updated_at DESC",
                          (title.strip().lower(),))

    def note_by_title(self, title: str) -> dict | None:
        return self.one("SELECT * FROM notes WHERE lower(trim(title)) = ? AND deleted_at IS NULL "
                        "ORDER BY is_template, updated_at DESC LIMIT 1", (title.strip().lower(),))

    def note_titles(self) -> list[dict]:
        return self.query("SELECT id, title, updated_at, is_template FROM notes WHERE deleted_at IS NULL "
                          "AND title != '' ORDER BY updated_at DESC")

    def notes_linking_to(self, title: str) -> list[dict]:
        return self.query("SELECT n.* FROM note_links l JOIN notes n ON n.id = l.src_id WHERE l.target = ?",
                          (title.strip().lower(),))

    # ------------------------------------------------------------- versions
    def add_version(self, note_id: int, title: str, content: str, keep: int = 60) -> None:
        self.execute("INSERT INTO note_versions(note_id, title, content, created_at) VALUES (?,?,?,?)",
                     (note_id, title, content, now_str()))
        self.execute("DELETE FROM note_versions WHERE note_id = ? AND id NOT IN "
                     "(SELECT id FROM note_versions WHERE note_id = ? ORDER BY id DESC LIMIT ?)",
                     (note_id, note_id, keep))

    def last_version(self, note_id: int) -> dict | None:
        return self.one("SELECT * FROM note_versions WHERE note_id = ? ORDER BY id DESC LIMIT 1", (note_id,))

    def list_versions(self, note_id: int) -> list[dict]:
        return self.query("SELECT * FROM note_versions WHERE note_id = ? ORDER BY id DESC", (note_id,))

    def delete_versions(self, note_id: int) -> None:
        self.execute("DELETE FROM note_versions WHERE note_id = ?", (note_id,))

    # ----------------------------------------------------------------- trash
    def trash_note(self, note_id: int) -> None:
        self.execute("UPDATE notes SET deleted_at = ? WHERE id = ?", (now_str(), note_id))

    def restore_note(self, note_id: int) -> None:
        self.execute("UPDATE notes SET deleted_at = NULL WHERE id = ?", (note_id,))

    def expired_trash(self, days: int = 30) -> list[int]:
        limit = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        return [r["id"] for r in self.query("SELECT id FROM notes WHERE deleted_at IS NOT NULL AND deleted_at < ?",
                                            (limit,))]

    def all_live_notes(self) -> list[dict]:
        return self.query("SELECT id, title, content, locked, updated_at FROM notes WHERE deleted_at IS NULL "
                          "AND is_template = 0 ORDER BY updated_at DESC")

    # ------------------------------------------------------- linked folders
    def list_linked_folders(self, subject_id: int | None = None) -> list[dict]:
        where, params = ("WHERE lf.subject_id = ?", (subject_id,)) if subject_id is not None else ("", ())
        return self.query(
            "SELECT lf.*, s.name AS subject_name, s.color AS subject_color FROM linked_folders lf "
            f"JOIN subjects s ON s.id = lf.subject_id {where} ORDER BY s.name, lf.path", params)

    def add_linked_folder(self, subject_id: int, path: str, label: str = "") -> None:
        self.execute("INSERT OR IGNORE INTO linked_folders(subject_id, path, label) VALUES (?,?,?)",
                     (subject_id, path, label))

    def remove_linked_folder(self, subject_id: int, path: str) -> None:
        self.execute("DELETE FROM linked_folders WHERE subject_id = ? AND path = ?", (subject_id, path))

    # ----------------------------------------------------------------- tags
    def list_tags(self) -> list[dict]:
        return self.query("SELECT t.*, COUNT(ft.file_id) AS files FROM tags t "
                          "LEFT JOIN file_tags ft ON ft.tag_id = t.id GROUP BY t.id ORDER BY t.name COLLATE NOCASE")

    def add_tag(self, name: str, color: str) -> int:
        existing = self.one("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,))
        return existing["id"] if existing else self.execute(
            "INSERT INTO tags(name, color) VALUES (?, ?)", (name, color))

    def update_tag(self, tag_id: int, **fields) -> None:
        self._update("tags", tag_id, fields)

    def delete_tag(self, tag_id: int) -> None:
        self.execute("DELETE FROM tags WHERE id = ?", (tag_id,))

    # --------------------------------------------------------- tracked files
    def track_file(self, path: str, subject_id: int | None = None) -> int:
        row = self.one("SELECT id FROM tracked_files WHERE path = ? COLLATE NOCASE", (path,))
        if row:
            if subject_id is not None:
                self._update("tracked_files", row["id"], {"subject_id": subject_id})
            return row["id"]
        return self.execute("INSERT INTO tracked_files(path, name, subject_id, added_at) VALUES (?,?,?,?)",
                            (path, Path(path).name, subject_id, now_str()))

    def get_tracked(self, file_id: int) -> dict | None:
        return self.one("SELECT * FROM tracked_files WHERE id = ?", (file_id,))

    def tracked_by_path(self, path: str) -> dict | None:
        return self.one("SELECT * FROM tracked_files WHERE path = ? COLLATE NOCASE", (path,))

    def update_tracked(self, file_id: int, **fields) -> None:
        self._update("tracked_files", file_id, fields)

    def untrack_file(self, file_id: int) -> None:
        self.execute("DELETE FROM tracked_files WHERE id = ?", (file_id,))

    def rename_tracked_path(self, old: str, new: str) -> None:
        """Keeps tags attached when a file is moved by the organizer."""
        self.execute("UPDATE tracked_files SET path = ?, name = ? WHERE path = ? COLLATE NOCASE",
                     (new, Path(new).name, old))

    def file_tag_ids(self, file_id: int) -> list[int]:
        return [r["tag_id"] for r in self.query("SELECT tag_id FROM file_tags WHERE file_id = ?", (file_id,))]

    def set_file_tags(self, file_id: int, tag_ids: list[int]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM file_tags WHERE file_id = ?", (file_id,))
            self.conn.executemany("INSERT INTO file_tags(file_id, tag_id) VALUES (?, ?)",
                                  [(file_id, t) for t in tag_ids])

    def list_tracked(self, tag_id: int | None = None, search: str = "", subject_id: int | None = None) -> list[dict]:
        where, params = [], []
        if tag_id is not None:
            where.append("f.id IN (SELECT file_id FROM file_tags WHERE tag_id = ?)")
            params.append(tag_id)
        if subject_id is not None:
            where.append("f.subject_id = ?")
            params.append(subject_id)
        if search:
            where.append("(f.name LIKE ? OR f.path LIKE ?)")
            params += [f"%{search}%"] * 2
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        return self.query(
            "SELECT f.*, s.name AS subject_name, s.color AS subject_color, "
            "(SELECT GROUP_CONCAT(tag_id) FROM file_tags WHERE file_id = f.id) AS tag_ids "
            f"FROM tracked_files f LEFT JOIN subjects s ON s.id = f.subject_id {clause} "
            "ORDER BY f.added_at DESC LIMIT 1000", params)

    def tags_by_path(self) -> dict[str, list[int]]:
        """lower-case path -> tag ids, used to show tag chips in the folder browser."""
        result: dict[str, list[int]] = {}
        for r in self.query("SELECT f.path, ft.tag_id FROM tracked_files f JOIN file_tags ft ON ft.file_id = f.id"):
            result.setdefault(r["path"].lower(), []).append(r["tag_id"])
        return result

    def search_files(self, text: str, limit: int = 6) -> list[dict]:
        like = f"%{text}%"
        return self.query("SELECT * FROM tracked_files WHERE name LIKE ? OR path LIKE ? "
                          "ORDER BY added_at DESC LIMIT ?", (like, like, limit))

    # ---------------------------------------------------- downloads organizer
    def organizer_rules(self) -> list[dict]:
        return self.query("SELECT * FROM organizer_rules")

    def learn_tokens(self, tokens: list[str], subject_id: int) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO organizer_rules(token, subject_id) VALUES (?, ?) "
                "ON CONFLICT(token, subject_id) DO UPDATE SET hits = hits + 1",
                [(t, subject_id) for t in tokens])

    def ignore_path(self, path: str) -> None:
        self.execute("INSERT OR REPLACE INTO organizer_ignored(path, ignored_at) VALUES (?, ?)", (path, now_str()))

    def ignored_paths(self) -> set[str]:
        return {r["path"].lower() for r in self.query("SELECT path FROM organizer_ignored")}

    def clear_ignored(self) -> None:
        self.execute("DELETE FROM organizer_ignored")

    def record_move(self, src: str, dst: str) -> int:
        return self.execute("INSERT INTO move_history(src, dst, moved_at) VALUES (?,?,?)", (src, dst, now_str()))

    def last_move(self) -> dict | None:
        return self.one("SELECT * FROM move_history ORDER BY id DESC LIMIT 1")

    def delete_move(self, move_id: int) -> None:
        self.execute("DELETE FROM move_history WHERE id = ?", (move_id,))

    # -------------------------------------------------------------- archive
    def add_archive_entry(self, name: str, original_path: str, archive_path: str, subject_id: int | None,
                          category: str, semester: str, mode: str, size: int, note: str = "") -> int:
        return self.execute(
            "INSERT INTO archive_entries(name, original_path, archive_path, subject_id, category, semester, mode, "
            "size, note, archived_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, original_path, archive_path, subject_id, category, semester, mode, size, note, now_str()))

    def list_archive(self, search: str = "", subject_id: int | None = None, category: str | None = None,
                     semester: str | None = None) -> list[dict]:
        where, params = [], []
        if search:
            where.append("(a.name LIKE ? OR a.original_path LIKE ? OR a.note LIKE ?)")
            params += [f"%{search}%"] * 3
        for column, value in (("a.subject_id", subject_id), ("a.category", category), ("a.semester", semester)):
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        return self.query(
            "SELECT a.*, s.name AS subject_name, s.color AS subject_color FROM archive_entries a "
            f"LEFT JOIN subjects s ON s.id = a.subject_id {clause} ORDER BY a.archived_at DESC", params)

    def archive_semesters(self) -> list[str]:
        return [r["semester"] for r in self.query(
            "SELECT DISTINCT semester FROM archive_entries WHERE semester != '' ORDER BY semester DESC")]

    def get_archive_entry(self, entry_id: int) -> dict | None:
        return self.one("SELECT * FROM archive_entries WHERE id = ?", (entry_id,))

    def delete_archive_entry(self, entry_id: int) -> None:
        self.execute("DELETE FROM archive_entries WHERE id = ?", (entry_id,))

    # --------------------------------------------------------------- alarms
    ALARM_FIELDS = ("label", "time", "days", "enabled", "sound", "volume", "gradual", "snooze_min",
                    "max_snoozes", "challenge", "color")

    def list_alarms(self) -> list[dict]:
        return self.query("SELECT * FROM alarms ORDER BY time, id")

    def get_alarm(self, alarm_id: int) -> dict | None:
        return self.one("SELECT * FROM alarms WHERE id = ?", (alarm_id,))

    def add_alarm(self, **fields) -> int:
        fields = {k: v for k, v in fields.items() if k in self.ALARM_FIELDS}
        fields["created_at"] = now_str()
        cols = ", ".join(fields)
        return self.execute(f"INSERT INTO alarms({cols}) VALUES ({', '.join('?' * len(fields))})",
                            list(fields.values()))

    def update_alarm(self, alarm_id: int, **fields) -> None:
        self._update("alarms", alarm_id, {k: v for k, v in fields.items()
                                          if k in self.ALARM_FIELDS or k == "last_fired"})

    def delete_alarm(self, alarm_id: int) -> None:
        self.execute("DELETE FROM alarms WHERE id = ?", (alarm_id,))

    def slots_on_day(self, weekday: int) -> list[dict]:
        return self.query(
            "SELECT sl.*, s.name AS subject_name, s.color AS subject_color FROM schedule_slots sl "
            "JOIN subjects s ON s.id = sl.subject_id WHERE sl.day_of_week = ? AND s.archived = 0 "
            "ORDER BY sl.start_time", (weekday,))

    # --------------------------------------------------------------- quotes
    def random_quote(self) -> dict | None:
        return self.one("SELECT * FROM quotes ORDER BY RANDOM() LIMIT 1")

    # ----------------------------------------------------------- phone link
    def list_phone_devices(self) -> list[dict]:
        return self.query("SELECT * FROM phone_devices ORDER BY COALESCE(last_seen, created_at) DESC")

    def add_phone_device(self, name: str, token_hash: str, ip: str) -> int:
        stamp = now_str()
        return self.execute("INSERT INTO phone_devices(name, token_hash, created_at, last_seen, last_ip) "
                            "VALUES (?,?,?,?,?)", (name, token_hash, stamp, stamp, ip))

    def device_by_hash(self, token_hash: str) -> dict | None:
        return self.one("SELECT * FROM phone_devices WHERE token_hash = ?", (token_hash,))

    def update_phone_device(self, device_id: int, **fields) -> None:
        self._update("phone_devices", device_id,
                     {k: v for k, v in fields.items() if k in ("name", "last_seen", "last_ip")})

    def delete_phone_device(self, device_id: int | None = None) -> None:
        """One device, or all of them when device_id is None."""
        if device_id is None:
            self.execute("DELETE FROM phone_devices")
        else:
            self.execute("DELETE FROM phone_devices WHERE id = ?", (device_id,))

    def add_transfer(self, direction: str, kind: str, title: str, content: str, size: int = 0,
                     device_id: int | None = None, subject_id: int | None = None) -> int:
        return self.execute(
            "INSERT INTO phone_transfers(direction, kind, title, content, size, device_id, subject_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)", (direction, kind, title, content, size, device_id, subject_id, now_str()))

    def list_transfers(self, direction: str, kind: str | None = None, search: str = "",
                       limit: int = 300) -> list[dict]:
        sql = ("SELECT t.*, d.name AS device_name, s.name AS subject_name, s.color AS subject_color "
               "FROM phone_transfers t LEFT JOIN phone_devices d ON d.id = t.device_id "
               "LEFT JOIN subjects s ON s.id = t.subject_id WHERE t.direction = ?")
        params: list = [direction]
        if kind:
            sql += " AND t.kind = ?"
            params.append(kind)
        if search:
            sql += " AND (t.title LIKE ? OR t.content LIKE ?)"
            params += [f"%{search}%"] * 2
        return self.query(sql + " ORDER BY t.id DESC LIMIT ?", [*params, limit])

    def get_transfer(self, transfer_id: int) -> dict | None:
        return self.one("SELECT * FROM phone_transfers WHERE id = ?", (transfer_id,))

    def set_transfer_fetched(self, transfer_id: int) -> None:
        self.execute("UPDATE phone_transfers SET fetched = fetched + 1 WHERE id = ?", (transfer_id,))

    def delete_transfers(self, ids: list[int] | None = None, direction: str | None = None) -> None:
        if ids is not None:
            self.conn.executemany("DELETE FROM phone_transfers WHERE id = ?", [(i,) for i in ids])
            self.conn.commit()
        elif direction:
            self.execute("DELETE FROM phone_transfers WHERE direction = ?", (direction,))

    def transfer_stats(self) -> dict:
        today = date.today().isoformat()
        row = self.one(
            "SELECT SUM(direction = 'in' AND created_at >= ?) AS today, SUM(direction = 'in') AS received, "
            "SUM(direction = 'out') AS sent, SUM(CASE WHEN kind = 'file' AND direction = 'in' THEN size ELSE 0 END) "
            "AS bytes FROM phone_transfers", (today,)) or {}
        return {k: row.get(k) or 0 for k in ("today", "received", "sent", "bytes")}

    # ------------------------------------------------------- clipboard history
    def add_clip(self, kind: str, content: str, preview: str, max_items: int = 200) -> int:
        """Adds an entry; the same content again just moves to the top. Returns the id."""
        old = self.one("SELECT id FROM clipboard_items WHERE kind = ? AND content = ?", (kind, content))
        if old:
            self.execute("UPDATE clipboard_items SET created_at = ? WHERE id = ?", (now_str(), old["id"]))
            return old["id"]
        clip_id = self.execute("INSERT INTO clipboard_items(kind, content, preview, created_at) VALUES (?,?,?,?)",
                               (kind, content, preview, now_str()))
        return clip_id

    def expired_clips(self, max_items: int) -> list[dict]:
        return self.query("SELECT * FROM clipboard_items WHERE pinned = 0 AND id NOT IN "
                          "(SELECT id FROM clipboard_items WHERE pinned = 0 ORDER BY created_at DESC, id DESC LIMIT ?)",
                          (max_items,))

    def list_clips(self, search: str = "", limit: int = 300) -> list[dict]:
        if search:
            return self.query("SELECT * FROM clipboard_items WHERE preview LIKE ? OR content LIKE ? "
                              "ORDER BY pinned DESC, created_at DESC, id DESC LIMIT ?",
                              (f"%{search}%", f"%{search}%", limit))
        return self.query("SELECT * FROM clipboard_items ORDER BY pinned DESC, created_at DESC, id DESC LIMIT ?",
                          (limit,))

    def get_clip(self, clip_id: int) -> dict | None:
        return self.one("SELECT * FROM clipboard_items WHERE id = ?", (clip_id,))

    def update_clip(self, clip_id: int, **fields) -> None:
        self._update("clipboard_items", clip_id, {k: v for k, v in fields.items() if k in ("pinned", "used", "created_at")})

    def delete_clips(self, ids: list[int] | None = None, keep_pinned: bool = True) -> list[dict]:
        """Deletes the given ids (or everything not pinned). Returns the deleted rows (for image clean-up)."""
        if ids is None:
            rows = self.query("SELECT * FROM clipboard_items" + (" WHERE pinned = 0" if keep_pinned else ""))
        else:
            rows = [r for r in (self.get_clip(i) for i in ids) if r]
        with self.conn:
            self.conn.executemany("DELETE FROM clipboard_items WHERE id = ?", [(r["id"],) for r in rows])
        return rows

    # ----------------------------------------------------------- PDF reader
    def pdf_highlights(self, file_key: str, path: str = "") -> list[dict]:
        rows = self.query("SELECT * FROM pdf_highlights WHERE file_key = ? ORDER BY page, id", (file_key,))
        if path and rows and any(r["path"] != path for r in rows):  # the file was moved / renamed
            self.execute("UPDATE pdf_highlights SET path = ? WHERE file_key = ?", (path, file_key))
        return rows

    def add_pdf_highlight(self, file_key: str, path: str, page: int, rects: str, text: str, color: str,
                          note: str = "") -> int:
        return self.execute("INSERT INTO pdf_highlights(file_key, path, page, rects, text, color, note, created_at) "
                            "VALUES (?,?,?,?,?,?,?,?)", (file_key, path, page, rects, text, color, note, now_str()))

    def update_pdf_highlight(self, hl_id: int, **fields) -> None:
        self._update("pdf_highlights", hl_id, {k: v for k, v in fields.items() if k in ("color", "note")})

    def delete_pdf_highlight(self, hl_id: int) -> None:
        self.execute("DELETE FROM pdf_highlights WHERE id = ?", (hl_id,))
