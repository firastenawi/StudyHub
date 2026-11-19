"""
OBS Studio from the Deck: start / stop recording and streaming, switch scenes.

Talks to OBS's built-in WebSocket server (OBS 28+: Tools > WebSocket Server Settings), protocol v5, with a tiny
WebSocket client of our own (no extra packages). One short connection per action, so a closed OBS never
leaves anything hanging. The password is kept encrypted for this Windows user (DPAPI).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct

TIMEOUT = 3.0


class ObsError(Exception):
    pass


class _WebSocket:
    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((host, port), timeout=TIMEOUT)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: obswebsocket.json\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ObsError("closed")
            head += chunk
        status, _, rest = head.partition(b"\r\n\r\n")
        if b" 101 " not in status.split(b"\r\n", 1)[0]:
            raise ObsError("not a WebSocket server")
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if accept not in status:
            raise ObsError("bad handshake")
        self.buf = rest

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ObsError("closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, obj: dict) -> None:
        data = json.dumps(obj).encode()
        head = bytes([0x81])
        n = len(data)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> dict:
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            op = b0 & 0x0F
            if op == 0x9:      # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + b"\0\0\0\0" + payload)
            elif op == 0x8:
                raise ObsError("closed")
            elif op in (0x1, 0x2):
                return json.loads(payload.decode("utf-8"))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Obs:
    def __init__(self, host: str = "127.0.0.1", port: int = 4455, password: str = ""):
        self.host, self.port, self.password = host or "127.0.0.1", int(port or 4455), password or ""

    def _session(self) -> _WebSocket:
        try:
            ws = _WebSocket(self.host, self.port)
        except OSError as exc:
            raise ObsError("not_running") from exc
        hello = ws.recv()
        auth = (hello.get("d") or {}).get("authentication")
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        if auth:
            if not self.password:
                ws.close()
                raise ObsError("password")
            secret = base64.b64encode(hashlib.sha256((self.password + auth["salt"]).encode()).digest()).decode()
            ident["authentication"] = base64.b64encode(hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
        ws.send({"op": 1, "d": ident})
        try:
            reply = ws.recv()
        except ObsError as exc:
            raise ObsError("password") from exc  # OBS closes the connection on a wrong password
        if reply.get("op") != 2:
            ws.close()
            raise ObsError("password")
        return ws

    def call(self, *requests: tuple[str, dict]) -> list[dict]:
        """Runs requests in one session; returns their responseData (raises ObsError)."""
        ws = self._session()
        out = []
        try:
            for i, (kind, data) in enumerate(requests):
                ws.send({"op": 6, "d": {"requestType": kind, "requestId": str(i), "requestData": data or {}}})
                while True:
                    msg = ws.recv()
                    if msg.get("op") == 7 and msg["d"].get("requestId") == str(i):
                        st = msg["d"].get("requestStatus", {})
                        if not st.get("result"):
                            raise ObsError(st.get("comment") or f"code {st.get('code')}")
                        out.append(msg["d"].get("responseData") or {})
                        break
        finally:
            ws.close()
        return out

    def status(self) -> dict:
        rec, stream, scenes = self.call(("GetRecordStatus", {}), ("GetStreamStatus", {}), ("GetSceneList", {}))
        return {"connected": True, "recording": bool(rec.get("outputActive")), "streaming": bool(stream.get("outputActive")),
                "scene": scenes.get("currentProgramSceneName", ""),
                "scenes": [s.get("sceneName", "") for s in reversed(scenes.get("scenes", []))]}

    def do(self, action: str, arg: str = "") -> None:
        if action == "record":
            self.call(("ToggleRecord", {}))
        elif action == "stream":
            self.call(("ToggleStream", {}))
        elif action == "scene" and arg:
            self.call(("SetCurrentProgramScene", {"sceneName": arg}))
        else:
            raise ObsError("unknown")


def save_password(db, password: str) -> None:
    from app.core.vault import protect
    db.set_setting("phone_obs_pw", base64.b64encode(protect(password.encode("utf-8"), b"StudyHub OBS")).decode() if password else "")


def load_password(db) -> str:
    blob = db.get_setting("phone_obs_pw", "") or ""
    if not blob:
        return ""
    try:
        from app.core.vault import unprotect
        return unprotect(base64.b64decode(blob), b"StudyHub OBS").decode("utf-8")
    except Exception:
        return ""


def from_settings(db) -> Obs:
    conf = db.get_setting("phone_obs", {}) or {}
    return Obs(conf.get("host", "127.0.0.1"), conf.get("port", 4455), load_password(db))
