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


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def jev(monkeypatch, pick):
    """Fake TypeSafe. `pick(questions)` returns the answers; every request body is recorded."""
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {"model": "test", "answers": pick(body["questions"])}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    return calls


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


def test_offered_operations_follow_the_page(monkeypatch):
    calls = jev(monkeypatch, lambda q: {"operation": choice(q["operation"]["criteria"], "WAIT")})
    model.choose(shop(), "Order Diet Coke", [])
    offered = set(calls[0]["questions"]["operation"]["criteria"])
    assert offered == {"CLICK", "DOUBLE_CLICK", "RIGHT_CLICK", "HOVER", "TYPE_TEXT", "PRESS_KEY", "SELECT", "DRAG",
                       "SCROLL_DOWN", "UPLOAD", "WAIT", "RELOAD", "ASK_USER", "CONFIRM", "NOTE", "DONE", "BLOCKED"}
    assert "drag_source" in calls[0]["questions"] and "press_key_key" in calls[0]["questions"]
    lonely = {**shop(), "actions": [a for a in shop()["actions"] if a["kind"] not in {"click", "scroll_down"}]}
    calls = jev(monkeypatch, lambda q: {"operation": choice(q["operation"]["criteria"], "WAIT")})
    model.choose(lonely, "Order Diet Coke", [])
    assert "DRAG" not in calls[0]["questions"]["operation"]["criteria"]
    assert "drag_source" not in calls[0]["questions"]


def test_all_questions_are_one_request_and_only_the_chosen_head_executes(monkeypatch):
    def pick(q):
        return {
            "operation": choice(q["operation"]["criteria"], "TYPE_TEXT"),
            "type_text_target": choice(["1"], "1"),
            "click_target": {"choice": "invented"},
        }

    calls = jev(monkeypatch, pick)
    d = model.choose(shop(), "Find Diet Coke", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"


def test_click_cannot_consume_a_text_target(monkeypatch):
    jev(monkeypatch, lambda q: {
        "operation": choice(q["operation"]["criteria"], "CLICK"),
        "click_target": choice(["1", "2", "7", "999"], "999"),
    })
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(shop(), "Find Diet Coke", [])


def test_drag_reads_its_drop_question_and_rejects_dropping_on_itself(monkeypatch):
    jev(monkeypatch, lambda q: {
        "operation": choice(q["operation"]["criteria"], "DRAG"),
        "drag_source": choice(["4"], "4"),
        "drag_target": choice(["1", "2", "5", "7"], "5"),
    })
    d = model.choose(shop(), "Move Alpha into the orders panel", [])
    assert d["choice"] == "e6" and d["drop"] == "e7"
    page = shop()
    page["actions"][6] = {**page["actions"][6], "kind": "click", "node": 4}
    jev(monkeypatch, lambda q: {
        "operation": choice(q["operation"]["criteria"], "DRAG"),
        "drag_source": choice(["4"], "4"),
        "drag_target": choice(q["drag_target"]["criteria"], "4"),
    })
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page, "Move Alpha", [])


def test_press_key_reads_its_key_question(monkeypatch):
    jev(monkeypatch, lambda q: {
        "operation": choice(q["operation"]["criteria"], "PRESS_KEY"),
        "press_key_target": choice(["1", "7", "key_page"], "1"),
        "press_key_key": choice(q["press_key_key"]["criteria"], "Enter"),
    })
    d = model.choose(shop(), "Search for Diet Coke", [])
    assert d["choice"] == "e1" and d["key"] == "Enter"


def test_select_and_control_choices_map_to_observed_actions(monkeypatch):
    jev(monkeypatch, lambda q: {
        "operation": choice(q["operation"]["criteria"], "SELECT"),
        "select_target": choice(["3:1", "3:2"], "3:2"),
    })
    assert model.choose(shop(), "Pick 1 L", [])["choice"] == "e5"
    jev(monkeypatch, lambda q: {"operation": choice(q["operation"]["criteria"], "RELOAD")})
    d = model.choose(shop(), "Pick 1 L", [])
    assert d["choice"] == "reload" and d["probabilities"] == {"reload": 1.0}


def test_target_questions_carry_state_and_the_full_rules(monkeypatch):
    page = shop()
    page["actions"].insert(0, {"id": "t", "kind": "click", "label": "Free cancellation", "node": 30,
                               "role": "checkbox", "checked": "true"})

    def pick(q):
        target = q["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert q["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {"operation": choice(q["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3")}

    jev(monkeypatch, pick)
    assert model.choose(page, "Search with free cancellation", [])["choice"] == "e3"  # index 3 is node 2


FIELD = {"goal": "Log in", "profile": {"profile_keys": ["phone"]}, "allowed": ["card.hdfc.cvv", "answer1"]}


def test_text_request_uses_only_parameters_openai_accepts(monkeypatch):
    post = reply('{"text": "Zurich"}')
    monkeypatch.setattr(model, "post_json", post)
    value, meta = model.field_value({"goal": 'Fly from "Zurich" to London'})
    url, key, body = post.call_args.args
    assert value == ("text", "Zurich") and meta["model"] == "gpt-4.1-mini"
    assert url == "https://api.openai.com/v1/chat/completions" and key == "sk-test"
    assert set(body) == {"model", "messages", "response_format", "max_tokens"}
    assert body["response_format"] == {"type": "json_object"}


def test_missing_key_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        model.field_value({"goal": "Find a flight"})


@pytest.mark.parametrize("content, expected", [
    ('{"text": "Zurich"}', ("text", "Zurich")),
    ('{"profile": "phone"}', ("profile", "phone")),
    ('{"secret": "card.hdfc.cvv"}', ("secret", "card.hdfc.cvv")),
    ('{"ask": "Enter the OTP", "secret": true}', ("ask", "Enter the OTP", True, None)),
    ('{"ask": "Card number?", "secret": true, "save_as": "card.number"}', ("ask", "Card number?", True, "card.number")),
])
def test_field_values_take_one_of_four_forms(monkeypatch, content, expected):
    monkeypatch.setattr(model, "post_json", reply(content))
    assert model.field_value(FIELD)[0] == expected


@pytest.mark.parametrize("content", [
    "Thinking: Zurich", '["Zurich"]', '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":" "}',
    '{"profile": "upi_id"}', '{"secret": "card.icici.cvv"}', '{"ask": "OTP?"}', '{"ask": "OTP?", "secret": "yes"}',
    '{"ask": "OTP?", "secret": true, "save_as": "card.pin"}',
])
def test_invalid_field_values_type_nothing(monkeypatch, content):
    monkeypatch.setattr(model, "post_json", reply(content))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_value(FIELD)


def test_every_model_request_is_redacted_for_the_current_task(monkeypatch):
    from assistant.profile import SECRETS, Secrets

    sent = []

    class Response:
        status_code, is_error = 200, False

        def json(self):
            return {"choices": [{"message": {"content": '{"text": "ok"}'}}]}

    monkeypatch.setattr(model.CLIENT, "post", lambda url, json, headers: sent.append(json) or Response())
    token = SECRETS.set(Secrets([("+91 98765 43210", "[profile:phone]"), ("9876543210", "[profile:phone]")]))
    try:
        model.field_value({"goal": "Call 98765 43210 about +91 98765 43210"})
    finally:
        SECRETS.reset(token)
    model.field_value({"goal": "no task: 98765 43210"})
    assert "98765" not in json.dumps(sent[0]) and "[profile:phone]" in json.dumps(sent[0])
    assert "98765 43210" in json.dumps(sent[1])

def test_text_model_payloads_are_redacted_before_they_are_encoded(monkeypatch):
    from assistant.profile import SECRETS, Secrets

    post = reply('{"text": "ok"}')
    monkeypatch.setattr(model, "post_json", post)  # below the text model, above post_json's own redaction
    token = SECRETS.set(Secrets([("José Müller", "[profile:name]"), ('ab"cd\\ef', "[secret:x]")]))
    try:
        model.field_value({"goal": 'José Müller typed ab"cd\\ef'})
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
