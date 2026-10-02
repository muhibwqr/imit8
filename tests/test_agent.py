import json

import pytest

from imit8.agent import Agent, _prune_images
from imit8.computer import Screenshot
from imit8.config import Config
from imit8.flows import FlowStore


class FakeComputer:
    def __init__(self):
        self.actions = []
        self.scope = None
        self.action_delay = 0

    def screenshot(self):
        return Screenshot(image=b"\xff\xd8\xff", width=1280, height=800)

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

    def launch(self, name):
        self.actions.append(("launch", name))

    def click_at(self, x, y):
        self.actions.append(("click_at", x, y))


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
    # 'open spotify' triggers the deterministic pre-action launch, then the model's actions
    assert computer.actions == [
        ("launch", "spotify"),
        ("key", "cmd+space"),
        ("type_text", "spotify"),
    ]

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
    assert computer.actions == [("launch", "spotify"), ("key", "cmd+space")]
    assert agent.client.script == []  # model was never consulted
    assert agent.store.find("open spotify").run_count == 2


def test_launch_action_runs_and_replays(tmp_path):
    agent, computer = build(
        tmp_path,
        [
            ("launch", {"name": "Calculator"}),
            ("done", {"success": True, "summary": "calculator is open"}),
        ],
    )
    agent.run("open calculator")
    # the pre-pass already launched it; the model's launch is belt-and-suspenders
    assert computer.actions == [("launch", "calculator"), ("launch", "Calculator")]

    computer.actions.clear()
    agent.replay(agent.store.find("open calculator"))
    assert computer.actions == [("launch", "calculator"), ("launch", "Calculator")]


def test_batch_runs_each_sub_action_and_replays(tmp_path):
    agent, computer = build(
        tmp_path,
        [
            (
                "batch",
                {
                    "actions": [
                        {"name": "key", "keys": "space"},
                        {"name": "key", "keys": "space"},
                        {"name": "batch", "actions": [{"name": "key", "keys": "x"}]},
                        {"name": "click", "x": 5, "y": 5},
                    ]
                },
            ),
            ("done", {"success": True, "summary": "flapped"}),
        ],
    )
    agent.run("play flappy bird")

    # nested batches and unknown actions are skipped, the rest fire in order
    assert computer.actions == [
        ("key", "space"),
        ("key", "space"),
        ("click", 5, 5, "left", 1),
    ]

    computer.actions.clear()
    agent.replay(agent.store.find("play flappy bird"))
    assert computer.actions == [
        ("key", "space"),
        ("key", "space"),
        ("click", 5, 5, "left", 1),
    ]


def test_fast_loop_clicks_the_picked_element(tmp_path):
    from imit8.elements import Element

    agent, computer = build(tmp_path, [], verify_done=False)
    elements = [
        Element(0, "AXButton", "Reload", 100, 50),
        Element(1, "AXLink", "Home", 200, 60),
    ]
    answers = iter(
        [
            {
                "done": {"noul": 0.0},
                "pick": {"probabilities": {"0": 0.1, "1": 0.9, "none": 0.0}},
                "key": {"choice": "none"},
            },
            {
                "done": {"noul": 0.95},
                "pick": {"probabilities": {"none": 1.0}},
                "key": {"choice": "none"},
            },
        ]
    )

    class FakeDecisions:
        def decide(self, state, questions):
            assert "go home" in state and "Home" in state
            return next(answers)

    events = []
    result = agent.run_fast(
        "go home",
        events.append,
        elements_fn=lambda: ("TestApp", elements),
        decisions=FakeDecisions(),
    )

    assert result.status == "success"
    assert result.mode == "fast"
    assert computer.actions == [("click_at", 200, 60)]
    assert any(e.kind == "perf" and "decide_ms" in e.data for e in events)
    # the trace replays the resolved click — no model needed
    computer.actions.clear()
    agent.replay(agent.store.find("go home"))
    assert computer.actions == [("click_at", 200, 60)]


def test_fast_loop_presses_elements_before_clicking(tmp_path):
    """AXPress keeps the user's cursor put; click_at is only the fallback."""
    from imit8.elements import Element

    agent, computer = build(tmp_path, [], verify_done=False)
    pressed = Element(0, "AXButton", "Save", 100, 50)
    pressed.press = lambda: True  # a live AX ref
    dead = Element(1, "AXButton", "Ghost", 300, 70)  # no ref -> coordinate click
    elements = [pressed, dead]
    answers = iter(
        [
            {
                "done": {"noul": 0.0},
                "pick": {"probabilities": {"0": 0.9, "none": 0.1}},
                "key": {"choice": "none"},
            },
            {
                "done": {"noul": 0.0},
                "pick": {"probabilities": {"1": 0.9, "none": 0.1}},
                "key": {"choice": "none"},
            },
            {
                "done": {"noul": 0.95},
                "pick": {"probabilities": {"none": 1.0}},
                "key": {"choice": "none"},
            },
        ]
    )

    class FakeDecisions:
        def decide(self, state, questions):
            return next(answers)

    agent.run_fast(
        "save the file",
        lambda e: None,
        elements_fn=lambda: ("TestApp", elements),
        decisions=FakeDecisions(),
    )

    # the pressable element never produced a coordinate click; the dead one did
    assert computer.actions == [("click_at", 300, 70)]


def test_fast_loop_escalates_to_vision_when_stalled(tmp_path):
    agent, computer = build(tmp_path, [("done", {"success": True, "summary": "ok"})])

    class StuckDecisions:
        def decide(self, state, questions):
            return {
                "done": {"noul": 0.0},
                "pick": {"probabilities": {"none": 1.0}},
                "key": {"choice": "none"},
            }

    result = agent.run_fast(
        "do a thing",
        elements_fn=lambda: ("TestApp", []),
        decisions=StuckDecisions(),
    )

    assert result.status == "success"
    assert result.mode == "agent"  # fell back to the vision loop


def test_pre_actions_open_urls_and_apps_before_the_model(tmp_path):
    """browser-use's directly_open_url: a URL in the task opens without a model call."""
    agent, computer = build(tmp_path, [("done", {"success": True, "summary": "ok"})])
    agent.run("open safari and go to https://example.com")

    assert computer.actions == [
        ("launch", "safari"),
        ("launch", "https://example.com"),
    ]
    flow = agent.store.find("open safari and go to https://example.com")
    assert flow.trace[0]["name"] == "launch"
    assert flow.trace[1]["arguments"] == {"name": "https://example.com"}


def test_click_element_resolves_to_raw_coords_and_replays(tmp_path):
    """The model picks an index; the trace records the resolved coordinates."""
    from imit8.elements import Element

    agent, computer = build(
        tmp_path,
        [("click_element", {"index": 1}), ("done", {"success": True, "summary": "ok"})],
    )
    elements = [Element(0, "AXButton", "A", 10, 10), Element(1, "AXButton", "B", 50, 60)]

    def fake_dump():
        agent._step_elements = elements
        return "[0] Button 'A'\n[1] Button 'B'"

    agent._dump_elements = fake_dump
    agent.run("click b")

    assert computer.actions == [("click_at", 50, 60)]
    trace = agent.store.find("click b").trace
    assert trace[0] == {"name": "click_at", "arguments": {"x": 50, "y": 60}}


def test_batch_aborts_when_the_frontmost_window_changes(tmp_path):
    """browser-use: stop the batch when the page changed — the plan is stale."""
    agent, computer = build(
        tmp_path,
        [
            ("batch", {"actions": [{"name": "key", "keys": "space"}] * 3}),
            ("done", {"success": True, "summary": "ok"}),
        ],
    )
    tags = iter(["w1", "w1", "w2", "w2"])  # sub-action 2 changes the screen
    agent._screen_tag = lambda: next(tags, "w2")
    agent.run("tap space")

    assert computer.actions == [("key", "space"), ("key", "space")]
    # the trace records only what actually ran — replays stay honest
    trace = agent.store.find("tap space").trace
    assert trace[0]["name"] == "batch"
    assert len(trace[0]["arguments"]["actions"]) == 2


def test_fallback_model_takes_over_after_an_error(tmp_path):
    from imit8.llm import LLMError

    config = Config(
        api_key="t", max_steps=5, verify_done=False, fallback_model="backup-model"
    )

    class FlakyClient:
        def __init__(self):
            self.calls = []
            self.config = config

        def complete(self, messages, timeout=120.0):
            self.calls.append(messages)
            if len(self.calls) == 1:
                raise LLMError("rate limited")
            return {
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {
                            "name": "done",
                            "arguments": '{"success": true, "summary": "ok"}',
                        },
                    }
                ]
            }

    agent = Agent(
        config=config,
        computer=FakeComputer(),
        client=FlakyClient(),
        store=FlowStore(tmp_path / "imit8.db"),
    )
    result = agent.run("click something")

    assert result.status == "success"
    assert agent.client.config.model == "backup-model"
    assert len(agent.client.calls) == 2


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


def test_scoped_run_records_scope_and_replay_reapplies_it(tmp_path):
    from imit8.computer import ScopeRegion

    agent, computer = build(
        tmp_path,
        [("click", {"x": 5, "y": 5}), ("done", {"success": True, "summary": "ok"})],
    )
    computer.scope = ScopeRegion((10, 10, 40, 20), app="Safari")
    agent.run("scoped click")
    flow = agent.store.find("scoped click")
    assert flow.trace[0]["name"] == "_scope"
    assert flow.trace[0]["arguments"]["region"] == [10, 10, 40, 20]

    seen = []
    real_click = computer.click
    computer.click = lambda *a, **k: (seen.append(computer.scope), real_click(*a, **k))
    computer.scope = None
    agent.replay(agent.store.find("scoped click"))
    assert seen and seen[0].bounds() == (10, 10, 40, 20)
    assert computer.scope is None  # restored after replay


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
