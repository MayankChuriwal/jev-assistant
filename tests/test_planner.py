"""Mini's turns and jobs with the text model scripted; dates and message rules are pure."""

import datetime

import pytest

from assistant import model, planner

THURSDAY = datetime.date(2026, 10, 1)
PAGE = {"url": "https://x.test/", "title": "X", "text": "Seats"}


def scripted(monkeypatch, *outputs):
    calls = []

    def text_json(system, payload, **kw):
        calls.append({"system": system, "payload": payload, **kw})
        return outputs[len(calls) - 1], {"model": "fake", "latency_ms": 1, "usage": {}}

    monkeypatch.setattr(model, "text_json", text_json)
    return calls


def test_a_turn_uses_the_strict_schema_and_the_system_prompt(monkeypatch):
    out = {"note": "", "subgoals": [], "action": {"op": "CONTINUE", "pick": "", "text": ""}, "message": "",
           "options": []}
    calls = scripted(monkeypatch, out)
    assert planner.turn({"goal": "x"})[0] == out
    assert calls[0]["schema"] is planner.SCHEMA and calls[0]["schema_name"] == "planner_turn"
    assert calls[0]["system"] == planner.SYSTEM
    assert planner.SCHEMA["properties"]["action"]["properties"]["op"]["enum"] == planner.OPS


def test_the_schema_is_strict_everywhere():
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])

    walk(planner.SCHEMA)


def test_split_keeps_each_errand_and_falls_back_to_the_message(monkeypatch):
    scripted(monkeypatch, {"goals": ["Buy milk on Zepto", " ", "Book a cab home"]}, {"goals": []})
    assert planner.split("milk from zepto and a cab home") == ["Buy milk on Zepto", "Book a cab home"]
    assert planner.split("just milk") == ["just milk"]


@pytest.mark.parametrize("url", ["ftp://x.test/", "javascript:alert(1)", "https://", None])
def test_start_accepts_only_http_addresses(monkeypatch, url):
    scripted(monkeypatch, {"start_url": url, "title": "X", "service_named": True})
    with pytest.raises(ValueError):
        planner.start("Order milk")


def test_start_returns_the_title_and_whether_a_site_was_named(monkeypatch):
    scripted(monkeypatch, {"start_url": "https://www.bigbasket.com/", "title": "BigBasket · Rice",
                           "service_named": True})
    assert planner.start("rice on bigbasket") == ("https://www.bigbasket.com/", "BigBasket · Rice", True)


def test_the_approval_summary_is_one_line_per_field(monkeypatch):
    calls = scripted(monkeypatch, {"lines": ["Diet Coke × 1", " ", "Total ₹79"]})
    assert planner.summary({"fields": ["what", "total"]}) == ["Diet Coke × 1", "Total ₹79"]
    assert calls[0]["schema_name"] == "approval_summary"


def test_status_replies_and_preferences_are_short_text(monkeypatch):
    scripted(monkeypatch, {"text": "Coke: at checkout."}, {"preference": "Default Instamart address: Home"})
    assert planner.chat("where are we?", [])[0] == "Coke: at checkout."
    assert planner.remember("Which address?", "Home")[0] == "Default Instamart address: Home"


def test_look_answers_are_cells_or_nothing(monkeypatch):
    scripted(monkeypatch, {"cell": "C4", "label": "Seat 12A", "text": "12A", "commits": False}, {"cell": None},
             {"cell": "Z9", "label": "x"})
    found, _ = planner.look("goal", PAGE, "seat 12A", "img", {"C4": {}})
    assert found == {"cell": "C4", "label": "Seat 12A", "text": "12A", "commits": False}
    assert planner.look("goal", PAGE, "seat 12A", "img", {"C4": {}})[0] is None
    with pytest.raises(ValueError):
        planner.look("goal", PAGE, "seat 12A", "img", {"C4": {}})


def test_relative_dates_are_resolved_by_code():
    got = planner.dates("cheapest flight next Friday, back on Sunday, or tomorrow", THURSDAY)
    assert got["next friday"] == "Friday 02 October 2026 (2026-10-02)"
    assert got["sunday"] == "Sunday 04 October 2026 (2026-10-04)"
    assert got["tomorrow"] == "Friday 02 October 2026 (2026-10-02)"
    assert planner.dates("day after tomorrow", THURSDAY) == {
        "day after tomorrow": "Saturday 03 October 2026 (2026-10-03)"}
    assert planner.dates("a comedy show this weekend", THURSDAY)["this weekend"].startswith("Saturday 03 October")


@pytest.mark.parametrize("op, message, options, rule", [
    ("ASK", "Please click the Continue button", [], "can't see the page"),
    ("TELL", "I'll tell the user the price", [], "speak to them"),
    ("ASK", "What is your UPI PIN?", [], "UPI app"),
    ("ASK", "Shall I place the order?", ["Yes", "No"], "Don't ask the user to confirm"),
])
def test_messages_that_break_a_rule_are_refused(op, message, options, rule):
    assert rule in planner.lint(op, message, options, [])


def test_a_question_already_answered_is_not_asked_again():
    chat = [{"from": "agent", "text": "Which size do you want?"}, {"from": "user", "text": "“300 ml”"}]
    assert "already" in planner.lint("ASK", "Which size do you want?", [], chat)
    assert planner.lint("ASK", "Where should I deliver it?", [], chat) is None
    assert planner.lint("TELL", "Found it: ₹40.", [], chat) is None
