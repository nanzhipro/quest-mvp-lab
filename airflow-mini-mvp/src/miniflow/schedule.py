"""Schedule parsing and next-fire computation.

Mirrors Airflow's timetables: a cron string becomes something like
``CronDataIntervalTimetable``, a ``timedelta`` becomes
``DeltaDataIntervalTimetable``, and the ``@daily`` / ``@hourly`` / ... macros
map to fixed cron expressions. ``next_after(schedule, dt)`` returns the first
fire time strictly after ``dt``; a DagRun for ``logical_date`` covers the data
interval ``[logical_date, next_after(logical_date))``.
"""
from __future__ import annotations

from datetime import datetime, timedelta

CRON_MACROS = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
}


def _parse_field(field: str, lo: int, hi: int) -> set[int] | None:
    """Parse one cron field into a set of ints, or None for ``*``."""
    if field == "*":
        return None
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part in ("*", ""):
            start, end = lo, hi
        elif "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
        else:
            start = end = int(part)
        values.update(range(start, end + 1, step))
    return values


class CronSchedule:
    """5-field cron schedule (minute hour day-of-month month day-of-week).

    Day-of-month and day-of-week follow standard cron OR semantics when both
    are restricted. Day-of-week: 0 and 7 are Sunday.
    """

    def __init__(self, expr: str):
        expr = CRON_MACROS.get(expr, expr)
        fields = expr.split()
        if len(fields) != 5:
            raise ValueError(f"cron expression needs 5 fields, got {expr!r}")
        self.expr = expr
        self.minutes = _parse_field(fields[0], 0, 59)
        self.hours = _parse_field(fields[1], 0, 23)
        self.dom = _parse_field(fields[2], 1, 31)
        self.months = _parse_field(fields[3], 1, 12)
        dow = _parse_field(fields[4], 0, 7)
        self.dow = {d % 7 for d in dow} if dow is not None else None  # 0 = Sunday

    def _day_matches(self, t: datetime) -> bool:
        dom_ok = self.dom is None or t.day in self.dom
        cron_dow = (t.weekday() + 1) % 7  # Python Mon=0 -> cron Sun=0
        dow_ok = self.dow is None or cron_dow in self.dow
        if self.dom is not None and self.dow is not None:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    def next_after(self, dt: datetime) -> datetime:
        t = (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)
        for _ in range(366 * 24 * 60 * 5):  # cap: ~5 years of minutes
            if self.months is not None and t.month not in self.months:
                t = (t.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0)
            elif not self._day_matches(t):
                t = (t + timedelta(days=1)).replace(hour=0, minute=0)
            elif self.hours is not None and t.hour not in self.hours:
                t = (t + timedelta(hours=1)).replace(minute=0)
            elif self.minutes is not None and t.minute not in self.minutes:
                later = [m for m in self.minutes if m > t.minute]
                t = t.replace(minute=min(later)) if later else (t + timedelta(hours=1)).replace(minute=0)
            else:
                return t
        raise ValueError(f"no fire time within 5 years for cron {self.expr!r}")

    def __repr__(self) -> str:
        return f"CronSchedule({self.expr!r})"


class DeltaSchedule:
    """Fixed-interval schedule. Mirrors ``DeltaDataIntervalTimetable``."""

    def __init__(self, delta: timedelta):
        if delta.total_seconds() <= 0:
            raise ValueError("timedelta schedule must be positive")
        self.delta = delta

    def next_after(self, dt: datetime) -> datetime:
        return dt + self.delta

    def __repr__(self) -> str:
        return f"DeltaSchedule({self.delta!r})"


class OnceSchedule:
    """Fire exactly once. Mirrors Airflow's ``@once``.

    Stateless: ``next_after`` always returns None; the scheduler special-cases
    this schedule type and creates a single DagRun if none exists yet.
    """

    def next_after(self, dt: datetime) -> datetime | None:
        return None

    def __repr__(self) -> str:
        return "OnceSchedule()"


def normalize_schedule(schedule):
    """Map an Airflow-style ``schedule=`` argument to a schedule object.

    Accepts None (unscheduled), ``"@once"``, ``"@daily"``-style macros,
    5-field cron strings, and ``timedelta``.
    """
    if schedule is None:
        return None
    if isinstance(schedule, timedelta):
        return DeltaSchedule(schedule)
    if isinstance(schedule, str):
        if schedule == "@once":
            return OnceSchedule()
        return CronSchedule(schedule)
    raise TypeError(f"unsupported schedule: {schedule!r}")


def describe_schedule(schedule) -> str:
    if schedule is None:
        return "None"
    return repr(schedule)
