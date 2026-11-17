"""
Proxmox VE API client (no Qt here): nodes, VMs and containers, power actions, snapshots, tasks.

Safety:
* the API (port 8006) usually has a self-signed certificate: the first connection shows its SHA-256
  fingerprint and asks (like SSH host keys); afterwards a different certificate stops the connection.
  A certificate that Windows already trusts for that name (e.g. Let's Encrypt) needs no question.
* the fingerprint is checked before anything is sent, so a password / token never reaches the wrong server
* sign in with an API token (Datacenter › Permissions › API Tokens — recommended, can be limited and revoked)
  or user + password (a ticket, kept only in memory); secrets live in the encrypted vault
"""
from __future__ import annotations

import hashlib
import http.client
import json
import socket
import ssl
import time
from urllib.parse import quote, urlencode

TIMEOUT = 12


class CertError(Exception):
    """Unknown / changed certificate — same shape as ssh.HostKeyError so the same trust dialog is used."""

    def __init__(self, fingerprint: str, old: str = ""):
        super().__init__("certificate not trusted")
        self.key_text = fingerprint
        self.fingerprint = _pretty(fingerprint)
        self.changed = bool(old)
        self.old_fingerprint = _pretty(old)


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status} {message}".strip())
        self.status = status


def _pretty(hexdigest: str) -> str:
    return ":".join(hexdigest[i:i + 2] for i in range(0, len(hexdigest), 2)).upper() if hexdigest else ""


class Proxmox:
    def __init__(self, address: str, port: int = 8006, fingerprint: str = "", token_id: str = "",
                 secret: str = "", user: str = "", password: str = ""):
        self.address, self.port = address, int(port or 8006)
        self.fingerprint = fingerprint.lower().replace(":", "")
        self.token_id, self.secret = token_id, secret
        self.user, self.password = user, password
        self._ticket: tuple[str, str, float] | None = None   # ticket, CSRF token, when

    # ------------------------------------------------------------------ transport
    def _connect(self) -> http.client.HTTPSConnection:
        # 1) a certificate Windows trusts for this name: fine without pinning
        try:
            conn = http.client.HTTPSConnection(self.address, self.port, timeout=TIMEOUT,
                                               context=ssl.create_default_context())
            conn.connect()
            return conn
        except ssl.SSLError:
            pass
        # 2) self-signed: compare the fingerprint before sending anything
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(self.address, self.port, timeout=TIMEOUT, context=ctx)
        conn.connect()
        seen = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
        if seen != self.fingerprint:
            conn.close()
            raise CertError(seen, self.fingerprint)
        return conn

    def _request(self, method: str, path: str, data: dict | None = None, auth: bool = True):
        headers = {"Accept": "application/json"}
        body = None
        if data is not None:
            body = urlencode({k: v for k, v in data.items() if v is not None})
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if auth:
            if self.token_id:
                headers["Authorization"] = f"PVEAPIToken={self.token_id}={self.secret}"
            else:
                ticket, csrf = self._login()
                headers["Cookie"] = f"PVEAuthCookie={ticket}"
                if method != "GET":
                    headers["CSRFPreventionToken"] = csrf
        conn = self._connect()
        try:
            conn.request(method, "/api2/json" + path, body, headers)
            r = conn.getresponse()
            raw = r.read(8 * 1024 * 1024)
        finally:
            conn.close()
        if r.status >= 400:
            msg = r.reason
            try:
                payload = json.loads(raw)
                if payload.get("errors"):
                    msg = "; ".join(f"{k}: {v}" for k, v in payload["errors"].items())
                elif payload.get("message"):
                    msg = payload["message"]
            except ValueError:
                pass
            raise ApiError(r.status, str(msg).strip())
        return json.loads(raw or b"{}").get("data")

    def _login(self) -> tuple[str, str]:
        if self._ticket and time.time() - self._ticket[2] < 90 * 60:
            return self._ticket[0], self._ticket[1]
        data = self._request("POST", "/access/ticket", {"username": self.user, "password": self.password}, auth=False)
        self._ticket = (data["ticket"], data["CSRFPreventionToken"], time.time())
        return self._ticket[0], self._ticket[1]

    # ------------------------------------------------------------------ reading
    def version(self) -> dict:
        return self._request("GET", "/version")

    def nodes(self) -> list[dict]:
        return sorted(self._request("GET", "/nodes") or [], key=lambda n: n.get("node", ""))

    def guests(self) -> list[dict]:
        """VMs (qemu) and containers (lxc) of the whole cluster."""
        rows = self._request("GET", "/cluster/resources?type=vm") or []
        return sorted((r for r in rows if not r.get("template")), key=lambda r: int(r.get("vmid", 0)))

    def snapshots(self, node: str, kind: str, vmid: int) -> list[dict]:
        rows = self._request("GET", f"/nodes/{quote(node)}/{kind}/{int(vmid)}/snapshot") or []
        return [r for r in rows if r.get("name") != "current"]

    def task(self, node: str, upid: str) -> dict:
        return self._request("GET", f"/nodes/{quote(node)}/tasks/{quote(upid, safe='')}/status")

    # ------------------------------------------------------------------ actions (return a task UPID)
    def power(self, node: str, kind: str, vmid: int, action: str) -> str:
        if action not in ("start", "shutdown", "stop", "reboot", "suspend", "resume"):
            raise ValueError(action)
        return self._request("POST", f"/nodes/{quote(node)}/{kind}/{int(vmid)}/status/{action}", {})

    def snapshot(self, node: str, kind: str, vmid: int, name: str, description: str = "") -> str:
        return self._request("POST", f"/nodes/{quote(node)}/{kind}/{int(vmid)}/snapshot",
                             {"snapname": name, "description": description or None})

    def rollback(self, node: str, kind: str, vmid: int, name: str) -> str:
        return self._request("POST", f"/nodes/{quote(node)}/{kind}/{int(vmid)}/snapshot/{quote(name)}/rollback", {})

    def delete_snapshot(self, node: str, kind: str, vmid: int, name: str) -> str:
        return self._request("DELETE", f"/nodes/{quote(node)}/{kind}/{int(vmid)}/snapshot/{quote(name)}")

    def wait(self, node: str, upid: str, seconds: int = 120) -> dict:
        """Waits for a task; returns its final status ({"status": "stopped", "exitstatus": "OK"} when fine)."""
        end = time.time() + seconds
        while time.time() < end:
            st = self.task(node, upid)
            if st.get("status") == "stopped":
                return st
            time.sleep(1.5)
        return {"status": "running"}

    def console_url(self, node: str, kind: str, vmid: int, name: str = "") -> str:
        console = "kvm" if kind == "qemu" else "lxc"
        return (f"https://{self.address}:{self.port}/?console={console}&novnc=1&vmid={int(vmid)}"
                f"&vmname={quote(name)}&node={quote(node)}&resize=off&cmd=")


def reachable(address: str, port: int) -> bool:
    try:
        with socket.create_connection((address, int(port)), timeout=4):
            return True
    except OSError:
        return False
