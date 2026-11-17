"""
Commands tab: a library of shell commands (built-in + your own) run on one or several servers at once,
with each server's output and exit code. Commands marked "ask first" (reboot, prune…) need a confirmation;
sudo gets its password from the vault through stdin.
"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QCheckBox, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                             QScrollArea, QSplitter, QVBoxLayout, QWidget)

from app.core.i18n import tr
from app.ui.dialogs.base import ConfirmDialog, StyledDialog
from app.ui.dialogs.study_dialogs import field
from app.ui.theme import Icons
from app.ui.widgets.common import Card, IconButton, add_action, button, label, make_menu, primary_button


def mono() -> QFont:
    f = QFont()
    f.setFamilies(["Cascadia Mono", "Consolas", "Courier New"])
    f.setPointSizeF(9.5)
    return f


def command_name(cmd: dict) -> str:
    return tr("infra_cmd_" + cmd["builtin"]) if cmd["builtin"] else cmd["name"]


class CommandDialog(StyledDialog):
    def __init__(self, cmd: dict | None, parent=None):
        super().__init__(tr("cmd_edit") if cmd and cmd.get("id") else tr("cmd_new"), parent, 600)
        c = cmd or {}
        self.body.addWidget(field(tr("infra_name")))
        self.name = QLineEdit(command_name(c) if c.get("builtin") else c.get("name", ""))
        self.body.addWidget(self.name)
        self.body.addWidget(field(tr("cmd_command")))
        self.command = QPlainTextEdit(c.get("command", ""))
        self.command.setFont(mono())
        self.command.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.command.setPlaceholderText("systemctl status nginx --no-pager")
        self.command.setMinimumHeight(140)
        self.body.addWidget(self.command)
        self.sudo = QCheckBox(tr("cmd_sudo"))
        self.sudo.setChecked(bool(c.get("sudo")))
        self.body.addWidget(self.sudo)
        self.confirm = QCheckBox(tr("cmd_confirm"))
        self.confirm.setChecked(bool(c.get("confirm")))
        self.body.addWidget(self.confirm)
        self.add_buttons(tr("save"))

    def validate(self) -> bool:
        if not self.name.text().strip():
            self.name.setFocus()
            return False
        if not self.command.toPlainText().strip():
            self.command.setFocus()
            return False
        return True

    def data(self) -> dict:
        return {"name": self.name.text().strip(), "command": self.command.toPlainText().strip(),
                "sudo": int(self.sudo.isChecked()), "confirm": int(self.confirm.isChecked())}


class CommandsView(QWidget):
    def __init__(self, infra, page):
        super().__init__()
        self.infra, self.page = infra, page
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        # ---- left: the library
        left = Card(padding=14)
        left.setFixedWidth(330)
        head = QHBoxLayout()
        head.addWidget(label(tr("cmd_library"), 11, QFont.Weight.DemiBold))
        head.addStretch(1)
        new = IconButton(Icons.ADD, tr("cmd_new"), 32, 10)
        new.clicked.connect(lambda: self.edit(None))
        head.addWidget(new)
        left.root.addLayout(head)
        self.list = QListWidget()
        self.list.setObjectName("CleanList")
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemDoubleClicked.connect(lambda _: self.run())
        left.root.addWidget(self.list, 1)
        lay.addWidget(left)

        # ---- right: what, where, output
        right = QVBoxLayout()
        right.setSpacing(10)
        self.preview = QLineEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono())
        self.preview.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        right.addWidget(self.preview)
        hosts_row = QHBoxLayout()
        hosts_row.addWidget(label(tr("cmd_on"), 9.5, role="Dim"))
        self.host_box = QWidget()
        self.host_lay = QHBoxLayout(self.host_box)
        self.host_lay.setContentsMargins(0, 0, 0, 0)
        self.host_lay.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(42)
        scroll.setWidget(self.host_box)
        hosts_row.addWidget(scroll, 1)
        self.run_btn = primary_button(tr("cmd_run"), Icons.PLAY)
        self.run_btn.clicked.connect(self.run)
        hosts_row.addWidget(self.run_btn)
        right.addLayout(hosts_row)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono())
        self.output.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.output.setPlaceholderText(tr("cmd_output_ph"))
        right.addWidget(self.output, 1)
        clear = button(tr("cmd_clear"), Icons.DELETE, role="Ghost")
        clear.clicked.connect(self.output.clear)
        r2 = QHBoxLayout()
        r2.addStretch(1)
        r2.addWidget(clear)
        right.addLayout(r2)
        lay.addLayout(right, 1)
        self.checks: dict[int, QCheckBox] = {}

    # ------------------------------------------------------------- filling
    def fill(self) -> None:
        keep = self.current()["id"] if self.current() else None
        self.list.clear()
        for c in self.infra.store.commands():
            marks = ("  🔒" if c["sudo"] else "") + ("  ⚠" if c["confirm"] else "")
            it = QListWidgetItem(command_name(c) + marks)
            it.setData(Qt.ItemDataRole.UserRole, c)
            it.setToolTip(c["command"])
            self.list.addItem(it)
            if c["id"] == keep:
                self.list.setCurrentItem(it)
        if not self.list.currentItem() and self.list.count():
            self.list.setCurrentRow(0)
        checked = {hid for hid, cb in self.checks.items() if cb.isChecked()}
        while self.host_lay.count():
            w = self.host_lay.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        self.checks = {}
        for h in self.infra.store.hosts():
            if h["kind"] == "windows":
                continue
            cb = QCheckBox(h["name"])
            cb.setChecked(h["id"] in checked)
            self.checks[h["id"]] = cb
            self.host_lay.addWidget(cb)
        if not self.checks:
            self.host_lay.addWidget(label(tr("cmd_no_hosts"), 9, role="Faint"))
        self.host_lay.addStretch(1)

    def current(self) -> dict | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _selected(self, *_):
        c = self.current()
        self.preview.setText(("sudo " if c and c["sudo"] else "") + (c["command"].replace("\n", " ⏎ ") if c else ""))

    # ------------------------------------------------------------- editing
    def _menu(self, pos) -> None:
        it = self.list.itemAt(pos)
        c = it.data(Qt.ItemDataRole.UserRole) if it else None
        if not c:
            return
        menu = make_menu(self)
        add_action(menu, Icons.PLAY, tr("cmd_run"), self.run)
        if c["builtin"]:
            add_action(menu, Icons.COPY, tr("cmd_duplicate"), lambda: self.edit({**c, "id": None, "builtin": ""}))
        else:
            add_action(menu, Icons.EDIT, tr("edit"), lambda: self.edit(c))
            add_action(menu, Icons.DELETE, tr("delete"), lambda: self.delete(c), danger=True)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def edit(self, cmd: dict | None) -> None:
        dlg = CommandDialog(cmd, self)
        if dlg.exec() == StyledDialog.DialogCode.Accepted:
            self.infra.store.save_command(cmd.get("id") if cmd else None, **dlg.data())
            self.fill()

    def delete(self, cmd: dict) -> None:
        if ConfirmDialog.ask(tr("delete"), tr("cmd_delete_msg", name=command_name(cmd)), tr("delete"), self):
            self.infra.store.delete_command(cmd["id"])
            self.fill()

    # ------------------------------------------------------------- running
    def run(self) -> None:
        cmd = self.current()
        hosts = [hid for hid, cb in self.checks.items() if cb.isChecked()]
        if not cmd:
            return
        if not hosts:
            self.infra.api.notify(tr("cmd_pick_hosts"), "warning")
            return
        names = ", ".join(self.infra.store.host(h)["name"] for h in hosts if self.infra.store.host(h))
        if cmd["confirm"] and not ConfirmDialog.ask(command_name(cmd), tr("cmd_confirm_msg", name=command_name(cmd),
                                                                            hosts=names), tr("cmd_run"), self):
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.output.appendPlainText(f"\n━━━━ {stamp}  {command_name(cmd)}  →  {names}")
        for hid in hosts:
            host = self.infra.store.host(hid)
            self.infra.run_on_host(hid, cmd["command"], bool(cmd["sudo"]),
                                   lambda r, h=host: self._result(h, r), self, command_name(cmd))

    def _result(self, host: dict, result) -> None:
        try:
            self.isVisible()
        except RuntimeError:
            return
        if isinstance(result, Exception):
            from app.plugins.infra.controller import error_text
            self.output.appendPlainText(f"\n▌ {host['name']}  ✗  {error_text(result)}")
            return
        mark = "✓" if result["code"] == 0 else f"✗ exit {result['code']}"
        self.output.appendPlainText(f"\n▌ {host['name']}  {mark}\n{result['out'].rstrip()}")
        lab = getattr(self.infra.ctl, "lab", None)
        cmd = self.current()
        if lab and lab.active and cmd:  # during a lab the result goes into the report
            lab.add("library", name=command_name(cmd), host=host["name"], host_id=host["id"], cmd=cmd["command"],
                    out=result["out"].rstrip(), code=result["code"])
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())
