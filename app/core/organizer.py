"""
Downloads organizer: suggests which subject folder each downloaded file belongs to.

Nothing is ever moved automatically — the UI shows suggestions and the user confirms.

How a suggestion is scored (per subject):
  * learned rules  : words from files you moved before (strongest, grows with every move)
  * subject code   : e.g. "MATH201" appears in the file name
  * subject name   : words of the subject name appear in the file name
Arabic text is normalized (أ/إ/آ -> ا, ة -> ه, ى -> ي, no diacritics) so
"محاضرة_التحليل.pdf" matches the subject "تحليل 2".
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime

# browsers' temporary files while a download is still in progress
PARTIAL_EXTENSIONS = {".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload"}
STOPWORDS = {"pdf", "docx", "doc", "pptx", "ppt", "xlsx", "zip", "rar", "png", "jpg", "jpeg", "copy", "final",
             "new", "file", "download", "the", "and", "for", "lecture", "lec", "chapter", "part", "week",
             "محاضره", "محاضرات", "الفصل", "فصل", "ملف", "جديد", "نسخه", "الجزء", "جزء", "اسبوع"}
_DIACRITICS = re.compile(r"[ؗ-ًؚ-ْـ]")


def normalize(text: str) -> str:
    text = _DIACRITICS.sub("", text.lower())
    return (text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
            .replace("ة", "ه").replace("ى", "ي"))


def tokens(text: str) -> list[str]:
    """Meaningful words of a file / subject name (len >= 3, no stopwords, no 'ال' prefix)."""
    words = re.split(r"[^\w]+|_", normalize(text))
    result = []
    for w in words:
        if w.startswith("ال") and len(w) > 4:
            w = w[2:]  # الفيزياء -> فيزياء
        if len(w) >= 3 and w not in STOPWORDS and not w.isdigit():
            result.append(w)
    return list(dict.fromkeys(result))  # unique, keep order


@dataclass
class Suggestion:
    path: str
    name: str
    size: int
    mtime: float
    subject_id: int | None = None
    folder: str | None = None
    score: int = 0
    reason: str = ""          # e.g. "code:MATH201", "name:تحليل", "learned:calc"
    alternatives: list[tuple[int, str]] = field(default_factory=list)


def candidate_files(folder: str, ignored: set[str]) -> list[dict]:
    """Finished downloads in `folder` (files only, newest first)."""
    result = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in PARTIAL_EXTENSIONS or entry.name.startswith(("~$", ".")) or \
                        entry.name.lower() == "desktop.ini" or entry.path.lower() in ignored:
                    continue
                st = entry.stat()
                result.append({"path": entry.path, "name": entry.name, "size": st.st_size, "mtime": st.st_mtime})
    except OSError:
        return []
    result.sort(key=lambda f: f["mtime"], reverse=True)
    return result


def suggest(files: list[dict], subjects: list[dict], destinations: dict[int, list[str]],
            rules: list[dict]) -> list[Suggestion]:
    """subjects: rows with id/name/code; destinations: subject_id -> folders; rules: token/subject_id/hits."""
    learned: dict[str, list[tuple[int, int]]] = {}
    for r in rules:
        learned.setdefault(r["token"], []).append((r["subject_id"], r["hits"]))
    profiles = {s["id"]: (tokens(s["name"]), normalize(s.get("code") or "").replace(" ", "")) for s in subjects}

    out = []
    for f in files:
        name_tokens = tokens(os.path.splitext(f["name"])[0])
        flat_name = normalize(f["name"]).replace(" ", "").replace("_", "").replace("-", "")
        scores: dict[int, tuple[int, str]] = {}
        for sid, (subject_tokens, code) in profiles.items():
            if not destinations.get(sid):
                continue  # nowhere to move it
            score, reason = 0, ""
            if code and len(code) >= 3 and code in flat_name:
                score, reason = score + 5, f"code:{code.upper()}"
            for t in subject_tokens:
                if any(t in nt or nt in t for nt in name_tokens if len(nt) >= 3):
                    score += 3
                    reason = reason or f"name:{t}"
            for t in name_tokens:
                for learned_sid, hits in learned.get(t, []):
                    if learned_sid == sid:
                        score += 2 + min(hits, 4)
                        reason = reason or f"learned:{t}"
            if score:
                scores[sid] = (score, reason)
        ranked = sorted(scores.items(), key=lambda kv: kv[1][0], reverse=True)
        s = Suggestion(f["path"], f["name"], f["size"], f["mtime"])
        if ranked and ranked[0][1][0] >= 3:
            sid, (score, reason) = ranked[0]
            s.subject_id, s.folder, s.score, s.reason = sid, destinations[sid][0], score, reason
        s.alternatives = [(sid, destinations[sid][0]) for sid, _ in ranked[1:4]]
        out.append(s)
    return out


def age_days(mtime: float) -> int:
    return max(0, (datetime.now() - datetime.fromtimestamp(mtime)).days)
