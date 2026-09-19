import json

import pytest

from imit8.agent import Agent, _prune_images
from imit8.computer import Screenshot
from imit8.config import Config
from imit8.flows import FlowStore


class FakeComputer:
    def __init__(self):
        self.actions = []

    def screenshot(self):
        return Screenshot(png=b"\x89PNG", width=1280, height=800)

    def click(self, x, y, button="left", clicks=1):
        self.actions.append(("click", x, y, button, clicks))

    def type_text(self, text):
        self.actions.append(("type_text", text))

    def key(self, keys):
        self.actions.append(("key", keys))

    def scroll(self, x, y, amount):
        self.actions.append(("scroll", x, y, amount))

    def drag(self, x1, y1, x2, y2):
        self.actions.append(("drag", x1, y1, x2, y2))


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def complete(self, messages, timeout=120.0):
        self.calls.append(messages)
        name, args = self.script.pop(0)
        return {
            "tool_calls": [
                {
                    "id": f"call_{len(self.calls)}",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ]
        }


def build(tmp_path, script, verify_done=False):
    computer = FakeComputer()
    agent = Agent(
        config=Config(api_key="test", max_steps=10, verify_done=verify_done),
        computer=computer,
        client=FakeClient(script),
        store=FlowStore(tmp_path / "imit8.db"),
    )
    return agent, computer


def test_agent_executes_actions_and_records_the_flow(tmp_path):
    agent, computer = build(
        tmp_path,
        [
            ("key", {"keys": "cmd+space"}),
            ("type_text", {"text": "spotify"}),
            ("done", {"success": True, "summary": "spotify is open"}),
        ],
    )
    result = agent.run("open spotify")

    assert result.status == "success"
    assert result.steps == 3
    assert computer.actions == [("key", "cmd+space"), ("type_text", "spotify")]

    flow = agent.store.find("open spotify")
    assert flow.run_count == 1
    assert flow.replayable


def test_replay_reruns_the_trace_without_the_model(tmp_path):
    agent, computer = build(
        tmp_path,
        [
            ("key", {"keys": "cmd+space"}),
            ("done", {"success": True, "summary": "done"}),
        ],
    )
    agent.run("open spotify")
    computer.actions.clear()

    flow = agent.store.find("open spotify")
    result = agent.replay(flow, lambda e: None)

    assert result.mode == "replay"
    assert computer.actions == [("key", "cmd+space")]
    assert agent.client.script == []  # model was never consulted
    assert agent.store.find("open spotify").run_count == 2


def test_replay_requires_a_recorded_trace(tmp_path):
    agent, _ = build(tmp_path, [("done", {"success": False, "summary": "nope"})])
    agent.run("impossible task")
    flow = agent.store.find("impossible task")
    with pytest.raises(ValueError):
        agent.replay(flow)


def test_flow_badge_event_reports_repeat_count(tmp_path):
    script = [("done", {"success": True, "summary": "ok"})] * 2
    agent, _ = build(tmp_path, script)
    events = []
    agent.run("open spotify")
    agent.run("open spotify", events.append)
    badge = next(e for e in events if e.kind == "flow")
    assert "used 2 times" in badge.text
    assert badge.data["run_count"] == 2


def test_failed_action_is_reported_to_the_model(tmp_path):
    agent, _ = build(
        tmp_path,
        [("click", {}), ("done", {"success": True, "summary": "ok"})],
    )
    result = agent.run("click something")
    tool_messages = [m for m in agent.client.calls[-1] if m.get("role") == "tool"]
    assert tool_messages and "action failed" in tool_messages[-1]["content"]
    assert result.status == "success"


def test_step_limit_stops_the_loop(tmp_path):
    agent, _ = build(tmp_path, [("key", {"keys": "a"})] * 50)
    agent.config.max_steps = 3
    result = agent.run("loop forever")
    assert result.status == "failed" and result.steps == 3


def test_success_is_double_checked_against_a_fresh_screenshot(tmp_path):
    agent, _ = build(
        tmp_path,
        [
            ("done", {"success": True, "summary": "looks done"}),
            ("done", {"success": True, "summary": "example.com is loaded"}),
        ],
        verify_done=True,
    )
    result = agent.run("go to example.com")
    assert result.status == "success"
    assert result.summary == "example.com is loaded"
    assert len(agent.client.calls) == 2


def test_verification_can_overturn_a_premature_success(tmp_path):
    agent, computer = build(
        tmp_path,
        [
            ("done", {"success": True, "summary": "probably fine"}),
            ("click", {"x": 10, "y": 20}),
            ("done", {"success": False, "summary": "the tab closed instead"}),
        ],
        verify_done=True,
    )
    result = agent.run("go to example.com")
    assert result.status == "failed"
    assert computer.actions == [("click", 10, 20, "left", 1)]
    assert not agent.store.find("go to example.com").replayable


def test_prune_images_keeps_only_recent_screenshots():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"shot {i}"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            ],
        }
        for i in range(5)
    ]
    _prune_images(messages, keep=2)
    remaining = [m for m in messages if isinstance(m["content"], list)]
    assert len(remaining) == 2
    assert messages[0]["content"] == "shot 0 [older screenshot dropped]"
