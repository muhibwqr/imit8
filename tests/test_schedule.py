from datetime import datetime

from jev.schedule import DAYS, ScheduleStore
from jev.scheduler import Scheduler


def stamp(day: str, hour: int, minute: int = 0, week: str = "2024-01-01") -> float:
    """Timestamp for a weekday in the week starting Monday 2024-01-01."""
    monday = datetime.fromisoformat(week)
    return datetime(
        monday.year, monday.month, monday.day + DAYS.index(day), hour, minute
    ).timestamp()


def store(tmp_path) -> ScheduleStore:
    return ScheduleStore(tmp_path / "jev.db")


def test_weekly_next_run_picks_the_soonest_slot(tmp_path):
    s = store(tmp_path).add("water the plants", slots=[(0, 9 * 60), (2, 18 * 60)])
    now = stamp("mon", 10)
    assert s.next_run(now) == stamp("wed", 18)
    assert s.next_run(stamp("wed", 19)) == stamp("mon", 9, week="2024-01-08")


def test_schedule_is_due_within_the_grace_window(tmp_path):
    s = store(tmp_path).add("standup notes", slots=[(0, 9 * 60)])
    assert not s.is_due(stamp("mon", 8, 59))
    assert s.is_due(stamp("mon", 9, 1))
    assert not s.is_due(stamp("mon", 9, 30))  # missed by more than the grace window


def test_marking_a_run_stops_the_same_slot_firing_twice(tmp_path):
    db = store(tmp_path)
    created = db.add("standup notes", slots=[(0, 9 * 60)])
    db.mark_run(created.id, "success", when=stamp("mon", 9, 1))
    again = db.get(created.id)
    assert again is not None
    assert not again.is_due(stamp("mon", 9, 2))
    assert again.is_due(stamp("mon", 9, 1, week="2024-01-08"))


def test_one_off_schedule_runs_once(tmp_path):
    db = store(tmp_path)
    at = stamp("tue", 7)
    once = db.add("book the court", run_at=at)
    assert once.once
    assert once.next_run(stamp("mon", 9)) == at
    assert once.is_due(stamp("tue", 7, 1))
    db.mark_run(once.id, "success")
    done = db.get(once.id)
    assert done is not None
    assert not done.is_due(stamp("tue", 7, 2))


def test_disabled_schedules_never_fire(tmp_path):
    db = store(tmp_path)
    created = db.add("noisy task", slots=[(0, 9 * 60)])
    db.set_enabled(created.id, False)
    off = db.get(created.id)
    assert off is not None
    assert off.next_run(stamp("mon", 8)) is None
    assert not off.is_due(stamp("mon", 9, 1))


def test_upcoming_orders_by_next_run(tmp_path):
    db = store(tmp_path)
    now = datetime.now().timestamp()
    db.add("later", run_at=now + 3600)
    soon = db.add("sooner", run_at=now + 60)
    assert [s.task for s in db.upcoming(2)] == ["sooner", "later"]
    assert db.upcoming(1)[0].id == soon.id


def test_describe_reads_like_a_calendar(tmp_path):
    s = store(tmp_path).add("gym", slots=[(0, 7 * 60), (0, 7 * 60 + 30), (4, 18 * 60)])
    assert s.describe() == "mon 07:00, 07:30 · fri 18:00"


def test_scheduler_tick_fires_due_schedules(tmp_path):
    db = store(tmp_path)
    fired = []
    now = datetime.now()
    db.add("always due", run_at=now.timestamp() - 5)
    db.add("far away", run_at=now.timestamp() + 86400)
    scheduler = Scheduler(fired.append, store=db)
    assert [s.task for s in scheduler.tick()] == ["always due"]
    assert [s.task for s in fired] == ["always due"]
