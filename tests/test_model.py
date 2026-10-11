"""Model transports and the action space. No paid APIs: the HTTP client or post_json is replaced in every test."""

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


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def shop():
    return {
        "url": "https://shop.test/", "title": "Shop", "text": "Shop", "scroll": {},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "searchbox", "value": "", "node": 1},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "searchbox", "value": "", "node": 1},
            {"id": "e3", "kind": "click", "label": "Diet Coke ₹40", "role": "clickable", "value": "", "node": 2},
            {"id": "e4", "kind": "select", "label": "Size → 300 ml", "role": "combobox", "value": "300",
             "current_value": "600 ml", "node": 3},
            {"id": "e5", "kind": "select", "label": "Size → 1 L", "role": "combobox", "value": "1000",
             "current_value": "600 ml", "node": 3},
            {"id": "e6", "kind": "drag", "label": "Alpha", "role": "draggable", "node": 4},
            {"id": "e7", "kind": "scroll_down", "label": "Panel: Orders", "role": "region", "node": 5},
            {"id": "e8", "kind": "upload", "label": "File input: Statement", "role": "file", "node": 6},
            {"id": "e9", "kind": "click", "label": "Quantity", "role": "slider", "value": "1", "node": 7},
            {"id": "page_scroll_down", "kind": "scroll_down", "label": "Page", "role": "page", "node": None},
            {"id": "wait", "kind": "wait", "label": "Wait for the page to update", "node": None},
            {"id": "key_page", "kind": "key", "label": "The page (focused element)", "role": "page", "node": None},
            {"id": "reload", "kind": "reload", "label": "Reload", "node": None},
        ],
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_and_compatible_targets_per_question():
    elements, heads, controls = model.action_space(shop()["actions"])
    assert [e["index"] for e in elements] == ["1", "2", "3", "4", "5", "6", "7"]
    assert elements[0]["operations"] == ["TYPE_TEXT", "PRESS_KEY", "CLICK", "DOUBLE_CLICK", "RIGHT_CLICK", "HOVER"]
    assert heads["type_text_target"] == {"1": shop()["actions"][0]}
    assert set(heads["click_target"]) == {"1", "2", "7"}
    assert set(heads["press_key_target"]) == {"1", "7", "key_page"}
    assert set(heads["select_target"]) == {"3:1", "3:2"}
    assert elements[2]["options"][1] == {"index": "3:2", "label": "Size → 1 L", "value": "1000"}
    assert set(heads["scroll_down_target"]) == {"5", "page_scroll_down"}
    assert set(heads["drag_target"]) == {"1", "2", "5", "7"}
    assert set(controls) == {"WAIT", "RELOAD"}


def test_text_requests_use_only_parameters_openai_accepts(monkeypatch):
    post = reply('{"text": "Zurich"}')
    monkeypatch.setattr(model, "post_json", post)
    out, _ = model.text_json("system", {"goal": 'Fly from "Zurich"'})
    body = post.call_args.args[2]
    assert out == {"text": "Zurich"} and set(body) == {"model", "max_tokens", "response_format", "messages"}
    assert body["model"] == "gpt-4.1-mini" and body["response_format"] == {"type": "json_object"}


def test_missing_key_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        model.text_json("system", {"goal": "Find a flight"})


def test_every_model_request_is_redacted_for_the_current_task(monkeypatch):
    from assistant.privacy import SECRETS, Secrets

    sent = []

    class Response:
        status_code, is_error = 200, False

        def json(self):
            return {"choices": [{"message": {"content": '{"text": "ok"}'}}]}

    monkeypatch.setattr(model.CLIENT, "post", lambda url, json, headers: sent.append(json) or Response())
    token = SECRETS.set(Secrets([("+91 98765 43210", "[profile:phone]"), ("9876543210", "[profile:phone]")]))
    try:
        model.text_json("system", {"goal": "Call 98765 43210 about +91 98765 43210"})
    finally:
        SECRETS.reset(token)
    model.text_json("system", {"goal": "no task: 98765 43210"})
    assert "98765" not in json.dumps(sent[0]) and "[profile:phone]" in json.dumps(sent[0])
    assert "98765 43210" in json.dumps(sent[1])


def test_text_model_payloads_are_redacted_before_they_are_encoded(monkeypatch):
    from assistant.privacy import SECRETS, Secrets

    post = reply('{"text": "ok"}')
    monkeypatch.setattr(model, "post_json", post)  # below the text model, above post_json's own redaction
    token = SECRETS.set(Secrets([("José Müller", "[profile:name]"), ('ab"cd\\ef', "[secret:x]")]))
    try:
        model.text_json("system", {"goal": 'José Müller typed ab"cd\\ef'})
    finally:
        SECRETS.reset(token)
    user = post.call_args.args[2]["messages"][1]["content"]
    assert "Jos" not in user and "cd" not in user
    assert "[profile:name]" in user and "[secret:x]" in user


def test_a_dropped_connection_is_tried_again(monkeypatch):
    import httpx

    calls = []

    class Response:
        status_code, is_error = 200, False

        def json(self):
            return {"ok": True}

    def flaky(url, json=None, headers=None):
        calls.append(url)
        if len(calls) < 3:
            raise httpx.ConnectError("connection reset")
        return Response()

    monkeypatch.setattr(model.CLIENT, "post", flaky)
    monkeypatch.setattr(model.time, "sleep", lambda seconds: None)
    assert model.post_json("https://example.test", "key", {}) == {"ok": True} and len(calls) == 3


def test_a_secret_that_survives_redaction_is_never_sent(monkeypatch):
    from assistant.privacy import SECRETS, Secrets

    class Broken(Secrets):
        def redact(self, obj):
            return obj  # a redaction bug: the scan must still stop the request

    monkeypatch.setattr(model.CLIENT, "post", lambda *a, **k: pytest.fail("a secret was sent"))
    token = SECRETS.set(Broken([("9876543210", "[profile:phone]")]))
    try:
        with pytest.raises(RuntimeError, match="secret survived redaction"):
            model.post_json("https://api.openai.com/v1/chat/completions", "k", {"goal": "call 98765 43210"})
    finally:
        SECRETS.reset(token)


def test_each_call_is_charged_to_the_running_task(monkeypatch):
    class Response:
        status_code, is_error = 200, False

        def __init__(self, usage):
            self.usage = usage

        def json(self):
            return {"usage": self.usage}

    replies = [Response({"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 400},
                         "completion_tokens": 100}), Response({})]
    monkeypatch.setattr(model.CLIENT, "post", lambda url, json, headers: replies.pop(0))
    meter = model.new_meter()
    token = model.METER.set(meter)
    try:
        model.post_json("https://api.openai.com/v1/chat/completions", "k", {"messages": []})
        model.post_json("https://api.typesafe.ai/v1/systemone", "k", {"state": "x" * 400})
    finally:
        model.METER.reset(token)
    assert meter["mini_calls"] == 1 and meter["jev_calls"] == 1
    assert meter["mini"] == pytest.approx(600 * 0.40e-6 + 400 * 0.10e-6 + 100 * 1.60e-6)
    assert meter["jev"] > 0 and model.spent(meter) == meter["mini"] + meter["jev"]


SCHEMA = {"type": "object", "additionalProperties": False, "required": ["text"],
          "properties": {"text": {"type": "string"}}}


def completion(message, finish="stop"):
    return Mock(return_value={"choices": [{"message": message, "finish_reason": finish}], "usage": {}})


def test_a_schema_holds_the_reply_to_strict_json_schema(monkeypatch):
    post = reply('{"text": "ok"}')
    monkeypatch.setattr(model, "post_json", post)
    model.text_json("system", {"goal": "g"}, schema=SCHEMA)
    assert post.call_args.args[2]["response_format"] == {
        "type": "json_schema", "json_schema": {"name": "reply", "strict": True, "schema": SCHEMA}}
    model.text_json("system", {"goal": "g"}, schema=SCHEMA, schema_name="plan")
    assert post.call_args.args[2]["response_format"]["json_schema"]["name"] == "plan"


def test_a_reply_cut_off_at_the_token_limit_is_an_error_not_a_partial_step(monkeypatch):
    monkeypatch.setattr(model, "post_json", completion({"content": '{"text": "ok'}, finish="length"))
    with pytest.raises(ValueError, match="cut off"):
        model.text_json("system", {"goal": "g"}, schema=SCHEMA)


def test_a_refused_reply_is_an_error_even_with_content(monkeypatch):
    monkeypatch.setattr(model, "post_json", completion({"content": '{"text": "ok"}', "refusal": "I can't help"}))
    with pytest.raises(ValueError, match="cut off or refused"):
        model.text_json("system", {"goal": "g"}, schema=SCHEMA)


@pytest.mark.parametrize("content", ["not json at all", '["text", "ok"]', '"ok"', "", None])
def test_a_reply_that_is_not_a_json_object_is_an_error(monkeypatch, content):
    monkeypatch.setattr(model, "post_json", completion({"content": content}))
    with pytest.raises(ValueError, match="no JSON object"):
        model.text_json("system", {"goal": "g"})


def test_a_reply_with_no_choices_is_an_error(monkeypatch):
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": []}))
    with pytest.raises(ValueError, match="no JSON object"):
        model.text_json("system", {"goal": "g"})


def test_criteria_carries_each_options_state_and_current_value():
    candidates = {
        "1": {"id": "e1", "kind": "click", "label": "Seat 12A", "role": "option", "node": 4, "value": "12A",
              "checked": True, "selected": False, "expanded": False, "popup": "menu", "current": "step"},
        "2": {"id": "e2", "kind": "select", "label": "Size → 300 ml", "role": "combobox", "node": 5, "value": "300",
              "current_value": "600 ml"},
        "page_scroll_down": {"id": "page_scroll_down", "kind": "scroll_down", "label": "Page", "role": "page",
                             "node": None}}
    assert model.criteria(candidates) == {
        "1": {"element": "[1] Seat 12A", "current_value": "12A", "role": "option", "checked": True,
              "selected": False, "expanded": False, "popup": "menu", "current": "step"},
        "2": {"element": "[2] Size → 300 ml", "current_value": "600 ml", "role": "combobox"},
        "page_scroll_down": {"element": "Page", "current_value": "", "role": "page"}}


def test_a_reply_whose_usage_numbers_are_null_is_charged_as_zero():
    meter = model.new_meter()
    token = model.METER.set(meter)
    try:
        model.charge({}, {"prompt_tokens": None, "completion_tokens": None,
                          "prompt_tokens_details": {"cached_tokens": None}}, jev=False)
        model.charge({"x": "y" * 400}, {"input_tokens": None, "prompt_tokens": None}, jev=True)
    finally:
        model.METER.reset(token)
    assert meter["mini"] == 0.0 and meter["mini_calls"] == 1 and meter["jev_calls"] == 1 and meter["jev"] > 0


# ---- the brain's step: one Responses API call that must call one tool

TOOLS = [{"type": "function", "name": "reply", "description": "Talk to the user.", "strict": True,
          "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                         "additionalProperties": False}}]


def called(name="reply", arguments='{"text": "Hi"}', **extra):
    return Mock(return_value={"status": "completed", "error": None, "usage": {"input_tokens": 9}, **extra,
                              "output": [{"type": "reasoning", "id": "rs_1", "summary": []},
                                         {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": name,
                                          "arguments": arguments, "status": "completed"}]})


@pytest.fixture
def brain_env(monkeypatch):
    monkeypatch.delenv("BRAIN_MODEL", raising=False)
    monkeypatch.delenv("BRAIN_EFFORT", raising=False)


def test_a_brain_step_must_call_one_tool_and_keeps_nothing_at_openai(monkeypatch, brain_env):
    post = called()
    monkeypatch.setattr(model, "post_json", post)
    items = [{"role": "user", "content": "Cancel my subscriptions"}]
    call, meta = model.agent_turn("You are Jev.", items, TOOLS)
    url, _key, body = post.call_args.args
    assert url == "https://api.openai.com/v1/responses"
    assert body == {"model": "gpt-5.6-luna", "instructions": "You are Jev.", "input": items, "tools": TOOLS,
                    "tool_choice": "required", "parallel_tool_calls": False, "reasoning": {"effort": "low"},
                    "store": False, "max_output_tokens": 4000}
    assert call == {"type": "function_call", "call_id": "call_1", "name": "reply", "arguments": '{"text": "Hi"}'}
    assert meta["model"] == "gpt-5.6-luna" and "latency_ms" in meta


def test_the_last_brain_step_may_only_reply(monkeypatch, brain_env):
    post = called()
    monkeypatch.setattr(model, "post_json", post)
    model.agent_turn("You are Jev.", [], TOOLS, final=True)
    assert post.call_args.args[2]["tool_choice"] == {"type": "function", "name": "reply"}


def test_the_brain_model_and_its_effort_come_from_the_environment(monkeypatch, brain_env):
    monkeypatch.setenv("BRAIN_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("BRAIN_EFFORT", "none")
    post = called()
    monkeypatch.setattr(model, "post_json", post)
    model.agent_turn("You are Jev.", [], TOOLS)
    body = post.call_args.args[2]
    assert body["model"] == "gpt-5.4-mini" and body["reasoning"] == {"effort": "none"}


def test_a_brain_reply_cut_off_is_an_error(monkeypatch, brain_env):
    monkeypatch.setattr(model, "post_json", called(status="incomplete",
                                                   incomplete_details={"reason": "max_output_tokens"}))
    with pytest.raises(ValueError, match="cut off"):
        model.agent_turn("You are Jev.", [], TOOLS)


def test_a_brain_reply_with_no_tool_call_is_an_error(monkeypatch, brain_env):
    monkeypatch.setattr(model, "post_json", Mock(return_value={"status": "completed", "error": None, "output": [
        {"type": "message", "content": [{"type": "output_text", "text": "Hi"}]}]}))
    with pytest.raises(ValueError, match="no tool"):
        model.agent_turn("You are Jev.", [], TOOLS)


def test_a_brain_reply_carrying_an_error_is_an_error(monkeypatch, brain_env):
    monkeypatch.setattr(model, "post_json", Mock(return_value={"status": "failed", "output": [],
                                                               "error": {"message": "server overloaded"}}))
    with pytest.raises(ValueError, match="server overloaded"):
        model.agent_turn("You are Jev.", [], TOOLS)


def test_the_brain_needs_the_openai_key(monkeypatch, brain_env):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        model.agent_turn("You are Jev.", [], TOOLS)


def test_a_brain_step_is_redacted_on_its_way_out(monkeypatch, brain_env):
    from assistant.privacy import SECRETS, Secrets

    sent = []

    class Response:
        status_code, is_error = 200, False

        def json(self):
            return called().return_value

    monkeypatch.setattr(model.CLIENT, "post", lambda url, json, headers: sent.append(json) or Response())
    token = SECRETS.set(Secrets([("98765 43210", "[profile:phone]")]))
    try:
        model.agent_turn("You are Jev.", [{"role": "user", "content": "my number is 98765 43210"}], TOOLS)
    finally:
        SECRETS.reset(token)
    assert "98765" not in json.dumps(sent[0]) and "[profile:phone]" in json.dumps(sent[0])
