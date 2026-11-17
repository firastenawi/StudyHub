"""System-tray icon: Study Hub keeps running in the background."""
from __future__ import annotations

from PyQt6.QtWidgets import QSystemTrayIcon

from app.config import APP_NAME
from app.core.i18n import tr
from app.ui.theme import Icons
from app.ui.widgets.common import add_action, make_menu


class TrayIcon(QSystemTrayIcon):
    def __init__(self, ctl):
        super().__init__(ctl.icon, ctl)
        self.ctl = ctl
        self.setToolTip(APP_NAME)
        self.activated.connect(self._on_activated)
        self.rebuild_menu()
        self.show()

    def rebuild_menu(self) -> None:
        ctl = self.ctl
        menu = make_menu()
        add_action(menu, Icons.HOME, tr("tray_open"), ctl.show_window, ctl.hotkey_text("toggle_window"))
        menu.addSeparator()
        add_action(menu, Icons.NOTES, tr("quick_note"), ctl.open_quick_note, ctl.hotkey_text("quick_note"))
        add_action(menu, Icons.PASTE, tr("note_from_clipboard"), ctl.clipboard_note)
        add_action(menu, Icons.LINK, tr("link_from_clipboard"), ctl.clipboard_link, ctl.hotkey_text("clipboard_link"))
        add_action(menu, Icons.PASTE, tr("hk_paste_to_desktop"), ctl.paste_hotkey, ctl.hotkey_text("paste_to_desktop"))
        add_action(menu, Icons.SEARCH, tr("open_palette"), ctl.open_palette, ctl.hotkey_text("command_palette"))
        add_action(menu, Icons.PASTE, tr("hk_clipboard_history"), ctl.open_clipboard_history,
                   ctl.hotkey_text("clipboard_history"))
        add_action(menu, Icons.PREVIEW, tr("hk_screenshot"), ctl.take_screenshot, ctl.hotkey_text("screenshot"))
        add_action(menu, Icons.CHECKLIST, tr("lab_start"), lambda: ctl.lab.start())
        add_action(menu, Icons.DOCUMENT, tr("pdf_app_name"), lambda: ctl.open_pdf())
        menu.addSeparator()
        add_action(menu, Icons.FOLDER, tr("explorer"), lambda: ctl.open_explorer(), ctl.hotkey_text("explorer"))
        add_action(menu, Icons.PHONE, tr("nav_phone"), lambda: ctl.open_phone_page())
        add_action(menu, Icons.FORWARD, tr("phone_send_clipboard"), ctl.send_clipboard_to_phone)
        add_action(menu, Icons.BELL, tr("new_alarm"), ctl.add_alarm_dialog)
        add_action(menu, Icons.LOCK, tr("hk_lock_screen"), ctl.show_lock_screen, ctl.hotkey_text("lock_screen"))
        plugin_actions = ctl.plugins.tray_actions()
        if plugin_actions:
            menu.addSeparator()
            for glyph, key, action in plugin_actions:
                add_action(menu, glyph, tr(key), action)
        menu.addSeparator()
        add_action(menu, Icons.SETTINGS, tr("nav_settings"), lambda: ctl.show_window("settings"))
        add_action(menu, Icons.POWER, tr("quit"), ctl.quit, danger=True)
        self.setContextMenu(menu)
        self._menu = menu  # keep a reference

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.ctl.toggle_window()
