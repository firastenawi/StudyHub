"""
Alarm scheduling (no UI).

* checks every second; an alarm rings once per minute at most (last_fired)
* an alarm missed by up to 2 minutes (busy PC, waking from sleep) still rings
* one-time alarms switch themselves off after ringing
* snoozes are kept in memory: snoozed alarms ring again after N minutes
* class reminders: N minutes before each class in the weekly timetable
"""
from __future__ import annotations

from datetime import datetime, timedelta

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

GRACE = timedelta(minutes=2)


def parse_days(days: str) -> list[int]:
    return sorted({int(d) for d in (days or "").split(",") if d.strip().isdigit() and 0 <= int(d) <= 6})


def join_days(days) -> str:
    return ",".join(str(d) for d in sorted(set(days)))


def _at(day: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":")[:2])
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def next_occurrence(alarm: dict, now: datetime | None = None) -> datetime | None:
    """When an enabled alarm will ring next (None if disabled)."""
    if not alarm.get("enabled"):
        return None
    now = now or datetime.now()
    days = parse_days(alarm.get("days", ""))
    for offset in range(0, 8):
        candidate = _at(now + timedelta(days=offset), alarm["time"])
        if candidate <= now:
            continue
        if not days or candidate.weekday() in days:
            return candidate
    return None


def until_text(target: datetime, now: datetime | None = None) -> tuple[int, int, int]:
    """(days, hours, minutes) until target."""
    delta = target - (now or datetime.now())
    minutes = max(0, int(delta.total_seconds() // 60) + 1)
    return minutes // 1440, (minutes % 1440) // 60, minutes % 60


class AlarmScheduler(QObject):
    ring = pyqtSignal(dict, int)       # alarm row, how many times it was already snoozed
    reminder = pyqtSignal(dict, int)   # timetable slot, minutes before it starts

    def __init__(self, db, parent: QObject | None = None):
        super().__init__(parent)
        self.db = db
        self._snoozed: dict[int, tuple[datetime, int]] = {}   # alarm id -> (ring at, snooze count)
        self._reminded: set[str] = set()
        self._timer = QTimer(self, interval=1000, timeout=self.check)
        self._timer.start()

    def snooze(self, alarm: dict, count: int) -> datetime:
        at = datetime.now().replace(microsecond=0) + timedelta(minutes=max(1, int(alarm["snooze_min"])))
        self._snoozed[alarm["id"]] = (at, count + 1)
        return at

    def cancel_snooze(self, alarm_id: int) -> None:
        self._snoozed.pop(alarm_id, None)

    def snoozed_until(self, alarm_id: int) -> datetime | None:
        entry = self._snoozed.get(alarm_id)
        return entry[0] if entry else None

    def check(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        stamp = now.strftime("%Y-%m-%d %H:%M")
        for alarm in self.db.list_alarms():
            if not alarm["enabled"]:
                continue
            due = _at(now, alarm["time"])
            days = parse_days(alarm["days"])
            in_window = due <= now < due + GRACE
            if not in_window or (days and due.weekday() not in days):
                continue
            fired_key = due.strftime("%Y-%m-%d %H:%M")
            if alarm["last_fired"] == fired_key:
                continue
            changes = {"last_fired": fired_key}
            if not days:
                changes["enabled"] = 0  # one-time alarm: done
            self.db.update_alarm(alarm["id"], **changes)
            self._snoozed.pop(alarm["id"], None)
            self.ring.emit(dict(alarm, **changes), 0)
        for alarm_id, (at, count) in list(self._snoozed.items()):
            if now >= at:
                del self._snoozed[alarm_id]
                alarm = self.db.get_alarm(alarm_id)
                if alarm:
                    self.ring.emit(alarm, count)
        self._check_classes(now, stamp)

    def _check_classes(self, now: datetime, stamp: str) -> None:
        minutes = int(self.db.get_setting("class_reminder_min", 0) or 0)
        if minutes <= 0:
            return
        for slot in self.db.slots_on_day(now.weekday()):
            start = _at(now, slot["start_time"])
            remind_at = start - timedelta(minutes=minutes)
            key = f"{slot['id']}@{start:%Y-%m-%d %H:%M}"
            if remind_at <= now < remind_at + GRACE and key not in self._reminded:
                self._reminded.add(key)
                self.reminder.emit(slot, minutes)
