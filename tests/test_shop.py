"""One goal through login, a wrong OTP, an address choice, the commit gate, and a Keychain CVV — real Chrome,
scripted models behind the real HTTP client, so every request body is checked for leaks. Run: -m browser"""

import json as jsonlib
import re

import pytest
from fakes import FakeVault

from assistant import agent as loop
from assistant import model, questions
from assistant.model import OPERATION_HEAD
from assistant.profile import Profile

pytestmark = pytest.mark.browser


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def jev(body):
    """A scripted stand-in for Jev that reads the same state the real one would."""
    q, text = body["questions"], body["state"]["page"]["text"]
    elements = body["state"]["elements"]

    def element(label):
        return next((e for e in elements if label in e["label"]), None)

    def pick(operation, label=None):
        answers = {"operation": choice(q["operation"]["criteria"], operation)}
        if label:
            head = OPERATION_HEAD[operation]
            index = next(i for i, c in q[head]["criteria"].items() if label in c["element"])
            answers[head] = choice(q[head]["criteria"], index)
        return {"model": "fake-jev", "answers": answers}

    if "Order placed" in text:
        return pick("DONE")
    if "Log in" in text:
        return pick("TYPE_TEXT", "Phone number") if not element("Phone number")["value"] else pick("CLICK", "Continue")
    if "Verify" in text:
        return pick("TYPE_TEXT", "One-time code") if not element("One-time code")["value"] else pick("CLICK", "Verify")
    if not any(e.get("checked") == "true" for e in elements if e.get("role") == "radio"):
        return pick("ASK_USER")
    if not element("CVV")["value"]:
        return pick("TYPE_TEXT", "CVV")
    return pick("CLICK", "Pay ₹79")


def text_model(system, payload):
    """A scripted stand-in for GPT-4.1 mini, one answer per job."""
    if system == questions.FIELD_VALUE:
        label = payload["field"]["label"]
        if label == "Phone number":
            return {"profile": "phone"}
        if label == "CVV":
            return {"ask": "Enter the CVV for this payment.", "secret": True}
        return {"ask": "Enter the code sent to your phone.", "secret": True}
    if system == questions.COMPOSE:
        index = {e["label"].split(" —")[0]: e["index"] for e in payload["elements"] if e.get("role") == "radio"}
        return {"kind": "choice", "text": "Deliver to which address?", "options": [
            {"label": "Home", "description": "12 MG Road", "element": index["Home"]},
            {"label": "Office", "description": "4 Residency Road", "element": index["Office"]}]}
    if system == questions.CONFIRM_CARD:
        return {"action_label": "Pay ₹79", "amount": "₹79",
                "lines": ["Diet Coke 300 ml × 1", "Home — 12 MG Road", "HDFC Visa ••1234"]}
    if system == questions.OUTCOME:
        placed = "Order placed #A1B2" in payload["page"]["text"]
        return {"outcome": "success", "evidence": "Order placed #A1B2"} if placed else {"outcome": "pending",
                                                                                     "evidence": ""}
    if system == questions.RESULT:
        return {"text": "Ordered Diet Coke for ₹79; it arrives in 12 minutes.", "evidence": "Order placed #A1B2",
                "achieved": True}
    raise AssertionError(f"unexpected job: {system[:40]}")


def test_login_otp_address_confirm_and_temporary_cvv_without_leaks(chrome, site, tmp_path, monkeypatch):
    sent = []

    class Response:
        status_code, is_error = 200, False

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def post(url, json=None, headers=None):  # the keyword httpx uses; the module is jsonlib here
        sent.append(json)
        if "typesafe" in url:
            return Response(jev(json))
        system, user = json["messages"][0]["content"], json["messages"][1]["content"]
        answer = text_model(system, jsonlib.loads(user))
        return Response({"choices": [{"message": {"content": jsonlib.dumps(answer)}}]})

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(model.CLIENT, "post", post)
    codes = ["111111", "482913", "123"]

    def asker(message):
        if message["kind"] == "secret":
            return {"text": codes.pop(0)}
        if message["kind"] == "choice":
            return {"option": "o1"}
        if message["kind"] == "confirm":
            return {"option": "yes"}
        raise AssertionError(message)

    told = []
    profile = Profile(tmp_path / "profile.json")
    profile.data.update(name="Mayank Churiwal", phone="+91 98765 43210",
                        cards=[{"id": "hdfc", "label": "HDFC Visa", "last4": "1234"}])
    with loop.Agent(site["a"] + "/shop.html", "Order a Diet Coke", asker=asker, notify=told.append, profile=profile,
                    vault=FakeVault()) as agent:
        for state in agent.run():
            assert len(state["decisions"]) < 40
        log = agent.browser.evaluate("window.log")
    assert agent.state["status"] == "done", agent.state["messages"][-3:]
    assert log == ["otp:111111", "otp:482913", "pay:home:3"]
    assert [m["kind"] for m in agent.state["messages"] if m["from"] == "agent" and m["kind"] != "info"] == [
        "secret", "secret", "choice", "secret", "confirm", "result"]
    assert told[-1]["evidence"] == "Order placed #A1B2"
    everything = jsonlib.dumps(sent, ensure_ascii=False)
    for secret in ("98765", "9876543210", "Mayank", "482913", "111111"):
        assert secret not in everything, secret
    assert not re.search(r"(?<!\d)123(?!\d)", everything)
    assert "[profile:phone]" in everything and "[secret:" in everything
