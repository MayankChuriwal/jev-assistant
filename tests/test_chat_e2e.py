"""Chat end to end in real Chrome: two goals typed into chat run in parallel. The shop order goes through login, a
wrong OTP, an address choice, and the commit gate; the other task saves a page as a PDF that arrives in chat as a
document. Models are scripted behind the real HTTP client, so every request body is checked for leaks.
Run: uv run pytest -m browser"""

import json as jsonlib
import re

import pytest
from fakes import FakeVault
from helpers import until
from test_shop import choice, jev, text_model

from assistant import agent as loop
from assistant import core as conversation
from assistant import model, questions
from assistant.channel_web import WebChannel
from assistant.profile import HOME, Profile

pytestmark = pytest.mark.browser


class Response:
    status_code, is_error = 200, False

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def webhook(**message):
    value = {"messaging_product": "whatsapp", "messages": [{"from": "local", **message}]}
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "local", "changes": [{"field": "messages", "value": value}]}]}


def tap(question, option):
    return {"type": "interactive", "context": {"id": question["x_id"]}, "interactive": {
        "type": "button_reply", "button_reply": {"id": f"q{question['x_question']}:{option}", "title": option}}}


def test_two_goals_in_chat_run_in_parallel_without_leaks(chrome, site, tmp_path, monkeypatch):
    sent = []

    def jev_chat(body):
        q = body["questions"]
        if "intent" in q:  # every free-text message this test types outside a question is a new goal
            return {"model": "fake-jev", "answers": {"intent": choice(q["intent"]["criteria"], "NEW_TASK"),
                                                     "task": choice(q["task"]["criteria"], "none")}}
        if body["state"]["page"]["title"] == "Next page":
            saved = any(a["operation"] == "SAVE_PDF" for a in body["state"]["recent_actions"])
            return {"model": "fake-jev", "answers": {
                "operation": choice(q["operation"]["criteria"], "DONE" if saved else "SAVE_PDF")}}
        return jev(body)

    def text_chat(system, payload):
        if system == questions.START:
            if "PDF" in payload["goal"]:
                return {"start_url": site["b"] + "/next.html", "title": "Second page PDF"}
            return {"start_url": site["a"] + "/shop.html", "title": "Diet Coke"}
        if system == questions.RESULT and "Second page." in payload["page"]["text"]:
            return {"text": "Saved the second page as a PDF.", "evidence": "Second page.", "achieved": True}
        return text_model(system, payload)

    def post(url, json=None, headers=None):  # the keyword httpx uses; the module is jsonlib here
        sent.append(json)
        if "typesafe" in url:
            return Response(jev_chat(json))
        answer = text_chat(json["messages"][0]["content"], jsonlib.loads(json["messages"][1]["content"]))
        return Response({"choices": [{"message": {"content": jsonlib.dumps(answer)}}]})

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(model.CLIENT, "post", post)
    profile = Profile(tmp_path / "profile.json")
    profile.data.update(name="Mayank Churiwal", phone="+91 98765 43210",
                        cards=[{"id": "hdfc", "label": "HDFC Visa", "last4": "1234"}])

    def make_agent(url, goal, title, asker, notify):  # the real Agent, with a fake vault instead of the Keychain
        return loop.Agent(url, goal, title=title, asker=asker, notify=notify, profile=profile,
                          vault=FakeVault({"card.hdfc.cvv": "123"}))

    channel = WebChannel(tmp_path / "media")
    chat = conversation.Core(channel, profile=profile, make_agent=make_agent, poll=0.05)
    count = 0

    def user(**message):
        nonlocal count
        count += 1
        chat.receive_webhook(webhook(id=f"wamid.in{count}", **message))

    def shown(check):
        return until(lambda: next((m for m in channel.since(0)[0] if check(m)), None), timeout=30)

    def buttons(m):
        if m["type"] != "interactive":
            return []
        return [b["reply"]["id"] for b in m["interactive"]["action"].get("buttons", [])]

    user(type="text", text={"body": "Order a Diet Coke"})
    user(type="text", text={"body": "Also save the second page on the other site as a PDF"})
    first = shown(lambda m: m.get("x_secret"))
    assert first, [m.get("text", {}).get("body") for m in channel.since(0)[0]]
    user(type="text", text={"body": "111111"})
    assert shown(lambda m: m.get("x_secret") and m["x_question"] != first["x_question"])  # the wrong code is re-asked
    user(type="text", text={"body": "482913"})
    address = shown(lambda m: m["type"] == "interactive"
                    and "Deliver to which address?" in m["interactive"]["body"]["text"])
    user(**tap(address, "o1"))
    confirm = shown(lambda m: any(i.endswith(":yes") for i in buttons(m)))
    user(**tap(confirm, "yes"))
    assert until(lambda: {t.status for t in chat.tasks.values()} == {"done"}, timeout=30), [
        (t.title, t.status) for t in chat.tasks.values()]

    messages = channel.since(0)[0]
    texts = " ".join(m["text"]["body"] for m in messages if m["type"] == "text")
    assert "Ordered Diet Coke for ₹79" in texts and "Saved the second page as a PDF." in texts
    [document] = [m["document"] for m in messages if m["type"] == "document"]
    assert document["filename"] == "Next page.pdf"
    assert channel.media(document["id"])[0].read_bytes().startswith(b"%PDF")
    assert chat.open is None and chat.queue == [] and until(lambda: not channel.typing)

    classified = [b["state"]["message"] for b in sent if "intent" in b.get("questions", {})]
    assert classified == ["Order a Diet Coke", "Also save the second page on the other site as a PDF"]
    everything = jsonlib.dumps(sent, ensure_ascii=False)
    for secret in ("98765", "9876543210", "Mayank", "482913", "111111"):
        assert secret not in everything, secret
    assert not re.search(r"(?<!\d)123(?!\d)", everything)
    assert "482913" not in str(chat.log) and "111111" not in str(chat.log)
    written = until(lambda: sorted((HOME / "traces").glob("*[0-9]-diet-coke.json")), timeout=15)  # at close
    trace = written[-1].read_text()
    shop = jsonlib.loads(trace)
    assert shop["status"] == "done" and shop["questions"] == 4 and shop["result"]["evidence"] == "Order placed #A1B2"
    for secret in ("98765", "Mayank", "482913", "111111"):
        assert secret not in trace, secret
