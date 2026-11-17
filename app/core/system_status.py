"""
Wi-Fi, VPN, sound, brightness and battery for the taskbar's status icons — read and controlled with what Windows
already has (no extra installs):

  sound       Core Audio (IAudioEndpointVolume) — volume and mute of the default speakers
  brightness  WMI (WmiMonitorBrightness) — the laptop panel; external monitors usually don't support it
  Wi-Fi       netsh wlan — the network you're on, networks around, connect to a saved one, disconnect
  VPN         Windows VPN connections (Get-VpnConnection / rasdial) + any VPN adapter that is up (Proton, WireGuard…)
  battery     percent, charging, time left
Slow things (netsh, PowerShell) run in a background thread; the taskbar gets a `changed` signal.
"""
from __future__ import annotations

import ctypes
import json
import re
import subprocess
import threading
import time
from ctypes import wintypes

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.core.logs import log

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(args: list[str], timeout: float = 8) -> str:
    try:
        out = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=_NOWIN)
        raw = out.stdout
        for enc in ("utf-8", "mbcs"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _ps(script: str, timeout: float = 10) -> str:
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script], timeout)


# ===================================================================== sound (Core Audio)
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        import uuid
        u = uuid.UUID(text)
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        g.Data4[:] = list(u.bytes[8:])
        return g


def _vt(obj, index, *args):
    vtbl = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *args)(vtbl[index])


def _endpoint_volume():
    ole = ctypes.windll.ole32
    ole.CoInitializeEx(None, 2)  # already initialised on the GUI thread: harmless
    enum = ctypes.c_void_p()
    if ole.CoCreateInstance(ctypes.byref(_GUID.of("{BCDE0395-E52F-467C-8E3D-C4579291692E}")), None, 1,
                            ctypes.byref(_GUID.of("{A95664D2-9614-4F35-A746-DE8DB63617E6}")), ctypes.byref(enum)):
        return None
    dev = ctypes.c_void_p()
    try:
        if _vt(enum, 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(enum, 0, 1, ctypes.byref(dev)):
            return None  # no speakers
        vol = ctypes.c_void_p()
        hr = _vt(dev, 3, ctypes.POINTER(_GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(
            dev, ctypes.byref(_GUID.of("{5CDF2C82-841E-4546-9722-0CF74078229A}")), 23, None, ctypes.byref(vol))
        _vt(dev, 2)(dev)
        return vol if hr == 0 else None
    finally:
        _vt(enum, 2)(enum)


def get_volume() -> tuple[int, bool] | None:
    vol = _endpoint_volume()
    if vol is None:
        return None
    try:
        level = ctypes.c_float()
        mute = wintypes.BOOL()
        _vt(vol, 9, ctypes.POINTER(ctypes.c_float))(vol, ctypes.byref(level))
        _vt(vol, 15, ctypes.POINTER(wintypes.BOOL))(vol, ctypes.byref(mute))
        return int(round(level.value * 100)), bool(mute.value)
    finally:
        _vt(vol, 2)(vol)


def set_volume(percent: int | None = None, mute: bool | None = None) -> None:
    vol = _endpoint_volume()
    if vol is None:
        return
    try:
        if percent is not None:
            _vt(vol, 7, ctypes.c_float, ctypes.c_void_p)(vol, max(0.0, min(1.0, percent / 100)), None)
        if mute is not None:
            _vt(vol, 14, wintypes.BOOL, ctypes.c_void_p)(vol, bool(mute), None)
    finally:
        _vt(vol, 2)(vol)


# ===================================================================== brightness (WMI)
def get_brightness() -> int | None:
    out = _ps("(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness -ErrorAction Stop | "
              "Select-Object -First 1).CurrentBrightness", 8).strip()
    return int(out) if out.isdigit() else None


def set_brightness(percent: int) -> None:
    _ps("$m = Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop | "
        f"Select-Object -First 1; Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness "
        f"-Arguments @{{Timeout=1; Brightness=[byte]{max(0, min(100, int(percent)))}}} | Out-Null", 8)


# ===================================================================== Wi-Fi (netsh)
def _kv(line: str):
    if ":" not in line:
        return None, None
    k, v = line.split(":", 1)
    return k.strip(), v.strip()


def wifi_now() -> dict:
    """{"ssid", "signal", "connected", "has_wifi"} — works whatever Windows' display language is."""
    out = _run(["netsh", "wlan", "show", "interfaces"], 6)
    info = {"ssid": "", "signal": 0, "connected": False, "has_wifi": bool(out.strip()) and "wlansvc" not in out.lower()}
    for line in out.splitlines():
        k, v = _kv(line)
        if not k:
            continue
        if k.upper() == "SSID" or (k.upper().endswith(" SSID") and "BSSID" not in k.upper()):
            info["ssid"] = v
        elif v.endswith("%") and v[:-1].strip().isdigit():
            info["signal"] = int(v[:-1].strip())
    info["connected"] = bool(info["ssid"])
    if "There is no wireless interface" in out or not re.search(r"SSID", out):
        info["has_wifi"] = "SSID" in out or info["connected"]
    return info


def wifi_networks() -> list[dict]:
    """Networks around: [{"ssid", "signal", "secure", "saved"}], strongest first."""
    out = _run(["netsh", "wlan", "show", "networks", "mode=bssid"], 10)
    saved = set(wifi_profiles())
    nets: dict[str, dict] = {}
    current = None
    for line in out.splitlines():
        m = re.match(r"^\s*SSID\s+\d+\s*:\s*(.*)$", line)
        if m:
            name = m.group(1).strip()
            current = nets.setdefault(name, {"ssid": name, "signal": 0, "secure": True, "saved": name in saved}) if name else None
            continue
        k, v = _kv(line)
        if current is not None and k and v.endswith("%") and v[:-1].strip().isdigit():
            current["signal"] = max(current["signal"], int(v[:-1].strip()))
        elif current is not None and k and v.lower() in ("open", "none", "مفتوح"):
            current["secure"] = False
    return sorted(nets.values(), key=lambda n: -n["signal"])


def wifi_profiles() -> list[str]:
    out = _run(["netsh", "wlan", "show", "profiles"], 6)
    names = []
    for line in out.splitlines():
        k, v = _kv(line)
        if k and v and ("profile" in k.lower() or "ملف" in k) and "profiles on" not in k.lower():
            names.append(v)
    return names


def wifi_connect(ssid: str) -> str:
    return _run(["netsh", "wlan", "connect", f"name={ssid}", f"ssid={ssid}"], 10).strip()


def wifi_disconnect() -> str:
    return _run(["netsh", "wlan", "disconnect"], 6).strip()


# ===================================================================== VPN
_VPN_WORDS = ("vpn", "proton", "wireguard", "openvpn", "tap-windows", "wintun", "nordlynx", "mullvad", "tunnel")


def vpn_status() -> dict:
    """{"windows": [{"name", "connected"}], "adapters": [names of VPN adapters that are up]}"""
    out = _ps("Get-VpnConnection -ErrorAction SilentlyContinue | Select-Object Name,ConnectionStatus | ConvertTo-Json -Compress", 10)
    conns = []
    try:
        data = json.loads(out) if out.strip() else []
        data = [data] if isinstance(data, dict) else data
        conns = [{"name": d.get("Name", ""), "connected": str(d.get("ConnectionStatus", "")).lower() == "connected"}
                 for d in data if d.get("Name")]
    except ValueError:
        pass
    adapters = []
    try:
        import psutil
        for name, st in psutil.net_if_stats().items():
            if st.isup and any(w in name.lower() for w in _VPN_WORDS):
                adapters.append(name)
    except Exception:
        pass
    return {"windows": conns, "adapters": adapters}


def vpn_set(name: str, connect: bool) -> str:
    args = ["rasdial", name] + ([] if connect else ["/disconnect"])
    return _run(args, 30).strip()


# ===================================================================== battery
def battery() -> dict | None:
    try:
        import psutil
        b = psutil.sensors_battery()
    except Exception:
        b = None
    if b is None:
        return None
    left = b.secsleft if isinstance(b.secsleft, int) and b.secsleft > 0 else None
    return {"percent": int(round(b.percent)), "plugged": bool(b.power_plugged), "secs_left": left}


# ===================================================================== the poller
class SystemStatus(QObject):
    """ctl.system — the latest state of all five, refreshed in the background."""
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = {"wifi": {"ssid": "", "signal": 0, "connected": False, "has_wifi": True}, "vpn": {"windows": [], "adapters": []},
                      "volume": None, "brightness": None, "battery": battery()}
        self._busy = False
        self._ticks = 0
        self._timer = QTimer(self, interval=5000, timeout=self.refresh)
        self._timer.start()
        QTimer.singleShot(1500, self.refresh)

    def refresh(self, full: bool = False) -> None:
        try:
            self.state["volume"] = get_volume()
        except Exception:
            self.state["volume"] = None
        self.state["battery"] = battery()
        self.changed.emit()
        if self._busy:
            return
        self._busy = True
        slow = full or self._ticks % 4 == 0  # VPN and brightness every ~20 s
        self._ticks += 1
        threading.Thread(target=self._work, args=(slow,), name="system-status", daemon=True).start()

    def _work(self, slow: bool) -> None:
        try:
            self.state["wifi"] = wifi_now()
            if slow:
                self.state["vpn"] = vpn_status()
                self.state["brightness"] = get_brightness()
        except Exception:
            log.exception("system status")
        finally:
            self._busy = False
        self.changed.emit()  # queued into the GUI thread (signal across threads)
