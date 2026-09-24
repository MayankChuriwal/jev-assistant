"""Fakes for agent tests: a checkout page, a vault, and an agent over a mock browser. No Chrome, no models."""

import time
from unittest.mock import Mock

from assistant import agent as loop
from assistant.browser import fingerprint
from assistant.profile import Profile, Secrets, Vault


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
        Vault._login(key)
        if key not in self.values:
            raise KeyError(key)
        return self.values[key]

    def set(self, key, value):
        Vault._login(key)
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
                           "cards": [], **(profile or {})})
    a.vault = vault if vault is not None else FakeVault()
    a.secrets = Secrets(a.profile.hidden_values())
    page = page or fake_page()
    a.browser = Mock(fresh=Mock(return_value=True), observe=Mock(return_value=page), act=Mock(return_value={}),
                     screenshot=Mock(return_value="iVBORw0KGgo="), focused_label=Mock(return_value=""))
    a.asked, a.told, queue = [], [], list(replies)
    a.asker = lambda message: a.asked.append(message) or (queue.pop(0) if queue else {"cancel": True})
    a.notify = a.told.append
    a.provide_file, a.screenshots, a.record_dir = None, False, None
    def coordinate_reply(system, payload, **kwargs):
        if 'Classify the external effect' in system:
            return {'effect': 'preparation'}, {}
        return dict(objective=a.task.objective, facts=[], requirements=[], completion=[], auth=None,
                    progress=None, proposal=None, outcome=None, notice=None, recovery=None), {}
    a.initialize_task("Order Diet Coke on Instamart", journal_dir=tmp_path / "journals",
                      coordinator_client=coordinate_reply)
    a.state = loop.Agent.new_state(a.browser, "Order Diet Coke on Instamart", "Diet Coke", page, False)
    a.state["started_at"] = time.perf_counter()
    a.retain_observation(page)
    return a


def act(a, d):
    a.validate_decision(d, a.state["page"])
    a.state["decision"] = {**d, "id": __import__("uuid").uuid4().hex, "revision": a.task.revision,
                           "observation_id": next(reversed(a.task.observations))}
    a.state["status"] = "predicted"
    return a.command("act", {"fingerprint": a.state["page"]["fingerprint"]})
