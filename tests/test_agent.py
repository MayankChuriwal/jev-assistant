"""Offline contracts for the task loop. The browser and both models are fakes."""

import time
from unittest.mock import Mock

import pytest

from assistant import agent as loop
from assistant.browser import StalePage, fingerprint
from assistant.questions import MAX_ACTIONS


def page():
    state = {
        "url": "https://example.test/", "title": "Search", "text": "Search", "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "e4", "kind": "upload", "label": "File input: Statement", "role": "file", "node": 30},
            {"id": "e5", "kind": "drag", "label": "Card A", "role": "draggable", "node": 40},
            {"id": "wait", "kind": "wait", "label": "Wait", "node": None},
            {"id": "open_url", "kind": "open_url", "label": "Open a web address", "node": None},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def decision(choice="e1", operation="TYPE_TEXT", **extra):
    return {"choice": choice, "operation": operation, "target": "1", "confidence": 1.0,
            "probabilities": {choice: 1.0}, "latency_ms": 10, "usage": {}, **extra}


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    a.provide_file = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p), act=Mock(return_value={})),
        "page": p, "decision": decision(), "goal": "Find a book", "history": [], "decisions": [],
        "status": "predicted", "started_at": time.perf_counter(), "record": False, "text_calls": [], "files": [],
    }
    return a


def act(runner):
    return runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        act(runner)
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), {}]
    with pytest.raises(StalePage):
        act(runner)
    runner.state["decision"] = decision()
    act(runner)
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_args.args[0] == "TYPE_TEXT"
    assert runner.state["browser"].act.call_args.kwargs == {"text": "book"}
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), {}]
    with pytest.raises(StalePage):
        act(runner)
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    act(runner)
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait", "WAIT")
        act(runner)
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_three_actions_without_change_block_the_task(runner):
    for _ in range(3):
        runner.state["decision"] = decision("e3", "CLICK")
        act(runner)
    assert runner.state["status"] == "blocked"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3", "CLICK")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        act(runner)
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


@pytest.mark.parametrize("operation, choice, extra, expected", [
    ("PRESS_KEY", "e1", {"key": "Enter"}, {"key": "Enter"}),
    ("DRAG", "e5", {"drop": "e3"}, {"drop": "e3"}),
    ("CLICK", "e3", {}, {}),
])
def test_operation_inputs_reach_the_browser(runner, operation, choice, extra, expected):
    runner.state["decision"] = decision(choice, operation, **extra)
    act(runner)
    args, kwargs = runner.state["browser"].act.call_args
    assert args[0] == operation and args[1]["id"] == choice
    if "drop" in expected:
        assert kwargs["drop"]["id"] == expected["drop"]
    else:
        assert kwargs == expected


def test_open_url_asks_the_text_model_for_the_address(runner, monkeypatch):
    monkeypatch.setattr(loop, "url_value", Mock(return_value=("https://x.test/", {"model": "t", "latency_ms": 1})))
    runner.state["decision"] = decision("open_url", "OPEN_URL")
    act(runner)
    assert runner.state["browser"].act.call_args.kwargs == {"url": "https://x.test/"}
    assert runner.state["history"][-1]["text"] == "https://x.test/"


def test_upload_uses_the_file_provider(runner):
    runner.provide_file = Mock(return_value=["/tmp/statement.pdf"])
    runner.state["decision"] = decision("e4", "UPLOAD")
    act(runner)
    assert runner.state["browser"].act.call_args.kwargs == {"files": ["/tmp/statement.pdf"]}


def test_upload_without_a_file_provider_blocks_before_input(runner):
    runner.state["decision"] = decision("e4", "UPLOAD")
    with pytest.raises(ValueError, match="needs a file"):
        act(runner)
    runner.state["browser"].act.assert_not_called()
    assert runner.state["status"] == "blocked"


def test_action_budget_stops_before_input(runner):
    runner.state["history"] = [{}] * MAX_ACTIONS
    runner.state["decision"] = decision("e3", "CLICK")
    with pytest.raises(ValueError, match="budget"):
        act(runner)
    runner.state["browser"].act.assert_not_called()


def test_done_needs_an_unchanged_page(runner):
    runner.state["decision"] = decision("DONE", "DONE")
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        act(runner)
    assert runner.state["status"] == "ready"


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready" and runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()
