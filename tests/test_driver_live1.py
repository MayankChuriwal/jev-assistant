"""Live bench run 1 (Uber) findings: sentence-ending prices, SELECT on list items, questions written in the wrong
field, refusals that never escalate, commits mini didn't flag, and pages whose text ticks every second."""

from copy import deepcopy

from fakes import fake_page, jev_fake, mini_with, out, script

from assistant import rules
from assistant.browser import fingerprint

WAIT = {"id": "wait", "kind": "wait", "label": "Wait", "node": None}
HERE = {"id": "e1", "kind": "click", "label": "Center Tower-1 Your current location", "role": "option", "node": 1}
GO = {"id": "e2", "kind": "click", "label": "Request Uber Go AC", "role": "button", "node": 2}


def test_a_price_that_ends_a_sentence_keeps_no_full_stop():
    assert rules.PRICE.findall("fare shown ₹805.92 to ₹948.14. Confirm, or ₹1,219.") == ["₹805.92", "₹948.14",
                                                                                          "₹1,219"]


def test_select_on_a_list_item_clicks_it(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Pickup", actions=[HERE, WAIT]))
    script(monkeypatch, out("SELECT", "Center Tower-1 Your current location"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_args.args[:2] == ("CLICK", HERE)


def test_a_question_written_in_text_is_still_asked(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "the cheapest"}], page=fake_page(text="Rides", actions=[WAIT]))
    script(monkeypatch, out("ASK", text="Which ride do you want?"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked[0]["text"] == "Which ride do you want?"


def test_refusals_escalate_then_ask_the_user(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "choose Cash"}], page=fake_page(text="Payment Methods", actions=[WAIT]))
    monkeypatch.setattr(a, "look", lambda page, purpose: None)
    script(monkeypatch, *[out("CLICK", "Payment Methods")] * 6)
    jev_fake(monkeypatch)
    for _ in range(3):
        a.command("tick")
    assert "refused 3 times" in a.state["feedback"][-1]
    for _ in range(3):
        a.command("tick")
    assert a.asked[-1]["text"].startswith("I'm stuck") and a.state["chat"][-1]["text"] == "“choose Cash”"


def test_a_commit_mini_did_not_flag_goes_back_for_a_summary(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="₹805.92\nRequest Uber Go AC", actions=[GO, WAIT]))
    script(monkeypatch, out("CLICK", "Request Uber Go AC", commits=False, message="Where is the payment panel?"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == [] and "commits true" in a.state["feedback"][0] and "summary" in a.state["feedback"][0]


def test_a_page_whose_text_ticks_still_gets_its_question(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "Cash"}], page=fake_page(text="Uber Go 4 mins away", actions=[WAIT]))
    ticked = deepcopy(a.state["page"])
    ticked["text"] = "Uber Go 3 mins away"
    ticked["fingerprint"] = fingerprint(ticked)
    a.browser.fresh.side_effect = [True, False]
    a.browser.observe.return_value = ticked
    script(monkeypatch, out("ASK", message="Which payment method: Cash or UPI?"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert [m["text"] for m in a.asked] == ["Which payment method: Cash or UPI?"]


CASH = {"id": "e4", "kind": "click", "label": "Cash", "role": "button", "node": 4, "frame": "F1"}
SAVE = {"id": "e6", "kind": "click", "label": "Save", "role": "button", "node": 6, "frame": "F1"}


def test_a_quoted_label_is_matched_exactly_without_asking_jev(tmp_path, monkeypatch):
    from assistant import driver

    a = mini_with(tmp_path, page=fake_page(text="Payment Methods\nCash\nSave", actions=[CASH, SAVE, WAIT]))
    script(monkeypatch, out("CLICK", 'button "Save"'))
    jev_fake(monkeypatch)
    monkeypatch.setattr(driver.jev, "ground", lambda *a: (_ for _ in ()).throw(AssertionError("Jev asked")))
    a.command("tick")
    assert a.browser.act.call_args.args[:2] == ("CLICK", SAVE)


def test_a_quoted_label_that_is_not_clickable_is_sent_back_not_guessed(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Request Go Non AC\nPayment Methods\nCash\nSave",
                                           actions=[CASH, SAVE, WAIT]))
    script(monkeypatch, out("CLICK", 'button "Request Go Non AC"', commits=True, message="Go Non AC · Cash"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_count == 0 and a.asked == []
    assert "covers it" in a.state["feedback"][0]


def test_mini_never_asks_for_its_own_confirmation(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Uber Go AC ₹784.85\nRequest Uber Go AC", actions=[GO, WAIT]))
    script(monkeypatch, out("ASK", message="Please confirm booking details: Uber Go AC, Cash. Confirm?",
                            options=["yes", "no"]),
           out("TELL", message="Do you want to confirm the booking?"))
    jev_fake(monkeypatch)
    a.command("tick")
    a.command("tick")
    assert a.asked == [] and a.told == []
    assert "commits true" in a.state["feedback"][0]


def test_a_question_sent_as_tell_is_asked(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "Terminal 1"}], page=fake_page(text="Where to?", actions=[WAIT]))
    script(monkeypatch, out("TELL", message="Which terminal should I drop you at?"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert [m["text"] for m in a.asked] == ["Which terminal should I drop you at?"]


def test_a_browser_timeout_is_a_hiccup_not_the_end(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Rides", actions=[WAIT]))
    a.browser.fresh.side_effect = TimeoutError("Page.getFrameTree timed out after 5s waiting for the daemon")
    script(monkeypatch, out("WAIT"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.state["status"] == "ready" and "timed out" in a.state["feedback"][-1]


def test_the_payment_method_is_always_the_users_choice(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "yes"}],
                  page=fake_page(text="Uber Go AC ₹785.93\nCash\nRequest Uber Go AC", actions=[GO, WAIT]))
    script(monkeypatch, out("CLICK", "Request Uber Go AC", commits=True, message="Uber Go AC ₹785.93 to Terminal 1"),
           out("CLICK", "Request Uber Go AC", commits=True, message="Uber Go AC ₹785.93 to Terminal 1, Cash"))
    jev_fake(monkeypatch, approvals=["yes"])
    a.command("tick")
    assert a.asked == [] and "payment method" in a.state["feedback"][0]
    a.state["chat"] += [{"from": "agent", "text": "Which payment method: Cash or UPI?"},
                        {"from": "user", "text": "“Cash”"}]
    a.command("tick")
    assert a.asked and a.asked[0]["approval"] is True
