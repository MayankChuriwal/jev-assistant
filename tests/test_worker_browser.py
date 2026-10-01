"""The worker in a real headless Chrome on the shop fixture, with Jev and the planner scripted by what the page
shows: sign-in, a code, an address, a CVV, the approval, and the order. Run: uv run pytest -m browser"""

import json
import re
from pathlib import Path

import pytest
from fakes import jev_checks, script_jev, script_planner, store, sub, turn
from helpers import log

from assistant.worker import Worker

pytestmark = pytest.mark.browser


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
    assert re.search(r"(?<!\d)123(?!\d)", requests) is None  # the CVV, anywhere ("••1234" is the card, not it)
    # nor does the trace written at close
    trace = Path(w.state["trace_path"]).read_text()
    assert json.loads(trace)["status"] == "done"
    assert "482913" not in trace and "98765" not in trace and re.search(r"(?<!\d)123(?!\d)", trace) is None
