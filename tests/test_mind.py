"""Mini's step contract: one strict-JSON reply per browser step. Fake HTTP only."""

import json

import pytest

from assistant import mind, model


def reply(monkeypatch, content, finish="stop"):
    sent = []

    def post(url, key, body):
        sent.append(body)
        return {"choices": [{"message": {"content": json.dumps(content)}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(model, "post_json", post)
    return sent


STEP = {"evaluation": {"status": "none", "why": ""}, "memory": "", "plan": ["Open ride booking"],
        "next_goal": "Open the pickup box", "action": {"op": "CLICK", "target": "[3] Pickup location", "text": "",
                                                      "key": "", "commits": False},
        "message": "", "options": []}


def test_a_step_is_asked_for_with_a_strict_schema(monkeypatch):
    sent = reply(monkeypatch, STEP)
    out, meta = mind.decide({"goal": "Book a cab"})
    assert out == STEP and meta["usage"]["prompt_tokens"] == 10
    fmt = sent[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["action"]["properties"]["op"]["enum"] == mind.OPS
    assert list(schema["properties"])[0] == "evaluation"  # reasoning comes before the action


def test_a_cut_off_reply_is_an_error_not_a_step(monkeypatch):
    reply(monkeypatch, STEP, finish="length")
    with pytest.raises(ValueError, match="cut off"):
        mind.decide({"goal": "Book a cab"})


def test_plain_json_jobs_still_ask_for_a_json_object(monkeypatch):
    sent = reply(monkeypatch, {"ok": True})
    model.text_json("sys", {"a": 1})
    assert sent[0]["response_format"] == {"type": "json_object"}
