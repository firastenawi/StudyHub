"""
Plugins: optional sections that extend Study Hub without touching its core.

A plugin is a Python package with
    PLUGIN = {"id": "...", "name": {"en": .., "ar": ..}, "description": {"en": .., "ar": ..},
              "version": "1.0.0", "glyph": "\\uE...", "default_enabled": True}
    def register(api: PluginAPI) -> None
and lives either inside the app (app/plugins/<id>, listed in app.plugins.BUILTIN) or in
%APPDATA%\\StudyHub\\plugins\\<id>\\ (third-party; off until the user turns it on — a plugin runs with
the same rights as Study Hub, so only add ones you trust).

Through the API a plugin can add sidebar pages, search-palette commands, tray actions and
translations, keep its own database tables (versioned migrations) and settings, and register
clean-up callbacks. A plugin that fails to load is reported in Settings › Plugins and never stops the app.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.config import DATA_DIR
from app.core import i18n
from app.core.logs import log

PLUGIN_DIR = DATA_DIR / "plugins"


@dataclass
class PluginInfo:
    id: str
    meta: dict
    builtin: bool
    source: str                      # module name (built-in) or folder (external)
    enabled: bool = False
    loaded: bool = False
    error: str = ""
    api: "PluginAPI | None" = None

    def text(self, key: str) -> str:
        value = self.meta.get(key, "")
        if isinstance(value, dict):
            return value.get(i18n.language()) or value.get("en") or ""
        return str(value)


@dataclass
class PluginAPI:
    """What a plugin may use. Everything it adds is removed again when it is turned off."""
    manager: "PluginManager"
    plugin_id: str
    pages: list = field(default_factory=list)       # (key, glyph, title_key, factory(ctl, main_window))
    commands: list = field(default_factory=list)    # (glyph, title_key, action)
    tray_actions: list = field(default_factory=list)
    _unload: list = field(default_factory=list)

    # app objects
    @property
    def ctl(self):
        return self.manager.ctl

    @property
    def db(self):
        return self.manager.ctl.db

    # UI
    def add_page(self, key: str, glyph: str, title_key: str, factory: Callable) -> None:
        self.pages.append((key, glyph, title_key, factory))

    def add_command(self, glyph: str, title_key: str, action: Callable) -> None:
        self.commands.append((glyph, title_key, action))

    def add_tray_action(self, glyph: str, title_key: str, action: Callable) -> None:
        self.tray_actions.append((glyph, title_key, action))

    def add_strings(self, en: dict, ar: dict) -> None:
        i18n.STRINGS["en"].update(en)
        i18n.STRINGS["ar"].update(ar)

    def on_unload(self, callback: Callable) -> None:
        self._unload.append(callback)

    # data
    def setting(self, key: str, default=None):
        return self.db.get_setting(f"plugin.{self.plugin_id}.{key}", default)

    def set_setting(self, key: str, value) -> None:
        self.db.set_setting(f"plugin.{self.plugin_id}.{key}", value)

    def migrate(self, migrations: list[list[str]]) -> None:
        """migrations[i] = SQL statements that bring the plugin's tables to version i+1 (run once, in order)."""
        current = int(self.setting("schema", 0))
        for version, statements in enumerate(migrations, start=1):
            if version <= current:
                continue
            with self.db.conn:
                for sql in statements:
                    self.db.conn.execute(sql)
            self.set_setting("schema", version)
            log.info("plugin %s: database schema v%s", self.plugin_id, version)

    def notify(self, text: str, kind: str = "info") -> None:
        self.ctl.notify(text, kind)


class PluginManager:
    def __init__(self, ctl):
        self.ctl = ctl
        self.plugins: dict[str, PluginInfo] = {}

    # ------------------------------------------------------------ discovery
    def discover(self) -> None:
        from app.plugins import BUILTIN
        found: dict[str, PluginInfo] = {}
        for pid in BUILTIN:
            module_name = f"app.plugins.{pid}"
            try:
                module = importlib.import_module(module_name)
                found[pid] = PluginInfo(pid, dict(module.PLUGIN), True, module_name)
            except Exception as exc:
                found[pid] = PluginInfo(pid, {"name": pid}, True, module_name, error=f"{exc!r}")
        if PLUGIN_DIR.is_dir():
            for folder in sorted(PLUGIN_DIR.iterdir()):
                init = folder / "__init__.py"
                if not init.is_file() or folder.name in found:
                    continue
                found[folder.name] = PluginInfo(folder.name, {"name": folder.name}, False, str(folder))
                try:  # read the manifest without running the plugin: it only runs once enabled
                    meta = _read_manifest(init)
                    if meta:
                        found[folder.name].meta = meta
                except Exception as exc:
                    found[folder.name].error = f"{exc!r}"
        enabled = self.ctl.db.get_setting("plugins_enabled", None)
        for info in found.values():
            if enabled is None or info.id not in enabled and info.id not in self.ctl.db.get_setting("plugins_seen", []):
                info.enabled = bool(info.meta.get("default_enabled", False)) and info.builtin
            else:
                info.enabled = info.id in (enabled or [])
        self.plugins = found
        self.ctl.db.set_setting("plugins_seen", sorted(found))
        self._save_enabled()

    def _save_enabled(self) -> None:
        self.ctl.db.set_setting("plugins_enabled", sorted(p.id for p in self.plugins.values() if p.enabled))

    # -------------------------------------------------------------- loading
    def load_enabled(self) -> None:
        for info in self.plugins.values():
            if info.enabled and not info.loaded:
                self._load(info)

    def _load(self, info: PluginInfo) -> bool:
        api = PluginAPI(self, info.id)
        try:
            if info.builtin:
                module = importlib.import_module(info.source)
            else:
                name = f"studyhub_plugin_{info.id}"
                spec = importlib.util.spec_from_file_location(name, Path(info.source) / "__init__.py",
                                                              submodule_search_locations=[info.source])
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
            module.register(api)
        except Exception:
            info.error = traceback.format_exc(limit=4)
            info.loaded = False
            log.error("plugin %s failed to load:\n%s", info.id, info.error)
            return False
        info.api, info.loaded, info.error = api, True, ""
        log.info("plugin %s loaded", info.id)
        return True

    def _unload(self, info: PluginInfo) -> None:
        if info.api:
            for callback in info.api._unload:
                try:
                    callback()
                except Exception:
                    log.exception("plugin %s: unload callback failed", info.id)
        info.api, info.loaded = None, False

    def set_enabled(self, plugin_id: str, on: bool) -> bool:
        info = self.plugins.get(plugin_id)
        if not info:
            return False
        if on and not info.loaded and not self._load(info):
            return False
        if not on:
            self._unload(info)
        info.enabled = on
        self._save_enabled()
        return True

    def shutdown(self) -> None:
        for info in self.plugins.values():
            if info.loaded:
                self._unload(info)

    # ------------------------------------------------------------ contents
    def _apis(self):
        return [p.api for p in self.plugins.values() if p.loaded and p.api]

    def pages(self) -> list:
        return [page for api in self._apis() for page in api.pages]

    def commands(self) -> list:
        return [cmd for api in self._apis() for cmd in api.commands]

    def tray_actions(self) -> list:
        return [a for api in self._apis() for a in api.tray_actions]


def _read_manifest(init: Path) -> dict:
    """PLUGIN = {...} literal from an external plugin's __init__.py, without executing the file."""
    import ast
    tree = ast.parse(init.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "PLUGIN" for t in node.targets):
            return ast.literal_eval(node.value)
    return {}
