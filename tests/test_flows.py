from imit8.flows import FlowStore, fingerprint, normalize


def store(tmp_path):
    return FlowStore(tmp_path / "imit8.db")


def test_normalize_drops_noise_words_and_punctuation():
    assert normalize("Open the Spotify app, please!") == "open spotify app"
    assert fingerprint("open spotify") == fingerprint("Open the Spotify!")


def test_run_count_increments_for_same_task(tmp_path):
    s = store(tmp_path)
    s.record_run("open spotify", status="success", steps=3, duration=2.0)
    flow = s.record_run("Open the spotify", status="success", steps=3, duration=4.0)
    assert flow.run_count == 2
    assert flow.badge() == "used 2 times"
    assert flow.avg_duration == 3.0


def test_fuzzy_match_groups_near_identical_tasks(tmp_path):
    s = store(tmp_path)
    s.record_run("open spotify and play lofi", status="success", steps=1, duration=1.0)
    flow = s.record_run("open spotify and play lofi!", status="success", steps=1, duration=1.0)
    assert flow.run_count == 2


def test_distinct_tasks_are_separate_flows(tmp_path):
    s = store(tmp_path)
    s.record_run("open spotify", status="success", steps=1, duration=1.0)
    s.record_run("send an email to mom", status="success", steps=1, duration=1.0)
    assert len(s.all_flows()) == 2


def test_only_successful_runs_store_a_trace(tmp_path):
    s = store(tmp_path)
    trace = [{"name": "key", "arguments": {"keys": "cmd+space"}}]
    failed = s.record_run("open spotify", status="failed", steps=1, duration=1.0, trace=trace)
    assert failed.trace is None and not failed.replayable

    ok = s.record_run("open spotify", status="success", steps=1, duration=1.0, trace=trace)
    assert ok.replayable and ok.trace == trace


def test_forget_removes_flow_and_runs(tmp_path):
    s = store(tmp_path)
    flow = s.record_run("open spotify", status="success", steps=1, duration=1.0)
    s.forget(flow.id)
    assert s.all_flows() == []
    assert s.history(flow.id) == []


def test_suggestions_are_ordered_by_run_count(tmp_path):
    s = store(tmp_path)
    s.record_run("rare task", status="success", steps=1, duration=1.0)
    for _ in range(3):
        s.record_run("common task", status="success", steps=1, duration=1.0)
    assert [f.task for f in s.suggestions()][0] == "common task"
