"""
The lab console's laptop side: one PowerShell that stays open (cd / variables carry over between commands),
fed one command at a time; every command's output, success and time are known exactly — no transcript to
parse, and the output of programs like ipconfig / ping / tracert is included (a PowerShell 5.1 transcript
would miss it). No Qt here: output arrives through callbacks from a reader thread.

Commands travel base64-encoded (so Arabic / any text arrives intact whatever the console code page is) and
run dot-sourced, so they behave as if typed at the prompt.
"""
from __future__ import annotations

import base64
import queue
import re
import subprocess
import threading
import time

NO_WINDOW = 0x08000000
MARK = "__SH_DONE__"
_WRAPPER_NOISE = re.compile(r"^At line:\d+ char:\d+\s*$")


def clean_output(text: str) -> str:
    """Drops PowerShell's 'At line:1 char:5 / + …' lines that point at our wrapper, not at the user's command."""
    out, skip = [], 0
    for line in text.replace("\r\n", "\n").split("\n"):
        if _WRAPPER_NOISE.match(line):
            skip = 2
            continue
        if skip and line.lstrip().startswith("+") and "CategoryInfo" not in line and "FullyQualifiedErrorId" not in line:
            skip -= 1
            continue
        skip = 0
        out.append(line)
    return "\n".join(out).strip("\n")


class PowerShellRunner:
    def __init__(self, cwd: str | None = None):
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue = queue.Queue()
        self.busy = False
        self.location = cwd or ""

    def _start(self) -> None:
        self.proc = subprocess.Popen(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=self.cwd,
            creationflags=NO_WINDOW)
        self.lines = queue.Queue()  # a fresh queue: nothing left over from a stopped shell
        threading.Thread(target=self._read, args=(self.proc, self.lines), daemon=True).start()
        self._send("[Console]::OutputEncoding = [Text.Encoding]::UTF8; $OutputEncoding = [Text.Encoding]::UTF8; "
                   "$ProgressPreference = 'SilentlyContinue'")

    @staticmethod
    def _read(proc: subprocess.Popen, lines: queue.Queue) -> None:
        for raw in iter(proc.stdout.readline, b""):
            lines.put(raw.decode("utf-8", "replace").rstrip("\r\n"))
        lines.put(None)  # the process ended

    def _send(self, text: str) -> None:
        self.proc.stdin.write((text + "\n").encode("ascii", "replace"))
        self.proc.stdin.flush()

    def run(self, command: str, on_line, on_done, timeout: float = 600) -> None:
        """Runs one command in a thread: on_line(text) for each output line, on_done(result dict) at the end."""
        if self.busy:
            raise RuntimeError("busy")
        self.busy = True

        def work():
            try:
                result = self._run(command, on_line, timeout)
            except Exception as exc:  # never leave the console stuck
                result = {"cmd": command, "out": str(exc), "code": -1, "t": time.time(), "cwd": self.location,
                          "seconds": 0}
            self.busy = False
            on_done(result)
        threading.Thread(target=work, name="lab-ps", daemon=True).start()

    def _run(self, command: str, on_line, timeout: float) -> dict:
        started = time.time()
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        encoded = base64.b64encode(command.encode("utf-8")).decode()
        self._send("$global:LASTEXITCODE = 0; $__o = @(); "
                   f"$__c = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')); "
                   ". ([ScriptBlock]::Create($__c)) 2>&1 | Tee-Object -Variable __o | Out-String -Width 200 -Stream; "
                   "$__err = @($__o | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] }).Count; "
                   f"\"{MARK}$global:LASTEXITCODE`t$__err`t$((Get-Location).Path)\"")
        out: list[str] = []
        code, errors = 0, 0
        lines = self.lines
        while True:
            try:
                line = lines.get(timeout=max(1.0, timeout - (time.time() - started)))
            except queue.Empty:
                self.stop()
                out.append("… (timed out, stopped)")
                code = -1
                break
            if line is None:
                code = code or -1
                break
            if line.startswith(MARK):
                parts = line[len(MARK):].split("\t")
                try:
                    code, errors = int(parts[0] or 0), int(parts[1] or 0)
                except (ValueError, IndexError):
                    pass
                if len(parts) > 2:
                    self.location = parts[2]
                break
            out.append(line)
            on_line(line)
        return {"cmd": command, "out": clean_output("\n".join(out)), "code": code if code else (1 if errors else 0),
                "t": started, "cwd": self.location, "seconds": round(time.time() - started, 1)}

    def stop(self) -> None:
        """Ends the running command (and the shell; the next command starts a fresh one in the same folder)."""
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
        self.cwd = self.location or self.cwd

    def close(self) -> None:
        self.stop()
