"""The commit gate: nothing irreversible without one explicit yes for that exact action. Fakes only."""

import pytest
from fakes import act, agent_with, decision, fake_page

from assistant import agent as loop
from assistant.browser import StalePage

META = {"model": "fake", "latency_ms": 1}
CARD = {"action_label": "Pay ₹79", "amount": "₹79", "lines": ["Diet Coke 300 ml", "Home"], "element": ""}
WAIT = {"id": "wait", "kind": "wait", "label": "Wait", "node": None}


@pytest.fixture(autouse=True)
def card(monkeypatch):
    monkeypatch.setattr(loop.jobs, "confirm_card", lambda context, page, label, elements=(): (dict(CARD), META))


def outcomes(monkeypatch, *results):
    queue = list(results)
    monkeypatch.setattr(loop.jobs, "outcome", lambda context, page, label: (*queue.pop(0), META))


def clicked(a):
    return [call.args[1]["id"] for call in a.browser.act.call_args_list]


def approve(a, d):
    """After a yes the page is read again before acting: the first act stops as stale, the next one runs."""
    with pytest.raises(StalePage):
        act(a, d)
    return act(a, d)


def test_a_commit_click_waits_for_an_explicit_yes(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"option": "yes"}])
    outcomes(monkeypatch, ("success", "Order placed #A1B2"))
    with pytest.raises(StalePage):
        act(a, decision("e6", "CLICK"))
    assert clicked(a) == [] and a.state["approval"]["target"] == (None, 11)
    act(a, decision("e6", "CLICK"))
    assert a.asked[0]["kind"] == "confirm" and a.asked[0]["options"][0]["label"] == "Yes, Pay ₹79"
    assert len(a.asked) == 1 and clicked(a) == ["e6"] and a.state["approval"] is None and a.state["commit"] is None
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


def test_the_commit_counts_even_if_the_next_observation_fails(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"option": "yes"}, {"option": "no"}, {"option": "cancel"}])
    with pytest.raises(StalePage):
        act(a, decision("e6", "CLICK"))
    a.browser.observe.side_effect = StalePage("still navigating")
    with pytest.raises(StalePage):
        act(a, decision("e6", "CLICK"))
    assert clicked(a) == ["e6"] and a.state["approval"] is None
    assert a.state["commit"] == {"label": "Pay ₹79", "checks": 0}
    a.browser.observe.side_effect = None
    act(a, decision("e6", "CLICK"))  # a second Pay needs a second yes
    assert a.asked[1]["kind"] == "confirm" and clicked(a) == ["e6"]


def test_an_approval_is_for_one_action_only(tmp_path, monkeypatch):
    complaint = {"action_label": "Submit complaint", "amount": "", "lines": [], "element": ""}
    monkeypatch.setattr(loop.jobs, "confirm_card", lambda context, page, label, elements=(): (complaint, META))
    a = agent_with(tmp_path, replies=[{"option": "yes"}, {"option": "no"}, {"option": "cancel"}])
    act(a, decision("CONFIRM", "CONFIRM"))
    act(a, decision("e6", "CLICK"))  # "Pay ₹79" is not the approved complaint
    assert clicked(a) == [] and a.asked[1]["kind"] == "confirm"


def test_a_confirmed_commit_the_regex_misses_still_spends_the_approval(tmp_path, monkeypatch):
    page = fake_page(actions=[{"id": "e1", "kind": "click", "label": "Place your order", "role": "button",
                               "node": 5}, WAIT])
    card = {"action_label": "Place your order", "amount": "₹79", "lines": [], "element": "1"}
    monkeypatch.setattr(loop.jobs, "confirm_card", lambda context, page, label, elements=(): (card, META))
    outcomes(monkeypatch, ("success", "Pay ₹79"))
    a = agent_with(tmp_path, page=page, replies=[{"option": "yes"}])
    act(a, decision("CONFIRM", "CONFIRM"))
    assert a.state["approval"]["target"] == (None, 5)
    act(a, decision("e1", "CLICK"))
    assert clicked(a) == ["e1"] and a.state["approval"] is None
    assert a.state["memory"][-1] == "Done: Place your order (Pay ₹79)"


def test_a_changed_total_needs_a_new_approval(tmp_path):
    a = agent_with(tmp_path, replies=[{"option": "no"}, {"option": "items"}])
    a.state["approval"] = {"action_label": "Pay ₹79", "amount": "₹79", "host": "shop.test", "step": 0,
                           "target": (None, 11)}
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


def test_enter_or_space_on_the_page_checks_the_focused_control(tmp_path):
    a = agent_with(tmp_path, replies=[{"option": "no"}, {"option": "payment"}])
    a.browser.focused_label.return_value = "Pay ₹79"
    page = fake_page()
    page["actions"].append({"id": "key_page", "kind": "key", "label": "The page", "role": "page", "node": None})
    a.state["page"] = a.browser.observe.return_value = page
    act(a, decision("key_page", "PRESS_KEY", key="Space"))
    assert a.asked[0]["kind"] == "confirm" and clicked(a) == []


def test_pending_outcomes_are_rechecked_and_unclear_ones_stop_everything(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"option": "yes"}])
    outcomes(monkeypatch, ("pending", ""), ("unclear", ""))
    approve(a, decision("e6", "CLICK"))
    assert a.state["commit"]["checks"] == 1 and a.state["status"] == "ready"
    act(a, decision("e5", "CLICK"))  # e.g. a step on the bank's page
    assert a.state["status"] == "blocked" and "can't tell whether Pay ₹79 went through" in a.told[-1]["text"]


def test_an_outcome_the_model_cannot_classify_stops_the_task(tmp_path, monkeypatch):
    def broken(context, page, label):
        raise ValueError("The text model returned an invalid outcome; nothing executed.")

    monkeypatch.setattr(loop.jobs, "outcome", broken)
    a = agent_with(tmp_path, replies=[{"option": "yes"}])
    approve(a, decision("e6", "CLICK"))
    assert a.state["status"] == "blocked" and "can't tell whether Pay ₹79 went through" in a.told[-1]["text"]


def test_a_stale_page_after_approval_keeps_the_approval(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"option": "yes"}])
    outcomes(monkeypatch, ("success", "Pay ₹79"))
    with pytest.raises(StalePage):
        act(a, decision("e6", "CLICK"))
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
    approve(a, decision("e6", "CLICK"))
    assert sorted(a.vault.writes) == [("card.card1111.cvv", "123"), ("card.card1111.expiry", "12/29"),
                                      ("card.card1111.number", "4111111111111111")]
    assert a.profile.data["cards"][-1] == {"id": "card1111", "label": "Card ••1111", "last4": "1111"}
    assert a.state["card_parts"] == {}


def test_done_waits_for_a_payment_that_is_still_settling(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    a.state["commit"] = {"label": "Pay ₹79", "checks": 0}
    outcomes(monkeypatch, ("pending", ""), ("success", "Order placed #A1B2"))
    results = []
    monkeypatch.setattr(loop.jobs, "result",
                        lambda context, page: results.append(page["url"]) or ("Ordered.", "Order placed #A1B2", True,
                                                                              META))
    act(a, decision("DONE", "DONE"))
    assert a.state["status"] == "ready" and results == [] and a.state["commit"]["checks"] == 1
    assert a.state["memory"][-1] == "Waiting for the result of Pay ₹79 before finishing."
    act(a, decision("DONE", "DONE"))
    assert a.state["status"] == "done" and len(results) == 1
    assert "Done: Pay ₹79 (Order placed #A1B2)" in a.state["memory"]
