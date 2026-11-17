"""
Translate selected text (PDF reader, screenshot OCR). Online: Google Translate's public endpoint,
MyMemory as a fallback. The text you translate is sent to that service — nothing else is.

Direction "auto": Arabic text -> English, anything else -> Arabic (or the target chosen in Settings).
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

_ARABIC = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")
_cache: dict[tuple[str, str], tuple[str, str]] = {}
MAX_CHUNK = 1800


class TranslateError(Exception):
    pass


def auto_target(text: str, preferred: str = "ar") -> str:
    arabic = len(_ARABIC.findall(text))
    latin = len(_LATIN.findall(text))
    if arabic > latin:
        return "en" if preferred == "ar" else preferred
    return preferred


def _chunks(text: str) -> list[str]:
    parts, current = [], ""
    for piece in re.split(r"(?<=[.!?؟\n])\s+", text):
        if len(current) + len(piece) + 1 > MAX_CHUNK and current:
            parts.append(current)
            current = ""
        current = f"{current} {piece}".strip() if current else piece
    if current:
        parts.append(current)
    return parts or [text]


def _google(text: str, target: str) -> tuple[str, str]:
    url = ("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&dt=t&tl="
           + urllib.parse.quote(target) + "&q=" + urllib.parse.quote(text))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 StudyHub"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    out = "".join(seg[0] for seg in data[0] if seg and seg[0])
    return out, data[2] if len(data) > 2 and isinstance(data[2], str) else ""


def _mymemory(text: str, target: str) -> tuple[str, str]:
    source = "ar" if _ARABIC.search(text) else "en"
    if source == target:
        source = "en" if target == "ar" else "ar"
    url = ("https://api.mymemory.translated.net/get?q=" + urllib.parse.quote(text[:480])
           + f"&langpair={source}|{target}")
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    if int(data.get("responseStatus", 0)) != 200:
        raise TranslateError(str(data.get("responseDetails", "error")))
    return data["responseData"]["translatedText"], source


def translate(text: str, target: str = "auto", preferred: str = "ar") -> tuple[str, str, str]:
    """Returns (translation, source language, target language)."""
    text = " ".join(text.split()) if "\n" not in text.strip() else re.sub(r"[ \t]+", " ", text.strip())
    if not text:
        return "", "", target
    if target == "auto":
        target = auto_target(text, preferred)
    key = (text, target)
    if key in _cache:
        out, source = _cache[key]
        return out, source, target
    pieces, source = [], ""
    try:
        for chunk in _chunks(text):
            out, source = _google(chunk, target)
            pieces.append(out)
    except Exception as first:
        try:
            pieces, source = [], ""
            for chunk in _chunks(text):
                out, source = _mymemory(chunk, target)
                pieces.append(out)
        except Exception:
            raise TranslateError(str(getattr(first, "reason", first)) or "offline") from first
    result = " ".join(pieces).strip()
    if len(_cache) > 300:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (result, source)
    return result, source, target


def web_url(text: str, target: str) -> str:
    return f"https://translate.google.com/?sl=auto&tl={target}&text={urllib.parse.quote(text[:4000])}&op=translate"
