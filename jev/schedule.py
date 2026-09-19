"""Calendar scheduling: run a flow on a weekly grid of time slots, or once.

A *slot* is a (weekday, minute-of-day) pair — exactly what the grid view in the
calendar window paints. A schedule owns a set of slots and fires whenever the
clock passes one of them; one-off schedules carry a single absolute timestamp
instead.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY,
    task TEXT NOT NULL,
    slots TEXT NOT NULL DEFAULT '[]',
    run_at REAL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    last_run_at REAL,
    last_status TEXT
);
"""

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SLOT_MINUTES = 30
SLOTS_PER_DAY = 24 * 60 // SLOT_MINUTES


def slot_label(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


@dataclass
class Schedule:
    id: int
    task: str
    slots: list[tuple[int, int]]  # (weekday 0=mon, minute of day)
    run_at: float | None
    enabled: bool
    created_at: float
    last_run_at: float | None
    last_status: str | None

    @property
    def once(self) -> bool:
        return self.run_at is not None and not self.slots

    def next_run(self, now: float | None = None) -> float | None:
        """Soonest future firing time, or None when nothing is left to run."""
        now = time.time() if now is None else now
        if not self.enabled:
            return None
        if self.once:
            assert self.run_at is not None
            return self.run_at if self.run_at > now else None
        if not self.slots:
            return None
        current = datetime.fromtimestamp(now)
        best: float | None = None
        for day_offset in range(8):
            day = (current + timedelta(days=day_offset)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            for weekday, minute in self.slots:
                if day.weekday() != weekday:
                    continue
                stamp = (day + timedelta(minutes=minute)).timestamp()
                if stamp > now and (best is None or stamp < best):
                    best = stamp
        return best

    def is_due(self, now: float | None = None) -> bool:
        """True when a slot passed since the last run (within a 5 minute grace)."""
        now = time.time() if now is None else now
        if not self.enabled:
            return False
        if self.once:
            assert self.run_at is not None
            return self.last_run_at is None and self.run_at <= now
        previous = self._previous_slot(now)
        if previous is None:
            return False
        if now - previous > 300:
            return False
        return self.last_run_at is None or self.last_run_at < previous

    def _previous_slot(self, now: float) -> float | None:
        current = datetime.fromtimestamp(now)
        best: float | None = None
        for day_offset in range(8):
            day = (current - timedelta(days=day_offset)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            for weekday, minute in self.slots:
                if day.weekday() != weekday:
                    continue
                stamp = (day + timedelta(minutes=minute)).timestamp()
                if stamp <= now and (best is None or stamp > best):
                    best = stamp
        return best

    def describe(self) -> str:
        if self.once:
            assert self.run_at is not None
            return datetime.fromtimestamp(self.run_at).strftime("once on %a %d %b at %H:%M")
        by_day: dict[int, list[int]] = {}
        for weekday, minute in sorted(self.slots):
            by_day.setdefault(weekday, []).append(minute)
        parts = [
            f"{DAYS[day]} {', '.join(slot_label(m) for m in minutes)}"
            for day, minutes in sorted(by_day.items())
        ]
        return " · ".join(parts) if parts else "no slots"


class ScheduleStore:
    def __init__(self, path: Path | str = DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _row(self, row: sqlite3.Row) -> Schedule:
        return Schedule(
            id=row["id"],
            task=row["task"],
            slots=[tuple(s) for s in json.loads(row["slots"])],
            run_at=row["run_at"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            last_run_at=row["last_run_at"],
            last_status=row["last_status"],
        )

    def all(self) -> list[Schedule]:
        rows = self.conn.execute("SELECT * FROM schedules ORDER BY created_at").fetchall()
        return [self._row(r) for r in rows]

    def get(self, schedule_id: int) -> Schedule | None:
        row = self.conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        return self._row(row) if row else None

    def upcoming(self, limit: int = 3) -> list[Schedule]:
        scheduled = [(s.next_run(), s) for s in self.all()]
        live = [(when, s) for when, s in scheduled if when is not None]
        return [s for _, s in sorted(live, key=lambda pair: pair[0])][:limit]

    def due(self, now: float | None = None) -> list[Schedule]:
        return [s for s in self.all() if s.is_due(now)]

    def add(
        self,
        task: str,
        *,
        slots: list[tuple[int, int]] | None = None,
        run_at: float | None = None,
    ) -> Schedule:
        if not slots and run_at is None:
            raise ValueError("a schedule needs either grid slots or a run_at timestamp")
        cursor = self.conn.execute(
            "INSERT INTO schedules (task, slots, run_at, created_at) VALUES (?, ?, ?, ?)",
            (task, json.dumps(sorted(slots or [])), run_at, time.time()),
        )
        self.conn.commit()
        created = self.get(int(cursor.lastrowid))
        assert created is not None
        return created

    def set_slots(self, schedule_id: int, slots: list[tuple[int, int]]) -> None:
        self.conn.execute(
            "UPDATE schedules SET slots = ? WHERE id = ?",
            (json.dumps(sorted(slots)), schedule_id),
        )
        self.conn.commit()

    def set_enabled(self, schedule_id: int, enabled: bool) -> None:
        self.conn.execute(
            "UPDATE schedules SET enabled = ? WHERE id = ?", (int(enabled), schedule_id)
        )
        self.conn.commit()

    def mark_run(self, schedule_id: int, status: str, when: float | None = None) -> None:
        self.conn.execute(
            "UPDATE schedules SET last_run_at = ?, last_status = ? WHERE id = ?",
            (time.time() if when is None else when, status, schedule_id),
        )
        self.conn.commit()

    def remove(self, schedule_id: int) -> None:
        self.conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
