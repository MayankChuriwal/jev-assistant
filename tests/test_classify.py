"""Jev classifies chat messages; the text model answers status questions and small talk."""

import json
from unittest.mock import Mock

import pytest

from assistant import jobs, model

TASKS = [{"id": "t1", "title": "Swiggy · Diet Coke", "goal": "Order Diet Coke", "step": "Checkout",
          "open_question": "Deliver to which address?"}]


def choice(ids, selected, top=1.0):
    rest = (1 - top) / max(len(ids) - 1, 1)
    return {"choice": selected, "confidence": top, "probabilities": {i: top if i == selected else rest for i in ids}}


def test_one_request_reads_intent_and_task(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        q = body["questions"]
        return {"model": "jev", "answers": {"intent": choice(q["intent"]["criteria"], "AMEND", 0.9),
                                            "task": choice(q["task"]["criteria"], "t1")}}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    chat = [{"from": "user", "text": str(n)} for n in range(10)]
    intent, task, probabilities = model.classify("make it 2 cans", TASKS, chat)
    assert (intent, task) == ("AMEND", "t1") and probabilities["AMEND"] == 0.9
    body = calls[0]
    assert set(body["questions"]["intent"]["criteria"]) == {"ANSWER", "AMEND", "NEW_TASK", "CANCEL", "STATUS", "OTHER"}
    assert set(body["questions"]["task"]["criteria"]) == {"t1", "none"}
    assert body["state"]["message"] == "make it 2 cans" and len(body["state"]["recent_chat"]) == 6


def test_invalid_answers_are_refused(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", lambda *_: {"model": "jev", "answers": {
        "intent": {"choice": "SHOUT", "confidence": 1.0, "probabilities": {"SHOUT": 1.0}}}})
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.classify("hi", TASKS, [])


@pytest.mark.parametrize("probabilities, expected", [
    ({"ANSWER": 0.9, "AMEND": 0.1}, False),
    ({"ANSWER": 0.55, "AMEND": 0.2}, True),
    ({"ANSWER": 0.62, "AMEND": 0.38}, False),
    ({"ANSWER": 0.6, "AMEND": 0.45}, True),
])
def test_ambiguity_thresholds(probabilities, expected):
    assert model.ambiguous(probabilities) is expected


def test_status_and_small_talk_get_short_replies(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    reply = json.dumps({"text": "Your Diet Coke is at checkout."})
    post = Mock(return_value={"choices": [{"message": {"content": reply}}]})
    monkeypatch.setattr(model, "post_json", post)
    assert jobs.chat("where are we?", TASKS)[0] == "Your Diet Coke is at checkout."
    assert json.loads(post.call_args.args[2]["messages"][1]["content"])["tasks"] == TASKS
    post.return_value = {"choices": [{"message": {"content": "{}"}}]}
    with pytest.raises(ValueError, match="invalid reply"):
        jobs.chat("hi", [])
