"""
The phone's Deck: a grid of buttons that run things on the laptop (Phone > Deck sets them up).

Button kinds
  media     play / pause, previous, next, volume down / up, mute   (the volume button shows the level)
  power     lock, sleep, screen off, screen on
  command   any Study Hub command (quick note, start focus, screenshot...)
  keys      a keyboard shortcut you recorded (e.g. Ctrl+Shift+Esc)
  open      a desktop item, a program / file, or a website
  obs       OBS Studio: record, stream, a scene

The phone only sends a button's id; what it does is decided here, from the buttons you set up.
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

from app.core.i18n import tr

log = logging.getLogger(__name__)

MEDIA = {"play": ("⏯", "deck_play"), "prev_track": ("⏮", "deck_prev"), "next_track": ("⏭", "deck_next"),
         "vol_down": ("🔉", "deck_vol_down"), "vol_up": ("🔊", "deck_vol_up"), "mute": ("🔇", "deck_mute")}
POWER = {"lock": ("🔒", "deck_lock"), "sleep": ("🌙", "deck_sleep"), "screen_off": ("⬛", "deck_screen_off"),
         "screen_on": ("🖥", "deck_screen_on")}
OBS = {"record": ("⏺", "deck_obs_record"), "stream": ("📡", "deck_obs_stream")}
COLORS = ["#7C5CFF", "#22D3EE", "#34D399", "#F59E0B", "#EF4444", "#F472B6", "#60A5FA", "#64748B"]

# keyboard shortcut names (what a recorded shortcut may contain)
_EXTRA_KEYS = {**{chr(c): (c, False) for c in range(ord("A"), ord("Z") + 1)},
               **{str(d): (0x30 + d, False) for d in range(10)},
               **{f"F{i}": (0x6F + i, False) for i in range(1, 13)},
               "ESC": (0x1B, False), "TAB": (0x09, False), "SPACE": (0x20, False), "ENTER": (0x0D, False),
               "RETURN": (0x0D, False), "BACKSPACE": (0x08, False), "DEL": (0x2E, True), "DELETE": (0x2E, True),
               "INS": (0x2D, True), "HOME": (0x24, True), "END": (0x23, True), "PGUP": (0x21, True),
               "PGDOWN": (0x22, True), "LEFT": (0x25, True), "RIGHT": (0x27, True), "UP": (0x26, True),
               "DOWN": (0x28, True), "PRINT": (0x2C, True), "CTRL": (0x11, False), "SHIFT": (0x10, False),
               "ALT": (0x12, False), "META": (0x5B, True), "WIN": (0x5B, True)}


def parse_shortcut(text: str) -> list[tuple[int, bool]] | None:
    """'Ctrl+Shift+Esc' -> virtual keys (None when something isn't a key we know)."""
    parts = [p.strip().upper() for p in str(text or "").replace(" ", "").split("+") if p.strip()]
    if not parts or len(parts) > 5:
        return None
    keys = [_EXTRA_KEYS.get(p) for p in parts]
    return None if any(k is None for k in keys) else keys


def press_shortcut(keys: list[tuple[int, bool]]) -> None:
    from app.core import remote_input as ri
    ri._send(*[ri._key(vk, False, ext) for vk, ext in keys], *[ri._key(vk, True, ext) for vk, ext in reversed(keys)])


def default_buttons() -> list[dict]:
    def b(kind, ref, color):
        return {"id": secrets.token_hex(4), "kind": kind, "ref": ref, "label": "", "emoji": "", "color": color}
    return [b("media", "prev_track", "#7C5CFF"), b("media", "play", "#7C5CFF"), b("media", "next_track", "#7C5CFF"),
            b("media", "vol_down", "#22D3EE"), b("media", "mute", "#22D3EE"), b("media", "vol_up", "#22D3EE"),
            b("command", "quick_note", "#34D399"), b("command", "start_focus", "#34D399"), b("command", "hk_screenshot", "#34D399"),
            b("keys", "Win+D", "#60A5FA"), b("power", "lock", "#64748B"), b("power", "screen_off", "#64748B")]


class Deck(QObject):
    """ctl.deck"""
    changed = pyqtSignal()

    def __init__(self, ctl):
        super().__init__(ctl)
        self.ctl = ctl
        self._obs_state: dict = {}
        self._obs_at = 0.0

    # ---------------------------------------------------------------- the buttons
    def buttons(self) -> list[dict]:
        saved = self.ctl.db.get_setting("phone_deck", None)
        if not isinstance(saved, list):  # first time: keep the defaults (their ids must stay the same)
            saved = default_buttons()
            self.ctl.db.set_setting("phone_deck", saved)
        return [dict(b) for b in saved]

    def save(self, buttons: list[dict]) -> None:
        self.ctl.db.set_setting("phone_deck", buttons)
        self.changed.emit()

    def add(self, kind: str, ref: str, label: str = "", emoji: str = "") -> dict:
        btn = {"id": secrets.token_hex(4), "kind": kind, "ref": ref, "label": label, "emoji": emoji,
               "color": COLORS[len(self.buttons()) % len(COLORS)]}
        self.save(self.buttons() + [btn])
        return btn

    def update(self, bid: str, **changes) -> None:
        self.save([dict(b, **changes) if b["id"] == bid else b for b in self.buttons()])

    def remove(self, bid: str) -> None:
        self.save([b for b in self.buttons() if b["id"] != bid])

    def reorder(self, ids: list[str]) -> None:
        by_id = {b["id"]: b for b in self.buttons()}
        self.save([by_id[i] for i in ids if i in by_id])

    def reset(self) -> None:
        self.save(default_buttons())

    # ---------------------------------------------------------------- how a button looks
    def _commands(self) -> dict:
        return {key: glyph for glyph, key, _ in self.ctl.command_list()}

    def describe(self, b: dict) -> tuple[str, str]:
        """(label, emoji) a button shows (its own, or the default for what it does)."""
        kind, ref = b["kind"], b["ref"]
        label, emoji = b.get("label") or "", b.get("emoji") or ""
        if kind == "media" and ref in MEDIA:
            emoji, label = emoji or MEDIA[ref][0], label or tr(MEDIA[ref][1])
        elif kind == "power" and ref in POWER:
            emoji, label = emoji or POWER[ref][0], label or tr(POWER[ref][1])
        elif kind == "command":
            emoji, label = emoji or "⚡", label or tr(ref)
        elif kind == "keys":
            emoji, label = emoji or "⌨", label or ref
        elif kind == "open":
            emoji = emoji or ("🌐" if str(ref).startswith(("http://", "https://")) else "📂")
            if not label:
                if str(ref).startswith("item:"):
                    item = self.ctl.db.get_item(int(ref[5:])) if ref[5:].isdigit() else None
                    label = item["title"] if item else ref
                else:
                    label = os.path.basename(str(ref).rstrip("/\\")) or str(ref)
        elif kind == "obs":
            if ref in OBS:
                emoji, label = emoji or OBS[ref][0], label or tr(OBS[ref][1])
            elif ref.startswith("scene:"):
                emoji, label = emoji or "🎬", label or ref[6:]
        return label[:40], emoji[:4]

    def obs_status(self, max_age: float = 3.0) -> dict:
        if not any(b["kind"] == "obs" for b in self.buttons()):
            return {}
        if time.time() - self._obs_at > max_age:
            from app.core import obs
            try:
                self._obs_state = obs.from_settings(self.ctl.db).status()
            except obs.ObsError as exc:
                self._obs_state = {"connected": False, "error": str(exc)}
            except OSError:
                self._obs_state = {"connected": False, "error": "not_running"}
            self._obs_at = time.time()
        return self._obs_state

    def public(self) -> dict:
        """What the phone shows: buttons (label, emoji, colour, on/off) + live volume + OBS."""
        from app.core import system_status
        vol = system_status.get_volume()
        obs_st = self.obs_status()
        out = []
        for b in self.buttons():
            label, emoji = self.describe(b)
            item = {"id": b["id"], "label": label, "emoji": emoji, "color": b.get("color") or COLORS[0],
                    "kind": b["kind"], "confirm": b["kind"] == "power" and b["ref"] == "sleep"}
            if b["kind"] == "media" and b["ref"] == "mute" and vol:
                item["on"] = vol[1]
            if b["kind"] == "obs" and obs_st.get("connected"):
                if b["ref"] == "record":
                    item["on"] = obs_st["recording"]
                elif b["ref"] == "stream":
                    item["on"] = obs_st["streaming"]
                elif b["ref"].startswith("scene:"):
                    item["on"] = obs_st["scene"] == b["ref"][6:]
            out.append(item)
        return {"buttons": out, "volume": {"level": vol[0], "muted": vol[1]} if vol else None,
                "obs": {"connected": obs_st.get("connected", False)} if obs_st else None}

    # ---------------------------------------------------------------- pressing one
    def press(self, bid: str) -> dict:
        b = next((x for x in self.buttons() if x["id"] == bid), None)
        if b is None:
            return {"error": "gone", "status": 404}
        kind, ref = b["kind"], b["ref"]
        try:
            if kind == "media" and ref in MEDIA:
                from app.core import remote_input
                remote_input.press(ref)
            elif kind == "power" and ref in POWER:
                from app.core import power
                if ref == "sleep":  # after the answer has gone back to the phone
                    threading.Timer(1.0, power.sleep).start()
                else:
                    getattr(power, ref)()
            elif kind == "command":
                fn = next((f for _, key, f in self.ctl.command_list() if key == ref), None)
                if fn is None:
                    return {"error": "gone", "status": 404}
                fn()
            elif kind == "keys":
                keys = parse_shortcut(ref)
                if not keys:
                    return {"error": "keys", "status": 400}
                press_shortcut(keys)
            elif kind == "open":
                self._open(ref)
            elif kind == "obs":
                from app.core import obs
                arg = ref[6:] if ref.startswith("scene:") else ""
                obs.from_settings(self.ctl.db).do("scene" if arg else ref, arg)
                self._obs_at = 0
            else:
                return {"error": "kind", "status": 400}
        except Exception as exc:  # OBS closed, an app that won't start...
            log.info("deck %s %s: %s", kind, ref, exc)
            return {"error": str(exc)[:120] or "failed", "status": 409}
        time.sleep(0.15)  # let volume / OBS settle so the phone shows the new state
        if kind == "obs":
            self._obs_at = 0
        return {"ok": True, **self.public()}

    def _open(self, ref: str) -> None:
        if ref.startswith("item:"):
            self.ctl.launch_item(int(ref[5:]))
        elif ref.startswith(("http://", "https://")):
            self.ctl.open_url(ref)
        elif os.path.exists(ref):
            os.startfile(ref)
        else:
            raise FileNotFoundError(ref)
