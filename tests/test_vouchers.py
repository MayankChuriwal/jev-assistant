"""A voucher job in the worker: a store's read and a purchase, with the page model scripted; money.py and the gate
decide about money. No Chrome, no models."""

import json

import pytest
from fakes import fake_page, worker_with

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
    assert w.narrows({"label": "Zomato Gold Membership"}) == {"gold", "membership"}
