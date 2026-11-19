"""
Phone > Home: the dashboard for the paired phone.

  device card     the phone with its battery, name, Android version, network and charging
  controls        DND, torch, Wi-Fi, Bluetooth, ringer, media volume
  quick actions   mirror the screen, send files, send the clipboard, find the phone
  notifications   the phone's notifications: reply, mark as read, mute the app, dismiss
  timeline        the latest things that moved between the phone and the laptop
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QVBoxLayout,
                             QWidget)

from app.core.i18n import ltr, relative_time, tr
from app.ui.pages.base import Page
from app.ui.theme import Icons, Palette, Theme, icon_font, qcolor, ui_font
from app.ui.widgets.common import (Card, GlyphLabel, GlyphPlate, IconButton, add_action, button, label, make_menu,
                                   primary_button)


# ------------------------------------------------------------------------------------------------ the phone
class PhoneArt(QWidget):
    """A drawn phone with a battery ring on its screen."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(108, 176)
        self.level: int | None = None
        self.charging = False
        self.online = False

    def set_state(self, level, charging: bool, online: bool) -> None:
        self.level, self.charging, self.online = level, charging, online
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(6, 4, -6, -4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qcolor(Theme.accent(), 0.18))
        p.drawRoundedRect(r.adjusted(-4, -2, 4, 2), 22, 22)
        p.setBrush(QColor("#17181F"))
        p.drawRoundedRect(r, 18, 18)
        screen = r.adjusted(5, 5, -5, -5)
        p.setBrush(QColor(Theme.accent()).darker(170) if self.online else QColor("#2A2C36"))
        p.drawRoundedRect(screen, 14, 14)
        p.setBrush(QColor(0, 0, 0, 136))
        p.drawRoundedRect(QRectF(screen.center().x() - 4, screen.top() + 6, 8, 8), 4, 4)  # camera
        ring = QRectF(0, 0, 58, 58)
        ring.moveCenter(screen.center())
        pen = QPen(QColor(255, 255, 255, 48), 5)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(ring)
        if self.level is not None:
            color = "#22C55E" if self.charging or self.level > 20 else "#EF4444"
            pen.setColor(QColor(color))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(ring, 90 * 16, -int(360 * 16 * self.level / 100))
        p.setPen(QColor("#ffffff"))
        p.setFont(ui_font(10.5, QFont.Weight.Bold))
        p.drawText(ring, Qt.AlignmentFlag.AlignCenter, ltr(f"{self.level}%") if self.level is not None else "—")
        if self.charging:
            f = icon_font()
            f.setPixelSize(12)
            p.setFont(f)
            p.drawText(QRectF(ring.left(), ring.top() + 4, ring.width(), 16), Qt.AlignmentFlag.AlignCenter, Icons.LIGHTNING)
        p.setBrush(QColor(255, 255, 255, 85))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(screen.center().x() - 16, screen.bottom() - 9, 32, 3), 1.5, 1.5)


def side_label(text: str, size: float, weight=QFont.Weight.Normal, role: str = "", wrap: bool = False) -> QLabel:
    """A label that stays on the reading side of the UI even when its text is in the other language."""
    lab = label(text, size, weight, role=role, wrap=wrap)
    rtl = lab.isRightToLeft()
    lab.setAlignment(Qt.AlignmentFlag.AlignAbsolute | (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft)
                     | Qt.AlignmentFlag.AlignVCenter)
    return lab


def pill(glyph: str, text: str) -> QWidget:
    w = QFrame()
    w.setObjectName("PhonePill")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(12, 6, 14, 6)
    lay.setSpacing(7)
    lay.addWidget(GlyphLabel(glyph, 10.5, Palette.text))
    lay.addWidget(label(text, 9.5))
    w.setStyleSheet(f"#PhonePill {{ background: {qcolor(Palette.text, 0.07).name(QColor.NameFormat.HexArgb)};"
                    f" border-radius: 15px; }}")
    return w


# ------------------------------------------------------------------------------------------------ controls
class ControlTile(QPushButton):
    """A square toggle with a glyph and a caption (filled with the accent colour when on)."""

    def __init__(self, glyph: str, text: str):
        super().__init__()
        self.glyph, self.text_ = glyph, text
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(74, 66)
        self.setToolTip(text)

    def set_caption(self, text: str, glyph: str | None = None) -> None:
        self.text_ = text
        if glyph:
            self.glyph = glyph
        self.setToolTip(text)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        on = self.isChecked()
        if not self.isEnabled():
            bg = qcolor(Palette.text, 0.04)
        elif on:
            bg = QColor(Theme.accent())
        else:
            bg = qcolor(Palette.text, 0.14 if self.underMouse() else 0.08)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 16, 16)
        fg = QColor("#ffffff") if on and self.isEnabled() else QColor(Palette.text if self.isEnabled() else Palette.text_faint)
        f = icon_font()
        f.setPixelSize(19)
        p.setFont(f)
        p.setPen(fg)
        p.drawText(QRectF(r.left(), r.top() + 8, r.width(), 26), Qt.AlignmentFlag.AlignCenter, self.glyph)
        p.setFont(ui_font(8.4))
        p.drawText(QRectF(r.left() + 2, r.top() + 36, r.width() - 4, 22), Qt.AlignmentFlag.AlignCenter, self.text_)

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


class ActionTile(QPushButton):
    def __init__(self, glyph: str, text: str, color: str):
        super().__init__()
        self.setObjectName("PhoneAction")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(92)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)
        plate = GlyphPlate(glyph, color, 38)
        plate.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(plate)
        t = label(text, 10.5, QFont.Weight.DemiBold)
        t.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(t)


class AppButton(QPushButton):
    """A phone app: its icon and name; click opens it in its own window on the laptop."""

    def __init__(self, hub, app: dict):
        super().__init__()
        self.setObjectName("PhoneApp")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(84, 84)
        self.setToolTip(app["label"])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.setSpacing(4)
        pix = hub.icon(app["pkg"])
        if pix is not None:
            ic = QLabel()
            ic.setPixmap(pix.scaled(38, 38, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            ic = GlyphPlate(Icons.VIEW_GRID, Theme.accent(), 38)
        ic.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignHCenter)
        name = QLabel(self.fontMetrics().elidedText(app["label"], Qt.TextElideMode.ElideRight, 76))
        name.setFont(ui_font(8.4))
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(name)
        self.clicked.connect(lambda: hub.open_app(app["pkg"], app["label"]))


# ------------------------------------------------------------------------------------------------ notifications
class NotifRow(QFrame):
    def __init__(self, view: "PhoneHome", n: dict):
        super().__init__()
        self.view, self.n = view, n
        hub = view.ctl.phone_hub
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 8)
        lay.setSpacing(12)
        icon = QLabel()
        icon.setFixedSize(36, 36)
        pix = hub.icon(n["pkg"])
        if pix:
            icon.setPixmap(pix.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        else:
            lay.addWidget(GlyphPlate(Icons.BELL, Theme.accent(), 36), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(side_label(f"{n['app'] or n['pkg']}  ·  {relative_time(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(n['when'])))}",
                                 8.6, role="Dim"))
        if n["title"]:
            col.addWidget(side_label(n["title"], 10.5, QFont.Weight.DemiBold))
        if n["text"]:
            col.addWidget(side_label(n["text"][:400], 9.8, wrap=True))
        acts = QHBoxLayout()
        acts.setSpacing(6)
        if n["reply"]:
            rb = button(tr("phone_reply"), Icons.REPLY, role="Ghost")
            rb.clicked.connect(self._open_reply)
            acts.addWidget(rb)
        mr = button(tr("phone_mark_read"), role="Ghost")
        mr.clicked.connect(lambda: hub.mark_read(n["key"]))
        acts.addWidget(mr)
        mute = button(tr("phone_mute_app"), role="Ghost")
        mute.clicked.connect(lambda: hub.mute_app(n["pkg"]))
        acts.addWidget(mute)
        acts.addStretch(1)
        col.addLayout(acts)
        self.reply_row = QHBoxLayout()
        self.reply_box = QLineEdit()
        self.reply_box.setPlaceholderText(tr("phone_reply_ph"))
        self.reply_box.returnPressed.connect(self._send_reply)
        self.reply_box.hide()
        self.reply_send = IconButton(Icons.SEND, tr("phone_reply"), 32, 10.5)
        self.reply_send.clicked.connect(self._send_reply)
        self.reply_send.hide()
        self.reply_row.addWidget(self.reply_box, 1)
        self.reply_row.addWidget(self.reply_send)
        col.addLayout(self.reply_row)
        lay.addLayout(col, 1)
        x = IconButton(Icons.CLOSE, tr("phone_dismiss"), 28, 9)
        x.clicked.connect(lambda: hub.dismiss(n["key"]))
        lay.addWidget(x, 0, Qt.AlignmentFlag.AlignTop)

    def _open_reply(self) -> None:
        self.reply_box.show()
        self.reply_send.show()
        self.reply_box.setFocus()

    def _send_reply(self) -> None:
        text = self.reply_box.text().strip()
        if text:
            self.view.ctl.phone_hub.reply(self.n["key"], text)
            self.reply_box.clear()
            self.reply_box.hide()
            self.reply_send.hide()
            self.view.ctl.notify(tr("phone_reply_sent"), "success")


# ------------------------------------------------------------------------------------------------ the page
class PhoneHome(QWidget):
    def __init__(self, ctl, page):
        super().__init__()
        self.ctl, self.page = ctl, page
        hub = ctl.phone_hub
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        outer.addWidget(Page.scrollable(content))
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 6, 6)
        root.setSpacing(14)

        # ---- not connected yet
        self.empty = Card(padding=22)
        er = QHBoxLayout()
        er.setSpacing(18)
        er.addWidget(GlyphPlate(Icons.PHONE, Theme.accent(), 56), 0, Qt.AlignmentFlag.AlignTop)
        ec = QVBoxLayout()
        ec.addWidget(label(tr("phone_home_empty"), 13, QFont.Weight.DemiBold))
        ec.addWidget(label(tr("phone_home_empty_desc"), 9.5, role="Dim", wrap=True))
        go = primary_button(tr("phone_home_connect"), Icons.PHONE)
        go.clicked.connect(lambda: page.show_tab("devices"))
        ec.addWidget(go, 0, Qt.AlignmentFlag.AlignLeft)
        er.addLayout(ec, 1)
        self.empty.root.addLayout(er)
        root.addWidget(self.empty)

        # ---- row 1: device + controls
        top = QHBoxLayout()
        top.setSpacing(14)
        self.device_card = Card(padding=18)
        dl = QHBoxLayout()
        dl.setSpacing(18)
        self.art = PhoneArt()
        dl.addWidget(self.art)
        info = QVBoxLayout()
        info.setSpacing(6)
        self.name = label("", 17, QFont.Weight.DemiBold)
        info.addWidget(self.name)
        self.sub = label("", 10, role="Dim")
        info.addWidget(self.sub)
        self.pills = QVBoxLayout()
        self.pills.setSpacing(6)
        info.addLayout(self.pills)
        self.access_hint = label("", 8.8, wrap=True)
        self.access_hint.setStyleSheet(f"color: {Palette.warning};")
        info.addWidget(self.access_hint)
        info.addStretch(1)
        dl.addLayout(info, 1)
        self.device_card.root.addLayout(dl)
        top.addWidget(self.device_card, 3)

        self.controls = Card(tr("phone_controls"), Icons.SETTINGS, padding=18)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.t_dnd = ControlTile(Icons.MOON, tr("phone_ctl_dnd"))
        self.t_dnd.clicked.connect(lambda on: hub.set_dnd(on))
        self.t_torch = ControlTile(Icons.FLASHLIGHT, tr("phone_ctl_torch"))
        self.t_torch.clicked.connect(lambda on: hub.set_torch(on))
        self.t_wifi = ControlTile(Icons.WIFI, tr("sys_wifi"))
        self.t_wifi.clicked.connect(lambda on: hub.set_radio("wifi", on))
        self.t_bt = ControlTile(Icons.BLUETOOTH, "Bluetooth")
        self.t_bt.clicked.connect(lambda on: hub.set_radio("bt", on))
        self.t_ring = ControlTile(Icons.RINGER, tr("phone_ring_normal"))
        self.t_ring.setCheckable(False)
        self.t_ring.clicked.connect(self._cycle_ringer)
        for i, t in enumerate((self.t_dnd, self.t_torch, self.t_wifi, self.t_bt, self.t_ring)):
            grid.addWidget(t, 0, i)
        self.controls.root.addLayout(grid)
        vol = QHBoxLayout()
        vol.addWidget(GlyphLabel(Icons.VOLUME, 12, Palette.text_dim))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.volume.sliderReleased.connect(lambda: hub.set_volume(self.volume.value()))
        vol.addWidget(self.volume, 1)
        self.vol_label = label("", 9, role="Dim")
        self.vol_label.setMinimumWidth(38)
        vol.addWidget(self.vol_label)
        self.volume.valueChanged.connect(lambda v: self.vol_label.setText(ltr(f"{v}%")))
        self.controls.root.addLayout(vol)
        top.addWidget(self.controls, 2)
        self.top_row = QWidget()
        self.top_row.setLayout(top)
        top.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.top_row)

        # ---- row 2: quick actions
        actions = QHBoxLayout()
        actions.setSpacing(12)
        for glyph, key, color, fn in (
                (Icons.PREVIEW, "phone_act_mirror", "#22D3EE", lambda: ctl.phone_mirror().start()),
                (Icons.SEND, "phone_act_files", "#A78BFA", lambda: ctl.send_files_to_phone_dialog(self)),
                (Icons.PASTE, "phone_act_clipboard", "#34D399", ctl.send_clipboard_to_phone),
                (Icons.RINGER, "phone_act_find", "#F59E0B", self._find)):
            t = ActionTile(glyph, tr(key), color)
            t.clicked.connect(fn)
            actions.addWidget(t)
        self.actions_row = QWidget()
        self.actions_row.setLayout(actions)
        actions.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.actions_row)

        # ---- row 3: phone apps + type on phone / screen
        mid = QHBoxLayout()
        mid.setSpacing(14)
        self.apps_card = Card(tr("phone_apps"), Icons.VIEW_GRID, padding=18)
        self.apps_card.header.addStretch(1)
        self.app_search = QLineEdit()
        self.app_search.setPlaceholderText(tr("phone_apps_search"))
        self.app_search.setClearButtonEnabled(True)
        self.app_search.setFixedWidth(200)
        self.app_search.textChanged.connect(lambda _: self.refresh_apps())
        self.apps_card.header.addWidget(self.app_search)
        reload_apps = IconButton(Icons.REFRESH, tr("phone_apps_reload"), 30, 10)
        reload_apps.clicked.connect(lambda: hub.request_apps(force=True))
        self.apps_card.header.addWidget(reload_apps)
        self.apps_grid = QGridLayout()
        self.apps_grid.setSpacing(6)
        self.apps_card.root.addLayout(self.apps_grid)
        self.apps_hint = label("", 8.8, role="Faint", wrap=True)
        self.apps_card.root.addWidget(self.apps_hint)
        mid.addWidget(self.apps_card, 3)
        side = QVBoxLayout()
        side.setSpacing(14)
        self.type_card = Card(padding=16)
        tr_row = QHBoxLayout()
        tr_row.addWidget(GlyphPlate(Icons.KEYBOARD, "#F472B6", 38))
        tcol = QVBoxLayout()
        tcol.setSpacing(1)
        tcol.addWidget(label(tr("phone_type"), 10.5, QFont.Weight.DemiBold))
        tcol.addWidget(label(tr("phone_type_desc"), 8.6, role="Dim", wrap=True))
        tr_row.addLayout(tcol, 1)
        start_type = button(tr("phone_type_start"), role="Ghost")
        start_type.clicked.connect(self.open_typing)
        tr_row.addWidget(start_type)
        self.type_card.root.addLayout(tr_row)
        side.addWidget(self.type_card)
        self.cam_card = Card(padding=16)
        cr = QHBoxLayout()
        cr.addWidget(GlyphPlate(Icons.CAMERA, "#F59E0B", 38))
        ccol = QVBoxLayout()
        ccol.setSpacing(1)
        ccol.addWidget(label(tr("phone_camera"), 10.5, QFont.Weight.DemiBold))
        ccol.addWidget(label(tr("phone_camera_desc"), 8.6, role="Dim", wrap=True))
        cr.addLayout(ccol, 1)
        for text, mode in ((tr("phone_camera_photo"), "photo"), (tr("phone_camera_scan"), "scan")):
            b = button(text, role="Ghost")
            b.clicked.connect(lambda _=False, m=mode: hub.request_camera(m))
            cr.addWidget(b)
        self.cam_card.root.addLayout(cr)
        side.addWidget(self.cam_card)
        self.handoff_card = Card(padding=16)
        hr = QHBoxLayout()
        hr.addWidget(GlyphPlate(Icons.LINK, "#60A5FA", 38))
        hcol = QVBoxLayout()
        hcol.setSpacing(4)
        hcol.addWidget(label(tr("handoff_title"), 10.5, QFont.Weight.DemiBold))
        self.handoff_url = QLineEdit()
        self.handoff_url.setPlaceholderText(tr("handoff_ph"))
        self.handoff_url.returnPressed.connect(self._handoff)
        hcol.addWidget(self.handoff_url)
        hr.addLayout(hcol, 1)
        go = button(tr("handoff_send"), role="Ghost")
        go.clicked.connect(self._handoff)
        hr.addWidget(go, 0, Qt.AlignmentFlag.AlignBottom)
        self.handoff_card.root.addLayout(hr)
        side.addWidget(self.handoff_card)
        self.screen_card = Card(padding=16)
        sr = QHBoxLayout()
        sr.addWidget(GlyphPlate(Icons.PREVIEW, "#22D3EE", 38))
        scol = QVBoxLayout()
        scol.setSpacing(1)
        scol.addWidget(label(tr("phone_screen_tools"), 10.5, QFont.Weight.DemiBold))
        scol.addWidget(label(tr("phone_screen_tools_desc"), 8.6, role="Dim", wrap=True))
        sr.addLayout(scol, 1)
        shot = button(tr("mirror_screenshot"), role="Ghost")
        shot.clicked.connect(hub.screenshot)
        sr.addWidget(shot)
        self.screen_card.root.addLayout(sr)
        side.addWidget(self.screen_card)
        side.addStretch(1)
        mid.addLayout(side, 2)
        self.mid_row = QWidget()
        self.mid_row.setLayout(mid)
        mid.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.mid_row)
        self._type_win = None

        # ---- row 4: notifications + timeline
        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        self.notif_card = Card(tr("phone_notifications"), Icons.BELL, padding=18)
        clear = button(tr("phone_clear_all"), Icons.CLOSE, role="Ghost")
        clear.clicked.connect(hub.clear_all)
        self.notif_card.header.addStretch(1)
        self.muted_btn = button("", role="Ghost")
        self.muted_btn.clicked.connect(self._muted_menu)
        self.notif_card.header.addWidget(self.muted_btn)
        self.notif_card.header.addWidget(clear)
        self.notif_list = QVBoxLayout()
        self.notif_list.setSpacing(2)
        self.notif_card.root.addLayout(self.notif_list)
        self.notif_card.root.addStretch(1)
        bottom.addWidget(self.notif_card, 3)
        self.timeline = Card(tr("phone_timeline"), Icons.CLOCK, padding=18)
        self.timeline_list = QVBoxLayout()
        self.timeline_list.setSpacing(8)
        self.timeline.root.addLayout(self.timeline_list)
        self.timeline.root.addStretch(1)
        bottom.addWidget(self.timeline, 2)
        self.bottom_row = QWidget()
        self.bottom_row.setLayout(bottom)
        bottom.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.bottom_row)
        root.addStretch(1)

        self.setStyleSheet(f"#PhoneApp {{ background: transparent; border: none; border-radius: 14px; }}"
                           f"#PhoneApp:hover {{ background: {qcolor(Palette.text, 0.09).name(QColor.NameFormat.HexArgb)}; }}"
                           f"#PhoneAction {{ background: {qcolor(Palette.text, 0.06).name(QColor.NameFormat.HexArgb)};"
                           f" border: none; border-radius: 18px; text-align: left; }}"
                           f"#PhoneAction:hover {{ background: {qcolor(Palette.text, 0.11).name(QColor.NameFormat.HexArgb)}; }}")
        hub.changed.connect(self._hub_changed)
        self._tick = QTimer(self, interval=15000, timeout=self.refresh_state)  # "online" goes stale on its own
        self._tick.start()
        self.refresh()

    # ---------------------------------------------------------------- actions
    def _cycle_ringer(self) -> None:
        order = ["normal", "vibrate", "silent"]
        now = self.ctl.phone_hub.state().get("ringer") or "normal"
        self.ctl.phone_hub.set_ringer(order[(order.index(now) + 1) % 3] if now in order else "normal")

    def _find(self) -> None:
        from app.ui.dialogs.base import ConfirmDialog
        hub = self.ctl.phone_hub
        if hub.find(True) is not False:  # rings until you stop it here or on the phone
            ConfirmDialog.ask(tr("phone_find"), tr("phone_find_ringing"), tr("phone_find_stop"), self, danger=False)
            hub.find(False)

    def _muted_menu(self) -> None:
        menu = make_menu(self)
        for pkg in sorted(self.ctl.phone_hub.muted()):
            add_action(menu, Icons.VOLUME, tr("phone_unmute", app=pkg), lambda _=False, p=pkg: self.ctl.phone_hub.mute_app(p, False))
        menu.exec(self.muted_btn.mapToGlobal(self.muted_btn.rect().bottomLeft()))

    # ---------------------------------------------------------------- refresh
    def _handoff(self) -> None:
        url = self.handoff_url.text().strip()
        if url:
            self.ctl.phone_hub.open_on_phone(url)
            self.handoff_url.clear()

    def showEvent(self, e):
        super().showEvent(e)
        try:  # a copied link is ready to send
            from PyQt6.QtWidgets import QApplication
            from app.core.launcher import normalize_url
            clip = QApplication.clipboard().text().strip()
            if not self.handoff_url.text() and clip and "\n" not in clip and normalize_url(clip):
                self.handoff_url.setText(clip)
        except Exception:
            pass

    def open_typing(self) -> None:
        from app.ui.phone_type import TypeWindow
        win = self._type_win
        try:
            alive = win is not None and win.isVisible()
        except RuntimeError:
            alive = False
        if not alive:
            win = self._type_win = TypeWindow(self.ctl)
        win.show()
        win.raise_()
        win.activateWindow()

    def _hub_changed(self, what: str) -> None:
        try:
            if what == "state":
                self.refresh_state()
            elif what == "apps":
                self.refresh_apps()
            elif what == "icon":
                self.refresh_notifs()
                self.refresh_apps()
            else:
                self.refresh_notifs()
        except RuntimeError:  # the page is gone
            pass

    def refresh(self) -> None:
        self.refresh_state()
        self.refresh_notifs()
        self.refresh_timeline()
        self.refresh_apps()
        if self.ctl.phone_hub.online():
            self.ctl.phone_hub.request_apps()

    def refresh_apps(self) -> None:
        hub = self.ctl.phone_hub
        while self.apps_grid.count():
            w = self.apps_grid.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        q = self.app_search.text().strip().lower()
        apps = [a for a in hub.apps() if not q or q in a["label"].lower() or q in a["pkg"].lower()]
        cols = 8
        for i, app in enumerate(apps[:24]):
            self.apps_grid.addWidget(AppButton(hub, app), i // cols, i % cols)
        self.apps_grid.setColumnStretch(cols, 1)
        if not hub.apps():
            hint = tr("phone_apps_wait") if hub.online() else tr("phone_apps_none")
        elif not hub.adb_ready():
            hint = tr("phone_apps_need_scrcpy")
        elif len(apps) > 24:
            hint = tr("phone_apps_more", n=len(apps) - 24)
        else:
            hint = tr("phone_apps_hint")
        self.apps_hint.setText(hint)

    def refresh_state(self) -> None:
        hub = self.ctl.phone_hub
        st = hub.state()
        has = bool(st)
        self.empty.setVisible(not has)
        for w in (self.top_row, self.actions_row, self.mid_row, self.bottom_row):
            w.setVisible(has)
        if not has:
            return
        online = hub.online()
        self.art.set_state(st.get("battery"), st.get("charging", False), online)
        self.name.setText(hub.device_name())
        self.sub.setText(" · ".join(x for x in (st.get("model"), f"Android {st['android']}" if st.get("android") else "") if x))
        while self.pills.count():
            w = self.pills.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        net = {"wifi": (Icons.WIFI, tr("sys_wifi")), "cell": (Icons.PHONE, tr("phone_net_cell"))}.get(
            st.get("net"), (Icons.WARNING, tr("phone_net_none")))
        self.pills.addWidget(pill(net[0], net[1] + ("" if online else "  ·  " + tr("phone_offline"))), 0, Qt.AlignmentFlag.AlignLeft)
        if st.get("battery") is not None:
            txt = tr("sys_charging") if st.get("charging") else ltr(f"{st['battery']}%")
            self.pills.addWidget(pill(Icons.CHARGING if st.get("charging") else Icons.BATTERY, txt), 0, Qt.AlignmentFlag.AlignLeft)
        hints = []
        if not st.get("notif_access"):
            hints.append(tr("phone_need_notif_access"))
        if not st.get("dnd_access"):
            hints.append(tr("phone_need_dnd_access"))
        self.access_hint.setText("\n".join("⚠ " + h for h in hints))
        self.access_hint.setVisible(bool(hints))
        for tile, key in ((self.t_dnd, "dnd"), (self.t_torch, "torch"), (self.t_wifi, "wifi"), (self.t_bt, "bt")):
            tile.setChecked(bool(st.get(key)))
            tile.setEnabled(online)
        adb = hub.adb_ready()
        for tile in (self.t_wifi, self.t_bt):
            tile.setEnabled(online and adb)
            tile.setToolTip("" if adb else tr("phone_radio_needs_adb"))
        ring = st.get("ringer") or "normal"
        self.t_ring.set_caption(tr(f"phone_ring_{ring}"), {"normal": Icons.RINGER, "vibrate": Icons.VIBRATE,
                                                           "silent": Icons.MUTE}.get(ring, Icons.RINGER))
        self.t_ring.setEnabled(online)
        self.t_dnd.setEnabled(online and st.get("dnd_access", False))
        if not self.volume.isSliderDown() and st.get("volume") is not None:
            self.volume.blockSignals(True)
            self.volume.setValue(int(st["volume"]))
            self.volume.blockSignals(False)
            self.vol_label.setText(ltr(f"{st['volume']}%"))
        self.volume.setEnabled(online)

    def refresh_notifs(self) -> None:
        hub = self.ctl.phone_hub
        while self.notif_list.count():
            w = self.notif_list.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        items = hub.notifications()
        self.notif_card.title_label.setText(f"{tr('phone_notifications')} · {len(items)}" if items else tr("phone_notifications"))
        for n in items[:12]:
            self.notif_list.addWidget(NotifRow(self, n))
        if not items:
            empty = label(tr("phone_no_notifs") if hub.state().get("notif_access", True) else tr("phone_need_notif_access"),
                          9.5, role="Faint", wrap=True)
            self.notif_list.addWidget(empty)
        muted = hub.muted()
        self.muted_btn.setText(tr("phone_muted_n", n=len(muted)))
        self.muted_btn.setVisible(bool(muted))

    def refresh_timeline(self) -> None:
        while self.timeline_list.count():
            w = self.timeline_list.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        rows = self.ctl.db.list_transfers("in", limit=6) + self.ctl.db.list_transfers("out", limit=6)
        rows.sort(key=lambda r: r["id"], reverse=True)
        glyph = {"file": Icons.DOCUMENT, "link": Icons.LINK, "note": Icons.NOTES, "clip": Icons.PASTE,
                 "task": Icons.CHECKLIST, "text": Icons.NOTES}
        for r in rows[:6]:
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(GlyphLabel(glyph.get(r["kind"], Icons.DOCUMENT), 12, Theme.accent()))
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(side_label((r["title"] or r["content"] or "")[:60], 9.8, QFont.Weight.DemiBold))
            arrow = tr("phone_tl_from") if r["direction"] == "in" else tr("phone_tl_to")
            col.addWidget(label(f"{arrow} {r.get('device_name') or ''} · {relative_time(r['created_at'])}", 8.6, role="Dim"))
            row.addLayout(col, 1)
            w = QWidget()
            w.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            self.timeline_list.addWidget(w)
        if not rows:
            self.timeline_list.addWidget(label(tr("phone_tl_empty"), 9.5, role="Faint"))
