"""
The laptop's power from the phone's Deck: lock, sleep, screen off / on, and the network addresses the phone app
uses for Wake-on-LAN (it only works if the laptop's network card and BIOS allow waking over the network;
many laptops don't over Wi-Fi).
"""
from __future__ import annotations

import ctypes
import socket

user32 = ctypes.windll.user32
HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER = 0xFFFF, 0x0112, 0xF170
VIRTUAL = ("00:15:5D", "00:50:56", "00:0C:29", "00:05:69", "08:00:27", "0A:00:27", "00:1C:42")  # Hyper-V, VMware, VirtualBox, Parallels


def lock() -> bool:
    return bool(user32.LockWorkStation())


def sleep() -> bool:
    """Sleep (not hibernate). Windows may refuse when an app blocks it."""
    return bool(ctypes.windll.powrprof.SetSuspendState(False, False, False))


def screen_off() -> None:
    # Post, not Send: SendMessage to every window can hang on one that doesn't answer
    user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)


def screen_on() -> None:
    from app.core import remote_input
    remote_input.move(1, 0)   # any input wakes the screen
    remote_input.move(-1, 0)


def mac_addresses() -> list[str]:
    """MACs of the network adapters that have an IPv4 address (for Wake-on-LAN from the phone)."""
    try:
        import psutil
    except ImportError:
        return []
    out = []
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        st = stats.get(name)
        if not st or not st.isup or "loopback" in name.lower():
            continue
        if not any(a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")) for a in addrs):
            continue
        for a in addrs:
            if a.family == psutil.AF_LINK and a.address and a.address.count("-") == 5:
                mac = a.address.replace("-", ":").upper()
                if mac.startswith(VIRTUAL) or int(mac[:2], 16) & 0x02:  # virtual / randomised: can't wake anything
                    continue
                if mac != "00:00:00:00:00:00" and mac not in out:
                    out.append(mac)
    return out
