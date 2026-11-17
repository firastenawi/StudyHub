"""
Pomodoro engine (no UI).

Lives in the controller, so the timer keeps running while you switch pages or
hide the window. Uses a monotonic deadline instead of counting ticks, so it
stays exact even if the UI is busy.

Cycle:  focus -> short break -> focus -> ... -> every Nth focus -> long break
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

PHASES = ("focus", "short_break", "long_break")
DEFAULT_DURATIONS = {"focus": 25, "short_break": 5, "long_break": 15}


class PomodoroTimer(QObject):
    tick = pyqtSignal(int, int)               # remaining seconds, total seconds
    state_changed = pyqtSignal(str)           # idle / running / paused
    phase_changed = pyqtSignal(str)           # focus / short_break / long_break
    session_finished = pyqtSignal(str, int)   # finished phase, its length in minutes

    def __init__(self, durations: dict | None = None, long_every: int = 4,
                 auto_start: bool = False, parent: QObject | None = None):
        super().__init__(parent)
        self.durations = dict(DEFAULT_DURATIONS, **(durations or {}))
        self.long_every = max(2, long_every)
        self.auto_start = auto_start
        self.phase = "focus"
        self.state = "idle"
        self.completed_focus = 0          # focus sessions finished in the current cycle
        self.total = self.durations["focus"] * 60
        self.remaining = self.total
        self._deadline = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._update)

    # ------------------------------------------------------------ control
    def start(self) -> None:
        if self.state == "running":
            return
        self._deadline = time.monotonic() + self.remaining
        self._timer.start()
        self._set_state("running")

    def pause(self) -> None:
        if self.state != "running":
            return
        self._timer.stop()
        self.remaining = max(0, int(round(self._deadline - time.monotonic())))
        self._set_state("paused")

    def toggle(self) -> None:
        self.pause() if self.state == "running" else self.start()

    def reset(self) -> None:
        """Back to the beginning of the current phase."""
        self._timer.stop()
        self.total = self.durations[self.phase] * 60
        self.remaining = self.total
        self._set_state("idle")
        self.tick.emit(self.remaining, self.total)

    def skip(self) -> None:
        """Jump to the next phase without recording the current one."""
        self._advance(record=False)

    def set_phase(self, phase: str) -> None:
        if phase in PHASES:
            self.phase = phase
            self.phase_changed.emit(phase)
            self.reset()

    def set_durations(self, durations: dict, long_every: int | None = None) -> None:
        self.durations.update({k: max(1, int(v)) for k, v in durations.items() if k in PHASES})
        if long_every:
            self.long_every = max(2, int(long_every))
        if self.state == "idle":
            self.reset()

    @property
    def progress(self) -> float:
        """0 -> 1 through the current phase."""
        return 1.0 - (self.remaining / self.total) if self.total else 0.0

    # ----------------------------------------------------------- internals
    def _set_state(self, state: str) -> None:
        self.state = state
        self.state_changed.emit(state)

    def _update(self) -> None:
        self.remaining = max(0, int(round(self._deadline - time.monotonic())))
        self.tick.emit(self.remaining, self.total)
        if self.remaining <= 0:
            self._advance(record=True)

    def _advance(self, record: bool) -> None:
        self._timer.stop()
        finished = self.phase
        if record:
            self.session_finished.emit(finished, self.durations[finished])
        if finished == "focus":
            if record:
                self.completed_focus += 1
            long_due = record and self.completed_focus % self.long_every == 0
            self.phase = "long_break" if long_due else "short_break"
        else:
            if finished == "long_break":
                self.completed_focus = 0
            self.phase = "focus"
        self.phase_changed.emit(self.phase)
        self.reset()
        if record and self.auto_start:
            self.start()
