"""Background poller that fires scheduled tasks when their slot comes around."""

from __future__ import annotations

import threading
from collections.abc import Callable

from .schedule import Schedule, ScheduleStore

POLL_SECONDS = 20.0


class Scheduler:
    """Calls `on_due(schedule)` once per slot that has just passed.

    The callback decides how to run the task (replay a recorded trace, or hand it
    to the model) and must call `store.mark_run` itself so a crashed run is not
    silently swallowed.
    """

    def __init__(
        self,
        on_due: Callable[[Schedule], None],
        store: ScheduleStore | None = None,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self.on_due = on_due
        self.store = store or ScheduleStore()
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> list[Schedule]:
        fired = []
        for schedule in self.store.due():
            fired.append(schedule)
            self.on_due(schedule)
        return fired

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="jev-scheduler", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - a bad schedule must not kill the loop
                continue

    def stop(self) -> None:
        self._stop.set()
