"""
Infrastructure plugin — your servers in one place.

Phase 1: hosts in groups (Linux / Proxmox / Docker / VMware / Windows), SSH terminal (Windows Terminal)
and RDP, passwords kept in the encrypted vault with one-click "switch to an SSH key", trust-on-first-use
host keys, live status cards over SSH, service monitoring (ping / port / HTTP / certificate expiry) with
alerts, and an operations log.
Phase 2: Proxmox (API: nodes, VMs / containers, power, snapshots, console; pinned certificate, token in the
vault), nginx over SSH (sites, enable / disable, test + reload, logs, certificates, templates, certbot) and a
command library run on several servers at once.
"""
from __future__ import annotations

PLUGIN = {
    "id": "infra",
    "name": {"en": "Infrastructure", "ar": "البنية التحتية"},
    "description": {"en": "Servers, SSH / RDP, monitoring, Proxmox, nginx and a command library.",
                    "ar": "السيرفرات، SSH وRDP، المراقبة، Proxmox وnginx ومكتبة أوامر."},
    "version": "1.1.0",
    "glyph": "",
    "default_enabled": True,
}


def register(api) -> None:
    from app.plugins.infra.controller import InfraController
    from app.plugins.infra.store import MIGRATIONS
    from app.plugins.infra.strings import AR, EN

    api.add_strings(EN, AR)
    api.migrate(MIGRATIONS)
    infra = InfraController(api)

    def page_factory(ctl, main_window):
        from app.plugins.infra.page import InfraPage
        return InfraPage(ctl, main_window, infra)

    api.add_page("infra", PLUGIN["glyph"], "nav_infra", page_factory)
    api.add_command(PLUGIN["glyph"], "nav_infra", lambda: api.ctl.show_window("infra"))
    api.add_tray_action(PLUGIN["glyph"], "nav_infra", lambda: api.ctl.show_window("infra"))
    infra.start()
    api.on_unload(infra.stop)
    api.ctl.infra = infra  # handy for tests / other plugins
