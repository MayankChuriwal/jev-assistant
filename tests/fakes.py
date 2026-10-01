"""Fakes for the worker tests: a checkout page, scripted planner and Jev, and a Worker over a mock browser. No
Chrome, no models."""

from unittest.mock import Mock

from assistant.browser import fingerprint
from assistant.profile import Profile

MEMORY = None  # conftest sets this for each test: calling it returns the test's own Memory, made on first use


def store():
    """This test's Memory: a fresh Postgres schema of its own."""
    return MEMORY()


def fake_page(text="Checkout\nDiet Coke 300 ml\nTotal ₹79\nPay ₹79", url="https://shop.test/checkout", actions=None):
    page = {"url": url, "title": "Shop", "text": text, "scroll": {}, "frames": {}, "actions": actions or [
        {"id": "e1", "kind": "fill", "label": "Phone number", "role": "textbox", "value": "", "node": 1,
         "constraints": {"type": "tel"}},
        {"id": "e2", "kind": "fill", "label": "Code", "role": "textbox", "value": "", "node": 2,
         "group": [2, 3, 4, 5, 6, 7], "constraints": {"inputmode": "numeric", "maxlength": 1}},
        {"id": "e3", "kind": "fill", "label": "CVV", "role": "textbox", "value": "", "node": 8,
         "constraints": {"type": "password", "inputmode": "numeric", "maxlength": 3}, "submit": "Pay ₹79"},
        {"id": "e4", "kind": "click", "label": "Home", "role": "radio", "node": 9},
        {"id": "e5", "kind": "click", "label": "Office", "role": "radio", "node": 10},
        {"id": "e6", "kind": "click", "label": "Pay ₹79", "role": "button", "node": 11},
        {"id": "e7", "kind": "upload", "label": "File input: Statement", "role": "file", "node": 12},
        {"id": "e8", "kind": "click", "label": "Open Phone number", "role": "textbox", "node": 1},
        {"id": "wait", "kind": "wait", "label": "Wait", "node": None},
        {"id": "open_url", "kind": "open_url", "label": "Open a web address", "node": None},
    ]}
    page["fingerprint"] = fingerprint(page)
    return page


def answers_for(questions, picks):
    """Valid Jev answers for a request: `picks` maps question names to a choice, an element's words, or (either,
    probability). Unnamed questions get none (blocker), worked (effect), THINK (next), or their first option."""
    defaults = {"blocker": "none", "effect": "worked", "next": "THINK"}
    out = {}
    for name, question in questions.items():
        options = question["options"]
        pick = picks.get(name, defaults.get(name, next(iter(options))))
        choice, p = pick if isinstance(pick, tuple) else (pick, 0.9)
        if choice not in options:  # words of an element: the first option whose element holds them
            choice = next(i for i, o in options.items() if isinstance(o, dict) and choice in o.get("element", ""))
        rest = [o for o in options if o != choice]
        probabilities = {o: (1 - p) / len(rest) for o in rest}
        probabilities[choice] = p if rest else 1.0
        out[name] = {"choice": choice, "probabilities": probabilities, "confidence": p}
    return out


def sub(do="the next step", done_when="the page shows it", kind="act", values=()):
    """One subgoal as the planner writes it; values are (field, text) pairs."""
    return {"do": do, "done_when": done_when, "kind": kind,
            "values": [{"field": field, "text": text} for field, text in values]}


def turn(op="CONTINUE", pick="", text="", message="", options=(), subgoals=(), note=""):
    """One planner output."""
    return {"note": note, "subgoals": list(subgoals), "action": {"op": op, "pick": str(pick), "text": text},
            "message": message, "options": list(options)}


def script_planner(monkeypatch, *outs):
    """The planner's turns in order (the last one repeats) and a fixed approval summary. Returns the payloads the
    planner was shown."""
    from assistant import planner

    queue, seen = list(outs), []

    def fake_turn(payload, image=None):
        seen.append(payload)
        return (queue.pop(0) if len(queue) > 1 else queue[0]), {"model": "fake", "latency_ms": 1}

    monkeypatch.setattr(planner, "turn", fake_turn)
    monkeypatch.setattr(planner, "summary", lambda payload: ["Diet Coke 300 ml × 1", "Total ₹79"])
    return seen


def script_jev(monkeypatch, *steps):
    """Jev's step answers in order (the last one repeats). Each step maps question names to a choice, an element's
    words, or (either, probability); a step may be a function of the state Jev sees. Returns the requests sent."""
    from assistant import jev

    queue, sent = list(steps), []

    def request(state, questions):
        sent.append((state, questions))
        picks = queue.pop(0) if len(queue) > 1 else queue[0]
        return answers_for(questions, picks(state) if callable(picks) else picks)

    monkeypatch.setattr(jev, "request", request)
    return sent


def jev_checks(monkeypatch, **overrides):
    """Jev's one-question checks, scripted: each is a value, or a list used in order (the last one repeats)."""
    from assistant import jev

    values = {"read_reply": "other", "approval": "yes", "achieved": "yes", "final": ("final", 1.0),
              "matches": "yes", "after_commit": "confirmed", "value_for": None, **overrides}
    for name, value in values.items():
        queue = list(value) if isinstance(value, list) else [value]
        monkeypatch.setattr(jev, name, lambda *a, _q=queue, **k: _q.pop(0) if len(_q) > 1 else _q[0])


def worker_with(tmp_path, *, replies=(), page=None, goal="Order Diet Coke on Instamart", profile=None, **options):
    """A Worker over a mock browser with the shipped skills. `replies` answer questions in order, then the user
    cancels. Questions land in `w.asked`; one-way messages in `w.told`."""
    from assistant.worker import Worker

    page = page or fake_page()
    owner = Profile(tmp_path / "profile.json")
    owner.data.update({"name": "Mayank Churiwal", "phone": "+91 98765 43210", **(profile or {})})
    browser = Mock(fresh=Mock(return_value=True), observe=Mock(return_value=page), act=Mock(return_value={}),
                   screenshot=Mock(return_value="iVBORw0KGgo="), focused_label=Mock(return_value=""),
                   downloads=[], pending_downloads=[])
    asked, told, queue = [], [], list(replies)
    w = Worker(page["url"], goal, profile=owner, browser=browser, trace_dir=tmp_path / "traces",
               asker=lambda m: asked.append(m) or (queue.pop(0) if queue else {"cancel": True}),
               notify=told.append, **options)
    w.asked, w.told = asked, told
    return w
