"""Fakes for agent tests: a checkout page, a vault, and an agent over a mock browser. No Chrome, no models."""

import time
from unittest.mock import Mock

from assistant import agent as loop
from assistant.browser import fingerprint
from assistant.profile import Profile, Secrets


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


class FakeVault:
    def __init__(self, values=None):
        self.values, self.writes = dict(values or {}), []

    def get(self, key):
        if key not in self.values:
            raise KeyError(key)
        return self.values[key]

    def set(self, key, value):
        self.values[key] = value
        self.writes.append((key, value))


def decision(choice, operation, **extra):
    return {"choice": choice, "operation": operation, "target": None, "key": None, "drop": None,
            "confidence": 1.0, "probabilities": {choice: 1.0}, "latency_ms": 1, "usage": {}, **extra}


def agent_with(tmp_path, *, replies=(), page=None, vault=None, profile=None):
    """An Agent over a mock browser. `replies` answer questions in order, then the user cancels.
    Questions land in `agent.asked`; one-way messages in `agent.told`."""
    a = loop.Agent.__new__(loop.Agent)
    a.profile = Profile(tmp_path / "profile.json")
    a.profile.data.update({"name": "Mayank Churiwal", "phone": "+91 98765 43210",
                           "cards": [{"id": "hdfc", "label": "HDFC Visa", "last4": "1234"}], **(profile or {})})
    a.vault = vault if vault is not None else FakeVault({"card.hdfc.cvv": "123"})
    a.secrets = Secrets(a.profile.hidden_values())
    page = page or fake_page()
    a.browser = Mock(fresh=Mock(return_value=True), observe=Mock(return_value=page), act=Mock(return_value={}),
                     screenshot=Mock(return_value="iVBORw0KGgo="), focused_label=Mock(return_value=""))
    a.asked, a.told, queue = [], [], list(replies)
    a.asker = lambda message: a.asked.append(message) or (queue.pop(0) if queue else {"cancel": True})
    a.notify = a.told.append
    a.provide_file, a.screenshots, a.record_dir = None, False, None
    a.state = loop.Agent.new_state(a.browser, "Order Diet Coke on Instamart", "Diet Coke", page, False)
    a.state["started_at"] = time.perf_counter()
    return a


def act(a, d):
    a.state["decision"] = d
    a.state["status"] = "predicted"
    return a.command("act", {"fingerprint": a.state["page"]["fingerprint"]})


def mini_with(tmp_path, **kw):
    """A MiniAgent over the same mock browser as agent_with."""
    from assistant import driver

    a = agent_with(tmp_path, **kw)
    a.__class__ = driver.MiniAgent
    a.state.update(driver.MiniAgent.extra_state())
    return a


def out(op, target="", text="", key="", commits=False, message="", options=(), next_goal="the next step",
        plan=(), memory="", evaluation="none"):
    return {"evaluation": {"status": evaluation, "why": ""}, "memory": memory, "plan": list(plan),
            "next_goal": next_goal, "action": {"op": op, "target": target, "text": text, "key": key,
                                               "commits": commits},
            "message": message, "options": list(options)}


def script(monkeypatch, *outs):
    """Mini's steps, in order. Returns the payloads mini was shown."""
    from assistant import driver

    queue, seen = list(outs), []

    def decide(payload, image=None):
        seen.append(payload)
        return queue.pop(0), {"model": "fake", "latency_ms": 1,
                              "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}

    monkeypatch.setattr(driver.mind, "decide", decide)
    return seen


def jev_fake(monkeypatch, checks=("success",), replies=(), approvals=(), achieved="yes", final=("final", 1.0),
             matches="yes"):
    """Jev by label: grounding matches mini's words inside element labels; checks and replies are scripted."""
    from assistant import driver
    from assistant.model import action_space

    checks, replies, approvals = list(checks), list(replies), list(approvals)

    def ground(page, operation, target, next_goal, goal):
        _, heads, _ = action_space(page["actions"])
        candidates = heads.get(driver.jev.HEAD[operation], {})
        if not target:
            index = next((i for i, a in candidates.items() if a.get("node") is None), None)
            return (candidates[index], 1.0, [], index) if index else (None, 0.0, [], None)
        for index, action in candidates.items():
            if target.lower() in action["label"].lower():
                return action, 0.9, [action["label"]], index
        return None, 0.1, [a["label"] for a in list(candidates.values())[:3]], None

    monkeypatch.setattr(driver.jev, "ground", ground)
    monkeypatch.setattr(driver.jev, "check", lambda *a, **k: checks.pop(0) if len(checks) > 1 else checks[0])
    monkeypatch.setattr(driver.jev, "read_reply", lambda *a: replies.pop(0))
    monkeypatch.setattr(driver.jev, "approval", lambda *a: approvals.pop(0))
    monkeypatch.setattr(driver.jev, "achieved", lambda *a: achieved)
    monkeypatch.setattr(driver.jev, "final", lambda *a: final)
    monkeypatch.setattr(driver.jev, "matches", lambda *a: matches)


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
