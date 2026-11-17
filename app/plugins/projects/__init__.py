"""
Projects plugin — your code projects in one place: Visual Studio solutions, .NET, Node, Python, Docker…
with their Git state (branch, changes, ahead / behind, last commit) and one-click Visual Studio / VS Code /
folder / terminal, Pull and Fetch.
"""
from __future__ import annotations

PLUGIN = {
    "id": "projects",
    "name": {"en": "Code projects", "ar": "مشاريعي البرمجية"},
    "description": {"en": "Your code folders with Git state; open in Visual Studio / VS Code, pull, fetch.",
                    "ar": "مجلدات مشاريعك مع حالة Git؛ فتح بـ Visual Studio / VS Code، وPull وFetch."},
    "version": "1.0.0",
    "glyph": "",
    "default_enabled": True,
}


def register(api) -> None:
    from app.plugins.projects.strings import AR, EN
    api.add_strings(EN, AR)

    def page_factory(ctl, main_window):
        from app.plugins.projects.page import ProjectsPage
        return ProjectsPage(ctl, main_window, api)

    api.add_page("projects", PLUGIN["glyph"], "nav_projects", page_factory)
    api.add_command(PLUGIN["glyph"], "nav_projects", lambda: api.ctl.show_window("projects"))
