"""
Infrastructure page (plugin): hosts, monitoring, operations log.

  Hosts    cards per group: kind, address, live status from SSH (OS, uptime, load, memory, disk, updates,
           reboot needed, Docker containers, Proxmox / nginx), buttons for terminal, status, web UI, RDP
  Monitor  ping / port / HTTP / certificate checks with state, latency sparkline, uptime %
  Proxmox  nodes, VMs / containers, power, snapshots, console (Proxmox API, pinned certificate)
  nginx    sites, enable / disable, test + reload, logs, certificates, new sites from templates
  Commands a library of shell commands run on several servers at once
  Log      every action and alert with time and result
"""
from __future__ import annotations

import json

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                             QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QScrollArea, QSpinBox,
                             QVBoxLayout, QWidget)

from app.core.cleanup import human
from app.core.i18n import ltr, relative_time, tr
from app.plugins.infra import ssh
from app.ui.animations import AnimatedStack
from app.ui.dialogs.base import StyledDialog
from app.ui.dialogs.study_dialogs import chip, field
from app.ui.pages.base import Page
from app.ui.theme import Icons, Palette, Theme, ui_font, ink
from app.ui.widgets.common import (Card, EmptyState, GlyphLabel, GlyphPlate, IconButton, ToggleSwitch, add_action,
                                   button, label, make_menu, primary_button)
from app.ui.widgets.segmented import SegmentedTabs

KIND_GLYPH = {"linux": "", "proxmox": "", "docker": "", "vmware": "", "windows": "",
              "other": ""}
STATE_COLOR = {"up": Palette.success, "warn": Palette.warning, "down": Palette.danger, "": Palette.text_faint}
COLORS = ["#22D3EE", "#60A5FA", "#A78BFA", "#34D399", "#FBBF24", "#F97316", "#F87171", "#F472B6"]


def _rgba(color: str, a: int) -> str:
    c = QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{a})"


class Meter(QWidget):
    """Thin usage bar (memory / disk)."""

    def __init__(self, ratio: float, parent=None):
        super().__init__(parent)
        self.ratio = max(0.0, min(1.0, ratio))
        self.setFixedHeight(6)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink(25 / 255))
        p.drawRoundedRect(r, 3, 3)
        color = Palette.danger if self.ratio > 0.9 else Palette.warning if self.ratio > 0.75 else Theme.accent2()
        fill = QRectF(r)
        fill.setWidth(r.width() * self.ratio)
        if self.layoutDirection() == Qt.LayoutDirection.RightToLeft:
            fill.moveRight(r.right())
        p.setBrush(QColor(color))
        p.drawRoundedRect(fill, 3, 3)


class Sparkline(QWidget):
    def __init__(self, points: list[tuple[str, float]], parent=None):
        super().__init__(parent)
        self.points = points
        self.setFixedSize(120, 28)

    def paintEvent(self, _):
        if len(self.points) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        values = [ms for _, ms in self.points]
        top = max(values) or 1
        w, h = self.width(), self.height() - 4
        step = w / (len(values) - 1)
        path = QPainterPath()
        for i, v in enumerate(values):
            pt = QPointF(i * step, 2 + h - h * v / top)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.setPen(QPen(QColor(Theme.accent2()), 1.6))
        p.drawPath(path)
        for i, (state, _) in enumerate(self.points):
            if state == "down":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(Palette.danger))
                p.drawEllipse(QPointF(i * step, h), 2, 2)


class Dot(QWidget):
    def __init__(self, color: str, size: int = 10, parent=None):
        super().__init__(parent)
        self.color = color
        self.setFixedSize(size + 6, size + 6)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.color)
        glow = QColor(c)
        glow.setAlpha(60)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(self.rect())
        p.setBrush(c)
        p.drawEllipse(QRectF(self.rect()).adjusted(3, 3, -3, -3))


def _uptime_text(seconds: int) -> str:
    days, rest = divmod(max(0, int(seconds)), 86400)
    hours, minutes = rest // 3600, (rest % 3600) // 60
    return tr("infra_uptime", d=days, h=hours) if days else tr("infra_uptime_h", h=hours, m=minutes)


class HostCard(Card):
    def __init__(self, host: dict, page):
        super().__init__(padding=16)
        self.host, self.page = host, page
        ctl = page.infra
        self.setMinimumWidth(330)
        self.setStyleSheet(f"QFrame#Card {{ border-top: 3px solid {host['color']}; }}")
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(GlyphPlate(KIND_GLYPH.get(host["kind"], Icons.DRIVE), host["color"], 40))
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(host["name"], 11.5, QFont.Weight.DemiBold))
        addr = f"{host['username']}@{host['address']}" if host["username"] else host["address"]
        if int(host["ssh_port"] or 22) != 22:
            addr += f":{host['ssh_port']}"
        a = label(ltr(addr), 8.8, role="Dim")
        a.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(a)
        top.addLayout(col, 1)
        badge = {"key": tr("infra_auth_key"), "password": tr("infra_auth_password"), "none": ""}.get(host["auth"], "")
        if badge:
            b = label(badge, 7.8)
            color = Palette.success if host["auth"] == "key" else Palette.warning
            b.setStyleSheet(f"color:{color}; border:1px solid {color}; border-radius:7px; padding:0 6px;")
            top.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        self.root.addLayout(top)

        status = json.loads(host["last_status"]) if host["last_status"] else {}
        body = QVBoxLayout()
        body.setSpacing(5)
        if page.busy_hosts.get(host["id"]):
            body.addWidget(label(tr("infra_checking"), 9, role="Dim"))
        elif status.get("error"):
            e = label("⚠ " + status["error"], 9, wrap=True)
            e.setStyleSheet(f"color: {Palette.danger};")
            body.addWidget(e)
        elif status:
            body.addWidget(label(ltr(status.get("os") or status.get("host", "")), 9.4, QFont.Weight.Medium))
            facts = [_uptime_text(status.get("uptime_s", 0))]
            if status.get("load"):
                facts.append(tr("infra_load", load=ltr(" / ".join(f"{x:.2f}" for x in status["load"]))))
            body.addWidget(label("  ·  ".join(facts), 8.8, role="Dim"))
            for key, total, used in (("infra_mem", status.get("mem_total"), status.get("mem_used")),
                                     ("infra_disk", status.get("disk_total"), status.get("disk_used"))):
                if total:
                    row = QHBoxLayout()
                    row.addWidget(label(tr(key), 8.6, role="Dim"))
                    row.addWidget(Meter(used / total), 1)
                    row.addWidget(label(ltr(f"{human(used)} / {human(total)}"), 8.4, role="Faint"))
                    body.addLayout(row)
            chips = QHBoxLayout()
            chips.setSpacing(6)
            if status.get("updates"):
                chips.addWidget(self._chip(tr("infra_updates", n=status["updates"]), Palette.warning))
            if status.get("reboot"):
                chips.addWidget(self._chip(tr("infra_reboot"), Palette.danger))
            if "docker_total" in status:
                chips.addWidget(self._chip(tr("infra_docker", run=status["docker_running"], all=status["docker_total"]),
                                           "#60A5FA"))
            if status.get("pve"):
                chips.addWidget(self._chip("Proxmox " + status["pve"], "#F97316"))
            if status.get("nginx"):
                chips.addWidget(self._chip("nginx · " + status["nginx"],
                                           Palette.success if status["nginx"] == "active" else Palette.danger))
            chips.addStretch(1)
            body.addLayout(chips)
        else:
            body.addWidget(label(tr("infra_no_status"), 9, role="Faint", wrap=True))
        if host["status_at"]:
            body.addWidget(label(tr("infra_checked", when=relative_time(host["status_at"])), 8, role="Faint"))
        self.root.addLayout(body)
        self.root.addStretch(1)

        actions = QHBoxLayout()
        actions.setSpacing(4)
        term = button(tr("infra_terminal"), Icons.KEYBOARD, role="Ghost")
        term.clicked.connect(lambda: ctl.open_terminal(host["id"], page))
        actions.addWidget(term)
        refresh = IconButton(Icons.REFRESH, tr("infra_refresh"), 32, 10)
        refresh.clicked.connect(lambda: ctl.refresh_status(host["id"], page))
        refresh.setEnabled(host["auth"] != "none" and not page.busy_hosts.get(host["id"]))
        actions.addWidget(refresh)
        if host["web_url"]:
            web = IconButton(Icons.GLOBE, tr("infra_web"), 32, 10)
            web.clicked.connect(lambda: ctl.open_web(host["id"]))
            actions.addWidget(web)
        if host["rdp"]:
            rdp = IconButton(Icons.DESKTOP, tr("infra_rdp"), 32, 10)
            rdp.clicked.connect(lambda: ctl.open_rdp(host["id"]))
            actions.addWidget(rdp)
        actions.addStretch(1)
        more = IconButton(Icons.MENU, tr("more"), 32, 10)
        more.clicked.connect(lambda: page.host_menu(host["id"], more.mapToGlobal(more.rect().bottomLeft())))
        actions.addWidget(more)
        self.root.addLayout(actions)

    @staticmethod
    def _chip(text: str, color: str) -> QWidget:
        w = label(text, 8)
        w.setStyleSheet(f"color:{color}; background:{_rgba(color, 35)}; border-radius:8px; padding:1px 7px;")
        return w


class InfraPage(Page):
    def __init__(self, ctl, main_window, infra):
        super().__init__(ctl, main_window)
        self.infra = infra
        self.busy_hosts: dict[int, bool] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(16)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(tr("nav_infra"), 22, QFont.Weight.DemiBold))
        titles.addWidget(label(tr("infra_subtitle"), 10, role="Dim"))
        header.addLayout(titles)
        header.addStretch(1)
        self.summary = QHBoxLayout()
        self.summary.setSpacing(10)
        header.addLayout(self.summary)
        refresh_all = IconButton(Icons.REFRESH, tr("infra_refresh_all"), 36, 11)
        refresh_all.clicked.connect(self.refresh_all)
        header.addWidget(refresh_all)
        add_check = button(tr("infra_add_check"), Icons.ADD, role="Ghost")
        add_check.clicked.connect(lambda: self.edit_check(None))
        header.addWidget(add_check)
        add = primary_button(tr("infra_add_host"), Icons.ADD)
        add.clicked.connect(lambda: self.edit_host(None))
        header.addWidget(add)
        root.addLayout(header)

        self.tabs = SegmentedTabs([("hosts", Icons.DRIVE, tr("infra_tab_hosts")),
                                   ("monitor", Icons.SYNC, tr("infra_tab_monitor")),
                                   ("proxmox", Icons.APPS, tr("infra_tab_pve")),
                                   ("nginx", Icons.GLOBE, tr("infra_tab_nginx")),
                                   ("commands", Icons.KEYBOARD, tr("infra_tab_commands")),
                                   ("log", Icons.VIEW_LIST, tr("infra_tab_log"))], "hosts")
        self.tabs.changed.connect(self.show_tab)
        trow = QHBoxLayout()
        trow.addWidget(self.tabs)
        trow.addStretch(1)
        root.addLayout(trow)

        self.stack = AnimatedStack()
        # hosts
        host_box = QWidget()
        self.hosts_lay = QVBoxLayout(host_box)
        self.hosts_lay.setContentsMargins(0, 0, 8, 0)
        self.hosts_lay.setSpacing(14)
        self.hosts_scroll = QScrollArea()
        self.hosts_scroll.setWidgetResizable(True)
        self.hosts_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.hosts_scroll.setWidget(host_box)
        self.hosts_scroll.viewport().setAutoFillBackground(False)
        host_box.setAutoFillBackground(False)
        # monitor
        self.checks_list = QListWidget()
        self.checks_list.setObjectName("CleanList")
        self.checks_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.checks_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.checks_list.customContextMenuRequested.connect(self._check_menu)
        self.checks_list.itemDoubleClicked.connect(lambda it: self.edit_check(it.data(Qt.ItemDataRole.UserRole)))
        mon = QWidget()
        ml = QVBoxLayout(mon)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(self.checks_list, 1)
        self.checks_empty = EmptyState(Icons.SYNC, tr("infra_no_checks"))
        ml.addWidget(self.checks_empty)
        # log
        self.log_list = QListWidget()
        self.log_list.setObjectName("CleanList")
        self.log_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        from app.plugins.infra.commands_view import CommandsView
        from app.plugins.infra.nginx_view import NginxView
        from app.plugins.infra.pve_view import ProxmoxView
        self.pve = ProxmoxView(infra, self)
        self.nginx = NginxView(infra, self)
        self.commands = CommandsView(infra, self)
        self.views = {"hosts": self.hosts_scroll, "monitor": mon, "proxmox": self.pve, "nginx": self.nginx,
                      "commands": self.commands, "log": self.log_list}
        for v in self.views.values():
            self.stack.addWidget(v)
        root.addWidget(self.stack, 1)

        self.listen(infra.changed, self.refresh)
        self.listen(infra.busy, self._busy)
        self.refresh("")

    # ----------------------------------------------------------- refresh
    def _busy(self, host_id: int, on: bool) -> None:
        self.busy_hosts[host_id] = on
        self._fill_hosts()

    def refresh(self, what: str = "") -> None:
        if what in ("", "hosts"):
            self._fill_hosts()
            self.pve.fill_hosts()
            self.nginx.fill_hosts()
            self.commands.fill()
        if what in ("", "checks", "hosts"):
            self._fill_checks()
        if what in ("", "log"):
            self._fill_log()
        self._fill_summary()

    def _fill_summary(self) -> None:
        while self.summary.count():
            w = self.summary.takeAt(0).widget()
            if w:
                w.hide()  # gone at once, even inside a nested event loop (open dialog)
                w.deleteLater()
        checks = self.infra.store.checks()
        counts = {s: sum(1 for c in checks if c["enabled"] and c["state"] == s) for s in ("up", "warn", "down")}
        self.summary.addWidget(label(tr("infra_hosts_n", n=len(self.infra.store.hosts())), 9.5, role="Dim"))
        for state in ("up", "warn", "down"):
            if counts[state] or state == "up":
                box = QWidget()
                h = QHBoxLayout(box)
                h.setContentsMargins(0, 0, 0, 0)
                h.setSpacing(4)
                h.addWidget(Dot(STATE_COLOR[state], 8))
                h.addWidget(label(f"{counts[state]} {tr('infra_state_' + state)}", 9.5))
                self.summary.addWidget(box)

    def _fill_hosts(self) -> None:
        while self.hosts_lay.count():
            item = self.hosts_lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        hosts = self.infra.store.hosts()
        if not hosts:
            self.hosts_lay.addWidget(EmptyState(Icons.DRIVE, tr("infra_no_hosts")))
            self.hosts_lay.addStretch(1)
            return
        cols = self._columns()
        self._cols_used = cols
        groups: dict[str, list] = {}
        for h in hosts:
            groups.setdefault(h["grp"] or tr("infra_no_group"), []).append(h)
        for name, items in groups.items():
            self.hosts_lay.addWidget(label(name.upper(), 9, QFont.Weight.DemiBold, "Faint"))
            grid_host = QWidget()
            grid = QGridLayout(grid_host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(14)
            for i, h in enumerate(items):
                grid.addWidget(HostCard(h, self), i // cols, i % cols)
            for c in range(cols):
                grid.setColumnStretch(c, 1)
            self.hosts_lay.addWidget(grid_host)
        self.hosts_lay.addStretch(1)

    def _columns(self) -> int:
        return max(1, min(3, (self.hosts_scroll.viewport().width() - 10) // 360))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._columns() != getattr(self, "_cols_used", None):
            self._fill_hosts()

    def on_shown(self) -> None:
        if self._columns() != getattr(self, "_cols_used", None):
            self._fill_hosts()

    def _fill_checks(self) -> None:
        self.checks_list.clear()
        checks = self.infra.store.checks()
        for c in checks:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(12, 6, 12, 6)
            h.setSpacing(12)
            h.addWidget(Dot(STATE_COLOR.get(c["state"] if c["enabled"] else "", Palette.text_faint)))
            col = QVBoxLayout()
            col.setSpacing(0)
            title = (f"{c['host_name']} · " if c["host_name"] else "") + c["name"]
            col.addWidget(label(title, 10.2, QFont.Weight.Medium, "" if c["enabled"] else "Faint"))
            target = c["target"] + (f":{c['port']}" if c["port"] else "")
            meta = f"{tr('infra_kind_' + c['kind'])} · {ltr(target)}"
            if c["last_detail"]:
                meta += f" · {c['last_detail']}"
            col.addWidget(label(meta, 8.6, role="Dim"))
            h.addLayout(col, 1)
            h.addWidget(Sparkline(list(self.infra.monitor.history.get(c["id"], []))))
            up = self.infra.monitor.uptime(c["id"])
            h.addWidget(label(ltr(f"{up:.0f}%") if up is not None else "—", 9.6, QFont.Weight.DemiBold))
            ms = label(ltr(f"{c['last_ms']:.0f} ms") if c["last_ms"] and c["state"] != "down" else "", 9.2, role="Dim")
            ms.setMinimumWidth(64)
            h.addWidget(ms)
            if c["last_change"]:
                h.addWidget(label(relative_time(c["last_change"]), 8.4, role="Faint"))
            item.setSizeHint(QSize(0, 56))
            self.checks_list.addItem(item)
            self.checks_list.setItemWidget(item, row)
        self.checks_empty.setVisible(not checks)
        self.checks_list.setVisible(bool(checks))

    def _fill_log(self) -> None:
        self.log_list.clear()
        for e in self.infra.store.log_entries():
            item = QListWidgetItem()
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(12, 2, 12, 2)
            h.setSpacing(10)
            h.addWidget(Dot(Palette.success if e["ok"] else Palette.danger, 6))
            h.addWidget(label(ltr(e["created_at"][5:16]), 8.6, role="Faint"))
            h.addWidget(label(tr("infra_act_" + e["action"]) if tr("infra_act_" + e["action"]) != "infra_act_" + e["action"]
                              else e["action"], 9.4, QFont.Weight.Medium))
            if e["host_name"]:
                h.addWidget(label(e["host_name"], 9.2, role="Dim"))
            d = label(e["detail"][:140], 8.8, role="Dim")
            h.addWidget(d, 1)
            item.setSizeHint(QSize(0, 34))
            self.log_list.addItem(item)
            self.log_list.setItemWidget(item, row)

    # ----------------------------------------------------------- actions
    def show_tab(self, key: str) -> None:
        if key in self.views:
            self.tabs.set_current(key)
            self.stack.switch_to(self.stack.indexOf(self.views[key]), animate=self.isVisible())
            if key == "nginx":
                self.nginx.on_shown()
            elif key == "proxmox" and not self.pve.guests:
                self.pve.reload(quiet=True)

    def refresh_all(self) -> None:
        hosts = [h for h in self.infra.store.hosts() if h["auth"] != "none"]
        if any(h["auth"] == "password" for h in hosts) and not self.ctl.ensure_vault_unlocked(self):
            hosts = [h for h in hosts if h["auth"] == "key"]
        for h in hosts:
            self.infra.refresh_status(h["id"], self, quiet=True)
        self.infra.monitor.run_now()

    def host_menu(self, host_id: int, pos) -> None:
        host = self.infra.store.host(host_id)
        if not host:
            return
        menu = make_menu(self)
        add_action(menu, Icons.EDIT, tr("edit"), lambda: self.edit_host(host_id))
        if host["auth"] == "password":
            add_action(menu, Icons.KEY, tr("infra_switch_key"), lambda: self.infra.switch_to_key(host_id, self))
        add_action(menu, Icons.SYNC, tr("infra_add_basic_checks"),
                   lambda: self.ctl.notify(tr("infra_checks_added", n=self.infra.add_basic_checks(host_id)), "success"))
        add_action(menu, Icons.COPY, tr("infra_copy_address"), lambda: self.ctl.copy_text(host["address"]))
        add_action(menu, Icons.COPY, tr("infra_copy_ssh"),
                   lambda: self.ctl.copy_text(ssh.command_text(host)))
        if host["host_key"]:
            add_action(menu, Icons.SHIELD, tr("infra_forget_key") + "  (" + ssh.fingerprint(host["host_key"])[:20] + "…)",
                       lambda: self.infra.forget_host_key(host_id))
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("infra_delete_host"), lambda: self.infra.delete_host(host_id, self), danger=True)
        menu.exec(pos)

    def _check_menu(self, pos) -> None:
        item = self.checks_list.itemAt(pos)
        if not item:
            return
        cid = item.data(Qt.ItemDataRole.UserRole)
        check = self.infra.store.check(cid)
        menu = make_menu(self)
        add_action(menu, Icons.PLAY, tr("infra_run_now"), lambda: self.infra.monitor.run_now(cid))
        add_action(menu, Icons.EDIT, tr("edit"), lambda: self.edit_check(cid))
        add_action(menu, Icons.EYE_OFF if check["enabled"] else Icons.EYE,
                   tr("infra_pause") if check["enabled"] else tr("infra_resume"),
                   lambda: self.infra.save_check({"enabled": 0 if check["enabled"] else 1, "state": ""}, cid))
        menu.addSeparator()
        add_action(menu, Icons.DELETE, tr("delete"), lambda: self.infra.delete_check(cid), danger=True)
        menu.exec(self.checks_list.viewport().mapToGlobal(pos))

    def edit_host(self, host_id: int | None) -> None:
        host = self.infra.store.host(host_id) if host_id else None
        dlg = HostDialog(self.infra.store.groups(), host, self)
        if not dlg.exec():
            return
        data, password, basic = dlg.result_data()
        new_id = self.infra.save_host(data, password, host_id, self)
        if new_id and basic and not host_id:
            self.infra.add_basic_checks(new_id)

    def edit_check(self, check_id: int | None) -> None:
        check = self.infra.store.check(check_id) if check_id else None
        dlg = CheckDialog(self.infra.store.hosts(), check, self)
        if dlg.exec():
            self.infra.save_check(dlg.result_data(), check_id)


# ------------------------------------------------------------------ dialogs
class HostDialog(StyledDialog):
    def __init__(self, groups: list[str], host: dict | None, parent=None):
        super().__init__(tr("infra_edit_host") if host else tr("infra_add_host"), parent, 560)
        h = host or {}
        self.body.addWidget(field(tr("infra_name")))
        self.name = QLineEdit(h.get("name", ""))
        self.name.setPlaceholderText(tr("infra_name_ph"))
        self.body.addWidget(self.name)

        self.body.addWidget(field(tr("infra_kind")))
        row = QHBoxLayout()
        self.kind = QButtonGroup(self)
        for k in ssh.KINDS:
            b = chip(tr("infra_type_" + k))
            b.setProperty("key", k)
            b.setChecked(k == h.get("kind", "linux"))
            self.kind.addButton(b)
            row.addWidget(b)
        row.addStretch(1)
        self.body.addLayout(row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.addWidget(field(tr("infra_address")), 0, 0)
        grid.addWidget(field(tr("infra_port")), 0, 1)
        self.address = QLineEdit(h.get("address", ""))
        self.address.setPlaceholderText("192.168.1.10")
        self.address.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        grid.addWidget(self.address, 1, 0)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(h.get("ssh_port", 22)))
        grid.addWidget(self.port, 1, 1)
        grid.addWidget(field(tr("infra_user")), 2, 0)
        grid.addWidget(field(tr("infra_group")), 2, 1)
        self.user = QLineEdit(h.get("username", ""))
        self.user.setPlaceholderText("root / ubuntu")
        self.user.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        grid.addWidget(self.user, 3, 0)
        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.addItem("")
        self.group.addItems(groups)
        self.group.setCurrentText(h.get("grp", ""))
        grid.addWidget(self.group, 3, 1)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)

        self.body.addWidget(field(tr("infra_auth")))
        arow = QHBoxLayout()
        self.auth = QButtonGroup(self)
        for k in ("password", "key", "none"):
            b = chip(tr("infra_auth_" + k))
            b.setProperty("key", k)
            b.setChecked(k == h.get("auth", "password"))
            b.toggled.connect(lambda _: self._auth_changed())
            self.auth.addButton(b)
            arow.addWidget(b)
        arow.addStretch(1)
        self.body.addLayout(arow)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(tr("infra_password_keep") if h.get("vault_id") else tr("infra_password_ph"))
        self.body.addWidget(self.password)
        self.key_row = QWidget()
        kr = QHBoxLayout(self.key_row)
        kr.setContentsMargins(0, 0, 0, 0)
        self.key = QLineEdit(h.get("key_path", "") or (str(ssh.KEY_PATH) if ssh.KEY_PATH.exists() else ""))
        self.key.setPlaceholderText(tr("infra_key_ph"))
        kr.addWidget(self.key, 1)
        browse = button(tr("browse"), role="Ghost")
        browse.clicked.connect(self._browse)
        kr.addWidget(browse)
        self.body.addWidget(self.key_row)
        self.secure_note = label(tr("infra_password_note"), 8.4, role="Faint", wrap=True)
        self.body.addWidget(self.secure_note)

        self.body.addWidget(field(tr("infra_web_url")))
        self.web = QLineEdit(h.get("web_url", ""))
        self.web.setPlaceholderText("https://192.168.1.10:8006")
        self.web.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.body.addWidget(self.web)
        opts = QHBoxLayout()
        self.rdp = QCheckBox(tr("infra_rdp_enable"))
        self.rdp.setChecked(bool(h.get("rdp", 0)))
        opts.addWidget(self.rdp)
        self.basic = QCheckBox(tr("infra_basic_checks"))
        self.basic.setChecked(host is None)
        self.basic.setVisible(host is None)
        opts.addWidget(self.basic)
        opts.addStretch(1)
        self.body.addLayout(opts)
        crow = QHBoxLayout()
        self.colors = QButtonGroup(self)
        from app.ui.dialogs.study_dialogs import ColorDot
        for c in COLORS:
            dot = ColorDot(c)
            dot.setChecked(c == h.get("color", COLORS[0]))
            self.colors.addButton(dot)
            crow.addWidget(dot)
        crow.addStretch(1)
        self.body.addLayout(crow)
        self.notes = QPlainTextEdit(h.get("notes", ""))
        self.notes.setPlaceholderText(tr("infra_notes_ph"))
        self.notes.setFixedHeight(60)
        self.body.addWidget(self.notes)
        self.error = label("", 9)
        self.error.setStyleSheet(f"color: {Palette.danger};")
        self.body.addWidget(self.error)
        self.add_buttons(tr("save"))
        self._auth_changed()

    def _sel(self, group: QButtonGroup) -> str:
        b = group.checkedButton()
        return b.property("key") if b else ""

    def _auth_changed(self) -> None:
        auth = self._sel(self.auth)
        self.password.setVisible(auth == "password")
        self.secure_note.setVisible(auth == "password")
        self.key_row.setVisible(auth == "key")
        self.adjustSize()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("infra_key_ph"), str(ssh.KEY_PATH.parent))
        if path:
            self.key.setText(path)

    def validate(self) -> bool:
        if not self.name.text().strip() or not self.address.text().strip():
            self.error.setText(tr("infra_err_required"))
            return False
        if self._sel(self.auth) != "none" and not self.user.text().strip():
            self.error.setText(tr("infra_err_user"))
            return False
        return True

    def result_data(self) -> tuple[dict, str | None, bool]:
        color = self.colors.checkedButton().color if self.colors.checkedButton() else COLORS[0]
        data = {"name": self.name.text().strip(), "kind": self._sel(self.kind) or "linux",
                "grp": self.group.currentText().strip(), "address": self.address.text().strip(),
                "ssh_port": self.port.value(), "username": self.user.text().strip(),
                "auth": self._sel(self.auth) or "password", "key_path": self.key.text().strip(),
                "web_url": self.web.text().strip(), "rdp": int(self.rdp.isChecked()),
                "notes": self.notes.toPlainText().strip(), "color": color}
        return data, self.password.text() or None, self.basic.isChecked()


class CheckDialog(StyledDialog):
    def __init__(self, hosts: list[dict], check: dict | None, parent=None):
        super().__init__(tr("infra_edit_check") if check else tr("infra_add_check"), parent, 480)
        c = check or {}
        self.body.addWidget(field(tr("infra_host")))
        self.host = QComboBox()
        self.host.addItem(tr("infra_no_host"), None)
        for h in hosts:
            self.host.addItem(h["name"], h["id"])
        self.host.setCurrentIndex(max(0, self.host.findData(c.get("host_id"))))
        self.host.currentIndexChanged.connect(self._host_changed)
        self.hosts = {h["id"]: h for h in hosts}
        self.body.addWidget(self.host)
        self.body.addWidget(field(tr("infra_kind")))
        row = QHBoxLayout()
        self.kind = QButtonGroup(self)
        for k in ("ping", "tcp", "http", "tls"):
            b = chip(tr("infra_kind_" + k))
            b.setProperty("key", k)
            b.setChecked(k == c.get("kind", "ping"))
            b.toggled.connect(lambda _: self._kind_changed())
            self.kind.addButton(b)
            row.addWidget(b)
        row.addStretch(1)
        self.body.addLayout(row)
        self.body.addWidget(field(tr("infra_name")))
        self.name = QLineEdit(c.get("name", ""))
        self.body.addWidget(self.name)
        self.target_label = field(tr("infra_target"))
        self.body.addWidget(self.target_label)
        trow = QHBoxLayout()
        self.target = QLineEdit(c.get("target", ""))
        self.target.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        trow.addWidget(self.target, 1)
        self.port = QSpinBox()
        self.port.setRange(0, 65535)
        self.port.setValue(int(c.get("port", 0)))
        trow.addWidget(self.port)
        self.body.addLayout(trow)
        orow = QHBoxLayout()
        orow.addWidget(label(tr("infra_interval"), 9.5, role="Dim"))
        self.interval = QComboBox()
        for sec in (30, 60, 300, 900, 3600):
            self.interval.addItem(tr("infra_every_s", n=sec) if sec < 60 else tr("infra_every_m", n=sec // 60), sec)
        self.interval.setCurrentIndex(max(0, self.interval.findData(int(c.get("interval_sec", 60)))))
        orow.addWidget(self.interval)
        orow.addStretch(1)
        self.body.addLayout(orow)
        self.insecure = QCheckBox(tr("infra_insecure"))
        self.insecure.setChecked(bool(c.get("insecure", 0)))
        self.body.addWidget(self.insecure)
        wrow = QHBoxLayout()
        self.warn_label = label(tr("infra_warn_days"), 9.5, role="Dim")
        wrow.addWidget(self.warn_label)
        self.warn = QSpinBox()
        self.warn.setRange(1, 120)
        self.warn.setValue(int(c.get("warn_days", 14)))
        wrow.addWidget(self.warn)
        wrow.addStretch(1)
        self.body.addLayout(wrow)
        self.error = label("", 9)
        self.error.setStyleSheet(f"color: {Palette.danger};")
        self.body.addWidget(self.error)
        self.add_buttons(tr("save"))
        self._kind_changed()

    def _sel(self) -> str:
        b = self.kind.checkedButton()
        return b.property("key") if b else "ping"

    def _kind_changed(self) -> None:
        k = self._sel()
        self.port.setVisible(k in ("tcp", "tls"))
        self.insecure.setVisible(k in ("http", "tls"))
        self.warn.setVisible(k == "tls")
        self.warn_label.setVisible(k == "tls")
        self.target.setPlaceholderText({"ping": "192.168.1.10", "tcp": "192.168.1.10", "http": "https://example.com",
                                        "tls": "example.com"}[k])
        self._host_changed()

    def _host_changed(self, *_):
        host = self.hosts.get(self.host.currentData())
        if host and not self.target.text().strip():
            k = self._sel()
            self.target.setText(host["web_url"] if k in ("http", "tls") and host["web_url"] else host["address"])
            if k == "tcp" and not self.port.value():
                self.port.setValue(int(host["ssh_port"] or 22))

    def validate(self) -> bool:
        if not self.target.text().strip():
            self.error.setText(tr("infra_err_target"))
            return False
        if self._sel() == "tcp" and not self.port.value():
            self.error.setText(tr("infra_err_port"))
            return False
        return True

    def result_data(self) -> dict:
        k = self._sel()
        name = self.name.text().strip() or {"ping": "Ping", "tcp": f"Port {self.port.value()}", "http": "Web",
                                            "tls": tr("infra_kind_tls")}[k]
        return {"host_id": self.host.currentData(), "name": name, "kind": k, "target": self.target.text().strip(),
                "port": self.port.value() if k in ("tcp", "tls") else 0, "interval_sec": self.interval.currentData(),
                "insecure": int(self.insecure.isChecked()), "warn_days": self.warn.value(), "state": ""}


class TrustDialog(StyledDialog):
    """First connection: show the server's fingerprint. Changed key: a clear warning, refuse by default."""

    def __init__(self, host: dict, err: ssh.HostKeyError, parent=None):
        super().__init__(tr("infra_trust_changed") if err.changed else tr("infra_trust_new"), parent, 520)
        text = tr("infra_trust_changed_msg", name=host["name"]) if err.changed else \
            tr("infra_trust_new_msg", name=host["name"], address=host["address"])
        msg = label(text, 9.6, wrap=True)
        if err.changed:
            msg.setStyleSheet(f"color: {Palette.danger};")
        self.body.addWidget(msg)
        for title, value in ((tr("infra_fingerprint"), err.fingerprint),
                             *(((tr("infra_old_fingerprint"), err.old_fingerprint),) if err.changed else ())):
            self.body.addWidget(field(title))
            fp = QLineEdit(value)
            fp.setReadOnly(True)
            fp.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            fp.setFont(ui_font(9.5))
            self.body.addWidget(fp)
        self.body.addWidget(label(tr("infra_trust_hint"), 8.6, role="Faint", wrap=True))
        self.add_buttons(tr("infra_trust_ok") if not err.changed else tr("infra_trust_replace"),
                         danger=err.changed)

    @classmethod
    def ask(cls, host: dict, err: ssh.HostKeyError, parent=None) -> bool:
        return cls(host, err, parent).exec() == StyledDialog.DialogCode.Accepted
