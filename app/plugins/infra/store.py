"""Infrastructure plugin: its own tables (created through the plugin API's versioned migrations)."""
from __future__ import annotations

from datetime import datetime

MIGRATIONS = [
    [  # v1
        """CREATE TABLE IF NOT EXISTS infra_hosts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'linux',     -- linux | proxmox | docker | vmware | windows | other
            grp         TEXT NOT NULL DEFAULT '',
            address     TEXT NOT NULL,
            ssh_port    INTEGER NOT NULL DEFAULT 22,
            username    TEXT NOT NULL DEFAULT '',
            auth        TEXT NOT NULL DEFAULT 'password',  -- password (in the vault) | key | none
            vault_id    INTEGER,
            key_path    TEXT NOT NULL DEFAULT '',
            host_key    TEXT NOT NULL DEFAULT '',          -- trusted SSH host key "type base64"
            web_url     TEXT NOT NULL DEFAULT '',
            rdp         INTEGER NOT NULL DEFAULT 0,
            notes       TEXT NOT NULL DEFAULT '',
            color       TEXT NOT NULL DEFAULT '#22D3EE',
            last_status TEXT NOT NULL DEFAULT '',          -- JSON from the last status refresh
            status_at   TEXT,
            created_at  TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS infra_checks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            host_id      INTEGER REFERENCES infra_hosts(id) ON DELETE CASCADE,
            name         TEXT NOT NULL,
            kind         TEXT NOT NULL,                    -- ping | tcp | http | tls
            target       TEXT NOT NULL,
            port         INTEGER NOT NULL DEFAULT 0,
            interval_sec INTEGER NOT NULL DEFAULT 60,
            insecure     INTEGER NOT NULL DEFAULT 0,       -- accept self-signed certificates (Proxmox…)
            warn_days    INTEGER NOT NULL DEFAULT 14,
            enabled      INTEGER NOT NULL DEFAULT 1,
            state        TEXT NOT NULL DEFAULT '',         -- '' | up | warn | down
            last_ms      REAL,
            last_detail  TEXT NOT NULL DEFAULT '',
            last_change  TEXT,
            created_at   TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS infra_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            host_id    INTEGER REFERENCES infra_hosts(id) ON DELETE SET NULL,
            action     TEXT NOT NULL,
            detail     TEXT NOT NULL DEFAULT '',
            ok         INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL)""",
    ],
    [  # v2: Proxmox API connections, the command library
        """CREATE TABLE IF NOT EXISTS infra_pve (
            host_id     INTEGER PRIMARY KEY REFERENCES infra_hosts(id) ON DELETE CASCADE,
            port        INTEGER NOT NULL DEFAULT 8006,
            auth        TEXT NOT NULL DEFAULT 'token',     -- token (API token, secret in the vault) | password
            token_id    TEXT NOT NULL DEFAULT '',          -- user@realm!name
            vault_id    INTEGER,                           -- the token secret
            realm       TEXT NOT NULL DEFAULT 'pam',
            fingerprint TEXT NOT NULL DEFAULT '')""",      # trusted certificate (SHA-256), self-signed is fine
        """CREATE TABLE IF NOT EXISTS infra_commands (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL DEFAULT '',
            command    TEXT NOT NULL,
            sudo       INTEGER NOT NULL DEFAULT 0,
            confirm    INTEGER NOT NULL DEFAULT 0,          -- ask before running (reboot, prune…)
            builtin    TEXT NOT NULL DEFAULT '',            -- key of a built-in command (name translated)
            position   INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL)""",
        """INSERT INTO infra_commands (name, command, sudo, confirm, builtin, position, created_at) VALUES
            ('', 'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade', 1, 1, 'update', 0, '2026-11-08 00:00:00'),
            ('', 'df -h -x tmpfs -x devtmpfs -x squashfs', 0, 0, 'disk', 1, '2026-11-08 00:00:00'),
            ('', 'free -h', 0, 0, 'memory', 2, '2026-11-08 00:00:00'),
            ('', 'ps aux --sort=-%cpu | head -n 12', 0, 0, 'top', 3, '2026-11-08 00:00:00'),
            ('', 'systemctl --failed --no-pager', 0, 0, 'failed', 4, '2026-11-08 00:00:00'),
            ('', 'journalctl -p 3 --since ''1 hour ago'' --no-pager | tail -n 60', 1, 0, 'errors', 5, '2026-11-08 00:00:00'),
            ('', 'ss -tulpn', 1, 0, 'ports', 6, '2026-11-08 00:00:00'),
            ('', 'docker ps --format ''table {{.Names}}\\t{{.Status}}\\t{{.Ports}}''', 1, 0, 'docker_ps', 7, '2026-11-08 00:00:00'),
            ('', 'docker system prune -f', 1, 1, 'docker_prune', 8, '2026-11-08 00:00:00'),
            ('', 'nginx -t && systemctl reload nginx', 1, 0, 'nginx_reload', 9, '2026-11-08 00:00:00'),
            ('', 'systemctl reboot', 1, 1, 'reboot', 10, '2026-11-08 00:00:00')""",
    ],
]

HOST_FIELDS = ("name", "kind", "grp", "address", "ssh_port", "username", "auth", "vault_id", "key_path", "host_key",
               "web_url", "rdp", "notes", "color", "last_status", "status_at")
CHECK_FIELDS = ("host_id", "name", "kind", "target", "port", "interval_sec", "insecure", "warn_days", "enabled",
                "state", "last_ms", "last_detail", "last_change")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class InfraStore:
    def __init__(self, db):
        self.db = db

    def _insert(self, table: str, allowed, fields: dict) -> int:
        fields = {k: v for k, v in fields.items() if k in allowed}
        fields["created_at"] = now()
        return self.db.execute(f"INSERT INTO {table}({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                               list(fields.values()))

    # hosts
    def hosts(self) -> list[dict]:
        return self.db.query("SELECT * FROM infra_hosts ORDER BY grp COLLATE NOCASE, name COLLATE NOCASE")

    def host(self, host_id: int) -> dict | None:
        return self.db.one("SELECT * FROM infra_hosts WHERE id = ?", (host_id,))

    def add_host(self, **fields) -> int:
        return self._insert("infra_hosts", HOST_FIELDS, fields)

    def update_host(self, host_id: int, **fields) -> None:
        self.db._update("infra_hosts", host_id, {k: v for k, v in fields.items() if k in HOST_FIELDS})

    def delete_host(self, host_id: int) -> None:
        self.db.execute("DELETE FROM infra_checks WHERE host_id = ?", (host_id,))
        self.db.execute("DELETE FROM infra_hosts WHERE id = ?", (host_id,))

    def groups(self) -> list[str]:
        return [r["grp"] for r in self.db.query("SELECT DISTINCT grp FROM infra_hosts WHERE grp != '' ORDER BY grp")]

    # checks
    def checks(self) -> list[dict]:
        return self.db.query("SELECT c.*, h.name AS host_name, h.color AS host_color FROM infra_checks c "
                             "LEFT JOIN infra_hosts h ON h.id = c.host_id ORDER BY c.host_id IS NULL, h.name, c.id")

    def check(self, check_id: int) -> dict | None:
        return self.db.one("SELECT * FROM infra_checks WHERE id = ?", (check_id,))

    def add_check(self, **fields) -> int:
        return self._insert("infra_checks", CHECK_FIELDS, fields)

    def update_check(self, check_id: int, **fields) -> None:
        self.db._update("infra_checks", check_id, {k: v for k, v in fields.items() if k in CHECK_FIELDS})

    def delete_check(self, check_id: int) -> None:
        self.db.execute("DELETE FROM infra_checks WHERE id = ?", (check_id,))

    # Proxmox connection
    def pve(self, host_id: int) -> dict:
        row = self.db.one("SELECT * FROM infra_pve WHERE host_id = ?", (host_id,))
        return row or {"host_id": host_id, "port": 8006, "auth": "token", "token_id": "", "vault_id": None,
                       "realm": "pam", "fingerprint": ""}

    def save_pve(self, host_id: int, **fields) -> None:
        row = {**self.pve(host_id), **fields, "host_id": host_id}
        cols = ("host_id", "port", "auth", "token_id", "vault_id", "realm", "fingerprint")
        self.db.execute(f"INSERT OR REPLACE INTO infra_pve ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row[c] for c in cols])

    # command library
    def commands(self) -> list[dict]:
        return self.db.query("SELECT * FROM infra_commands ORDER BY position, id")

    def command(self, cmd_id: int) -> dict | None:
        return self.db.one("SELECT * FROM infra_commands WHERE id = ?", (cmd_id,))

    def save_command(self, cmd_id: int | None, **fields) -> int:
        fields = {k: v for k, v in fields.items() if k in ("name", "command", "sudo", "confirm")}
        if cmd_id:
            self.db._update("infra_commands", cmd_id, fields)
            return cmd_id
        position = self.db.scalar("SELECT COALESCE(MAX(position), 0) + 1 FROM infra_commands", default=0)
        return self._insert("infra_commands", ("name", "command", "sudo", "confirm", "position"),
                            {**fields, "position": position})

    def delete_command(self, cmd_id: int) -> None:
        self.db.execute("DELETE FROM infra_commands WHERE id = ?", (cmd_id,))

    # log
    def log(self, action: str, detail: str = "", host_id: int | None = None, ok: bool = True) -> None:
        self.db.execute("INSERT INTO infra_log(host_id, action, detail, ok, created_at) VALUES (?,?,?,?,?)",
                        (host_id, action, detail[:2000], int(ok), now()))
        self.db.execute("DELETE FROM infra_log WHERE id NOT IN (SELECT id FROM infra_log ORDER BY id DESC LIMIT 2000)")

    def log_entries(self, limit: int = 400) -> list[dict]:
        return self.db.query("SELECT l.*, h.name AS host_name FROM infra_log l "
                             "LEFT JOIN infra_hosts h ON h.id = l.host_id ORDER BY l.id DESC LIMIT ?", (limit,))
