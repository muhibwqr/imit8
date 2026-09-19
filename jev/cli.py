"""jev command line: `jev` opens the spotlight, `jev run "task"` runs headless."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .agent import Agent, Event
from .config import Config
from .flows import FlowStore
from .schedule import DAYS, ScheduleStore


def _print_event(event: Event) -> None:
    prefix = {"action": "→", "step": "·", "error": "!", "done": "✓", "flow": "★"}.get(
        event.kind, " "
    )
    print(f" {prefix} {event.text}", flush=True)


def cmd_run(args: argparse.Namespace) -> int:
    agent = Agent()
    result = agent.run(" ".join(args.task), _print_event)
    print(f"\n{result.status}: {result.summary} ({result.steps} steps, {result.duration:.1f}s)")
    return 0 if result.status == "success" else 1


def cmd_flows(_: argparse.Namespace) -> int:
    store = FlowStore()
    flows = store.all_flows()
    if not flows:
        print("no flows yet — run something first")
        return 0
    for flow in flows:
        when = (
            datetime.fromtimestamp(flow.last_run_at).strftime("%Y-%m-%d %H:%M")
            if flow.last_run_at
            else "never"
        )
        bolt = "⚡" if flow.replayable else " "
        print(f"{bolt} [{flow.id:>3}] {flow.task}")
        print(f"      {flow.badge()} · last {when} · avg {flow.avg_duration:.1f}s")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    agent = Agent()
    flow = agent.store.get(args.flow_id)
    if flow is None:
        print(f"no flow with id {args.flow_id}", file=sys.stderr)
        return 1
    if not flow.replayable:
        print("flow has no recorded trace; running with the model instead")
        result = agent.run(flow.task, _print_event)
    else:
        result = agent.replay(flow, _print_event)
    print(f"\n{result.status}: {result.summary}")
    return 0 if result.status == "success" else 1


def cmd_forget(args: argparse.Namespace) -> int:
    FlowStore().forget(args.flow_id)
    print(f"forgot flow {args.flow_id}")
    return 0


def _parse_slots(days: list[str], times: list[str]) -> list[tuple[int, int]]:
    weekdays = []
    for day in days:
        key = day.lower()[:3]
        if key not in DAYS:
            raise ValueError(f"unknown day {day!r} (use {', '.join(DAYS)})")
        weekdays.append(DAYS.index(key))
    slots = []
    for stamp in times:
        hour, _, minute = stamp.partition(":")
        minutes = int(hour) * 60 + int(minute or 0)
        slots.extend((weekday, minutes) for weekday in weekdays)
    return slots


def cmd_schedule(args: argparse.Namespace) -> int:
    try:
        slots = _parse_slots(args.days, args.at)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    schedule = ScheduleStore().add(" ".join(args.task), slots=slots)
    when = schedule.next_run()
    stamp = datetime.fromtimestamp(when).strftime("%a %d %b %H:%M") if when else "never"
    print(f"[{schedule.id}] {schedule.task}\n      {schedule.describe()} · next {stamp}")
    return 0


def cmd_schedules(_: argparse.Namespace) -> int:
    schedules = ScheduleStore().all()
    if not schedules:
        print("nothing scheduled — `jev calendar` opens the grid picker")
        return 0
    for schedule in schedules:
        when = schedule.next_run()
        stamp = datetime.fromtimestamp(when).strftime("%a %d %b %H:%M") if when else "never"
        mark = " " if schedule.enabled else "·"
        print(f"{mark} [{schedule.id:>3}] {schedule.task}")
        print(f"      {schedule.describe()} · next {stamp} · last {schedule.last_status or '—'}")
    return 0


def cmd_unschedule(args: argparse.Namespace) -> int:
    ScheduleStore().remove(args.schedule_id)
    print(f"removed schedule {args.schedule_id}")
    return 0


def cmd_calendar(_: argparse.Namespace) -> int:
    from PySide6.QtWidgets import QApplication

    from .calendar_view import CalendarWindow

    app = QApplication(sys.argv)
    window = CalendarWindow()
    window.show()
    return app.exec()


def cmd_config(_: argparse.Namespace) -> int:
    config = Config.load()
    config.save()
    print(f"model: {config.model}\nbase_url: {config.base_url}")
    print("api key: " + ("set" if config.api_key else "missing (set OPENROUTER_API_KEY)"))
    return 0


def cmd_ui(_: argparse.Namespace) -> int:
    from .ui import main as ui_main

    return ui_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jev", description="a local computer-use agent")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run a task")
    run.add_argument("task", nargs="+")
    run.set_defaults(func=cmd_run)

    flows = sub.add_parser("flows", help="list recorded flows and their run counts")
    flows.set_defaults(func=cmd_flows)

    replay = sub.add_parser("replay", help="replay a recorded flow by id")
    replay.add_argument("flow_id", type=int)
    replay.set_defaults(func=cmd_replay)

    forget = sub.add_parser("forget", help="delete a flow")
    forget.add_argument("flow_id", type=int)
    forget.set_defaults(func=cmd_forget)

    config = sub.add_parser("config", help="show/write config")
    config.set_defaults(func=cmd_config)

    schedule = sub.add_parser("schedule", help="run a task on a weekly slot")
    schedule.add_argument("task", nargs="+")
    schedule.add_argument(
        "--days", nargs="+", required=True, metavar="DAY", help=f"one or more of {', '.join(DAYS)}"
    )
    schedule.add_argument(
        "--at", nargs="+", required=True, metavar="HH:MM", help="one or more times of day"
    )
    schedule.set_defaults(func=cmd_schedule)

    schedules = sub.add_parser("schedules", help="list scheduled tasks")
    schedules.set_defaults(func=cmd_schedules)

    unschedule = sub.add_parser("unschedule", help="delete a schedule by id")
    unschedule.add_argument("schedule_id", type=int)
    unschedule.set_defaults(func=cmd_unschedule)

    calendar = sub.add_parser("calendar", help="open the drag-to-select week grid")
    calendar.set_defaults(func=cmd_calendar)

    ui = sub.add_parser("ui", help="open the spotlight window (default)")
    ui.set_defaults(func=cmd_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        return cmd_ui(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
