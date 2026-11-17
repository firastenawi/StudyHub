"""Single-instance support: the *server* side (runs inside the main app)."""
from __future__ import annotations

import json

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer

from app.config import PIPE_NAME


class CommandServer(QObject):
    """Listens on the named pipe and emits every JSON command it receives."""

    command_received = pyqtSignal(dict)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        QLocalServer.removeServer(PIPE_NAME)  # cleans stale endpoints (no-op on Windows)
        self.listening = self._server.listen(PIPE_NAME)
        self._server.newConnection.connect(self._on_new_connection)

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            buffer = bytearray()

            def read(sock=sock, buffer=buffer) -> None:
                buffer.extend(bytes(sock.readAll()))
                while b"\n" in buffer:  # one JSON command per line
                    line, _, rest = bytes(buffer).partition(b"\n")
                    buffer[:] = rest
                    self._dispatch(line)

            sock.readyRead.connect(read)
            sock.disconnected.connect(lambda sock=sock, read=read: (read(), sock.deleteLater()))
            read()  # data may already be waiting

    def _dispatch(self, line: bytes) -> None:
        try:
            command = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(command, dict):
            self.command_received.emit(command)
