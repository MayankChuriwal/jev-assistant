"""Model contracts. No paid APIs: post_json is replaced in every test."""

import json
from unittest.mock import Mock

import pytest

from assistant import model


def reply(content):
    return Mock(return_value={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 9}})


@pytest.fixture(autouse=True)
def openai_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("TEXT_MODEL", raising=False)
    monkeypatch.delenv("TEXT_MODEL_BASE_URL", raising=False)


def test_text_request_uses_only_parameters_openai_accepts(monkeypatch):
    post = reply('{"text": "Zurich"}')
    monkeypatch.setattr(model, "post_json", post)
    value, meta = model.field_text({"goal": 'Fly from "Zurich" to London'})
    url, key, body = post.call_args.args
    assert value == "Zurich" and meta["model"] == "gpt-4.1-mini"
    assert url == "https://api.openai.com/v1/chat/completions" and key == "sk-test"
    assert set(body) == {"model", "messages", "response_format", "max_tokens"}
    assert body["response_format"] == {"type": "json_object"}
    assert json.loads(body["messages"][1]["content"])["goal"] == 'Fly from "Zurich" to London'


def test_missing_key_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        model.field_text({"goal": "Find a flight"})


@pytest.mark.parametrize(
    "content",
    ["Thinking: Zurich", '["Zurich"]', '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}',
     '{"text":" "}'],
)
def test_invalid_field_values_type_nothing(monkeypatch, content):
    monkeypatch.setattr(model, "post_json", reply(content))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_url_value_returns_an_http_address(monkeypatch):
    post = reply('{"url": "https://www.swiggy.com/instamart"}')
    monkeypatch.setattr(model, "post_json", post)
    page = {"url": "https://a.test/", "title": "A", "text": "Home " * 2000}
    assert model.url_value("Order Diet Coke", page, [{"action": "Search", "text": "coke"}])[0] == (
        "https://www.swiggy.com/instamart"
    )
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == "Order Diet Coke" and len(sent["page"]["text"]) == 3000


@pytest.mark.parametrize(
    "content",
    ['{"url": "javascript:alert(1)"}', '{"url": "file:///etc/passwd"}', '{"url": "swiggy.com"}',
     '{"url": "https://x.test", "why": "because"}', '{"url": null}'],
)
def test_url_value_rejects_everything_else(monkeypatch, content):
    monkeypatch.setattr(model, "post_json", reply(content))
    with pytest.raises(ValueError, match="nothing opened"):
        model.url_value("Order Diet Coke", {"url": "https://a.test/", "title": "A", "text": ""}, [])
