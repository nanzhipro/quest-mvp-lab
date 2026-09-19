"""Cron/timedelta schedule parsing and next-fire computation."""
from datetime import datetime, timedelta

import pytest

from miniflow.schedule import CronSchedule, DeltaSchedule, OnceSchedule, normalize_schedule

BASE = datetime(2026, 9, 8, 10, 23, 45)  # a Tuesday


def test_daily_cron_fires_at_next_midnight():
    sched = CronSchedule("0 0 * * *")
    assert sched.next_after(BASE) == datetime(2026, 9, 9, 0, 0)


def test_minute_step_cron():
    sched = CronSchedule("*/15 * * * *")
    assert sched.next_after(BASE) == datetime(2026, 9, 8, 10, 30)


def test_daily_macro_maps_to_midnight_cron():
    sched = normalize_schedule("@daily")
    assert isinstance(sched, CronSchedule)
    assert sched.next_after(BASE) == datetime(2026, 9, 9, 0, 0)


def test_cron_range_and_list():
    sched = CronSchedule("0 9-17 * * 1,3")  # 09:00 on Mon/Wed
    assert sched.next_after(BASE) == datetime(2026, 9, 9, 9, 0)  # Wednesday


def test_timedelta_schedule():
    sched = DeltaSchedule(timedelta(hours=1))
    assert sched.next_after(BASE) == BASE + timedelta(hours=1)


def test_once_schedule_fires_never_via_next_after():
    assert isinstance(normalize_schedule("@once"), OnceSchedule)
    assert normalize_schedule("@once").next_after(BASE) is None


def test_none_schedule_stays_none():
    assert normalize_schedule(None) is None


def test_invalid_cron_raises():
    with pytest.raises(ValueError):
        CronSchedule("0 0 *")
    with pytest.raises(TypeError):
        normalize_schedule(42)
