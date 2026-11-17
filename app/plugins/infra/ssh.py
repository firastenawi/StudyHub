"""
SSH to your servers: status, key setup, terminals and RDP (no Qt here).

Safety:
* host keys are trust-on-first-use: the first connection shows the server's SHA-256 fingerprint
  and asks; afterwards a different key stops the connection (possible man-in-the-middle)
* passwords come from the encrypted vault and are only handed to paramiko in memory
* "Switch to an SSH key" creates ~/.ssh/studyhub_ed25519 (the standard OpenSSH place, so Windows'
  own `ssh` uses it too), appends the public key to the server's authorized_keys once over the
  password session, and checks the key works before the host is switched over
"""
from __future__ import annotations

import base64
import hashlib
import os
import shlex
import shutil
import socket
import subprocess
from pathlib import Path

import paramiko

KEY_PATH = Path.home() / ".ssh" / "studyhub_ed25519"
CONNECT_TIMEOUT = 8
KINDS = ("linux", "proxmox", "docker", "vmware", "windows", "other")


class HostKeyError(Exception):
    """Unknown host key (first connection) or a key that changed since it was trusted."""

    def __init__(self, changed: bool, key_text: str, old_text: str = ""):
        super().__init__("host key changed" if changed else "unknown host key")
        self.changed = changed
        self.key_text = key_text
        self.fingerprint = fingerprint(key_text)
        self.old_fingerprint = fingerprint(old_text) if old_text else ""


def key_text(key: paramiko.PKey) -> str:
    return f"{key.get_name()} {key.get_base64()}"


def fingerprint(text: str) -> str:
    """'ssh-ed25519 AAAA…' -> 'SHA256:…' (the format `ssh-keygen -l` prints)."""
    try:
        blob = base64.b64decode(text.split()[1])
    except (IndexError, ValueError):
        return ""
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


class _TrustPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, trusted: str):
        self.trusted = trusted

    def missing_host_key(self, client, hostname, key):
        seen = key_text(key)
        if self.trusted and seen == self.trusted:
            return
        raise HostKeyError(bool(self.trusted), seen, self.trusted)


def connect(host: dict, password: str | None = None, key_path: str | None = None) -> paramiko.SSHClient:
    """Raises HostKeyError, paramiko.AuthenticationException, OSError / socket.timeout…"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_TrustPolicy(host.get("host_key", "")))
    kwargs = dict(hostname=host["address"], port=int(host.get("ssh_port") or 22), username=host["username"],
                  timeout=CONNECT_TIMEOUT, banner_timeout=CONNECT_TIMEOUT, auth_timeout=CONNECT_TIMEOUT,
                  allow_agent=False, look_for_keys=False)
    if key_path:
        kwargs["key_filename"] = key_path
    else:
        kwargs["password"] = password or ""
    try:
        client.connect(**kwargs)
    except Exception:
        client.close()
        raise
    return client


def run(client: paramiko.SSHClient, command: str, timeout: int = 25) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    return stdout.channel.recv_exit_status(), out, err


# --------------------------------------------------------------------- status
STATUS_SCRIPT = r"""
echo "host=$(hostname)"
echo "os=$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
echo "kernel=$(uname -r)"
echo "uptime=$(cut -d' ' -f1 /proc/uptime)"
echo "load=$(cut -d' ' -f1-3 /proc/loadavg)"
echo "cpus=$(nproc 2>/dev/null)"
free -b 2>/dev/null | awk '/^Mem:/{print "mem="$2" "$7}'
df -PB1 / 2>/dev/null | awk 'NR==2{print "disk="$2" "$3}'
if [ -f /var/run/reboot-required ]; then echo "reboot=1"; else echo "reboot=0"; fi
if command -v apt-get >/dev/null 2>&1; then echo "updates=$(apt-get -s -o Debug::NoLocking=1 upgrade 2>/dev/null | grep -c '^Inst ')"; fi
if command -v docker >/dev/null 2>&1; then echo "docker=$(docker ps -q 2>/dev/null | wc -l) $(docker ps -aq 2>/dev/null | wc -l)"; fi
if command -v pveversion >/dev/null 2>&1; then echo "pve=$(pveversion 2>/dev/null | cut -d/ -f2)"; fi
if command -v nginx >/dev/null 2>&1; then echo "nginx=$(systemctl is-active nginx 2>/dev/null)"; fi
"""


def parse_status(text: str) -> dict:
    raw = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            raw[k.strip()] = v.strip()

    def ints(value: str) -> list[int]:
        try:
            return [int(float(x)) for x in value.split()]
        except ValueError:
            return []

    status: dict = {"host": raw.get("host", ""), "os": raw.get("os", ""), "kernel": raw.get("kernel", "")}
    status["uptime_s"] = (ints(raw.get("uptime", "")) or [0])[0]
    try:
        status["load"] = [float(x) for x in raw.get("load", "").split()]
    except ValueError:
        status["load"] = []
    status["cpus"] = (ints(raw.get("cpus", "")) or [0])[0]
    mem = ints(raw.get("mem", ""))
    if len(mem) == 2 and mem[0]:
        status["mem_total"], status["mem_used"] = mem[0], mem[0] - mem[1]
    disk = ints(raw.get("disk", ""))
    if len(disk) == 2 and disk[0]:
        status["disk_total"], status["disk_used"] = disk
    status["reboot"] = raw.get("reboot") == "1"
    if "updates" in raw:
        status["updates"] = (ints(raw["updates"]) or [0])[0]
    if "docker" in raw:
        d = ints(raw["docker"])
        if len(d) == 2:
            status["docker_running"], status["docker_total"] = d
    for key in ("pve", "nginx"):
        if raw.get(key):
            status[key] = raw[key]
    return status


def fetch_status(host: dict, password: str | None, key_path: str | None) -> dict:
    client = connect(host, password, key_path)
    try:
        code, out, err = run(client, STATUS_SCRIPT)
    finally:
        client.close()
    status = parse_status(out)
    if not status.get("host") and code != 0:
        raise RuntimeError(err.strip() or f"exit {code}")
    return status


# --------------------------------------------------------------------- SSH key
def ensure_key() -> tuple[Path, str]:
    """~/.ssh/studyhub_ed25519 (+ .pub), created once. Returns (private path, public key line)."""
    pub = KEY_PATH.with_suffix(".pub")
    if KEY_PATH.exists() and pub.exists():
        return KEY_PATH, pub.read_text(encoding="ascii").strip()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                serialization.NoEncryption())
    line = private.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                             serialization.PublicFormat.OpenSSH).decode()
    line += f" studyhub@{socket.gethostname()}"
    KEY_PATH.write_bytes(pem)
    pub.write_text(line + "\n", encoding="ascii")
    _only_me(KEY_PATH)
    return KEY_PATH, line


def _only_me(path: Path) -> None:
    """Windows' OpenSSH refuses private keys that other users can read."""
    user = os.environ.get("USERNAME", "")
    if user:
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def install_key(host: dict, password: str) -> str:
    """Adds our public key to the server's authorized_keys, then proves the key works. Returns the key path."""
    path, line = ensure_key()
    client = connect(host, password=password)
    try:
        quoted = shlex.quote(line)
        code, _, err = run(client, "umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
                                   f"(grep -qxF {quoted} ~/.ssh/authorized_keys || echo {quoted} >> ~/.ssh/authorized_keys)"
                                   " && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys")
    finally:
        client.close()
    if code != 0:
        raise RuntimeError(err.strip() or f"exit {code}")
    connect(host, key_path=str(path)).close()
    return str(path)


# ------------------------------------------------------------- terminal / RDP
def ssh_args(host: dict) -> list[str]:
    args = ["ssh", "-p", str(int(host.get("ssh_port") or 22))]
    if host.get("auth") == "key" and host.get("key_path"):
        args += ["-i", host["key_path"]]
    args.append(f"{host['username']}@{host['address']}" if host.get("username") else host["address"])
    return args


def command_text(host: dict) -> str:
    """The same command, to paste into any terminal."""
    return " ".join(f'"{a}"' if " " in a else a for a in ssh_args(host))


def terminal_command(host: dict, remote: str = "") -> list[str]:
    """remote: a command to run on the server instead of a plain shell (e.g. a recorded `script` session)."""
    ssh = ssh_args(host) + (["-t", remote] if remote else [])
    wt = shutil.which("wt") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "wt.exe")
    if os.path.exists(wt):
        return [wt, "new-tab", "--title", host["name"], *ssh]
    return ["cmd.exe", "/k", *ssh]


def open_terminal(host: dict, remote: str = "") -> None:
    cmd = terminal_command(host, remote)
    flags = 0 if cmd[0].lower().endswith("wt.exe") else subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen(cmd, creationflags=flags, close_fds=True)


def open_rdp(host: dict) -> None:
    subprocess.Popen(["mstsc.exe", f"/v:{host['address']}"], close_fds=True)


# ------------------------------------------------------------------ commands (library, nginx)
MAX_OUTPUT = 400_000


def run_command(host: dict, password: str | None, key_path: str | None, command: str, sudo: bool = False,
                timeout: int = 120) -> dict:
    """Runs one shell command; with sudo the password (from the vault) goes to `sudo -S` on stdin, never on the
    command line. Returns {"code", "out"} (stdout + stderr, capped)."""
    client = connect(host, password, key_path)
    try:
        wrapped = f"bash -lc {shlex.quote(command)}"
        if sudo:
            wrapped = (f"sudo -S -p '' {wrapped}" if password else f"sudo -n {wrapped}")
        channel = client.get_transport().open_session(timeout=timeout)
        channel.set_combine_stderr(True)  # one stream, in the order things happened
        channel.settimeout(timeout)
        channel.exec_command(wrapped)
        if sudo and password:
            channel.sendall((password + "\n").encode())
        channel.shutdown_write()
        chunks, size = [], 0
        while True:
            data = channel.recv(65536)
            if not data:
                break
            if size < MAX_OUTPUT:
                chunks.append(data)
            size += len(data)
        code = channel.recv_exit_status()
        out = b"".join(chunks).decode("utf-8", "replace")
        if size > MAX_OUTPUT:
            out += f"\n… ({size - MAX_OUTPUT} more bytes)"
        if sudo and not password and code != 0 and "a password is required" in out:
            out += "\n(sudo needs a password: this host signs in with a key and has no password in the vault)"
        return {"code": code, "out": out}
    finally:
        client.close()
