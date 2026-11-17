"""
Proxmox tab: nodes, VMs and containers of a Proxmox host, power actions, snapshots, console.
Talks to the Proxmox API through InfraController.pve_call (certificate pinning, secrets from the vault).
"""
from __future__ import annotations

import re
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                             QSpinBox, QVBoxLayout, QWidget)

from app.core.cleanup import human
from app.core.i18n import ltr, tr
from app.ui.dialogs.base import ConfirmDialog, StyledDialog
from app.ui.dialogs.study_dialogs import chip, field
from app.ui.theme import Icons, Palette
from app.ui.widgets.common import Card, EmptyState, IconButton, add_action, button, label, make_menu

SNAP_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")


def _uptime(sec) -> str:
    sec = int(sec or 0)
    if not sec:
        return "—"
    d, rem = divmod(sec, 86400)
    h, m = rem // 3600, rem % 3600 // 60
    return f"{d}d {h}h" if d else f"{h}h {m}m"


def _pct(x) -> str:
    return f"{float(x or 0) * 100:.0f}%"


class PveSettingsDialog(StyledDialog):
    def __init__(self, cfg: dict, host: dict, parent=None):
        super().__init__(tr("pve_connection"), parent, 540)
        self.body.addWidget(label(tr("pve_connection_hint", address=host["address"]), 9, role="Dim", wrap=True))
        row = QHBoxLayout()
        row.addWidget(field(tr("pve_port")))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(cfg["port"] or 8006))
        row.addWidget(self.port)
        row.addStretch(1)
        self.body.addLayout(row)
        self.body.addWidget(field(tr("infra_auth")))
        arow = QHBoxLayout()
        self.auth = QButtonGroup(self)
        for k in ("token", "password"):
            b = chip(tr("pve_auth_" + k))
            b.setProperty("key", k)
            b.setChecked(k == cfg["auth"])
            b.toggled.connect(lambda _: self._mode())
            self.auth.addButton(b)
            arow.addWidget(b)
        arow.addStretch(1)
        self.body.addLayout(arow)
        self.token_box = QWidget()
        tl = QVBoxLayout(self.token_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(field(tr("pve_token_id")))
        self.token_id = QLineEdit(cfg["token_id"])
        self.token_id.setPlaceholderText("root@pam!studyhub")
        self.token_id.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        tl.addWidget(self.token_id)
        tl.addWidget(field(tr("pve_secret")))
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setPlaceholderText(tr("infra_password_keep") if cfg["vault_id"] else "xxxxxxxx-xxxx-…")
        self.secret.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        tl.addWidget(self.secret)
        tl.addWidget(label(tr("pve_token_hint"), 8.6, role="Faint", wrap=True))
        self.body.addWidget(self.token_box)
        self.pw_box = QWidget()
        pl = QVBoxLayout(self.pw_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(field(tr("pve_realm")))
        self.realm = QComboBox()
        self.realm.setEditable(True)
        self.realm.addItems(["pam", "pve"])
        self.realm.setCurrentText(cfg["realm"] or "pam")
        pl.addWidget(self.realm)
        pl.addWidget(label(tr("pve_password_hint", user=host["username"] or "root"), 8.6, role="Faint", wrap=True))
        self.body.addWidget(self.pw_box)
        self.had_secret = bool(cfg["vault_id"])
        self.add_buttons(tr("save"))
        self._mode()

    def mode(self) -> str:
        b = self.auth.checkedButton()
        return b.property("key") if b else "token"

    def _mode(self) -> None:
        self.token_box.setVisible(self.mode() == "token")
        self.pw_box.setVisible(self.mode() == "password")

    def validate(self) -> bool:
        if self.mode() == "token":
            if not re.match(r"^[^@\s]+@[^!\s]+![A-Za-z0-9_-]+$", self.token_id.text().strip()):
                self.token_id.setFocus()
                return False
            if not self.secret.text() and not self.had_secret:
                self.secret.setFocus()
                return False
        return True


class SnapshotDialog(StyledDialog):
    def __init__(self, view, guest: dict, parent=None):
        super().__init__(tr("pve_snapshots_of", name=guest.get("name") or guest["vmid"]), parent, 560)
        self.view, self.guest = view, guest
        self.list = QListWidget()
        self.list.setMinimumHeight(220)
        self.body.addWidget(self.list)
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("pve_snap_name"))
        self.name.setText(f"sh_{datetime.now():%Y%m%d_%H%M}")
        self.name.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        row.addWidget(self.name, 1)
        self.desc = QLineEdit()
        self.desc.setPlaceholderText(tr("pve_snap_desc"))
        row.addWidget(self.desc, 2)
        make = button(tr("pve_snap_create"), Icons.ADD)
        make.clicked.connect(self._create)
        row.addWidget(make)
        self.body.addLayout(row)
        acts = QHBoxLayout()
        rb = button(tr("pve_snap_rollback"), Icons.UNDO)
        rb.clicked.connect(self._rollback)
        acts.addWidget(rb)
        dl = button(tr("delete"), Icons.DELETE, role="Danger")
        dl.clicked.connect(self._delete)
        acts.addWidget(dl)
        acts.addStretch(1)
        self.state = label("", 9, role="Dim")
        acts.addWidget(self.state)
        self.body.addLayout(acts)
        self.add_buttons(tr("close"))
        self.load()

    def _call(self, fn, label_text, then=None):
        self.state.setText(tr("pve_working"))
        g = self.guest

        def done(result):
            try:
                self.state.setText("")
            except RuntimeError:
                return
            if isinstance(result, Exception):
                self.view.infra.api.notify(f"{label_text}: {result}", "error")
            elif then:
                then(result)
        self.view.infra.pve_call(self.view.host_id, lambda c: fn(c, g), done, self, label_text)

    def load(self):
        def fill(rows):
            self.list.clear()
            for s in sorted(rows, key=lambda r: r.get("snaptime", 0), reverse=True):
                when = datetime.fromtimestamp(s["snaptime"]).strftime("%Y-%m-%d %H:%M") if s.get("snaptime") else ""
                it = QListWidgetItem(f"{s['name']}   ·   {when}   {s.get('description', '').strip()}")
                it.setData(Qt.ItemDataRole.UserRole, s["name"])
                self.list.addItem(it)
            if not rows:
                self.list.addItem(QListWidgetItem(tr("pve_no_snapshots")))
        self._call(lambda c, g: c.snapshots(g["node"], g["type"], g["vmid"]), "", fill)

    def _selected(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _create(self):
        name = self.name.text().strip()
        if not SNAP_RE.match(name):
            self.name.setFocus()
            return
        desc = self.desc.text().strip()
        self._call(lambda c, g: c.wait(g["node"], c.snapshot(g["node"], g["type"], g["vmid"], name, desc), 600),
                   tr("pve_snap_create") + f" {name}", lambda st: (self._task_done(st), self.load()))

    def _rollback(self):
        name = self._selected()
        if name and ConfirmDialog.ask(tr("pve_snap_rollback"), tr("pve_rollback_msg", snap=name,
                                                                   name=self.guest.get("name", "")), tr("pve_snap_rollback"), self):
            self._call(lambda c, g: c.wait(g["node"], c.rollback(g["node"], g["type"], g["vmid"], name), 600),
                       tr("pve_snap_rollback") + f" {name}", lambda st: (self._task_done(st), self.view.reload()))

    def _delete(self):
        name = self._selected()
        if name and ConfirmDialog.ask(tr("delete"), tr("pve_delete_snap_msg", snap=name), tr("delete"), self):
            self._call(lambda c, g: c.wait(g["node"], c.delete_snapshot(g["node"], g["type"], g["vmid"], name), 600),
                       tr("delete") + f" {name}", lambda st: (self._task_done(st), self.load()))

    def _task_done(self, st: dict):
        ok = st.get("exitstatus") == "OK"
        self.view.infra.api.notify(tr("pve_task_ok") if ok else tr("pve_task_failed", detail=st.get("exitstatus", "…")),
                                   "success" if ok else "warning")


class ProxmoxView(QWidget):
    def __init__(self, infra, page):
        super().__init__()
        self.infra, self.page = infra, page
        self.host_id: int | None = None
        self.guests: list[dict] = []
        self.filter = "all"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        top = QHBoxLayout()
        self.hosts = QComboBox()
        self.hosts.setMinimumWidth(220)
        self.hosts.currentIndexChanged.connect(self._host_changed)
        top.addWidget(self.hosts)
        self.version = label("", 9, role="Dim")
        top.addWidget(self.version)
        top.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("pve_search"))
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(self._fill)
        top.addWidget(self.search)
        self.chips = QButtonGroup(self)
        for k in ("all", "running", "stopped"):
            c = chip(tr("pve_filter_" + k))
            c.setChecked(k == "all")
            c.clicked.connect(lambda _=False, key=k: self._set_filter(key))
            self.chips.addButton(c)
            top.addWidget(c)
        refresh = IconButton(Icons.REFRESH, tr("reload"), 36, 11)
        refresh.clicked.connect(self.reload)
        top.addWidget(refresh)
        conn = button(tr("pve_connection"), Icons.SETTINGS, role="Ghost")
        conn.clicked.connect(self.edit_connection)
        top.addWidget(conn)
        lay.addLayout(top)
        self.nodes = QHBoxLayout()
        self.nodes.setSpacing(12)
        lay.addLayout(self.nodes)
        self.list = QListWidget()
        self.list.setObjectName("CleanList")
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemDoubleClicked.connect(lambda it: self._snapshots(it.data(Qt.ItemDataRole.UserRole)))
        lay.addWidget(self.list, 1)
        self.empty = EmptyState(Icons.DRIVE, tr("pve_no_hosts"))
        lay.addWidget(self.empty)
        self.timer = QTimer(self, interval=15_000, timeout=lambda: self.isVisible() and self.reload(quiet=True))
        self.timer.start()

    # -------------------------------------------------------------- data
    def fill_hosts(self) -> None:
        pve = [h for h in self.infra.store.hosts() if h["kind"] == "proxmox"]
        keep = self.host_id
        self.hosts.blockSignals(True)
        self.hosts.clear()
        for h in pve:
            self.hosts.addItem(f"{h['name']}  ·  {h['address']}", h["id"])
        self.hosts.blockSignals(False)
        has = bool(pve)
        self.empty.setVisible(not has)
        self.list.setVisible(has)
        if has:
            idx = max(0, self.hosts.findData(keep))
            self.hosts.setCurrentIndex(idx)
            self._host_changed()

    def _host_changed(self, *_):
        hid = self.hosts.currentData()
        if hid != self.host_id:
            self.host_id = hid
            self.guests = []
            self._fill()
            self.reload()

    def reload(self, quiet: bool = False) -> None:
        if not self.host_id:
            return
        cfg = self.infra.pve_settings(self.host_id)
        host = self.infra.store.host(self.host_id)
        if cfg["auth"] == "token" and not cfg["token_id"] and host and host["auth"] != "password":
            if not quiet:
                self.edit_connection()
            return
        if quiet and self.infra.ctl.vault and not self.infra.ctl.vault.unlocked:
            return  # never pop the vault question from the auto-refresh

        def done(result):
            try:
                self.isVisible()
            except RuntimeError:
                return
            if isinstance(result, Exception):
                self.version.setText("⚠ " + str(result)[:120])
                return
            version, nodes, guests = result
            self.version.setText(f"Proxmox VE {version.get('version', '')}")
            self.guests = guests
            self._fill_nodes(nodes)
            self._fill()
        self.infra.pve_call(self.host_id, lambda c: (c.version(), c.nodes(), c.guests()), done, self)

    def _fill_nodes(self, nodes: list[dict]) -> None:
        while self.nodes.count():
            w = self.nodes.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        for n in nodes:
            card = Card(padding=14)
            online = n.get("status") == "online"
            card.root.addWidget(label(f"{'●' if online else '○'}  {n['node']}", 11, QFont.Weight.DemiBold))
            mem = f"{human(n.get('mem', 0))} / {human(n.get('maxmem', 0))}"
            card.root.addWidget(label(ltr(f"CPU {_pct(n.get('cpu'))} · RAM {mem} · {_uptime(n.get('uptime'))}"),
                                      9, role="Dim"))
            self.nodes.addWidget(card)
        self.nodes.addStretch(1)

    def _set_filter(self, key: str) -> None:
        self.filter = key
        self._fill()

    def _fill(self) -> None:
        self.list.clear()
        q = self.search.text().strip().lower()
        for g in self.guests:
            if self.filter == "running" and g.get("status") != "running":
                continue
            if self.filter == "stopped" and g.get("status") == "running":
                continue
            if q and q not in f"{g.get('name', '')} {g['vmid']} {g.get('node', '')}".lower():
                continue
            running = g.get("status") == "running"
            kind = "VM" if g.get("type") == "qemu" else "CT"
            mem = f"{human(g.get('mem', 0))} / {human(g.get('maxmem', 0))}" if running else human(g.get("maxmem", 0))
            text = (f"{'🟢' if running else '⚪'}  {g['vmid']}  {g.get('name', '')}   ·   {kind} · {g.get('node', '')}"
                    f"   ·   CPU {_pct(g.get('cpu')) if running else '—'} · RAM {mem} · {_uptime(g.get('uptime'))}")
            it = QListWidgetItem(ltr(text))
            it.setData(Qt.ItemDataRole.UserRole, g)
            it.setToolTip(tr("pve_row_hint"))
            self.list.addItem(it)
        if self.host_id and not self.guests:
            self.list.addItem(QListWidgetItem(tr("pve_loading")))

    # ----------------------------------------------------------- actions
    def edit_connection(self) -> None:
        if not self.host_id:
            return
        host = self.infra.store.host(self.host_id)
        dlg = PveSettingsDialog(self.infra.pve_settings(self.host_id), host, self)
        if dlg.exec() == StyledDialog.DialogCode.Accepted:
            if self.infra.save_pve_settings(self.host_id, dlg.port.value(), dlg.mode(), dlg.token_id.text(),
                                            dlg.secret.text() or None, dlg.realm.currentText(), self):
                self.reload()

    def _menu(self, pos) -> None:
        it = self.list.itemAt(pos)
        g = it.data(Qt.ItemDataRole.UserRole) if it else None
        if not g:
            return
        running = g.get("status") == "running"
        menu = make_menu(self)
        if running:
            add_action(menu, Icons.POWER, tr("pve_shutdown"), lambda: self._power(g, "shutdown"))
            add_action(menu, Icons.REFRESH, tr("pve_reboot"), lambda: self._power(g, "reboot"))
            add_action(menu, Icons.CLOSE, tr("pve_stop"), lambda: self._power(g, "stop"), danger=True)
            add_action(menu, Icons.PREVIEW, tr("pve_console"), lambda: self._console(g))
        else:
            add_action(menu, Icons.PLAY, tr("pve_start"), lambda: self._power(g, "start"))
        menu.addSeparator()
        add_action(menu, Icons.SAVE, tr("pve_snapshots"), lambda: self._snapshots(g))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _power(self, g: dict, action: str) -> None:
        name = g.get("name") or str(g["vmid"])
        if action in ("stop", "reboot", "shutdown"):
            danger = action == "stop"
            if not ConfirmDialog.ask(tr("pve_" + action), tr(f"pve_{action}_msg", name=name), tr("pve_" + action),
                                     self, danger=danger):
                return
        text = f"{tr('pve_' + action)} {g['vmid']} {name}"

        def done(st):
            if isinstance(st, Exception):
                self.infra.api.notify(f"{text}: {st}", "error")
            else:
                ok = st.get("exitstatus") == "OK" or st.get("status") == "running"
                self.infra.api.notify(f"{text} ✓" if ok else tr("pve_task_failed", detail=st.get("exitstatus", "…")),
                                      "success" if ok else "warning")
            self.reload(quiet=True)
        self.infra.api.notify(tr("pve_sent", action=text), "info")
        self.infra.pve_call(self.host_id, lambda c: c.wait(g["node"], c.power(g["node"], g["type"], g["vmid"], action),
                                                           180), done, self, text)

    def _console(self, g: dict) -> None:
        host = self.infra.store.host(self.host_id)
        cfg = self.infra.pve_settings(self.host_id)
        from app.plugins.infra.proxmox import Proxmox
        url = Proxmox(host["address"], cfg["port"]).console_url(g["node"], g["type"], g["vmid"], g.get("name", ""))
        QDesktopServices.openUrl(QUrl(url))

    def _snapshots(self, g: dict | None) -> None:
        if g:
            SnapshotDialog(self, g, self).exec()
