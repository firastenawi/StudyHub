"""Infrastructure plugin logic: hosts, credentials (vault), SSH actions, monitoring results, operations log."""
from __future__ import annotations

import json
import threading

import paramiko
from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices

from app.core.i18n import tr
from app.core.logs import log
from app.plugins.infra import ssh
from app.plugins.infra.monitor import Monitor
from app.plugins.infra.store import InfraStore


class _Bg(QObject):
    """fn() in a thread, callback(result) back in the GUI thread (exceptions are passed as the result)."""
    done = pyqtSignal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cb: dict[int, object] = {}
        self._n = 0
        self.done.connect(self._finish)

    def run(self, fn, callback) -> None:
        self._n += 1
        job = self._n
        self._cb[job] = callback

        def work():
            try:
                result = fn()
            except Exception as exc:
                result = exc
            self.done.emit(job, result)
        threading.Thread(target=work, daemon=True).start()

    def _finish(self, job: int, result) -> None:
        cb = self._cb.pop(job, None)
        if cb:
            try:
                cb(result)
            except RuntimeError:
                pass


def error_text(exc: Exception) -> str:
    if isinstance(exc, paramiko.AuthenticationException):
        return tr("infra_err_auth")
    if isinstance(exc, (TimeoutError, OSError)) and not isinstance(exc, paramiko.SSHException):
        return tr("infra_err_unreachable", error=getattr(exc, "strerror", None) or str(exc) or type(exc).__name__)
    return str(exc) or type(exc).__name__


class InfraController(QObject):
    changed = pyqtSignal(str)          # "hosts" | "checks" | "log"
    busy = pyqtSignal(int, bool)        # host id, working?

    def __init__(self, api):
        super().__init__(api.ctl)
        self.api = api
        self.ctl = api.ctl
        self.store = InfraStore(api.db)
        self.bg = _Bg(self)
        self.monitor = Monitor(self.store.checks, self)
        self.monitor.result.connect(self._on_result)

    def start(self) -> None:
        self.monitor.start()

    def stop(self) -> None:
        self.monitor.stop()

    # ------------------------------------------------------------- credentials
    def password(self, host: dict, parent=None) -> str | None:
        """The host's password from the vault (asks to unlock it). None if cancelled / not stored."""
        if host["auth"] != "password":
            return None
        if not host["vault_id"]:
            self.api.notify(tr("infra_no_password"), "warning")
            return None
        if not self.ctl.ensure_vault_unlocked(parent):
            return None
        entry = self.ctl.vault.get(host["vault_id"])
        self.ctl.vault_activity()
        if not entry:
            self.api.notify(tr("infra_no_password"), "warning")
            return None
        return entry["password"]

    def save_host(self, data: dict, password: str | None, host_id: int | None = None, parent=None) -> int | None:
        """Creates / updates a host; a typed password is stored (encrypted) in the vault."""
        old = self.store.host(host_id) if host_id else None
        vault_id = old["vault_id"] if old else None
        if data.get("auth") == "password" and password:
            if not self.ctl.ensure_vault_unlocked(parent):
                return None
            title = f"SSH · {data['name']}"
            url = f"ssh://{data['username']}@{data['address']}:{data['ssh_port']}"
            entry = self.ctl.vault.get(vault_id) if vault_id else None
            if entry:
                self.ctl.vault.update(vault_id, title, url, data["username"], password, entry.get("notes", ""),
                                      entry.get("category", "work"), bool(entry.get("favorite")))
            else:
                vault_id = self.ctl.vault.add(title, url, data["username"], password, tr("infra_vault_note"), "work")
            self.ctl.vault_changed()
        data["vault_id"] = vault_id
        if old and (old["address"] != data["address"] or int(old["ssh_port"]) != int(data["ssh_port"])):
            data["host_key"] = ""  # another machine: trust its key again
        if host_id:
            self.store.update_host(host_id, **data)
            self.store.log("edit_host", data["name"], host_id)
        else:
            host_id = self.store.add_host(**data)
            self.store.log("add_host", f"{data['name']} ({data['address']})", host_id)
        self.changed.emit("hosts")
        self.changed.emit("log")
        return host_id

    def delete_host(self, host_id: int, parent=None) -> None:
        from app.ui.dialogs.base import ConfirmDialog
        host = self.store.host(host_id)
        if host and ConfirmDialog.ask(tr("infra_delete_host"), tr("infra_delete_host_msg", name=host["name"]),
                                      tr("delete"), parent):
            self.store.delete_host(host_id)
            self.store.log("delete_host", host["name"])
            self.changed.emit("hosts")
            self.changed.emit("checks")
            self.changed.emit("log")

    # ------------------------------------------------------------- SSH actions
    def _with_trust(self, host_id: int, action, on_done, parent=None) -> None:
        """Runs action(host, password, key_path) in a thread; handles the host-key question, then on_done(result)."""
        host = self.store.host(host_id)
        if not host:
            return
        password = None
        if host["auth"] == "password":
            password = self.password(host, parent)
            if password is None:
                return
        key = host["key_path"] if host["auth"] == "key" else None
        self.busy.emit(host_id, True)

        def finished(result):
            self.busy.emit(host_id, False)
            if isinstance(result, ssh.HostKeyError):
                from app.plugins.infra.page import TrustDialog
                if TrustDialog.ask(host, result, parent):
                    self.store.update_host(host_id, host_key=result.key_text)
                    self.store.log("trust_key", result.fingerprint, host_id)
                    self.changed.emit("log")
                    return self._with_trust(host_id, action, on_done, parent)
                self.store.log("trust_key", tr("infra_refused") + " " + result.fingerprint, host_id, ok=False)
                self.changed.emit("log")
                return
            on_done(result)
        self.bg.run(lambda: action(dict(host), password, key), finished)

    def refresh_status(self, host_id: int, parent=None, quiet: bool = False) -> None:
        def done(result):
            host = self.store.host(host_id)
            if host is None:
                return
            if isinstance(result, Exception):
                self.store.update_host(host_id, last_status=json.dumps({"error": error_text(result)}),
                                       status_at=_now())
                self.store.log("status", error_text(result), host_id, ok=False)
                if not quiet:
                    self.api.notify(f"{host['name']}: {error_text(result)}", "error")
            else:
                self.store.update_host(host_id, last_status=json.dumps(result), status_at=_now())
                self.store.log("status", result.get("os", ""), host_id)
            self.changed.emit("hosts")
            self.changed.emit("log")
        self._with_trust(host_id, ssh.fetch_status, done, parent)

    def switch_to_key(self, host_id: int, parent=None) -> None:
        from app.ui.dialogs.base import ConfirmDialog
        host = self.store.host(host_id)
        if not host or not ConfirmDialog.ask(tr("infra_switch_key"), tr("infra_switch_key_msg", name=host["name"]),
                                             tr("infra_switch_key"), parent, danger=False):
            return

        def action(h, password, _key):
            return ssh.install_key(h, password)

        def done(result):
            if isinstance(result, Exception):
                self.store.log("key_setup", error_text(result), host_id, ok=False)
                self.api.notify(tr("infra_key_failed", error=error_text(result)), "error")
            else:
                self.store.update_host(host_id, auth="key", key_path=result)
                self.store.log("key_setup", result, host_id)
                self.api.notify(tr("infra_key_done", name=host["name"]), "success")
            self.changed.emit("hosts")
            self.changed.emit("log")
        self._with_trust(host_id, action, done, parent)

    def open_terminal(self, host_id: int, parent=None, remote: str = "") -> None:
        host = self.store.host(host_id)
        if not host:
            return
        if host["auth"] == "password":
            password = self.password(host, parent)
            if password is None:
                return
            self.ctl.copy_secret(password, host["vault_id"])
            self.api.notify(tr("infra_password_copied"), "info")
        try:
            ssh.open_terminal(host, remote)
            self.store.log("terminal", host["address"], host_id)
        except OSError as exc:
            self.store.log("terminal", str(exc), host_id, ok=False)
            self.api.notify(str(exc), "error")
        self.changed.emit("log")

    def open_rdp(self, host_id: int) -> None:
        host = self.store.host(host_id)
        if host:
            ssh.open_rdp(host)
            self.store.log("rdp", host["address"], host_id)
            self.changed.emit("log")

    def open_web(self, host_id: int) -> None:
        host = self.store.host(host_id)
        if host and host["web_url"]:
            url = host["web_url"] if "://" in host["web_url"] else "https://" + host["web_url"]
            QDesktopServices.openUrl(QUrl(url))

    def forget_host_key(self, host_id: int) -> None:
        self.store.update_host(host_id, host_key="")
        self.store.log("forget_key", "", host_id)
        self.changed.emit("hosts")
        self.changed.emit("log")

    # -------------------------------------------------- commands (library, nginx)
    def run_on_host(self, host_id: int, command: str, sudo: bool, on_done, parent=None, label: str = "") -> None:
        """Runs a shell command on one host (trust / vault handled); on_done(result dict or Exception)."""
        def action(h, password, key):
            return ssh.run_command(h, password, key, command, sudo)

        def done(result):
            ok = not isinstance(result, Exception) and result.get("code") == 0
            detail = label or command.splitlines()[0][:120]
            if not ok:
                why = error_text(result) if isinstance(result, Exception) else f"exit {result.get('code')}"
                detail = f"{detail} — {why}"
            self.store.log("command", detail, host_id, ok=ok)
            self.changed.emit("log")
            on_done(result)
        self._with_trust(host_id, action, done, parent)

    # ------------------------------------------------------------------ Proxmox
    def pve_settings(self, host_id: int) -> dict:
        return self.store.pve(host_id)

    def save_pve_settings(self, host_id: int, port: int, auth: str, token_id: str, secret: str | None,
                          realm: str, parent=None) -> bool:
        cur = self.store.pve(host_id)
        vault_id = cur["vault_id"]
        if auth == "token" and secret:
            if not self.ctl.ensure_vault_unlocked(parent):
                return False
            host = self.store.host(host_id)
            title = f"Proxmox API · {host['name'] if host else host_id}"
            entry = self.ctl.vault.get(vault_id) if vault_id else None
            if entry:
                self.ctl.vault.update(vault_id, title, entry.get("url", ""), token_id, secret, entry.get("notes", ""),
                                      entry.get("category", "work"), bool(entry.get("favorite")))
            else:
                vault_id = self.ctl.vault.add(title, f"https://{host['address'] if host else ''}:{port}", token_id,
                                              secret, tr("infra_vault_note"), "work")
            self.ctl.vault_changed()
        self.store.save_pve(host_id, port=int(port), auth=auth, token_id=token_id.strip(), vault_id=vault_id,
                            realm=realm.strip() or "pam")
        self.store.log("pve_settings", f"{auth} :{port}", host_id)
        self.changed.emit("log")
        return True

    def _pve_client(self, host_id: int, parent=None):
        from app.plugins.infra.proxmox import Proxmox
        host = self.store.host(host_id)
        if not host:
            return None
        cfg = self.store.pve(host_id)
        if cfg["auth"] == "token":
            if not cfg["token_id"] or not cfg["vault_id"]:
                self.api.notify(tr("pve_need_setup"), "warning")
                return None
            if not self.ctl.ensure_vault_unlocked(parent):
                return None
            entry = self.ctl.vault.get(cfg["vault_id"])
            self.ctl.vault_activity()
            if not entry:
                self.api.notify(tr("pve_need_setup"), "warning")
                return None
            return Proxmox(host["address"], cfg["port"], cfg["fingerprint"], token_id=cfg["token_id"],
                           secret=entry["password"])
        password = self.password(host, parent)
        if password is None:
            return None
        user = host["username"] if "@" in host["username"] else f"{host['username']}@{cfg['realm'] or 'pam'}"
        return Proxmox(host["address"], cfg["port"], cfg["fingerprint"], user=user, password=password)

    def pve_call(self, host_id: int, fn, on_done, parent=None, label: str = "") -> None:
        """fn(client) in a thread; a new / changed certificate asks first (then retries)."""
        from app.plugins.infra.proxmox import CertError
        client = self._pve_client(host_id, parent)
        if client is None:
            return
        self.busy.emit(host_id, True)

        def finished(result):
            self.busy.emit(host_id, False)
            if isinstance(result, CertError):
                from app.plugins.infra.page import TrustDialog
                host = self.store.host(host_id)
                if host and TrustDialog.ask(host, result, parent):
                    self.store.save_pve(host_id, fingerprint=result.key_text)
                    self.store.log("trust_cert", result.fingerprint, host_id)
                    self.changed.emit("log")
                    return self.pve_call(host_id, fn, on_done, parent, label)
                self.store.log("trust_cert", tr("infra_refused") + " " + result.fingerprint, host_id, ok=False)
                self.changed.emit("log")
                return
            if label:
                self.store.log("proxmox", label if not isinstance(result, Exception) else f"{label} — {result}",
                               host_id, ok=not isinstance(result, Exception))
                self.changed.emit("log")
            on_done(result)
        self.bg.run(lambda: fn(client), finished)

    # --------------------------------------------------------------- checks
    def add_basic_checks(self, host_id: int) -> int:
        host = self.store.host(host_id)
        if not host:
            return 0
        made = 0
        existing = {(c["kind"], c["target"], c["port"]) for c in self.store.checks() if c["host_id"] == host_id}
        wanted = [("ping", host["address"], 0), ("tcp", host["address"], int(host["ssh_port"] or 22))]
        if host["web_url"]:
            wanted.append(("http", host["web_url"], 0))
            if host["web_url"].lower().startswith("https") or "://" not in host["web_url"]:
                wanted.append(("tls", host["web_url"], 0))
        for kind, target, port in wanted:
            if (kind, target, port) in existing:
                continue
            name = {"ping": "Ping", "tcp": f"SSH :{port}", "http": "Web", "tls": tr("infra_kind_tls")}[kind]
            self.store.add_check(host_id=host_id, name=name, kind=kind, target=target, port=port,
                                 insecure=int(host["kind"] == "proxmox"))
            made += 1
        self.changed.emit("checks")
        self.monitor.run_now()
        return made

    def save_check(self, data: dict, check_id: int | None = None) -> None:
        if check_id:
            self.store.update_check(check_id, **data)
        else:
            check_id = self.store.add_check(**data)
        self.monitor.next_due.pop(check_id, None)
        self.changed.emit("checks")
        self.monitor.run_now(check_id)

    def delete_check(self, check_id: int) -> None:
        self.store.delete_check(check_id)
        self.monitor.history.pop(check_id, None)
        self.changed.emit("checks")

    def _on_result(self, check_id: int, state: str, ms: float, detail: str) -> None:
        check = self.store.check(check_id)
        if not check:
            return
        self.monitor.add_history(check_id, state, ms)
        shown = self.monitor.record(check_id, state)
        if not shown:  # first failure: wait for confirmation
            self.changed.emit("checks")
            return
        fields = {"last_ms": ms, "last_detail": detail}
        previous = check["state"]
        if shown != previous:
            fields.update(state=shown, last_change=_now())
            host = self.store.host(check["host_id"]) if check["host_id"] else None
            who = f"{host['name']} · {check['name']}" if host else check["name"]
            if shown == "down":
                self.api.notify(tr("infra_down", name=who, detail=detail), "error")
                self.store.log("down", f"{who}: {detail}", check["host_id"], ok=False)
            elif shown == "warn":
                self.api.notify(tr("infra_warn", name=who, detail=detail), "warning")
                self.store.log("warn", f"{who}: {detail}", check["host_id"], ok=False)
            elif previous in ("down", "warn"):
                self.api.notify(tr("infra_up", name=who), "success")
                self.store.log("up", who, check["host_id"])
            self.changed.emit("log")
        self.store.update_check(check_id, **fields)
        self.changed.emit("checks")


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
