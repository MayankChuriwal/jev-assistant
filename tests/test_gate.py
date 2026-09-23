"""The commit gate: nothing irreversible without one explicit yes, and nothing retried when unclear. Fakes only."""

import pytest
from fakes import act, agent_with, decision, fake_page

from assistant import agent as loop
from assistant.browser import StalePage

META = {"model": "fake", "latency_ms": 1}
CARD = {"action_label": "Pay ₹79", "amount": "₹79", "lines": ["Diet Coke 300 ml", "Home"]}


@pytest.fixture(autouse=True)
def card(monkeypatch):
    monkeypatch.setattr(loop.jobs, "confirm_card", lambda context, page, label: (CARD, META))


def outcomes(monkeypatch, *results):
    queue = list(results)
    monkeypatch.setattr(loop.jobs, "outcome", lambda context, page, label: (*queue.pop(0), META))


def clicked(a):
    return [call.args[1]["id"] for call in a.browser.act.call_args_list]


def test_a_commit_click_waits_for_an_explicit_yes(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"option": "yes"}])
    outcomes(monkeypatch, ("success", "Order placed #A1B2"))
    act(a, decision("e6", "CLICK"))
    assert a.asked[0]["kind"] == "confirm" and a.asked[0]["options"][0]["label"] == "Yes, Pay ₹79"
    assert clicked(a) == ["e6"] and a.state["approval"] is None and a.state["commit"] is None
    assert a.state["memory"][-1] == "Done: Pay ₹79 (Order placed #A1B2)"


def test_no_means_nothing_is_clicked(tmp_path):
    a = agent_with(tmp_path, replies=[{"option": "no"}, {"option": "items"}])
    act(a, decision("e6", "CLICK"))
    assert clicked(a) == [] and "change the items" in a.state["memory"][-1]


def test_one_approval_covers_exactly_one_commit(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"option": "yes"}, {"option": "no"}, {"option": "cancel"}])
    outcomes(monkeypatch, ("failed", "Pay ₹79"))
    act(a, decision("CONFIRM", "CONFIRM"))
    assert a.state["approval"]["amount"] == "₹79"
    act(a, decision("e6", "CLICK"))
    assert clicked(a) == ["e6"] and len(a.asked) == 1 and "didn't go through" in a.told[-1]["text"]
    act(a, decision("e6", "CLICK"))  # a retry is a new commit: ask again
    assert clicked(a) == ["e6"] and a.asked[1]["kind"] == "confirm" and a.state["status"] == "cancelled"


def test_a_changed_total_needs_a_new_approval(tmp_path):
    a = agent_with(tmp_path, replies=[{"option": "no"}, {"option": "items"}])
    a.state["approval"] = {"action_label": "Pay ₹79", "amount": "₹79", "host": "shop.test", "step": 0}
    page = fake_page(text="Checkout\nTotal ₹99\nPay ₹99")
    a.state["page"] = a.browser.observe.return_value = page
    act(a, decision("e6", "CLICK"))
    assert a.asked[0]["kind"] == "confirm" and clicked(a) == []


def test_enter_in_a_field_whose_form_pays_is_a_commit(tmp_path):
    a = agent_with(tmp_path, replies=[{"option": "no"}, {"option": "payment"}])
    act(a, decision("e3", "PRESS_KEY", key="Enter"))
    assert a.asked[0]["kind"] == "confirm" and clicked(a) == []
    act(a, decision("e3", "PRESS_KEY", key="Tab"))
    assert clicked(a) == ["e3"] and len(a.asked) == 2


def test_pending_outcomes_are_rechecked_and_unclear_ones_stop_everything(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"option": "yes"}])
    outcomes(monkeypatch, ("pending", ""), ("unclear", ""))
    act(a, decision("e6", "CLICK"))
    assert a.state["commit"]["checks"] == 1 and a.state["status"] == "ready"
    act(a, decision("e5", "CLICK"))  # e.g. a step on the bank's page
    assert a.state["status"] == "blocked" and "can't tell whether Pay ₹79 went through" in a.told[-1]["text"]


def test_a_stale_page_after_approval_keeps_the_approval(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"option": "yes"}])
    outcomes(monkeypatch, ("success", "Pay ₹79"))
    a.browser.act.side_effect = [StalePage("moved"), {}]
    with pytest.raises(StalePage):
        act(a, decision("e6", "CLICK"))
    assert a.state["approval"] is not None
    act(a, decision("e6", "CLICK"))
    assert len(a.asked) == 1 and a.state["approval"] is None


def test_card_details_typed_during_the_task_can_be_saved_after_success(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"option": "yes"}, {"option": "save"}])
    a.state["card_parts"] = {"card.number": "4111111111111111", "card.expiry": "12/29", "card.cvv": "123"}
    outcomes(monkeypatch, ("success", "Pay ₹79"))
    act(a, decision("e6", "CLICK"))
    assert sorted(a.vault.writes) == [("card.card1111.cvv", "123"), ("card.card1111.expiry", "12/29"),
                                      ("card.card1111.number", "4111111111111111")]
    assert a.profile.data["cards"][-1] == {"id": "card1111", "label": "Card ••1111", "last4": "1111"}
    assert a.state["card_parts"] == {}
