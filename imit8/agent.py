"""The agent loop: screenshot -> model -> action, plus model-free replay.

There is also a fast path (`run_fast`): instead of shipping a screenshot to a
vision model every step, it dumps the frontmost app's UI elements locally
(accessibility API, ~20ms) and asks a decisions model which one to act on —
text only, ~100-300ms per step. A vision check still confirms the end state.
"""

from __future__ import annotations

import json
import os
import platform
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .computer import Computer, ScopeRegion
from .config import Config
from .elements import TYPEABLE
from .flows import Flow, FlowStore
from .llm import TOOLS, LLMError, OpenRouterClient

SYSTEM_PROMPT = """You are imit8, a hyper-efficient computer-use agent driving a real
desktop. You operate like an elite human at the keyboard: read the screen once,
decide instantly, act decisively. Hesitation and redundant checking are failure.

You get a screenshot before every decision. Call exactly one tool per turn, using
coordinates in the screenshot's pixel space (top-left is 0,0).

Rules:
- Act now. If the screen already shows what you need, don't wait, and don't
  re-check an action that obviously worked.
- To open or focus an app, call `launch` — never Spotlight, Launchpad or the Dock.
  For anything else, prefer keyboard shortcuts over hunting for tiny UI targets.
- When an 'Interactive elements' list follows a screenshot, press elements with
  `click_element` instead of aiming `click` at pixels — it never misses.
- On fast-moving screens (games, animations, live UIs) one action per turn is too
  slow — plan ahead and fire a whole sequence with `batch`, including `wait`
  sub-actions to control rhythm.
- After an action that changes the screen, look again before assuming it worked.
- Never guess at credentials, never accept destructive prompts you were not asked
  to accept (deleting files, sending money, sending messages to strangers).
- Call `done` as soon as the task is achieved, with a one-line summary of what
  you see on screen that proves it. Call `done` with success=false if you are
  stuck or the task would require something you are not allowed to do.
"""

VERIFY_PROMPT = (
    "Before 'done' is accepted: this is a fresh screenshot taken right now. Does it "
    "actually show the task finished? If it does, call done(success=true) again and "
    "quote the on-screen evidence in the summary. If it does not — the window closed, "
    "the page never loaded, nothing changed — keep working with another action, or call "
    "done(success=false)."
)

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
        self._replaying = False  # batches replay fully — no abort-on-change
        self._step_elements: list = []  # the AX dump that came with the latest screenshot

    def stop(self) -> None:
        self._stop = True

    # --- public API --------------------------------------------------------
    def run(self, task: str, on_event: EventSink = _noop, context: str = "") -> Result:
        """Run a task with the model in the loop, then record it as a flow."""
        self._stop = False
        started = time.time()
        pre = self._pre_actions(task, on_event)
        result = self._agent_loop(task, on_event, context)
        result.trace = pre + result.trace
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
        previous_scope = self.computer.scope
        self._replaying = True
        try:
            for action in flow.trace or []:
                if self._stop:
                    break
                name, args = action["name"], action.get("arguments", {})
                if name == "done":
                    continue
                if name == "_scope":
                    self._apply(name, args)
                    continue
                on_event(Event("action", _describe(name, args), {"name": name, "arguments": args}))
                self._apply(name, args)
                steps += 1
        finally:
            self._replaying = False
            self.computer.scope = previous_scope
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

    FAST_KEYS = [
        "enter", "tab", "shift+tab", "escape", "space",
        "up", "down", "left", "right",
        "cmd+l", "cmd+t", "cmd+r", "cmd+a", "cmd+v", "page down",
    ]

    def run_fast(
        self,
        task: str,
        on_event: EventSink = _noop,
        elements_fn: Callable[[], tuple[str, list]] | None = None,
        decisions=None,
    ) -> Result:
        """Fast loop: local element dump + typed decisions, no images per step.

        Enumerating the frontmost app's UI elements costs ~20ms and one
        decisions call ~100-300ms — an order of magnitude under a vision
        round-trip. jev only sees labels, so a vision model still confirms
        the end state when verify_done is on, and if the fast loop stalls we
        escalate to the full vision loop rather than give up.
        """
        from .decisions import DecisionsClient, DecisionsError  # noqa: PLC0415
        from .elements import frontmost_elements  # noqa: PLC0415

        self._stop = False
        started = time.time()
        decisions = decisions or DecisionsClient(self.config)
        elements_fn = elements_fn or frontmost_elements
        trace: list[dict[str, Any]] = self._pre_actions(task, on_event)
        history: list[str] = []  # jev sees no screen — recent actions are its only feedback
        steps = misses = 0
        while steps < self.config.max_steps and misses < 6:
            if self._stop:
                duration = time.time() - started
                return Result("stopped", "Stopped by user", steps, duration, trace, mode="fast")
            dump_start = time.monotonic()
            app, elements = elements_fn()
            dump_ms = (time.monotonic() - dump_start) * 1000
            questions = self._fast_questions(elements)
            decide_start = time.monotonic()
            try:
                answers = decisions.decide(
                    self._fast_state(task, app, elements, history), questions
                )
            except DecisionsError as exc:
                on_event(Event("error", str(exc)))
                duration = time.time() - started
                return Result("error", str(exc), steps, duration, trace, mode="fast")
            decide_ms = (time.monotonic() - decide_start) * 1000
            steps += 1
            on_event(Event("step", f"Step {steps}", {"step": steps}))
            on_event(
                Event(
                    "perf",
                    f"{decide_ms:.0f}ms",
                    {"step": steps, "dump_ms": dump_ms, "decide_ms": decide_ms},
                )
            )

            if float(answers.get("done", {}).get("noul") or 0) >= 0.7:
                if self.config.verify_done and not self._verify_done(task):
                    misses += 1
                    continue
                duration = time.time() - started
                flow = self.store.record_run(
                    task, status="success", steps=steps, duration=duration,
                    mode="fast", summary="task complete", trace=trace,
                )
                on_event(Event("done", "task complete", {"status": "success"}))
                self._emit_flow_badge(flow, on_event)
                return Result("success", "task complete", steps, duration, trace, mode="fast")

            acted = self._fast_act(answers, elements, task, trace, on_event)
            misses = 0 if acted else misses + 1
            history.append(acted or "nothing useful to do")
            history = history[-10:]

        on_event(Event("info", "fast loop stalled — switching to the vision loop"))
        return self.run(task, on_event)

    def _fast_state(self, task: str, app: str, elements: list, history: list) -> str:
        lines = [f"Task: {task}", f"Frontmost app: {app or 'unknown'}", "UI elements:"]
        lines += [e.line() for e in elements] or ["(none detected)"]
        if history:
            lines += ["Actions already taken:", *history]
        return "\n".join(lines)

    def _fast_questions(self, elements: list) -> dict:
        # score criteria are an array of bare labels; the descriptions live in
        # state, so the labels stay as plain element indices.
        pick_criteria = [str(e.index) for e in elements] + ["none"]
        return {
            "done": {
                "type": "noul",
                "instructions": "Is the task already complete given this UI state?",
                "criteria": {
                    "true": "the goal is visibly achieved",
                    "false": "more actions are needed",
                },
            },
            "pick": {
                "type": "score",
                "instructions": (
                    "Which element index should be clicked to make progress "
                    "(descriptions are in state), or 'none'?"
                ),
                "criteria": pick_criteria,
            },
            "key": {
                "type": "choice",
                "instructions": "If a keystroke would progress more than a click, which?",
                "criteria": {
                    **{k: f"press {k}" for k in self.FAST_KEYS},
                    "none": "no keystroke helps right now",
                },
            },
        }

    def _fast_act(
        self,
        answers: dict,
        elements: list,
        task: str,
        trace: list[dict[str, Any]],
        on_event: EventSink,
    ) -> str | None:
        """Execute jev's pick. Returns a description, or None if nothing fired."""
        probabilities = answers.get("pick", {}).get("probabilities") or {}
        pick = max(probabilities, key=probabilities.get) if probabilities else "none"
        if pick != "none" and pick.isdigit() and int(pick) < len(elements):
            el = elements[int(pick)]
            desc = f"clicked {el.label or el.role} @({el.x},{el.y})"
            on_event(Event("action", desc))
            if el.press():
                time.sleep(self.computer.action_delay)
            else:
                self.computer.click_at(el.x, el.y)
            trace.append({"name": "click_at", "arguments": {"x": el.x, "y": el.y}})
            quoted = re.search(r'"([^"]+)"|\'([^\']+)\'', task)
            if quoted and el.role in TYPEABLE:
                text = next(g for g in quoted.groups() if g)
                on_event(Event("action", f"type {text!r}"))
                self.computer.type_text(text)
                trace.append({"name": "type_text", "arguments": {"text": text}})
                desc += f" and typed {text!r}"
            return desc
        key = answers.get("key", {}).get("choice", "none")
        if key and key != "none":
            on_event(Event("action", f"key {key}"))
            self.computer.key(key)
            trace.append({"name": "key", "arguments": {"keys": key}})
            return f"pressed {key}"
        return None

    def _verify_done(self, task: str) -> bool:
        """One vision call confirming jev's 'done' against the real screen."""
        shot = self.computer.screenshot()
        try:
            message = self.client.complete(
                [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"Task: {task}\nDoes this screenshot show it "
                                    "complete? Answer via done."
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": shot.data_url()}},
                        ],
                    }
                ],
                tools=[t for t in TOOLS if t["function"]["name"] == "done"],
            )
        except LLMError:
            return True  # can't verify — accept jev's call
        for call in message.get("tool_calls") or []:
            if call["function"]["name"] == "done":
                return bool(_parse_args(call["function"].get("arguments")).get("success"))
        return True

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

    def _pre_actions(self, task: str, on_event: EventSink) -> list[dict[str, Any]]:
        """Deterministic head start — browser-use's `directly_open_url` trick.

        If the task names an app to open or contains a URL, just open it
        instead of spending a model call telling us to. Failure is silent:
        the model falls back to doing it by hand.
        """
        targets: list[str] = []
        m = re.match(
            r"\s*(?:open|launch|start)\s+(?:the\s+)?(.+?)(?:\s+app)?\s*(?:\.|,| and | then |$)",
            task,
            re.IGNORECASE,
        )
        if m and not re.search(r"https?://|www\.", m.group(1)):
            targets.append(m.group(1))
        url = re.search(r"(?:https?://|www\.)\S+", task)
        if url:
            targets.append(url.group(0).rstrip(".,)"))
        trace: list[dict[str, Any]] = []
        for target in targets:
            try:
                self.computer.launch(target)
            except Exception:
                continue
            on_event(Event("action", f"launch {target}"))
            trace.append({"name": "launch", "arguments": {"name": target}})
        return trace

    def _screen_tag(self):
        """Cheap 'did the context change' probe — frontmost window identity.
        (Geometry is deliberately excluded: a live window drifting by a pixel
        would abort every batch.)"""
        try:
            from .windows import frontmost_window

            w = frontmost_window(exclude_pid=os.getpid())
        except Exception:
            return None
        return None if w is None else (w.app, w.window_id)

    def _dump_elements(self) -> str:
        """The frontmost app's UI elements as text for the vision loop.

        Skipped when scoped (the crop hides global coords) or when AX is
        unavailable. Stored on self so click_element can resolve indices.
        """
        self._step_elements = []
        if self.computer.scope is not None or platform.system() != "Darwin":
            return ""
        try:
            from .elements import frontmost_elements

            _app, elements = frontmost_elements()
        except Exception:
            return ""
        self._step_elements = elements
        return "\n".join(e.line() for e in elements)

    def _agent_loop(self, task: str, on_event: EventSink, context: str = "") -> Result:
        prompt = f"Task: {task}" + (f"\n{context}" if context else "")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        trace: list[dict[str, Any]] = []
        if self.computer.scope is not None:
            # Replays of this flow reapply the same region before acting.
            scope = self.computer.scope
            trace.append(
                {
                    "name": "_scope",
                    "arguments": {
                        "region": list(scope.bounds()),
                        "app": getattr(scope, "app", ""),
                    },
                }
            )
        steps = 0
        verified = False
        fresh_screenshot = False
        fallback_used = False
        while steps < self.config.max_steps:
            if self._stop:
                return Result("stopped", "Stopped by user", steps, 0.0, trace)
            if not fresh_screenshot:
                self._append_screenshot(messages, "Next action?")
            fresh_screenshot = False
            _prune_images(messages)
            steps += 1
            on_event(Event("step", f"Step {steps}", {"step": steps}))
            try:
                message = self.client.complete(messages)
            except LLMError as exc:
                # browser-use's fallback_llm: swap models once and keep going
                if self.config.fallback_model and not fallback_used:
                    fallback_used = True
                    fallback = self.config.fallback_model
                    self.client.config.model = fallback
                    on_event(Event("info", f"model error — switched to {fallback}"))
                    continue
                on_event(Event("error", str(exc)))
                return Result("error", str(exc), steps, 0.0, trace)

            calls = message.get("tool_calls") or []
            if not calls:
                text = (message.get("content") or "").strip()
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": "Respond with a tool call, not prose."})
                continue

            call = calls[0]
            name = call["function"]["name"]
            args = _parse_args(call["function"].get("arguments"))
            messages.append({"role": "assistant", "tool_calls": [call], "content": None})

            if name == "done":
                summary = args.get("summary", "")
                success = bool(args.get("success"))
                if success and self.config.verify_done and not verified:
                    verified = True
                    fresh_screenshot = True
                    on_event(Event("step", "checking the screen before calling it done"))
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id", ""),
                            "content": "not accepted yet — verify against a fresh screenshot",
                        }
                    )
                    self._append_screenshot(messages, VERIFY_PROMPT)
                    continue
                status = "success" if success else "failed"
                on_event(Event("done", summary or status, {"status": status}))
                return Result(status, summary, steps, 0.0, trace)

            on_event(Event("action", _describe(name, args), {"name": name, "arguments": args}))
            try:
                override = self._apply(name, args)
                observation = "ok"
            except Exception as exc:  # noqa: BLE001 - surfaced back to the model
                override = None
                observation = f"action failed: {exc}"
                on_event(Event("error", observation))
            trace.append(override or {"name": name, "arguments": args})
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id", ""), "content": observation}
            )

        return Result("failed", f"Hit the {self.config.max_steps} step limit", steps, 0.0, trace)

    def _append_screenshot(self, messages: list[dict[str, Any]], prompt: str) -> None:
        shot = self.computer.screenshot()
        text = f"Screenshot ({shot.width}x{shot.height}). {prompt}"
        elements = self._dump_elements()
        if elements:
            text += "\nInteractive elements (call click_element with the index):\n" + elements
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": shot.data_url()}},
                ],
            }
        )

    def _apply(self, name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """Execute one action. Returns a trace-override when what actually ran
        differs from what was asked (e.g. click_element resolves to raw coords).
        """
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
        elif name == "launch":
            c.launch(args["name"])
        elif name == "click_at":
            c.click_at(args["x"], args["y"])
        elif name == "click_element":
            try:
                el = self._step_elements[int(args["index"])]
            except (KeyError, TypeError, ValueError, IndexError):
                raise ValueError(
                    f"no element {args.get('index')!r} in the current element list"
                ) from None
            if el.press():
                time.sleep(c.action_delay)
            else:
                c.click_at(el.x, el.y)
            # record raw coords so replays work without an element dump
            return {"name": "click_at", "arguments": {"x": el.x, "y": el.y}}
        elif name == "batch":
            tag = self._screen_tag()
            executed: list[dict[str, Any]] = []
            for sub in args.get("actions", [])[:30]:
                if self._stop:
                    break
                if not isinstance(sub, dict):
                    continue
                sub_name = sub.get("name")
                if sub_name in {"batch", "done", "_scope"}:
                    continue
                self._apply(sub_name, sub)
                executed.append(sub)
                # browser-use aborts the batch when the page changes — the
                # plan's tail assumed an unchanged screen. Replays run fully.
                if not self._replaying and self._screen_tag() != tag:
                    break
            if len(executed) != len(args.get("actions", [])):
                return {"name": "batch", "arguments": {"actions": executed}}
        elif name == "wait":
            time.sleep(min(float(args.get("seconds", 1)), 10))
        elif name == "_scope":
            c.scope = ScopeRegion(tuple(args["region"]), args.get("app", ""))
        else:
            raise ValueError(f"unknown action {name}")
        return None


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
    if name == "click_at":
        return f"click ({args.get('x')}, {args.get('y')})"
    if name == "click_element":
        return f"click element {args.get('index')}"
    if name == "type_text":
        text = str(args.get("text", ""))
        return f"type {text[:40]!r}"
    if name == "key":
        return f"key {args.get('keys')}"
    if name == "launch":
        return f"launch {args.get('name')}"
    if name == "scroll":
        return f"scroll {args.get('amount')}"
    if name == "drag":
        return f"drag to ({args.get('x2')}, {args.get('y2')})"
    if name == "batch":
        return f"batch ×{len(args.get('actions', []))}"
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
