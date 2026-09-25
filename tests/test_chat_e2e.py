"""Chat end to end in real Chrome: two goals typed into chat run in parallel. The shop order goes through login, a
wrong OTP, an address choice, and the commit gate; the other task saves a page as a PDF that arrives in chat as a
document. Models are scripted behind the real HTTP client, so every request body is checked for leaks.
Run: uv run pytest -m browser"""

import json as jsonlib
import re
import threading

import httpx
import pytest
from fakes import FakeVault
from helpers import until
from reliability_fakes import assessment, current_evidence, fixture_agent_class
from test_shop import choice, jev, text_model

from assistant import core as conversation
from assistant import model, questions, server
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


def test_two_goals_in_chat_run_in_parallel_without_leaks(chrome, site, tmp_path, monkeypatch, request):
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
        if system.startswith('Extract only explicit user intent'):
            return dict(provider=None, compare=False, provider_quote='', compare_quote='')
        if system == questions.INTERPRET_MESSAGE:
            return dict(intent='NEW_TASK', task_id=None, ambiguous=False, reply='')
        if system == questions.PREFLIGHT:
            if 'PDF' in payload['goal']:
                return {'requirements': [], 'completion': [dict(
                    id='pdf', description='Deliver second page PDF', kind='artifact',
                    material_keys=['document'], transfer_direction='download')]}
            return {'requirements': [], 'completion': [dict(
                id='result', description='Order Diet Coke', kind='action',
                material_keys=['product'], transfer_direction=None)]}
        if system == questions.COORDINATE and 'PDF' in payload['task']['goal']:
            task = payload['task']
            out = assessment()
            ids = current_evidence(task)
            if payload['event'] == 'start' and not task['completion']:
                out['completion'] = [dict(id='pdf', description='Deliver second page PDF', kind='artifact',
                    material_keys=['document'], evidence_ids=[], execution_id=None, artifact_id=None,
                    transfer_direction='download', delivery_required=True)]
            if 'Second page.' in payload['untrusted_page']['text']:
                out['facts'] = [dict(key='document', value='Second page.', source='page', scope='task', revision=0,
                                    depends_on=[], evidence=ids, valid=True)]
            if payload['event'] == 'completion':
                out['notice'] = dict(kind='result-detail', text='Saved the second page as a PDF.',
                                     evidence_ids=ids, requirement_ids=[])
            return out
        if system == questions.COMPLETION_PROOF and 'PDF' in payload['task']['goal']:
            task = payload['task']
            ids = [eid for eid, row in task['evidence'].items()
                   if row['kind'] == 'browser-event' and row['observation_id'] == task['observation_ids'][-1]]
            execution = next(eid for eid, row in payload['executions'].items() if row['operation'] == 'SAVE_PDF')
            return {'bindings': [dict(requirement_id='pdf', evidence_ids=ids, execution_id=execution,
                                      artifact_id=payload['artifact_ids'][0])]}
        if system == questions.START:
            if "PDF" in payload["goal"]:
                return {"start_url": site["b"] + "/next.html", "title": "Second page PDF"}
            return {"start_url": site["a"] + "/shop.html", "title": "Diet Coke"}
        if system == questions.RESULT and "Second page." in payload["page"]["text"]:
            return {"text": "Saved the second page as a PDF.", "evidence": "Second page.", "achieved": True}
        result = text_model(system, payload)
        if system == questions.COORDINATE and payload['event'] == 'completion':
            result['notice'] = dict(kind='result-detail', text='Ordered Diet Coke for ₹79; it arrives in 12 minutes.',
                                   evidence_ids=current_evidence(payload['task']), requirement_ids=[])
        return result

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

    def make_agent(url, goal, title, asker, notify, *, task):
        return fixture_agent_class(channel)(url, goal, title=title, asker=asker, notify=notify, profile=profile,
                          vault=FakeVault(), task=task.flow)

    profile.data['defaults'] = {'provider': dict(value='Shop', consent=True, source='user', scope='task')}
    channel = WebChannel(tmp_path / "media")
    chat = conversation.Core(channel, profile=profile, make_agent=make_agent, poll=0.05)
    httpd = server.ChatServer(chat, channel, port=0, token='fixture-token')
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    client = httpx.Client(base_url=f'http://127.0.0.1:{httpd.server_port}',
                         headers={'X-Jev-Token': 'fixture-token'}, timeout=10)
    request.addfinalizer(httpd.server_close)
    request.addfinalizer(httpd.shutdown)
    request.addfinalizer(client.close)
    count = 0

    def user(**message):
        nonlocal count
        count += 1
        response = client.post("/webhook", json=webhook(id=f"wamid.in{count}", **message))
        assert response.status_code == 200, response.text

    def shown(check):
        return until(lambda: next((m for m in client.get("/outbox").json()["messages"] if check(m)), None), timeout=30)

    def buttons(m):
        if m["type"] != "interactive":
            return []
        return [b["reply"]["id"] for b in m["interactive"]["action"].get("buttons", [])]

    user(type="text", text={"body": "Order a Diet Coke"})
    user(type="text", text={"body": "Also save the second page on the other site as a PDF"})
    first = shown(lambda m: m.get("x_secret"))
    assert first, [m.get("text", {}).get("body") for m in channel.since(0)[0]]
    user(type="text", text={"body": "111111"}, x_question_id=str(first["x_question"]), x_secret_reply=True)
    second = shown(lambda m: m.get("x_secret") and m["x_question"] != first["x_question"])
    assert second  # the wrong code is re-asked
    user(type="text", text={"body": "482913"}, x_question_id=str(second["x_question"]), x_secret_reply=True)
    address = shown(lambda m: m["type"] == "interactive"
                    and "Home" in str(m["interactive"]) and "Office" in str(m["interactive"]))
    assert address, [(t.status, t.agent.state if t.agent else None) for t in chat.tasks.values()]
    user(**tap(address, "o1"))
    confirm = shown(lambda m: any(i.endswith(":yes") for i in buttons(m)))
    assert confirm
    user(**tap(confirm, "yes"))
    cvv = shown(lambda m: m.get("x_secret") and "CVV" in m.get("text", {}).get("body", ""))
    assert cvv
    user(type="text", text={"body": "123"}, x_question_id=str(cvv["x_question"]), x_secret_reply=True)
    assert until(lambda: {t.status for t in chat.tasks.values()} == {"done"}, timeout=30), [
        (t.title, t.status) for t in chat.tasks.values()]

    messages = channel.since(0)[0]
    texts = " ".join(m["text"]["body"] for m in messages if m["type"] == "text")
    assert "Ordered Diet Coke for ₹79" in texts and "Saved the second page as a PDF." in texts
    [document] = [m["document"] for m in messages if m["type"] == "document"]
    assert document["filename"] == "Next page.pdf"
    assert channel.media(document["id"])[0].read_bytes().startswith(b"%PDF")
    assert chat.open is None and chat.queue == [] and until(lambda: not channel.typing)

    classified = [jsonlib.loads(b["messages"][1]["content"])["message"] for b in sent
                  if b.get("messages", [{}])[0].get("content") == questions.INTERPRET_MESSAGE]
    assert classified == ["Order a Diet Coke", "Also save the second page on the other site as a PDF"]
    everything = jsonlib.dumps(sent, ensure_ascii=False)
    for secret in ("98765", "9876543210", "Mayank", "482913", "111111"):
        assert secret not in everything, secret
    assert not re.search(r"(?<![\da-f.])123(?![\da-f.])", everything)
    assert "482913" not in str(chat.log) and "111111" not in str(chat.log)
    written = until(lambda: sorted((HOME / "traces").glob("*[0-9]-diet-coke.json")), timeout=15)  # at close
    trace = written[-1].read_text()
    shop = jsonlib.loads(trace)
    assert shop["status"] == "done" and shop["questions"] == 5 and bool(shop["result"]["evidence"])
    for secret in ("98765", "Mayank", "482913", "111111"):
        assert secret not in trace, secret
