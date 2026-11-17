"""
Alarm sounds.

Built-in sounds are synthesized once into WAV files (no downloads, no copyright issues);
any audio file the user picks (mp3 / wav / ogg / m4a) also works. Playback loops until
stopped, and can start quietly and rise to the chosen volume ("gradual wake-up").
"""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

from app.config import DATA_DIR

SOUNDS_DIR = DATA_DIR / "sounds"
RATE = 22050
BUILTIN = ["gentle", "chime", "classic", "digital", "rise"]


def _tone(freq: float, seconds: float, volume: float = 0.6, decay: float = 0.0, harmonics=(1.0,)) -> list[float]:
    n = int(RATE * seconds)
    out = []
    for i in range(n):
        t = i / RATE
        env = min(1.0, i / (RATE * 0.01)) * min(1.0, (n - i) / (RATE * 0.02))  # click-free edges
        if decay:
            env *= math.exp(-decay * t)
        s = sum(a * math.sin(2 * math.pi * freq * (k + 1) * t) for k, a in enumerate(harmonics))
        out.append(volume * env * s / sum(harmonics))
    return out


def _silence(seconds: float) -> list[float]:
    return [0.0] * int(RATE * seconds)


def _compose(name: str) -> list[float]:
    if name == "gentle":  # soft major arpeggio, like a music box
        notes = [523.25, 659.25, 783.99, 1046.5, 783.99, 659.25]
        return sum((_tone(f, 0.42, 0.45, 3.0, (1, 0.3)) for f in notes), []) + _silence(0.6)
    if name == "chime":  # bell with long decay
        return (_tone(880, 1.2, 0.6, 2.2, (1, 0.5, 0.25, 0.12)) + _tone(1318.5, 1.4, 0.5, 1.8, (1, 0.4, 0.2))
                + _silence(0.4))
    if name == "classic":  # beep-beep
        return (_tone(880, 0.12, 0.7) + _silence(0.08)) * 4 + _silence(0.6)
    if name == "digital":  # quick triple chirps
        return (_tone(1760, 0.06, 0.55) + _silence(0.05)) * 3 + _silence(0.45)
    if name == "rise":  # sweep upwards
        n = int(RATE * 1.6)
        return [0.55 * math.sin(2 * math.pi * (400 + 500 * i / n) * i / RATE) * min(1, i / 800) * min(1, (n - i) / 800)
                for i in range(n)] + _silence(0.4)
    return _silence(1)


def builtin_path(name: str) -> Path:
    """Creates the WAV the first time it's needed."""
    path = SOUNDS_DIR / f"{name}.wav"
    if not path.exists():
        SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
        samples = _compose(name)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples))
    return path


def source_path(sound: str) -> Path:
    return builtin_path(sound) if sound in BUILTIN else Path(sound)


class SoundPlayer(QObject):
    """Plays one sound at a time; loop + gradual volume."""

    FADE_SECONDS = 30

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.output)
        self._target = 0.8
        self._fade = QTimer(self, interval=500, timeout=self._fade_step)
        self._stop_timer = QTimer(self, singleShot=True, timeout=self.stop)

    def play(self, sound: str, volume: int = 80, gradual: bool = False, loop: bool = True,
             max_seconds: int | None = None) -> bool:
        path = source_path(sound)
        if not path.exists():
            path = builtin_path("gentle")  # the chosen file was moved/deleted: still wake the user
        self.stop()
        self._target = max(0.0, min(1.0, volume / 100))
        self.output.setVolume(0.08 if gradual else self._target)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.setLoops(QMediaPlayer.Loops.Infinite if loop else 1)
        self.player.play()
        if gradual:
            self._fade.start()
        if max_seconds:
            self._stop_timer.start(max_seconds * 1000)
        return True

    def preview(self, sound: str, volume: int = 80) -> None:
        self.play(sound, volume, gradual=False, loop=False, max_seconds=6)

    def _fade_step(self) -> None:
        step = self._target / (self.FADE_SECONDS * 2)
        new = min(self._target, self.output.volume() + step)
        self.output.setVolume(new)
        if new >= self._target:
            self._fade.stop()

    def stop(self) -> None:
        self._fade.stop()
        self._stop_timer.stop()
        self.player.stop()

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
