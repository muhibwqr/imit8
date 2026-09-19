"""The agent loop: screenshot -> model -> action, plus model-free replay."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .computer import Computer
from .config import Config
from .flows import Flow, FlowStore
from .llm import LLMError, OpenRouterClient

SYSTEM_PROMPT = """You are jev, a computer-use agent driving a real desktop.

You get a screenshot before every decision. Call exactly one tool per turn, using
coordinates in the screenshot's pixel space (top-left is 0,0).

Rules:
- Prefer keyboard shortcuts and app launchers over hunting for tiny UI targets.
- After an action that changes the screen, look again before assuming it worked.
- Never guess at credentials, never accept destructive prompts you were not asked
  to accept (deleting files, sending money, sending messages to strangers).
- Call `done` as soon as the task is achieved, with a one-line summary of what
  you see on screen that proves it. Call `done` with success=false if you are
  stuck or the task would require something you are not allowed to do.
"""

MAX_IMAGES = 3


@dataclass
class Event:
    kind: str  # step | action | done | error | info
    text: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Result:
    status: str  # success | failed | error | stopped
    summary: str
    steps: int
    duration: float
    trace: list[dict[str, Any]]
    mode: str = "agent"


EventSink = Callable[[Event], None]


def _noop(_: Event) -> None:
    return None


class Agent:
    def __init__(
        self,
        config: Config | None = None,
        computer: Computer | None = None,
        client: OpenRouterClient | None = None,
        store: FlowStore | None = None,
    ) -> None:
        self.config = config or Config.load()
        self.computer = computer or Computer(
            target_width=self.config.screenshot_width, action_delay=self.config.action_delay
        )
        self.client = client or OpenRouterClient(self.config)
        self.store = store or FlowStore()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    # --- public API --------------------------------------------------------
    def run(self, task: str, on_event: EventSink = _noop) -> Result:
        """Run a task with the model in the loop, then record it as a flow."""
        self._stop = False
        started = time.time()
        result = self._agent_loop(task, on_event)
        result.duration = time.time() - started
        flow = self.store.record_run(
            task,
            status=result.status,
            steps=result.steps,
            duration=result.duration,
            mode=result.mode,
            summary=result.summary,
            trace=result.trace,
        )
        self._emit_flow_badge(flow, on_event)
        return result

    def replay(self, flow: Flow, on_event: EventSink = _noop) -> Result:
        """Re-run a flow's recorded actions without calling the model."""
        self._stop = False
        if not flow.replayable:
            raise ValueError(f"Flow {flow.id!r} has no recorded trace to replay")
        started = time.time()
        steps = 0
        for action in flow.trace or []:
            if self._stop:
                break
            name, args = action["name"], action.get("arguments", {})
            if name == "done":
                continue
            on_event(Event("action", _describe(name, args), {"name": name, "arguments": args}))
            self._apply(name, args)
            steps += 1
        status = "stopped" if self._stop else "success"
        duration = time.time() - started
        summary = f"Replayed {steps} recorded steps"
        updated = self.store.record_run(
            flow.task,
            status=status,
            steps=steps,
            duration=duration,
            mode="replay",
            summary=summary,
            trace=flow.trace,
        )
        on_event(Event("done", summary, {"status": status}))
        self._emit_flow_badge(updated, on_event)
        return Result(status, summary, steps, duration, flow.trace or [], mode="replay")

    # --- internals ---------------------------------------------------------
    def _emit_flow_badge(self, flow: Flow, on_event: EventSink) -> None:
        on_event(
            Event(
                "flow",
                f"You {flow.badge()} this flow! Run it again?",
                {
                    "flow_id": flow.id,
                    "run_count": flow.run_count,
                    "replayable": flow.replayable,
                    "task": flow.task,
                },
            )
        )

    def _agent_loop(self, task: str, on_event: EventSink) -> Result:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {task}"},
        ]
        trace: list[dict[str, Any]] = []
        steps = 0
        while steps < self.config.max_steps:
            if self._stop:
                return Result("stopped", "Stopped by user", steps, 0.0, trace)
            shot = self.computer.screenshot()
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Screenshot ({shot.width}x{shot.height}). Next action?",
                        },
                        {"type": "image_url", "image_url": {"url": shot.data_url()}},
                    ],
                }
            )
            _prune_images(messages)
            steps += 1
            on_event(Event("step", f"Step {steps}", {"step": steps}))
            try:
                message = self.client.complete(messages)
            except LLMError as exc:
                on_event(Event("error", str(exc)))
                return Result("error", str(exc), steps, 0.0, trace)

            calls = message.get("tool_calls") or []
            if not calls:
                text = (message.get("content") or "").strip()
                messages.append({"role": "assistant", "content": text})
                messages.append(
                    {"role": "user", "content": "Respond with a tool call, not prose."}
                )
                continue

            call = calls[0]
            name = call["function"]["name"]
            args = _parse_args(call["function"].get("arguments"))
            messages.append({"role": "assistant", "tool_calls": [call], "content": None})

            if name == "done":
                summary = args.get("summary", "")
                status = "success" if args.get("success") else "failed"
                on_event(Event("done", summary or status, {"status": status}))
                return Result(status, summary, steps, 0.0, trace)

            on_event(Event("action", _describe(name, args), {"name": name, "arguments": args}))
            try:
                self._apply(name, args)
                observation = "ok"
            except Exception as exc:  # noqa: BLE001 - surfaced back to the model
                observation = f"action failed: {exc}"
                on_event(Event("error", observation))
            trace.append({"name": name, "arguments": args})
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id", ""), "content": observation}
            )

        return Result("failed", f"Hit the {self.config.max_steps} step limit", steps, 0.0, trace)

    def _apply(self, name: str, args: dict[str, Any]) -> None:
        c = self.computer
        if name == "click":
            c.click(args["x"], args["y"], args.get("button", "left"), int(args.get("clicks", 1)))
        elif name == "type_text":
            c.type_text(args["text"])
        elif name == "key":
            c.key(args["keys"])
        elif name == "scroll":
            c.scroll(args["x"], args["y"], int(args["amount"]))
        elif name == "drag":
            c.drag(args["x1"], args["y1"], args["x2"], args["y2"])
        elif name == "wait":
            time.sleep(min(float(args.get("seconds", 1)), 10))
        else:
            raise ValueError(f"unknown action {name}")


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _describe(name: str, args: dict[str, Any]) -> str:
    if name == "click":
        return f"click ({args.get('x')}, {args.get('y')})"
    if name == "type_text":
        text = str(args.get("text", ""))
        return f"type {text[:40]!r}"
    if name == "key":
        return f"key {args.get('keys')}"
    if name == "scroll":
        return f"scroll {args.get('amount')}"
    if name == "drag":
        return f"drag to ({args.get('x2')}, {args.get('y2')})"
    if name == "wait":
        return f"wait {args.get('seconds')}s"
    return name


def _prune_images(messages: list[dict[str, Any]], keep: int = MAX_IMAGES) -> None:
    """Drop old screenshots so the context stays small (and fast)."""
    image_indices = [
        i
        for i, m in enumerate(messages)
        if isinstance(m.get("content"), list)
        and any(part.get("type") == "image_url" for part in m["content"])
    ]
    for i in image_indices[:-keep] if len(image_indices) > keep else []:
        parts: Iterable[dict[str, Any]] = messages[i]["content"]
        text = next((p.get("text", "") for p in parts if p.get("type") == "text"), "")
        messages[i] = {"role": "user", "content": f"{text} [older screenshot dropped]"}
