import io
import json

import pytest

from imit8 import api, mcp
from imit8.schedule import ScheduleStore


@pytest.fixture
def store(tmp_path):
    return ScheduleStore(tmp_path / "imit8.db")


def test_parse_slots_maps_days_and_times():
    assert api.parse_slots(["mon", "Friday"], ["09:00", "17:30"]) == [
        (0, 540),
        (4, 540),
        (0, 1050),
        (4, 1050),
    ]


def test_parse_slots_rejects_nonsense_days():
    with pytest.raises(ValueError):
        api.parse_slots(["funday"], ["09:00"])


def test_schedule_accepts_weekly_slots(store):
    created = api.schedule("water the plants", days=["tue"], times=["06:30"], store=store)
    assert created["slots"] == [[1, 390]]
    assert not created["once"]
    assert [s["id"] for s in api.schedules(store=store)] == [created["id"]]


def test_schedule_accepts_a_one_off_timestamp(store):
    created = api.schedule("back up notes", once="2030-01-04T18:00", store=store)
    assert created["once"]
    assert created["next_run"] == "2030-01-04T18:00"


def test_schedule_needs_days_and_times_or_once(store):
    with pytest.raises(ValueError):
        api.schedule("nothing", days=["mon"], store=store)


def test_unschedule_and_pause(store):
    created = api.schedule("water the plants", days=["tue"], times=["06:30"], store=store)
    assert api.set_enabled(created["id"], False, store=store)["enabled"] is False
    api.unschedule(created["id"], store=store)
    assert api.schedules(store=store) == []


def roundtrip(*requests):
    out = io.StringIO()
    mcp.serve(io.StringIO("\n".join(json.dumps(r) for r in requests)), out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_mcp_lists_its_tools():
    [response] = roundtrip({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert "imit8_run" in names and "imit8_schedule" in names


def test_mcp_initialize_reports_tool_capability():
    [response] = roundtrip({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response["result"]["capabilities"] == {"tools": {}}
    assert response["result"]["serverInfo"]["name"] == "imit8"


def test_mcp_notifications_get_no_response():
    assert roundtrip({"jsonrpc": "2.0", "method": "notifications/initialized"}) == []


def test_mcp_calls_a_tool(monkeypatch):
    monkeypatch.setitem(mcp.HANDLERS, "imit8_run", lambda task: {"status": "success", "task": task})
    [response] = roundtrip(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "imit8_run", "arguments": {"task": "open spotify"}},
        }
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload == {"status": "success", "task": "open spotify"}
    assert response["result"]["isError"] is False


def test_mcp_reports_tool_errors_instead_of_crashing(monkeypatch):
    def boom(task):
        raise ValueError("no such window")

    monkeypatch.setitem(mcp.HANDLERS, "imit8_run", boom)
    [response] = roundtrip(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "imit8_run", "arguments": {"task": "x"}},
        }
    )
    assert response["result"]["isError"]
    assert "no such window" in response["result"]["content"][0]["text"]
