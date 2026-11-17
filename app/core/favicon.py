"""
Downloads a website's real logo when you add it to Study Hub.

Order of attempts (first one that gives a usable image wins):
  1. the site's own page: <link rel="apple-touch-icon"> / <link rel="icon" sizes=...>, largest first
  2. https://site/favicon.ico
  3. DuckDuckGo's public icon service (only the domain name is sent)
Logos are cached per domain in %APPDATA%\\StudyHub\\icons, so each site is fetched once.
Everything is asynchronous (QNetworkAccessManager), up to 3 sites at a time: the UI never waits.
"""
from __future__ import annotations

import hashlib
import re
from collections import deque
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

_LINK_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([a-zA-Z-]+)\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)')
_USER_AGENT = b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
MAX_BYTES = 600_000
PARALLEL = 3


def icon_candidates(html: str, base: QUrl) -> list[str]:
    """Icon URLs declared by a page, best first (big colored PNGs before SVGs and tiny icons)."""
    found = []
    for tag in _LINK_RE.findall(html[:MAX_BYTES]):
        attrs = {k.lower(): v.strip("'\"") for k, v in _ATTR_RE.findall(tag)}
        rel = attrs.get("rel", "").lower()
        # "mask-icon" is a one-color Safari silhouette: never what we want
        if "icon" not in rel or "mask" in rel or not attrs.get("href"):
            continue
        size = 0
        for s in attrs.get("sizes", "").split():
            m = re.match(r"(\d+)x\d+", s)
            if m:
                size = max(size, int(m.group(1)))
        if "apple-touch-icon" in rel:
            size = max(size, 180)
        if attrs["href"].lower().split("?")[0].endswith(".svg") or "svg" in attrs.get("type", ""):
            size = max(size, 64)
        found.append((size, base.resolved(QUrl(attrs["href"])).toString()))
    found.sort(key=lambda x: x[0], reverse=True)
    return [url for _, url in found]


class FaviconFetcher(QObject):
    fetched = pyqtSignal(int, str)   # item id, saved icon path
    failed = pyqtSignal(int)

    def __init__(self, folder: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.nam = QNetworkAccessManager(self)
        self.nam.setTransferTimeout(6000)
        self._queue: deque[tuple[int, str]] = deque()
        self._active = 0

    def cached_path(self, url: str) -> Path:
        host = QUrl(url).host().lower().removeprefix("www.")
        return self.folder / (hashlib.md5(host.encode()).hexdigest() + ".png")

    def fetch(self, item_id: int, url: str) -> None:
        cached = self.cached_path(url)
        if cached.exists():
            self.fetched.emit(item_id, str(cached))
            return
        self._queue.append((item_id, url))
        self._next()

    # ------------------------------------------------------------ internals
    def _get(self, url: str, callback) -> None:
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", _USER_AGENT)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self.nam.get(request)
        reply.finished.connect(lambda: callback(reply))

    def _next(self) -> None:
        while self._active < PARALLEL and self._queue:
            item_id, url = self._queue.popleft()
            cached = self.cached_path(url)
            if cached.exists():  # another item of the same site finished meanwhile
                self.fetched.emit(item_id, str(cached))
                continue
            self._active += 1
            page = QUrl(url)
            job = {"item_id": item_id, "url": url, "candidates": [],
                   "fallbacks": [f"{page.scheme() or 'https'}://{page.host()}/favicon.ico",
                                 f"https://icons.duckduckgo.com/ip3/{page.host()}.ico"]}
            self._get(url, lambda reply, job=job: self._on_page(job, reply))

    def _on_page(self, job: dict, reply: QNetworkReply) -> None:
        if reply.error() == QNetworkReply.NetworkError.NoError:
            html = bytes(reply.read(MAX_BYTES) or b"").decode("utf-8", errors="ignore")
            job["candidates"] = icon_candidates(html, reply.url())
        reply.deleteLater()
        job["candidates"] += job.pop("fallbacks")
        self._try_next_candidate(job)

    def _try_next_candidate(self, job: dict) -> None:
        if not job["candidates"]:
            self.failed.emit(job["item_id"])
            self._done()
            return
        self._get(job["candidates"].pop(0), lambda reply: self._on_image(job, reply))

    def _on_image(self, job: dict, reply: QNetworkReply) -> None:
        image = QImage()
        if reply.error() == QNetworkReply.NetworkError.NoError:
            image.loadFromData(bytes(reply.read(MAX_BYTES) or b""))  # empty answer: read() gives None
        reply.deleteLater()
        if image.isNull() or image.width() < 16:
            self._try_next_candidate(job)
            return
        if image.width() > 128 or image.height() > 128:
            image = image.scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        path = self.cached_path(job["url"])
        image.save(str(path), "PNG")
        self.fetched.emit(job["item_id"], str(path))
        self._done()

    def _done(self) -> None:
        self._active -= 1
        self._next()
