"""
nginx tab: sites of a server (enable / disable / view), test + reload, logs, certificates, new sites from
templates, HTTPS with certbot. Runs over SSH through InfraController.run_on_host (sudo from the vault).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QListWidget,
                             QListWidgetItem, QPlainTextEdit, QSplitter, QVBoxLayout, QWidget)

from app.core.i18n import ltr, tr
from app.plugins.infra import nginx
from app.ui.dialogs.base import ConfirmDialog, PromptDialog, StyledDialog
from app.ui.dialogs.study_dialogs import chip, field
from app.ui.theme import Icons, Palette
from app.ui.widgets.common import EmptyState, IconButton, add_action, button, label, make_menu, primary_button


def mono() -> QFont:
    f = QFont()
    f.setFamilies(["Cascadia Mono", "Consolas", "Courier New"])
    f.setPointSizeF(9.5)
    return f


class NewSiteDialog(StyledDialog):
    def __init__(self, parent=None):
        super().__init__(tr("ngx_new_site"), parent, 640)
        self.body.addWidget(field(tr("ngx_template")))
        row = QHBoxLayout()
        self.kind = QButtonGroup(self)
        for k in ("proxy", "static", "redirect"):
            b = chip(tr("ngx_tpl_" + k))
            b.setProperty("key", k)
            b.setChecked(k == "proxy")
            b.toggled.connect(lambda _: self._update())
            self.kind.addButton(b)
            row.addWidget(b)
        row.addStretch(1)
        self.body.addLayout(row)
        self.body.addWidget(field(tr("ngx_domains")))
        self.domains = QLineEdit()
        self.domains.setPlaceholderText("app.example.com www.app.example.com")
        self.body.addWidget(self.domains)
        self.target_label = field(tr("ngx_upstream"))
        self.body.addWidget(self.target_label)
        self.target = QLineEdit()
        self.target.setPlaceholderText("127.0.0.1:3000")
        self.body.addWidget(self.target)
        self.body.addWidget(field(tr("ngx_site_name")))
        self.name = QLineEdit()
        self.name.setPlaceholderText("app.example.com")
        self.body.addWidget(self.name)
        for w in (self.domains, self.target, self.name):
            w.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            w.textChanged.connect(self._update)
        self.body.addWidget(field(tr("ngx_preview")))
        self.preview = QPlainTextEdit()
        self.preview.setFont(mono())
        self.preview.setMinimumHeight(220)
        self.preview.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.body.addWidget(self.preview)
        self.error = label("", 9, role="Dim")
        self.body.addWidget(self.error)
        self.add_buttons(tr("ngx_create"))
        self._update()

    def template(self) -> str:
        b = self.kind.checkedButton()
        return b.property("key") if b else "proxy"

    def _update(self, *_):
        t = self.template()
        self.target_label.setText(tr("ngx_upstream") if t == "proxy" else tr("ngx_root") if t == "static"
                                  else tr("ngx_redirect_to"))
        self.target.setPlaceholderText({"proxy": "127.0.0.1:3000", "static": "/var/www/site",
                                        "redirect": "https://example.com"}[t])
        if not self.name.isModified() and self.domains.text().split():
            self.name.setText(self.domains.text().split()[0].replace("*", "wildcard"))
        try:
            text = nginx.render(t, self.domains.text() or "example.com",
                                target=self.target.text() or ("127.0.0.1:3000" if t == "proxy" else "https://example.com"),
                                root=self.target.text() if t == "static" else "")
            self.preview.setPlainText(text)
            self.error.setText("")
        except ValueError as exc:
            self.error.setText("⚠ " + tr("ngx_invalid", field=str(exc)))

    def validate(self) -> bool:
        try:
            nginx.render(self.template(), self.domains.text(), self.target.text(),
                         self.target.text() if self.template() == "static" else "")
        except ValueError as exc:
            self.error.setText("⚠ " + tr("ngx_invalid", field=str(exc)))
            return False
        if not nginx.NAME_RE.match(self.name.text().strip()):
            self.error.setText("⚠ " + tr("ngx_invalid", field="name"))
            return False
        return True


class NginxView(QWidget):
    def __init__(self, infra, page):
        super().__init__()
        self.infra, self.page = infra, page
        self.host_id: int | None = None
        self.info: dict = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        top = QHBoxLayout()
        self.hosts = QComboBox()
        self.hosts.setMinimumWidth(220)
        self.hosts.currentIndexChanged.connect(self._host_changed)
        top.addWidget(self.hosts)
        self.state = label("", 9, role="Dim")
        top.addWidget(self.state)
        top.addStretch(1)
        for text, glyph, slot in ((tr("ngx_test"), Icons.ACCEPT, self.test), (tr("ngx_reload"), Icons.REFRESH, self.reload_nginx),
                                  (tr("ngx_logs"), Icons.VIEW_LIST, self.logs), (tr("ngx_certs"), Icons.SHIELD, self.certs)):
            b = button(text, glyph, role="Ghost")
            b.clicked.connect(slot)
            top.addWidget(b)
        refresh = IconButton(Icons.REFRESH, tr("reload"), 36, 11)
        refresh.clicked.connect(self.load)
        top.addWidget(refresh)
        new = primary_button(tr("ngx_new_site"), Icons.ADD)
        new.clicked.connect(self.new_site)
        top.addWidget(new)
        lay.addLayout(top)
        split = QSplitter(Qt.Orientation.Vertical)
        self.list = QListWidget()
        self.list.setObjectName("CleanList")
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemDoubleClicked.connect(lambda it: self.show_config(it.data(Qt.ItemDataRole.UserRole)))
        split.addWidget(self.list)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono())
        self.output.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.output.setPlaceholderText(tr("ngx_output_ph"))
        split.addWidget(self.output)
        split.setSizes([300, 260])
        lay.addWidget(split, 1)
        self.split = split
        self.empty = EmptyState(Icons.DRIVE, tr("ngx_no_hosts"))
        lay.addWidget(self.empty)

    def fill_hosts(self) -> None:
        hosts = [h for h in self.infra.store.hosts() if h["kind"] not in ("windows", "vmware")]
        keep = self.host_id
        self.hosts.blockSignals(True)
        self.hosts.clear()
        for h in hosts:
            self.hosts.addItem(f"{h['name']}  ·  {h['address']}", h["id"])
        self.hosts.blockSignals(False)
        self.empty.setVisible(not hosts)
        self.split.setVisible(bool(hosts))
        if hosts:
            self.hosts.setCurrentIndex(max(0, self.hosts.findData(keep)))
            self._host_changed()

    def _host_changed(self, *_):
        hid = self.hosts.currentData()
        if hid != self.host_id:
            self.host_id = hid
            self.list.clear()
            self.output.clear()
            self.state.setText("")

    def on_shown(self) -> None:
        if self.host_id and not self.list.count():
            self.load()

    # ------------------------------------------------------------ running
    def _run(self, command: str, sudo: bool, then, title: str = "") -> None:
        if not self.host_id:
            return
        if title:
            self.output.setPlainText(f"$ {title}\n…")

        def done(result):
            try:
                self.isVisible()
            except RuntimeError:
                return
            if isinstance(result, Exception):
                from app.plugins.infra.controller import error_text
                self.output.setPlainText(f"$ {title}\n⚠ {error_text(result)}")
                return
            then(result)
        self.infra.run_on_host(self.host_id, command, sudo, done, self, title or command[:80])

    def _show(self, title: str, result: dict) -> None:
        mark = "✓" if result["code"] == 0 else f"✗ exit {result['code']}"
        self.output.setPlainText(f"$ {title}   {mark}\n\n{result['out'].strip()}")
        lab = getattr(self.infra.ctl, "lab", None)
        host = self.infra.store.host(self.host_id) if self.host_id else None
        if lab and lab.active and host:  # during a lab the result goes into the report
            lab.add("library", name=f"nginx · {title}", host=host["name"], host_id=host["id"], cmd=title,
                    out=result["out"].strip(), code=result["code"])

    def load(self) -> None:
        def done(result):
            self.info = nginx.parse_sites(result["out"])
            self.list.clear()
            active = self.info.get("active", "")
            self.state.setText(ltr(f"nginx {self.info.get('version', '?')} · {active or tr('ngx_not_found')}"))
            for s in self.info["sites"]:
                mark = "●" if s["enabled"] else "○"
                text = f"{mark}  {s['name']}    {s['names'] or '—'}    [{s['listen'] or '—'}]"
                it = QListWidgetItem(ltr(text))
                it.setData(Qt.ItemDataRole.UserRole, s)
                if not s["enabled"]:
                    it.setForeground(QColor(Palette.text_faint))
                self.list.addItem(it)
            if not self.info["sites"]:
                self.list.addItem(QListWidgetItem(tr("ngx_no_sites")))
        self._run(nginx.LIST_SITES, False, done, tr("ngx_sites"))

    def _menu(self, pos) -> None:
        it = self.list.itemAt(pos)
        s = it.data(Qt.ItemDataRole.UserRole) if it else None
        if not s:
            return
        menu = make_menu(self)
        add_action(menu, Icons.PREVIEW, tr("ngx_view"), lambda: self.show_config(s))
        if not s["conf_d"]:
            if s["enabled"]:
                add_action(menu, Icons.CLOSE, tr("ngx_disable"), lambda: self.toggle(s, False), danger=True)
            else:
                add_action(menu, Icons.ACCEPT, tr("ngx_enable"), lambda: self.toggle(s, True))
        if self.info.get("certbot") and s["names"]:
            add_action(menu, Icons.SHIELD, tr("ngx_https"), lambda: self.https(s))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def show_config(self, s: dict | None) -> None:
        if s:
            self._run(nginx.show(s), False, lambda r: self._show(s["path"], r), s["path"])

    def toggle(self, s: dict, on: bool) -> None:
        if not on and not ConfirmDialog.ask(tr("ngx_disable"), tr("ngx_disable_msg", name=s["name"]), tr("ngx_disable"), self):
            return
        cmd = nginx.enable(s) if on else nginx.disable(s)
        self._run(cmd, True, lambda r: (self._show(tr("ngx_enable") if on else tr("ngx_disable"), r), self.load()),
                  f"{tr('ngx_enable') if on else tr('ngx_disable')} {s['name']}")

    def test(self) -> None:
        self._run(nginx.TEST, True, lambda r: self._show("nginx -t", r), "nginx -t")

    def reload_nginx(self) -> None:
        self._run(nginx.RELOAD, True, lambda r: self._show("nginx -t && systemctl reload nginx", r), "reload nginx")

    def logs(self) -> None:
        self._run(nginx.logs(None, 150), True, lambda r: self._show(tr("ngx_logs"), r), tr("ngx_logs"))

    def certs(self) -> None:
        def done(r):
            certs = nginx.parse_certs(r["out"])
            if not certs:
                self.output.setPlainText(tr("ngx_no_certs"))
                return
            from datetime import datetime
            lines = []
            for c in certs:
                try:
                    days = (datetime.strptime(c["expires"], "%b %d %H:%M:%S %Y %Z") - datetime.utcnow()).days
                    flag = "⚠" if days < 15 else "✓"
                    lines.append(f"{flag}  {c['name']:<32} {days:>4} {tr('ngx_days')}   {c['domains']}")
                except ValueError:
                    lines.append(f"?  {c['name']:<32} {c['expires']}   {c['domains']}")
            self.output.setPlainText("\n".join(lines))
        self._run(nginx.CERTS, True, done, tr("ngx_certs"))

    def https(self, s: dict) -> None:
        email = PromptDialog.ask(tr("ngx_https"), "", tr("ngx_email_ph"), self)
        if email is None:
            return
        try:
            cmd = nginx.certbot(s, email.strip())
        except ValueError as exc:
            self.infra.api.notify(str(exc), "warning")
            return
        self._run(cmd, True, lambda r: (self._show("certbot", r), self.load()), f"certbot {s['name']}")

    def new_site(self) -> None:
        if not self.host_id:
            return
        dlg = NewSiteDialog(self)
        if dlg.exec() != StyledDialog.DialogCode.Accepted:
            return
        name = dlg.name.text().strip()
        try:
            cmd = nginx.create_site(name, dlg.preview.toPlainText())
        except ValueError as exc:
            self.infra.api.notify(str(exc), "warning")
            return

        def done(r):
            self._show(tr("ngx_new_site") + " " + name, r)
            if "CREATED" in r["out"]:
                self.infra.api.notify(tr("ngx_created", name=name), "success")
            elif "EXISTS" in r["out"]:
                self.infra.api.notify(tr("ngx_exists", name=name), "warning")
            else:
                self.infra.api.notify(tr("ngx_rolled_back"), "error")
            self.load()
        self._run(cmd, True, done, f"{tr('ngx_new_site')} {name}")
