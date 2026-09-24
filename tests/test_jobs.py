"""Text-model job contracts. post_json is replaced in every test."""

import json
from unittest.mock import Mock

import pytest

from assistant import jobs, model

PAGE = {"url": "https://shop.test/pay", "title": "Checkout", "text": "Diet Coke 300 ml\nTotal ₹79\nPay ₹79"}
ELEMENTS = [{"index": "1", "label": "Home", "role": "radio"}, {"index": "2", "label": "Office", "role": "radio"}]


@pytest.fixture(autouse=True)
def openai_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


def reply(monkeypatch, content):
    post = Mock(return_value={"choices": [{"message": {"content": json.dumps(content)}}]})
    monkeypatch.setattr(model, "post_json", post)
    return post


def test_start_needs_an_http_url_and_a_title(monkeypatch):
    reply(monkeypatch, {"start_url": "https://www.swiggy.com/instamart", "title": "Swiggy · Diet Coke"})
    assert jobs.start("Order Diet Coke")[:2] == ("https://www.swiggy.com/instamart", "Swiggy · Diet Coke")
    for bad in ({"start_url": "javascript:alert(1)", "title": "x"}, {"start_url": "https://a.test"}):
        reply(monkeypatch, bad)
        with pytest.raises(ValueError, match="invalid start"):
            jobs.start("Order Diet Coke")


def test_choice_questions_bind_options_to_observed_elements(monkeypatch):
    reply(monkeypatch, {"kind": "choice", "text": "Deliver where?", "options": [
        {"label": "Home – Indiranagar Bengaluru", "description": "12 MG Road", "element": "1"},
        {"label": "Office", "element": "2"}]})
    question, _ = jobs.compose("Goal: order", PAGE, ELEMENTS)
    assert question["options"][0] == {"label": "Home – Indiranagar B", "description": "12 MG Road", "element": "1"}
    for bad in ({"kind": "choice", "text": "Where?", "options": [{"label": "Home", "element": "9"}, {"label": "B"}]},
                {"kind": "choice", "text": "Where?", "options": [{"label": "Home"}]},
                {"kind": "shout", "text": "Where?"}):
        reply(monkeypatch, bad)
        with pytest.raises(ValueError, match="invalid question"):
            jobs.compose("Goal: order", PAGE, ELEMENTS)


def test_confirmation_amounts_must_be_on_the_page(monkeypatch):
    reply(monkeypatch, {"action_label": "Pay ₹79", "amount": "₹79", "lines": ["Diet Coke 300 ml", "Home"]})
    assert jobs.confirm_card("c", PAGE, "Pay ₹79")[0]["amount"] == "₹79"
    reply(monkeypatch, {"action_label": "Submit complaint", "amount": "", "lines": []})
    assert jobs.confirm_card("c", PAGE, "Submit complaint")[0]["amount"] == ""
    reply(monkeypatch, {"action_label": "Pay ₹69", "amount": "₹69", "lines": []})
    with pytest.raises(ValueError, match="invalid confirmation card"):
        jobs.confirm_card("c", PAGE, "Pay ₹79")


def test_outcomes_need_the_pages_own_words(monkeypatch):
    done = {**PAGE, "text": "Order placed #A1B2"}
    reply(monkeypatch, {"outcome": "success", "evidence": "Order placed #A1B2"})
    assert jobs.outcome("c", done, "Pay ₹79")[:2] == ("success", "Order placed #A1B2")
    reply(monkeypatch, {"outcome": "success", "evidence": "Payment successful"})
    assert jobs.outcome("c", done, "Pay ₹79")[0] == "unclear"
    reply(monkeypatch, {"outcome": "pending", "evidence": ""})
    assert jobs.outcome("c", PAGE, "Pay ₹79")[0] == "pending"
    reply(monkeypatch, {"outcome": "maybe", "evidence": ""})
    with pytest.raises(ValueError):
        jobs.outcome("c", PAGE, "Pay ₹79")


def test_results_notes_help_hints_and_preferences(monkeypatch):
    reply(monkeypatch, {"text": "Ordered.", "evidence": "Pay ₹79"})
    assert jobs.result("c", PAGE)[:2] == ("Ordered.", "Pay ₹79")
    reply(monkeypatch, {"text": "Ordered.", "evidence": "Order #9"})
    assert jobs.result("c", PAGE)[1] == ""
    reply(monkeypatch, {"text": "Ordered.", "evidence": "Pay ₹79", "achieved": True})
    assert jobs.result("c", PAGE)[2] is True
    reply(monkeypatch, {"text": "Signed in.", "evidence": PAGE["text"] * 30})  # a page dump is not a quote
    assert jobs.result("c", PAGE)[1:3] == ("", False)
    reply(monkeypatch, {"facts": ["Total ₹79", "", "a", "b", "c", "d", "e"]})
    assert jobs.note("c", PAGE)[0] == ["Total ₹79", "a", "b", "c", "d"]
    options = [{"label": "Home", "description": "", "element": "2"}, {"label": "Office", "description": "",
                                                                        "element": ""}]
    reply(monkeypatch, {"rung": "choose", "text": "Which address?", "options": options})
    assert jobs.unblock("c", PAGE, ELEMENTS)[0] == {"rung": "choose", "text": "Which address?", "options": options}
    reply(monkeypatch, {"rung": "choose", "text": "Which address?", "options": [{"label": "Home", "element": "99"}]})
    with pytest.raises(ValueError):
        jobs.unblock("c", PAGE, ELEMENTS)  # an option tied to an element the page doesn't have
    assert jobs.unblock("c", PAGE, ELEMENTS, must_stop=True)[0]["rung"] == "stop"
    post = reply(monkeypatch, {"hint": "Open Accounts → Disputes."})
    assert jobs.hint("c", PAGE, image="iVBORw0KGgo=")[0] == "Open Accounts → Disputes."
    parts = post.call_args.args[2]["messages"][1]["content"]
    assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}
    reply(monkeypatch, {"preference": "Default Instamart address: Home"})
    assert jobs.remember("Deliver where?", "Home")[0] == "Default Instamart address: Home"


def test_paying_commits_need_an_amount_and_a_real_element(monkeypatch):
    reply(monkeypatch, {"action_label": "Pay", "amount": "", "lines": [], "element": ""})
    with pytest.raises(ValueError, match="invalid confirmation card"):
        jobs.confirm_card("c", PAGE, "Pay ₹79", ELEMENTS)
    reply(monkeypatch, {"action_label": "Pay ₹79", "amount": "₹79", "lines": [], "element": "9"})
    with pytest.raises(ValueError, match="invalid confirmation card"):
        jobs.confirm_card("c", PAGE, "Pay ₹79", ELEMENTS)
    reply(monkeypatch, {"action_label": "Pay ₹79", "amount": "₹79", "lines": [], "element": "2"})
    assert jobs.confirm_card("c", PAGE, "Pay ₹79", ELEMENTS)[0]["element"] == "2"


def test_the_start_job_is_told_to_use_a_grocery_section_for_groceries():
    from assistant import questions

    assert "grocery section" in questions.START and "Instamart" in questions.START


def test_choice_questions_say_whether_they_are_lasting_preferences(monkeypatch):
    reply(monkeypatch, {"kind": "choice", "text": "Deliver where?", "preference": True, "options": [
        {"label": "Home", "element": "1"}, {"label": "Office", "element": "2"}]})
    assert jobs.compose("Goal: order", PAGE, ELEMENTS)[0]["preference"] is True
    reply(monkeypatch, {"kind": "choice", "text": "What next?", "options": [
        {"label": "View full menu", "element": "1"}, {"label": "Search items", "element": "2"}]})
    assert jobs.compose("Goal: order", PAGE, ELEMENTS)[0]["preference"] is False


def test_visual_annotations_use_coordinator_schema(monkeypatch):
    from reliability_fakes import observed
    s, page, _ = observed()
    monkeypatch.setattr(jobs, "text_json", lambda *args, **kwargs: (
        {"annotations": [{"cell": "A1", "label": "Map", "text": ""}]}, {}))
    assert jobs.visual_annotations(s, page, "image", {"A1": {}}) == [
        {"cell": "A1", "label": "Map", "text": ""}]


def test_confirmation_preserves_all_observed_charge_lines(monkeypatch):
    from assistant import jobs

    lines = [f"Charge {n}: ₹1" for n in range(30)]
    monkeypatch.setattr(jobs, "text_json", lambda *args, **kwargs: (
        {"action_label": "Pay ₹30", "amount": "₹30", "lines": lines, "element": ""}, {}))
    card, _ = jobs.confirm_card("review", {"url": "https://shop.test", "title": "Review",
                                           "text": "\n".join(lines) + " Total ₹30"}, "Pay ₹30")
    assert card["lines"] == lines


def test_completion_binding_job_rejects_boolean_proof(monkeypatch):
    from assistant import jobs
    from assistant.state import TaskState
    monkeypatch.setattr(jobs, 'text_json', lambda *a, **k: ({'achieved': True}, {}))
    with pytest.raises(ValueError, match='completion'):
        jobs.completion_bindings(TaskState('t', 'Fetch my statement'))


def test_completion_binding_job_rolls_back_partial_bindings(monkeypatch):
    from assistant import jobs
    from assistant.state import TaskState, cite, observe_state, put_fact
    s = TaskState('t', 'Compare blue parcel prices')
    oid = observe_state(s, {'url': 'https://example.test', 'text': 'blue parcel costs 10'})
    eid = cite(s, oid, 'blue parcel costs 10')
    put_fact(s, 'item', 'blue parcel', source='user')
    s.completion = [dict(id='r', description='Comparison', kind='page', material_keys=['item'],
                        evidence_ids=[], execution_id=None, artifact_id=None)]
    binding = dict(requirement_id='r', evidence_ids=[eid], execution_id=None, artifact_id=None)
    monkeypatch.setattr(jobs, 'text_json', lambda *a, **k: ({'bindings': [binding, binding]}, {}))
    with pytest.raises(ValueError):
        jobs.completion_bindings(s)
    assert s.completion[0]['evidence_ids'] == []
