"""
"Play on the laptop": music files picked on the phone are uploaded to a temporary folder and played
here (a queue), controlled from the phone. The folder is emptied when Study Hub starts and when the
queue is cleared. GUI thread only.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PyQt6.QtCore import QObject, QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

from app.config import DATA_DIR

MUSIC_DIR = DATA_DIR / "phone_music"
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma", ".webm", ".mp4"}


def clear_folder() -> None:
    shutil.rmtree(MUSIC_DIR, ignore_errors=True)


class PhoneMusic(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.queue: list[dict] = []        # {path, title}
        self.index = -1
        self.output = QAudioOutput(self)
        self.output.setVolume(0.8)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.output)
        self.player.mediaStatusChanged.connect(self._status)

    # ------------------------------------------------------------- control
    def add(self, path: str, title: str) -> None:
        self.queue.append({"path": path, "title": title})
        if self.index < 0 or self.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
            self.play_index(len(self.queue) - 1)

    def play_index(self, i: int) -> None:
        if 0 <= i < len(self.queue):
            self.index = i
            self.player.setSource(QUrl.fromLocalFile(self.queue[i]["path"]))
            self.player.play()

    def action(self, name: str, value=None) -> None:
        p = self.player
        if name == "toggle":
            if p.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                p.pause()
            elif self.index >= 0:
                p.play()
        elif name == "next":
            self.play_index(min(self.index + 1, len(self.queue) - 1))
        elif name == "prev":
            if p.position() > 3000:
                p.setPosition(0)
            else:
                self.play_index(max(self.index - 1, 0))
        elif name == "stop":
            p.stop()
        elif name == "volume":
            self.output.setVolume(max(0.0, min(1.0, float(value or 0) / 100)))
        elif name == "seek":
            p.setPosition(int(max(0.0, float(value or 0)) * 1000))
        elif name == "play" and value is not None:
            self.play_index(int(value))
        elif name == "clear":
            p.stop()
            p.setSource(QUrl())
            self.queue, self.index = [], -1
            clear_folder()

    def _status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.index + 1 < len(self.queue):
            self.play_index(self.index + 1)

    def state(self) -> dict:
        p = self.player
        return {"queue": [q["title"] for q in self.queue], "index": self.index,
                "playing": p.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                "position": p.position() // 1000, "duration": max(0, p.duration()) // 1000,
                "volume": round(self.output.volume() * 100), "error": p.errorString() if p.error() else ""}
