"""
Lab report generator — the core (no Qt here).

A lab session collects, with times, everything you do for a lab: commands typed in a recorded PowerShell
(Start-Transcript, one header per command) or in a recorded SSH session (the server's `script`), command
library / nginx results, Proxmox actions, screenshots, quick notes, photos and notes from the phone, and —
if wanted — what you copied. At the end it becomes a report note: title, course, student, date, objective,
environment, the steps in order (commands with their output, pictures), observations, conclusion.

Sessions are written to %APPDATA%\\StudyHub\\lab\\<id>\\session.json after every event, so nothing is lost if
Study Hub closes during a lab.
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

from app.config import DATA_DIR

LAB_DIR = DATA_DIR / "lab"
MAX_OUT_LINES = 40
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[@-Z\\-_]|\x1b[()][0-9A-Za-z]")


class LabSession:
    def __init__(self, folder: Path, data: dict):
        self.folder = folder
        self.data = data

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def start(cls, title: str, subject: str, objective: str, student: str, with_clipboard: bool) -> "LabSession":
        sid = datetime.now().strftime("%Y%m%d-%H%M%S")
        folder = LAB_DIR / sid
        folder.mkdir(parents=True, exist_ok=True)
        s = cls(folder, {"id": sid, "title": title, "subject": subject, "objective": objective, "student": student,
                         "clipboard": with_clipboard, "started": time.time(), "ended": None, "events": [],
                         "transcripts": [], "ssh": []})
        s.save()
        return s

    @classmethod
    def resume(cls) -> "LabSession | None":
        """A session that was still running when Study Hub closed."""
        if not LAB_DIR.is_dir():
            return None
        for folder in sorted(LAB_DIR.iterdir(), reverse=True):
            try:
                data = json.loads((folder / "session.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            return cls(folder, data) if not data.get("ended") else None
        return None

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def started(self) -> float:
        return self.data["started"]

    def save(self) -> None:
        tmp = self.folder / "session.json.tmp"
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.folder / "session.json")

    def add(self, kind: str, **fields) -> None:
        self.data["events"].append({"t": time.time(), "kind": kind, **fields})
        self.save()

    def add_file(self, kind: str, src: str | Path, **fields) -> Path | None:
        """Copies a picture / file into the session folder (the original may move later)."""
        src = Path(src)
        if not src.is_file():
            return None
        dest = self.folder / f"{len(self.data['events']):03d}-{src.name}"
        shutil.copy2(src, dest)
        self.add(kind, file=dest.name, **fields)
        return dest

    def new_transcript(self) -> Path:
        path = self.folder / f"powershell-{len(self.data['transcripts']) + 1}.txt"
        self.data["transcripts"].append(path.name)
        self.add("terminal", shell="powershell", file=path.name)
        return path

    def new_ssh(self, host: dict) -> str:
        """Remote log name for a recorded SSH session on that host."""
        remote = f".studyhub/lab-{self.id}-{len(self.data['ssh']) + 1}.log"
        self.data["ssh"].append({"host_id": host["id"], "host": host["name"], "remote": remote, "t": time.time()})
        self.add("ssh_start", host=host["name"], host_id=host["id"], remote=remote)
        return remote

    def stop(self) -> None:
        self.data["ended"] = time.time()
        self.save()


# ------------------------------------------------------------------------ parsing logs
def parse_ps_transcript(text: str) -> list[dict]:
    """PowerShell transcript (-IncludeInvocationHeader) -> [{t, cwd, cmd, out}]."""
    steps, when, cur = [], None, None
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^Command start time: (\d{14})", line)
        if m:
            when = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").timestamp()
        elif line.startswith("**********************"):
            if cur:
                steps.append(cur)
                cur = None
        else:
            p = re.match(r"^PS ([^>]*)> ?(.*)$", line)
            if p and cur is None and when is not None:
                cur = {"t": when, "cwd": p.group(1), "cmd": p.group(2).strip(), "out": []}
            elif cur is not None:
                cur["out"].append(line)
        i += 1
    if cur:
        steps.append(cur)
    out = []
    for s in steps:
        body = "\n".join(s["out"]).strip("\n")
        if not s["cmd"] or s["cmd"].startswith(("Start-Transcript", "Stop-Transcript")):
            continue
        if body.startswith("Transcript started"):
            continue
        out.append({"t": s["t"], "cwd": s["cwd"], "cmd": s["cmd"], "out": body})
    return out


def _clean_terminal(text: str) -> str:
    text = ANSI.sub("", text)
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if "\r" in raw:  # carriage return: what is visible is the last overwrite
            raw = raw.split("\r")[-1]
        buf: list[str] = []
        for ch in raw:
            if ch == "\b":
                if buf:
                    buf.pop()
            elif ch == "\x07" or (ord(ch) < 32 and ch != "\t"):
                continue
            else:
                buf.append(ch)
        lines.append("".join(buf))
    return "\n".join(lines)


PROMPT = re.compile(r"^(?:\([^)]*\)\s*)?[\w.-]+@[\w.-]+(?::[^$#\n]*)?[$#] ?(.*)$")


def parse_script_log(text: str) -> list[dict]:
    """`script` log of an SSH session -> [{cmd, out}] (prompts like user@host:~$)."""
    text = _clean_terminal(text)
    steps, cur = [], None
    for line in text.split("\n"):
        if line.startswith(("Script started", "Script done")):
            continue
        m = PROMPT.match(line)
        if m:
            if cur and (cur["cmd"] or cur["out"]):
                steps.append(cur)
            cur = {"cmd": m.group(1).strip(), "out": []}
        elif cur is not None:
            cur["out"].append(line)
    if cur and (cur["cmd"] or cur["out"]):
        steps.append(cur)
    return [{"cmd": s["cmd"], "out": "\n".join(s["out"]).strip("\n")} for s in steps
            if s["cmd"] and s["cmd"] not in ("exit", "logout")]


def trim(out: str, n: int = MAX_OUT_LINES) -> str:
    lines = out.split("\n")
    if len(lines) <= n:
        return out
    keep = n // 2
    return "\n".join(lines[:keep] + [f"… ({len(lines) - n} lines) …"] + lines[-keep:])


# ------------------------------------------------------------------------ the report
LABELS = {
    "en": {"course": "Course", "student": "Student", "date": "Date", "duration": "Duration", "objective": "Objective",
           "env": "Environment", "steps": "Procedure", "observations": "Observations & results",
           "conclusion": "Conclusion", "conclusion_ph": "_Write the conclusion here…_", "laptop": "Laptop",
           "ps": "PowerShell", "ssh": "SSH session on", "shot": "Screenshot", "note": "Note", "phone": "From the phone",
           "library": "Command", "infra": "Infrastructure", "clips": "Copied during the lab", "min": "min",
           "no_output": "(no output)"},
    "ar": {"course": "المادة", "student": "الطالب", "date": "التاريخ", "duration": "المدة", "objective": "الهدف",
           "env": "بيئة العمل", "steps": "خطوات العمل", "observations": "الملاحظات والنتائج",
           "conclusion": "الخلاصة", "conclusion_ph": "_اكتب الخلاصة هون…_", "laptop": "اللابتوب",
           "ps": "PowerShell", "ssh": "جلسة SSH على", "shot": "لقطة شاشة", "note": "ملاحظة", "phone": "من الموبايل",
           "library": "أمر", "infra": "البنية التحتية", "clips": "نُسخ خلال المختبر", "min": "دقيقة",
           "no_output": "(بدون نتيجة)"},
}


def _fence(text: str) -> str:
    """A code fence longer than any backtick run inside."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _block(lang: str, prompt: str, cmd: str, out: str, no_output: str) -> str:
    body = f"{prompt}{cmd}\n{trim(out) if out.strip() else no_output}".rstrip()
    f = _fence(body)
    return f"{f}{lang}\n{body}\n{f}"


def build_markdown(session: LabSession, steps: list[dict], env: list[str], lang: str, image_path) -> str:
    """steps: [{t, kind, ...}] already merged and sorted. image_path(file) -> markdown path of the copied image."""
    L = LABELS.get(lang, LABELS["en"])
    d = session.data
    start = datetime.fromtimestamp(d["started"])
    minutes = max(1, round(((d.get("ended") or time.time()) - d["started"]) / 60))
    meta = [f"**{L['date']}:** {start:%Y-%m-%d %H:%M}", f"**{L['duration']}:** {minutes} {L['min']}"]
    if d.get("subject"):
        meta.insert(0, f"**{L['course']}:** {d['subject']}")
    if d.get("student"):
        meta.insert(1 if d.get("subject") else 0, f"**{L['student']}:** {d['student']}")
    out = [f"# {d['title']}", "", " · ".join(meta), ""]
    if d.get("objective"):
        out += [f"## {L['objective']}", "", d["objective"].strip(), ""]
    if env:
        out += [f"## {L['env']}", ""] + [f"- {e}" for e in env] + [""]
    out += [f"## {L['steps']}", ""]
    notes = []
    n = 0
    for s in steps:
        when = datetime.fromtimestamp(s["t"]).strftime("%H:%M:%S")
        kind = s["kind"]
        if kind == "note":
            notes.append(f"- **{when}** — {s['text']}")
            continue
        n += 1
        if kind == "ps":
            out += [f"### {n}. {L['ps']} · {when}", "", _block("powershell", f"PS {s.get('cwd', '')}> ", s["cmd"],
                                                                   s["out"], L["no_output"]), ""]
        elif kind == "terminal":  # what happened in Study Hub's terminal (one block per command)
            out += [f"### {n}. {L.get('terminal', 'Terminal')} · {s.get('host', '')} · {when}", "",
                    _fence(s.get("out", "")) + "text", trim(s.get("out", "")), _fence(s.get("out", "")), ""]
        elif kind == "ssh_cmd":
            out += [f"### {n}. {s['host']} · {when}", "", _block("bash", "$ ", s["cmd"], s["out"], L["no_output"]), ""]
        elif kind == "library":
            mark = "" if s.get("code", 0) == 0 else f" (exit {s.get('code')})"
            out += [f"### {n}. {L['library']}: {s.get('name', '')} — {s['host']} · {when}{mark}", "",
                    _block("bash", "$ ", s["cmd"], s["out"], L["no_output"]), ""]
        elif kind in ("shot", "phone_file"):
            title = L["shot"] if kind == "shot" else L["phone"]
            caption = s.get("caption", "")
            out += [f"### {n}. {title} · {when}", "", f"![{caption or title}]({image_path(s['file'])})", ""]
            if caption:
                out += [f"*{caption}*", ""]
        elif kind == "phone_note":
            out += [f"### {n}. {L['phone']} · {when}", "", s["text"].strip(), ""]
        elif kind == "infra":
            out += [f"### {n}. {L['infra']} · {when}", "", f"{'✅' if s.get('ok', True) else '⚠️'} {s['text']}", ""]
        elif kind == "clip":
            n -= 1
            notes.append(f"- **{when}** — `{s['text'][:200]}`")
    out += [f"## {L['observations']}", ""]
    out += notes or ["- "]
    out += ["", f"## {L['conclusion']}", "", L["conclusion_ph"], ""]
    return "\n".join(out)


def laptop_env(lang: str) -> str:
    L = LABELS.get(lang, LABELS["en"])
    return f"{L['laptop']}: {platform.system()} {platform.release()} ({platform.version()}), {platform.node()}"
