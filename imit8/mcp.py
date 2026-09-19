"""MCP server: `imit8 mcp` over stdio, so an agent can drive imit8 in-session.

Implements the three calls a client actually needs — initialize, tools/list,
tools/call — as line-delimited JSON-RPC on stdin/stdout, with no dependencies
beyond the standard library.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

from . import api

PROTOCOL_VERSION = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "imit8_run",
        "description": (
            "Run a natural-language task on this computer (mouse + keyboard). Replays the "
            "recorded flow instantly if the task has been done before."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"task": {"type": "string"}},
            "required": ["task"],
        },
    },
    {
        "name": "imit8_replay",
        "description": "Replay a recorded flow by id — no model calls, sub-second.",
        "inputSchema": {
            "type": "object",
            "properties": {"flow_id": {"type": "integer"}},
            "required": ["flow_id"],
        },
    },
    {
        "name": "imit8_flows",
        "description": "List recorded flows with run counts, badges and whether they replay.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "imit8_forget",
        "description": "Delete a recorded flow by id.",
        "inputSchema": {
            "type": "object",
            "properties": {"flow_id": {"type": "integer"}},
            "required": ["flow_id"],
        },
    },
    {
        "name": "imit8_schedule",
        "description": (
            "Schedule a task: weekly with days (mon..sun) + times (HH:MM), or one-off with "
            "an ISO timestamp in `once` (YYYY-MM-DDTHH:MM, local time)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "days": {"type": "array", "items": {"type": "string"}},
                "times": {"type": "array", "items": {"type": "string"}},
                "once": {"type": "string"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "imit8_schedules",
        "description": "List scheduled tasks with their next run time and last status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "imit8_unschedule",
        "description": "Delete a schedule by id.",
        "inputSchema": {
            "type": "object",
            "properties": {"schedule_id": {"type": "integer"}},
            "required": ["schedule_id"],
        },
    },
    {
        "name": "imit8_set_schedule_enabled",
        "description": "Pause or resume a schedule.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "schedule_id": {"type": "integer"},
                "enabled": {"type": "boolean"},
            },
            "required": ["schedule_id", "enabled"],
        },
    },
]

HANDLERS: dict[str, Callable[..., Any]] = {
    "imit8_run": lambda task: api.run(task),
    "imit8_replay": lambda flow_id: api.replay(flow_id),
    "imit8_flows": api.flows,
    "imit8_forget": lambda flow_id: api.forget(flow_id),
    "imit8_schedule": lambda task, days=None, times=None, once=None: api.schedule(
        task, days=days, times=times, once=once
    ),
    "imit8_schedules": api.schedules,
    "imit8_unschedule": lambda schedule_id: api.unschedule(schedule_id),
    "imit8_set_schedule_enabled": lambda schedule_id, enabled: api.set_enabled(
        schedule_id, enabled
    ),
}


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    handler = HANDLERS.get(name)
    if handler is None:
        return _content(f"unknown tool {name}", error=True)
    try:
        payload = handler(**arguments)
    except Exception as exc:  # noqa: BLE001 - reported to the calling agent
        return _content(f"{type(exc).__name__}: {exc}", error=True)
    return _content(json.dumps(payload, indent=2))


def _content(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "imit8", "version": "0.1.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params") or {}
        result = call_tool(params.get("name", ""), params.get("arguments") or {})
    elif method == "ping":
        result = {}
    elif request_id is None:  # a notification we do not care about
        return None
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"unknown method {method}"},
        }
    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(request)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0
