"""Flow memory: every task you run is fingerprinted, counted and replayable.

A *flow* is a normalized task ("open spotify and play lo-fi"). Each execution is
a *run*, and a successful run stores its action trace so the same flow can be
replayed later without asking the model anything.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS flows (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT UNIQUE NOT NULL,
    task TEXT NOT NULL,
    run_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    last_run_at REAL,
    avg_duration REAL NOT NULL DEFAULT 0,
    trace TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    flow_id INTEGER NOT NULL REFERENCES flows(id),
    task TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'agent',
    status TEXT NOT NULL,
    steps INTEGER NOT NULL DEFAULT 0,
    duration REAL NOT NULL DEFAULT 0,
    started_at REAL NOT NULL,
    summary TEXT
);
CREATE INDEX IF NOT EXISTS runs_flow_idx ON runs(flow_id);
"""

_STOPWORDS = {"the", "a", "an", "to", "for", "my", "please", "then", "and"}
_FUZZY_THRESHOLD = 0.9


@dataclass
class Flow:
    id: int
    fingerprint: str
    task: str
    run_count: int
    success_count: int
    last_run_at: float | None
    avg_duration: float
    trace: list[dict] | None

    @property
    def replayable(self) -> bool:
        return bool(self.trace) and self.success_count > 0

    def badge(self) -> str:
        if self.run_count <= 1:
            return "used once"
        return f"used {self.run_count} times"


def normalize(task: str) -> str:
    text = re.sub(r"[^\w\s]", " ", task.lower())
    words = [w for w in text.split() if w not in _STOPWORDS]
    return " ".join(words)


def fingerprint(task: str) -> str:
    return hashlib.sha1(normalize(task).encode()).hexdigest()[:16]


class FlowStore:
    def __init__(self, path: Path | str = DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- reads -------------------------------------------------------------
    def _row_to_flow(self, row: sqlite3.Row) -> Flow:
        return Flow(
            id=row["id"],
            fingerprint=row["fingerprint"],
            task=row["task"],
            run_count=row["run_count"],
            success_count=row["success_count"],
            last_run_at=row["last_run_at"],
            avg_duration=row["avg_duration"],
            trace=json.loads(row["trace"]) if row["trace"] else None,
        )

    def all_flows(self) -> list[Flow]:
        rows = self.conn.execute(
            "SELECT * FROM flows ORDER BY run_count DESC, last_run_at DESC"
        ).fetchall()
        return [self._row_to_flow(r) for r in rows]

    def get(self, flow_id: int) -> Flow | None:
        row = self.conn.execute("SELECT * FROM flows WHERE id = ?", (flow_id,)).fetchone()
        return self._row_to_flow(row) if row else None

    def find(self, task: str) -> Flow | None:
        """Exact fingerprint match, falling back to a close fuzzy match."""
        row = self.conn.execute(
            "SELECT * FROM flows WHERE fingerprint = ?", (fingerprint(task),)
        ).fetchone()
        if row:
            return self._row_to_flow(row)
        target = normalize(task)
        best, best_score = None, 0.0
        for flow in self.all_flows():
            score = difflib.SequenceMatcher(None, target, normalize(flow.task)).ratio()
            if score > best_score:
                best, best_score = flow, score
        return best if best_score >= _FUZZY_THRESHOLD else None

    def suggestions(self, limit: int = 6, min_runs: int = 1) -> list[Flow]:
        return [f for f in self.all_flows() if f.run_count >= min_runs][:limit]

    def history(self, flow_id: int, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM runs WHERE flow_id = ? ORDER BY started_at DESC LIMIT ?",
            (flow_id, limit),
        ).fetchall()

    # --- writes ------------------------------------------------------------
    def ensure_flow(self, task: str) -> Flow:
        existing = self.find(task)
        if existing:
            return existing
        self.conn.execute(
            "INSERT INTO flows (fingerprint, task) VALUES (?, ?)", (fingerprint(task), task)
        )
        self.conn.commit()
        found = self.find(task)
        assert found is not None
        return found

    def record_run(
        self,
        task: str,
        *,
        status: str,
        steps: int,
        duration: float,
        mode: str = "agent",
        summary: str = "",
        trace: list[dict] | None = None,
    ) -> Flow:
        flow = self.ensure_flow(task)
        started = time.time() - duration
        self.conn.execute(
            "INSERT INTO runs (flow_id, task, mode, status, steps, duration, started_at, summary)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (flow.id, task, mode, status, steps, duration, started, summary),
        )
        success = status == "success"
        new_count = flow.run_count + 1
        avg = (flow.avg_duration * flow.run_count + duration) / new_count
        trace_json = json.dumps(trace) if (success and trace) else None
        self.conn.execute(
            "UPDATE flows SET run_count = ?, success_count = ?, last_run_at = ?,"
            " avg_duration = ?, trace = COALESCE(?, trace) WHERE id = ?",
            (
                new_count,
                flow.success_count + int(success),
                time.time(),
                avg,
                trace_json,
                flow.id,
            ),
        )
        self.conn.commit()
        updated = self.get(flow.id)
        assert updated is not None
        return updated

    def forget(self, flow_id: int) -> None:
        self.conn.execute("DELETE FROM runs WHERE flow_id = ?", (flow_id,))
        self.conn.execute("DELETE FROM flows WHERE id = ?", (flow_id,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
