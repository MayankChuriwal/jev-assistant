"""A voucher job in the worker: a store's read and a purchase, with the page model scripted; money.py and the gate
decide about money. No Chrome, no models."""

import json

import pytest
from fakes import fake_page, jev_checks, worker_with

from assistant import actor, money, planner
from assistant import worker as module

STORE = "https://www.gyftr.com/instantvouchers/swiggy-gv-gift-vouchers"
GOAL = money.voucher_goal("SmartBuy", "Swiggy", 5000, {2000: 2, 1000: 1}, "Infinia", compared="5X Reward Points")
READ = money.read_goal("SmartBuy", "Swiggy", 5000)
CART = "Cart\nSwiggy ₹2,000 × 2\nSwiggy ₹1,000 × 1\nYou Pay ₹5,000\n5X Reward Points\nCheckout"
ORDER = "Thank you\nOrder ID: GV7Q2M44K1\nVoucher Code: ABCD-EFGH-1234-5678\nPIN: 482913"
CODES = ("ABCD-EFGH-1234-5678", "482913")


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(module, "CONFIRM_GAP", 0)
    monkeypatch.setattr(planner, "turn", lambda *a, **k: pytest.fail("the planner was asked"))


def page(text, url=STORE, extra=()):
    return fake_page(url=url, text=text, actions=[
        *extra, {"id": "pay", "kind": "click", "label": "Pay ₹5,000", "role": "button", "node": 9},
        {"id": "wait", "kind": "wait", "label": "Wait", "node": None}])


def answer(actions=(), end="continue", say=None, **facts):
    return {"actions": [{"op": op, "target": target, "text": text} for op, target, text in actions],
            "facts": {name: facts.get(name) for name in money.VOUCHER_FACTS}, "end": end, "say": say, "options": []}


def script(monkeypatch, *answers):
    """The page model's answers in order (the last one repeats). Returns (body, image, kind) for each call."""
    queue, seen = list(answers), []

    def turn(body, image=None, kind="bill"):
        seen.append((body, image, kind))
        return (queue.pop(0) if len(queue) > 1 else queue[0]), {"model": "fake", "latency_ms": 1}

    monkeypatch.setattr(actor, "turn", turn)
    return seen


def paid(w):
    """The user's yes was acted on: the pay control was pressed with it."""
    w.state["history"].append({"operation": "CLICK", "action": "Pay ₹5,000", "committed": True, "approved": "user"})
    w.state.update(approved_pay="5000", offer="5000", checked=True)
    w.browser.fresh.return_value = False  # the page is read again, as the worker reads every page after paying


def test_a_read_returns_the_offers_facts_and_asks_nothing(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=page("Swiggy\n₹500 ₹1,000 ₹2,000\n5X Reward Points\nMonthly limit ₹10,000"),
                    goal=READ)
    seen = script(monkeypatch, answer(end="done", say="Read", amounts="₹500 ₹1,000 ₹2,000",
                                      reward="5X Reward Points", limit="Monthly limit ₹10,000"))
    w.command("tick")
    assert w.state["outcome"] == "read" and w.state["job_facts"]["reward"] == "5X Reward Points"
    assert w.asked == [] and seen[0][2] == "voucher_read"
    assert w.told[-1]["text"] == ("Read SmartBuy: amounts: ₹500 ₹1,000 ₹2,000; reward: 5X Reward Points; limit: "
                                  "Monthly limit ₹10,000.")


@pytest.mark.parametrize("typed", ["[ask]", "[captcha]", "[profile:phone]"])
def test_a_read_never_asks_the_user_anything(tmp_path, monkeypatch, typed):
    box = {"id": "b", "kind": "fill", "label": "Enter 10 - digit mobile number", "role": "textbox", "value": "",
           "node": 1, "constraints": {}}
    w = worker_with(tmp_path, page=page("Sign in to see prices", extra=[box]), goal=READ)
    script(monkeypatch, answer([("type", "1", typed)]))
    w.command("tick")
    assert w.asked == [] and w.state["outcome"] == "unread" and not w.browser.act.called


def test_a_read_never_presses_a_pay_control(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=page("Payable Amount: ₹5,000"), goal=READ)
    script(monkeypatch, answer([("click", "1", None)]))
    w.command("tick")
    assert w.asked == [] and not any(c.args[0] == "CLICK" for c in w.browser.act.call_args_list)


def test_the_yes_waits_for_the_cart_to_match_the_goal(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=page(CART.replace("₹1,000 × 1", "₹1,000 × 3")), goal=GOAL)
    script(monkeypatch, answer([("click", "1", None)], cart="Swiggy ₹2,000 × 2 Swiggy ₹1,000 × 3",
                               payable="₹5,000"))
    w.command("tick")
    assert not any(m.get("approval") for m in w.asked)
    assert any("cart" in f.lower() for f in w.state["feedback"])


def test_the_purchases_one_yes_is_written_in_code(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=page(CART), goal=GOAL, replies=[{"option": "o1"}])
    script(monkeypatch, answer([("click", "1", None)], cart="Swiggy ₹2,000 × 2 Swiggy ₹1,000 × 1", payable="₹5,000",
                               reward="5X Reward Points"))
    w.command("tick")
    approval = next(m for m in w.asked if m.get("approval"))
    assert approval["text"].startswith("Buy ₹5,000 of Swiggy vouchers on SmartBuy\n• 2 × ₹2,000 + 1 × ₹1,000\n"
                                       "• Paying with Infinia\n• Payable: ₹5,000")


def test_the_order_is_the_proof_and_codes_reach_no_model(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=page(ORDER), goal=GOAL)
    paid(w)
    w.state["payment_id"] = w.memory.payment_started("SmartBuy", money.voucher_key("Swiggy"), "5000", "Infinia")
    seen = script(monkeypatch, answer(end="done", say="Order placed", order_id="GV7Q2M44K1"))
    w.command("tick")
    assert w.state["outcome"] == "bought"
    assert w.told[-1]["text"] == ("Bought ₹5,000 of Swiggy vouchers on SmartBuy (2 × ₹2,000 + 1 × ₹1,000). Order "
                                  "GV7Q2M44K1. The codes come by SMS and email.")
    assert w.memory.payments("SmartBuy", "voucher_swiggy")[0]["status"] == "paid"
    sent = json.dumps([body for body, _, _ in seen], ensure_ascii=False) + json.dumps(w.trace(), ensure_ascii=False,
                                                                                      default=str)
    assert not any(code in sent for code in CODES)


def test_no_screenshot_reaches_the_model_after_the_yes(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=page(ORDER), goal=GOAL)
    paid(w)
    w.state["actor_look"] = True
    seen = script(monkeypatch, answer([("wait", None, None)]))
    w.command("tick")
    assert seen[0][1] is None


def test_a_stop_after_the_yes_is_unclear_and_says_not_to_buy_again(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=page("Payment pending"), goal=GOAL)
    paid(w)
    script(monkeypatch, answer(end="stop", say="The bank page timed out."))
    w.command("tick")
    assert w.state["outcome"] == "unclear" and "check your SMS and email" in w.told[-1]["text"]


def test_vouchers_bought_today_ask_before_buying_again(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=page(CART), goal=GOAL, replies=[{"option": "o2"}])
    row = w.memory.payment_started("SmartBuy", money.voucher_key("Swiggy"), "5000", "Infinia")
    w.memory.payment_ended(row, "paid", "GV1")
    script(monkeypatch, answer(cart="Swiggy ₹2,000 × 2 Swiggy ₹1,000 × 1", payable="₹5,000"))
    w.command("tick")
    assert w.state["outcome"] == "bought_before" and "GV1" in w.asked[0]["text"]


def test_a_read_that_would_ask_the_user_stops_unread(tmp_path, monkeypatch):
    # live, Axis Grab Deals: the loop guard's "I'm stuck … What should I do?" went to the user from a read
    w = worker_with(tmp_path, page=page("Swiggy\n₹500 ₹1,000"), goal=READ)
    with pytest.raises(module.Halt):
        w.ask({"kind": "text", "text": "I'm stuck on SmartBuy. What should I do?"})
    assert w.state["outcome"] == "unread" and w.asked == []


def test_a_read_past_its_turns_ends_with_what_it_read(tmp_path, monkeypatch):
    # live, Axis Grab Deals: amounts and discount read by turn 4, then nine turns opening and closing an offer panel
    w = worker_with(tmp_path, page=page("Swiggy\n₹500 ₹1,000\n2% OFF"), goal=READ)
    seen = script(monkeypatch, answer([("wait", None, None)]))
    w.state["job_facts"].update(amounts="₹500 ₹1,000", discount="2% OFF")  # read in earlier turns
    w.state["actor_turns"] = module.READ_TURNS
    w.command("tick")
    assert w.state["outcome"] == "read" and seen == [] and w.told[-1]["text"].startswith("Read SmartBuy: amounts")


def test_a_stores_suggestion_of_the_brands_gift_card_is_no_narrowing(tmp_path):
    # live, SmartBuy: "Zomato Gift Card" was refused as adding "card, gift" to the search, and Enter found nothing
    w = worker_with(tmp_path, page=page("Search"), goal=money.read_goal("SmartBuy", "Zomato", 2000))
    w.state["history"].append({"operation": "TYPE_TEXT", "action": "input search", "text": "Zomato"})
    assert w.narrows({"label": "Zomato Gift Card"}) == set()
    assert w.narrows({"label": "Zomato E-Gift Voucher Zomato E-Gift Voucher Product"}) == set()  # live, Woohoo
    assert w.narrows({"label": "Zomato E-Gift Card Zomato E-Gift Card Product (1 of 2)"}) == set()  # its twin's mark
    # live sweep, Grab Deals: "Amazon Shopping Voucher (0.60% OFF) … 0.6% OFF" was refused too; on a voucher store
    # the page model picks the brand's own voucher (its guide), so no suggestion counts as narrowing
    assert w.narrows({"label": "Amazon Shopping Voucher (0.60% OFF) Amazon Shopping Voucher (0.60% OFF) 0.6% OFF"}) \
        == set()


def test_a_yes_acted_on_by_a_key_starts_the_payment_and_the_masking(tmp_path, monkeypatch):
    # review C1: Enter in the CVV box submits the pay form; the yes was acted on, but no row and no masking followed
    cvv = {"id": "c", "kind": "fill", "label": "CVV", "role": "textbox", "value": "[secret]", "node": 3,
           "constraints": {}, "submit": "Pay ₹5,000"}
    w = worker_with(tmp_path, page=page("Payable Amount: ₹5,000\nCVV", extra=[cvv]), goal=GOAL,
                    replies=[{"option": "o1"}])
    w.state.update(cart_ok=True, checked=True)
    w.state["job_facts"].update(cart="Swiggy ₹2,000 × 2 Swiggy ₹1,000 × 1", payable="₹5,000")
    jev_checks(monkeypatch, after_commit="next_step")
    script(monkeypatch, answer([("key", "1", "Enter")], payable="₹5,000"))
    w.command("tick")  # the yes is asked
    w.command("tick")  # and acted on by the key
    assert any(h.get("committed") and h.get("operation") == "PRESS_KEY" for h in w.state["history"])
    assert w.state["approved_pay"] == "5000" and w.state["payment_id"] is not None and w.masking()


def test_the_masks_placeholder_is_no_order_number(tmp_path, monkeypatch):
    # review I2a: grounded("[code]", "Voucher Code: [code]") was taken for the order's proof
    w = worker_with(tmp_path, page=page("Thank you\nVoucher Code: ABCD-EFGH-1234-5678"), goal=GOAL)
    paid(w)
    w.state["paid_url"] = "https://pay.gyftr.com/"
    script(monkeypatch, answer(end="done", say="Done", order_id="[code]"))
    w.command("tick")
    assert w.state["outcome"] == "unclear"


def test_the_payment_pages_own_order_id_is_no_proof(tmp_path, monkeypatch):
    # review I2b: pay.gyftr.com shows "Order Id" before paying; a failed payment back on it was reported bought
    pay = page("Bill Details\nOrder Id :\n230145678912\nPayable Amount: ₹5,000\nPlease wait",
               url="https://pay.gyftr.com/")
    w = worker_with(tmp_path, page=pay, goal=GOAL)
    paid(w)
    w.state["paid_url"] = "https://pay.gyftr.com/"
    script(monkeypatch, answer(end="done", say="Done", order_id="230145678912"))
    w.command("tick")
    assert w.state["outcome"] == "unclear" and "Bought" not in w.told[-1]["text"]


def test_a_payable_far_above_the_total_gets_no_yes(tmp_path, monkeypatch):
    # review I3: the page's cart held ₹7,000; the model read it as the goal's ₹5,000, and ₹7,000 went out as "fee"
    w = worker_with(tmp_path, page=page(CART.replace("₹2,000 × 2", "₹2,000 × 3").replace("You Pay ₹5,000",
                                                                                         "You Pay ₹7,000")), goal=GOAL)
    script(monkeypatch, answer([("click", "1", None)], cart="Swiggy ₹2,000 × 2 Swiggy ₹1,000 × 1", payable="₹7,000"))
    w.command("tick")
    assert not any(m.get("approval") for m in w.asked)
    assert any("₹2,000 more than" in f for f in w.state["feedback"])


def test_a_purchase_never_settled_on_the_store_asks_whatever_its_brand(tmp_path, monkeypatch):
    # review I4: an unclear Zomato purchase on SmartBuy, then Swiggy there: the user is asked before buying again
    w = worker_with(tmp_path, page=page(CART), goal=GOAL, replies=[{"option": "o2"}])
    row = w.memory.payment_started("SmartBuy", money.voucher_key("Zomato"), "2000", "Infinia")
    w.memory.payment_ended(row, "unclear")
    script(monkeypatch, answer(cart="Swiggy ₹2,000 × 2 Swiggy ₹1,000 × 1", payable="₹5,000"))
    w.command("tick")
    assert w.state["outcome"] == "bought_before" and "never confirmed" in w.asked[0]["text"]


def test_the_users_stop_after_the_yes_says_it_may_be_bought(tmp_path, monkeypatch):
    # review I1: the user's own stop after the yes was answered "Cancelled. Nothing more will be done."
    otp = {"id": "o", "kind": "fill", "label": "Enter OTP", "role": "textbox", "value": "", "node": 1,
           "constraints": {}}
    w = worker_with(tmp_path, page=page("Authenticate the transaction\nEnter OTP", url="https://acs.bank.in/otp",
                                        extra=[otp]), goal=GOAL)  # no replies: the user stops at the code
    paid(w)
    w.state["payment_id"] = w.memory.payment_started("SmartBuy", money.voucher_key("Swiggy"), "5000", "Infinia")
    script(monkeypatch, answer([("wait", None, None)]))
    w.command("tick")
    assert w.state["outcome"] == "unclear" and "check your SMS and email" in w.told[-1]["text"]
    assert w.memory.payments("SmartBuy", "voucher_swiggy")[0]["status"] == "unclear"


def test_after_the_yes_no_code_is_asked_for_a_page_opened_by_typing_its_address(tmp_path, monkeypatch):
    # review I6: otp_step asked for the bank's code on any page with a code box once the yes was acted on
    otp = {"id": "o", "kind": "fill", "label": "Enter OTP", "role": "textbox", "value": "", "node": 1,
           "constraints": {}}
    w = worker_with(tmp_path, page=page("Complete your payment\nEnter OTP", url="https://pay-verify.example/otp",
                                        extra=[otp]), goal=GOAL, replies=[{"text": "123456"}])
    paid(w)
    w.state["typed_hosts"].append("pay-verify.example")
    script(monkeypatch, answer([("type", "1", "[ask]")]))
    w.command("tick")
    assert not any(m["kind"] == "secret" for m in w.asked) and w.state["status"] == "blocked"


def test_a_cards_last_four_digits_go_through_the_hidden_field():
    # review: Axis Grab Deals signs in with the card's last four digits; [ask] there was refused
    assert module.secret_field({"kind": "fill", "label": "Last 4 digits of your card"})
    assert module.secret_field({"kind": "fill", "label": "Enter card's last four digits"})
    assert not module.secret_field({"kind": "fill", "label": "Last 4 digits of your bank account"})


def test_a_fee_after_the_yes_asks_a_new_yes(tmp_path, monkeypatch):
    # review: the voucher path had no test for a payable that rose after the yes (bills' rides tests only)
    fee_page = fake_page(url="https://pay.gyftr.com/", text="Payable Amount: ₹5,125\nConvenience fee ₹125", actions=[
        {"id": "pay", "kind": "click", "label": "Pay ₹5,125", "role": "button", "node": 9}])
    w = worker_with(tmp_path, page=fee_page, goal=GOAL, replies=[{"option": "o2"}])
    paid(w)
    w.state["cart_ok"] = True
    script(monkeypatch, answer([("click", "1", None)], payable="₹5,125", fee="Convenience fee ₹125"))
    w.command("tick")
    approval = next(m for m in w.asked if m.get("approval"))
    assert "Payable with the site's fee: ₹5,125" in approval["text"] and w.state["outcome"] == "not_approved"


def test_a_payment_the_page_says_was_declined_is_not_bought(tmp_path, monkeypatch):
    # spec §5: a card declined after the yes is recorded failed and reported "Not bought"
    w = worker_with(tmp_path, page=page("Transaction declined by your bank. Please try another card."), goal=GOAL)
    paid(w)
    w.state["payment_id"] = w.memory.payment_started("SmartBuy", money.voucher_key("Swiggy"), "5000", "Infinia")
    script(monkeypatch, answer(end="stop", say="The bank declined the card."))
    w.command("tick")
    assert w.state["outcome"] == "not_bought" and "Not bought" in w.told[-1]["text"]
    assert w.memory.payments("SmartBuy", "voucher_swiggy")[0]["status"] == "failed"


def test_a_read_never_adds_to_a_cart(tmp_path, monkeypatch):
    # review: a read could press "Add to cart" (a step, not a commit) and leave a cart for the next purchase
    add = {"id": "add", "kind": "click", "label": "Add to cart", "role": "button", "node": 4}
    w = worker_with(tmp_path, page=page("Swiggy\n₹500 ₹1,000", extra=[add]), goal=READ)
    script(monkeypatch, answer([("click", "1", None)]))
    w.command("tick")
    assert not w.browser.act.called and any("never" in f for f in w.state["feedback"])


def test_a_standing_approval_never_stands_in_for_the_purchases_yes(tmp_path, monkeypatch):
    # review: with pre-approval on, a standing rule could commit a purchase with no yes, no row and no masking
    from assistant import gate
    monkeypatch.setenv("JEV_PREAUTH", "on")
    monkeypatch.setattr(gate, "preauthorised", lambda *a: True)
    monkeypatch.setattr(gate, "standing", lambda *a: {"id": 1, "site": "gyftr.com", "cap": 10000})
    w = worker_with(tmp_path, page=page(CART), goal=GOAL, replies=[{"option": "o2"}])
    script(monkeypatch, answer([("click", "1", None)], cart="Swiggy ₹2,000 × 2 Swiggy ₹1,000 × 1", payable="₹5,000"))
    w.command("tick")
    assert any(m.get("approval") for m in w.asked)


def test_after_the_yes_a_dropdowns_value_and_the_address_are_masked(tmp_path, monkeypatch):
    # review: a select's current value and the page's address reached the model unmasked
    pick = {"id": "s", "kind": "select", "label": "Your vouchers", "role": "combobox", "node": 2,
            "value": "QWER7890TYUI", "current_value": "QWER7890TYUI"}
    w = worker_with(tmp_path, page=page(ORDER, url="https://www.gyftr.com/order?code=ZXCV5678ASDF", extra=[pick]),
                    goal=GOAL)
    paid(w)
    seen = script(monkeypatch, answer([("wait", None, None)]))
    w.command("tick")
    sent = json.dumps(seen[0][0], ensure_ascii=False)
    assert "QWER7890TYUI" not in sent and "ZXCV5678ASDF" not in sent and "www.gyftr.com" in sent


def test_a_read_presses_a_product_whose_name_says_pay(tmp_path, monkeypatch):
    # live sweep: "MakeMyTrip e-Pay", the brand's own voucher, was refused as a pay control on every GyFTR store
    option = {"id": "o", "kind": "click", "label": "MakeMyTrip e-Pay", "role": "option", "node": 4}
    w = worker_with(tmp_path, page=page("Search", extra=[option]),
                    goal=money.read_goal("SmartBuy", "MakeMyTrip", 5000))
    script(monkeypatch, answer([("click", "1", None)]))
    w.command("tick")
    assert w.browser.act.called and w.browser.act.call_args.args[1]["label"] == "MakeMyTrip e-Pay"


def test_a_brands_own_product_is_no_pay_control_but_a_pay_button_is():
    # live sweep: "MakeMyTrip e-Pay" and "Amazon Pay Gift Card" were taken for pay buttons; they're the brand's own
    from assistant import gate
    store = "https://www.gyftr.com/instantvouchers"
    for brand, label, commits in (("MakeMyTrip", "MakeMyTrip e-Pay", False), ("Amazon", "Amazon Pay Gift Card", False),
                                  ("Amazon", "Pay ₹5,000", True), ("Amazon", "Buy Amazon Pay voucher now", True)):
        class W:
            job = {"kind": "voucher", "brand": brand}
        assert bool(gate.brand_product(W, {"label": label}, {"url": store})) is not commits, label
