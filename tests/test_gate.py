"""The commit gate over a fake worker: what commits, the facts an approval shows, asking and pre-authorising,
binding the yes, and watching the result. Jev and mini are scripted."""

from unittest.mock import Mock

import pytest
from fakes import fake_page

from assistant import gate
from assistant.browser import StalePage
from assistant.errors import Halt, Retry

PAY = next(a for a in fake_page()["actions"] if a["label"] == "Pay ₹79")


class Fake:
    """What the gate uses of a worker."""

    bench, processing_ms, outcome_pause = False, 300000, 0

    def __init__(self, page, replies=(), preauth=False, cap=None):
        self.state = {"goal": "Order one Diet Coke", "chat": [], "recent": [page["text"]], "history": [],
                      "approval": None, "commit": None, "preauth": preauth, "cap": cap, "site": "shop.test",
                      "notes": [], "done": [], "mismatches": 0, "page": page, "reason": None,
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


def test_inside_a_commit_subgoal_jev_confirms_a_control_the_regex_missed(monkeypatch):
    page = fake_page()
    go = {"id": "g", "kind": "click", "label": "Continue", "role": "button", "node": 41}
    monkeypatch.setattr(gate.jev, "final", lambda *a: ("final", 0.7))
    assert gate.commit_label(Fake(page), "CLICK", go, page, subgoal={"do": "place the order", "kind": "commit"}) \
        == "Continue"
    assert gate.commit_label(Fake(page), "CLICK", go, page, subgoal={"do": "open the cart", "kind": "act"}) is None


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


def test_a_yes_with_a_change_commits_nothing(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke 300 ml × 1", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "yes but make it 2"}])
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert w.state["approval"] is None


def test_an_amount_the_page_doesnt_show_is_sent_back(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke", "Total ₹97"])
    checks(monkeypatch)
    with pytest.raises(Retry, match="doesn't show ₹97"):
        gate.approved(Fake(page), page, PAY, "Pay ₹79", None)


def test_an_order_that_doesnt_match_is_fixed_twice_then_asked(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Pepsi", "Total ₹79"])
    checks(monkeypatch, matches="no")
    w = Fake(page, replies=[{"text": "no"}])
    for _ in range(2):
        with pytest.raises(Retry, match="doesn't match"):
            gate.approved(w, page, PAY, "Pay ₹79", None)
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert len(w.asked) == 1


def test_preauthorised_within_the_cap_runs_and_says_so(monkeypatch):
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
    w = Fake(page, replies=[{"text": "no"}], preauth=True, cap=cap)
    with pytest.raises(Retry):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert len(w.asked) == 1


def test_preauthorised_without_a_cap_submits_a_form_with_no_amount(monkeypatch):
    page = fake_page(text="Pizza order\nCustomer name\nSubmit order", url="https://httpbin.org/forms/post")
    submit = {"id": "s", "kind": "click", "label": "Submit order", "role": "button", "node": 50}
    summary(monkeypatch, ["Medium pizza with bacon", "Test User at 19:00"])
    checks(monkeypatch)
    w = Fake(page, preauth=True)
    w.state["site"] = "httpbin.org"
    assert gate.approved(w, page, submit, "Submit order", None) is True


def test_a_bench_run_never_uses_preauthorisation(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "no"}], preauth=True, cap=100.0)
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
    with pytest.raises(Retry, match="did not approve"):
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
    with pytest.raises(Retry, match="did not approve"):
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
    ("Item total ₹1,000\nTo pay ₹700", None),                           # asks rather than guess: a larger total line
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
        with pytest.raises(Retry, match="did not approve"):
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
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, control("Submit order", node=50), "Submit order", None)
    assert len(w.asked) == 1


def test_a_page_with_no_amount_still_shows_no_money():
    page = "Pizza order\nCustomer name\nPreferred delivery time\nDelivery instructions\nSubmit order"
    assert not gate.shows_money(page)
