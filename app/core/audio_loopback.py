"""
"Laptop sound on the phone": captures what the laptop is playing (WASAPI loopback of the default
speakers, plain ctypes — no extra package) and hands 16-bit PCM chunks to every listening phone.

The capture thread only runs while at least one phone listens; each listener gets its own small
queue (a slow phone drops chunks instead of slowing the others down).
"""
from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
import uuid
from array import array
from ctypes import POINTER, byref, c_uint32, c_uint64, c_void_p, wintypes

log = logging.getLogger(__name__)

HRESULT = ctypes.c_long
CLSCTX_ALL = 0x17
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_PCM, WAVE_FORMAT_IEEE_FLOAT, WAVE_FORMAT_EXTENSIBLE = 1, 3, 0xFFFE
E_RENDER, E_CONSOLE = 0, 0


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "GUID":
        u = uuid.UUID(text)
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        g.Data4[:] = list(u.bytes[8:])
        return g


CLSID_MMDeviceEnumerator = GUID.of("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = GUID.of("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioClient = GUID.of("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
IID_IAudioCaptureClient = GUID.of("C8ADBD64-E71E-48A0-A4DE-185C395CD317")
SUBTYPE_FLOAT = uuid.UUID("00000003-0000-0010-8000-00AA00389B71")


class WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1  # mmreg.h structs are byte-packed
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD),
                ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]


class WAVEFORMATEXTENSIBLE(ctypes.Structure):
    _pack_ = 1  # mmreg.h structs are byte-packed
    _fields_ = [("Format", WAVEFORMATEX), ("wValidBitsPerSample", wintypes.WORD), ("dwChannelMask", wintypes.DWORD),
                ("SubFormat", GUID)]


ole32 = ctypes.windll.ole32


def _call(obj: c_void_p, index: int, *args, argtypes=()):
    """Calls method #index of a COM object's vtable."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(HRESULT, c_void_p, *argtypes)(vtbl[index])
    hr = fn(obj, *args)
    if hr < 0:
        raise OSError(f"COM call {index} failed: 0x{hr & 0xFFFFFFFF:08X}")
    return hr


def _release(obj) -> None:
    if obj:
        vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
        ctypes.WINFUNCTYPE(wintypes.ULONG, c_void_p)(vtbl[2])(obj)


class Loopback:
    """One capture session: open() -> read() repeatedly -> close(). Runs in one thread."""

    def __init__(self):
        self.rate = 48000
        self.channels = 2
        self._float = True
        self._bits = 32
        self._src_channels = 2
        self._objs: list[c_void_p] = []
        self._client = c_void_p()
        self._capture = c_void_p()

    def open(self) -> None:
        ole32.CoInitializeEx(None, 0)
        enum = c_void_p()
        hr = ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                    byref(IID_IMMDeviceEnumerator), byref(enum))
        if hr < 0:
            raise OSError(f"no audio devices (0x{hr & 0xFFFFFFFF:08X})")
        self._objs.append(enum)
        device = c_void_p()
        _call(enum, 4, E_RENDER, E_CONSOLE, byref(device), argtypes=(ctypes.c_int, ctypes.c_int, POINTER(c_void_p)))
        self._objs.append(device)
        _call(device, 3, byref(IID_IAudioClient), CLSCTX_ALL, None, byref(self._client),
              argtypes=(POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p)))
        self._objs.append(self._client)
        fmt_ptr = POINTER(WAVEFORMATEX)()
        _call(self._client, 8, byref(fmt_ptr), argtypes=(POINTER(POINTER(WAVEFORMATEX)),))
        fmt = fmt_ptr.contents
        self.rate, self._src_channels, self._bits = fmt.nSamplesPerSec, fmt.nChannels, fmt.wBitsPerSample
        if fmt.wFormatTag == WAVE_FORMAT_EXTENSIBLE:
            ext = ctypes.cast(fmt_ptr, POINTER(WAVEFORMATEXTENSIBLE)).contents
            sub = ext.SubFormat
            self._float = uuid.UUID(fields=(sub.Data1, sub.Data2, sub.Data3, sub.Data4[0], sub.Data4[1],
                                            int.from_bytes(bytes(sub.Data4[2:]), "big"))) == SUBTYPE_FLOAT
        else:
            self._float = fmt.wFormatTag == WAVE_FORMAT_IEEE_FLOAT
        if not ((self._float and self._bits == 32) or (not self._float and self._bits == 16)):
            ole32.CoTaskMemFree(fmt_ptr)
            raise OSError(f"unsupported mix format ({self._bits}-bit)")
        self.channels = min(2, self._src_channels)
        try:
            _call(self._client, 3, AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, 2_000_000, 0, fmt_ptr, None,
                  argtypes=(ctypes.c_int, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong,
                            POINTER(WAVEFORMATEX), c_void_p))
        finally:
            ole32.CoTaskMemFree(fmt_ptr)
        _call(self._client, 14, byref(IID_IAudioCaptureClient), byref(self._capture),
              argtypes=(POINTER(GUID), POINTER(c_void_p)))
        self._objs.append(self._capture)
        _call(self._client, 10)  # Start

    def read(self) -> bytes:
        """All captured audio since the last call, as 16-bit PCM (silence included as zeros)."""
        out = bytearray()
        size = c_uint32()
        while True:
            _call(self._capture, 5, byref(size), argtypes=(POINTER(c_uint32),))
            if not size.value:
                return bytes(out)
            data, frames, flags = POINTER(ctypes.c_ubyte)(), c_uint32(), wintypes.DWORD()
            _call(self._capture, 3, byref(data), byref(frames), byref(flags), None, None,
                  argtypes=(POINTER(POINTER(ctypes.c_ubyte)), POINTER(c_uint32), POINTER(wintypes.DWORD),
                            POINTER(c_uint64), POINTER(c_uint64)))
            n = frames.value
            if flags.value & AUDCLNT_BUFFERFLAGS_SILENT or not data:
                out += bytes(n * self.channels * 2)
            else:
                raw = ctypes.string_at(data, n * self._src_channels * self._bits // 8)
                out += self._to_pcm16(raw, n)
            _call(self._capture, 4, n, argtypes=(c_uint32,))

    def _to_pcm16(self, raw: bytes, frames: int) -> bytes:
        src = array("f" if self._float else "h")
        src.frombytes(raw)
        sc, ch = self._src_channels, self.channels
        if sc != ch:  # 5.1 / 7.1 -> front left / right
            src = array(src.typecode, (src[i * sc + c] for i in range(frames) for c in range(ch)))
        if not self._float:
            return src.tobytes()
        return array("h", (32767 if x >= 1 else -32767 if x <= -1 else int(x * 32767) for x in src)).tobytes()

    def close(self) -> None:
        try:
            if self._client:
                _call(self._client, 11)  # Stop
        except OSError:
            pass
        for obj in reversed(self._objs):
            _release(obj)
        self._objs.clear()
        ole32.CoUninitialize()


class LoopbackHub:
    """Shares one capture between all listening phones (thread-safe)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.listeners: dict[int, queue.Queue] = {}
        self.format: dict | None = None
        self.error = ""
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._next = 0

    def subscribe(self, timeout: float = 4.0) -> tuple[int, queue.Queue, dict] | None:
        with self.lock:
            self._next += 1
            key, q = self._next, queue.Queue(maxsize=40)
            self.listeners[key] = q
            if self._thread is None or not self._thread.is_alive():
                self._ready.clear()
                self.error = ""
                self._thread = threading.Thread(target=self._run, name="loopback", daemon=True)
                self._thread.start()
        self._ready.wait(timeout)
        if not self.format:
            self.unsubscribe(key)
            return None
        return key, q, dict(self.format)

    def unsubscribe(self, key: int) -> None:
        with self.lock:
            self.listeners.pop(key, None)

    @property
    def count(self) -> int:
        with self.lock:
            return len(self.listeners)

    def _run(self) -> None:
        cap = Loopback()
        try:
            cap.open()
            self.format = {"rate": cap.rate, "channels": cap.channels}
        except Exception as exc:
            log.warning("loopback capture unavailable: %s", exc)
            self.error = str(exc)
            self.format = None
            self._ready.set()
            try:
                cap.close()
            except Exception:
                pass
            return
        self._ready.set()
        idle_since = None
        try:
            while True:
                time.sleep(0.02)
                chunk = cap.read()
                with self.lock:
                    queues = list(self.listeners.values())
                    if not queues:
                        idle_since = idle_since or time.monotonic()
                        if time.monotonic() - idle_since > 2:
                            self._thread = None  # a phone that subscribes from now on starts a new capture
                            break
                if not queues:
                    continue
                idle_since = None
                if chunk:
                    for q in queues:
                        try:
                            q.put_nowait(chunk)
                        except queue.Full:  # this phone is behind: drop its oldest chunk
                            try:
                                q.get_nowait()
                                q.put_nowait(chunk)
                            except (queue.Empty, queue.Full):
                                pass
        except Exception as exc:
            log.warning("loopback capture stopped: %s", exc)
        finally:
            with self.lock:
                if self._thread is None or self._thread is threading.current_thread():
                    self.format = None
                    self._thread = None
            cap.close()


HUB = LoopbackHub()
