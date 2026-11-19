"""
What's playing on the laptop (Windows' media sessions: browsers, Spotify, players...) and at which second.
Used by "Open on phone" to continue a YouTube video at the same moment. Read through PowerShell / WinRT
(GlobalSystemMediaTransportControlsSessionManager), so no extra Python packages are needed.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

log = logging.getLogger(__name__)
CREATE_NO_WINDOW = 0x08000000
BROWSERS = ("chrome", "msedge", "firefox", "brave", "opera", "vivaldi")

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $null = $t.Wait(4000); $t.Result }
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$out = @()
foreach ($s in $mgr.GetSessions()) {
  $tl = $s.GetTimelineProperties(); $pb = $s.GetPlaybackInfo()
  $props = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
  $pos = $tl.Position.TotalSeconds
  $playing = [string]$pb.PlaybackStatus -eq 'Playing'
  if ($playing) { $pos += ([DateTimeOffset]::Now - $tl.LastUpdatedTime).TotalSeconds }
  $out += [pscustomobject]@{ app = $s.SourceAppUserModelId; title = $props.Title; artist = $props.Artist;
    position = [math]::Round($pos, 1); length = [math]::Round($tl.EndTime.TotalSeconds, 1); playing = $playing }
}
ConvertTo-Json -InputObject @($out) -Compress
"""


def sessions(timeout: int = 12) -> list[dict]:
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
                           capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
        data = json.loads(r.stdout.strip() or "[]")
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        log.info("media sessions: %s", exc)
        return []
    return [d for d in (data if isinstance(data, list) else [data]) if isinstance(d, dict)]


def is_youtube(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("youtu.be", "youtube.com", "m.youtube.com", "www.youtube.com", "music.youtube.com")


def with_time(url: str, seconds: int) -> str:
    """The same YouTube link starting at this second (replaces any t= it had)."""
    parts = urlparse(url)
    q = {k: v for k, v in parse_qs(parts.query, keep_blank_values=True).items() if k != "t"}
    q["t"] = [f"{max(0, int(seconds))}s"]
    return urlunparse(parts._replace(query=urlencode(q, doseq=True)))


def youtube_position(found: list[dict] | None = None) -> int | None:
    """Where the video playing in a browser is: only when exactly one browser session is playing or paused
    with a real position (otherwise we can't tell which video the link is)."""
    found = sessions() if found is None else found
    browser = [s for s in found if any(b in str(s.get("app", "")).lower() for b in BROWSERS)
               and (s.get("length") or 0) > 0 and (s.get("position") or 0) > 1]
    if len(browser) != 1:
        return None
    pos = int(browser[0]["position"])
    return pos if re.match(r"\d+$", str(pos)) else None
