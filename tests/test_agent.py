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


def test_three_actions_without_change_ask_the_user_for_help(tmp_path, monkeypatch):
    help_step = {"rung": "guide", "text": "I'm stuck on this page. What should I do?", "options": []}
    monkeypatch.setattr(loop.jobs, "unblock", lambda context, page, elements, must_stop=False: (help_step, {}))
    monkeypatch.setattr(loop.jobs, "hint", lambda context, page, text=None, image=None: ("Type the phone number", {}))
    a = agent_with(tmp_path, replies=[{"text": "type my number in the phone box"}])
    for _ in range(3):
        act(a, decision("e5", "CLICK"))
    assert a.asked[-1]["text"] == "I'm stuck on this page. What should I do?"  # never a silent stop
    assert a.state["memory"][-2] == "My last 3 steps (Office) changed nothing on this page."
    assert a.state["memory"][-1] == "Hint from the user: Type the phone number" and a.state["status"] == "ready"


def test_stale_observation_preserves_the_executed_action(tmp_path):
    a = agent_with(tmp_path)
    a.browser.observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        act(a, decision("e5", "CLICK"))
    assert a.state["history"][-1]["action"] == "Office"
    a.browser.act.assert_called_once()


@pytest.mark.parametrize("operation, choice, extra", [
    ("PRESS_KEY", "e1", {"key": "Tab"}), ("DRAG", "e4", {"drop": "e5"}), ("CLICK", "e5", {}),
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
    a.provide_file = Mock(return_value=["/tmp/statement.pdf"])
    act(a, decision("e7", "UPLOAD"))
    assert a.browser.act.call_args.kwargs == {"files": ["/tmp/statement.pdf"]} and a.asked == []


def test_the_action_budget_stops_before_input(tmp_path):
    a = agent_with(tmp_path)
    a.state["history"] = [{}] * MAX_ACTIONS
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
    assert a.state["status"] == "blocked" and "can't confirm" in a.told[-1]["text"]


def test_going_in_circles_asks_the_user_for_help(tmp_path, monkeypatch):
    from copy import deepcopy
    from itertools import count

    from assistant.browser import fingerprint

    help_step = {"rung": "guide", "text": "I keep going in circles. What should I do?", "options": []}
    monkeypatch.setattr(loop.jobs, "unblock", lambda context, page, elements, must_stop=False: (help_step, {}))
    monkeypatch.setattr(loop.jobs, "hint", lambda context, page, text=None, image=None: ("Use Instamart", {}))
    a = agent_with(tmp_path, replies=[{"text": "open Instamart"}])
    shown, n = a.browser.observe.return_value, count(1)

    def moving(screenshot=False):  # every click changes the page, so only the repetition gives the loop away
        page = deepcopy(shown)
        page["text"] += f"\nMenu section {next(n)}"
        page["fingerprint"] = fingerprint(page)
        return page

    a.browser.observe.side_effect = moving
    for _ in range(4):
        act(a, decision("e5", "CLICK"))
    assert a.asked[-1]["text"] == "I keep going in circles. What should I do?"
    assert a.state["memory"][-2] == "I've done “Office” 4 times recently without getting closer."


def test_a_start_link_that_lands_on_an_error_page_falls_back_to_the_home_page(tmp_path):
    from fakes import FakeVault, fake_page

    from assistant.profile import Profile

    missing = fake_page(text="404: This page could not be found.\nGo to Home", url="https://www.zepto.com/groceries/x")
    home = fake_page(text="Zepto\nSearch for products", url="https://www.zepto.com/")
    browser = Mock(observe=Mock(side_effect=[missing, home]))
    a = loop.Agent("https://www.zepto.com/groceries/x", "Order milk on Zepto", browser=browser,
                   profile=Profile(tmp_path / "profile.json"), vault=FakeVault({}), title="Zepto · milk")
    browser.navigate.assert_called_once_with("https://www.zepto.com/")
    assert a.state["page"]["url"] == "https://www.zepto.com/"
