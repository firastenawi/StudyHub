"""
Service monitoring: ping, TCP port, HTTP(S) and TLS-certificate checks on a schedule.

Checks run in a small thread pool; results come back to the GUI thread as a signal. A service is
only reported down after 2 failures in a row (no alarms for one lost packet); you're told again when
it comes back. Certificates warn `warn_days` before they expire.
"""
from __future__ import annotations

import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

FAILS_BEFORE_DOWN = 2


def check_ping(target: str) -> tuple[bool, float, str]:
    start = time.monotonic()
    try:
        out = subprocess.run(["ping", "-n", "1", "-w", "1500", target], capture_output=True, text=True, timeout=6,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), errors="replace")
    except Exception as exc:
        return False, 0, str(exc)
    ms = (time.monotonic() - start) * 1000
    m = re.search(r"[=<]\s*(\d+)\s*ms", out.stdout, re.I)
    if m:
        ms = float(m.group(1))
    ok = out.returncode == 0 and "ttl=" in out.stdout.lower()
    return ok, ms, "" if ok else "no reply"


def check_tcp(target: str, port: int) -> tuple[bool, float, str]:
    start = time.monotonic()
    try:
        with socket.create_connection((target, port), timeout=4):
            pass
    except OSError as exc:
        return False, 0, exc.strerror or str(exc)
    return True, (time.monotonic() - start) * 1000, ""


def _context(insecure: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def check_http(url: str, insecure: bool) -> tuple[bool, float, str]:
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    start = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "StudyHub-monitor"})
    try:
        with urllib.request.urlopen(req, timeout=8, context=_context(insecure)) as r:
            code = r.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    except Exception as exc:
        reason = getattr(exc, "reason", exc)
        return False, 0, str(reason)[:120]
    ms = (time.monotonic() - start) * 1000
    return code < 400, ms, f"HTTP {code}"


def cert_days_left(host: str, port: int, insecure: bool) -> tuple[int, str]:
    """Days until the certificate expires (negative = expired) and its subject."""
    ctx = _context(insecure)
    with socket.create_connection((host, port or 443), timeout=6) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    from cryptography import x509
    cert = x509.load_der_x509_certificate(der)
    expires = cert.not_valid_after_utc
    days = (expires - datetime.now(timezone.utc)).days
    return days, expires.strftime("%Y-%m-%d")


def split_host_port(target: str) -> tuple[str, int | None]:
    """'https://pve:8006/x' -> ('pve', 8006); 'example.com' -> ('example.com', None)."""
    hostport = re.sub(r"^[a-z]+://", "", target.strip(), flags=re.I).split("/")[0]
    host, _, port = hostport.rpartition(":") if hostport.count(":") == 1 else (hostport, "", "")
    return (host or hostport), (int(port) if port.isdigit() else None)


def check_tls(target: str, port: int, insecure: bool, warn_days: int) -> tuple[str, float, str]:
    host, url_port = split_host_port(target)
    start = time.monotonic()
    try:
        days, date = cert_days_left(host, port or url_port or 443, insecure)
    except ssl.SSLCertVerificationError as exc:
        return "down", 0, f"untrusted: {exc.verify_message}"
    except Exception as exc:
        return "down", 0, str(exc)[:120]
    ms = (time.monotonic() - start) * 1000
    if days < 0:
        return "down", ms, f"expired {date}"
    return ("warn" if days <= warn_days else "up"), ms, f"{days}d · {date}"


def run_check(check: dict) -> tuple[str, float, str]:
    kind = check["kind"]
    if kind == "tls":
        return check_tls(check["target"], int(check["port"] or 0), bool(check["insecure"]), int(check["warn_days"]))
    if kind == "ping":
        ok, ms, detail = check_ping(check["target"])
    elif kind == "tcp":
        ok, ms, detail = check_tcp(check["target"], int(check["port"]))
    else:
        ok, ms, detail = check_http(check["target"], bool(check["insecure"]))
    return ("up" if ok else "down"), ms, detail


class Monitor(QObject):
    """Runs due checks every few seconds. result(check_id, state, ms, detail) arrives in the GUI thread."""

    result = pyqtSignal(int, str, float, str)

    def __init__(self, checks_provider, parent=None):
        super().__init__(parent)
        self.checks_provider = checks_provider     # () -> list[dict] (called in the GUI thread)
        self.pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="infra-check")
        self.next_due: dict[int, float] = {}
        self.running: set[int] = set()
        self.fails: dict[int, int] = {}
        self.history: dict[int, deque] = {}        # last 60 (state, ms) per check, for sparkline / uptime
        self.timer = QTimer(self, interval=3000, timeout=self.tick)

    def start(self) -> None:
        self.timer.start()
        QTimer.singleShot(500, self.tick)

    def stop(self) -> None:
        self.timer.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def run_now(self, check_id: int | None = None) -> None:
        for cid in list(self.next_due):
            if check_id is None or cid == check_id:
                self.next_due[cid] = 0
        self.tick()

    def tick(self) -> None:
        now = time.monotonic()
        for check in self.checks_provider():
            cid = check["id"]
            if not check["enabled"] or cid in self.running:
                continue
            if now < self.next_due.get(cid, 0):
                continue
            self.next_due[cid] = now + max(15, int(check["interval_sec"]))
            self.running.add(cid)
            snapshot = dict(check)
            self.pool.submit(self._work, snapshot)

    def _work(self, check: dict) -> None:
        try:
            state, ms, detail = run_check(check)
        except Exception as exc:
            state, ms, detail = "down", 0.0, str(exc)[:120]
        self.result.emit(check["id"], state, float(ms), detail)

    def record(self, check_id: int, state: str) -> str:
        """Debounces failures; returns the state to show (a single failure keeps the previous state)."""
        self.running.discard(check_id)
        if state == "down":
            self.fails[check_id] = self.fails.get(check_id, 0) + 1
            if self.fails[check_id] < FAILS_BEFORE_DOWN:
                return ""  # wait for one more failure before calling it down
        else:
            self.fails[check_id] = 0
        return state

    def add_history(self, check_id: int, state: str, ms: float) -> None:
        self.history.setdefault(check_id, deque(maxlen=60)).append((state, ms))

    def uptime(self, check_id: int) -> float | None:
        h = self.history.get(check_id)
        if not h:
            return None
        return 100.0 * sum(1 for s, _ in h if s != "down") / len(h)
