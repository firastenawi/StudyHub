"""
What a note contains, read from its Markdown (no Qt): #tags, [[links]], - [ ] tasks, plus the
built-in templates. Code (``` blocks and `inline`) is ignored, so "#include" or "a[[0]]" in code
never becomes a tag or a link.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

TAG_RE = re.compile(r"(?<![\w#&/\\\]\[])#([A-Za-z؀-ۿ_][\w؀-ۿ\-/]*)")
LINK_RE = re.compile(r"\[\[([^\[\]|#\n]+)(?:#[^\[\]|\n]*)?(?:\|([^\[\]\n]+))?\]\]")
TASK_RE = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+\[)([ xX])\]\s*(.*)$")
_INLINE_CODE = re.compile(r"`[^`\n]*`")


def _prose_lines(text: str) -> list[str]:
    """Lines with code blanked out (fenced blocks become empty lines, so line numbers stay)."""
    out, fence = [], False
    for line in (text or "").split("\n"):
        if line.strip().startswith(("```", "~~~")):
            fence = not fence
            out.append("")
            continue
        out.append("" if fence else _INLINE_CODE.sub("", line))
    return out


def tags(text: str) -> list[str]:
    found = []
    for line in _prose_lines(text):
        if re.match(r"^\s{0,3}#{1,6}\s", line):  # a heading's leading "# " is not a tag (its words still can be)
            line = re.sub(r"^\s{0,3}#{1,6}\s", "", line)
        for m in TAG_RE.finditer(line):
            tag = m.group(1).rstrip("-/").lower()
            if tag and not tag.isdigit() and not re.fullmatch(r"[0-9a-f]{3}|[0-9a-f]{6}", tag):
                found.append(tag)
    return sorted(set(found))


def links(text: str) -> list[str]:
    return sorted({m.group(1).strip().lower() for line in _prose_lines(text) for m in LINK_RE.finditer(line)
                   if m.group(1).strip()})


def tasks(text: str) -> list[tuple[int, bool, str]]:
    result = []
    for number, line in enumerate(_prose_lines(text)):
        m = TASK_RE.match(line)
        if m:
            result.append((number, m.group(2) in "xX", m.group(3).strip()))
    return result


def rename_links(text: str, old: str, new: str) -> str:
    """[[Old]] / [[old|alias]] / [[Old#part]] -> [[New…]] (case-insensitive)."""
    pattern = re.compile(r"\[\[\s*" + re.escape(old.strip()) + r"\s*(?=[\]|#])", re.IGNORECASE)
    return pattern.sub("[[" + new.strip(), text)


# ------------------------------------------------------------------ templates
BUILTIN_TEMPLATES = {
    "lecture": {
        "en": ("Lecture — {{date}}", "**Subject:** \n**Lecturer:** \n\n## Key ideas\n- \n\n## Details\n\n\n"
                                      "## Questions\n- [ ] \n\n## Summary\n> [!TIP]\n> \n\n#lecture"),
        "ar": ("محاضرة — {{date}}", "**المادة:** \n**الدكتور:** \n\n## الأفكار الرئيسية\n- \n\n## التفاصيل\n\n\n"
                                     "## أسئلة\n- [ ] \n\n## الخلاصة\n> [!TIP]\n> \n\n#محاضرة"),
    },
    "lab": {
        "en": ("Lab report — {{date}}", "## Goal\n\n\n## Setup\n| Item | Value |\n|---|---|\n|  |  |\n\n"
                                         "## Steps\n1. \n\n## Commands\n```bash\n\n```\n\n## Results\n\n\n"
                                         "## Conclusion\n\n#lab"),
        "ar": ("تقرير مخبر — {{date}}", "## الهدف\n\n\n## التجهيز\n| العنصر | القيمة |\n|---|---|\n|  |  |\n\n"
                                          "## الخطوات\n1. \n\n## الأوامر\n```bash\n\n```\n\n## النتائج\n\n\n"
                                          "## الاستنتاج\n\n#مخبر"),
    },
    "meeting": {
        "en": ("Meeting — {{date}}", "**Time:** {{time}}\n**People:** \n\n## Agenda\n- \n\n## Notes\n\n\n"
                                      "## Decisions\n- \n\n## Action items\n- [ ] \n\n#meeting"),
        "ar": ("اجتماع — {{date}}", "**الوقت:** {{time}}\n**الحضور:** \n\n## جدول الأعمال\n- \n\n## ملاحظات\n\n\n"
                                     "## القرارات\n- \n\n## المهام\n- [ ] \n\n#اجتماع"),
    },
    "daily": {
        "en": ("{{date}}", "## Plan for today\n- [ ] \n\n## Notes\n\n\n## Done\n- \n\n"
                            "[[{{yesterday}}]] · [[{{tomorrow}}]]\n#daily"),
        "ar": ("{{date}}", "## خطة اليوم\n- [ ] \n\n## ملاحظات\n\n\n## إنجازات\n- \n\n"
                            "[[{{yesterday}}]] · [[{{tomorrow}}]]\n#يومي"),
    },
    "project": {
        "en": ("Project — ", "## Goal\n\n\n## Scope\n- \n\n## Milestones\n- [ ] \n\n## Architecture\n```mermaid\n"
                             "flowchart LR\n    User --> App --> DB\n```\n\n## Risks\n> [!WARNING]\n> \n\n## Links\n- \n\n#project"),
        "ar": ("مشروع — ", "## الهدف\n\n\n## النطاق\n- \n\n## المراحل\n- [ ] \n\n## البنية\n```mermaid\n"
                           "flowchart LR\n    User --> App --> DB\n```\n\n## المخاطر\n> [!WARNING]\n> \n\n## روابط\n- \n\n#مشروع"),
    },
    "bug": {
        "en": ("Bug — ", "**Where:** \n**Severity:** \n\n## Steps to reproduce\n1. \n\n## Expected\n\n\n## Actual\n\n\n"
                         "## Logs\n```text\n\n```\n\n## Fix\n- [ ] \n\n#bug"),
        "ar": ("مشكلة — ", "**وين:** \n**الخطورة:** \n\n## خطوات إعادة المشكلة\n1. \n\n## المتوقع\n\n\n## الفعلي\n\n\n"
                           "## السجلات\n```text\n\n```\n\n## الحل\n- [ ] \n\n#مشكلة"),
    },
    "weekly": {
        "en": ("Week review — {{date}}", "## Wins\n- \n\n## What didn't go well\n- \n\n## Next week\n- [ ] \n\n#weekly"),
        "ar": ("مراجعة أسبوع — {{date}}", "## إنجازات\n- \n\n## شو ما زبط\n- \n\n## الأسبوع الجاي\n- [ ] \n\n#أسبوعي"),
    },
}


def fill(text: str, title: str = "", day: date | None = None) -> str:
    day = day or date.today()
    values = {"date": day.isoformat(), "time": datetime.now().strftime("%H:%M"), "title": title,
              "yesterday": (day - timedelta(days=1)).isoformat(), "tomorrow": (day + timedelta(days=1)).isoformat()}
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), text)


def builtin(key: str, lang: str, day: date | None = None) -> tuple[str, str]:
    title, body = BUILTIN_TEMPLATES[key]["ar" if lang == "ar" else "en"]
    return fill(title, day=day), fill(body, day=day)
