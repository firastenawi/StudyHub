"""
Text from images with Windows' own OCR engine (Windows.Media.Ocr) — offline, no extra packages.

Runs a tiny PowerShell script (WinRT is reachable from Windows PowerShell 5.1). Arabic and English
are tried and the result that fits better is kept. Languages come from Windows: Settings › Language ›
add a language with "Optical character recognition".
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_SCRIPT = r"""
param([string]$Path, [string]$Langs)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$out = @{}
foreach ($tag in $Langs.Split(',')) {
  $lang = [Windows.Globalization.Language]::new($tag)
  if (-not [Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($lang)) { continue }
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
  $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  $out[$tag] = (($result.Lines | ForEach-Object { $_.Text }) -join "`n")
}
$stream.Dispose()
$out | ConvertTo-Json -Compress
"""

_ARABIC = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")


class OcrError(Exception):
    pass


def available_languages() -> list[str]:
    script = ("Add-Type -AssemblyName System.Runtime.WindowsRuntime;"
              "$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime];"
              "[Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                             text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def recognize(image_path: str, langs: tuple[str, ...] = ("ar", "en")) -> str:
    """Text found in the image (PNG/JPG). Picks the language whose result looks right."""
    script = Path(image_path).with_suffix(".ps1")
    script.write_text(_SCRIPT, encoding="utf-8-sig")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-File", str(script), "-Path", str(Path(image_path).resolve()), "-Langs", ",".join(langs)],
                             capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        script.unlink(missing_ok=True)
    text = out.stdout.decode("utf-8", "replace").strip()
    if out.returncode != 0 or not text.startswith("{"):
        raise OcrError((out.stderr.decode("utf-8", "replace").strip().splitlines() or ["OCR failed"])[-1])
    results = {k: v or "" for k, v in json.loads(text).items()}
    return choose(results)


def logical_order(line: str) -> str:
    """Windows OCR returns Arabic lines in visual (left-to-right) word order. Reverse the words,
    but keep runs of Latin words / numbers ("docker compose up") in their own order."""
    if not _ARABIC.search(line):
        return line
    groups: list[list[str]] = []
    for word in line.split():
        latin = not _ARABIC.search(word)
        if groups and latin and not _ARABIC.search(groups[-1][-1]):
            groups[-1].append(word)
        else:
            groups.append([word])
    return " ".join(" ".join(g) for g in reversed(groups))


def choose(results: dict[str, str]) -> str:
    """Merges both engines line by line: the Arabic engine reads Arabic (and usually Latin too),
    the English engine reads Latin text more accurately — so Latin lines take the English reading."""
    from difflib import SequenceMatcher
    ar = [line for line in results.get("ar", "").splitlines() if line.strip()]
    en = [line for line in results.get("en", "").splitlines() if line.strip()]
    if not ar or not any(_ARABIC.search(line) for line in ar):
        return "\n".join(en or ar).strip()
    used: set[int] = set()
    merged = []
    for line in ar:
        if not _ARABIC.search(line) and en:
            best = max(range(len(en)), key=lambda i: SequenceMatcher(None, line, en[i]).ratio())
            if SequenceMatcher(None, line, en[best]).ratio() > 0.6:
                used.add(best)
                line = en[best]
        merged.append(logical_order(line))
    for i, line in enumerate(en):  # Latin lines only the English engine found
        if i not in used and all(SequenceMatcher(None, line, m).ratio() < 0.6 for m in merged):
            merged.append(line)
    return "\n".join(merged).strip()
