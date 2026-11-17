"""
Lab report generator — the window and the service (see app/core/lab.py for the session and the report).

  LabService    start / add / finish; the rest of Study Hub hands it events while a lab is recording
                (command library and nginx results, Proxmox actions, phone photos and notes)
  LabWindow     the recording window: a console (this laptop's PowerShell, or any server over SSH) whose
                commands and outputs go into the report, screenshot, quick note, recorded interactive SSH,
                Finish
"""
from __future__ import annotations

import shlex
import tempfile
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget)

from app.core import lab
from app.core.i18n import language, tr
from app.core.logs import log
from app.ui.dialogs.base import PromptDialog, StyledDialog
from app.ui.dialogs.study_dialogs import field
from app.ui.theme import Icons, Palette, app_icon
from app.ui.widgets.common import IconButton, button, label, primary_button

CWD_MARK = "__SH_CWD__"


def mono(pt: float = 9.8) -> QFont:
    f = QFont()
    f.setFamilies(["Cascadia Mono", "Consolas", "Courier New"])
    f.setPointSizeF(pt)
    return f


class LabStartDialog(StyledDialog):
    def __init__(self, ctl, parent=None):
        super().__init__(tr("lab_start"), parent, 560)
        self.body.addWidget(label(tr("lab_start_hint"), 9, role="Dim", wrap=True))
        self.body.addWidget(field(tr("lab_title")))
        self.title = QLineEdit(tr("lab_default_title", date=datetime.now().strftime("%Y-%m-%d")))
        self.body.addWidget(self.title)
        row = QHBoxLayout()
        col1, col2 = QVBoxLayout(), QVBoxLayout()
        col1.addWidget(field(tr("lab_course")))
        self.subject = QComboBox()
        self.subject.addItem("—", None)
        for s in ctl.db.list_subjects():
            self.subject.addItem(s["name"], s["id"])
        col1.addWidget(self.subject)
        col2.addWidget(field(tr("lab_student")))
        self.student = QLineEdit(ctl.db.get_setting("lab_student", ""))
        col2.addWidget(self.student)
        row.addLayout(col1, 1)
        row.addLayout(col2, 1)
        self.body.addLayout(row)
        self.body.addWidget(field(tr("lab_objective")))
        self.objective = QPlainTextEdit()
        self.objective.setPlaceholderText(tr("lab_objective_ph"))
        self.objective.setFixedHeight(90)
        self.body.addWidget(self.objective)
        self.clips = QCheckBox(tr("lab_with_clipboard"))
        self.body.addWidget(self.clips)
        self.add_buttons(tr("lab_record"))

    def validate(self) -> bool:
        if not self.title.text().strip():
            self.title.setFocus()
            return False
        return True


class LabService(QObject):
    changed = pyqtSignal()

    def __init__(self, ctl):
        super().__init__(ctl)
        self.ctl = ctl
        self.session: lab.LabSession | None = None
        self.window: LabWindow | None = None
        self._pending = 0
        self._steps: list[dict] = []

    @property
    def active(self) -> bool:
        return self.session is not None

    # --------------------------------------------------------------- lifecycle
    def resume(self) -> None:
        """A lab that was recording when Study Hub closed keeps going."""
        s = lab.LabSession.resume()
        if s:
            self.session = s
            self.show_window()
            self.ctl.notify(tr("lab_resumed", title=s.data["title"]), "info")

    def start(self, parent=None) -> None:
        if self.session:
            self.show_window()
            return
        dlg = LabStartDialog(self.ctl, parent)
        from app.ui import apps as app_registry
        dlg.setWindowIcon(app_registry.find(self.ctl, "lab").icon())
        if dlg.exec() != StyledDialog.DialogCode.Accepted:
            return
        self.ctl.db.set_setting("lab_student", dlg.student.text().strip())
        subject = dlg.subject.currentText() if dlg.subject.currentData() else ""
        self.session = lab.LabSession.start(dlg.title.text().strip(), subject, dlg.objective.toPlainText().strip(),
                                            dlg.student.text().strip(), dlg.clips.isChecked())
        self.session.data["subject_id"] = dlg.subject.currentData()
        self.session.save()
        self.show_window()
        self.changed.emit()

    def show_window(self) -> None:
        if self.window is None:
            self.window = LabWindow(self)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.ctl.apps.changed.emit()  # its button on the shell's taskbar

    def add(self, kind: str, **fields) -> None:
        if self.session:
            self.session.add(kind, **fields)
            if self.window:
                self.window.event_added(kind, fields)

    def add_file(self, kind: str, path: str, **fields) -> None:
        if self.session and self.session.add_file(kind, path, **fields) and self.window:
            self.window.event_added(kind, fields)

    # ------------------------------------------------------------------ finish
    def finish(self, parent=None) -> None:
        s = self.session
        if not s:
            return
        term = getattr(self.ctl.apps, "terminal_window", None)
        if term is not None:
            term.flush_lab()  # the terminal's last command
        s.stop()
        self._steps = []
        infra = getattr(self.ctl, "infra", None)
        sessions = s.data.get("ssh", [])
        self._pending = len(sessions) if infra else 0
        if not self._pending:
            self._build()
            return
        self.ctl.notify(tr("lab_collecting"), "info")
        for i, entry in enumerate(sessions):
            remote = entry["remote"]

            def done(result, entry=entry, i=i):
                if not isinstance(result, Exception) and result.get("code") == 0:
                    for k, step in enumerate(lab.parse_script_log(result["out"])):
                        self._steps.append({"t": entry["t"] + 0.001 * (k + 1), "kind": "ssh_cmd",
                                            "host": entry["host"], **step})
                self._pending -= 1
                if self._pending <= 0:
                    self._build()
            infra.run_on_host(entry["host_id"], f"cat ~/{shlex.quote(remote)} 2>/dev/null; rm -f ~/{shlex.quote(remote)}",
                              False, done, parent, "lab log")

    def _build(self) -> None:
        s = self.session
        if s is None:
            return
        d = s.data
        steps = list(self._steps)
        for e in d["events"]:
            if e["kind"] in ("ps", "ssh_cmd", "library", "shot", "phone_file", "phone_note", "note", "terminal"):
                steps.append(e)
        start, end = d["started"], d.get("ended") or time.time()
        fmt = lambda ts: datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
        infra = getattr(self.ctl, "infra", None)
        hosts_used: dict[int, str] = {}
        if infra:
            for row in self.ctl.db.query("SELECT * FROM infra_log WHERE created_at >= ? AND created_at <= ? ORDER BY id",
                                         (fmt(start), fmt(end))):
                if row["host_id"]:
                    hosts_used[row["host_id"]] = ""
                if row["action"] in ("command", "terminal", "status", "trust_key", "trust_cert"):
                    continue
                t = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S").timestamp()
                steps.append({"t": t, "kind": "infra", "ok": bool(row["ok"]),
                              "text": f"{tr('infra_act_' + row['action'])}: {row['detail']}"})
        if d.get("clipboard"):
            for c in self.ctl.db.query("SELECT * FROM clipboard_items WHERE created_at >= ? AND created_at <= ? "
                                       "AND kind IN ('text', 'link') ORDER BY created_at", (fmt(start), fmt(end))):
                t = datetime.strptime(c["created_at"], "%Y-%m-%d %H:%M:%S").timestamp()
                steps.append({"t": t, "kind": "clip", "text": c["content"]})
        steps.sort(key=lambda x: x["t"])
        lang = language()
        env = [lab.laptop_env(lang)]
        if infra:
            import json as _json
            ids = {e.get("host_id") for e in d["events"] if e.get("host_id")} | set(hosts_used)
            for hid in ids:
                host = infra.store.host(hid)
                if host:
                    try:
                        st = _json.loads(host["last_status"] or "{}")
                    except ValueError:
                        st = {}
                    env.append(f"{host['name']} ({host['address']}): {st.get('os') or host['kind']}")
        # the report note, its pictures in the note's attachments folder
        from app.ui.widgets.note_editor import _md_path, attachment_dir
        note_id = self.ctl.create_note(subject_id=d.get("subject_id"), title=d["title"], content="")
        folder = attachment_dir(note_id)
        folder.mkdir(parents=True, exist_ok=True)

        def image_path(name: str) -> str:
            src = s.folder / name
            dest = folder / name
            if src.is_file():
                dest.write_bytes(src.read_bytes())
            return _md_path(dest)
        text = lab.build_markdown(s, steps, env, lang, image_path)
        self.ctl.autosave_note(note_id, d["title"], text)
        log.info("lab report %s: %d steps -> note %s", s.id, len(steps), note_id)
        self.session = None
        if self.window:
            self.window.close_runner()
            self.window.hide()
            self.window.deleteLater()
            self.window = None
            self.ctl.apps.changed.emit()
        self.changed.emit()
        self.ctl.notify(tr("lab_done"), "success")
        self.ctl.recents.add("lab", "note", note_id, d["title"])
        self.ctl.open_notebook(note_id)
        self.last_note_id = note_id


class LabWindow(QWidget):
    """The recording window (stays on top, can be small)."""

    def __init__(self, service: LabService):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.service = service
        self.ctl = service.ctl
        self.setWindowTitle(tr("lab_window_title"))
        from app.ui import apps as app_registry
        self._info = app_registry.find(self.ctl, "lab")
        self.setWindowIcon(self._info.icon())
        self._identity_set = False
        self.resize(760, 520)
        self.setObjectName("LabWindow")
        self.setStyleSheet(f"QWidget#LabWindow {{ background: {Palette.bg}; }}")
        from app.core.lab_console import PowerShellRunner
        self.runner = PowerShellRunner(str(Path.home()))
        self.ssh_cwd: dict[int, str] = {}
        self.history: list[str] = []
        self.hist_pos = 0
        self.running = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        top = QHBoxLayout()
        self.rec = label("", 10.5, QFont.Weight.DemiBold)
        self.rec.setStyleSheet(f"color: {Palette.danger};")
        top.addWidget(self.rec)
        self.title = label(self.service.session.data["title"], 10, role="Dim")
        top.addWidget(self.title, 1)
        for glyph, tip, slot in ((Icons.PREVIEW, tr("lab_shot"), self.capture),
                                 (Icons.EDIT, tr("lab_note"), self.quick_note),
                                 (Icons.KEYBOARD, tr("lab_ssh_recorded"), self.recorded_ssh)):
            b = IconButton(glyph, tip, 34, 10)
            b.clicked.connect(slot)
            top.addWidget(b)
        self.pin = IconButton(Icons.PIN, tr("lab_on_top"), 34, 10)
        self.pin.setCheckable(True)
        self.pin.setChecked(True)
        self.pin.toggled.connect(self._on_top)
        top.addWidget(self.pin)
        finish = primary_button(tr("lab_finish"), Icons.ACCEPT)
        finish.clicked.connect(lambda: self.service.finish(self))
        top.addWidget(finish)
        lay.addLayout(top)

        trow = QHBoxLayout()
        trow.addWidget(label(tr("lab_run_on"), 9, role="Dim"))
        self.target = QComboBox()
        self.target.addItem("💻  " + tr("lab_this_laptop"), None)
        infra = getattr(self.ctl, "infra", None)
        if infra:
            for h in infra.store.hosts():
                if h["kind"] != "windows":
                    self.target.addItem(f"🖧  {h['name']}  ·  {h['address']}", h["id"])
        self.target.currentIndexChanged.connect(self._update_prompt)
        trow.addWidget(self.target, 1)
        self.count = label("", 9, role="Faint")
        trow.addWidget(self.count)
        lay.addLayout(trow)

        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setFont(mono())
        self.out.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.out.setPlaceholderText(tr("lab_console_ph"))
        self.out.setMaximumBlockCount(5000)
        lay.addWidget(self.out, 1)

        irow = QHBoxLayout()
        self.prompt = label("", 9.5)
        self.prompt.setFont(mono(9.5))
        irow.addWidget(self.prompt)
        self.input = QLineEdit()
        self.input.setFont(mono())
        self.input.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.input.setPlaceholderText(tr("lab_input_ph"))
        self.input.returnPressed.connect(self.run_command)
        self.input.installEventFilter(self)
        irow.addWidget(self.input, 1)
        self.run_btn = button(tr("cmd_run"), Icons.PLAY)
        self.run_btn.clicked.connect(self.run_command)
        irow.addWidget(self.run_btn)
        self.stop_btn = button(tr("lab_stop"), Icons.CLOSE, role="Danger")
        self.stop_btn.clicked.connect(self.stop_command)
        self.stop_btn.setEnabled(False)
        irow.addWidget(self.stop_btn)
        lay.addLayout(irow)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.out.clear)

        self._line_sig.connect(self.out.appendPlainText)
        self._done_sig.connect(self._ps_finished)
        self.timer = QTimer(self, interval=1000, timeout=self._tick)
        self.timer.start()
        self._tick()
        self._update_prompt()
        self._update_count()

    # ----------------------------------------------------------------- display
    def _tick(self) -> None:
        s = self.service.session
        if not s:
            return
        sec = int(time.time() - s.started)
        self.rec.setText(f"●  REC  {sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
                         if sec >= 3600 else f"●  REC  {sec // 60:02d}:{sec % 60:02d}")

    def _update_count(self) -> None:
        s = self.service.session
        if s:
            n = len([e for e in s.data["events"] if e["kind"] not in ("terminal",)])
            self.count.setText(tr("lab_events", n=n))

    def event_added(self, kind: str, fields: dict) -> None:
        self._update_count()
        if kind in ("library", "phone_file", "phone_note", "shot", "note"):
            what = {"library": "⚙", "phone_file": "📱", "phone_note": "📱", "shot": "📸", "note": "✎"}[kind]
            text = fields.get("name") or fields.get("caption") or fields.get("text") or fields.get("file", "")
            self.out.appendPlainText(f"[{datetime.now():%H:%M:%S}] {what} {str(text)[:120]}")

    def _update_prompt(self, *_):
        hid = self.target.currentData()
        if hid is None:
            self.prompt.setText(f"PS {self.runner.location or str(Path.home())}>")
        else:
            host = self.ctl.infra.store.host(hid)
            self.prompt.setText(f"{host['username'] or 'user'}@{host['name']}:{self.ssh_cwd.get(hid, '~')}$")

    def _on_top(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == event.Type.KeyPress and self.history:
            if event.key() == Qt.Key.Key_Up:
                self.hist_pos = max(0, self.hist_pos - 1)
                self.input.setText(self.history[self.hist_pos])
                return True
            if event.key() == Qt.Key.Key_Down:
                self.hist_pos = min(len(self.history), self.hist_pos + 1)
                self.input.setText(self.history[self.hist_pos] if self.hist_pos < len(self.history) else "")
                return True
        return super().eventFilter(obj, event)

    # ----------------------------------------------------------------- running
    def _busy(self, on: bool) -> None:
        self.running = on
        self.run_btn.setEnabled(not on)
        self.stop_btn.setEnabled(on and self.target.currentData() is None)
        self.input.setEnabled(not on)
        if not on:
            self.input.setFocus()

    def run_command(self) -> None:
        cmd = self.input.text().strip()
        if not cmd or self.running:
            return
        if cmd in ("cls", "clear"):
            self.out.clear()
            self.input.clear()
            return
        self.history.append(cmd)
        self.hist_pos = len(self.history)
        self.input.clear()
        self.out.appendPlainText(f"\n{self.prompt.text()} {cmd}")
        hid = self.target.currentData()
        self._busy(True)
        if hid is None:
            self._ps_cwd = self.runner.location or str(Path.home())  # the prompt shows where the command started
            self.runner.run(cmd, self._line, self._ps_done)
        else:
            self._ssh_run(hid, cmd)

    # the runner calls back from its thread: hop to the GUI thread
    _line_sig = pyqtSignal(str)
    _done_sig = pyqtSignal(dict)

    def _line(self, text: str) -> None:
        # same filtering as the report (lab_console.clean_output): hide "At line:1 char:5 / + …" wrapper noise
        import re
        if re.match(r"^At line:\d+ char:\d+\s*$", text):
            self._skip = 2
            return
        if getattr(self, "_skip", 0) and text.lstrip().startswith("+") and "CategoryInfo" not in text                 and "FullyQualifiedErrorId" not in text:
            self._skip -= 1
            return
        self._skip = 0
        self._line_sig.emit(text)

    def _ps_done(self, result: dict) -> None:
        self._done_sig.emit(result)

    def _ps_finished(self, result: dict) -> None:
        if result["code"]:
            self.out.appendPlainText(f"✗ exit {result['code']}")
        self.service.add("ps", cmd=result["cmd"], out=result["out"], code=result["code"],
                         cwd=getattr(self, "_ps_cwd", result["cwd"]))
        self._busy(False)
        self._update_prompt()

    def stop_command(self) -> None:
        if self.target.currentData() is None:
            self.runner.stop()

    def _ssh_run(self, hid: int, cmd: str) -> None:
        sudo = cmd.startswith("sudo ")
        body = cmd[5:] if sudo else cmd
        cwd = self.ssh_cwd.get(hid, "")
        script = (f"cd {shlex.quote(cwd)} 2>/dev/null; " if cwd else "") + body + \
            f"; __rc=$?; printf '\\n{CWD_MARK}%s' \"$(pwd)\"; exit $__rc"
        host = self.ctl.infra.store.host(hid)

        def done(result):
            try:
                self.isVisible()
            except RuntimeError:
                return
            if isinstance(result, Exception):
                from app.plugins.infra.controller import error_text
                self.out.appendPlainText(f"✗ {error_text(result)}")
                self._busy(False)
                return
            out = result["out"]
            if CWD_MARK in out:
                out, _, where = out.rpartition(CWD_MARK)
                self.ssh_cwd[hid] = where.strip() or cwd
                out = out.rstrip("\n")
            if out:
                self.out.appendPlainText(out)
            if result["code"]:
                self.out.appendPlainText(f"✗ exit {result['code']}")
            self.service.add("ssh_cmd", host=host["name"], host_id=hid, cmd=cmd, out=out, code=result["code"])
            self._busy(False)
            self._update_prompt()
        self.ctl.infra.run_on_host(hid, script, sudo, done, self, f"lab: {cmd[:60]}")

    # ------------------------------------------------------------- extras
    def capture(self) -> None:
        from app.ui.screenshot import SelectOverlay
        self.hide()

        def picked(pixmap):
            self.show()
            path = Path(tempfile.gettempdir()) / f"studyhub-lab-{int(time.time())}.png"
            pixmap.save(str(path), "PNG")
            caption = PromptDialog.ask(tr("lab_shot"), "", tr("lab_caption_ph"), self) or ""
            self.service.add_file("shot", str(path), caption=caption.strip())
            path.unlink(missing_ok=True)

        def start():
            self._overlay = SelectOverlay()
            self._overlay.selected.connect(picked)
            self._overlay.destroyed.connect(lambda *_: self.show())
            self._overlay.show()
            self._overlay.activateWindow()
        QTimer.singleShot(250, start)  # let this window disappear first

    def quick_note(self) -> None:
        text = PromptDialog.ask(tr("lab_note"), "", tr("lab_note_ph"), self)
        if text and text.strip():
            self.service.add("note", text=text.strip())

    def recorded_ssh(self) -> None:
        hid = self.target.currentData()
        infra = getattr(self.ctl, "infra", None)
        if hid is None or not infra:
            self.ctl.notify(tr("lab_pick_server"), "warning")
            return
        host = infra.store.host(hid)
        remote = self.service.session.new_ssh(host)
        infra.open_terminal(hid, self, remote=f"mkdir -p ~/.studyhub && script -q -f ~/{remote}")
        self.out.appendPlainText(f"[{datetime.now():%H:%M:%S}] 🔐 {tr('lab_ssh_started', name=host['name'])}")

    def close_runner(self) -> None:
        self.runner.close()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._identity_set:  # its own Windows taskbar button, like any Study Hub app
            self._identity_set = True
            from app.ui.app_window import give_identity
            give_identity(self, self._info)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (e.Type.ActivationChange, e.Type.WindowStateChange):
            self.ctl.apps.changed.emit()

    def closeEvent(self, e):
        # closing the window doesn't end the lab: it keeps recording (tray › Lab report brings it back)
        self.ctl.notify(tr("lab_still_recording"), "info")
        super().closeEvent(e)
