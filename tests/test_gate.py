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
