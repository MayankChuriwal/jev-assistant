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
    script(monkeypatch, out("CLICK", "Request Uber Go AC", commits=True, message="Uber Go AC · Terminal 1 · ₹785.93"),
           out("CLICK", "Request Uber Go AC", commits=True, message="Uber Go AC · Terminal 1 · Cash · ₹785.93"))
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
           out("TELL", message="Cheapest: Air India 10:00 PM ₹8,200."), out("WAIT"))
    jev_fake(monkeypatch)
    a.command("tick")
    a.command("tick")
    assert "already told" in a.state["feedback"][0]
    a.command("tick")
    assert [m["text"] for m in a.told] == ["Cheapest: Air India 10:00 PM ₹8,200."]


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
           out("TELL", message="Actually the cheapest is Akasa ₹16,613."), out("WAIT"))
    jev_fake(monkeypatch)
    a.command("tick")
    a.command("tick")
    assert "DONE" in a.state["feedback"][0]
    a.command("tick")
    assert len(a.told) == 1


def test_done_finds_its_own_evidence_when_mini_paraphrases(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Top flights\nIndiGo\n1:00 AM – 3:50 AM\n₹16,976 round trip",
                                           actions=[WAIT]))
    script(monkeypatch, out("DONE", message="Cheapest: IndiGo at 1:00 AM for ₹16,976.",
                            text="Cheapest flight is IndiGo at 1:00 AM costing ₹16,976"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.state["status"] == "done" and a.told[-1]["evidence"] == "₹16,976 round trip"


def test_an_empty_summary_is_written_from_the_form_itself(tmp_path, monkeypatch):
    form = [{"id": "e1", "kind": "fill", "label": "Customer name:", "role": "textbox", "value": "Test User", "node": 1},
            {"id": "e2", "kind": "click", "label": "Medium", "role": "radio", "checked": True, "node": 2},
            {"id": "e3", "kind": "click", "label": "Small", "role": "radio", "checked": False, "node": 3},
            {"id": "e4", "kind": "click", "label": "Bacon", "role": "checkbox", "checked": True, "node": 4},
            {"id": "e5", "kind": "fill", "label": "CVV", "role": "textbox", "value": "[secret]", "node": 5},
            {"id": "e6", "kind": "click", "label": "Submit order", "role": "button", "node": 6}]
    from assistant import driver

    a = mini_with(tmp_path, replies=[{"text": "no"}], page=fake_page(text="Pizza order", actions=[*form, WAIT]))
    monkeypatch.setattr(driver.mind, "summarize", lambda payload: (
        {"what": payload["form"], "where": "", "option": "", "payment": "", "total": ""}, {}))
    script(monkeypatch, out("CLICK", "Submit order", commits=True, message=""))
    jev_fake(monkeypatch, approvals=["no"])
    a.command("tick")
    text = a.asked[0]["text"]
    assert "Customer name: Test User" in text and "Medium" in text and "Bacon" in text
    assert "Small" not in text and "secret" not in text and "“Submit order”" in text


def test_back_on_a_new_tab_with_no_history_closes_it(tmp_path, monkeypatch):
    tabs = [{"id": "tab1", "kind": "tab", "node": None, "tab": "T1", "current": False, "label": "Tab: Search results"},
            {"id": "tab2", "kind": "tab", "node": None, "tab": "T2", "current": True, "label": "Tab: Nike Air Max 90"}]
    a = mini_with(tmp_path, page=fake_page(text="Nike Air Max 90", actions=[*tabs, WAIT]))
    script(monkeypatch, out("BACK"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_args.args[:2] == ("CLOSE_TAB", tabs[1])


def test_evidence_seen_on_an_earlier_page_counts(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Nike Air Max\nNike Air Max 90 Rs. 3398\nNike Air Max 270 Rs. 5999",
                                           url="https://shop.test/search", actions=[WAIT]))
    product = fake_page(text="Nike Air Max 90 LV8\nRs. 12995", url="https://shop.test/p/1", actions=[WAIT])
    script(monkeypatch, out("WAIT"), out("DONE", message="The cheapest Nike Air Max is Rs. 3398.", text="cheapest"))
    jev_fake(monkeypatch)
    a.command("tick")  # the search results are read before the step
    a.state["page"] = a.browser.observe.return_value = product
    a.command("tick")
    assert a.state["status"] == "done" and a.told[-1]["evidence"] == "Nike Air Max 90 Rs. 3398"


def test_a_confirmation_with_a_yes_or_no_plus_words_option_is_still_refused(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Uber Go AC ₹784.30", actions=[WAIT]))
    script(monkeypatch, out("ASK", message="Shall I proceed to book this ride for you?",
                            options=["Yes", "No, choose a different ride"]))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == [] and "commits true" in a.state["feedback"][0]


def test_an_empty_summary_is_written_by_a_focused_summary_call(tmp_path, monkeypatch):
    from assistant import driver

    pay = {"id": "e7", "kind": "click", "label": "Place Order", "role": "button", "node": 7}
    a = mini_with(tmp_path, replies=[{"text": "no"}],
                  page=fake_page(text="Diet Coke 300 ml\nDelivering to Home\nPay on Delivery\nTotal ₹121",
                                 actions=[pay, WAIT]))
    a.state["chat"] += [{"from": "agent", "text": "Which payment method?"},
                        {"from": "user", "text": "“Pay on delivery”"}]
    seen = []
    monkeypatch.setattr(driver.mind, "summarize", lambda payload: seen.append(payload) or (
        {"what": "1 × Diet Coke 300 ml", "where": "Home", "option": "", "payment": "Pay on Delivery",
         "total": "₹121"}, {}))
    script(monkeypatch, out("CLICK", "Place Order", commits=True, message=""))
    jev_fake(monkeypatch, approvals=["no"])
    a.command("tick")
    assert a.asked[0]["text"].startswith("1 × Diet Coke 300 ml · Home · Pay on Delivery · ₹121")
    assert seen and seen[0]["page"]["text"].startswith("Diet Coke")


def test_double_escaped_entities_are_fully_unescaped(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "Wallets"}], page=fake_page(text="Pay", actions=[WAIT]))
    script(monkeypatch, out("ASK", message="Which payment method?",
                            options=["Wallets (PhonePe, Amazon Pay &amp;amp; more)"]))
    jev_fake(monkeypatch, replies=["1"])
    a.command("tick")
    assert "Amazon Pay & more" in a.asked[0]["text"]


def test_a_summary_without_the_price_the_page_shows_is_rewritten(tmp_path, monkeypatch):
    from assistant import driver

    req = {"id": "e8", "kind": "click", "label": "Request Go Non AC", "role": "button", "node": 8}
    a = mini_with(tmp_path, replies=[{"text": "no"}],
                  page=fake_page(text="Go Non AC ₹776.05\nCash\nRequest Go Non AC", actions=[req, WAIT]))
    a.state["chat"] += [{"from": "agent", "text": "Which payment method?"}, {"from": "user", "text": "“Cash”"}]
    monkeypatch.setattr(driver.mind, "summarize", lambda payload: (
        {"what": "Go Non AC ride", "where": "Sunnyside → Terminal 1", "option": "", "payment": "Cash",
         "total": "₹776.05"}, {}))
    script(monkeypatch, out("CLICK", "Request Go Non AC", commits=True,
                            message="Booking Go Non AC from Sunnyside to Terminal 1 with cash."))
    jev_fake(monkeypatch, approvals=["no"])
    a.command("tick")
    assert "₹776.05" in a.asked[0]["text"]


def test_telling_the_user_to_act_on_the_page_is_refused(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Swipe right to add item to cart", actions=[WAIT]))
    script(monkeypatch, out("TELL", message="I need you to swipe right on the Margherita pizza to add it. Tell me when "
                                            "you've done that."))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.told == [] and "can't see the page" in a.state["feedback"][0]


def test_after_the_user_leaves_a_choice_to_mini_it_does_not_ask_again(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "You choose whatever fits my goal best."}],
                  page=fake_page(text="Domino's is closed", actions=[WAIT]))
    script(monkeypatch, out("ASK", message="Order from another restaurant?", options=["La Pino'z", "Pizza House"]),
           out("ASK", message="Which restaurant would you like instead?", options=["La Pino'z", "Pizza House"]))
    jev_fake(monkeypatch, replies=["other"])
    a.command("tick")
    a.command("tick")
    assert len(a.asked) == 1 and "left this choice to you" in a.state["feedback"][0]


def test_a_note_about_the_user_is_not_sent_to_them(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Domino's Pizza is closed", actions=[WAIT]))
    script(monkeypatch, out("TELL", message="Domino's Pizza is closed. Ask user for next preference."))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.told == [] and "speak to them" in a.state["feedback"][0]


def test_a_summary_that_lists_no_details_is_rewritten(tmp_path, monkeypatch):
    from assistant import driver

    size = {"id": "e9", "kind": "click", "label": "Medium", "role": "radio", "node": 9, "checked": True}
    submit = {"id": "e10", "kind": "click", "label": "Submit order", "role": "button", "node": 10}
    a = mini_with(tmp_path, replies=[{"text": "no"}],
                  page=fake_page(text="Pizza size\nMedium\nSubmit order", actions=[size, submit, WAIT]))
    monkeypatch.setattr(driver.mind, "summarize", lambda payload: (
        {"what": "Pizza order", "where": "", "option": "Medium", "payment": "", "total": ""}, {}))
    script(monkeypatch, out("CLICK", "Submit order", commits=True, message="Submitting the pizza order form now."))
    jev_fake(monkeypatch, approvals=["no"])
    a.command("tick")
    assert "Medium" in a.asked[0]["text"]


def test_stopping_because_the_site_does_not_serve_the_shown_location_is_sent_back_once(tmp_path, monkeypatch):
    place = {"id": "e11", "kind": "click", "label": "Other", "role": "button", "node": 11}
    a = mini_with(tmp_path, page=fake_page(text="Other - Ranchi Bypass, Jharkhand\nSit Tight! We're Coming Soon!",
                                           actions=[place, WAIT]))
    script(monkeypatch, out("STOP", message="Zepto service not available now; cannot order here."),
           out("STOP", message="Zepto service not available now; cannot order here."))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.state["status"] != "blocked" and "delivery location" in a.state["feedback"][0]
    a.command("tick")
    assert a.state["status"] == "blocked"


def test_a_step_that_seems_to_do_nothing_is_looked_at_again(tmp_path, monkeypatch):
    opener = {"id": "e12", "kind": "click", "label": "Open Where from?", "role": "combobox", "node": 12}
    before = fake_page(text="Where from?", actions=[opener, WAIT])
    later = fake_page(text="Where from?\nBengaluru, Karnataka", actions=[opener, WAIT])
    a = mini_with(tmp_path, page=before)
    looks = []

    def observe(screenshot=False):
        if not a.browser.act.called:
            return before
        looks.append(1)
        return before if len(looks) == 1 else later  # the dropdown renders a moment after the click

    a.browser.observe.side_effect = observe
    script(monkeypatch, out("CLICK", "Open Where from?"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert "Bengaluru, Karnataka" in a.state["history"][-1]["changed"]


def test_an_answer_told_just_before_done_is_sent_once(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Air India 2:00 PM ₹8,257", actions=[WAIT]))
    script(monkeypatch, out("TELL", message="The cheapest flight is Air India at 2:00 PM for ₹8,252."),
           out("DONE", message="The cheapest flight is Air India at 2:00 PM. The price is ₹8,257.",
               text="Air India 2:00 PM ₹8,257"))
    jev_fake(monkeypatch)
    a.command("tick")
    a.command("tick")
    assert [m["kind"] for m in a.told] == ["result"] and "₹8,257" in a.told[0]["text"]


def test_an_update_goes_out_before_the_next_step(tmp_path, monkeypatch):
    go = {"id": "e1", "kind": "click", "label": "See prices", "role": "button", "node": 1}
    a = mini_with(tmp_path, page=fake_page(text="Flights", actions=[go, WAIT]))
    script(monkeypatch, out("TELL", message="Searching flights for Friday."), out("CLICK", "See prices"))
    jev_fake(monkeypatch)
    a.command("tick")
    a.command("tick")
    assert [m["text"] for m in a.told] == ["Searching flights for Friday."] and a.browser.act.called


def test_the_same_reply_twice_to_one_question_means_stop_asking(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Pay with\nHDFC Bank Card 2479\nAMEX Card 1007", actions=[WAIT]))
    a.state["chat"] += [
        {"from": "agent", "text": "Which payment method would you like to use for paying ₹331?"},
        {"from": "user", "text": "“Cash on delivery”"},
        {"from": "agent", "text": "Cash on delivery is not available here. Would you like to use one of the cards?"},
        {"from": "user", "text": "“Cash on delivery”"}]
    script(monkeypatch, out("ASK", message="Cash on delivery is not available for this order. Please choose one of "
                                           "the listed card payment options.", options=["HDFC Bank Card 2479"]))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == [] and "same answer twice" in a.state["feedback"][0]


def test_a_detail_the_profile_holds_is_typed_not_asked(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Login\nEnter your mobile number", actions=[WAIT]))
    script(monkeypatch, out("ASK", message="Please provide your mobile number to log in."))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == [] and "[profile:phone]" in a.state["feedback"][0]


def test_the_summary_facts_give_a_total_only_where_the_page_labels_one(tmp_path):
    rides_page = fake_page(text="Uber Go AC ₹792.28-₹932.09\nGo Non AC ₹777.46-₹914.66\n"
                                "Premier AC ₹1,020.02-₹1,200.02\nCash\nRequest Go Non AC", actions=[WAIT])
    cart = fake_page(text="Diet Coke 300 ml ₹40\nItem total ₹40\nDelivery fee ₹30\nTo pay\n₹79\nPlace order",
                     actions=[WAIT])
    a = mini_with(tmp_path, page=rides_page)
    assert "₹1,200.02" not in a.page_summary(rides_page)
    assert "Total: ₹79" in a.page_summary(cart)


def test_an_answer_told_twice_then_done_is_sent_once(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Air India 2:00 PM ₹8,257", actions=[WAIT]))
    answer = "The cheapest flight is Air India at 2:00 PM for ₹8,257."
    script(monkeypatch, out("TELL", message=answer), out("TELL", message=answer),
           out("DONE", message=answer, text="Air India 2:00 PM ₹8,257"))
    jev_fake(monkeypatch)
    for _ in range(3):
        a.command("tick")
    assert [m["kind"] for m in a.told] == ["result"]


def test_a_payment_option_must_be_the_one_the_user_chose(tmp_path, monkeypatch):
    card = {"id": "e13", "kind": "click", "label": "HDFC Bank Card (1 of 3)", "role": "button", "node": 13}
    upi = {"id": "e14", "kind": "click", "label": "UPI", "role": "button", "node": 14}
    a = mini_with(tmp_path, page=fake_page(text="Payment Options\nHDFC Bank Card\nUPI", actions=[card, upi, WAIT]))
    a.state["chat"] += [{"from": "agent", "text": "Which payment method would you like to use?\n1. HDFC Bank Card"},
                        {"from": "user", "text": "“Cash on delivery if it's offered; otherwise UPI” → none of the "
                                                 "options"}]
    script(monkeypatch, out("CLICK", "HDFC Bank Card (1 of 3)"), out("CLICK", "UPI"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_count == 0 and "chose" in a.state["feedback"][0]
    a.command("tick")
    assert a.browser.act.call_args.args[1]["id"] == "e14"


def test_the_approval_shows_every_value_typed_into_the_form(tmp_path, monkeypatch):
    name = {"id": "e15", "kind": "fill", "label": "Customer name:", "role": "textbox", "value": "Test User", "node": 15}
    search = {"id": "e16", "kind": "fill", "label": "Search", "role": "searchbox", "value": "pizza", "node": 16}
    submit = {"id": "e17", "kind": "click", "label": "Submit order", "role": "button", "node": 17}
    a = mini_with(tmp_path, replies=[{"text": "no"}],
                  page=fake_page(text="Customer name\nSize\nMedium\nSubmit order",
                                 actions=[name, search, submit, WAIT]))
    script(monkeypatch, out("CLICK", "Submit order", commits=True,
                            message="medium pizza with bacon · delivery at 19:00"))
    jev_fake(monkeypatch, approvals=["no"])
    a.command("tick")
    assert "Customer name: Test User" in a.asked[0]["text"] and "pizza ·" not in a.asked[0]["text"]
