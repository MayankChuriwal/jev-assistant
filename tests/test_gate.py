"""The commit gate over a fake worker: what commits, the facts an approval shows, asking and pre-authorising,
binding the yes, and watching the result. Jev and mini are scripted."""

from unittest.mock import Mock

import pytest
from fakes import fake_page, worker_with

from assistant import gate
from assistant.browser import StalePage
from assistant.errors import Halt, Retry

PAY = next(a for a in fake_page()["actions"] if a["label"] == "Pay ₹79")


class Fake:
    """What the gate uses of a worker."""

    bench, processing_ms, outcome_pause, memory = False, 300000, 0, None

    def __init__(self, page, replies=(), preauth=False, cap=None):
        self.state = {"goal": "Order one Diet Coke", "chat": [], "recent": [page["text"]], "history": [],
                      "approval": None, "commit": None, "preauth": preauth, "cap": cap, "site": "shop.test",
                      "notes": [], "done": [], "orders": [], "mismatches": 0, "page": page, "reason": None,
                      "browser": Mock(fresh=Mock(return_value=True), focused_label=Mock(return_value=""))}
        self.replies, self.asked, self.said = list(replies), [], []

    def skill(self):
        return None

    def say(self, text, kind="info", **extra):
        self.said.append(text)

    def ask(self, message):
        self.asked.append(message)
        return self.replies.pop(0) if self.replies else {"text": ""}

    def moved(self, page):
        return False

    def steady(self, page):
        pass

    def elapsed(self):
        return 0

    def observe(self):
        return self.state["page"]

    def stop_if_interrupted(self):
        pass


def summary(monkeypatch, lines):
    monkeypatch.setattr(gate.planner, "summary", lambda payload: list(lines))


def checks(monkeypatch, matches="yes", approval="yes"):
    monkeypatch.setattr(gate.jev, "matches", lambda *a: matches)
    monkeypatch.setattr(gate.jev, "approval", lambda *a: approval)


def test_a_pay_button_commits_and_a_payment_method_named_pay_is_cleared_by_jev(monkeypatch):
    page = fake_page()
    w = Fake(page)
    assert gate.commit_label(w, "CLICK", PAY, page) == "Pay ₹79"
    upi = {"id": "u", "kind": "click", "label": "UPI Scan and Pay", "role": "radio", "node": 40}
    monkeypatch.setattr(gate.jev, "final", lambda *a: ("step", 0.9))
    assert gate.commit_label(w, "CLICK", upi, page) is None
    monkeypatch.setattr(gate.jev, "final", lambda *a: ("final", 0.9))
    assert gate.commit_label(w, "CLICK", upi, page) == "UPI Scan and Pay"


def test_jev_never_clears_a_clear_commit(monkeypatch):
    monkeypatch.setattr(gate.jev, "final", lambda *a: ("step", 1.0))
    page = fake_page()
    assert gate.commit_label(Fake(page), "CLICK", PAY, page) == "Pay ₹79"


def test_a_control_its_site_note_names_final_commits_without_jevs_check(monkeypatch):
    # SmartBuy: “Proceed to Pay” was a maybe-commit Jev could clear as a step, opening an order before any yes
    monkeypatch.setattr(gate.jev, "final", lambda *a: pytest.fail("Jev was asked"))
    page = fake_page(url="https://www.gyftr.com/instantvouchers/cart")
    w = Fake(page)
    w.library = {"sites": {"gyftr.com": {"host": "gyftr.com", "final": "Proceed to Pay, Pay Now", "body": ""}}}
    pay = {"id": "p", "kind": "click", "label": "Proceed to Pay ₹500", "role": "button", "node": 42}
    assert gate.commit_label(w, "CLICK", pay, page) == "Proceed to Pay ₹500"
    other = {"id": "o", "kind": "click", "label": "Add to cart", "role": "button", "node": 43}
    assert gate.commit_label(w, "CLICK", other, page) is None


def test_inside_a_commit_subgoal_jev_confirms_a_control_the_regex_missed(monkeypatch):
    page = fake_page()
    go = {"id": "g", "kind": "click", "label": "Continue", "role": "button", "node": 41}
    monkeypatch.setattr(gate.jev, "final", lambda *a: ("final", 0.7))
    assert gate.commit_label(Fake(page), "CLICK", go, page, subgoal={"do": "place the order", "kind": "commit"}) \
        == "Continue"
    assert gate.commit_label(Fake(page), "CLICK", go, page, subgoal={"do": "open the cart", "kind": "act"}) is None


@pytest.mark.parametrize("verdict", [("step", 0.9), ("final", 0.4)])
def test_a_control_jev_lets_through_inside_a_commit_subgoal_is_kept_as_cleared_for_the_audit(monkeypatch, verdict):
    page = fake_page()
    go = {"id": "g", "kind": "click", "label": "Continue", "role": "button", "node": 41}
    monkeypatch.setattr(gate.jev, "final", lambda *a: verdict)
    w = Fake(page)
    assert gate.commit_label(w, "CLICK", go, page, subgoal={"do": "place the order", "kind": "commit"}) is None
    assert w.state["cleared"] == "Continue"  # it runs with no yes: the bench audits it, a stale page decides again


@pytest.mark.parametrize("text, expected", [
    ("order it, don't ask, under ₹300", 300.0), ("max Rs. 1,250", 1250.0), ("budget of 500 INR", 500.0),
    ("upto ₹99.50", 99.5), ("order 2 cokes", None)])
def test_caps_come_from_the_users_words(text, expected):
    assert gate.cap(text) == expected


def test_amounts_read_rupees_in_any_form():
    assert gate.amount("To pay ₹1,234.50") == 1234.5 and gate.amount("Rs. 3,398") == 3398.0
    assert gate.amount("free") is None


def test_facts_come_from_the_page():
    page = fake_page(text="Cart\n2 items\nDelivering to: Home, 12 MG Road, Bengaluru\nItem total ₹70\n"
                          "Handling fee ₹5\nTo pay ₹79\nPay ₹79")
    found = gate.facts(page, [])
    assert found["total"] == "₹79" and found["items"] == 2 and found["address"].startswith("Home, 12 MG Road")
    assert found["fees"] == ["Handling fee ₹5"]


def test_a_clear_yes_binds_the_approval_and_reads_the_page_again(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "yes, go ahead"}])
    with pytest.raises(StalePage):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    question = w.asked[0]["text"]
    assert question.startswith("Diet Coke 300 ml × 1\n• Total ₹79") and "I'll press “Pay ₹79” on shop.test" in question
    assert w.state["approval"]["amount"] == "₹79" and w.state["approval"]["target"] == (None, 11)
    assert gate.approved(w, page, PAY, "Pay ₹79", None) is True  # the same control, host, and amount
    assert [c.get("approval") for c in w.state["chat"][-2:]] == [True, True]  # marked: never a new answer to learn


def test_a_yes_with_a_change_commits_nothing(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "yes but make it 2"}])
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert w.state["approval"] is None


@pytest.mark.parametrize("reply", [{"option": "o2"}, {"text": "No"}, {"text": "nahi, don't pay"}])
def test_a_plain_no_to_a_purchase_ends_it_with_nothing_charged(monkeypatch, reply):
    # the voucher demo: the user taps No at the approval card, and the planner went on looking for another way
    page = fake_page()
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[reply])
    with pytest.raises(Halt, match="You chose not to pay. Nothing was charged.") as ended:
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert ended.value.status == "done" and w.state["outcome"] == "not_approved" and w.state["approval"] is None


def test_the_approval_offers_yes_and_no_and_a_tapped_yes_approves(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"option": "o1"}])
    with pytest.raises(StalePage):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert w.asked[0]["options"] == [{"id": "o1", "label": "Yes"}, {"id": "o2", "label": "No"}]


def test_a_no_that_says_what_to_change_still_redirects(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "No, make it 2"}])
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)


def test_an_amount_the_page_doesnt_show_never_reaches_the_user(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke", "Total ₹97"])
    checks(monkeypatch)
    w = Fake(page)
    with pytest.raises(Retry, match="did not approve"):  # asked, with the page's own total
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert "₹97" not in w.asked[0]["text"] and "Total: ₹79" in w.asked[0]["text"]


INSTAMART = ("Bill details\nDiet Coke 300 ml ₹50.00\nItem total ₹50.00\nHandling Fee ₹12.00\n"
             "Small Cart Fee ₹20.00\nNo small cart fee on orders above ₹99\nDelivery Partner Fee ₹30.00\n"
             "Platform Fee ₹9.00\nTo Pay ₹121\nProceed to Pay")


def test_a_summary_may_leave_off_or_add_a_zero_fraction_the_page_shows(monkeypatch):
    page = fake_page(text=INSTAMART)
    found = gate.facts(page, [])
    summary(monkeypatch, ["Diet Coke 300 ml × 1 · ₹50", "Fees: handling ₹12, small cart ₹20, delivery partner ₹30, "
                          "platform ₹9", "Total ₹121.00"])
    lines = gate.summary(Fake(page), page, found)
    assert lines[:3] == ["Diet Coke 300 ml × 1 · ₹50", "Fees: handling ₹12, small cart ₹20, delivery partner ₹30, "
                         "platform ₹9", "Total ₹121.00"]
    assert not any(line.startswith("Total:") for line in lines)  # ₹121.00 already says the page's ₹121


@pytest.mark.parametrize("line, missing, total", [("Total ₹121.50", "₹121.50", "Total: ₹121"),  # "To Pay": no word
                                                  ("Diet Coke ₹5", "₹5", "Total: ₹121"),
                                                  ("Diet Coke ₹500", "₹500", "Total: ₹121"),
                                                  ("Total ₹12,100", "₹12,100", "Total: ₹121")])
def test_a_summary_amount_with_other_digits_is_still_left_out(monkeypatch, line, missing, total):
    page = fake_page(text=INSTAMART)
    summary(monkeypatch, ["Diet Coke 300 ml × 1", line])
    lines = gate.summary(Fake(page), page, gate.facts(page, []))
    assert not gate.rules.amount_shown(missing, " ".join(lines)) and total in lines


def test_a_whole_amount_is_never_taken_for_one_with_paise(monkeypatch):
    page = fake_page(text="Checkout\nDiet Coke ₹79.50\nTo pay ₹79.50\nPay")
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    lines = gate.summary(Fake(page), page, gate.facts(page, []))
    assert "Total ₹79" not in lines and "Total: ₹79.50" in lines  # "To pay" names no word "Total" does: the page's


def test_an_order_that_doesnt_match_is_fixed_twice_then_asked(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Pepsi", "Total ₹79"])
    checks(monkeypatch, matches="no")
    w = Fake(page, replies=[{"text": "no"}])
    for _ in range(2):
        with pytest.raises(Retry, match="doesn't match"):
            gate.approved(w, page, PAY, "Pay ₹79", None)
    with pytest.raises(Halt, match="Nothing was charged"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert len(w.asked) == 1


def test_preauthorised_within_the_cap_runs_and_says_so(monkeypatch):
    monkeypatch.setenv("JEV_PREAUTH", "on")
    page = fake_page()
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, preauth=True, cap=100.0)
    assert gate.approved(w, page, PAY, "Pay ₹79", None) is True
    assert w.asked == [] and w.said[0].startswith("Placing: Diet Coke 300 ml × 1")
    assert w.state["approval"]["by"] == "preauth"


@pytest.mark.parametrize("cap", [78.0, None])
def test_preauthorised_over_the_cap_or_without_one_still_asks(monkeypatch, cap):
    page = fake_page(text="Total ₹79\nPay ₹79")
    summary(monkeypatch, ["Diet Coke", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "change it"}], preauth=True, cap=cap)
    with pytest.raises(Retry):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert len(w.asked) == 1


def test_preauthorised_without_a_cap_submits_a_form_with_no_amount_only_for_a_goal_that_buys_nothing(monkeypatch):
    monkeypatch.setenv("JEV_PREAUTH", "on")
    page = fake_page(text="Pizza order\nCustomer name\nSubmit order", url="https://httpbin.org/forms/post")
    submit = {"id": "s", "kind": "click", "label": "Submit order", "role": "button", "node": 50}
    summary(monkeypatch, ["Medium pizza with bacon", "Test User at 19:00"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "no"}], preauth=True)
    w.state["site"] = "httpbin.org"  # the bench's pizza form: "order" makes it a commerce goal, so it still asks
    w.state["goal"] = "Fill the pizza order form at https://httpbin.org/forms/post and submit it, don't ask"
    with pytest.raises(Halt, match="Nothing was charged"):
        gate.approved(w, page, submit, "Submit order", None)
    assert len(w.asked) == 1
    w = Fake(page, preauth=True)
    w.state["site"], w.state["goal"] = "httpbin.org", "Fill the feedback form on httpbin and submit it, don't ask"
    assert gate.approved(w, page, submit, "Submit order", None) is True


def test_a_bench_run_never_uses_preauthorisation(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "change it"}], preauth=True, cap=100.0)
    w.bench = True
    with pytest.raises(Retry):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert w.state["bench_preauth"] is True and len(w.asked) == 1


@pytest.mark.parametrize("verdicts, note", [(["processing", "confirmed"], "Done: Pay ₹79"),
                                            (["failed"], "failed"), (["next_step"], "only a step")])
def test_after_a_commit_the_site_is_watched_until_it_says(monkeypatch, verdicts, note):
    w = Fake(fake_page())
    w.state["commit"] = {"label": "Pay ₹79", "since": 0}
    queue = list(verdicts)
    monkeypatch.setattr(gate.jev, "after_commit", lambda *a: queue.pop(0))
    gate.watch(w, "Pay ₹79", "Order a coke")
    assert w.state["commit"] is None and note in w.state["notes"][-1] and w.state["reason"] == "commit"
    assert (w.state["done"] == ["Pay ₹79"]) == (verdicts[-1] == "confirmed")
    assert (len(w.said) == 1) == ("processing" in verdicts)


def test_an_unclear_result_twice_stops_the_task(monkeypatch):
    w = Fake(fake_page())
    w.state["commit"] = {"label": "Pay ₹79", "since": 0}
    monkeypatch.setattr(gate.jev, "after_commit", lambda *a: "unclear")
    with pytest.raises(Halt, match="can't tell whether Pay ₹79 went through"):
        gate.watch(w, "Pay ₹79", "Order a coke")


# Fix round 1: what the review found in the gate. Every case errs toward asking.

def control(label, node=60, role="button"):
    return {"id": f"c{node}", "kind": "click", "label": label, "role": role, "node": node}


def test_a_double_click_on_a_pay_button_commits_like_a_click(monkeypatch):
    page = fake_page()
    assert gate.commit_label(Fake(page), "DOUBLE_CLICK", PAY, page) == "Pay ₹79"
    upi = control("UPI Scan and Pay", role="radio")
    monkeypatch.setattr(gate.jev, "final", lambda *a: ("step", 0.9))
    assert gate.commit_label(Fake(page), "DOUBLE_CLICK", upi, page) is None  # an ambiguous one is cleared as a click is


@pytest.mark.parametrize("text, expected", [
    ("To pay ₹1,299 (you save ₹200)\nPay ₹1,299", "₹1,299"),            # an aside in brackets is not the payable figure
    ("To pay ₹1,299 (was ₹1,499)\nPay ₹1,299", "₹1,299"),
    ("To pay ₹1,299 · ₹50 cashback\nPay ₹1,299", None),                  # a line about cashback is not a total line
    ("Total ₹79\nTo pay ₹1,299 · ₹50 cashback", None),                  # never fall back to a smaller total line
    ("To pay ₹1,299\nAdd ₹99 more to pay ₹0 delivery fee", "₹1,299"),
    ("To pay ₹1,299 + ₹20 fee, free delivery", None),
    ("Amount due ₹120 incl. ₹20 fee", "₹120"),                           # the largest price on the line, not the last
    ("To pay ₹50\nTo pay ₹79", "₹79"),                                   # the last payable line
    ("To pay\n₹79", "₹79"),                                              # a price on the next line
    ("To pay\nYou save ₹200", None),                                     # the next line is not a total either
    ("To pay INR 1,499\nSave ₹30", "INR 1,499"),
    ("Grand total $120", "$120"),
    ("Item total ₹70\nSubtotal ₹70\nYou saved ₹10 discount", None)])
def test_the_payable_total_is_the_largest_price_on_the_last_payable_line(text, expected):
    assert gate.facts(fake_page(text=text), [])["total"] == expected


def test_only_rupee_amounts_are_read():
    assert gate.amount("$120") is None and gate.amount("€5") is None
    assert gate.amount("INR 1,499") == 1499.0 and gate.amount("Rs 50") == 50.0 and gate.amount("₹ 7.5") == 7.5


def test_a_total_in_another_currency_is_never_preauthorised(monkeypatch):
    page = fake_page(text="Cart\nGrand total $120\nPay now")
    summary(monkeypatch, ["Diet Coke", "Total $120"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "no"}], preauth=True, cap=5000.0)
    assert gate.preauthorised(w.state, gate.facts(page, []), page) is False
    with pytest.raises(Halt, match="Nothing was charged"):
        gate.approved(w, page, control("Pay now"), "Pay now", None)
    assert len(w.asked) == 1


@pytest.mark.parametrize("text, expected", [
    ("deliver within 500 m of me, don't ask", None), ("under 30 minutes", None), ("up to 2 items", None),
    ("budget 500", None), ("max 1000 words", None),
    ("up to 20000 mAh, under ₹1500", 1500.0), ("within 500 m, but max Rs. 250", 250.0),
    ("up to INR 500", 500.0), ("under 300 rupees", 300.0), ("less than 99 rs", 99.0),
    ("not more than ₹ 1,200 please", 1200.0), ("upto 40 inr", 40.0)])
def test_a_cap_needs_a_rupee_marker_beside_the_number(text, expected):
    assert gate.cap(text) == expected


@pytest.mark.parametrize("label", ["Place order (Cash on Delivery)", "Buy now with UPI", "Confirm payment via card",
                                   "Pay now using Google Pay"])
def test_jev_cannot_clear_a_commit_that_only_mentions_a_payment_method(monkeypatch, label):
    page = fake_page()
    monkeypatch.setattr(gate.jev, "final", lambda *a: ("step", 0.9))
    assert gate.commit_label(Fake(page), "CLICK", control(label), page) == label


def test_an_approval_does_not_survive_a_changed_total(monkeypatch):
    before = fake_page()
    after = fake_page(text="Checkout\nDiet Coke ₹79\nDelivery fee ₹20\nTo pay ₹99\nPay ₹99")
    summary(monkeypatch, ["Diet Coke", "Total ₹99"])
    checks(monkeypatch)
    w = Fake(after, replies=[{"text": "yes please"}])
    gate.bind(w.state, PAY, "Pay ₹79", "₹79", before, "user")
    assert gate.approved(w, before, PAY, "Pay ₹79", None) is True  # the same page: the approval stands
    with pytest.raises(StalePage):
        gate.approved(w, after, PAY, "Pay ₹99", None)  # ₹79 is still on the page, but the payable total is ₹99
    assert len(w.asked) == 1 and w.state["approval"]["amount"] == "₹99"


def test_an_approval_with_the_same_total_still_stands(monkeypatch):
    page = fake_page(text="Checkout\nDiet Coke ₹79\nTo pay Rs. 79\nPay ₹79")
    w = Fake(page)
    gate.bind(w.state, PAY, "Pay ₹79", "₹79", page, "user")
    assert gate.approved(w, page, PAY, "Pay ₹79", None) is True and w.asked == []


@pytest.mark.parametrize("text", ["Amount INR 1,499\nSubmit order", "Total 1499\nPlace order",
                                  "Fee payable: Rs 50\nSubmit order", "Amount 250 only\nSubmit order",
                                  "Pay 1,200 rupees\nSubmit order", "Charge: INR 99\nSubmit order",
                                  "To pay 1499\nSubmit order"])
def test_without_a_cap_a_page_that_shows_money_is_never_preauthorised(monkeypatch, text):
    page = fake_page(text=text, url="https://httpbin.org/forms/post")
    submit = control("Submit order", node=50)
    summary(monkeypatch, ["Medium pizza with bacon", "Test User at 19:00"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "no"}], preauth=True)
    w.state["site"] = "httpbin.org"
    with pytest.raises(Halt, match="Nothing was charged"):
        gate.approved(w, page, submit, "Submit order", None)
    assert len(w.asked) == 1


def test_a_cap_does_not_preauthorise_a_page_whose_amount_could_not_be_read(monkeypatch):
    page = fake_page(text="Amount INR 1,499\nSubmit order", url="https://httpbin.org/forms/post")
    w = Fake(page, preauth=True, cap=5000.0)
    w.state["site"] = "httpbin.org"
    assert gate.preauthorised(w.state, {"total": None}, page) is False
    assert gate.preauthorised(w.state, {"total": "INR 1,499"}, page) is True  # read and within the cap


def test_a_reply_that_is_not_a_clear_yes_lapses_any_earlier_approval(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "no, make it 2"}])
    w.state["approval"] = {"action_label": "Pay ₹79", "amount": "₹79", "host": "shop.test", "step": 0,
                           "target": (None, 99), "by": "user"}  # an approval for another control
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert w.state["approval"] is None


def test_a_yes_that_jev_reads_as_a_change_lapses_the_approval_too(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch, approval="change")
    w = Fake(page, replies=[{"text": "yes"}])
    w.state["approval"] = {"action_label": "Pay ₹79", "amount": "₹79", "host": "shop.test", "step": 0,
                           "target": (None, 99), "by": "user"}
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert w.state["approval"] is None


def test_fees_from_an_earlier_page_count_only_while_its_total_is_the_current_one():
    page = fake_page(text="Cart\nTo pay ₹79\nPay ₹79")
    older = "Cart\nDelivery fee ₹30\nTo pay ₹109"
    assert gate.facts(page, [older])["fees"] == []
    same = "Cart\nHandling fee ₹5\nTo pay ₹79"
    assert gate.facts(page, [older, same])["fees"] == ["Handling fee ₹5"]
    assert gate.facts(page, [same.replace("₹79", "Rs. 79")])["fees"] == ["Handling fee ₹5"]
    assert gate.facts(page, ["Cart\nHandling fee ₹5"])["fees"] == []  # no total to tell that it still applies
    now = fake_page(text="Cart\nDelivery fee ₹20\nTo pay ₹99\nPay ₹99")
    assert gate.facts(now, [same, older])["fees"] == ["Delivery fee ₹20"]


def test_a_page_with_no_total_takes_its_fees_from_itself_only():
    page = fake_page(text="Cart\nPlatform fee ₹3\nPay")
    assert gate.facts(page, ["Cart\nHandling fee ₹5\nTo pay ₹79"])["fees"] == ["Platform fee ₹3"]


# Fix round 2.

@pytest.mark.parametrize("text, expected", [
    ("To pay ₹1,299 (you save ₹200)", "₹1,299"),
    ("To pay ₹1,299 (was ₹1,499)", "₹1,299"),
    ("To pay ₹1,299 (MRP ₹1,599)", "₹1,299"),
    ("To pay ₹999 (₹1,199 incl. GST)", "₹1,199"),                        # a price in brackets can be the real one
    ("Grand total (₹2,999 incl. GST)\nDelivery fee ₹30", "₹2,999"),
    ("Cart total ₹2,900\nTo pay (₹2,999 incl. taxes)", "₹2,999"),
    ("To pay ₹999 (incl. ₹50 off)", "₹999"),
    ("To pay (₹2,999 (incl. GST))", "₹2,999")])
def test_a_bracket_hides_a_price_only_when_it_is_a_remark(text, expected):
    assert gate.facts(fake_page(text=text), [])["total"] == expected


@pytest.mark.parametrize("text, expected", [
    ("Total ₹79\nTo pay ₹1,299 · ₹50 cashback", None),
    ("Total ₹79\nTo pay ₹1,299 you save ₹200", None),
    ("Total ₹79\nTo pay\n₹1,299 you save ₹200", None),                   # the turned-away price sits on the next line
    ("Items total ₹1,249\nDelivery ₹30\nGrand total ₹1,278 · Saved ₹30", None),
    ("Items total ₹1,249\nGrand total ₹1,278", "₹1,278"),               # an item total is not the payable total
    ("Item total ₹1,000\nTo pay ₹700", "₹700"),                         # a discounted cart: the item total is no hint
    ("Item total ₹70\nHandling fee ₹5\nTo pay ₹79", "₹79"),
    ("To pay ₹1,299\nAdd ₹99 more to pay ₹0 delivery fee", "₹1,299")])
def test_a_total_line_that_was_turned_away_never_leaves_a_smaller_total(text, expected):
    assert gate.facts(fake_page(text=text), [])["total"] == expected


def test_a_payable_line_hidden_by_a_remark_is_not_preauthorised_on_a_smaller_total():
    page = fake_page(text="Total ₹79\nTo pay ₹1,299 · ₹50 cashback\nPay")
    w = Fake(page, preauth=True, cap=100.0)
    assert gate.preauthorised(w.state, gate.facts(page, []), page) is False


def test_an_approval_with_no_amount_does_not_survive_a_page_that_now_shows_money(monkeypatch):
    form = fake_page(text="Pizza order\nSubmit order")
    summary(monkeypatch, ["Medium pizza", "Test User at 19:00"])
    checks(monkeypatch)
    for now in ("Pizza order\nTo pay ₹2,999\nSubmit order", "Pizza order\nTotal: 2,999\nSubmit order",
                "Pizza order\nPrice: 2,999/-\nSubmit order"):
        w = Fake(form, replies=[{"text": "no"}])
        gate.bind(w.state, PAY, "Submit order", "", form, "user")
        assert gate.approved(w, form, PAY, "Submit order", None) is True and w.asked == []  # still no money
        with pytest.raises(Halt, match="Nothing was charged"):
            gate.approved(w, fake_page(text=now), PAY, "Submit order", None)
        assert len(w.asked) == 1 and w.state["approval"] is None


def test_an_approval_given_on_a_page_with_money_but_no_readable_total_is_not_asked_for_again():
    page = fake_page(text="Pizza order\nPrice: 1,499/-\nSubmit order")
    w = Fake(page)
    gate.bind(w.state, PAY, "Submit order", "", page, "user")
    assert gate.approved(w, page, PAY, "Submit order", None) is True and w.asked == []


@pytest.mark.parametrize("text", ["Price: 1,499/-", "Fare 250", "Cost 1499", "Charges: 99", "₨ 500", "USD 120",
                                  "EUR 90", "AED 50", "Rate 1,200", "Fees: 15", "1,499/- only", "Rs 99"])
def test_more_ways_to_show_money_are_never_preauthorised_without_a_cap(monkeypatch, text):
    page = fake_page(text=f"Order form\n{text}\nSubmit order", url="https://httpbin.org/forms/post")
    assert gate.shows_money(page["text"])
    summary(monkeypatch, ["Medium pizza with bacon", "Test User at 19:00"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "no"}], preauth=True)
    w.state["site"] = "httpbin.org"
    with pytest.raises(Halt, match="Nothing was charged"):
        gate.approved(w, page, control("Submit order", node=50), "Submit order", None)
    assert len(w.asked) == 1


def test_a_page_with_no_amount_still_shows_no_money():
    page = "Pizza order\nCustomer name\nPreferred delivery time\nDelivery instructions\nSubmit order"
    assert not gate.shows_money(page)


# Fix round 3: two structural backstops (the worst number on a money line, and a snapshot of the money lines).

def cart(text, cap, url="https://shop.test/checkout", site="shop.test"):
    page = fake_page(text=text, url=url)
    w = Fake(page, preauth=True, cap=cap)
    w.state["site"] = site
    return w, page


def test_numbers_read_commas_and_decimals():
    assert gate.numbers("Total 1,499.00, 2,50,000 and 7 (19:05)") == [1499.0, 250000.0, 7.0]  # a clock is cut out
    assert gate.numbers("Customer name") == []


def test_money_lines_take_the_lines_around_a_money_word():
    text = "Cart\nTotal\n1,499\nDiet Coke\nPrice: ₹40\nthanks\nbye\nsee you"
    assert gate.money_lines(text) == ["Cart", "Total", "1,499", "Diet Coke", "Price: ₹40", "thanks"]
    assert gate.money_lines("Pizza order\nCustomer name\nSubmit order") == []
    assert gate.money_lines("Laptop 60,000 USD\nDiet Coke") == ["Laptop 60,000 USD"]  # a currency line alone


def test_worst_is_the_largest_number_on_the_money_lines_brackets_included():
    assert gate.worst("Diet Coke ₹4,000\nHandling fee ₹5\nTo pay ₹45") == 4000.0  # a price line counts too
    assert gate.worst("To pay ₹999 (₹1,199 incl. GST, free delivery)") == 1199.0
    assert gate.worst("Total\n1,499") == 1499.0
    assert gate.worst("Pizza order\nCustomer name") == 0.0


def test_the_snapshot_holds_every_number_on_the_money_lines_in_page_order():
    page = "Cart\nItems total ₹200 ₹150\nHandling fee ₹5\nGrand total ₹155\nPay"
    assert gate.snapshot(page) == (200.0, 150.0, 5.0, 155.0)  # in page order, not sorted
    assert gate.snapshot("Pizza order\nCustomer name") == ()


@pytest.mark.parametrize("text, cap, expected", [
    ("Total ₹79\nTo pay · ₹50 cashback applied\n₹1,299", 100.0, False),
    ("Bill total ₹79\nGrand total · you saved\n₹1,299", 100.0, False),
    ("To pay ₹999 (₹1,199 incl. GST, free delivery)", 1000.0, False),
    ("Total ₹79\nTo pay (₹1,299 after ₹50 cashback)", 100.0, False),
    ("Total\n1,499", 1000.0, False),
    ("Diet Coke ₹40\nHandling fee ₹5\nTo pay ₹45", 100.0, True),
    ("Item total ₹100\nDiscount ₹30\nTo pay ₹70", 300.0, True)])
def test_under_a_cap_no_number_on_a_money_line_may_exceed_it(text, cap, expected):
    w, page = cart(text, cap)
    assert gate.preauthorised(w.state, gate.facts(page, []), page) is expected


def test_the_bill_shown_to_the_user_is_not_a_smaller_total_beside_a_hidden_payable_line():
    assert gate.facts(fake_page(text="Bill total ₹79\nGrand total · you saved\n₹1,299"), [])["total"] is None


def test_the_snapshot_is_bound_with_the_approval(monkeypatch):
    page = fake_page()
    w = Fake(page)
    gate.bind(w.state, PAY, "Pay ₹79", "₹79", page, "user")
    # the money lines: the item line before "Total", the total, and the pay button
    assert w.state["approval"]["snapshot"] == gate.snapshot(page["text"]) == (300.0, 79.0, 79.0) and \
        "money" not in w.state["approval"]


def test_a_fresh_yes_then_the_same_page_is_approved_without_a_second_question(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "yes"}])
    with pytest.raises(StalePage):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert gate.approved(w, page, PAY, "Pay ₹79", None) is True and len(w.asked) == 1


@pytest.mark.parametrize("before, after", [
    ("Pizza order\nPrice: 1,499/-\nSubmit order", "Pizza order\nTo pay ₹14,999\nSubmit order"),
    ("Cart\nItems total ₹200 ₹150\nHandling fee ₹5\nGrand total ₹155\nPay",
     "Cart\nItems total ₹400 ₹300\nHandling fee ₹5\nGrand total ₹305\nPay"),
    ("Pizza order\nSubmit order", "Pizza order\nTotal\n1,499\nSubmit order")])
def test_an_approval_lapses_when_any_money_line_changes(monkeypatch, before, after):
    first, second = fake_page(text=before), fake_page(text=after)
    summary(monkeypatch, ["Medium pizza", "Test User at 19:00"])
    checks(monkeypatch)
    w = Fake(second, replies=[{"text": "no"}])
    gate.bind(w.state, PAY, "Submit order", "", first, "user")
    assert gate.approved(w, first, PAY, "Submit order", None) is True and w.asked == []
    with pytest.raises(Halt, match="Nothing was charged"):
        gate.approved(w, second, PAY, "Submit order", None)
    assert len(w.asked) == 1 and w.state["approval"] is None


def test_an_approval_with_no_snapshot_is_void(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "no"}])
    gate.bind(w.state, PAY, "Pay ₹79", "₹79", page, "user")
    del w.state["approval"]["snapshot"]
    with pytest.raises(Halt, match="Nothing was charged"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert len(w.asked) == 1


def test_a_total_that_could_not_be_read_binds_no_amount_not_the_last_price_in_the_summary(monkeypatch):
    page = fake_page(text="Cart\nDelivery fee ₹30\nPay")
    summary(monkeypatch, ["Medium pizza", "Delivery fee ₹30"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "yes"}])
    with pytest.raises(StalePage):
        gate.approved(w, page, control("Pay", node=11), "Pay", None)
    assert w.state["approval"]["amount"] == ""
    assert gate.approved(w, page, control("Pay", node=11), "Pay", None) is True  # same page: no second question
    assert len(w.asked) == 1


@pytest.mark.parametrize("text", [
    "Total\n1,499", "Amount payable\n1,499.00", "Fare\n250", "120 USD", "50 AED", "Pay 1,499", "SGD 50", "RM 50",
    "¥500", "20 euros", "500 रुपये", "USD  120", "USD120", "Rs5", "₩9,000", "10 dollars", "₹\n1,499",
    "Price: 1,499/-", "Fare 250", "Cost 1499", "Charges: 99", "₨ 500", "USD 120", "EUR 90", "AED 50",
    "Amount INR 1,499", "Total 1499", "Bill\n99", "Due 50", "500 रुपया"])
def test_these_pages_show_money_and_are_never_preauthorised_without_a_cap(monkeypatch, text):
    page = fake_page(text=f"Order form\n{text}\nSubmit order", url="https://httpbin.org/forms/post")
    assert gate.shows_money(page["text"])
    summary(monkeypatch, ["Medium pizza with bacon", "Test User at 19:00"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "no"}], preauth=True)
    w.state["site"] = "httpbin.org"
    with pytest.raises(Halt, match="Nothing was charged"):
        gate.approved(w, page, control("Submit order", node=50), "Submit order", None)
    assert len(w.asked) == 1


@pytest.mark.parametrize("text", [
    "Pizza order\nCustomer name\nSubmit order",
    "Pizza Size\nSmall\nMedium\nLarge\nPreferred delivery time\nDelivery instructions\nSubmit order",
    "Contact form\nYour message\nSubmit request"])
def test_a_form_with_no_digits_on_its_money_lines_shows_no_money(text):
    assert not gate.shows_money(text)


# Fix round 4: flat text, one money set, suffixes, ticking lines, and the turned-away rule restored.

@pytest.mark.parametrize("text, expected", [
    ("₹\n1,299", "₹ 1,299"), ("To pay\n₹\n1,299\nPay", "To pay\n₹ 1,299\nPay"), ("Rs.\n1,299", "Rs. 1,299"),
    ("Order Total:\nRs.\n1,299", "Order Total:\nRs. 1,299"), ("INR:\n99", "INR: 99"), ("USD\n 120 ", "USD 120"),
    ("$\n5", "$ 5"), ("₱\n5", "₱ 5"), ("₺\n5", "₺ 5"), ("₪\n5", "₪ 5"), ("रुपये\n500", "रुपये 500"),
    ("rupees\n500", "rupees 500"), ("₹\n\n1,299", "₹ 1,299"), ("₹\n₹\n5", "₹ ₹ 5"),
    ("1,499\n/-", "1,499 /-"), ("₹\n1,499\n/-", "₹ 1,499 /-"), ("Total\n₹", "Total\n₹"),
    ("Total ₹79\nPay", "Total ₹79\nPay"), ("Qty\n2\nPay", "Qty\n2\nPay"), ("", "")])
def test_flat_joins_a_currency_marker_to_its_number(text, expected):
    assert gate.flat(text) == expected
    assert gate.flat(gate.flat(text)) == gate.flat(text)


@pytest.mark.parametrize("text, expected", [
    ("₹1.5L", [150000.0]), ("₹15K", [15000.0]), ("Rs 2Cr", [20000000.0]), ("₹2 crore", [20000000.0]),
    ("₹3 lakh", [300000.0]), ("INR 1.2M", [1200000.0]), ("₹1.2 million", [1200000.0]), ("Total 1.5L", [150000.0]),
    ("Diet Coke 2L", [2.0]), ("Diet Coke 2L ₹95", [2.0, 95.0]), ("Diet Coke 300ml", [300.0]), ("₹1,499.00", [1499.0])])
def test_numbers_read_lakh_crore_and_k_suffixes_only_where_they_mean_money(text, expected):
    assert gate.numbers(text) == expected


def test_a_quantity_with_a_unit_is_not_an_amount():
    assert gate.amounts("Diet Coke 300 ml") == [] and gate.amounts("Qty 2") == [2.0]
    assert gate.amounts("₹300 ml") == [300.0] and gate.amount("₹1.5L") == 150000.0


@pytest.mark.parametrize("text, cap, expected", [
    ("To pay\n₹\n1,299", 100.0, False),
    ("Diet Coke\nTotal ₹79\nTo pay\n₹\n1,299\nPay", 100.0, False),
    ("Total ₹79\nOrder Total:\nRs.\n1,299\nPay", 100.0, False),
    ("Total (1 item) ₹79\nDelivery fee ₹30\nTo pay\n₹\n109\nPay", 100.0, False),
    ("Total ₹79\nTo pay · ₹50 cashback applied\n₹1,299", 100.0, False),
    ("Total ₹1.5L", 100.0, False),
    ("Total ₹79\nInclusive of taxes\nYou will be charged ₹1,299", 100.0, False),
    ("Total ₹79\nYou were billed ₹1,299", 100.0, False),
    ("Total ₹79\nAlready paid ₹1,299", 100.0, False),
    ("Total ₹79\nTo pay\n₹\n99\nPay", 100.0, True),
    ("Diet Coke ₹40\nHandling fee ₹5\nTo pay ₹45", 100.0, True),
    ("Item total ₹100\nDiscount ₹30\nTo pay ₹70", 300.0, True),
    ("Item total ₹100\nDiscount ₹30\nTo pay ₹70", 99.0, False)])
def test_split_layouts_and_hidden_totals_are_not_preauthorised(text, cap, expected):
    w, page = cart(text, cap)
    assert gate.preauthorised(w.state, gate.facts(page, []), page) is expected


@pytest.mark.parametrize("text, expected", [
    ("To pay\n₹\n1,299", "₹ 1,299"),
    ("Total ₹79\nOrder Total:\nRs.\n1,299\nPay", "Rs. 1,299"),
    ("Total ₹79\nTo pay · ₹50 cashback applied\n₹1,299", None),
    ("Bill total ₹79\nGrand total · you saved\n₹1,299", None),
    ("Total fees ₹25\nTotal ₹1,299 · ₹50 cashback applied", None),
    ("Total GST ₹79\nTotal ₹1,299 · you save ₹200", None),
    ("Item total ₹1,000\nTo pay ₹700", "₹700"),
    ("Item total ₹100\nDiscount ₹30\nTo pay ₹70", "₹70"),
    ("Total savings ₹300\nTo pay ₹79", "₹79"),
    ("Items total ₹1,249 · Saved ₹30\nTo pay ₹1,219", "₹1,219"),
    ("To pay ₹1,299 (you save ₹200)", "₹1,299"),
    ("Add ₹99 more to pay ₹0 delivery fee\nTo pay ₹1,299", "₹1,299"),
    ("Items total ₹1,249\nDelivery ₹30\nGrand total ₹1,278 · Saved ₹30", None),
    ("Total ₹1.5L", "₹1.5L")])
def test_a_payable_line_turned_away_for_a_remark_hides_a_smaller_total_but_an_item_total_does_not(text, expected):
    assert gate.facts(fake_page(text=text), [])["total"] == expected


def test_a_page_with_the_currency_on_its_own_line_gets_the_same_facts_and_fees():
    page = fake_page(text="Cart\n2 items\nDelivering to: Home, 12 MG Road\nHandling fee\n₹\n5\nTo pay\n₹\n79\nPay")
    found = gate.facts(page, [])
    assert found["total"] == "₹ 79" and found["items"] == 2 and found["address"].startswith("Home, 12 MG Road")
    assert gate.amount(found["total"]) == 79.0


@pytest.mark.parametrize("before, after", [
    ("Pizza order\nSubmit order", "Pizza order\n₹\n14,999\nSubmit order"),
    ("Diet Coke ₹79\nQty 1\nTo pay\n₹\n79", "Diet Coke ₹79\nQty 2\nTo pay\n₹\n158"),
    ("Item total ₹99\nDiscount ₹20\nTo pay ₹79", "Item total ₹79\nHandling fee ₹20\nTo pay ₹99"),
    ("Total ₹500\nTo pay\n₹\n500", "Total ₹500\nTo pay\n₹\n5,000")])
def test_an_approval_lapses_when_the_split_layout_or_the_order_of_the_money_changes(monkeypatch, before, after):
    first, second = fake_page(text=before), fake_page(text=after)
    assert gate.snapshot(before) != gate.snapshot(after)
    summary(monkeypatch, ["Medium pizza", "Test User at 19:00"])
    checks(monkeypatch)
    w = Fake(second, replies=[{"text": "no"}])
    gate.bind(w.state, PAY, "Submit order", "", first, "user")
    assert gate.approved(w, first, PAY, "Submit order", None) is True and w.asked == []
    with pytest.raises(Halt, match="Nothing was charged"):
        gate.approved(w, second, PAY, "Submit order", None)
    assert len(w.asked) == 1 and w.state["approval"] is None


@pytest.mark.parametrize("before, after", [
    ("Total ₹500\nPay within 04:59", "Total ₹500\nPay within 04:58"),
    ("Total ₹500\nTime left 07:42", "Total ₹500\nTime left 07:41"),
    ("Total ₹500\nOffer expires in 12 mins", "Total ₹500\nOffer expires in 11 mins"),
    ("Total ₹500\nArriving in 25 minutes", "Total ₹500\nArriving in 24 minutes"),
    ("Total ₹500\nETA 9 min", "Total ₹500\nETA 8 min")])
def test_a_ticking_line_does_not_change_the_snapshot_or_void_the_approval(monkeypatch, before, after):
    assert gate.snapshot(before) == gate.snapshot(after) == (500.0,)
    first, second = fake_page(text=before), fake_page(text=after)
    w = Fake(second)
    gate.bind(w.state, PAY, "Pay", "₹500", first, "user")
    assert gate.approved(w, second, PAY, "Pay", None) is True and w.asked == []


def test_a_countdown_inside_a_price_line_is_stripped_and_the_price_stays():
    assert gate.snapshot("Total ₹500 (offer ends 04:59)") == gate.snapshot("Total ₹500 (offer ends 04:58)") == (500.0,)


@pytest.mark.parametrize("text", [
    "1,499\n/-", "₱500", "₺500", "₪500", "₱\n500", "5 pounds", "20 pesos", "Rp 5000", "50 baht", "zł 20", "30 riyal",
    "10 dirham", "You pay 99", "You will pay 99", "Charged 99", "Billed 99", "Paid 99", "¢50", "฿ 90", "₫ 500"])
def test_more_currencies_and_words_count_as_money_without_a_cap(monkeypatch, text):
    page = fake_page(text=f"Order form\n{text}\nSubmit order", url="https://httpbin.org/forms/post")
    assert gate.shows_money(page["text"])
    summary(monkeypatch, ["Medium pizza with bacon", "Test User at 19:00"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "no"}], preauth=True)
    w.state["site"] = "httpbin.org"
    with pytest.raises(Halt, match="Nothing was charged"):
        gate.approved(w, page, control("Submit order", node=50), "Submit order", None)
    assert len(w.asked) == 1


def test_a_ticking_line_alone_is_no_money():
    assert not gate.shows_money("Pizza order\nPay within 04:59\nSubmit order")
    assert not gate.shows_money("Pizza order\nDelivery in 30 mins\nSubmit order")


def test_a_summary_may_copy_an_amount_the_page_splits_over_two_lines(monkeypatch):
    page = fake_page(text="Checkout\nTo pay\n₹\n79\nPay")
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "yes"}])
    with pytest.raises(StalePage):
        gate.approved(w, page, control("Pay", node=11), "Pay", None)
    assert "Total ₹79" in w.asked[0]["text"] and w.asked[0]["text"].count("₹79") == 1  # no duplicate total line
    assert w.state["approval"]["amount"] == "₹ 79"
    assert gate.approved(w, page, control("Pay", node=11), "Pay", None) is True and len(w.asked) == 1


# Fix round 5: suffixes with a gap, ticking tokens (not whole lines), split remarks, and units only off money lines.

@pytest.mark.parametrize("text", [
    "Total ₹1.5 L", "To pay ₹15 K", "To pay Rs 1.5 L", "To pay ₹ 1.5 L", "To pay\n₹\n1.5\nL", "To pay ₹2.5 Mn",
    "To pay ₹1 bn", "To pay ₹1 billion", "₹1 billion\nTo pay\n₹1 billion", "Grand Total: 1.5L (incl. ₹2,000 GST)",
    "To pay ₹1.5 lakh", "To pay ₹2 Cr", "To pay ₹ 3 crore", "To pay ₹1.5L", "To pay ₹15k", "Total INR 1.5 L",
    "Total ₹79\nTo pay\n₹\n1.5 lac"])
def test_a_scaled_total_is_never_preauthorised_under_a_rupee_cap(text):
    w, page = cart(text, 100.0)
    assert gate.preauthorised(w.state, gate.facts(page, []), page) is False
    assert gate.worst(text) > 100.0


@pytest.mark.parametrize("text, expected", [
    ("₹1.5 L", [150000.0]), ("₹15 K", [15000.0]), ("Rs 1.5 L", [150000.0]), ("₹ 1.5 L", [150000.0]),
    ("₹2.5 Mn", [2500000.0]), ("₹1 bn", [1000000000.0]), ("₹1 billion", [1000000000.0]), ("₹2 Cr", [20000000.0]),
    ("Grand Total: 1.5L (incl. ₹2,000 GST)", [150000.0, 2000.0]), ("To pay 1.5 L", [150000.0]),
    ("Total 1,299 L", [129900000.0]), ("Diet Coke 2 L ₹95", [2.0, 95.0]), ("Diet Coke 2L ₹95", [2.0, 95.0]),
    ("Pizza 2 M ₹300", [2.0, 300.0]), ("₹95 Large", [95.0]), ("₹300 Min order", [300.0]), ("Diet Coke 2 L", [2.0])])
def test_a_suffix_may_follow_a_gap_and_scales_where_it_means_money(text, expected):
    assert gate.numbers(text) == expected


def test_a_total_with_a_spaced_suffix_is_read_whole():
    found = gate.facts(fake_page(text="Cart\nTo pay\n₹\n1.5\nL\nPay"), [])
    assert found["total"] == "₹ 1.5 L" and gate.amount(found["total"]) == 150000.0
    assert gate.amount("To pay Rs 1.5 L") == 150000.0 and gate.amount("₹ 2.5 Mn") == 2500000.0


@pytest.mark.parametrize("text, expected", [
    ("1.5\nL", "1.5 L"), ("To pay\n₹\n1.5\nL", "To pay\n₹ 1.5 L"), ("₹ 1.5\nL", "₹ 1.5 L"), ("2\nMn", "2 Mn"),
    ("2\nbn", "2 bn"), ("3\nCr", "3 Cr"), ("2\nlakh", "2 lakh"), ("2\ncrore", "2 crore"), ("2\nmillion", "2 million"),
    ("2\nbillion", "2 billion"), ("Price 1,499\n/-", "Price 1,499 /-"), ("Size\nL", "Size\nL"),
    ("Qty 2\nLarge", "Qty 2\nLarge"), ("L\n2", "L\n2")])
def test_flat_joins_a_suffix_line_to_the_number_before_it(text, expected):
    assert gate.flat(text) == expected and gate.flat(gate.flat(text)) == gate.flat(text)


@pytest.mark.parametrize("text, expected", [
    ("Fare 249 · 4 mins away", [249.0]), ("To pay 1,299 · Arriving in 10 mins", [1299.0]),
    ("Amount due 1,299 by 10:30", [1299.0]), ("Pay within 04:59", []), ("Time left 07:42", []),
    ("Offer ends 04:59:12", []), ("Only 3 left", []), ("ETA 9 min", []), ("Delivery in 10 mins", []),
    ("Expires in 12 mins", []), ("Ready in 2 hrs 5 secs", []), ("Total ₹500 (offer ends 04:59)", [500.0]),
    ("Total ₹79 · delivery in 10 minutes", [79.0]), ("Total ₹79 expiring soon", [79.0]), ("Fare 249 by 8:30", [249.0])])
def test_only_the_ticking_tokens_are_stripped_from_a_line(text, expected):
    assert gate.numbers(text) == expected


@pytest.mark.parametrize("text", [
    "Fare 249 · 4 mins away", "To pay 1,299 · Arriving in 10 mins", "Amount due 1,299 by 10:30",
    "Total ₹79\nTo pay 1,299 · delivery in 10 mins", "Total ₹79\nFare 249 · 4 mins away"])
def test_a_price_beside_a_countdown_still_counts(text):
    assert gate.shows_money(text) and gate.worst(text) >= 249.0
    w, page = cart(text, 100.0)
    assert gate.preauthorised(w.state, gate.facts(page, []), page) is False
    nocap, nopage = cart(text, None)
    assert gate.preauthorised(nocap.state, gate.facts(nopage, []), nopage) is False


@pytest.mark.parametrize("text, expected", [
    ("Total ₹79\nTo pay\n₹50 cashback applied\n₹1,299", "₹1,299"),
    ("Total ₹79\nGrand total\n(₹50 cashback applied)\n₹1,299", "₹1,299"),
    ("Total ₹79\nTo pay\nIncl. all taxes\n₹1,299", "₹1,299"),
    ("Total ₹79\nTo pay\n₹1,299 cashback applied\n₹50", None),          # the turned-away price is the larger
    ("Total ₹79\nGrand total · you saved\n(₹50)\n₹1,299", None),        # a turned-away line with no price of its own
    ("To pay\nYou save ₹200", None), ("To pay\nPay now", None),
    ("Total ₹79\nTo pay\nYou save ₹30\n₹49", "₹49")])
def test_a_remark_between_a_payable_label_and_its_price_hides_nothing(text, expected):
    assert gate.facts(fake_page(text=text), [])["total"] == expected


def test_a_split_remark_page_is_not_preauthorised_on_the_smaller_total():
    for text in ("Total ₹79\nTo pay\n₹50 cashback applied\n₹1,299",
                 "Total ₹79\nGrand total\n(₹50 cashback applied)\n₹1,299",
                 "Total ₹79\nTo pay\nIncl. all taxes\n₹1,299"):
        w, page = cart(text, 100.0)
        assert gate.preauthorised(w.state, gate.facts(page, []), page) is False


@pytest.mark.parametrize("text", [
    "Total ₹79\nTotal 1,299 pcs", "Total ₹79\nPrice 1,299 m", "Total ₹79\nAmount 1,299 L", "Total ₹79\nTotal 1.2 M",
    "Total ₹79\nFee 1,299 ml", "Total ₹79\nCost 1,299 kg"])
def test_a_unit_does_not_exempt_a_number_on_a_money_word_line(text):
    assert gate.worst(text) > 100.0
    w, page = cart(text, 100.0)
    assert gate.preauthorised(w.state, gate.facts(page, []), page) is False


@pytest.mark.parametrize("text", [
    "Diet Coke 300 ml\nTotal ₹79", "Diet Coke 300 ml\nTotal ₹79\nPay ₹79", "Total ₹79\nDiet Coke 2 L",
    "Diet Coke 2L ₹40\nTo pay ₹40", "Pepsi 500 g\nHandling fee ₹5\nTo pay ₹45"])
def test_a_size_on_a_line_beside_the_total_does_not_block_a_small_order(text):
    w, page = cart(text, 100.0)
    assert gate.preauthorised(w.state, gate.facts(page, []), page) is True


def test_fees_on_a_split_layout_are_not_invented():
    split = fake_page(text="Cart\nHandling fee\n₹\n5\nTo pay\n₹\n79\nPay")
    assert gate.facts(split, [])["fees"] == []
    assert gate.facts(fake_page(text="Cart\nHandling fee ₹5\nTo pay ₹79\nPay"), [])["fees"] == ["Handling fee ₹5"]


# Fix round 5, probes of its own work: split numbers, a price before "mins", thousand, "to be paid".

@pytest.mark.parametrize("text, expected", [
    ("₹\n1\n,\n299", "₹ 1,299"), ("₹\n1,299\n.\n00", "₹ 1,299.00"), ("1,\n299", "1,299"), ("1\n,299", "1,299"),
    ("79.\n50", "79.50"), ("Qty 1\n,\nOff", "Qty 1\n,\nOff"), ("Total\n,", "Total\n,"), ("₹1 299", "₹1,299"),
    ("a b", "a b"), ("Total 79.\n50 off", "Total 79.\n50 off"), ("Total\n1\n,", "Total\n1\n,")])
def test_flat_puts_a_number_split_by_the_page_back_together(text, expected):
    assert gate.flat(text) == expected and gate.flat(gate.flat(text)) == gate.flat(text)


@pytest.mark.parametrize("text, expected", [
    ("To pay ₹1,299 left", [1299.0]), ("₹1,299 mins", [1299.0]), ("₹4 mins", [4.0]), ("₹15 thousand", [15000.0]),
    ("₹300 Min order", [300.0]), ("Total ₹79 · 2 hrs", [79.0]), ("₹1,299 hours left", [1299.0]),
    ("Amount due 299 left", [299.0]), ("Only 3 left", []), ("Fare 249 · 4 mins away", [249.0])])
def test_a_price_is_never_cut_out_as_a_countdown(text, expected):
    assert gate.numbers(text) == expected


def test_a_total_split_over_several_text_nodes_is_not_preauthorised_and_the_yes_still_holds(monkeypatch):
    text = "Checkout\nNet payable\n₹\n1\n,\n299\nPay"
    w, page = cart(text, 100.0)
    assert gate.facts(page, [])["total"] == "₹ 1,299" and gate.worst(text) == 1299.0
    assert gate.preauthorised(w.state, gate.facts(page, []), page) is False
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹1,299"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "yes"}])
    with pytest.raises(StalePage):
        gate.approved(w, page, control("Pay", node=11), "Pay", None)
    assert w.state["approval"]["amount"] == ""  # the raw page spells the amount in parts: the snapshot guards it
    assert gate.approved(w, page, control("Pay", node=11), "Pay", None) is True and len(w.asked) == 1
    changed = fake_page(text="Checkout\nNet payable\n₹\n1\n,\n999\nPay")
    w.replies.append({"text": "change it"})
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹1,999"])
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, changed, control("Pay", node=11), "Pay", None)
    assert len(w.asked) == 2


@pytest.mark.parametrize("text, expected", [
    ("Total ₹79\nAmount to be paid ₹1,299", "₹1,299"), ("Total ₹79\nYou will pay ₹1,299", "₹1,299"),
    ("Total ₹79\nFinal amount ₹1,299", "₹1,299"), ("Net amount\n₹\n99", "₹ 99")])
def test_more_ways_to_say_what_is_payable(text, expected):
    assert gate.facts(fake_page(text=text), [])["total"] == expected


def test_zero_width_characters_hide_no_price():
    assert gate.flat("₹\u200b1,299") == "₹1,299"
    assert gate.facts(fake_page(text="To pay ₹\u200b1,299"), [])["total"] == "₹1,299"


# Fix round 1 of Task 11: a control found by sight is held to the same gate, and its yes covers only its spot.

SPOT = {"id": "look", "kind": "look", "node": None, "label": "Orange button", "point": [237.5, 337.5]}


def test_a_control_found_by_sight_commits_when_it_reads_as_a_commit_or_jev_confirms_the_looks_flag(monkeypatch):
    page = fake_page()
    calls = jev_says(monkeypatch, ("final", 0.9))
    assert gate.commit_label(Fake(page), "LOOK", {**SPOT, "commits": True}, page) == "Orange button"
    jev_says(monkeypatch, ("step", 0.9))  # live bookmyshow: seat squares the look flagged were asked about as the order
    assert gate.commit_label(Fake(page), "LOOK", {**SPOT, "label": "Seats 03 04", "commits": True}, page) is None
    assert calls == ["Orange button"]
    assert gate.commit_label(Fake(page), "LOOK", {**SPOT, "text": "Place order"}, page) == "Orange button"
    assert gate.commit_label(Fake(page), "LOOK", {**SPOT, "text": "Seat 12A"}, page) is None


def test_a_hover_found_by_sight_is_never_a_commit():
    page = fake_page(text="Buy now")
    hover = {**SPOT, "label": "Buy now", "text": "Buy now", "point": [1, 2], "hover": True}
    assert gate.commit_label(Fake(page), "LOOK", hover, page) is None
    assert gate.commit_label(Fake(page), "LOOK", {**hover, "commits": True}, page) is None
    assert gate.commit_label(Fake(page), "LOOK", {**hover, "hover": False}, page) == "Buy now"  # a click would


def test_a_spot_found_by_sight_matches_only_its_own_point_and_a_listed_control_only_its_own_node():
    page = fake_page()
    spot = {**SPOT, "label": "Pay ₹79"}
    state = Fake(page).state
    gate.bind(state, spot, "Pay ₹79", "₹79", page, "user")
    approval = state["approval"]
    assert approval["look"] == {"point": [237.5, 337.5]} and approval["target"] is None
    assert gate.matches(approval, spot, "Pay ₹79")
    assert not gate.matches(approval, {**spot, "point": [10, 10]}, "Pay ₹79")  # the same words, another spot
    assert not gate.matches(approval, PAY, "Pay ₹79")  # the page's own button with the same label
    gate.bind(state, PAY, "Pay ₹79", "₹79", page, "user")
    assert gate.matches(state["approval"], PAY, "Pay ₹79") and not gate.matches(state["approval"], spot, "Pay ₹79")


# Final review, Group A: two tiers of commit words, the pre-authorisation switch, and its scope.

ACT = {"do": "choose the ride", "kind": "act"}
FINAL_LABELS = [  # the bench sites' final controls: (label, matched outright)
    ("Book Bike", True), ("Book Ride", True), ("Book Cab", True), ("Book Auto", True), ("Book Ola Mini", False),
    ("Confirm UberGo", False), ("Request Uber Go AC", True), ("End membership", True), ("Cancel Membership", True),
    ("Continue to cancel", False), ("Make Payment", True), ("Submit", False), ("Submit order", True),
    ("Send message", False), ("Reserve", False), ("Recharge Now", True), ("Proceed to Recharge", False),
    ("Order Now", True), ("Proceed to Pay", False), ("Pay ₹79", True), ("Place order", True),
    ("[secret:answer1] ₹79", False)]


def jev_says(monkeypatch, verdict):
    calls = []
    monkeypatch.setattr(gate.jev, "final", lambda page, label, goal: calls.append(label) or verdict)
    return calls


@pytest.mark.parametrize("label, outright", FINAL_LABELS)
def test_a_final_control_commits_in_an_act_subgoal_unless_jev_is_sure_a_maybe_one_is_a_step(monkeypatch, label,
                                                                                         outright):
    page = fake_page(text="Bike ₹45 · 3 min\nAuto ₹80")
    for verdict in [("final", 0.9), ("final", 0.5), ("step", 0.5), ("step", 0.79)]:
        jev_says(monkeypatch, verdict)
        w = Fake(page)
        assert gate.commit_label(w, "CLICK", control(label), page, subgoal=ACT) == label, verdict
        assert w.state.get("cleared") is None
    calls = jev_says(monkeypatch, ("step", 0.8))
    w = Fake(page)
    assert gate.commit_label(w, "CLICK", control(label), page, subgoal=ACT) == (label if outright else None)
    assert w.state.get("cleared") == (None if outright else label)  # kept for the bench's audit
    assert calls == ([] if outright else [label])  # Jev is never asked about an outright commit


@pytest.mark.parametrize("label", ["Add to cart", "Search", "Continue", "Verify", "Send OTP"])
def test_ordinary_controls_are_not_commits_and_jev_is_not_asked(monkeypatch, label):
    page = fake_page()
    calls = jev_says(monkeypatch, ("final", 1.0))
    assert gate.commit_label(Fake(page), "CLICK", control(label), page, subgoal=ACT) is None and calls == []


def test_double_click_enter_and_a_dialog_use_the_same_two_tiers(monkeypatch):
    message = {"id": "m", "kind": "fill", "label": "Message", "role": "textbox", "value": "", "node": 30,
               "submit": "Send message"}
    page = fake_page(text="Chat with the host\nMessage", actions=[message, control("Book Ola Mini")])
    page_key = {"id": "key", "kind": "key", "node": None, "label": "The page"}
    dialog = fake_page(text="Browser confirm dialog: Confirm UberGo for ₹212?")
    ok = {"id": "accept_dialog", "kind": "accept_dialog", "node": None, "label": "OK"}
    for verdict, committed in [(("step", 0.5), True), (("final", 0.9), True), (("step", 0.9), False)]:
        jev_says(monkeypatch, verdict)
        w = Fake(page)
        w.state["browser"].focused_label = Mock(return_value="Message")
        assert gate.commit_label(w, "DOUBLE_CLICK", control("Book Ola Mini"), page, subgoal=ACT) == \
            ("Book Ola Mini" if committed else None)
        assert gate.commit_label(w, "PRESS_KEY", message, page, key="Enter", subgoal=ACT) == \
            ("Send message" if committed else None)
        assert gate.commit_label(w, "PRESS_KEY", page_key, page, key="Enter", subgoal=ACT) == \
            ("Send message" if committed else None)  # Enter on the page submits the focused field's form
        assert gate.commit_label(Fake(dialog), "ACCEPT_DIALOG", ok, dialog, subgoal=ACT) == \
            (dialog["text"] if committed else None)
    jev_says(monkeypatch, ("step", 1.0))  # an outright commit is never cleared, whatever the operation
    assert gate.commit_label(Fake(page), "DOUBLE_CLICK", control("Book Bike"), page) == "Book Bike"
    pay = fake_page(text="Browser confirm dialog: Pay ₹212 now?")
    assert gate.commit_label(Fake(pay), "ACCEPT_DIALOG", ok, pay) == pay["text"]


def test_a_commit_subgoal_still_confirms_a_control_no_rule_names(monkeypatch):
    page = fake_page()
    jev_says(monkeypatch, ("final", 0.5))
    assert gate.commit_label(Fake(page), "CLICK", control("Go"), page, subgoal={"do": "x", "kind": "commit"}) == "Go"
    assert gate.commit_label(Fake(page), "CLICK", control("Go"), page, subgoal=ACT) is None


@pytest.fixture
def shadow(monkeypatch):
    monkeypatch.delenv("JEV_PREAUTH", raising=False)


def test_preauthorisation_is_only_shadowed_until_it_is_switched_on(monkeypatch, shadow):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "no"}], preauth=True, cap=100.0)
    with pytest.raises(Halt, match="Nothing was charged"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert len(w.asked) == 1 and w.said == [] and w.state["preauth_shadow"] is True
    assert w.asked[0]["text"].endswith("\n(You said not to ask. I'm asking anyway until pre-approval is switched on: "
                                       "set JEV_PREAUTH=on.)")
    plain = Fake(page, replies=[{"text": "change it"}], preauth=True, cap=50.0)  # over the cap: an ordinary question
    with pytest.raises(Retry):
        gate.approved(plain, page, PAY, "Pay ₹79", None)
    assert "JEV_PREAUTH" not in plain.asked[0]["text"] and "preauth_shadow" not in plain.state
    monkeypatch.setenv("JEV_PREAUTH", "on")
    on = Fake(page, preauth=True, cap=100.0)
    assert gate.approved(on, page, PAY, "Pay ₹79", None) is True and on.asked == []


def test_a_bench_run_asks_even_with_preauthorisation_switched_on(monkeypatch):
    monkeypatch.setenv("JEV_PREAUTH", "on")
    page = fake_page()
    summary(monkeypatch, ["Diet Coke", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "change it"}], preauth=True, cap=100.0)
    w.bench = True
    with pytest.raises(Retry):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert w.state["bench_preauth"] is True and len(w.asked) == 1 and "JEV_PREAUTH" not in w.asked[0]["text"]


@pytest.mark.parametrize("text", ["Checkout\nTo pay\nIncl. all taxes\n1,299\nPlace order", "Pizza order\nSubmit order"])
def test_a_commerce_goal_with_no_cap_is_never_preauthorised(text):
    page = fake_page(text=text)
    state = {**Fake(page).state, "preauth": True, "cap": None, "goal": "Order a medium pizza, don't ask"}
    assert gate.preauthorised(state, gate.facts(page, []), page) is False
    if text == "Pizza order\nSubmit order":  # a goal that buys nothing: a form with no money needs no cap
        form = {**state, "goal": "Send the feedback form on shop.test with my comments, don't ask"}
        assert gate.preauthorised(form, gate.facts(page, []), page) is True


def test_a_checkout_step_beside_a_payment_method_is_not_cleared_as_an_outright_commit(monkeypatch):
    """The checkout-step exception lives in the maybe tier now; the outright tier keeps only the payment-method one."""
    page = fake_page()
    jev_says(monkeypatch, ("step", 1.0))
    for label in ["Amazon Pay ₹79 · proceed to checkout", "Amazon Pay · proceed to pay"]:
        assert gate.commit_label(Fake(page), "CLICK", control(label), page, subgoal=ACT) == label
    assert gate.commit_label(Fake(page), "CLICK", control("Amazon Pay"), page, subgoal=ACT) is None  # a method
    assert gate.commit_label(Fake(page), "CLICK", control("Proceed to Pay"), page, subgoal=ACT) is None  # a step


# Live bench B: the approval is asked on the right control, at the right time, and only when the goal commits.

MAKE_PAYMENT = {"id": "mp", "kind": "click", "role": "button", "node": 61, "name": "Make payment, tap to go to payment",
                "shown": "Proceed to Pay", "label": "Make payment, tap to go to payment (shows “Proceed to Pay”)"}


def test_a_control_that_shows_a_checkout_step_is_a_maybe_commit_whatever_its_hidden_name_says(monkeypatch):
    page = fake_page(text=INSTAMART)
    calls = jev_says(monkeypatch, ("step", 0.9))
    w = Fake(page)
    assert gate.commit_label(w, "CLICK", MAKE_PAYMENT, page, subgoal=ACT) is None
    assert calls == ["Make payment, tap to go to payment"] and w.state["cleared"] == calls[0]
    for verdict in [("final", 0.9), ("step", 0.79)]:  # unsure: the approval names the control
        jev_says(monkeypatch, verdict)
        assert gate.commit_label(Fake(page), "CLICK", MAKE_PAYMENT, page, subgoal=ACT) == calls[0]


@pytest.mark.parametrize("name, shown", [("Pay ₹121", "Proceed to Pay"), ("Make payment", "Pay now"),
                                         ("Make payment", "Proceed to Pay ₹121"), ("Place order", "Place order")])
def test_a_control_with_a_price_or_a_final_word_on_show_stays_an_outright_commit(monkeypatch, name, shown):
    page = fake_page(text=INSTAMART)
    calls = jev_says(monkeypatch, ("step", 1.0))
    control_ = {**MAKE_PAYMENT, "name": name, "shown": shown, "label": f"{name} (shows “{shown}”)"}
    assert gate.commit_label(Fake(page), "CLICK", control_, page, subgoal=ACT) == name and calls == []


MOVIE = "Drishyam The Conclusion\nUA16+ · Hindi\n2h 30m · Drama, Thriller\nAbout the movie\nBook tickets"


def test_booking_words_on_a_page_with_no_money_are_a_maybe_commit(monkeypatch):
    page = fake_page(text=MOVIE)
    calls = jev_says(monkeypatch, ("step", 0.9))
    for label in ["Book tickets", "Book", "Buy", "Reserve"]:
        assert gate.commit_label(Fake(page), "CLICK", control(label), page, subgoal=ACT) is None, label
    assert calls == ["Book tickets", "Book", "Buy", "Reserve"]
    jev_says(monkeypatch, ("final", 0.9))
    assert gate.commit_label(Fake(page), "CLICK", control("Book tickets"), page, subgoal=ACT) == "Book tickets"


@pytest.mark.parametrize("text, label", [
    (MOVIE + "\nTickets from ₹250", "Book tickets"), (MOVIE + "\nPrice 250", "Book tickets"),
    (MOVIE, "Book tickets ₹500"), (MOVIE, "Book Ride"), (MOVIE, "Pay"), (MOVIE, "Place order"),
    (MOVIE, "Confirm payment"), (MOVIE, "Buy now")])
def test_a_page_with_money_or_a_control_that_pays_keeps_booking_words_outright(monkeypatch, text, label):
    page = fake_page(text=text)
    calls = jev_says(monkeypatch, ("step", 1.0))
    assert gate.commit_label(Fake(page), "CLICK", control(label), page, subgoal=ACT) == label and calls == []


BANNER = ("img1 Sign up/Login now to Lock Flight Prices & Pay Later Book Hotels @ ₹0 Get 3X refund, if your "
          "waitlisted train doesn’t get confirmed")
HOTEL = "Find the cheapest hotel in Goa rated above 4 for 2 adults next Saturday to Sunday on MakeMyTrip"


def test_a_zero_amount_is_no_price_so_a_pay_later_banner_is_left_to_jev(monkeypatch):
    page = fake_page(text="Hotels\nGoa\nSearch")
    jev_says(monkeypatch, ("step", 0.9))
    assert gate.commit_label(Fake(page), "CLICK", control(BANNER), page, subgoal=ACT) is None
    jev_says(monkeypatch, ("step", 1.0))
    priced = "Amazon Pay ₹10 cashback"  # a payment method beside a real price stays outright
    assert gate.commit_label(Fake(page), "CLICK", control(priced), page, subgoal=ACT) == priced


@pytest.mark.parametrize("label, verdict", [(BANNER, ("final", 0.9)), ("Pay ₹79", ("step", 1.0)),
                                            ("Book now", ("final", 0.9)), ("Reserve", ("step", 0.5))])
def test_a_goal_that_only_looks_things_up_never_asks_to_commit_it_is_told_to_read_and_report(monkeypatch, label,
                                                                                            verdict):
    page = fake_page()
    jev_says(monkeypatch, verdict)
    w = Fake(page)
    w.state["goal"], w.state["skill"] = HOTEL, "travel"
    with pytest.raises(Retry, match="doesn't buy or book anything"):
        gate.commit_label(w, "CLICK", control(label), page, subgoal=ACT)
    assert w.asked == []


def test_a_lookup_still_runs_a_step_jev_clears_and_a_commit_subgoal_is_refused_too(monkeypatch):
    page = fake_page()
    w = Fake(page)
    w.state["goal"] = "Compare the price of the Apple iPhone 16 128 GB on Flipkart and Amazon.in"
    jev_says(monkeypatch, ("step", 0.9))
    assert gate.commit_label(w, "CLICK", control("Reserve"), page, subgoal=ACT) is None
    jev_says(monkeypatch, ("final", 0.9))
    with pytest.raises(Retry, match="doesn't buy or book anything"):
        gate.commit_label(w, "CLICK", control("Go"), page, subgoal={"do": "x", "kind": "commit"})


def test_a_lookup_that_signs_in_is_still_asked_about_a_code_step_jev_is_unsure_of(monkeypatch):
    page = fake_page()
    w = Fake(page)
    w.state["goal"] = "Where is my latest Flipkart order?"
    jev_says(monkeypatch, ("step", 0.5))
    assert gate.commit_label(w, "CLICK", control("Request OTP"), page, subgoal=ACT) == "Request OTP"


@pytest.mark.parametrize("goal, label", [
    ("Fill the web form at https://www.selenium.dev/selenium/web/web-form.html with the text Hello and submit it",
     "Submit"), ("Cancel my latest undelivered Flipkart order", "Cancel order"),
    ("Order one Diet Coke 300 ml can on Swiggy Instamart", "Pay ₹79")])
def test_a_goal_that_submits_cancels_or_orders_is_still_asked(monkeypatch, goal, label):
    page = fake_page()
    jev_says(monkeypatch, ("final", 0.9))
    w = Fake(page)
    w.state["goal"] = goal
    assert gate.commit_label(w, "CLICK", control(label), page, subgoal=ACT) == label


def test_the_delivery_address_is_read_from_a_control_when_the_text_has_none():
    selected = control("button in “Selected address: WORK, Ground floor, Bhive Workspace, Service Rd, Sector 6, HSR”")
    here = control("Deliver to this address", node=62)
    page = fake_page(text=INSTAMART, actions=[here, selected, MAKE_PAYMENT])
    assert gate.facts(page, [])["address"] == "WORK, Ground floor, Bhive Workspace, Service Rd, Sector 6, HSR"
    texted = fake_page(text="Delivering to: Home, 12 MG Road, Bengaluru\n" + INSTAMART, actions=[selected])
    assert gate.facts(texted, [])["address"].startswith("Home, 12 MG Road")  # the page's own text first
    assert gate.facts(fake_page(text=INSTAMART, actions=[here]), [])["address"] is None


def test_an_address_read_from_a_control_reaches_the_approval(monkeypatch):
    selected = control("button in “Selected address: WORK, Ground floor, Bhive Workspace, Service Rd, Sector 6, HSR”")
    page = fake_page(text=INSTAMART, actions=[selected, MAKE_PAYMENT])
    summary(monkeypatch, ["1 Diet Coke 300 ml can", "No saved delivery address provided", "Total ₹121"])
    lines = gate.summary(Fake(page), page, gate.facts(page, []))
    assert "Delivering to: WORK, Ground floor, Bhive Workspace, Service Rd, Sector 6, HSR" in lines
    assert not any("provided" in line for line in lines)


def test_a_summary_line_the_page_gave_no_fact_for_is_dropped(monkeypatch):
    page = fake_page(text=INSTAMART)
    summary(monkeypatch, ["1 Diet Coke 300 ml can", "No payment method provided", "form's name: None",
                          "Seller: N/A", "Coupon: null", "Delivery slot: unknown", "Payment method: not provided",
                          "Total ₹121"])
    assert gate.summary(Fake(page), page, gate.facts(page, []))[:2] == ["1 Diet Coke 300 ml can", "Total ₹121"]
    summary(monkeypatch, ["Diet Coke 300 ml × 1 · ₹50 (seller: N/A)", "Total ₹121"])  # a priced line stays
    assert gate.summary(Fake(page), page, gate.facts(page, []))[0] == "Diet Coke 300 ml × 1 · ₹50 (seller: N/A)"


def test_an_amount_the_redactor_hid_still_counts_as_money():
    """A secret equal to the price ("₹499" with a CVV 499) hides the number: the page still shows money."""
    for text in ("Renew\nTo pay ₹[secret:answer1]\nPay", "Renew\nTotal\n[secret:answer1]\nPay"):
        assert gate.shows_money(text), text
        page = fake_page(text=text)
        state = {**Fake(page).state, "preauth": True, "cap": None, "goal": "Renew my plan, don't ask"}
        assert gate.preauthorised(state, gate.facts(page, []), page) is False
    assert not gate.shows_money("Hi [profile:name]\nYour plan\nRenew")


# Final review, Group D: the window below a bare money word, digit groups when rejoining, spaced thousands, EMIs.

@pytest.mark.parametrize("text, cap", [
    ("Checkout\nTo pay\nIncl. all taxes\n1,299\nPlace order", 100.0),
    ("Checkout\nTo pay\nIncl. all taxes\n1,299\nPlace order", None),
    ("Checkout\nTotal ₹79\nTo pay\n₹50 cashback applied\n1,299\nPlace order", 100.0),
    ("Checkout\nTotal ₹79\nTo pay\n1.5 L\nPlace order", 100.0),
    ("Checkout\nTotal ₹79\nTo pay\n1.5 L\nPlace order", None)])
def test_a_number_up_to_three_lines_below_a_bare_money_word_is_money(text, cap):
    assert gate.shows_money(text) and gate.worst(text) >= 1299.0
    w, page = cart(text, cap)
    w.state["goal"] = "Renew my plan, don't ask"  # no commerce word: only the money check can stop it
    assert gate.preauthorised(w.state, gate.facts(page, []), page) is False


def test_the_window_below_a_bare_money_word_is_three_lines_and_its_first_line_scales_suffixes():
    assert gate.money_lines("Cart\nTo pay\nIncl. all taxes\n1,299\nPlace order\nThanks\nBye") == \
        ["Cart", "To pay", "Incl. all taxes", "1,299", "Place order"]
    assert gate.worst("Total ₹79\nTo pay\n1.5 L") == 150000.0  # "1.5 L" right under "To pay" is money
    assert gate.worst("Total ₹79\nTo pay\nDiet Coke\n2 L") == 79.0  # a size two lines down is still a size
    assert gate.money_lines("Total ₹79\nDiet Coke\nBrand\nSize\nThanks") == ["Total ₹79", "Diet Coke"]


@pytest.mark.parametrize("text", ["Checkout\nTotal ₹79\nTo pay\n₹50 cashback applied\n1,299\nPlace order",
                                  "Checkout\nTotal ₹79\nTo pay\n1.5 L\nPlace order"])
def test_a_bare_number_below_a_payable_label_that_is_larger_than_the_total_leaves_the_total_unknown(text):
    assert gate.facts(fake_page(text=text), [])["total"] is None


@pytest.mark.parametrize("text, expected", [
    ("Total ₹79.\n1299", "Total ₹79.\n1299"), ("Total ₹79\n.\n1,299", "Total ₹79\n1,299"),  # a stray dot goes
    ("Total ₹79\n.\n1299", "Total ₹79\n1299"), ("Save ₹20.\n499\nTo pay", "Save ₹20.\n499\nTo pay"),
    ("Total ₹79\n,\n1299", "Total ₹79\n1299"), ("₹799\n,\n29", "₹799\n29"), ("₹79\n.5\n0", "₹79.5\n0"),
    ("Qty 1\n,\nOff", "Qty 1\n,\nOff"),
    ("₹\n2\n,\n50\n,\n000", "₹ 2,50,000"), ("₹2\n,50,000", "₹2,50,000"), ("₹1,299\n.\n00", "₹1,299.00"),
    ("₹79.\n50", "₹79.50"), ("₹1,\n299", "₹1,299")])
def test_flat_rejoins_only_digit_groups_a_number_can_have(text, expected):
    assert gate.flat(text) == expected and gate.flat(gate.flat(text)) == gate.flat(text)


@pytest.mark.parametrize("text", ["Checkout\nTotal ₹79.\n1299\nPlace order",
                                  "Checkout\nTotal ₹79\n.\n1,299\nPlace order", "Checkout\nSave ₹20.\n499\nTo pay"])
def test_a_dot_never_makes_a_large_number_into_decimals(text):
    assert gate.worst(text) >= 499.0
    w, page = cart(text, 100.0)
    assert gate.preauthorised(w.state, gate.facts(page, []), page) is False


@pytest.mark.parametrize("text, expected", [
    ("To pay ₹1 299", "To pay ₹1,299"), ("To pay Rs 1 299", "To pay Rs 1,299"),
    ("To pay Rs. 1 299", "To pay Rs. 1,299"),
    ("To pay INR 12 345", "To pay INR 12,345"), ("To pay ₹1\u2007299", "To pay ₹1,299"),
    ("To pay 1\u2007299", "To pay 1,299"), ("Qty 2 300 ml", "Qty 2 300 ml"), ("₹1 2999", "₹1 2999")])
def test_thousands_spaced_after_a_currency_marker_or_by_a_figure_space_are_one_number(text, expected):
    assert gate.flat(text) == expected


@pytest.mark.parametrize("text", ["Checkout\nTo pay ₹1 299\nPlace order", "Checkout\nTo pay Rs 1 299\nPlace order",
                                  "Checkout\nTo pay ₹1\u2007299\nPlace order"])
def test_a_spaced_1299_order_is_not_preauthorised_under_500(text):
    w, page = cart(text, 500.0)
    found = gate.facts(page, [])
    assert gate.amount(found["total"]) == 1299.0 and gate.worst(text) == 1299.0
    assert gate.preauthorised(w.state, found, page) is False


def test_an_emi_line_is_not_the_payable_total():
    text = "Checkout\nGrand total ₹1,299\nPay in 3 EMIs: you pay ₹433 now\nPlace order"
    assert gate.facts(fake_page(text=text), [])["total"] == "₹1,299"
    w, page = cart(text, 500.0)
    assert gate.preauthorised(w.state, gate.facts(page, []), page) is False
    assert gate.money_lines(text)[2] == "Pay in 3 EMIs: you pay ₹433 now"  # still a money line


def test_a_cvv_equal_to_a_spaced_price_group_never_makes_the_payable_pre_approved(monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_PREAUTH", "on")
    w = worker_with(tmp_path)
    w.secrets.add("299", "[secret:answer1]")  # a CVV the user typed: no model, trace, or message may carry it
    page = w.clean(fake_page(text="To pay ₹1 299\nPay"))
    assert "₹1 299" in page["text"] and "secret" not in page["text"]
    found = gate.facts(page, [])
    assert found["total"] in (None, "₹1,299", "₹1 299")
    state = {**w.state, "preauth": True, "cap": 500.0, "site": "shop.test"}
    assert gate.preauthorised(state, found, page) is False


# ---- Plan 7 Task 5: cart lines and standing approvals

SHOP = "Checkout\nDiet Coke 300 ml × 1 — ₹40\nDelivery ₹39\nTotal ₹79\nPay ₹79"
COKE = [{"item": "diet coke 300 ml", "qty": 1}]


def test_cart_reads_items_and_quantities():
    text = ("Checkout\nDiet Coke 300 ml × 2 — ₹80\n2 x Atta 1 kg ₹60\nAmul Taaza 500 ml\nQty: 1\nBox x 200ml\nClose ×\n"
            "Delivery ₹39\nTotal ₹179\nDiet Coke 300 ml × 2 — ₹80\nLux Soap x2")
    assert gate.cart(text) == [{"item": "diet coke 300 ml", "qty": 2}, {"item": "atta 1 kg", "qty": 2},
                               {"item": "amul taaza 500 ml", "qty": 1}, {"item": "lux soap", "qty": 2}]
    assert gate.cart(fake_page()["text"]) == []  # no quantities shown: no cart, so no rule can apply
    assert gate.same_cart(COKE + [{"item": "atta 1 kg", "qty": 2}], [{"item": "atta 1 kg", "qty": 2}] + COKE)
    assert not gate.same_cart(COKE, [{"item": "diet coke 300 ml", "qty": 2}])


def test_a_standing_approvals_cap_is_the_total_and_a_fifth_rounded_up_to_ten_rupees():
    assert (gate.rule_cap("₹79"), gate.rule_cap("₹245"), gate.rule_cap("₹125"), gate.rule_cap("$79")) == \
        (100.0, 300.0, 150.0, None)


def with_rule(monkeypatch, memory, *, text=SHOP, site="shop.test", qty=1, limit=100, days=30, mode="on", **fake):
    monkeypatch.setenv("JEV_PREAUTH", mode)
    rule = memory.approve(site, [{"item": "diet coke 300 ml", "qty": qty}], limit, "Don't ask next time", days=days)
    page = fake_page(text=text)
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "change it"}], **fake)  # not a yes, and not a plain no that ends it
    w.memory = memory
    return w, page, rule


def test_a_standing_approval_places_the_usual_order_and_says_so(monkeypatch, memory):
    w, page, rule = with_rule(monkeypatch, memory)
    assert gate.approved(w, page, PAY, "Pay ₹79", None) is True
    said = w.said[0]
    assert said.startswith("Placing your usual shop.test order (₹79) under your ₹100 rule (until ")
    assert said.endswith("Say “stop that rule” to end it.") and w.asked == []
    assert w.state["approval"]["by"] == "standing" and w.state["approval"]["rule"] == rule
    assert w.state["approval"]["cart"] == COKE and memory.live_rules()[0]["uses"] == 1


@pytest.mark.parametrize("change", ["another site", "another cart", "over the cap", "a money line over the cap",
                                    "expired", "revoked", "Jev says it doesn't match"])
def test_a_standing_approval_covers_only_its_own_site_cart_and_cap_while_it_is_live(monkeypatch, memory, change):
    options = {"another site": {"site": "other.test"}, "another cart": {"qty": 2}, "over the cap": {"limit": 78},
               "a money line over the cap": {"text": SHOP + "\nConvenience fee ₹150"}, "expired": {"days": 0}}
    w, page, rule = with_rule(monkeypatch, memory, **options.get(change, {}))
    if change == "revoked":
        memory.forget(f"rule:{rule}")
    if change == "Jev says it doesn't match":
        checks(monkeypatch, matches="no")
        w.state["mismatches"] = 2  # the summary goes to the user after two mismatches
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert len(w.asked) == 1 and not any(line.startswith("Placing") for line in w.said)


def test_a_bench_run_only_logs_a_standing_approval(monkeypatch, memory):
    w, page, _ = with_rule(monkeypatch, memory)
    w.bench = True
    with pytest.raises(Retry):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert w.state["bench_standing"] is True and len(w.asked) == 1 and memory.live_rules()[0]["uses"] == 0


def test_with_pre_approval_off_a_standing_approval_is_only_reported(monkeypatch, memory):
    w, page, _ = with_rule(monkeypatch, memory, mode="shadow")
    with pytest.raises(Retry):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert "Your standing approval for this order would have covered it." in w.asked[0]["text"]
    assert w.state["standing_shadow"] is True and memory.live_rules()[0]["uses"] == 0
    assert w.state["checkout"] == {"site": "shop.test", "cart": COKE, "total": "₹79"}


def test_your_own_words_come_before_a_standing_approval(monkeypatch, memory):
    w, page, _ = with_rule(monkeypatch, memory, preauth=True, cap=100.0)
    assert gate.approved(w, page, PAY, "Pay ₹79", None) is True
    assert w.state["approval"]["by"] == "preauth" and memory.live_rules()[0]["uses"] == 0


def test_a_confirmed_order_keeps_its_site_cart_total_and_who_approved_it(monkeypatch):
    w = Fake(fake_page(text=SHOP))
    w.state["commit"] = {"label": "Pay ₹79", "since": 0, "goal": "Order a coke", "site": "shop.test", "cart": COKE,
                         "total": "₹79", "by": "user"}
    monkeypatch.setattr(gate.jev, "after_commit", lambda *a: "confirmed")
    gate.watch(w, "Pay ₹79", "Order a coke")
    assert w.state["orders"] == [{"label": "Pay ₹79", "site": "shop.test", "cart": COKE, "total": "₹79", "by": "user"}]


def test_a_cap_in_your_own_words_is_never_raised_by_a_standing_approval(monkeypatch, memory):
    w, page, _ = with_rule(monkeypatch, memory, preauth=True, cap=50.0)  # you said "under ₹50"; the rule allows ₹100
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert len(w.asked) == 1 and memory.live_rules()[0]["uses"] == 0 and w.state["approval"] is None


@pytest.mark.parametrize("broken", ["rules_for", "used"])
def test_a_standing_approval_that_cannot_be_read_or_recorded_is_not_used(monkeypatch, memory, broken):
    w, page, _ = with_rule(monkeypatch, memory)

    def fail(*args):
        raise RuntimeError("database down")

    monkeypatch.setattr(memory, broken, fail)
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert len(w.asked) == 1 and not any(line.startswith("Placing") for line in w.said)


# ---- Plan 7 Task 5 fix round 1: an item the cart reading missed never rides along under a standing approval

LAYS_QTY = "\nLays Classic 52 g\n₹20\nQty: 1"


def stray_lines():
    lays = "Lays Classic 52 g"
    return {"a quantity stepper": SHOP + f"\n{lays}\n−\n1\n+",
            "a stepper written plus first": SHOP + f"\n{lays}\n+\n1\n−",
            "a price line between an item and its Qty": SHOP + LAYS_QTY,
            "the same item on two lines": SHOP.replace("Delivery", "Diet Coke 300 ml × 1 — ₹40\nDelivery"),
            "an item count that fits nothing": SHOP + "\n3 items",
            "an upsell with a price": SHOP + "\nJoin Pass ₹99",
            "a stray priced item line": SHOP + f"\n{lays} ₹20",
            "a stray item with its price on the next line": SHOP + f"\n{lays}\n₹20",
            **{f"an item named with a bill word: {line!r}": SHOP + "\n" + line for line in [
                "Sugar Free Gold 25 Pellets ₹20", "Sugar Free Gold 25 Pellets\n₹20", "Gluten Free Bread\n₹20",
                "Lays Classic 52 g 10% off\n₹20", "Free Range Eggs ₹20", "Delivery Pack ₹20",
                "Cotton Tips 100 pcs\n₹20", "Total Whey Protein Bar\n₹20", "Rain Poncho Kids ₹20",
                "Service Station Wipes\n₹20", "MRP Saver Pack ₹20", "Packing Tape 2 rolls ₹20"]}}


@pytest.mark.parametrize("probe", list(stray_lines()))
def test_a_standing_approval_never_covers_a_cart_with_something_the_reading_missed(monkeypatch, memory, probe):
    w, page, _ = with_rule(monkeypatch, memory, text=stray_lines()[probe])
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert len(w.asked) == 1 and not any(line.startswith("Placing") for line in w.said)
    assert memory.live_rules()[0]["uses"] == 0 and w.state["approval"] is None


def test_a_qty_line_reads_the_item_above_its_price_and_a_matching_count_still_applies(monkeypatch, memory):
    assert gate.cart(SHOP + LAYS_QTY) == COKE + [{"item": "lays classic 52 g", "qty": 1}]
    w, page, _ = with_rule(monkeypatch, memory, text=SHOP + "\n1 item")  # a count equal to the cart is no hint
    assert gate.approved(w, page, PAY, "Pay ₹79", None) is True


def test_the_same_item_on_two_lines_is_not_one_item(monkeypatch, memory):
    text = SHOP.replace("Delivery", "Diet Coke 300 ml × 1 — ₹40\nDelivery")
    w, page, _ = with_rule(monkeypatch, memory, text=text)
    assert gate.standing(w, {"total": "₹79"}, page) is None
    w, page, _ = with_rule(monkeypatch, memory, text=SHOP)
    assert gate.standing(w, {"total": "₹79"}, page)["cart"] == COKE


def test_a_standing_approval_revoked_before_it_is_counted_is_not_used(monkeypatch, memory):
    w, page, rule = with_rule(monkeypatch, memory)
    real = memory.used

    def revoked_first(rule_id):
        memory.forget(f"rule:{rule}")  # between reading the rule and counting its use
        return real(rule_id)

    monkeypatch.setattr(memory, "used", revoked_first)
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert len(w.asked) == 1 and not any(line.startswith("Placing") for line in w.said)
    assert w.state["approval"] is None


@pytest.mark.parametrize("broken", ["rules_for", "used"])
def test_a_standing_approval_error_is_asked_about_and_kept_for_the_trace(monkeypatch, memory, broken):
    w, page, _ = with_rule(monkeypatch, memory)
    monkeypatch.setattr(memory, broken, lambda *a: (_ for _ in ()).throw(RuntimeError("database down")))
    with pytest.raises(Retry):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert w.state["standing_error"] == "database down" and w.said == []


BILL = ("Checkout\nDiet Coke 300 ml × 1 — ₹40\nItem total ₹40\nDelivery fee ₹30\nHandling charge ₹5\nPlatform fee ₹4\n"
        "GST ₹0\nYou saved ₹10 on this order\nTo pay ₹79\nPay ₹79")


@pytest.mark.parametrize("text", [BILL, BILL.replace("Delivery fee ₹30", "Delivery fee\n₹30")])
def test_a_realistic_bill_of_fees_taxes_and_savings_still_lets_the_rule_apply(monkeypatch, memory, text):
    w, page, _ = with_rule(monkeypatch, memory, text=text)
    assert gate.approved(w, page, PAY, "Pay ₹79", None) is True


def test_a_failing_rule_lookup_reaches_the_trace(tmp_path, monkeypatch, memory):
    w = worker_with(tmp_path, memory=memory)
    monkeypatch.setattr(memory, "rules_for", lambda *a: (_ for _ in ()).throw(RuntimeError("database down")))
    assert gate.standing(w, {"total": "₹79"}, fake_page(text=SHOP)) is None
    assert w.trace()["standing_error"] == "database down"


MORE_BILL = ("Checkout\nDiet Coke 300 ml × 1 — ₹40\nMRP ₹45\nSubtotal ₹40\nService fee ₹5\nPacking charge ₹3\n"
             "Rain fee ₹10\nTo pay ₹79\nPay ₹79")


@pytest.mark.parametrize("text", [MORE_BILL, MORE_BILL.replace("Service fee ₹5", "Service fee\n₹5")])
def test_service_subtotal_packing_rain_and_mrp_lines_are_bill_lines(monkeypatch, memory, text):
    assert all(gate.bill_line(line) for line in ("Service fee ₹5", "Subtotal ₹40", "Packing charge ₹3",
                                                  "Rain fee ₹10", "MRP ₹45"))
    w, page, _ = with_rule(monkeypatch, memory, text=text)
    assert gate.approved(w, page, PAY, "Pay ₹79", None) is True


# ---- live bench D1: the summary's amounts are fixed in the summary, and an accordion is no outright commit


def scripted_summary(monkeypatch, *outs):
    """planner.summary's replies in order (the last one repeats); returns the payloads it was sent."""
    calls = []
    monkeypatch.setattr(gate.planner, "summary",
                        lambda payload: calls.append(payload) or list(outs[min(len(calls), len(outs)) - 1]))
    return calls


def test_an_amount_far_from_any_the_page_shows_is_left_out_and_the_pages_total_stands(monkeypatch):
    page = fake_page()
    calls = scripted_summary(monkeypatch, ["Diet Coke ₹97", "Total ₹97"])
    assert gate.summary(Fake(page), page, gate.facts(page, [])) == ["Diet Coke", "Total: ₹79"]
    assert len(calls) == 1  # the summary model is asked once: code fixes its amounts


RIDE = ("Choose a ride\nUber Go\n4 mins away\n₹904.38\nPremier\n₹1,206.50\nHOUSE OF COMMONS\n"
        "Kempegowda International Airport Terminal-1\nCash\nRequest Uber Go")


def test_a_rounded_fare_becomes_the_one_amount_the_page_shows_near_it(monkeypatch):
    page = fake_page(text=RIDE)
    found = gate.facts(page, [])
    assert found["total"] is None  # a fare with no total word: only the summary carries it
    summary(monkeypatch, ["Uber Go · Fare: ₹904", "HOUSE OF COMMONS → Kempegowda International Airport Terminal-1",
                          "Cash"])
    lines = gate.summary(Fake(page), page, found)
    assert lines == ["Uber Go · Fare: ₹904.38", "HOUSE OF COMMONS → Kempegowda International Airport Terminal-1",
                     "Cash"]
    summary(monkeypatch, ["Uber Go", "Fare: ₹904"])  # a fare line naming no ride: nothing ties it to Go's fare
    assert gate.summary(Fake(page), page, found) == ["Uber Go"]


def test_an_amount_with_two_page_amounts_near_it_is_never_guessed(monkeypatch):
    page = fake_page(text=RIDE.replace("₹1,206.50", "₹899.98"))
    summary(monkeypatch, ["Uber Go", "Fare: ₹902", "Kempegowda International Airport Terminal-1"])
    lines = gate.summary(Fake(page), page, gate.facts(page, []))
    assert lines == ["Uber Go", "Kempegowda International Airport Terminal-1"]


def test_an_item_line_keeps_its_words_and_the_destination_when_its_amount_is_cut(monkeypatch):
    page = fake_page(text="Cart\nBritannia Milk Bread 400 g\n₹55\nDelivery fee ₹25\nTo pay ₹80\nPlace order")
    summary(monkeypatch, ["Britannia Milk Bread 400 g × 1 · ₹60", "Deliver to HSR Layout", "Total ₹85"])
    lines = gate.summary(Fake(page), page, gate.facts(page, []))
    assert lines[:2] == ["Britannia Milk Bread 400 g × 1", "Deliver to HSR Layout"] and "Total: ₹80" in lines


AIRDOPES = ("boAt Airdopes 141 Gen 2\nOne-time purchase\nCashback Rewards ₹100\nDelivery Saturday, 3 October\n"
            "Deliver to Mayank - Mumbai 400098")


def test_a_total_read_off_a_cashback_line_is_left_out(monkeypatch):
    page = fake_page(text=AIRDOPES)
    found = gate.facts(page, [])
    assert found["total"] is None
    summary(monkeypatch, ["boAt Airdopes 141 Gen 2, Qty 1", "Total: ₹100"])
    lines = gate.summary(Fake(page), page, found)
    assert lines[0] == "boAt Airdopes 141 Gen 2, Qty 1" and not any("₹100" in line for line in lines)
    priced = fake_page(text="boAt Airdopes 141\nPrice ₹1,099\nCashback ₹100")
    summary(monkeypatch, ["boAt Airdopes 141", "Total ₹1,099"])
    assert "Total ₹1,099" in gate.summary(Fake(priced), priced, gate.facts(priced, []))


def test_an_accordion_header_with_no_price_is_at_most_a_maybe_commit(monkeypatch):
    page = fake_page(text="boAt Airdopes 141\nOne-time purchase\nSubscribe & Save")
    header = {"id": "a", "kind": "click", "label": "One-time purchase #buybox .a-accordion", "role": "button",
              "node": 50, "expanded": "false"}
    calls = jev_says(monkeypatch, ("step", 0.9))
    assert gate.commit_label(Fake(page), "CLICK", header, page, subgoal=ACT) is None and calls == [header["label"]]
    jev_says(monkeypatch, ("step", 0.5))
    assert gate.commit_label(Fake(page), "CLICK", header, page, subgoal=ACT) == header["label"]
    jev_says(monkeypatch, ("step", 1.0))
    plain = {k: v for k, v in header.items() if k != "expanded"}
    assert gate.commit_label(Fake(page), "CLICK", plain, page, subgoal=ACT) == plain["label"]  # no state: outright
    priced = {**header, "label": "One-time purchase ₹1,099"}
    assert gate.commit_label(Fake(page), "CLICK", priced, page, subgoal=ACT) == priced["label"]


# ---- round-3 approval shapes: the summary keeps the page's price, total, and destination


AMAZON_BUY = ("Deliver to Mayank\nMumbai 400098\nboAt Airdopes 141 Gen 2, 4 Mics ENx Tech (Active Black)\n"
              "Visit the boAt Store\n₹799.00\nFREE delivery in 10 minutes on orders over ₹149\n-80%\n₹799.00\n"
              "M.R.P.:\n₹3,990.00\nInclusive of all taxes\nBuy Now\nAdd to Cart")


AMAZON_VARIANT = ("Only 1 left in stock.\nShips from\nAmazon\nSold by\nClicktech Retail Private Ltd\nAdd a Protection "
                  "Plan:\nOnsitego 1 Year Extended Warranty\nfor\n₹199.00\nM.R.P.: ₹5,990.00\nInclusive of all taxes\n"
                  "EMI\nstarts at ₹128 per month.\nPay on Delivery\nColour:\nBlack\nBuy Now")  # bench: no price read


def variant(label, checked):
    return {"id": label[:6], "kind": "click", "role": "radio", "label": label, "checked": checked, "node": len(label)}


VARIANTS = [variant("Active Black ₹799.00 ₹3,990.00 Local Stores", "false"),
            variant("Black ₹1,399.00 with 77 percent savings ₹5,990.00 Only 1 left in stock.", "true"),
            variant("Bold Black ₹999.00 with 80 percent savings ₹4,990.00 In stock", "false"),
            {"id": "bn", "kind": "click", "role": "button", "label": "Buy Now", "node": 99}]


def test_a_buy_now_with_no_price_in_the_text_names_the_selected_options_own_price(monkeypatch):
    single = [VARIANTS[0], variant("Black ₹1,399.00 Only 1 left in stock.", "true"), *VARIANTS[2:]]  # one price
    page = fake_page(text=AMAZON_VARIANT, url="https://www.amazon.in/dp/B0F5BDRQN3", actions=single)
    found = gate.facts(page, [])
    assert found["total"] is None and found["chosen"] == ["Black ₹1,399.00"]
    summary(monkeypatch, ["Product: boAt Airdopes 141 Black, Quantity: 1", "Seller: Clicktech Retail Private Ltd"])
    lines = gate.summary(Fake(page), page, found)  # live bench: the approval named no price at all
    assert "Selected: Black ₹1,399.00" in lines and not any("799" in line or "999.00" in line for line in lines)
    summary(monkeypatch, ["Product: boAt Airdopes 141 Black, Quantity: 1", "Price: ₹1,399.00"])
    lines = gate.summary(Fake(page), page, found)  # the selected option's price is the page's own: kept, once
    assert "Price: ₹1,399.00" in lines and not any(line.startswith("Selected:") for line in lines)


@pytest.mark.parametrize("label", [
    "Black ₹1,999 ₹1,399 30% off",  # the review's probes: the first price was the M.R.P., then an EMI
    "Black EMI from ₹67/month ₹1,399", "Black EMI from ₹67/month", "Black ₹1,399 30% off", "Black ₹1,399 M.R.P.",
    "Black ₹1,399 (save ₹600)", "Black ₹1,399.00 with 77 percent savings ₹5,990.00 Only 1 left in stock."])
def test_a_selected_option_whose_label_holds_more_than_one_plain_price_names_no_price(monkeypatch, label):
    page = fake_page(text=AMAZON_VARIANT, url="https://www.amazon.in/dp/B0F5BDRQN3",
                     actions=[variant(label, "true"), VARIANTS[3]])
    found = gate.facts(page, [])
    assert found["chosen"] == []
    summary(monkeypatch, ["Product: boAt Airdopes 141 Black, Quantity: 1"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "no"}])
    with pytest.raises(Halt, match="Nothing was charged"):
        gate.approved(w, page, VARIANTS[3], "Buy Now", None)
    assert "Selected:" not in w.asked[0]["text"] and NO_TOTAL in w.asked[0]["text"]  # the caveat says to check


def test_an_approval_never_shows_a_placeholder_for_the_users_own_details(monkeypatch):
    page = fake_page(text="Deliver to [profile:name]\nMumbai 400098\nboAt Airdopes 141 ANC\n₹1,499.00\nBuy Now",
                     url="https://www.amazon.in/dp/B0C7QHHT63")  # live amazon: "Delivering to: [profile:name]"
    found = gate.facts(page, [])
    assert found["address"] is None  # a name hidden as a placeholder is no address
    summary(monkeypatch, ["boAt Airdopes 141 ANC, Quantity: 1", "Deliver to [profile:name], Mumbai 400098",
                          "Name: [profile:name]", "Price: ₹1,499.00"])
    assert gate.summary(Fake(page), page, found) == ["boAt Airdopes 141 ANC, Quantity: 1", "Deliver to Mumbai 400098",
                                                     "Price: ₹1,499.00"]


def test_a_buy_now_summary_keeps_the_products_price_and_drops_a_line_saying_none_is_shown(monkeypatch):
    page = fake_page(text=AMAZON_BUY, url="https://www.amazon.in/dp/B0F8BVSK21")
    summary(monkeypatch, ["boAt Airdopes 141 Gen 2 Active Black, Qty 1", "Delivery address: Mayank Mumbai 400098",
                          "Price: ₹799", "Total: Not shown"])
    lines = gate.summary(Fake(page), page, gate.facts(page, []))
    assert lines == ["boAt Airdopes 141 Gen 2 Active Black, Qty 1", "Delivery address: Mayank Mumbai 400098",
                     "Price: ₹799"]


def test_a_dispute_approval_drops_lines_saying_what_it_doesnt_mention(monkeypatch):
    # live Experian: “No specific order or membership is mentioned in the dispute.” under the address it corrects
    page = fake_page(text="Personal Details\nAddress 1\n19, Old Lane\nSubmit",
                     url="https://consumer.experian.in/ECSINDIA-DCE/view/angular/disputePreFillPage")
    summary(monkeypatch, ["The address on the report will be corrected to 19, New Lane, Howrah-711101.",
                          "No specific order or membership is mentioned in the dispute.",
                          "No amount or refund is discussed in this dispute."])
    assert gate.summary(Fake(page), page, gate.facts(page, [])) == [
        "The address on the report will be corrected to 19, New Lane, Howrah-711101."]


def test_a_bike_fare_off_by_a_rupee_is_cut_and_the_drop_stays(monkeypatch):
    page = fake_page(text="Bike\n₹45\n3 mins\nAuto\n₹92\nPickup: Mandpura\nDrop: Indiranagar Metro Station\n"
                          "Book Bike", url="https://www.rapido.bike/Home")
    summary(monkeypatch, ["Rapido Bike · ₹46", "Mandpura → Indiranagar Metro Station"])
    assert gate.summary(Fake(page), page, gate.facts(page, [])) == ["Rapido Bike",  # ₹1 off is no rounding
                                                                    "Mandpura → Indiranagar Metro Station"]
    summary(monkeypatch, ["Rapido Bike · ₹45.40"])
    assert gate.summary(Fake(page), page, gate.facts(page, []))[0] == "Rapido Bike · ₹45"


def test_a_ticket_total_the_summary_rounded_is_the_pages_payable_amount(monkeypatch):
    page = fake_page(text="PVR Orion Mall\nSat, 3 Oct 07:30 PM\n2 Tickets\nSubtotal ₹600.00\nConvenience fees "
                          "₹70.80\nAmount Payable ₹670.80\nProceed To Pay", url="https://in.bookmyshow.com/buytickets")
    summary(monkeypatch, ["Drishyam 2 · PVR Orion Mall · Sat, 3 Oct 07:30 PM", "2 tickets", "Total ₹670"])
    lines = gate.summary(Fake(page), page, gate.facts(page, []))
    assert lines[:3] == ["Drishyam 2 · PVR Orion Mall · Sat, 3 Oct 07:30 PM", "2 tickets", "Total: ₹670.80"]
    assert "Total ₹670" not in lines  # "Amount Payable" names no word "Total" does: the page's own total is added


def test_an_order_that_doesnt_match_says_to_fix_the_cart_before_the_final_step(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke 330 ml × 2", "Total ₹79"])
    checks(monkeypatch, matches="no")
    with pytest.raises(Retry) as refused:
        gate.approved(Fake(page), page, PAY, "Pay ₹79", None)
    said = str(refused.value)  # live instamart: "never remove items… Then do the final step again" → pressed Pay again
    assert "fix the cart first" in said and "decrease" in said and "do the final step again" not in said


def test_a_key_pressed_on_a_page_with_nothing_focused_is_never_the_approved_commit(monkeypatch):
    page = fake_page(text="Choose a ride\nUber Go AC ₹1,113.06\nCash", actions=[control("Request Uber Go AC")])
    jev_says(monkeypatch, ("final", 0.9))
    w = Fake(page)
    w.state["browser"].focused_label = lambda: ""
    key = {"id": "key_page", "kind": "press_key", "label": "The page (focused element)", "node": None}
    with pytest.raises(Retry, match="press the final control itself"):  # live uber: “I'll press “The page…””
        gate.commit_label(w, "PRESS_KEY", key, page, key="Enter", subgoal={"do": "request the ride", "kind": "commit"})
    assert gate.commit_label(w, "PRESS_KEY", key, page, key="Enter", subgoal=ACT) is None


def test_a_rating_prompt_is_no_money_so_booking_words_beside_it_stay_a_maybe_commit(monkeypatch):
    page = fake_page(text="Drishyam The Conclusion\n9.1/10\n(6.4K+ Votes)\nRate now\n2h 30m\n•\nDrama\nHindi\n"
                          "Book tickets\nShare")  # live bookmyshow: approval asked on the movie's own page
    jev_says(monkeypatch, ("step", 0.9))
    assert not gate.shows_money(page["text"])
    assert gate.commit_label(Fake(page), "CLICK", control("Book tickets"), page, subgoal=ACT) is None
    assert gate.shows_money("Per km\nRate\n12") and gate.shows_money("Rate: 12 per km")


# ---- a control that opens the payment page is a step only on a cart page with no method in sight, and Jev decides


PLACE_ORDER = {"id": "po", "kind": "click", "label": "Place order (shows “Proceed to Pay”)", "name": "Place order",
               "shown": "Proceed to Pay", "role": "button", "node": 30}
PAYMENT_PAGE = "Payment options\nCash on Delivery\nSelected\nUPI\nCards\nTo Pay ₹1,499"
CART_PAGE = ("Bill Details\nItem Total\n₹50.00\nHandling Fee\n₹12.00\nSmall Cart Fee\n₹20.00\nDelivery Partner Fee\n"
             "₹30.00\nGST and Charges\n₹9.00\nTo Pay\n₹121\nCart total is 121.0 Rupees\nVIEW DETAILED BILL\n"
             "Proceed to Pay")  # live instamart's cart page, at the bottom


def unpaid(page, goal="Order one Diet Coke on Instamart"):
    """A worker whose user hasn't said how to pay."""
    w = Fake(page)
    w.state["goal"] = goal
    w.method_chosen = lambda: False
    return w


def final_check(monkeypatch, verdict):
    asked = []
    monkeypatch.setattr(gate.jev, "final", lambda *a: asked.append(a) or verdict)
    return asked


def test_a_pay_control_showing_proceed_to_pay_on_the_payment_page_asks_and_jev_is_consulted(monkeypatch):
    page = fake_page(text=PAYMENT_PAGE, actions=[PLACE_ORDER])  # the review's probe: it ran with no yes, unchecked
    asked = final_check(monkeypatch, ("final", 0.99))
    assert gate.commit_label(unpaid(page), "CLICK", PLACE_ORDER, page) == "Place order" and asked == []


@pytest.mark.parametrize("text, goal, extra", [
    (PAYMENT_PAGE, "Order a lamp", []),  # the methods are on the page
    (CART_PAGE, "Order a lamp and pay cash on delivery", []),  # the goal names how to pay
    (CART_PAGE + "\nPay on delivery", "Order a lamp", []),
    (CART_PAGE.replace("Bill Details", "Paying with\nSelected"), "Order a lamp", []),  # a method already chosen
    (CART_PAGE, "Order a lamp", [{"id": "r", "kind": "click", "label": "Saved option ending 4242", "role": "radio",
                                  "node": 31, "checked": "true"}]),  # a pre-selected choice
    ("Deliver to Home\nProceed to Pay", "Order a lamp", []),  # no bill at all: not a cart step
])
def test_a_page_that_may_take_the_payment_never_lets_proceed_to_pay_through_on_a_lean(monkeypatch, text, goal, extra):
    page = fake_page(text=text, actions=[PLACE_ORDER, *extra])
    asked = final_check(monkeypatch, ("step", 0.6))  # Jev leans step but isn't sure: the user is asked
    assert gate.commit_label(unpaid(page, goal), "CLICK", PLACE_ORDER, page) == "Place order" and asked == []


@pytest.mark.parametrize("name", ["Place order", "Pay now", "Confirm payment", "Book now", "Submit order",
                                  "Complete purchase"])
def test_a_control_named_as_a_commit_never_gets_the_cart_pages_lean_from_the_checkout_step_it_shows(monkeypatch,
                                                                                                    name):
    control_ = {**PLACE_ORDER, "name": name, "label": f"{name} (shows “Proceed to Pay”)"}
    cart = fake_page(text=CART_PAGE, actions=[control_])  # a cart page with no method in sight, the user unasked
    final_check(monkeypatch, ("step", 0.55))  # the review's probe: Jev leans step, as for instamart's own control
    assert gate.commit_label(unpaid(cart), "CLICK", control_, cart) == name


@pytest.mark.parametrize("sure", [0.85, 0.55])
@pytest.mark.parametrize("name", ["Place order", "Pay now", "Confirm order"])
def test_a_control_named_as_a_commit_asks_however_sure_jev_is_that_its_checkout_step_is_a_step(monkeypatch, name,
                                                                                               sure):
    control_ = {**PLACE_ORDER, "name": name, "label": f"{name} (shows “Proceed to Pay”)"}
    cart = fake_page(text=CART_PAGE, actions=[control_])  # the re-review's probe: a sure step cleared "Place order"
    final_check(monkeypatch, ("step", sure))
    w = unpaid(cart)
    assert gate.commit_label(w, "CLICK", control_, cart) == name and "cleared" not in w.state


def test_a_control_that_says_it_goes_to_payment_or_names_no_commit_still_goes_to_jev(monkeypatch):
    cart = fake_page(text=CART_PAGE, actions=[MAKE_PAYMENT])
    asked = final_check(monkeypatch, ("step", 0.85))
    assert gate.commit_label(unpaid(cart), "CLICK", MAKE_PAYMENT, cart) is None and len(asked) == 1
    proceed = {**MAKE_PAYMENT, "name": "Continue", "label": "Continue (shows “Proceed to Pay”)"}
    asked = final_check(monkeypatch, ("step", 0.85))
    assert gate.commit_label(unpaid(cart), "CLICK", proceed, cart) is None and len(asked) == 1
    asked = final_check(monkeypatch, ("final", 0.9))
    assert gate.commit_label(unpaid(cart), "CLICK", proceed, cart) == "Proceed to Pay" and len(asked) == 1


def test_proceed_to_pay_on_the_cart_page_before_a_method_is_a_step_only_when_jev_says_step(monkeypatch):
    cart = fake_page(text=CART_PAGE, actions=[MAKE_PAYMENT])
    w = unpaid(cart)
    asked = final_check(monkeypatch, ("step", 0.6))
    assert gate.commit_label(w, "CLICK", MAKE_PAYMENT, cart) is None and len(asked) == 1  # live instamart's flow
    assert w.state["cleared"] == MAKE_PAYMENT["name"]  # kept for the bench's audit
    asked = final_check(monkeypatch, ("final", 0.7))
    assert gate.commit_label(unpaid(cart), "CLICK", MAKE_PAYMENT, cart) == MAKE_PAYMENT["name"] and len(asked) == 1
    payment = fake_page(text=PAYMENT_PAGE, actions=[MAKE_PAYMENT])  # the same control on the payment page
    asked = final_check(monkeypatch, ("step", 0.6))
    assert gate.commit_label(unpaid(payment), "CLICK", MAKE_PAYMENT, payment) == MAKE_PAYMENT["name"]
    assert len(asked) == 1


# ---- a summary amount is swapped for the page's only when it was the page's own, rounded: under ₹1, on a line that
# names the same thing, never a saving


def test_a_fare_a_few_rupees_off_is_cut_never_swapped_for_another_rides_fare(monkeypatch):
    page = fake_page(text=RIDE)  # the review's probe: Premier's fare became Go's ₹904.38
    summary(monkeypatch, ["Uber Premier · Fare ₹910", "HOUSE OF COMMONS → Kempegowda International Airport Terminal-1"])
    lines = gate.summary(Fake(page), page, gate.facts(page, []))
    assert lines == ["Uber Premier · Fare", "HOUSE OF COMMONS → Kempegowda International Airport Terminal-1"]


@pytest.mark.parametrize("url", ["https://shop.test/checkout", "https://m.uber.com/go/product-selection"])
@pytest.mark.parametrize("line, kept", [("Uber Premier · Fare ₹904", "Uber Premier · Fare"),
                                        ("Uber Go Plus · ₹904", "Uber Go Plus")])
def test_a_rounded_fare_is_never_swapped_for_another_options_fare_by_a_word_they_share(monkeypatch, url, line, kept):
    page = fake_page(text=RIDE, url=url)  # the re-review's probes: "Uber" alone tied them to Go's ₹904.38
    summary(monkeypatch, [line, "HOUSE OF COMMONS → Kempegowda International Airport Terminal-1"])
    lines = gate.summary(Fake(page), page, gate.facts(page, []))
    assert lines == [kept, "HOUSE OF COMMONS → Kempegowda International Airport Terminal-1"]


def test_a_rounded_fare_is_swapped_only_for_the_one_option_that_names_everything_the_line_names(monkeypatch):
    for url in ["https://shop.test/checkout", "https://m.uber.com/go/product-selection"]:
        page = fake_page(text=RIDE, url=url)
        summary(monkeypatch, ["Uber Go · Fare: ₹904"])
        assert gate.summary(Fake(page), page, gate.facts(page, []))[0] == "Uber Go · Fare: ₹904.38"
    tied = fake_page(text=RIDE.replace("Premier", "Uber Go"))  # two options name it: never guessed
    summary(monkeypatch, ["Uber Go · Fare: ₹904"])
    assert gate.summary(Fake(tied), tied, gate.facts(tied, []))[0] == "Uber Go · Fare"


def test_a_wrong_fee_is_never_swapped_for_a_saving_and_the_real_fee_is_added(monkeypatch):
    page = fake_page(text="Cart\nDiet Coke 300 ml × 1\n₹40\nDelivery fee ₹30\nYou save ₹4\nTo pay ₹70")
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Delivery fee ₹5", "Total ₹70"])
    lines = gate.summary(Fake(page), page, gate.facts(page, []))
    assert "Delivery fee ₹30" in lines and not any("₹4" in line or "₹5" in line for line in lines)


def test_a_wrong_total_is_never_swapped_for_an_item_price_beside_the_real_one(monkeypatch):
    page = fake_page(text="Cart\nAtta 5 kg × 1\n₹120\nDelivery fee ₹25\nTo pay ₹145")
    summary(monkeypatch, ["Atta 5 kg × 1", "Total ₹121"])
    lines = gate.summary(Fake(page), page, gate.facts(page, []))
    assert "Total: ₹145" in lines and not any("₹120" in line or "₹121" in line for line in lines)


def test_an_amount_under_a_rupee_off_on_a_saving_line_is_cut(monkeypatch):
    page = fake_page(text="Cart\nDiet Coke 300 ml × 1 ₹45.00\nYou saved ₹39.50 on Diet Coke\nTo pay ₹45.00")
    summary(monkeypatch, ["Diet Coke 300 ml × 1 · ₹39"])
    lines = gate.summary(Fake(page), page, gate.facts(page, []))
    assert lines[0] == "Diet Coke 300 ml × 1" and not any("39" in line for line in lines)


def test_an_amount_under_a_rupee_off_on_a_line_naming_something_else_is_cut(monkeypatch):
    page = fake_page(text="Cart\nPepsi 300 ml × 1 ₹40.40\nDiet Coke 300 ml × 1 ₹45.00\nTo pay ₹85.40")
    summary(monkeypatch, ["Diet Coke 300 ml × 1 · ₹40", "Total ₹85.40"])
    lines = gate.summary(Fake(page), page, gate.facts(page, []))
    assert lines[:2] == ["Diet Coke 300 ml × 1", "Total ₹85.40"]


def test_a_control_showing_a_commit_word_under_another_name_asks_for_approval(monkeypatch):
    summaryish = {"id": "cs", "kind": "click", "name": "Cart summary", "shown": "Place order ₹121", "role": "button",
                  "label": "Cart summary (shows “Place order ₹121”)", "node": 40}
    page = fake_page(text=INSTAMART, actions=[summaryish])
    asked = final_check(monkeypatch, ("step", 1.0))
    assert gate.commit_label(Fake(page), "CLICK", summaryish, page, subgoal=ACT) == "Place order ₹121" and asked == []
    spot = {**summaryish, "within": "Place order", "label": "Cart summary (at “Place order”)"}  # the planner's words
    assert gate.commit_label(Fake(page), "CLICK", spot, page, subgoal=ACT) == "Place order ₹121"


def test_an_approval_holds_only_for_the_spot_inside_the_control_it_was_given_for(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "yes"}])
    on = {**PAY, "within": "Pay", "label": "Pay ₹79 (at “Pay”)"}
    with pytest.raises(StalePage):
        gate.approved(w, page, on, "Pay ₹79", None)
    assert gate.approved(w, page, on, "Pay ₹79", None) is True and w.state["approval"]["within"] == "Pay"
    assert not gate.matches(w.state["approval"], PAY, "Pay ₹79")  # the control's middle is another spot
    other = {**PAY, "within": "₹79", "label": "Pay ₹79 (at “₹79”)"}
    with pytest.raises(Retry, match="did not approve"):  # asked again, and no yes came
        gate.approved(w, page, other, "Pay ₹79", None)
    assert len(w.asked) == 2


NO_TOTAL = "(I couldn't read the total on this page — check it on the site before you say yes.)"


@pytest.mark.parametrize("text, warned", [(RIDE, True), ("Cart\nDiet Coke 300 ml\nTo pay ₹79\nPay ₹79", False),
                                          ("Book a demo\nName\nEmail\nSubmit", False)])
def test_an_approval_on_a_page_with_money_but_no_total_read_says_to_check_the_total(monkeypatch, text, warned):
    page = fake_page(text=text)
    summary(monkeypatch, ["Uber Go", "HOUSE OF COMMONS → Kempegowda International Airport Terminal-1"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "no"}])
    with pytest.raises(Halt, match="Nothing was charged"):
        gate.approved(w, page, PAY, "Request Uber Go", None)
    assert (NO_TOTAL in w.asked[0]["text"]) is warned
