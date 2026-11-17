"""
Password vault — encryption core (no UI).

Security design
---------------
* Every entry's secret part (username, password, notes) is encrypted with Windows DPAPI
  (CryptProtectData, AES-256 under the hood), bound to *this Windows user account*.
* On top of that, DPAPI's "entropy" is a 256-bit key derived from your MASTER PASSWORD with
  PBKDF2-HMAC-SHA256 (600 000 iterations, random 16-byte salt). Without the master password the
  data can't be decrypted — not even by another program running as you.
* The master password itself is never stored: only the salt and an encrypted check value.
* The key lives in memory only while the vault is unlocked; lock() forgets it.
* Nothing here needs extra packages (ctypes + hashlib from the standard library).

Limits (said honestly in the UI): a forgotten master password cannot be recovered, and because
DPAPI is tied to the Windows account, the vault can't be opened after reinstalling Windows —
use "Export" (CSV) before that.
"""
from __future__ import annotations

import base64
import csv
import ctypes
import hashlib
import io
import json
import math
import os
import re
import secrets
import string
from ctypes import wintypes
from datetime import datetime
from urllib.parse import urlparse

ITERATIONS = 600_000
CHECK_PLAINTEXT = b"studyhub-vault-v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class VaultError(Exception):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _from_blob(blob: _Blob) -> bytes:
    data = ctypes.string_at(blob.pbData, blob.cbData)
    ctypes.windll.kernel32.LocalFree(blob.pbData)
    return data


def protect(data: bytes, entropy: bytes) -> bytes:
    data_in, keep1 = _to_blob(data)
    ent, keep2 = _to_blob(entropy)
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(data_in), "StudyHub Vault", ctypes.byref(ent),
                                                  None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise VaultError("encryption failed")
    return _from_blob(out)


def unprotect(data: bytes, entropy: bytes) -> bytes:
    data_in, keep1 = _to_blob(data)
    ent, keep2 = _to_blob(entropy)
    out = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(data_in), None, ctypes.byref(ent),
                                                    None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise VaultError("wrong key or damaged data")
    return _from_blob(out)


def derive_key(master: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", master.encode("utf-8"), salt, iterations, dklen=32)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Vault:
    def __init__(self, db):
        self.db = db
        self._key: bytes | None = None

    # ----------------------------------------------------------- master key
    def is_set_up(self) -> bool:
        return bool(self.db.get_setting("vault_salt"))

    @property
    def unlocked(self) -> bool:
        return self._key is not None

    def setup(self, master: str) -> None:
        if self.is_set_up():
            raise VaultError("already set up")
        salt = os.urandom(16)
        key = derive_key(master, salt)
        check = protect(CHECK_PLAINTEXT, key)
        self.db.set_setting("vault_salt", salt.hex())
        self.db.set_setting("vault_iterations", ITERATIONS)
        self.db.set_setting("vault_check", base64.b64encode(check).decode())
        self._key = key

    def unlock(self, master: str) -> bool:
        salt = bytes.fromhex(self.db.get_setting("vault_salt"))
        key = derive_key(master, salt, int(self.db.get_setting("vault_iterations", ITERATIONS)))
        try:
            ok = unprotect(base64.b64decode(self.db.get_setting("vault_check")), key) == CHECK_PLAINTEXT
        except VaultError:
            ok = False
        self._key = key if ok else None
        return ok

    def lock(self) -> None:
        self._key = None

    def change_master(self, old: str, new: str) -> bool:
        if not self.unlock(old):
            return False
        entries = [(e["id"], self.decrypt(e["secret"])) for e in self.db.query("SELECT id, secret FROM vault_entries")]
        salt = os.urandom(16)
        new_key = derive_key(new, salt)
        with self.db.conn:  # all or nothing
            for entry_id, secret in entries:
                self.db.conn.execute("UPDATE vault_entries SET secret = ? WHERE id = ?",
                                     (protect(json.dumps(secret).encode(), new_key), entry_id))
            self.db.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('vault_salt', ?)",
                                 (json.dumps(salt.hex()),))
            self.db.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('vault_check', ?)",
                                 (json.dumps(base64.b64encode(protect(CHECK_PLAINTEXT, new_key)).decode()),))
        self._key = new_key
        return True

    # ---------------------------------------------------------- encryption
    def _need_key(self) -> bytes:
        if self._key is None:
            raise VaultError("vault is locked")
        return self._key

    def encrypt(self, secret: dict) -> bytes:
        return protect(json.dumps(secret, ensure_ascii=False).encode("utf-8"), self._need_key())

    def decrypt(self, blob: bytes) -> dict:
        return json.loads(unprotect(bytes(blob), self._need_key()).decode("utf-8"))

    # -------------------------------------------------------------- entries
    def entries(self) -> list[dict]:
        """All entries with their secrets decrypted (only while unlocked)."""
        self._need_key()
        out = []
        for row in self.db.query("SELECT * FROM vault_entries ORDER BY favorite DESC, title COLLATE NOCASE"):
            secret = self.decrypt(row["secret"])
            row.pop("secret")
            out.append({**row, **secret})
        return out

    def add(self, title: str, url: str, username: str, password: str, notes: str = "",
            category: str = "other", favorite: bool = False) -> int:
        blob = self.encrypt({"username": username, "password": password, "notes": notes})
        now = _now()
        return self.db.execute(
            "INSERT INTO vault_entries(title, url, category, favorite, secret, pw_changed_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)", (title, url, category, int(favorite), blob, now, now, now))

    def update(self, entry_id: int, title: str, url: str, username: str, password: str, notes: str,
               category: str, favorite: bool) -> None:
        old = self.get(entry_id)
        blob = self.encrypt({"username": username, "password": password, "notes": notes})
        changed = old is None or old["password"] != password
        self.db.execute(
            "UPDATE vault_entries SET title = ?, url = ?, category = ?, favorite = ?, secret = ?, updated_at = ?"
            + (", pw_changed_at = ?" if changed else "") + " WHERE id = ?",
            [title, url, category, int(favorite), blob, _now()] + ([_now()] if changed else []) + [entry_id])

    def get(self, entry_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM vault_entries WHERE id = ?", (entry_id,))
        if not row:
            return None
        secret = self.decrypt(row.pop("secret"))
        return {**row, **secret}

    def delete(self, entry_id: int) -> None:
        self.db.execute("DELETE FROM vault_entries WHERE id = ?", (entry_id,))

    def set_favorite(self, entry_id: int, on: bool) -> None:
        self.db.execute("UPDATE vault_entries SET favorite = ? WHERE id = ?", (int(on), entry_id))

    def touch(self, entry_id: int) -> None:
        self.db.execute("UPDATE vault_entries SET last_used = ? WHERE id = ?", (_now(), entry_id))

    # ------------------------------------------------------- import / export
    def import_csv(self, path: str) -> tuple[int, int]:
        """Chrome / Edge / Brave (name,url,username,password,note) and Firefox (url,username,password)
        password exports. Returns (imported, skipped duplicates)."""
        existing = {(e["url"].lower(), e["username"].lower(), e["password"]) for e in self.entries()}
        added = skipped = 0
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
                password = row.get("password", "")
                url = row.get("url") or row.get("origin") or row.get("hostname") or ""
                username = row.get("username") or row.get("login") or ""
                if not password:
                    continue
                key = (url.lower(), username.lower(), password)
                if key in existing:
                    skipped += 1
                    continue
                title = row.get("name") or site_name(url) or username or "Login"
                self.add(title, url, username, password, row.get("note") or row.get("notes") or "",
                         guess_category(url))
                existing.add(key)
                added += 1
        return added, skipped

    def export_csv(self) -> str:
        """Plain-text CSV in Chrome's format (for moving to another password manager / PC)."""
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["name", "url", "username", "password", "note"])
        for e in self.entries():
            writer.writerow([e["title"], e["url"], e["username"], e["password"], e["notes"]])
        return out.getvalue()


def secure_delete(path: str) -> None:
    """Overwrites a plain-text export before removing it (it held every password in the clear)."""
    size = os.path.getsize(path)
    with open(path, "r+b") as fh:
        fh.write(os.urandom(size))
        fh.flush()
        os.fsync(fh.fileno())
    os.remove(path)


# ------------------------------------------------------------------ helpers
def site_name(url: str) -> str:
    host = urlparse(url if "://" in url else "https://" + url).netloc.split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    return (parts[-2] if len(parts) >= 2 else host).capitalize() if host else ""


_CATEGORY_HINTS = {
    "social": ("facebook", "instagram", "twitter", "x.com", "tiktok", "snapchat", "linkedin", "reddit", "telegram",
               "whatsapp", "discord"),
    "email": ("mail", "outlook", "proton", "yahoo", "gmail"),
    "study": (".edu", "moodle", "learn", "classroom", "coursera", "udemy", "edx", "khan", "university", "aiu"),
    "finance": ("bank", "paypal", "pay", "wallet", "binance", "visa"),
    "shopping": ("amazon", "ebay", "aliexpress", "shop", "store", "noon"),
    "work": ("github", "gitlab", "slack", "notion", "microsoft", "office", "atlassian", "zoom"),
}
CATEGORIES = ["social", "email", "study", "finance", "shopping", "work", "other"]


def guess_category(url: str) -> str:
    u = url.lower()
    for cat, hints in _CATEGORY_HINTS.items():
        if any(h in u for h in hints):
            return cat
    return "other"


AMBIGUOUS = set("Il1O0o")
SYMBOLS = "!@#$%^&*()-_=+[]{};:,.?/"


def generate(length: int = 18, upper: bool = True, lower: bool = True, digits: bool = True,
             symbols: bool = True, avoid_ambiguous: bool = True) -> str:
    """Cryptographically random password containing at least one of each chosen class."""
    pools = [p for p, on in ((string.ascii_uppercase, upper), (string.ascii_lowercase, lower),
                             (string.digits, digits), (SYMBOLS, symbols)) if on] or [string.ascii_lowercase]
    if avoid_ambiguous:
        pools = [("".join(c for c in p if c not in AMBIGUOUS) or p) for p in pools]
    length = max(length, len(pools))
    chars = [secrets.choice(p) for p in pools]
    alphabet = "".join(pools)
    chars += [secrets.choice(alphabet) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


_COMMON = {"password", "123456", "12345678", "qwerty", "abc123", "111111", "iloveyou", "admin", "welcome",
           "letmein", "monkey", "dragon", "football", "123123", "000000", "password1", "qwerty123", "1q2w3e4r"}


def strength(password: str) -> tuple[int, float]:
    """(score 0..4, estimated entropy bits). Penalises common passwords, repeats and sequences."""
    if not password:
        return 0, 0.0
    pool = 0
    pool += 26 if re.search(r"[a-z]", password) else 0
    pool += 26 if re.search(r"[A-Z]", password) else 0
    pool += 10 if re.search(r"\d", password) else 0
    pool += 33 if re.search(r"[^A-Za-z0-9]", password) else 0
    pool += 100 if re.search(r"[^\x00-\x7f]", password) else 0
    bits = len(password) * math.log2(max(pool, 2))
    lower = password.lower()
    if lower in _COMMON or lower.rstrip("0123456789!") in _COMMON:
        bits = min(bits, 10)
    if re.fullmatch(r"[A-Za-z]+\d{1,4}[!.]?", password) and len(password) < 13:
        bits = min(bits, 34)  # "word + a few digits" is the first thing cracking tools try
    if password.isdigit() and len(password) < 12:
        bits = min(bits, 24)  # dates / phone numbers
    if re.search(r"(.)\1{2,}", password):
        bits *= 0.8
    if any(seq in lower for seq in ("1234", "abcd", "qwer", "asdf", "4321")):
        bits *= 0.8
    score = 0 if bits < 28 else 1 if bits < 40 else 2 if bits < 60 else 3 if bits < 80 else 4
    return score, bits


def health(entries: list[dict], old_days: int = 365) -> dict:
    """Weak, reused and old passwords (ids)."""
    weak = [e["id"] for e in entries if strength(e["password"])[0] < 2]
    by_pw: dict[str, list[int]] = {}
    for e in entries:
        by_pw.setdefault(e["password"], []).append(e["id"])
    reused = [i for ids in by_pw.values() if len(ids) > 1 for i in ids]
    cutoff = datetime.now().timestamp() - old_days * 86400
    old = [e["id"] for e in entries
           if datetime.strptime(e["pw_changed_at"], "%Y-%m-%d %H:%M:%S").timestamp() < cutoff]
    return {"weak": weak, "reused": reused, "old": old}
