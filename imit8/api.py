"""Programmatic surface for other agents.

Everything the spotlight and the CLI can do, as plain functions returning JSON
friendly dicts: run a task, replay a recorded flow, list flows, and create or
drop schedules. `imit8.mcp` wraps these as MCP tools so an agent session can
drive imit8 without leaving its own loop.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from .agent import Agent
from .flows import Flow, FlowStore
from .schedule import DAYS, Schedule, ScheduleStore


def parse_slots(days: Iterable[str], times: Iterable[str]) -> list[tuple[int, int]]:
    """["mon","fri"], ["09:00"] -> [(0, 540), (4, 540)]."""
    weekdays = []
    for day in days:
        key = str(day).lower()[:3]
        if key not in DAYS:
            raise ValueError(f"unknown day {day!r} (use {', '.join(DAYS)})")
        weekdays.append(DAYS.index(key))
    slots = []
    for stamp in times:
        hour, _, minute = str(stamp).partition(":")
        minutes = int(hour) * 60 + int(minute or 0)
        slots.extend((weekday, minutes) for weekday in weekdays)
    if not slots:
        raise ValueError("pass at least one day and one time")
    return slots


def _stamp(value: float | None) -> str | None:
    return datetime.fromtimestamp(value).isoformat(timespec="minutes") if value else None


def flow_dict(flow: Flow) -> dict[str, Any]:
    return {
        "id": flow.id,
        "task": flow.task,
        "run_count": flow.run_count,
        "success_count": flow.success_count,
        "badge": flow.badge(),
        "replayable": flow.replayable,
        "avg_duration": round(flow.avg_duration, 3),
        "last_run_at": _stamp(flow.last_run_at),
    }


def schedule_dict(schedule: Schedule) -> dict[str, Any]:
    return {
        "id": schedule.id,
        "task": schedule.task,
        "describes": schedule.describe(),
        "slots": [list(slot) for slot in schedule.slots],
        "once": schedule.once,
        "enabled": schedule.enabled,
        "next_run": _stamp(schedule.next_run()),
        "last_run_at": _stamp(schedule.last_run_at),
        "last_status": schedule.last_status,
    }


def run(task: str, *, agent: Agent | None = None) -> dict[str, Any]:
    """Drive the desktop until the task is done. Replays a recorded flow if there is one."""
    agent = agent or Agent()
    flow = agent.store.find(task)
    if flow and flow.replayable:
        result = agent.replay(flow)
    else:
        result = agent.run(task)
    return {
        "mode": result.mode,
        "status": result.status,
        "summary": result.summary,
        "steps": result.steps,
        "duration": round(result.duration, 3),
    }


def replay(flow_id: int, *, agent: Agent | None = None) -> dict[str, Any]:
    """Re-fire a flow's recorded actions with no model calls at all."""
    agent = agent or Agent()
    flow = agent.store.get(flow_id)
    if flow is None:
        raise ValueError(f"no flow {flow_id}")
    result = agent.replay(flow)
    return {
        "mode": result.mode,
        "status": result.status,
        "summary": result.summary,
        "steps": result.steps,
        "duration": round(result.duration, 3),
    }


def flows(*, store: FlowStore | None = None) -> list[dict[str, Any]]:
    return [flow_dict(f) for f in (store or FlowStore()).all_flows()]


def forget(flow_id: int, *, store: FlowStore | None = None) -> dict[str, Any]:
    (store or FlowStore()).forget(flow_id)
    return {"forgot": flow_id}


def schedule(
    task: str,
    *,
    days: Iterable[str] | None = None,
    times: Iterable[str] | None = None,
    once: str | None = None,
    store: ScheduleStore | None = None,
) -> dict[str, Any]:
    """Weekly slots (`days` + `times`) or a one-off ISO timestamp (`once`)."""
    if once:
        created = (store or ScheduleStore()).add(
            task, run_at=datetime.fromisoformat(once).timestamp()
        )
    else:
        if not (days and times):
            raise ValueError("pass days + times for a weekly schedule, or once for a one-off")
        created = (store or ScheduleStore()).add(task, slots=parse_slots(days, times))
    return schedule_dict(created)


def schedules(*, store: ScheduleStore | None = None) -> list[dict[str, Any]]:
    return [schedule_dict(s) for s in (store or ScheduleStore()).all()]


def unschedule(schedule_id: int, *, store: ScheduleStore | None = None) -> dict[str, Any]:
    (store or ScheduleStore()).remove(schedule_id)
    return {"removed": schedule_id}


def set_enabled(
    schedule_id: int, enabled: bool, *, store: ScheduleStore | None = None
) -> dict[str, Any]:
    store = store or ScheduleStore()
    store.set_enabled(schedule_id, enabled)
    current = store.get(schedule_id)
    if current is None:
        raise ValueError(f"no schedule {schedule_id}")
    return schedule_dict(current)
