"""
Self-test for a build (mainly the PyInstaller exe): `StudyHub.exe --self-test report.json`.

Boots the real app, then checks the parts that packaging tends to break — Qt WebEngine,
QtPdf, QtMultimedia, DPAPI, SQLite FTS5 — grabs a screenshot of the window, writes a JSON
report and quits. Run it with its own APPDATA / STUDYHUB_INSTANCE / STUDYHUB_NO_SHELL so it
never touches the real data, the running app or Explorer.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import traceback
from pathlib import Path


def run(report_path: str) -> int:
    report: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "checks": {}}
    out = Path(report_path or "selftest-report.json").resolve()

    def check(name: str, fn) -> None:
        try:
            report["checks"][name] = {"ok": True, "detail": fn()}
        except Exception as exc:
            report["checks"][name] = {"ok": False, "detail": f"{exc!r}", "trace": traceback.format_exc()}

    from app import config
    report.update(version=config.VERSION, frozen=bool(config.IS_FROZEN), base_dir=str(config.BASE_DIR),
                  data_dir=str(config.DATA_DIR), launch_command=config.launch_command())

    from app.core.logs import setup_logging
    setup_logging()
    from app.application import StudyHubApp
    hub = StudyHubApp()
    report["checks"]["boot"] = {"ok": True, "detail": "app started"}

    from PyQt6.QtCore import QTimer, QUrl, QEventLoop

    def wait_for(signal, ms: int):
        loop = QEventLoop()
        result = []
        signal.connect(lambda *a: (result.append(a), loop.quit()))
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        return result

    def web():
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        view = QWebEngineView()
        view.resize(300, 200)
        view.setHtml("<h1>ok</h1>")
        got = wait_for(view.loadFinished, 15000)
        view.deleteLater()
        if not (got and got[0][0]):
            raise RuntimeError("page did not load")
        return "QWebEngineView loaded a page"

    def pdf():
        from PyQt6.QtGui import QPainter, QPdfWriter
        from PyQt6.QtPdf import QPdfDocument
        path = config.DATA_DIR / "selftest.pdf"
        writer = QPdfWriter(str(path))
        painter = QPainter(writer)
        painter.drawText(200, 200, "Study Hub self-test")
        painter.end()
        doc = QPdfDocument(None)
        doc.load(str(path))
        pages = doc.pageCount()
        image = doc.render(0, doc.pagePointSize(0).toSize()) if pages else None
        doc.close()
        if pages != 1 or image is None or image.isNull():
            raise RuntimeError(f"pages={pages}")
        return "wrote, loaded and rendered a PDF"

    def sound():
        from PyQt6.QtMultimedia import QMediaPlayer
        from app.core.sounds import builtin_path
        player = hub.sound.player if hasattr(hub, "sound") else QMediaPlayer()
        player.setSource(QUrl.fromLocalFile(str(builtin_path("gentle"))))
        deadline = time.monotonic() + 10
        while player.mediaStatus() in (QMediaPlayer.MediaStatus.LoadingMedia, QMediaPlayer.MediaStatus.NoMedia) \
                and time.monotonic() < deadline:
            hub.qt.processEvents()
            time.sleep(0.05)
        status = player.mediaStatus().name
        if player.error() != QMediaPlayer.Error.NoError or status not in ("LoadedMedia", "BufferedMedia"):
            raise RuntimeError(f"status={status} error={player.errorString()}")
        return f"WAV loaded ({status})"

    def dpapi():
        from app.core.vault import derive_key, protect, unprotect
        key = derive_key("self-test", b"0123456789abcdef", iterations=1000)
        if unprotect(protect(b"secret", key), key) != b"secret":
            raise RuntimeError("roundtrip mismatch")
        return "protect/unprotect roundtrip"

    def fts5():
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        con.close()
        return f"sqlite {sqlite3.sqlite_version} with FTS5"

    def phone_link():
        import http.client
        import qrcode
        qrcode.QRCode().add_data("x")
        hub.db.set_setting("phone_link_folder", str(config.DATA_DIR / "received"))
        hub.db.set_setting("phone_link_port", 18765)
        if not hub.set_phone_link(True, quiet=True):
            raise RuntimeError("server did not start")
        try:
            con = http.client.HTTPConnection("127.0.0.1", hub.phone.port, timeout=5)
            con.request("POST", "/api/pair", body=json.dumps({"code": hub.phone.code, "name": "self-test"}),
                        headers={"Content-Type": "application/json"})
            token = json.loads(con.getresponse().read())["token"]
            con.request("GET", "/api/ping", headers={"X-Device": token})
            status = con.getresponse().status
            con.close()
            hub.qt.processEvents()
            hub.db.delete_phone_device()
        finally:
            hub.set_phone_link(False, quiet=True)
        if status != 200:
            raise RuntimeError(f"ping status {status}")
        return f"server answered on port {hub.phone.port}"

    def notes_preview():
        from app.ui.widgets.note_preview import VENDOR, NotePreview
        for f in ("katex/katex.min.js", "katex/katex.min.css", "mermaid/mermaid.min.js"):
            if not (VENDOR / f).is_file():
                raise RuntimeError(f"missing {f}")
        view = NotePreview()
        view.resize(700, 600)
        view.show()
        view.set_note("t", "$x^2$\n\n```python\nprint(1)\n```\n\n```mermaid\nflowchart LR\n  A-->B\n```")
        deadline = time.monotonic() + 15
        counts = None
        while time.monotonic() < deadline:
            hub.qt.processEvents()
            time.sleep(0.1)
            box = {}
            view.web.page().runJavaScript(
                "[document.querySelectorAll('.katex').length, document.querySelectorAll('.mermaid-wrap svg').length,"
                " document.querySelectorAll('.code [class^=tok-]').length]", lambda v: box.setdefault("v", v))
            for _ in range(20):
                hub.qt.processEvents()
                time.sleep(0.02)
            counts = box.get("v")
            if counts and all(counts):
                break
        view.deleteLater()
        if not counts or not all(counts):
            raise RuntimeError(f"katex/mermaid/code = {counts}")
        return f"math, diagram and colored code rendered {counts}"

    def plugins():
        loaded = {pid: info.loaded for pid, info in hub.plugins.plugins.items()}
        errors = {pid: info.error.splitlines()[-1] for pid, info in hub.plugins.plugins.items() if info.error}
        if errors or not all(loaded.values()):
            raise RuntimeError(f"{loaded} {errors}")
        import paramiko  # noqa: F401 (SSH for the infrastructure plugin)
        return f"loaded: {', '.join(loaded)}"

    def terminal():
        """The terminal app: ConPTY helpers packaged, a real program runs, xterm.js + QtWebChannel present."""
        from PyQt6 import QtWebChannel  # noqa: F401
        from winpty import PtyProcess
        from app.ui.terminal_window import ASSETS
        for f in ("term.html", "xterm.js", "xterm.css", "addon-fit.js"):
            if not (ASSETS / f).is_file():
                raise RuntimeError(f"missing {f}")
        p = PtyProcess.spawn("cmd.exe /c echo studyhub-pty-ok", dimensions=(24, 80))
        got, deadline = "", time.monotonic() + 10
        while time.monotonic() < deadline and "studyhub-pty-ok" not in got:
            try:
                got += p.read(4096)
            except EOFError:
                break
        try:
            if p.isalive():
                p.terminate(force=True)
        except OSError:  # it ended on its own meanwhile
            pass
        if "studyhub-pty-ok" not in got:
            raise RuntimeError(f"no output from the pty: {got[-200:]!r}")
        return "ConPTY runs programs; xterm.js assets present"

    def window():
        hub.window.showNormal()
        hub.window.resize(1280, 800)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            hub.qt.processEvents()
            time.sleep(0.05)
        shot = out.with_suffix(".png")
        hub.window.grab().save(str(shot))
        return str(shot)

    def finish():
        for name, fn in (("webengine", web), ("pdf", pdf), ("sound", sound), ("dpapi", dpapi),
                         ("fts5", fts5), ("phone_link", phone_link), ("notes_preview", notes_preview), ("plugins", plugins), ("terminal", terminal),
                         ("window", window)):
            if name not in os.environ.get("STUDYHUB_SELFTEST_SKIP", "").split(","):
                check(name, fn)
        report["ok"] = all(c["ok"] for c in report["checks"].values())
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        hub.quit()

    QTimer.singleShot(3000, finish)
    hub.run()
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else ""))
