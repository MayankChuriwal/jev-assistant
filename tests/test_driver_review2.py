"""Second review of the live-run fixes, each finding pinned: codes stay secret through every question, commits are
judged by a control's own name, the user's choices (payment, place, delegation) are never widened or guessed, and
approvals show what the page will really do."""

from fakes import fake_page, jev_fake, mini_with, out, script

from assistant import driver, rules

WAIT = {"id": "wait", "kind": "wait", "label": "Wait", "node": None}
OTP = {"id": "e3", "kind": "fill", "label": "Enter the code", "role": "textbox", "value": "", "node": 3,
       "group": [3, 4, 5, 6, 7, 8], "constraints": {"inputmode": "numeric", "maxlength": 1}}
PAID = [{"from": "agent", "text": "Which payment method would you like to use?"}, {"from": "user", "text": "“Cash”"}]


def test_a_new_code_asked_after_a_rejected_one_stays_secret(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "482193"}, {"text": "771204"}],
                  page=fake_page(text="Enter the code we texted you", actions=[OTP, WAIT]))
    script(monkeypatch, out("ASK", message="Please enter the 6-digit OTP sent to your phone."),
           out("ASK", message="The OTP was incorrect. Please enter the new 6-digit OTP sent to your phone."))
    jev_fake(monkeypatch)
    a.command("tick")
    a.command("tick")
    assert [m["kind"] for m in a.asked] == ["secret", "secret"] and "771204" not in str(a.state["chat"])


def test_being_stuck_on_a_code_page_asks_for_a_secret_and_keeps_the_question(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "482193"}],
                  page=fake_page(text="Enter the code we texted you", actions=[OTP, WAIT]))
    a.stuck()
    assert a.asked[0]["kind"] == "secret" and "482193" not in str(a.state["chat"])
    assert a.state["chat"][-2]["from"] == "agent" and a.state["chat"][-2]["text"].startswith("I'm stuck")


def test_a_commit_is_judged_by_the_controls_own_name():
    place = {"kind": "click", "label": "Place order (shows “Proceed to Pay 120”)", "name": "Place order",
             "shown": "Proceed to Pay 120"}
    add = {"kind": "click", "label": "ADD (Buy 1 Get 1 Free Margherita 170 ADD)", "name": "ADD"}
    page = {"text": ""}
    assert rules.commit_label("CLICK", place, page) == "Place order"
    assert rules.commit_label("CLICK", add, page) is None


def test_a_dialog_that_proceeds_to_pay_still_commits():
    page = {"text": "Browser confirm dialog: Proceed to pay ₹1,299 with your saved card?"}
    assert rules.commit_label("ACCEPT_DIALOG", {"label": "OK"}, page)


def test_a_choice_left_to_mini_does_not_spread_to_payment_place_or_other_topics(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Margherita Pizza\nSize", actions=[WAIT]))
    a.state["chat"] += [
        {"from": "agent", "text": "Which pizza restaurant would you like, for the Margherita pizza?"},
        {"from": "user", "text": "“you choose” → the user leaves this choice to you: pick the option that best fits "
                                 "the goal, TELL them which, and carry on"}]
    a.state["history"].append({"step": 1, "operation": "CLICK", "action": "Search", "url": "https://s.test/"})
    script(monkeypatch, out("ASK", message="Which pizza size would you like?", options=["Regular", "Medium"]),
           out("ASK", message="How would you like to pay for the Margherita pizza?", options=["Cash", "UPI"]))
    jev_fake(monkeypatch, replies=["1", "1"])
    a.command("tick")
    a.command("tick")
    assert len(a.asked) == 2


def test_any_or_whatever_inside_a_real_answer_is_not_a_delegation(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "I don't want any toppings"}],
                  page=fake_page(text="Toppings", actions=[WAIT]))
    script(monkeypatch, out("ASK", message="Which toppings would you like?", options=["Olives", "Onion"]))
    jev_fake(monkeypatch, replies=["other"])
    a.command("tick")
    assert "leaves this choice to you" not in a.state["chat"][-1]["text"]


def test_a_non_place_question_mentioning_an_area_can_still_be_left_to_mini(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "you choose"}], page=fake_page(text="Outlets", actions=[WAIT]))
    script(monkeypatch, out("ASK", message="Which Domino's outlet in your area should I order from?",
                            options=["Indiranagar", "Koramangala"]))
    jev_fake(monkeypatch, replies=["other"])
    a.command("tick")
    assert "leaves this choice to you" in a.state["chat"][-1]["text"]


def test_a_payment_is_asked_only_by_a_question_the_user_answered(tmp_path, monkeypatch):
    pay = {"id": "e1", "kind": "click", "label": "Place order", "role": "button", "node": 1}
    a = mini_with(tmp_path, page=fake_page(text="UPI\nCash\nTo pay ₹79\nPlace order", actions=[pay, WAIT]))
    a.state["chat"] += [{"from": "agent", "text": "Heading to payment now."}]
    script(monkeypatch, out("CLICK", "Place order", commits=True, message="Diet Coke · home · Cash · ₹79"))
    jev_fake(monkeypatch, approvals=["no"])
    a.command("tick")
    assert a.asked == [] and "payment method" in a.state["feedback"][0]


def test_payment_words_in_the_elements_count_when_the_text_has_none(tmp_path, monkeypatch):
    pay = {"id": "e1", "kind": "click", "label": "Place order", "role": "button", "node": 1}
    upi = {"id": "e2", "kind": "click", "label": "UPI", "role": "radio", "node": 2}
    a = mini_with(tmp_path, page=fake_page(text="Checkout\nTo pay ₹79\nPlace order", actions=[pay, upi, WAIT]))
    script(monkeypatch, out("CLICK", "Place order", commits=True, message="Diet Coke · home · UPI · ₹79"))
    jev_fake(monkeypatch, approvals=["no"])
    a.command("tick")
    assert a.asked == [] and "payment method" in a.state["feedback"][0]


def test_the_payment_choice_is_read_from_the_payment_question_not_an_approval(tmp_path, monkeypatch):
    upi = {"id": "e2", "kind": "click", "label": "UPI", "role": "radio", "node": 2}
    a = mini_with(tmp_path, page=fake_page(text="Pay with\nUPI\nCash", actions=[upi, WAIT]))
    a.state["chat"] += [{"from": "agent", "text": "Which payment method would you like to use?"},
                        {"from": "user", "text": "“UPI”"},
                        {"from": "agent", "text": "Diet Coke · UPI · ₹79\n\nI'll press “Pay ₹79” on shop.test. Reply "
                                                  "yes to go ahead, or tell me what to change."},
                        {"from": "user", "text": "“yes”"}]
    script(monkeypatch, out("CLICK", "UPI"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_args.args[1]["id"] == "e2"


def test_the_total_is_what_to_pay_not_the_savings(tmp_path):
    a = mini_with(tmp_path, page=fake_page(text="x", actions=[WAIT]))
    page = fake_page(text="Item total ₹99\nTo pay ₹79\nYour total savings ₹20", actions=[WAIT])
    assert "Total: ₹79" in a.page_summary(page)


def test_a_quoted_text_that_is_only_another_controls_context_is_not_clicked(tmp_path, monkeypatch):
    add = {"id": "e1", "kind": "click", "label": "ADD (Margherita Pizza ₹170)", "name": "ADD", "role": "button",
           "node": 1}
    a = mini_with(tmp_path, page=fake_page(text="Margherita Pizza ₹170\nADD", actions=[add, WAIT]))
    script(monkeypatch, out("CLICK", '"Margherita Pizza"'))
    jev_fake(monkeypatch)
    monkeypatch.setattr(driver.jev, "ground", lambda *a: (None, 0.1, [], None))
    monkeypatch.setattr(a, "look", lambda page, purpose: None)
    a.command("tick")
    assert a.browser.act.call_count == 0


def test_a_captcha_question_reaches_the_relay(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Please verify you are human. I'm not a robot", actions=[WAIT]))
    relayed = []
    monkeypatch.setattr(a, "relay_captcha", lambda page: relayed.append(page))
    monkeypatch.setattr(driver.rules, "captcha_challenge", lambda page: True)
    script(monkeypatch, out("ASK", message="Please solve the captcha: click 'I am not a robot'."))
    jev_fake(monkeypatch)
    a.command("tick")
    assert len(relayed) == 1


def test_a_question_that_only_mentions_a_phone_is_asked(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "black"}], page=fake_page(text="Phone cases", actions=[WAIT]))
    script(monkeypatch, out("ASK", message="Which phone case colour would you like?", options=["Black", "Blue"]))
    jev_fake(monkeypatch, replies=["1"])
    a.command("tick")
    assert len(a.asked) == 1


def test_an_order_goal_is_never_done_with_a_caution_when_nothing_was_committed(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Your order is placed", actions=[WAIT]))
    report = out("DONE", message="Your order is placed.", text="Your order is placed")
    script(monkeypatch, report, report, report)
    jev_fake(monkeypatch, achieved="no")
    for _ in range(3):
        a.command("tick")
    assert a.state["status"] != "done"


def test_an_open_question_that_mentions_booking_is_asked(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "the airport"}], page=fake_page(text="Where to?", actions=[WAIT]))
    script(monkeypatch, out("ASK", message="Where should I book the cab to?"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert len(a.asked) == 1


def test_a_upi_pin_is_never_asked_for(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Enter UPI PIN", actions=[WAIT]))
    a.state["chat"] += PAID
    script(monkeypatch, out("ASK", message="Please share your UPI PIN to complete the payment."))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == [] and "UPI app" in a.state["feedback"][0]


def test_a_hover_found_by_sight_is_never_a_commit(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Buy now", actions=[WAIT]))
    look = {"id": "look", "kind": "look", "node": None, "label": "Buy now", "point": [1, 2], "hover": True}
    assert a.commit_label("LOOK", look, out("HOVER", "Buy now")["action"], a.state["page"], {}) is None


def test_an_error_word_in_a_long_page_is_not_an_error_page():
    long = "Welcome to Swiggy\nCall 1800-404-1234 for help\n" + "Restaurants near you\n" * 200
    assert not rules.error_page({"title": "Swiggy", "text": long})
    assert rules.error_page({"title": "404: This page could not be found.", "text": "Go to Home"})


def test_a_refused_confirmation_names_the_final_control_to_press(tmp_path, monkeypatch):
    go = {"id": "e1", "kind": "click", "label": "Request Uber Go AC", "role": "button", "node": 1}
    a = mini_with(tmp_path, page=fake_page(text="Uber Go AC ₹790\nRequest Uber Go AC", actions=[go, WAIT]))
    script(monkeypatch, out("ASK", message="Please confirm your booking details.", options=["Yes", "No"]))
    jev_fake(monkeypatch)
    a.command("tick")
    assert "“Request Uber Go AC”" in a.state["feedback"][0]


def test_a_round_trip_price_is_not_the_answer_to_a_one_way_question(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="IndiGo 11:30 PM ₹16,609 round trip", actions=[WAIT]))
    a.state["goal"] = "Find the cheapest flight from Bengaluru to Delhi next Friday"
    script(monkeypatch, out("DONE", message="IndiGo at 11:30 PM for ₹16,609 round trip.",
                            text="IndiGo 11:30 PM ₹16,609 round trip"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.state["status"] != "done" and "one-way" in a.state["feedback"][0]


def test_a_refused_done_never_invites_telling_the_answer(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Air India 10:00 PM ₹8,200", actions=[WAIT]))
    a.state["goal"] = "Find the cheapest flight from Bengaluru to Delhi next Friday"
    script(monkeypatch, out("DONE", message="Air India at 10:00 PM for ₹8,200.", text="Air India 10:00 PM ₹8,200"))
    jev_fake(monkeypatch, achieved="no")
    a.command("tick")
    assert "TELL" not in a.state["feedback"][0].split("never")[0]


UPI = {"id": "e5", "kind": "click", "label": "UPI Scan and Pay", "name": "UPI Scan and Pay", "role": "button",
       "node": 5}
CASH = {"id": "e4", "kind": "click", "label": "Cash", "name": "Cash", "role": "button", "node": 4}
SAVE = {"id": "e6", "kind": "click", "label": "Save", "name": "Save", "role": "button", "node": 6}


def test_a_payment_method_named_with_pay_is_just_chosen_when_jev_calls_it_a_step(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Payment methods\nCash\nUPI Scan and Pay\nSave",
                                           actions=[CASH, UPI, SAVE, WAIT]))
    a.state["chat"] += [{"from": "agent", "text": "Which payment method do you want to use?"},
                        {"from": "user", "text": "“UPI”"}]
    script(monkeypatch, out("CLICK", "UPI Scan and Pay"))
    jev_fake(monkeypatch, final=("step", 0.95))
    a.command("tick")
    assert a.asked == [] and a.browser.act.call_args.args[1]["id"] == "e5"


def test_a_pay_button_with_an_amount_always_needs_the_approval(tmp_path, monkeypatch):
    pay = {"id": "e7", "kind": "click", "label": "Pay ₹79", "name": "Pay ₹79", "role": "button", "node": 7}
    a = mini_with(tmp_path, replies=[{"text": "no"}], page=fake_page(text="To pay ₹79\nPay ₹79", actions=[pay, WAIT]))
    a.state["chat"] += PAID
    script(monkeypatch, out("CLICK", "Pay ₹79", commits=True, message="Diet Coke · home · Cash · ₹79"))
    jev_fake(monkeypatch, final=("step", 0.95), approvals=["no"])
    a.command("tick")
    assert a.asked and "I'll press “Pay ₹79”" in a.asked[0]["text"]


def test_a_press_that_turned_out_to_be_a_step_lets_the_task_go_on(tmp_path, monkeypatch):
    go = {"id": "e8", "kind": "click", "label": "Request Uber Go AC", "role": "button", "node": 8}
    a = mini_with(tmp_path, page=fake_page(text="Cash\nUPI Scan and Pay\nRequest Uber Go AC", actions=[go, WAIT]))
    a.state["commit"] = {"label": "UPI Scan and Pay", "checks": 0, "since": 0}
    monkeypatch.setattr(driver.jev, "check", lambda *a, **k: "next_step")
    a.watch({"next_goal": "choose UPI"})
    assert a.state["status"] != "blocked" and a.state["commit"] is None
    assert "only a step" in a.state["memory"][-1]


def test_an_approval_without_the_address_gets_the_sites_own_address_line(tmp_path, monkeypatch):
    cod = {"id": "e9", "kind": "click", "label": "Cash Pay cash at the time of delivery.", "role": "button", "node": 9}
    a = mini_with(tmp_path, replies=[{"text": "no"}],
                  page=fake_page(text="Pay on delivery\n1 item. Total: ₹156\nCash/Pay on Delivery",
                                 actions=[cod, WAIT]))
    a.state["chat"] += [{"from": "agent", "text": "Where should I deliver?\n1. home 1601, AECS Layout\n2. Here"},
                        {"from": "user", "text": "“1” → option 1: home 1601, AECS Layout"}, *PAID]
    a.state["recent"] = ["Delivering to: home 1601, AECS Layout, Bengaluru\nPayment Options"]
    script(monkeypatch, out("CLICK", "Cash Pay cash at the time of delivery.", commits=True,
                            message="1 Diet Coke 300 ml can · Pay on Delivery · ₹156"))
    jev_fake(monkeypatch, approvals=["no"])
    a.command("tick")
    assert "Delivering to: home 1601, AECS Layout, Bengaluru" in a.asked[0]["text"]


def test_other_items_in_the_cart_are_never_removed_without_asking(tmp_path, monkeypatch):
    pay = {"id": "e1", "kind": "click", "label": "Proceed to Buy", "role": "button", "node": 1}
    place = {"id": "e2", "kind": "click", "label": "Place your order", "role": "button", "node": 2}
    a = mini_with(tmp_path, page=fake_page(text="Cart\nboAt Airdopes 141 ₹999\nboAt Airdopes 141 ANC ₹1,499\n"
                                                "Order total ₹2,498\nPlace your order", actions=[pay, place, WAIT]))
    a.state["chat"] += PAID
    script(monkeypatch, out("CLICK", "Place your order", commits=True,
                            message="boAt Airdopes 141 · home · Cash · ₹2,498"))
    jev_fake(monkeypatch, matches="no")
    a.command("tick")
    assert "Buy Now" in a.state["feedback"][0] and "ASK" in a.state["feedback"][0]


def test_a_price_is_never_sent_as_an_update_for_a_question_goal(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Akasa Air 5:20 PM ₹16,228", actions=[WAIT]))
    a.state["goal"] = "Find the cheapest flight from Bengaluru to Delhi next Friday"
    script(monkeypatch, out("TELL", message="The cheapest flight is Akasa Air at 5:20 PM for ₹16,228."), out("WAIT"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert "DONE" in a.state["feedback"][0]
    a.command("tick")
    assert a.told == []


def test_a_code_goes_only_into_a_code_or_password_field(tmp_path, monkeypatch):
    phone = {"id": "e1", "kind": "fill", "label": "Enter Phone Number", "role": "textbox", "value": "", "node": 1,
             "constraints": {"type": "tel"}}
    a = mini_with(tmp_path, page=fake_page(text="OTP Verification\nEnter Phone Number", actions=[phone, WAIT]))
    a.keep_secret("482193")
    script(monkeypatch, out("TYPE", "Enter Phone Number", text="[secret:answer1]"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_count == 0 and "code field" in a.state["feedback"][0]


def test_a_one_time_code_input_counts_as_a_code_field():
    otp = {"kind": "fill", "label": "textbox in “Verify your number”", "constraints": {"autocomplete": "one-time-code"}}
    assert rules.secret_field(otp)


def test_the_user_is_never_asked_about_input_boxes(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="OTP Verification", actions=[WAIT]))
    script(monkeypatch, out("ASK", message="Please describe exactly the OTP input box or its label so I can find it."))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == [] and "can't see the page" in a.state["feedback"][0]


def test_an_approval_never_repeats_a_part(tmp_path, monkeypatch):
    pay = {"id": "e2", "kind": "click", "label": "Pay 121", "role": "button", "node": 2}
    a = mini_with(tmp_path, replies=[{"text": "no"}],
                  page=fake_page(text="To pay ₹121\nPay 121", actions=[pay, WAIT]))
    a.state["chat"] += PAID
    script(monkeypatch, out("CLICK", "Pay 121", commits=True,
                            message="Diet Coke 330 ml · home · HDFC Bank Card · HDFC Bank Card · ₹121"))
    jev_fake(monkeypatch, approvals=["no"])
    a.command("tick")
    assert a.asked[0]["text"].count("HDFC Bank Card") == 1


def test_after_three_code_requests_the_task_stops_instead_of_sending_more_codes(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "111111"}, {"text": "222222"}, {"text": "333333"}],
                  page=fake_page(text="OTP Verification\nEnter the code", actions=[OTP, WAIT]))
    script(monkeypatch, *[out("ASK", message=f"Please enter the OTP sent to your phone ({n}).") for n in range(4)])
    jev_fake(monkeypatch)
    for _ in range(4):
        a.command("tick")
    assert len(a.asked) == 3 and a.state["status"] == "blocked" and "codes" in a.told[-1]["text"]


GO_AC = {"id": "e1", "kind": "click", "label": "Uber Go AC 4 2 mins away ₹811.47 ₹954.67", "role": "option",
         "node": 1}
PREMIER_AC = {"id": "e2", "kind": "click", "label": "Premier AC 4 1 min away ₹1,041.51 ₹1,225.31", "role": "option",
              "node": 2}
REQUEST = {"id": "e3", "kind": "click", "label": "Request Uber Go AC", "role": "button", "node": 3}
RIDE_PAGE = "Uber Go AC\n₹811.47\nPremier AC\n₹1,041.51\nCash\nRequest Uber Go AC"


def test_a_choice_between_priced_options_is_the_users_before_the_approval(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text=RIDE_PAGE, actions=[GO_AC, PREMIER_AC, REQUEST, WAIT]))
    a.state["chat"] += PAID
    script(monkeypatch, out("CLICK", "Request Uber Go AC", commits=True,
                            message="Uber Go AC · home → airport · Cash · ₹811.47"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == [] and "price" in a.state["feedback"][0] and "Premier AC" in a.state["feedback"][0]


def test_once_the_user_picked_an_option_the_approval_goes_ahead(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "no"}],
                  page=fake_page(text=RIDE_PAGE, actions=[GO_AC, PREMIER_AC, REQUEST, WAIT]))
    a.state["chat"] += [{"from": "agent", "text": "Which ride?\n1. Uber Go AC ₹811\n2. Premier AC ₹1,041"},
                        {"from": "user", "text": "“1” → option 1: Uber Go AC ₹811"}, *PAID]
    script(monkeypatch, out("CLICK", "Request Uber Go AC", commits=True,
                            message="Uber Go AC · home → airport · Cash · ₹811.47"))
    jev_fake(monkeypatch, approvals=["no"])
    a.command("tick")
    assert a.asked and "Request Uber Go AC" in a.asked[0]["text"], a.state["feedback"]


def test_a_delivery_address_is_asked_before_the_approval(tmp_path, monkeypatch):
    pay = {"id": "e4", "kind": "click", "label": "Place order", "role": "button", "node": 4}
    a = mini_with(tmp_path, page=fake_page(text="Delivering to: home 1601, AECS Layout\nTo pay ₹121\nPlace order",
                                           actions=[pay, WAIT]))
    a.state["chat"] += PAID
    script(monkeypatch, out("CLICK", "Place order", commits=True, message="Diet Coke · home · Cash · ₹121"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == [] and "where to deliver" in a.state["feedback"][0]
