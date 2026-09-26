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


def test_a_question_already_answered_is_not_asked_again(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Checkout", actions=[WAIT]))
    a.state["chat"] += [{"from": "agent", "text": "Please provide your delivery address for this order."},
                        {"from": "user", "text": "“The first saved address”"}]
    script(monkeypatch, out("ASK", message="Please provide your delivery address for this order."))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == [] and "The first saved address" in a.state["feedback"][0]


SEARCH = {"id": "e7", "kind": "click", "label": 'Search for "Ice cream" "Chips" "Cold drinks"', "role": "button",
          "node": 7}
PANEL = {"id": "e8", "kind": "scroll_down", "label": "Search results", "role": "region", "node": 8}


def test_an_escaped_quoted_label_still_matches_exactly(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Instamart", actions=[SEARCH, WAIT]))
    script(monkeypatch, out("CLICK", '[2] button "Search for \\"Ice cream\\" \\"Chips\\" \\"Cold drinks\\""'))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_args.args[:2] == ("CLICK", SEARCH)


def test_scrolling_the_page_scrolls_its_panel_when_the_page_itself_does_not_scroll():
    from assistant import jev

    action, confidence, _, _ = jev.ground(fake_page(text="Results", actions=[PANEL, WAIT]), "SCROLL_DOWN", "",
                                          "See more results", "Order a coke")
    assert action == PANEL and confidence == 1.0


def test_a_card_detail_needs_the_payment_method_asked_first(tmp_path, monkeypatch):
    cvv = {"id": "e9", "kind": "fill", "label": "CVV", "role": "textbox", "value": "", "node": 9,
           "constraints": {"type": "password"}}
    a = mini_with(tmp_path, replies=[{"text": "123"}],
                  page=fake_page(text="Preferred Payment\nHDFC Bank Card\nPay on Delivery\nPay 132",
                                 actions=[cvv, WAIT]))
    script(monkeypatch, out("ASK", message="Please provide the CVV for your HDFC Bank Card to proceed with payment."))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == [] and "payment method" in a.state["feedback"][0]


def test_a_question_that_says_proceed_is_not_a_confirmation(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "Terminal 1"}], page=fake_page(text="Where to?", actions=[WAIT]))
    script(monkeypatch, out("ASK", message="Which terminal should I use to proceed with the booking?"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert len(a.asked) == 1


def test_a_step_only_mini_calls_final_is_checked_by_jev(tmp_path, monkeypatch):
    save = {"id": "e6", "kind": "click", "label": "Save", "role": "button", "node": 6}
    panel = "Payment Methods\nCash\nSave"
    a = mini_with(tmp_path, replies=[{"text": "yes"}], page=fake_page(text=panel, actions=[save, WAIT]))
    a.state["chat"] += [{"from": "agent", "text": "Which payment method?"}, {"from": "user", "text": "“Cash”"}]
    script(monkeypatch, out("CLICK", "Save", commits=True, message="Uber Go AC · Cash · ₹819"))
    jev_fake(monkeypatch, final=("step", 0.9))
    a.command("tick")
    assert a.asked == [] and a.browser.act.call_args.args[1]["id"] == "e6"  # a step on the way: no approval
    b = mini_with(tmp_path, replies=[{"text": "no"}], page=fake_page(text=panel, actions=[save, WAIT]))
    b.state["chat"] += [{"from": "agent", "text": "Which payment method?"}, {"from": "user", "text": "“Cash”"}]
    script(monkeypatch, out("CLICK", "Save", commits=True, message="Uber Go AC · Cash"))
    jev_fake(monkeypatch, final=("step", 0.6))
    b.command("tick")
    assert b.asked and b.browser.act.call_count == 0  # not sure it's only a step: the user approves


def test_questions_about_page_elements_are_refused(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Pickup", actions=[WAIT]))
    script(monkeypatch, out("ASK", message="I couldn't find the current location option. Please describe the exact "
                                           "words or element number."))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == [] and "can't see the page" in a.state["feedback"][0]


def test_html_entities_never_reach_the_user(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "Wallets"}], page=fake_page(text="Pay", actions=[WAIT]))
    script(monkeypatch, out("ASK", message="Which payment method?",
                            options=["Wallets (PhonePe, Amazon Pay &amp; more)"]))
    jev_fake(monkeypatch, replies=["1"])
    a.command("tick")
    assert "&amp;" not in a.asked[0]["text"] and "Amazon Pay & more" in a.asked[0]["text"]


def test_a_reply_that_leaves_the_choice_to_mini_says_so(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "You choose whatever fits my goal best."}],
                  page=fake_page(text="Pizza places", actions=[WAIT]))
    script(monkeypatch, out("ASK", message="Please choose a restaurant.", options=["Pizza 4P's", "Thin Tony's"]))
    jev_fake(monkeypatch, replies=["other"])
    a.command("tick")
    assert "leaves this choice to you" in a.state["chat"][-1]["text"]


def test_a_reworded_repeat_of_an_answered_question_is_caught(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Pizza places", actions=[WAIT]))
    a.state["chat"] += [{"from": "agent", "text": "Please choose a restaurant for your Margherita pizza from the "
                                                  "following options."}, {"from": "user", "text": "“You choose”"}]
    script(monkeypatch, out("ASK", message="Please choose a restaurant for your Margherita pizza from these options."))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == []


def test_the_same_message_is_never_sent_twice(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Flights", actions=[WAIT]))
    script(monkeypatch, out("TELL", message="Cheapest: Air India 10:00 PM ₹8,200."),
           out("TELL", message="Cheapest: Air India 10:00 PM ₹8,200."))
    jev_fake(monkeypatch)
    a.command("tick")
    a.command("tick")
    assert [m["text"] for m in a.told] == ["Cheapest: Air India 10:00 PM ₹8,200."]
    assert "already told" in a.state["feedback"][0]


def test_a_report_quoted_from_the_page_finishes_after_two_refusals_with_a_caution(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Air India 10:00 PM ₹8,200", actions=[WAIT]))
    report = out("DONE", message="Cheapest: Air India 10:00 PM ₹8,200.", text="Air India 10:00 PM ₹8,200")
    script(monkeypatch, report, report, report)
    jev_fake(monkeypatch, achieved="no")
    for _ in range(3):
        a.command("tick")
    assert a.state["status"] == "done" and "couldn't fully confirm" in a.told[-1]["text"]


DEPARTURE = {"id": "e3", "kind": "fill", "label": "Departure", "role": "textbox", "value": "", "node": 3}


def test_clicking_a_quoted_text_field_clicks_it(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Flights", actions=[DEPARTURE, WAIT]))
    script(monkeypatch, out("CLICK", '[15] textbox "Departure"'))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_args.args[:2] == ("CLICK", DEPARTURE)


def test_two_updates_in_a_row_are_refused(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Flights", actions=[WAIT]))
    script(monkeypatch, out("TELL", message="Cheapest so far: IndiGo ₹8,207."),
           out("TELL", message="Actually the cheapest is Akasa ₹16,613."))
    jev_fake(monkeypatch)
    a.command("tick")
    a.command("tick")
    assert len(a.told) == 1 and "DONE" in a.state["feedback"][0]


def test_done_finds_its_own_evidence_when_mini_paraphrases(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Top flights\nIndiGo\n1:00 AM – 3:50 AM\n₹16,976 round trip",
                                           actions=[WAIT]))
    script(monkeypatch, out("DONE", message="Cheapest: IndiGo at 1:00 AM for ₹16,976.",
                            text="Cheapest flight is IndiGo at 1:00 AM costing ₹16,976"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.state["status"] == "done" and a.told[-1]["evidence"] == "₹16,976 round trip"
