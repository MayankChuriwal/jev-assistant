"""Loop contracts from Plan 1 on the new agent: stale decisions, waits, stuck detection, inputs, budgets."""

from unittest.mock import Mock

import pytest
from fakes import act, agent_with, decision

from assistant import agent as loop
from assistant.browser import ExecutionUncertain, StalePage
from assistant.questions import MAX_ACTIONS


def test_stale_decision_is_consumed_before_any_mutation(tmp_path):
    a = agent_with(tmp_path)
    a.browser.fresh.return_value = False
    with pytest.raises(StalePage):
        act(a, decision("e1", "TYPE_TEXT"))
    a.browser.act.assert_not_called()
    assert a.state["decision"] is None


def test_loading_waits_do_not_trigger_the_no_progress_stop(tmp_path):
    a = agent_with(tmp_path)
    for _ in range(5):
        act(a, decision("wait", "WAIT"))
    assert len(a.state["history"]) == 5 and a.state["status"] == "ready"


def test_three_actions_without_change_request_semantic_recovery(tmp_path):
    a = agent_with(tmp_path)
    for _ in range(3):
        act(a, decision('e5', 'CLICK'))
    assert not a.asked
    assert any(d.get('kind') == 'stalled' for d in a.task.diagnostics)


def test_stale_observation_preserves_the_executed_action(tmp_path):
    a = agent_with(tmp_path)
    a.browser.observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        act(a, decision("e5", "CLICK"))
    assert a.state["history"][-1]["action"] == "Office"
    a.browser.act.assert_called_once()


@pytest.mark.parametrize("operation, choice, extra", [
    ("PRESS_KEY", "e1", {"key": "Tab"}), ("CLICK", "e5", {}),
])
def test_operation_inputs_reach_the_browser(tmp_path, operation, choice, extra):
    a = agent_with(tmp_path)
    act(a, decision(choice, operation, **extra))
    args, kwargs = a.browser.act.call_args
    assert args[0] == operation and args[1]["id"] == choice
    if operation == "DRAG":
        assert kwargs["drop"]["id"] == "e5"
    else:
        assert kwargs == ({"key": "Tab"} if operation == "PRESS_KEY" else {})


def test_open_url_asks_the_text_model_for_the_address(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "url_value", Mock(return_value=("https://x.test/", {"model": "t", "latency_ms": 1})))
    a = agent_with(tmp_path)
    act(a, decision("open_url", "OPEN_URL"))
    assert a.browser.act.call_args.kwargs == {"url": "https://x.test/"}
    assert a.state["history"][-1]["text"] == "https://x.test/"


def test_a_file_provider_answers_uploads_without_asking(tmp_path):
    a = agent_with(tmp_path)
    path = tmp_path / "statement.pdf"
    path.write_bytes(b"PDF")
    a.provide_file = Mock(return_value=[str(path)])
    act(a, decision("e7", "UPLOAD"))
    assert a.browser.act.call_args.kwargs == {"files": [str(path)]} and a.asked == []


def test_the_action_budget_stops_before_input(tmp_path):
    a = agent_with(tmp_path)
    a.task.progress["counts"] = {"actions": MAX_ACTIONS}
    act(a, decision("e5", "CLICK"))
    a.browser.act.assert_not_called()
    assert a.state["status"] == "blocked"


def test_done_needs_an_unchanged_page(tmp_path):
    a = agent_with(tmp_path)
    a.browser.fresh.return_value = False
    with pytest.raises(StalePage):
        act(a, decision("DONE", "DONE"))
    assert a.state["status"] == "ready"


def test_navigation_during_prediction_reobserves_without_action(tmp_path):
    a = agent_with(tmp_path)
    a.browser.fresh.side_effect = StalePage("Document navigating")
    a.command("tick")
    assert a.state["status"] == "ready" and a.state["decision"] is None
    a.browser.act.assert_not_called()


def test_uncertain_execution_is_logged_and_stops_the_task(tmp_path):
    a = agent_with(tmp_path)
    a.browser.act.side_effect = ExecutionUncertain("Browser input did not return")
    act(a, decision("e5", "CLICK"))
    assert a.state["history"][-1]["action"] == "Office" and a.state["history"][-1]["unconfirmed"]
    assert a.state["status"] == "blocked" and "unknown" in a.told[-1]["text"]


def test_repeated_semantic_actions_request_recovery(tmp_path):
    a = agent_with(tmp_path)
    for _ in range(2):
        act(a, decision('e5', 'CLICK'))
    assert any(d.get('kind') == 'stalled' for d in a.task.diagnostics)
    assert not a.asked
