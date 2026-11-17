"""
Phone link: a small web server on the local network.

The phone pairs by scanning a QR code, then sends notes, links, tasks, clipboard text, photos and files
(optionally into a subject) and picks up what the laptop queued for it. application.py (_on_phone_item)
decides where each thing goes.

Security:
- off by default; only answers private / link-local addresses
- pairing needs the code shown on the laptop; each phone gets its own token (only the SHA-256 is stored)
  and can be revoked separately
- 8 wrong codes or tokens from one address blocks it for 5 minutes
- the API needs a custom header and sends no CORS headers, so other websites can't post to it
- size limits, sanitised file names, never overwrites
- files, screen, remote, clipboard history and sound are switched on per feature (Phone > Access) and only
  work over HTTPS with the local certificate; shared folders can't be escaped (.. and links are refused)
- handler threads never touch the database, they ask the GUI thread
- the Android app finds the laptop by UDP broadcast (private addresses only) and pins Study Hub's CA;
  /app.apk serves the app itself
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

DEFAULT_PORT = 8765
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I: easy to type by hand
MAX_JSON = 256 * 1024
MAX_TEXT = 100_000
MAX_FAILS, BLOCK_SECONDS = 8, 300
DISCOVERY_PORT = 48765          # the Android app asks "where is Study Hub?" here (UDP broadcast)
DISCOVERY_ASK = b"STUDYHUB-DISCOVER 1"
SECURE_ONLY = {"files", "screen", "remote", "clipboard", "sound"}
FILE_ATTRIBUTE_HIDDEN, FILE_ATTRIBUTE_SYSTEM = 0x2, 0x4


def new_code() -> str:
    """10 characters (~50 bits): plenty with the lock-out, still typeable."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))


def pretty_code(code: str) -> str:
    return f"{code[:5]}-{code[5:]}"


def normalize_code(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


_VIRTUAL = ("vmware", "virtualbox", "vethernet", "hyper-v", "vpn", "tap-", "tun", "wireguard", "proton",
            "openvpn", "zerotier", "tailscale", "hamachi", "docker", "wsl", "loopback")


def local_addresses() -> list[str]:
    """Private IPv4 addresses a phone can reach: Wi-Fi first, then real Ethernet, then the rest
    (VPN / VMware / Hyper-V adapters last — a phone on the Wi-Fi can't reach those)."""
    try:
        from PyQt6.QtNetwork import QAbstractSocket, QNetworkInterface
    except ImportError:
        return _socket_addresses()
    ranked: list[tuple[int, str]] = []
    up = QNetworkInterface.InterfaceFlag.IsUp | QNetworkInterface.InterfaceFlag.IsRunning
    for iface in QNetworkInterface.allInterfaces():
        if (iface.flags() & up) != up:
            continue
        name = iface.humanReadableName().lower()
        kind = iface.type()
        if any(word in name for word in _VIRTUAL):
            rank = 3
        elif kind == QNetworkInterface.InterfaceType.Wifi:
            rank = 0
        elif kind == QNetworkInterface.InterfaceType.Ethernet:
            rank = 1
        else:
            rank = 2
        for entry in iface.addressEntries():
            ip = entry.ip()
            if ip.protocol() != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                continue
            addr = ipaddress.ip_address(ip.toString())
            if addr.is_private and not addr.is_loopback and not addr.is_link_local:
                ranked.append((rank, str(addr)))
    return [ip for _, ip in sorted(ranked, key=lambda r: r[0])] or _socket_addresses()


def _socket_addresses() -> list[str]:
    found: list[str] = []
    try:  # no packet is sent: this only asks Windows which interface it would use
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    result = []
    for ip in dict.fromkeys(found):
        addr = ipaddress.ip_address(ip)
        if addr.is_private and not addr.is_loopback:
            result.append(ip)
    return result


def safe_filename(name: str) -> str:
    name = Path(unquote(name or "").replace("\\", "/")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    stem = Path(name).stem.upper()
    if not name or stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                            *(f"LPT{i}" for i in range(10))}:
        name = f"phone-{time.strftime('%Y%m%d-%H%M%S')}" + Path(name).suffix
    return name[:150]


def unique_path(folder: Path, name: str) -> Path:
    path = folder / name
    n = 2
    while path.exists():
        path = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return path


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class _Handler(BaseHTTPRequestHandler):
    server_version = "StudyHub"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    server: "_Server"

    def log_message(self, fmt, *args):  # keep the console / log quiet
        pass

    # ------------------------------------------------------------ helpers
    def _send(self, status: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _fail(self, status: int, error: str, **extra) -> None:
        self.close_connection = True  # a refused body may still be unread
        self._json(status, {"error": error, **extra})

    @property
    def ip(self) -> str:
        return self.client_address[0]

    def _client_allowed(self) -> bool:
        try:
            addr = ipaddress.ip_address(self.ip)
        except ValueError:
            return False
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        return addr.is_private or addr.is_loopback or addr.is_link_local

    def _host_ok(self) -> bool:
        """Host must be an IP literal: blocks DNS-rebinding pages from reaching us by name."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host == "localhost":
            return True
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return False

    def _blocked(self) -> bool:
        with self.server.lock:
            return self.server.blocked.get(self.ip, 0) > time.monotonic()

    def _failed_attempt(self) -> None:
        srv = self.server
        with srv.lock:
            srv.fails[self.ip] = srv.fails.get(self.ip, 0) + 1
            if srv.fails[self.ip] >= MAX_FAILS:
                srv.blocked[self.ip] = time.monotonic() + BLOCK_SECONDS
                srv.fails[self.ip] = 0
                log.warning("phone link: %s blocked for %ss after wrong codes", self.ip, BLOCK_SECONDS)

    def _device(self) -> str | None:
        """The paired device making this call (its token hash), or None."""
        if self._blocked():
            return None
        token = self.headers.get("X-Device", "")
        digest = token_hash(token) if token else ""
        owner = self.server.owner
        with owner.lock:
            ok = bool(digest) and any(hmac.compare_digest(digest, known) for known in owner.devices)
        if ok:
            with self.server.lock:
                self.server.fails.pop(self.ip, None)
            owner.mark_seen(digest, self.ip)
            return digest
        self._failed_attempt()
        return None

    def _length(self) -> int:
        try:
            return int(self.headers.get("Content-Length", ""))
        except ValueError:
            return -1

    def _read_json(self) -> dict | None:
        length = self._length()
        if length < 0 or length > MAX_JSON or "json" not in (self.headers.get("Content-Type") or ""):
            self._fail(413 if length > MAX_JSON else 415, "bad body")
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "bad json"})
            return None
        if not isinstance(data, dict):
            self._json(400, {"error": "bad json"})
            return None
        return data

    def _gate(self) -> bool:
        if not self._client_allowed() or not self._host_ok():
            self._fail(403, "forbidden")
            return False
        return True

    # ------------------------------------------------------------- routes
    def do_GET(self):
        if not self._gate():
            return
        path = self.path.split("?", 1)[0]
        owner = self.server.owner
        if path in ("/manifest.webmanifest", "/sw.js", "/icon-192.png", "/icon-512.png", "/ca.crt", "/app.apk"):
            self._static(path)
            return
        if path in ("/", "/index.html"):
            from app.core.lan_page import render
            csp = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                   "img-src 'self' data: blob:; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
                   "form-action 'none'; base-uri 'none'; frame-ancestors 'none'")
            self._send(200, render(owner.info()).encode("utf-8"), "text/html; charset=utf-8",
                       {"Content-Security-Policy": csp, "X-Frame-Options": "DENY"})
            return
        if path.startswith("/s/"):  # quick share (QR code): anyone on this Wi-Fi with the link, for a while
            self._quick_share(path)
            return
        if not path.startswith("/api/"):
            self._json(404, {"error": "not found"})
            return
        device = self._device()
        if not device:
            self._fail(401, "device")
            return
        if path == "/api/ping":
            since = self._query_int("since", 0)
            caps = owner.caps_for(self.server.secure)
            with owner.lock:
                handoff = dict(owner.handoff) if caps.get("study") else {}
                lab = dict(owner.lab)
            self._json(200, {"ok": True, **owner.info(), "pending": owner.pending_for(device),
                             "caps": caps, "events": owner.events_since(since), "handoff": handoff, "lab": lab})
        elif path == "/api/subjects":
            with owner.lock:
                self._json(200, [{k: s[k] for k in ("id", "name", "color", "has_folder")} for s in owner.subjects])
        elif path == "/api/outbox":
            self._json(200, owner.outbox_list(device))
        elif path.startswith("/api/outbox/"):
            self._send_outbox_file(path.rsplit("/", 1)[1], device)
        else:
            self._app_get(path, device)

    do_HEAD = do_GET

    # ------------------------------------------------------- phone app
    def _query(self) -> dict[str, str]:
        raw = self.path.split("?", 1)[1] if "?" in self.path else ""
        return {k: v[0] for k, v in parse_qs(raw).items()}

    def _query_int(self, key: str, default: int) -> int:
        try:
            return int(self._query().get(key, default))
        except ValueError:
            return default

    def _allowed(self, cap: str) -> bool:
        """The feature is switched on (and, for files / screen / remote, this is the HTTPS server)."""
        caps = self.server.owner.caps_for(self.server.secure)
        if caps.get(cap):
            return True
        if cap in SECURE_ONLY and self.server.owner.caps.get(cap) and not self.server.secure:
            self._fail(403, "https")
        else:
            self._fail(403, "off", cap=cap)
        return False

    def _ask(self, kind: str, payload=None, device: str = "", timeout: float = 10.0):
        """Asks the GUI thread; answers 503 when it doesn't reply in time. Returns the result or None."""
        result = self.server.owner.ask(kind, payload, device, timeout)
        if result is None:
            self._fail(503, "busy")
        return result

    def _reply(self, result) -> None:
        if result is None:
            return
        if isinstance(result, dict) and result.get("error"):
            self._json(int(result.get("status", 400)), {"error": result["error"]})
        else:
            self._json(200, result)

    def _id_from(self, path: str) -> int | None:
        try:
            return int(path.rstrip("/").rsplit("/", 1)[1])
        except ValueError:
            self._json(404, {"error": "not found"})
            return None

    def _app_get(self, path: str, device: str) -> None:
        q = self._query()
        if path == "/api/desktop":
            if self._allowed("desktop"):
                self._reply(self._ask("desktop", None, device))
        elif path.startswith("/api/icon/"):
            item_id = self._id_from(path)
            if item_id is not None and self._allowed("desktop"):
                data = self._ask("icon", item_id, device)
                if data is not None:
                    self._send(200 if data else 404, data or b"", "image/png")
        elif path.startswith("/api/desktop/file/"):
            item_id = self._id_from(path)
            if item_id is not None and self._allowed("desktop") and self._allowed("files"):
                found = self._ask("item_file", item_id, device)
                if found is not None:
                    if found:
                        self._stream_file(Path(found), q.get("inline") == "1")
                    else:
                        self._json(404, {"error": "gone"})
        elif path == "/api/fs":
            if self._allowed("files"):
                self._list_folder(q)
        elif path == "/api/fs/get":
            if self._allowed("files"):
                target = self._shared_path(q.get("root"), q.get("path", ""))
                if target is None or not target.is_file():
                    self._json(404, {"error": "not found"})
                else:
                    self._stream_file(target, q.get("inline") == "1")
        elif path == "/api/screen":
            if self._allowed("screen"):
                shot = self._ask("screen", {"monitor": self._query_int("m", 0),
                                            "width": max(320, min(2560, self._query_int("w", 1280)))}, device)
                if shot is not None:
                    self._send(200, shot["jpeg"], "image/jpeg", {"X-Monitors": str(shot["monitors"])})
        elif path == "/api/notes":
            if self._allowed("study"):
                self._reply(self._ask("notes", q.get("q", "")[:200], device))
        elif path.startswith("/api/notes/"):
            note_id = self._id_from(path)
            if note_id is not None and self._allowed("study"):
                self._reply(self._ask("note", note_id, device))
        elif path == "/api/tasks":
            if self._allowed("study"):
                self._reply(self._ask("tasks", None, device))
        elif path == "/api/timer":  # read-only: the timer is controlled on the laptop
            if self._allowed("study"):
                self._reply(self._ask("timer", "", device))
        elif path == "/api/handoff/page":
            if not self._allowed("study"):
                return
            if not self.server.secure:  # the pages of your PDF: only over the secure connection
                self._fail(403, "https")
                return
            page = self._ask("handoff_page", {"n": self._query_int("n", 0),
                                              "width": max(400, min(2200, self._query_int("w", 1200)))}, device)
            if page is not None:
                if page.get("error"):
                    self._json(int(page.get("status", 404)), {"error": page["error"]})
                else:
                    self._send(200, page["jpeg"], "image/jpeg", {"X-Pages": str(page["pages"])})
        elif path == "/api/stand":
            if self._allowed("study"):
                self._reply(self._ask("stand", None, device))
        elif path == "/api/clips":
            if self._allowed("clipboard"):
                self._reply(self._ask("clips", q.get("q", "")[:200], device))
        elif path.startswith("/api/clips/") and path.endswith("/image"):
            try:
                clip_id = int(path.split("/")[3])
            except (IndexError, ValueError):
                self._json(404, {"error": "not found"})
                return
            if self._allowed("clipboard"):
                data = self._ask("clip_image", clip_id, device)
                if data is not None:
                    self._send(200 if data else 404, data or b"", "image/png")
        elif path == "/api/audio/live":
            if self._allowed("sound"):
                self._stream_sound(device)
        elif path == "/api/music":
            if self._allowed("sound"):
                self._reply(self._ask("music", {"action": "state"}, device))
        else:
            self._json(404, {"error": "not found"})

    def _stream_sound(self, device: str) -> None:
        """Laptop sound, live: raw 16-bit PCM until the phone hangs up (format in the headers)."""
        from app.core.audio_loopback import HUB
        sub = HUB.subscribe()
        if sub is None:
            self._fail(503, "audio", detail=HUB.error[:200])
            return
        key, q, fmt = sub
        self.server.owner.ask("notice", "sound", device, timeout=2)
        try:
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Rate", str(fmt["rate"]))
            self.send_header("X-Channels", str(fmt["channels"]))
            self.send_header("Connection", "close")
            self.end_headers()
            while self.server.owner.running:
                try:
                    chunk = q.get(timeout=1.0)
                except Exception:
                    continue
                self.wfile.write(chunk)
                self.wfile.flush()
        except OSError:
            pass  # the phone stopped listening
        finally:
            HUB.unsubscribe(key)

    def _app_post(self, path: str, device: str) -> None:
        if path == "/api/input":
            if not self._allowed("remote"):
                return
            data = self._read_json()
            if data is not None:
                self._reply(self._ask("input", data, device))
            return
        if path == "/api/fs/open":
            if not self._allowed("files"):
                return
            data = self._read_json()
            if data is None:
                return
            target = self._shared_path(data.get("root"), str(data.get("path", "")))
            if target is None or not target.exists():
                self._json(404, {"error": "not found"})
            else:
                self._reply(self._ask("open_path", str(target), device))
            return
        if path.startswith("/api/desktop/open/"):
            item_id = self._id_from(path)
            if item_id is not None and self._allowed("desktop"):
                self._reply(self._ask("open_item", item_id, device))
            return
        if path == "/api/handoff":
            if not self._allowed("study"):
                return
            data = self._read_json()
            if data is not None:
                kind = data.get("kind")
                if kind not in ("pdf", "note"):
                    self._json(400, {"error": "kind"})
                    return
                self._reply(self._ask("handoff_set", {"kind": kind, "page": data.get("page"),
                                                      "note_id": data.get("note_id")}, device))
            return
        if path == "/api/music/upload":
            if self._allowed("sound"):
                self._receive_music(device)
            return
        if path in ("/api/music", "/api/mirror") or path.startswith("/api/clips/"):
            cap = {"/api/music": "sound", "/api/mirror": "remote"}.get(path, "clipboard")
            if not self._allowed(cap):
                return
            data = self._read_json()
            if data is None:
                return
            if path == "/api/music":
                self._reply(self._ask("music", {"action": str(data.get("action", ""))[:20],
                                                "value": data.get("value")}, device))
            elif path == "/api/mirror":
                self._reply(self._ask("mirror", str(data.get("action", "start")), device))
            else:
                clip_id = self._id_from(path)
                if clip_id is not None:
                    self._reply(self._ask("clip_action", {"id": clip_id, "action": str(data.get("action", ""))},
                                          device))
            return
        cap = {"/api/notes/": "study", "/api/tasks/": "study", "/api/timer": "study"}
        kind = next((c for c in cap if path.startswith(c)), None)
        if kind is None:
            self._fail(404, "not found")
            return
        if not self._allowed(cap[kind]):
            return
        data = self._read_json()
        if data is None:
            return
        if kind == "/api/timer":  # no remote control of the focus timer (it is a laptop app now)
            self._json(410, {"error": "timer control is on the laptop"})
            return
        item_id = self._id_from(path)
        if item_id is None:
            return
        if kind == "/api/notes/":
            self._reply(self._ask("save_note", {"id": item_id, "title": str(data.get("title", ""))[:300],
                                                "content": str(data.get("content", ""))[:MAX_TEXT * 5],
                                                "base": str(data.get("base", ""))}, device))
        else:
            self._reply(self._ask("task_done", {"id": item_id, "done": bool(data.get("done", True))}, device))

    def _receive_music(self, device: str) -> None:
        """A music file from the phone, to be played on the laptop (kept in a temporary folder)."""
        from app.core.phone_music import AUDIO_EXT, MUSIC_DIR
        owner = self.server.owner
        length = self._length()
        name = safe_filename(self.headers.get("X-Filename", ""))
        if Path(name).suffix.lower() not in AUDIO_EXT:
            self._fail(415, "type")
            return
        if length <= 0 or length > max(owner.max_mb, 100) * 1024 * 1024:
            self._fail(413, "too big", max_mb=max(owner.max_mb, 100))
            return
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        with self.server.lock:
            target = unique_path(MUSIC_DIR, name)
            target.touch()
        remaining = length
        try:
            with open(target, "wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise ConnectionError("upload interrupted")
                    fh.write(chunk)
                    remaining -= len(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            self.close_connection = True
            return
        self._reply(self._ask("music", {"action": "add", "path": str(target), "title": Path(name).stem}, device))

    def _shared_path(self, root_raw, rel: str) -> Path | None:
        """A path inside one of the shared folders, or None (also for .. / links that lead outside)."""
        try:
            idx = int(root_raw)
        except (TypeError, ValueError):
            return None
        roots = self.server.owner.shared_roots()
        if not 0 <= idx < len(roots):
            return None
        try:
            base = Path(roots[idx]["path"]).resolve(strict=True)
            target = (base / unquote(rel).replace("\\", "/").lstrip("/")).resolve()
        except (OSError, RuntimeError, ValueError):
            return None
        if target != base and base not in target.parents:
            return None
        return target

    def _list_folder(self, q: dict) -> None:
        roots = self.server.owner.shared_roots()
        if q.get("root") is None:
            self._json(200, {"roots": [{"i": i, "name": r["name"]} for i, r in enumerate(roots)]})
            return
        folder = self._shared_path(q.get("root"), q.get("path", ""))
        if folder is None or not folder.is_dir():
            self._json(404, {"error": "not found"})
            return
        entries = []
        try:
            with os.scandir(folder) as it:
                for e in it:
                    try:
                        st = e.stat()
                        if getattr(st, "st_file_attributes", 0) & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM):
                            continue
                        is_dir = e.is_dir()
                    except OSError:
                        continue
                    if e.name.startswith(("~$", ".")):
                        continue
                    entries.append({"name": e.name, "dir": is_dir, "size": 0 if is_dir else st.st_size,
                                    "mtime": int(st.st_mtime)})
                    if len(entries) >= 2000:
                        break
        except OSError:
            self._json(403, {"error": "denied"})
            return
        entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
        base = Path(roots[int(q["root"])]["path"]).resolve()
        rel = "" if folder == base else folder.relative_to(base).as_posix()
        self._json(200, {"root": int(q["root"]), "name": roots[int(q["root"])]["name"], "path": rel, "entries": entries})

    def _quick_share(self, path: str) -> None:
        """/s/<token>/<name> — a file shared from the PDF reader with a QR code: no pairing needed, the link is
        random (128 bit), works only on the local network and only until it expires (or is stopped)."""
        owner = self.server.owner
        parts = path.split("/")
        token = parts[2] if len(parts) > 2 else ""
        with owner.lock:
            entry = owner.shares.get(token)
            if entry and entry["expires"] < time.time():
                owner.shares.pop(token, None)
                entry = None
        if not entry or not token:
            body = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                    "<body style='font:16px system-ui;background:#0A0D14;color:#E8EBF4;display:grid;place-items:center;"
                    "height:90vh;text-align:center'><div><div style='font-size:46px'>⌛</div>"
                    "<p>This link has expired.<br>انتهت صلاحية هالرابط.</p></div>").encode("utf-8")
            self._send(410, body, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            return
        if self._stream_file(Path(entry["path"]), inline=False):
            with owner.lock:
                entry["count"] = entry.get("count", 0) + 1
            owner.share_downloaded.emit(token, self.ip)

    def _stream_file(self, path: Path, inline: bool = False) -> bool:
        import mimetypes
        try:
            size = path.stat().st_size
            fh = open(path, "rb")
        except OSError:
            self._json(404, {"error": "gone"})
            return False
        ctype = (mimetypes.guess_type(path.name)[0] or "application/octet-stream") if inline else "application/octet-stream"
        if inline and not ctype.startswith(("image/", "text/plain", "application/pdf", "audio/", "video/")):
            ctype = "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition",
                         f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(path.name)}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        self.end_headers()
        try:
            with fh:
                if self.command != "HEAD":
                    while chunk := fh.read(65536):
                        self.wfile.write(chunk)
        except OSError:
            self.close_connection = True
            return False
        return True

    def _static(self, path: str) -> None:
        """The app files: manifest, service worker, icons, and the certificate the phone installs once."""
        from app.core import lan_page
        owner = self.server.owner
        if path == "/manifest.webmanifest":
            self._send(200, lan_page.manifest().encode("utf-8"), "application/manifest+json")
        elif path == "/sw.js":
            self._send(200, lan_page.SERVICE_WORKER.encode("utf-8"), "text/javascript; charset=utf-8",
                       {"Service-Worker-Allowed": "/"})
        elif path.startswith("/icon-"):
            size = 192 if "192" in path else 512
            data = owner.icons.get(size, b"")
            self._send(200 if data else 404, data, "image/png", {"Cache-Control": "max-age=86400"})
        elif path == "/app.apk":  # the Android app (public, signed; nothing secret in it)
            apk = owner.apk_path()
            if apk is None:
                self._json(404, {"error": "no app"})
                return
            data = apk.read_bytes()
            self._send(200, data, "application/vnd.android.package-archive",
                       {"Content-Disposition": 'attachment; filename="StudyHub.apk"'})
        else:  # /ca.crt — public certificate (no secret in it)
            from app.core.local_ca import ca_der
            self._send(200, ca_der(), "application/x-x509-ca-cert",
                       {"Content-Disposition": 'attachment; filename="StudyHub-CA.crt"'})

    def do_POST(self):
        if not self._gate():
            return
        path = self.path.split("?", 1)[0]
        owner = self.server.owner
        if path == "/share":  # only reached when the app's service worker isn't active yet
            self.close_connection = True
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/pair":
            self._pair()
            return
        device = self._device()
        if not device:
            self._fail(401, "device")
            return
        if path == "/api/file":
            self._receive_file(device)
            return
        if path.startswith("/api/got/"):
            owner.mark_fetched(path.rsplit("/", 1)[1], device)
            self._json(200, {"ok": True})
            return
        if path not in ("/api/note", "/api/link", "/api/clip", "/api/task"):
            self._app_post(path, device)
            return
        data = self._read_json()
        if data is None:
            return
        kind = path.rsplit("/", 1)[1]
        text = str(data.get({"link": "url", "task": "title"}.get(kind, "text"), "")).strip()[:MAX_TEXT]
        if not text:
            self._json(400, {"error": "empty"})
            return
        item = {"kind": kind, "text": text, "title": str(data.get("title", "")).strip()[:200],
                "subject_id": owner.valid_subject(data.get("subject_id")), "device": device, "from": self.ip}
        if kind == "task":
            item["title"] = text[:300]
            item["due"] = data.get("due") if data.get("due") in ("today", "tomorrow", "none") else "today"
            item["priority"] = data.get("priority") if data.get("priority") in (1, 2, 3) else 2
            item["details"] = str(data.get("details", "")).strip()[:5000]
        owner.received.emit(item)  # queued to the GUI thread
        self._json(200, {"ok": True})

    def _pair(self) -> None:
        owner = self.server.owner
        if self._blocked():
            self._fail(429, "blocked")
            return
        data = self._read_json()
        if data is None:
            return
        if not owner.allow_pairing:
            self._json(403, {"error": "pairing closed"})
            return
        sent = normalize_code(str(data.get("code", "")))
        if not sent or not hmac.compare_digest(sent.encode(), owner.code.encode()):
            self._failed_attempt()
            self._json(401, {"error": "code"})
            return
        name = re.sub(r"[\x00-\x1f]", "", str(data.get("name", "")))[:60].strip() or "Phone"
        token = secrets.token_urlsafe(32)
        digest = token_hash(token)
        with owner.lock:
            owner.devices.add(digest)
        owner.paired.emit(digest, name, self.ip)
        log.info("phone link: new device paired (%s)", name)
        self._json(200, {"ok": True, "token": token, **owner.info()})

    def _receive_file(self, device: str) -> None:
        owner = self.server.owner
        length = self._length()
        if length <= 0 or length > owner.max_mb * 1024 * 1024:
            self._fail(413 if length > 0 else 411, "too big" if length > 0 else "length required",
                       max_mb=owner.max_mb)
            return
        subject_id = owner.valid_subject(self.headers.get("X-Subject"))
        folder = owner.folder_for(subject_id)
        if self.headers.get("X-Root") is not None:  # "upload here" while browsing a shared folder
            if not self._allowed("files"):
                return
            folder = self._shared_path(self.headers.get("X-Root"), self.headers.get("X-Dir", ""))
            if folder is None or not folder.is_dir():
                self._fail(404, "not found")
                return
            subject_id = None
        folder.mkdir(parents=True, exist_ok=True)
        name = safe_filename(self.headers.get("X-Filename", ""))
        with self.server.lock:  # pick the name and create the file atomically w.r.t. parallel uploads
            target = unique_path(folder, name)
            target.touch()
        part = target.with_name(target.name + ".part")
        remaining = length
        try:
            with open(part, "wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise ConnectionError("upload interrupted")
                    fh.write(chunk)
                    remaining -= len(chunk)
            part.replace(target)
        except Exception as exc:
            log.warning("phone link: upload of %s failed: %s", name, exc)
            for p in (part, target):
                try:
                    p.unlink()
                except OSError:
                    pass
            self.close_connection = True
            return
        from urllib.parse import unquote
        caption = " ".join(unquote(self.headers.get("X-Caption", "")).split())[:300]
        owner.received.emit({"kind": "file", "path": str(target), "title": target.name, "size": length,
                             "subject_id": subject_id, "device": device, "from": self.ip,
                             "caption": caption, "lab": self.headers.get("X-Lab") == "1"})
        self._json(200, {"ok": True, "name": target.name})

    def _send_outbox_file(self, raw_id: str, device: str) -> None:
        owner = self.server.owner
        entry = owner.outbox_entry(raw_id)
        if not entry or entry["kind"] != "file" or not Path(entry["content"]).is_file():
            self._json(404, {"error": "gone"})
            return
        if self._stream_file(Path(entry["content"])) and self.command != "HEAD":
            owner.mark_fetched(raw_id, device)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # Windows: True would let two programs share the port

    def handle_error(self, request, client_address):
        """Phones that don't trust the certificate yet, dropped Wi-Fi...: a log line, not a traceback."""
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (OSError, ConnectionError)):
            log.debug("phone link: %s from %s", exc, client_address[0])
        else:
            log.exception("phone link: request from %s failed", client_address[0])

    def __init__(self, addr, owner: "PhoneLinkServer", secure: bool = False):
        self.owner = owner
        self.secure = secure
        self.lock = threading.Lock()
        self.fails: dict[str, int] = {}
        self.blocked: dict[str, float] = {}
        super().__init__(addr, _Handler)


class PhoneLinkServer(QObject):
    """Owned by the GUI thread; the HTTP server runs in a daemon thread.

    The handler threads never touch the database: the GUI hands this object snapshots
    (paired devices, subjects, outbox) and receives everything back through queued signals."""

    received = pyqtSignal(dict)            # an item from a phone
    paired = pyqtSignal(str, str, str)     # token hash, device name, ip
    seen = pyqtSignal(str, str)            # token hash, ip (at most once a minute per device)
    fetched = pyqtSignal(int, str)         # outbox id, token hash
    request = pyqtSignal(int, str, object, str)  # request id, kind, payload, token hash -> answer(id, result)
    share_downloaded = pyqtSignal(str, str)  # quick-share token, ip

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.lock = threading.Lock()
        self.code = new_code()
        self.allow_pairing = True
        self.devices: set[str] = set()
        self.subjects: list[dict] = []     # {id, name, color, has_folder, folder}
        self.outbox: list[dict] = []       # newest first: {id, kind, title, content, size, created_at, fetched_by}
        self.folder = str(Path.home() / "Downloads")
        self.max_mb = 50
        self.lang = "ar"
        self.port = 0
        self.https_port = 0
        self.icons: dict[int, bytes] = {}
        self.caps: dict[str, bool] = {}    # phone app features switched on (Phone › Access)
        self.handoff: dict = {}            # what's open on the laptop (continue on the phone)
        self.lab: dict = {}                # the lab being recorded ({"title": ...}) — the phone shows a camera button
        self.roots: list[dict] = []        # shared folders {name, path}
        self.events: list[dict] = []       # {seq, kind, text} for phone notifications (newest last)
        self._event_seq = int(time.time())
        self._pending: dict[int, dict] = {}
        self._req_seq = 0
        self._seen_at: dict[str, float] = {}
        self.shares: dict[str, dict] = {}  # quick share: token -> {path, name, expires, count}
        self._httpd: _Server | None = None
        self._httpsd: _Server | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------- state from the GUI
    def set_devices(self, hashes) -> None:
        with self.lock:
            self.devices = set(hashes)

    def set_subjects(self, subjects: list[dict]) -> None:
        with self.lock:
            self.subjects = list(subjects)

    def set_outbox(self, entries: list[dict]) -> None:
        with self.lock:
            self.outbox = [dict(e) for e in entries]

    def set_access(self, caps: dict, roots: list[dict]) -> None:
        with self.lock:
            self.caps = dict(caps)
            self.roots = [dict(r) for r in roots]

    def add_share(self, path: str, minutes: int = 10) -> str:
        """A temporary public link for one file; returns the token."""
        import secrets
        token = secrets.token_urlsafe(16)
        with self.lock:
            now = time.time()
            self.shares = {k: v for k, v in self.shares.items() if v["expires"] > now}
            self.shares[token] = {"path": str(path), "name": Path(path).name, "expires": now + minutes * 60, "count": 0}
        return token

    def stop_share(self, token: str) -> None:
        with self.lock:
            self.shares.pop(token, None)

    def share_url(self, token: str, ip: str) -> str:
        with self.lock:
            name = self.shares.get(token, {}).get("name", "file")
        return f"http://{ip}:{self.port}/s/{token}/{quote(name)}"

    def active_shares(self) -> int:
        now = time.time()
        with self.lock:
            return sum(1 for v in self.shares.values() if v["expires"] > now)

    def set_lab(self, state: dict) -> None:
        with self.lock:
            self.lab = dict(state or {})

    def set_handoff(self, public: dict) -> None:
        with self.lock:
            self.handoff = dict(public)

    def push_event(self, kind: str, text: str) -> None:
        """Something to show as a notification on the phones (timer done, sent to phone, ...)."""
        with self.lock:
            self._event_seq += 1
            self.events = (self.events + [{"seq": self._event_seq, "kind": kind, "text": text[:300]}])[-30:]

    def answer(self, req_id: int, result) -> None:
        """Called by the GUI thread with the result of a request."""
        with self.lock:
            slot = self._pending.get(req_id)
        if slot is not None:
            slot["result"] = result
            slot["done"].set()

    # ------------------------------------------------- used by the handler
    def caps_for(self, secure: bool) -> dict:
        with self.lock:
            caps = {k: bool(v) for k, v in self.caps.items()}
        caps["need_https"] = [] if secure else sorted(k for k in SECURE_ONLY if caps.get(k))
        for k in SECURE_ONLY:
            caps[k] = caps.get(k, False) and secure
        caps["secure"] = secure
        return caps

    def shared_roots(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.roots]

    def events_since(self, since: int) -> list[dict]:
        with self.lock:
            if not since:  # first ping of a phone: only learn where "now" is
                return [{"seq": self._event_seq, "kind": "", "text": ""}]
            return [dict(e) for e in self.events if e["seq"] > since]

    def ask(self, kind: str, payload, device: str = "", timeout: float = 10.0):
        """From a handler thread: hand a request to the GUI thread and wait for its answer (None = no answer)."""
        with self.lock:
            self._req_seq += 1
            req_id = self._req_seq
            slot = self._pending[req_id] = {"done": threading.Event(), "result": None}
        self.request.emit(req_id, kind, payload, device)   # queued to the GUI thread
        answered = slot["done"].wait(timeout)
        with self.lock:
            self._pending.pop(req_id, None)
        return slot["result"] if answered else None

    def info(self) -> dict:
        return {"name": socket.gethostname(), "max_mb": self.max_mb, "lang": self.lang, "https_port": self.https_port}

    def mark_seen(self, digest: str, ip: str) -> None:
        now = time.monotonic()
        with self.lock:
            if now - self._seen_at.get(digest, -1e9) < 60:
                return
            self._seen_at[digest] = now
        self.seen.emit(digest, ip)

    def valid_subject(self, raw) -> int | None:
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            return None
        with self.lock:
            return sid if any(s["id"] == sid for s in self.subjects) else None

    def folder_for(self, subject_id: int | None) -> Path:
        with self.lock:
            for s in self.subjects:
                if s["id"] == subject_id and s.get("folder"):
                    return Path(s["folder"])
        return Path(self.folder)

    def outbox_list(self, device: str) -> list[dict]:
        with self.lock:
            return [{"id": e["id"], "kind": e["kind"], "title": e["title"],
                     "text": e["content"] if e["kind"] != "file" else "",
                     "size": e["size"], "created_at": e["created_at"],
                     "got": device in e.get("fetched_by", set())} for e in self.outbox]

    def pending_for(self, device: str) -> int:
        with self.lock:
            return sum(1 for e in self.outbox if device not in e.get("fetched_by", set()))

    def outbox_entry(self, raw_id: str) -> dict | None:
        try:
            oid = int(raw_id)
        except ValueError:
            return None
        with self.lock:
            return next((dict(e) for e in self.outbox if e["id"] == oid), None)

    def mark_fetched(self, raw_id: str, device: str) -> None:
        try:
            oid = int(raw_id)
        except ValueError:
            return
        with self.lock:
            entry = next((e for e in self.outbox if e["id"] == oid), None)
            if not entry or device in entry.setdefault("fetched_by", set()):
                return
            entry["fetched_by"].add(device)
        self.fetched.emit(oid, device)

    # --------------------------------------------------------- lifecycle
    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self, preferred_port: int = DEFAULT_PORT) -> int:
        """Returns the port in use (tries the next 9 if the preferred one is busy)."""
        if self._httpd:
            return self.port
        last_error: OSError | None = None
        for port in range(preferred_port, preferred_port + 10):
            try:
                self._httpd = _Server(("0.0.0.0", port), self)
                break
            except OSError as exc:
                last_error = exc
        if not self._httpd:
            raise last_error or OSError("no free port")
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="phone-link",
                                        kwargs={"poll_interval": 0.5}, daemon=True)
        self._thread.start()
        log.info("phone link listening on port %s", self.port)
        self._make_icons()
        self._start_https()
        self._start_discovery()
        return self.port

    @staticmethod
    def apk_path() -> Path | None:
        """The Android app shipped next to Study Hub (android\\StudyHub.apk), if this build has one."""
        import sys
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:  # running from source: the last build
            base = Path(__file__).resolve().parents[2] / "dist" / "StudyHub"
        apk = base / "android" / "StudyHub.apk"
        return apk if apk.is_file() else None

    def _start_discovery(self) -> None:
        """Answers the Android app's LAN broadcast with where to connect (never to public addresses)."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            sock.bind(("0.0.0.0", DISCOVERY_PORT))
            sock.settimeout(1.0)
        except OSError as exc:
            log.warning("phone link: discovery unavailable (%s)", exc)
            return
        self._udp = sock
        try:
            from app.core.local_ca import ca_der
            ca = hashlib.sha256(ca_der()).hexdigest()
        except Exception:
            ca = ""

        def serve():
            while self._udp is sock:
                try:
                    data, (ip, port) = sock.recvfrom(256)
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    addr = ipaddress.ip_address(ip)
                except ValueError:
                    continue
                if data.strip() != DISCOVERY_ASK or not (addr.is_private or addr.is_link_local) or addr.is_loopback:
                    continue
                reply = json.dumps({"app": "studyhub", "name": socket.gethostname(), "port": self.port,
                                    "https_port": self.https_port, "ca": ca}).encode()
                try:
                    sock.sendto(reply, (ip, port))
                except OSError:
                    pass
        threading.Thread(target=serve, name="phone-discovery", daemon=True).start()

    def _make_icons(self) -> None:
        """App icons for the phone's home screen (drawn here, in the GUI thread)."""
        from PyQt6.QtCore import QBuffer, QIODevice
        from app.ui.theme import app_icon_pixmap
        for size in (192, 512):
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            app_icon_pixmap(size).save(buf, "PNG")
            self.icons[size] = bytes(buf.data())

    def _start_https(self) -> None:
        """Same server over HTTPS (port + 1…): Android only installs web apps / shows them in Share over HTTPS."""
        try:
            from app.core.local_ca import server_context
            ctx = server_context(local_addresses())
        except Exception as exc:
            log.warning("phone link: HTTPS not available (%s)", exc)
            return
        for port in range(self.port + 1, self.port + 11):
            try:
                httpsd = _Server(("0.0.0.0", port), self, secure=True)
            except OSError:
                continue
            # handshake in the request thread, so a silent client can't block new connections
            httpsd.socket = ctx.wrap_socket(httpsd.socket, server_side=True, do_handshake_on_connect=False)
            self._httpsd, self.https_port = httpsd, port
            threading.Thread(target=httpsd.serve_forever, name="phone-link-https",
                             kwargs={"poll_interval": 0.5}, daemon=True).start()
            log.info("phone link HTTPS on port %s", port)
            return

    def stop(self) -> None:
        udp, self._udp = getattr(self, "_udp", None), None
        if udp:
            udp.close()
        for attr in ("_httpsd", "_httpd"):
            httpd = getattr(self, attr)
            setattr(self, attr, None)
            if httpd:
                httpd.shutdown()
                httpd.server_close()
        self.https_port = 0
        log.info("phone link stopped")

    def unblock_all(self) -> None:
        for httpd in (self._httpd, self._httpsd):
            if httpd:
                with httpd.lock:
                    httpd.blocked.clear()
                    httpd.fails.clear()

    def urls(self) -> list[str]:
        return [f"http://{ip}:{self.port}/" for ip in local_addresses()] if self.running else []

    def https_urls(self) -> list[str]:
        return [f"https://{ip}:{self.https_port}/" for ip in local_addresses()] if self.https_port else []
