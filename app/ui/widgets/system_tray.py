"""
The taskbar's status icons — Wi-Fi, VPN, sound, brightness, battery — and the small panel each one opens.

Settings › Taskbar decides: which show, their order, whether each shows its text (network name, percent…), and
whether they sit together in one button (Windows 11 style: one click opens all controls) or each on its own.
Icons are drawn here (bars, waves, a battery that fills), so they always match the state exactly.
"""
from __future__ import annotations

import math
import threading

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QAbstractButton, QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                             QListWidget, QListWidgetItem, QMenu, QPushButton, QSlider, QVBoxLayout, QWidget)

from app.core import system_status as sysst
from app.core.i18n import is_rtl, tr
from app.ui.animations import OUT, value_animation
from app.ui.dialogs.base import drop_shadow
from app.ui.theme import Icons, Palette, Theme, ink, qcolor, ui_font
from app.ui.widgets.common import IconButton, button, label

TRAY_KEYS = ["wifi", "vpn", "sound", "brightness", "battery"]
DEFAULT_TRAY = [{"key": k, "show": k != "vpn", "text": k == "battery"} for k in TRAY_KEYS]


def tray_conf(conf: dict) -> list[dict]:
    items = conf.get("tray") or DEFAULT_TRAY
    have = {i["key"] for i in items}
    return [dict(i) for i in items if i["key"] in TRAY_KEYS] + [dict(d) for d in DEFAULT_TRAY if d["key"] not in have]


# ===================================================================== drawn icons
def draw_icon(p: QPainter, key: str, r: QRectF, state: dict, color: QColor) -> None:
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, max(1.2, r.width() * 0.085), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy, w = r.center().x(), r.center().y(), r.width()
    if key == "wifi":
        wifi = state.get("wifi") or {}
        bars = 0 if not wifi.get("connected") else (3 if wifi.get("signal", 0) >= 70 else 2 if wifi.get("signal", 0) >= 40 else 1)
        base = QPointF(cx, r.bottom() - w * 0.12)
        for i, rad in enumerate((w * 0.18, w * 0.34, w * 0.5)):
            c = QColor(color)
            if i + 1 > bars:
                c.setAlphaF(0.28)
            p.setPen(QPen(c, pen.widthF(), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(base.x() - rad, base.y() - rad, rad * 2, rad * 2), 45 * 16, 90 * 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color if bars else qcolor(color.name(), 0.3))
        p.drawEllipse(base, w * 0.06, w * 0.06)
        if not wifi.get("connected"):
            p.setPen(QPen(QColor(Palette.danger), pen.widthF()))
            p.drawLine(QPointF(r.left() + w * 0.2, r.top() + w * 0.2), QPointF(r.right() - w * 0.2, r.bottom() - w * 0.2))
    elif key == "vpn":
        on = vpn_on(state)
        path = QPainterPath()
        top, bot = r.top() + w * 0.1, r.bottom() - w * 0.08
        path.moveTo(cx, top)
        path.lineTo(r.right() - w * 0.16, top + w * 0.14)
        path.quadTo(r.right() - w * 0.16, bot - w * 0.15, cx, bot)
        path.quadTo(r.left() + w * 0.16, bot - w * 0.15, r.left() + w * 0.16, top + w * 0.14)
        path.closeSubpath()
        if on:
            p.setBrush(qcolor(Palette.success, 0.85))
            p.setPen(QPen(QColor(Palette.success), pen.widthF() * 0.8))
        p.drawPath(path)
        if on:
            p.setPen(QPen(QColor("#FFFFFF"), pen.widthF() * 0.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawPolyline([QPointF(cx - w * 0.14, cy), QPointF(cx - w * 0.03, cy + w * 0.11), QPointF(cx + w * 0.16, cy - w * 0.1)])
    elif key == "sound":
        vol = state.get("volume")
        level, muted = (vol if vol else (0, True))
        body = QPainterPath()
        body.moveTo(r.left() + w * 0.12, cy - w * 0.12)
        body.lineTo(r.left() + w * 0.28, cy - w * 0.12)
        body.lineTo(r.left() + w * 0.48, cy - w * 0.3)
        body.lineTo(r.left() + w * 0.48, cy + w * 0.3)
        body.lineTo(r.left() + w * 0.28, cy + w * 0.12)
        body.lineTo(r.left() + w * 0.12, cy + w * 0.12)
        body.closeSubpath()
        p.setBrush(color)
        p.setPen(QPen(color, pen.widthF() * 0.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPath(body)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if muted or level == 0:
            p.setPen(QPen(QColor(Palette.danger) if muted else color, pen.widthF()))
            p.drawLine(QPointF(r.left() + w * 0.6, cy - w * 0.14), QPointF(r.left() + w * 0.86, cy + w * 0.14))
            p.drawLine(QPointF(r.left() + w * 0.86, cy - w * 0.14), QPointF(r.left() + w * 0.6, cy + w * 0.14))
        else:
            waves = 1 if level < 34 else 2 if level < 67 else 3
            p.setPen(pen)
            for i in range(waves):
                rad = w * (0.14 + 0.13 * i)
                p.drawArc(QRectF(r.left() + w * 0.48 - rad, cy - rad, rad * 2, rad * 2), -50 * 16, 100 * 16)
    elif key == "brightness":
        level = state.get("brightness")
        p.setBrush(color)
        p.drawEllipse(QPointF(cx, cy), w * 0.15, w * 0.15)
        p.setBrush(Qt.BrushStyle.NoBrush)
        rays = 8
        for i in range(rays):
            a = i * 2 * math.pi / rays
            inner, outer = w * 0.26, w * (0.36 if level is None or level >= 50 else 0.31)
            p.drawLine(QPointF(cx + inner * math.cos(a), cy + inner * math.sin(a)),
                       QPointF(cx + outer * math.cos(a), cy + outer * math.sin(a)))
    elif key == "battery":
        b = state.get("battery")
        body = QRectF(r.left() + w * 0.08, cy - w * 0.2, w * 0.74, w * 0.4)
        p.setPen(QPen(color, pen.widthF() * 0.8))
        p.drawRoundedRect(body, w * 0.07, w * 0.07)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(body.right() + w * 0.03, cy - w * 0.08, w * 0.07, w * 0.16), w * 0.02, w * 0.02)
        if b:
            pct = b["percent"] / 100
            fill_c = QColor(Palette.danger) if pct <= 0.15 and not b["plugged"] else (QColor(Palette.success) if b["plugged"] else color)
            p.setBrush(fill_c)
            inner = body.adjusted(w * 0.06, w * 0.06, -w * 0.06, -w * 0.06)
            p.drawRoundedRect(QRectF(inner.left(), inner.top(), max(1.5, inner.width() * pct), inner.height()), 1.5, 1.5)
            if b["plugged"]:
                p.setPen(QPen(QColor("#FFFFFF") if pct > 0.5 else color, pen.widthF() * 0.7))
                p.drawPolyline([QPointF(cx - w * 0.02, body.top() + w * 0.04), QPointF(cx - w * 0.1, cy + w * 0.02),
                                QPointF(cx + w * 0.04, cy - w * 0.02), QPointF(cx - w * 0.04, body.bottom() - w * 0.04)])
    p.restore()


def vpn_on(state: dict) -> bool:
    v = state.get("vpn") or {}
    return any(c["connected"] for c in v.get("windows", [])) or bool(v.get("adapters"))


def item_text(key: str, state: dict) -> str:
    if key == "wifi":
        w = state.get("wifi") or {}
        return w.get("ssid") or tr("sys_offline")
    if key == "vpn":
        v = state.get("vpn") or {}
        on = [c["name"] for c in v.get("windows", []) if c["connected"]] + list(v.get("adapters", []))
        return on[0] if on else tr("sys_vpn_off")
    if key == "sound":
        vol = state.get("volume")
        return "—" if not vol else (tr("sys_muted") if vol[1] else f"{vol[0]}%")
    if key == "brightness":
        b = state.get("brightness")
        return "—" if b is None else f"{b}%"
    if key == "battery":
        b = state.get("battery")
        return "—" if not b else f"{b['percent']}%"
    return ""


def item_tip(key: str, state: dict) -> str:
    return f"{tr('sys_' + key)}: {item_text(key, state)}"


def available(key: str, state: dict) -> bool:
    if key == "battery":
        return state.get("battery") is not None
    if key == "wifi":
        return (state.get("wifi") or {}).get("has_wifi", True)
    return True


# ===================================================================== the buttons
class StatusButton(QAbstractButton):
    """One status icon (or, grouped, several side by side) with optional text."""

    def __init__(self, keys: list[tuple[str, bool]], size: int, ctl, parent=None):
        super().__init__(parent)
        self.keys = keys          # [(key, show_text)]
        self.size_px = size
        self.ctl = ctl
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(ui_font(8.8))
        self.refresh()

    def state(self) -> dict:
        return self.ctl.system.state if getattr(self.ctl, "system", None) else {}

    def refresh(self) -> None:
        st = self.state()
        w = 8
        fm = self.fontMetrics()
        for key, text in self.keys:
            w += int(self.size_px * 0.5) + 4
            if text:
                w += fm.horizontalAdvance(item_text(key, st)) + 6
        self.setFixedSize(w + 4, self.size_px)
        self.setToolTip("\n".join(item_tip(k, st) for k, _ in self.keys))
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self._hover or self.isDown():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ink(0.08))
            p.drawRoundedRect(r, 9, 9)
        st = self.state()
        icon = self.size_px * 0.5
        x = r.left() + 6
        color = QColor(Palette.text)
        order = self.keys if not is_rtl() else list(reversed(self.keys))
        for key, text in order:
            draw_icon(p, key, QRectF(x, (self.height() - icon) / 2, icon, icon), st, color)
            x += icon + 4
            if text:
                t = item_text(key, st)
                p.setPen(QColor(Palette.text))
                p.setFont(ui_font(8.8))
                tw = p.fontMetrics().horizontalAdvance(t)
                p.drawText(QRectF(x, 0, tw + 2, self.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, t)
                x += tw + 6


# ===================================================================== the panel
class _Section(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("SysSection")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(12, 10, 12, 10)
        self.lay.setSpacing(8)
        self.head = QHBoxLayout()
        self.title = label(title, 10, QFont.Weight.DemiBold)
        self.head.addWidget(self.title, 1)
        self.lay.addLayout(self.head)


class SystemPanel(QFrame):
    """Drops up from the status icons: the controls of the item(s) clicked."""
    _nets_ready = pyqtSignal(list)      # from the scan thread (queued into the GUI thread)
    _refresh_later = pyqtSignal(bool)   # after a change made in a thread: re-read the state

    def __init__(self, ctl, host: QWidget):
        super().__init__(host)
        self.ctl = ctl
        self.keys: list[str] = []
        self.setObjectName("ActionCenter")
        self.setFixedWidth(380)
        drop_shadow(self, 40, 10, 150)
        self._t = 0.0
        self._anim = value_animation(self, 0.0, 1.0, 170, self._set_t, OUT, self._done)
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0)
        self.setGraphicsEffect(self._fx)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 14, 14, 14)
        self.lay.setSpacing(10)
        self.anchor_x = None
        ctl.system.changed.connect(self._refresh_values)
        self._nets_ready.connect(self._show_nets)
        self._refresh_later.connect(lambda full: QTimer.singleShot(800, lambda: self.ctl.system.refresh(full=full)))
        host.installEventFilter(self)
        self.hide()

    # ---------------------------------------------------------------- build
    def open_for(self, keys: list[str], anchor: QWidget) -> None:
        if self.isVisible() and self.keys == keys and self._anim.direction() == self._anim.Direction.Forward:
            self.close_panel()
            return
        self.keys = keys
        self.anchor = anchor
        self._build()
        self.ctl.system.refresh(full=True)
        self.show()
        self.raise_()
        QApplication.instance().installEventFilter(self)
        self._anim.stop()
        self._anim.setDirection(self._anim.Direction.Forward)
        self._anim.start()
        if "wifi" in keys:
            self._scan_wifi()

    def _clear(self) -> None:
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()

    def _build(self) -> None:
        self._clear()
        st = self.ctl.system.state
        self.widgets = {}
        for key in self.keys:
            if not available(key, st):
                continue
            sec = _Section(tr("sys_" + key))
            getattr(self, f"_sec_{key}")(sec, st)
            self.lay.addWidget(sec)
        settings = button(tr("sys_windows_settings"), Icons.SETTINGS, role="Ghost")
        page = {"wifi": "network-wifi", "vpn": "network-vpn", "sound": "sound", "brightness": "display",
                "battery": "batterysaver"}.get(self.keys[0] if len(self.keys) == 1 else "", "")
        settings.clicked.connect(lambda: (self.close_panel(), __import__("os").startfile(f"ms-settings:{page}")))
        self.lay.addWidget(settings, 0, Qt.AlignmentFlag.AlignLeft)
        self.adjustSize()

    def _sec_wifi(self, sec: _Section, st: dict) -> None:
        wifi = st.get("wifi") or {}
        self.wifi_state = label("", 9, role="Dim")
        sec.lay.addWidget(self.wifi_state)
        refresh = IconButton(Icons.REFRESH, tr("sys_scan"), 28, 9)
        refresh.clicked.connect(self._scan_wifi)
        sec.head.addWidget(refresh)
        off = IconButton(Icons.CLOSE, tr("sys_disconnect"), 28, 9)
        off.clicked.connect(lambda: self._bg(sysst.wifi_disconnect))
        sec.head.addWidget(off)
        self.wifi_list = QListWidget()
        self.wifi_list.setObjectName("NotifList")
        self.wifi_list.setFixedHeight(150)
        self.wifi_list.itemClicked.connect(self._wifi_clicked)
        sec.lay.addWidget(self.wifi_list)
        self.widgets["wifi"] = True

    def _sec_vpn(self, sec: _Section, st: dict) -> None:
        self.vpn_box = QVBoxLayout()
        self.vpn_box.setSpacing(4)
        sec.lay.addLayout(self.vpn_box)
        self.widgets["vpn"] = True

    def _sec_sound(self, sec: _Section, st: dict) -> None:
        row = QHBoxLayout()
        self.mute_btn = QPushButton()
        self.mute_btn.setObjectName("IconButton")
        self.mute_btn.setFixedSize(34, 34)
        self.mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute_btn.clicked.connect(self._toggle_mute)
        row.addWidget(self.mute_btn)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setLayoutDirection(Qt.LayoutDirection.LeftToRight)  # quiet on the left, loud on the right, in Arabic too
        self.vol.setRange(0, 100)
        self.vol.valueChanged.connect(lambda v: (sysst.set_volume(v), self._vol_label()))
        row.addWidget(self.vol, 1)
        self.vol_lbl = label("", 9.5, QFont.Weight.DemiBold)
        self.vol_lbl.setFixedWidth(42)
        row.addWidget(self.vol_lbl)
        sec.lay.addLayout(row)
        self.widgets["sound"] = True

    def _sec_brightness(self, sec: _Section, st: dict) -> None:
        if st.get("brightness") is None:
            sec.lay.addWidget(label(tr("sys_no_brightness"), 9, role="Dim", wrap=True))
            return
        row = QHBoxLayout()
        self.bri = QSlider(Qt.Orientation.Horizontal)
        self.bri.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.bri.setRange(0, 100)
        self.bri.setValue(int(st["brightness"]))
        self.bri_lbl = label(f"{st['brightness']}%", 9.5, QFont.Weight.DemiBold)
        self.bri_lbl.setFixedWidth(42)
        self._bri_timer = QTimer(self, singleShot=True, interval=250,
                                 timeout=lambda: self._bg(sysst.set_brightness, self.bri.value()))
        self.bri.valueChanged.connect(lambda v: (self.bri_lbl.setText(f"{v}%"), self._bri_timer.start()))
        row.addWidget(self.bri, 1)
        row.addWidget(self.bri_lbl)
        sec.lay.addLayout(row)
        self.widgets["brightness"] = True

    def _sec_battery(self, sec: _Section, st: dict) -> None:
        self.bat_big = label("", 18, QFont.Weight.DemiBold)
        self.bat_sub = label("", 9, role="Dim")
        sec.lay.addWidget(self.bat_big)
        sec.lay.addWidget(self.bat_sub)
        self.widgets["battery"] = True

    # ------------------------------------------------------------- values
    def _refresh_values(self) -> None:
        if not self.isVisible():
            return
        st = self.ctl.system.state
        if "wifi" in self.widgets:
            w = st.get("wifi") or {}
            self.wifi_state.setText(tr("sys_connected_to", name=w["ssid"], n=w["signal"]) if w.get("connected") else tr("sys_offline"))
        if "sound" in self.widgets and st.get("volume"):
            level, muted = st["volume"]
            if not self.vol.isSliderDown():
                self.vol.blockSignals(True)
                self.vol.setValue(level)
                self.vol.blockSignals(False)
            self.mute_btn.setText("🔇" if muted else "🔊")
            self._vol_label()
        if "battery" in self.widgets and st.get("battery"):
            b = st["battery"]
            self.bat_big.setText(f"{b['percent']}%")
            if b["plugged"]:
                sub = tr("sys_charging") if b["percent"] < 100 else tr("sys_full")
            elif b["secs_left"]:
                h, m = divmod(b["secs_left"] // 60, 60)
                sub = tr("sys_time_left", t=f"{h}:{m:02d}")
            else:
                sub = tr("sys_on_battery")
            self.bat_sub.setText(sub)
        if "vpn" in self.widgets:
            self._fill_vpn(st.get("vpn") or {})

    def _vol_label(self) -> None:
        vol = sysst.get_volume()
        if vol:
            self.vol_lbl.setText(tr("sys_muted") if vol[1] else f"{vol[0]}%")

    def _toggle_mute(self) -> None:
        vol = sysst.get_volume()
        if vol:
            sysst.set_volume(mute=not vol[1])
            self.ctl.system.refresh()

    def _fill_vpn(self, v: dict) -> None:
        while self.vpn_box.count():
            it = self.vpn_box.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
        if not v.get("windows") and not v.get("adapters"):
            self.vpn_box.addWidget(label(tr("sys_no_vpn"), 9, role="Dim", wrap=True))
        for c in v.get("windows", []):
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.addWidget(label(("🟢  " if c["connected"] else "⚪  ") + c["name"], 9.5), 1)
            b = button(tr("sys_disconnect") if c["connected"] else tr("sys_connect"), role="Ghost")
            b.clicked.connect(lambda _=False, n=c["name"], on=c["connected"]: self._bg(sysst.vpn_set, n, not on, full=True))
            rl.addWidget(b)
            self.vpn_box.addWidget(row)
        for a in v.get("adapters", []):
            self.vpn_box.addWidget(label("🟢  " + tr("sys_vpn_adapter", name=a), 9.5))

    def _scan_wifi(self) -> None:
        if not hasattr(self, "wifi_list"):
            return
        self.wifi_list.clear()
        self.wifi_list.addItem(tr("sys_scanning"))

        def work():
            self._nets_ready.emit(sysst.wifi_networks())
        threading.Thread(target=work, daemon=True).start()

    def _show_nets(self, nets: list[dict]) -> None:
        try:
            self.wifi_list.clear()
        except RuntimeError:
            return
        current = (self.ctl.system.state.get("wifi") or {}).get("ssid")
        for n in nets:
            mark = "✓ " if n["ssid"] == current else ""
            lock = "🔒 " if n["secure"] else ""
            saved = "" if n["saved"] else "  ·  " + tr("sys_not_saved")
            it = QListWidgetItem(f"{mark}{n['ssid'] or tr('sys_hidden')}   {n['signal']}%  {lock}{saved}")
            it.setData(Qt.ItemDataRole.UserRole, n)
            self.wifi_list.addItem(it)
        if not nets:
            self.wifi_list.addItem(tr("sys_no_networks"))

    def _wifi_clicked(self, item) -> None:
        n = item.data(Qt.ItemDataRole.UserRole)
        if not n:
            return
        if n["saved"]:
            self.ctl.notify(tr("sys_connecting", name=n["ssid"]), "info")
            self._bg(sysst.wifi_connect, n["ssid"], full=True)
        else:  # a new network needs its password: Windows' own dialog
            import os
            os.startfile("ms-availablenetworks:")

    def _bg(self, fn, *args, full: bool = False) -> None:
        def work():
            try:
                fn(*args)
            except Exception as exc:
                from app.core.logs import log
                log.warning("system panel: %s", exc)
            finally:
                self._refresh_later.emit(full)
        threading.Thread(target=work, daemon=True).start()

    # --------------------------------------------------------- open / close
    def close_panel(self) -> None:
        if not self.isVisible():
            return
        self._anim.stop()
        self._anim.setDirection(self._anim.Direction.Backward)
        self._anim.start()

    def _done(self) -> None:
        if self._anim.direction() == self._anim.Direction.Backward:
            self.hide()
            QApplication.instance().removeEventFilter(self)

    def _set_t(self, v) -> None:
        self._t = float(v)
        self._fx.setOpacity(self._t)
        self._refresh_values()
        self.place()

    def place(self) -> None:
        host = self.parentWidget()
        bar = getattr(self.ctl.window, "taskbar", None)
        top = bar is not None and bar.conf["position"] == "top"
        bar_h = bar.height() if bar is not None else 0
        self.adjustSize()
        anchor = getattr(self, "anchor", None)
        if anchor is not None and anchor.isVisible():
            ax = anchor.mapTo(host, anchor.rect().center()).x()
            x = max(12, min(host.width() - self.width() - 12, ax - self.width() // 2))
        else:
            x = 12 if is_rtl() else host.width() - self.width() - 12
        slide = int((1 - self._t) * 20)
        y = (bar_h + 10 - slide) if top else (host.height() - bar_h - self.height() - 10 + slide)
        self.move(x, y)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and self.isVisible() and isinstance(obj, QWidget) \
                and not isinstance(obj.window(), QMenu):
            pos = event.globalPosition().toPoint()
            a = getattr(self, "anchor", None)
            on_anchor = a is not None and a.isVisible() and a.rect().contains(a.mapFromGlobal(pos))
            if not self.rect().contains(self.mapFromGlobal(pos)) and not on_anchor:
                self.close_panel()
        return super().eventFilter(obj, event)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(ink(0.12))
        p.setBrush(QColor(Palette.surface_solid))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
