"""The worker in a real headless Chrome on the shop fixture, with Jev and the planner scripted by what the page
shows: sign-in, a code, an address, a CVV, the approval, and the order. Run: uv run pytest -m browser"""

import json
import re
from pathlib import Path

import pytest
from fakes import jev_checks, script_jev, script_planner, store, sub, turn
from helpers import log

from assistant import bench, learn
from assistant.worker import Worker

pytestmark = pytest.mark.browser


def no_cvv(text):
    """True when the CVV is nowhere in the text. Timing fields ("elapsed_ms": 123) are not a leak: drop them first."""
    return re.search(r"(?<!\d)123(?!\d)", re.sub(r'"\w*_ms": \d+', "", text)) is None


def shop_step(state):
    """What Jev would choose on each screen of shop.html."""
    text, elements = state["page"]["text"], state["elements"]
    filled = {e["label"] for e in elements if e.get("value")}
    if "Order placed" in text:
        return {"next": "DONE"}
    if "Log in" in text:
        if "Phone number" in filled:
            return {"next": "CLICK", "click_target": "Continue"}
        return {"next": "TYPE", "type_text_target": "Phone number"}
    if "Checkout" not in text:  # the code screen
        if filled:
            return {"next": "CLICK", "click_target": "Verify"}
        return {"next": "TYPE", "type_text_target": "One-time code", "blocker": "code"}
    # snapshot.js reports a radio's state as the string "true" or "false": compare it, never test its truthiness
    if next(e for e in elements if e["label"].startswith("Office")).get("checked") != "true":
        return {"next": "CLICK", "click_target": "Office"}
    if "CVV" not in filled:
        return {"next": "TYPE", "type_text_target": "CVV", "blocker": "code"}
    return {"next": "CLICK", "click_target": "Pay"}


def test_the_worker_signs_in_orders_and_reports_on_a_real_page(chrome, site, tmp_path, monkeypatch):
    memory = store()
    memory.set_id("phone", "phone number", "+91 98765 43210")
    seen = script_planner(monkeypatch,
                          turn(subgoals=[sub("sign in and place the order", "the order is placed",
                                             values=[("phone number", "[profile:phone]")])]),
                          turn("DONE", message="Order placed #A1B2, arriving in 12 minutes.",
                               text="Order placed #A1B2"))
    sent = script_jev(monkeypatch, shop_step)
    jev_checks(monkeypatch)
    replies, questions = [{"text": "482913"}, {"text": "123"}, {"text": "yes"}], []
    w = Worker(site["a"] + "/shop.html", "Order Diet Coke from the shop", memory=memory, trace_dir=tmp_path / "t",
               asker=lambda message: questions.append(message) or replies.pop(0))
    try:
        for state in w.steps():
            assert len(state["history"]) < 20, "the loop should finish in a few steps"
        assert w.state["status"] == "done" and log(w.browser) == ["otp:482913", "pay:office:3"]
    finally:
        w.close()
    asked = [m["kind"] for m in w.state["messages"] if m["from"] == "agent" and m.get("kind") not in {"info", "result"}]
    assert asked == ["secret", "secret", "text"]  # the code, the CVV, the approval
    assert [bool(q.get("approval")) for q in questions] == [False, False, True] and "Pay" in questions[-1]["text"]
    assert w.state["messages"][-1]["kind"] == "result" and "A1B2" in w.state["messages"][-1]["text"]
    assert "Order placed" in w.state["page"]["text"]  # the site itself shows the order placed
    # the phone typed was the real number: the code screen shows its last four digits
    assert any("Code sent to •••• 3210" in state["page"]["text"] for state, _ in sent)
    # no request to Jev or the planner carries the code, the CVV, or the phone number
    requests = json.dumps([state for state, _ in sent]) + json.dumps(seen, default=str)
    assert "482913" not in requests and "98765" not in requests
    assert no_cvv(requests)  # the CVV, anywhere ("••1234" is the card, not it)
    # nor does the trace written at close
    trace = Path(w.state["trace_path"]).read_text()
    assert json.loads(trace)["status"] == "done"
    assert "482913" not in trace and "98765" not in trace and no_cvv(trace)


# ---- Plan 7 Task 11: the same errand twice


def shop_run(site, tmp_path, memory, monkeypatch, *, turns, habit=None):
    """One run of shop.html. Jev answers as shop_step, but is unsure about the address the first time it is asked."""
    seen = script_planner(monkeypatch, *turns)
    unsure = []

    def step(state):
        pick = shop_step(state)
        if pick.get("click_target") == "Office" and not unsure:
            unsure.append(True)
            return {**pick, "next": ("CLICK", 0.4)}  # under ACT_P: Phase A would ask the planner
        return pick

    sent = script_jev(monkeypatch, step)
    jev_checks(monkeypatch)
    replies = [{"text": "482913"}, {"text": "123"}, {"text": "yes"}]
    w = Worker(site["a"] + "/shop.html", "Order Diet Coke from the shop", memory=memory, skill="groceries",
               habit=habit, sameness="same" if habit else None, trace_dir=tmp_path / "t",
               asker=lambda message: replies.pop(0))
    try:
        for state in w.steps():
            assert len(state["history"]) < 20, "the loop should finish in a few steps"
        assert w.state["status"] == "done" and log(w.browser) == ["otp:482913", "pay:office:3"]
    finally:
        w.close()
    return w, seen, sent, unsure


def test_the_same_errand_replays_its_habit_to_the_approval_without_the_planner(chrome, site, tmp_path, monkeypatch):
    memory = store()
    memory.set_id("phone", "phone number", "+91 98765 43210")
    start = turn(subgoals=[sub("sign in and place the order", "the order is placed",
                               values=[("phone number", "[profile:phone]")])])
    done = turn("DONE", message="Order placed #A1B2, arriving in 12 minutes.", text="Order placed #A1B2")
    # Jev is unsure about Office once: the planner picks it (a CONTINUE back to an unsure Jev is refused)
    first, seen_one, sent_one, _ = shop_run(site, tmp_path, memory, monkeypatch,
                                            turns=[start, turn("PICK", pick=1), done])
    learn.after(first.state, memory, reflect=lambda digest: ([], []))
    habit = memory.habits(skill="groceries")[0]
    second, seen_two, sent_two, unsure_two = shop_run(site, tmp_path, memory, monkeypatch, turns=[done], habit=habit)
    assert [p["reason"] for p in seen_one] == ["start", "unsure", "commit"]
    assert [p["reason"] for p in seen_two] == ["commit"]  # nothing before the approval
    # Jev was asked about the Office click in pass 2 too, and was unsure (p=0.4): replay's unique control made the
    # click, not a confident Jev and not the planner
    assert unsure_two == [True] and any("Office" in json.dumps(q) for _, q in sent_two)
    assert len(sent_two) < len(sent_one)  # a thin signal: the reason lists above are what discriminate
    record = second.state["replay"]
    assert record["fallbacks"] == [] and record["matched"] >= 4
    assert second.state["messages"][0]["text"] == "Same as last time: " + habit["title"] + "."
    # no ID, code, or CVV in any model request, either trace, or any table
    requests = json.dumps([state for state, _ in sent_one + sent_two]) + json.dumps(seen_one + seen_two, default=str)
    traces = [first.state["trace_path"], second.state["trace_path"]]
    tables = "\n".join(r["row"] for table in bench.SCANNED for r in memory.run(  # an ID's events: only its token
        lambda cur, t=table: cur.execute(f"SELECT to_jsonb(r)::text AS row FROM {t} r"
                                         + (" WHERE ref NOT LIKE 'ids:%'" if t == "events" else "")).fetchall()))
    for text in (requests, tables, *(Path(t).read_text() for t in traces)):
        assert "482913" not in text and "98765" not in text and no_cvv(text)
    assert bench.secret_audit(memory, traces) == []
