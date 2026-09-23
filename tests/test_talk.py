"""The agent talking to its user: typing resolution, questions, choices, notes, results. Fakes only."""

import json
from unittest.mock import Mock

import pytest
from fakes import FakeVault, act, agent_with, decision, fake_page

from assistant import agent as loop
from assistant import model
from assistant.browser import StalePage
from assistant.profile import Profile

META = {"model": "fake", "latency_ms": 1}


def field(monkeypatch, *forms):
    queue, calls = list(forms), []

    def fake(context):
        calls.append(context)
        return queue.pop(0), META

    monkeypatch.setattr(loop, "field_value", fake)
    return calls


def typed(a):
    return a.browser.act.call_args.kwargs["text"]


def choice(monkeypatch, *options, kind="choice"):
    question = {"kind": kind, "text": "Deliver to which address?",
                "options": [{"label": label, "description": "", "element": element} for label, element in options]}
    monkeypatch.setattr(loop.jobs, "compose", lambda context, page, elements: (question, META))


def test_profile_values_are_typed_but_logged_as_placeholders(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    field(monkeypatch, ("profile", "phone"))
    act(a, decision("e1", "TYPE_TEXT"))
    assert typed(a) == "+91 98765 43210"
    assert a.state["history"][-1]["text"] == a.state["text_calls"][-1]["value"] == "[profile:phone]"


def test_vault_secrets_are_typed_masked_and_registered(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    calls = field(monkeypatch, ("secret", "card.hdfc.cvv"))
    act(a, decision("e3", "TYPE_TEXT"))
    assert typed(a) == "123" and a.state["history"][-1]["text"] == "[secret:card.hdfc.cvv]"
    assert "card.hdfc.cvv" in calls[0]["allowed"] and (None, 8) in a.secrets.nodes
    after = fake_page()
    next(x for x in after["actions"] if x["id"] == "e3")["value"] = "123"
    a.browser.observe.return_value = after
    assert next(x for x in a.observe()["actions"] if x["id"] == "e3")["value"] == "[secret]"
    assert a.secrets.text("cvv 123") == "cvv [secret:card.hdfc.cvv]"


def test_codes_are_asked_as_secrets_and_checked_before_typing(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"text": "4829"}, {"text": "482 913"}])
    field(monkeypatch, ("ask", "Enter the code Swiggy sent you.", True, None))
    act(a, decision("e2", "TYPE_TEXT"))
    assert [m["kind"] for m in a.asked] == ["secret", "secret"]
    assert a.asked[0]["expects"]["length"] == 6 and a.asked[1]["text"].startswith("This code needs exactly 6")
    assert typed(a) == "482913" and a.state["history"][-1]["text"] == "[secret:answer1]"
    assert "482913" not in json.dumps(a.state["messages"]) and a.secrets.text("482913") == "[secret:answer1]"
    assert {(None, n) for n in range(2, 8)} <= a.secrets.nodes


def test_an_answer_survives_a_stale_retry_and_is_never_asked_twice(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"text": "482913"}])
    calls = field(monkeypatch, ("ask", "Enter the code.", True, None))
    a.browser.act.side_effect = [StalePage("changed"), {}]
    with pytest.raises(StalePage):
        act(a, decision("e2", "TYPE_TEXT"))
    act(a, decision("e2", "TYPE_TEXT"))
    assert len(calls) == 1 and len(a.asked) == 1 and typed(a) == "482913"


def test_unreadable_vault_value_is_asked_for(tmp_path, monkeypatch):
    a = agent_with(tmp_path, vault=FakeVault(), replies=[{"text": "321"}])
    field(monkeypatch, ("secret", "card.hdfc.cvv"))
    act(a, decision("e3", "TYPE_TEXT"))
    assert a.asked[0]["kind"] == "secret" and "Keychain" in a.asked[0]["text"] and typed(a) == "321"


def test_uploads_ask_for_a_file(tmp_path):
    statement = tmp_path / "statement.pdf"
    statement.write_bytes(b"%PDF")
    a = agent_with(tmp_path, replies=[{"file": str(statement)}])
    act(a, decision("e7", "UPLOAD"))
    assert a.asked[0]["kind"] == "file" and "Statement" in a.asked[0]["text"]
    assert a.browser.act.call_args.kwargs == {"files": [str(statement)]}
    assert a.state["memory"][-1] == "User sent the file statement.pdf"


def test_choices_click_the_element_their_option_is_tied_to(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"option": "o1", "remember": True}])
    choice(monkeypatch, ("Home", "4"), ("Office", "5"))
    monkeypatch.setattr(loop.jobs, "remember", lambda question, answer: ("Default address: Home", META))
    act(a, decision("ASK_USER", "ASK_USER"))
    assert [o["label"] for o in a.asked[0]["options"]] == ["Home", "Office"] and a.asked[0]["remember"]
    assert a.browser.act.call_args.args[:2][0] == "CLICK" and a.browser.act.call_args.args[1]["id"] == "e4"
    assert "Home" in a.state["memory"][0]
    assert a.profile.data["preferences"][-1]["text"] == "Default address: Home"
    assert a.told[-1]["text"] == "Saved for next time: Default address: Home"


def test_option_bound_to_a_non_clickable_element_is_not_clicked(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"option": "o1"}])
    choice(monkeypatch, ("Use my phone", "1"), ("Office", "5"))
    act(a, decision("ASK_USER", "ASK_USER"))
    a.browser.act.assert_not_called()
    assert "Use my phone" in a.state["memory"][0]


def test_secret_answers_become_task_secrets(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"text": "XK7P2"}])
    question = {"kind": "secret", "text": "What is the PIN?", "options": []}
    monkeypatch.setattr(loop.jobs, "compose", lambda context, page, elements: (question, META))
    act(a, decision("ASK_USER", "ASK_USER"))
    assert a.state["answers"] == {"answer1": "XK7P2"} and "XK7P2" not in json.dumps(a.state["memory"])
    assert "answer1" in a.allowed(("shop.test", "PIN")) and "answers" not in a.snapshot()


def test_notes_go_to_task_memory(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    monkeypatch.setattr(loop.jobs, "note", lambda context, page: (["Total ₹79"], META))
    act(a, decision("NOTE", "NOTE"))
    assert a.state["memory"] == ["Noted: Total ₹79"] and a.state["status"] == "ready"


def test_done_reports_with_evidence_or_a_caution(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    monkeypatch.setattr(loop.jobs, "result", lambda context, page: ("Ordered for ₹79.", "Pay ₹79", META))
    act(a, decision("DONE", "DONE"))
    assert a.state["status"] == "done"
    assert a.told[-1] == {"from": "agent", "kind": "result", "text": "Ordered for ₹79.", "evidence": "Pay ₹79"}
    a = agent_with(tmp_path)
    monkeypatch.setattr(loop.jobs, "result", lambda context, page: ("Ordered.", "", META))
    act(a, decision("DONE", "DONE"))
    assert "couldn't find proof" in a.told[-1]["text"]


def test_without_anyone_to_ask_the_task_blocks(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    a.asker = None
    field(monkeypatch, ("ask", "Enter the code.", True, None))
    act(a, decision("e2", "TYPE_TEXT"))
    assert a.state["status"] == "blocked" and "no one is attached" in a.told[-1]["text"]
    a.browser.act.assert_not_called()


def test_cancel_stops_before_anything_else(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"cancel": True}])
    field(monkeypatch, ("ask", "Enter the code.", True, None))
    act(a, decision("e2", "TYPE_TEXT"))
    assert a.state["status"] == "cancelled"
    a.browser.act.assert_not_called()


def test_confirm_records_an_approval_or_asks_what_to_change(tmp_path, monkeypatch):
    card = {"action_label": "Pay ₹79", "amount": "₹79", "lines": ["Diet Coke 300 ml", "Home"]}
    monkeypatch.setattr(loop.jobs, "confirm_card", lambda context, page, label: (card, META))
    a = agent_with(tmp_path, replies=[{"option": "yes"}])
    assert a.confirm(a.state["page"], "Pay ₹79")
    assert a.state["approval"] == {"action_label": "Pay ₹79", "amount": "₹79", "host": "shop.test", "step": 0}
    assert a.asked[0]["kind"] == "confirm" and "Total: ₹79" in a.asked[0]["text"]
    a = agent_with(tmp_path, replies=[{"option": "no"}, {"option": "payment"}])
    assert not a.confirm(a.state["page"], "Pay ₹79")
    assert a.state["approval"] is None and "payment" in a.state["memory"][-1].lower()


def test_the_start_page_is_found_when_no_url_is_given(tmp_path, monkeypatch):
    seen = []
    start = ("https://www.swiggy.com/instamart", "Swiggy · Diet Coke", META)
    monkeypatch.setattr(loop.jobs, "start", lambda goal: start)
    monkeypatch.setattr(loop, "Browser", lambda url, geolocation=None: seen.append(url) or Mock(
        observe=Mock(return_value=fake_page())))
    a = loop.Agent(None, "Order Diet Coke", profile=Profile(tmp_path / "p.json"), vault=FakeVault())
    assert seen == ["https://www.swiggy.com/instamart"] and a.state["title"] == "Swiggy · Diet Coke"


def test_model_requests_during_a_step_are_redacted(tmp_path, monkeypatch):
    sent = []

    class Response:
        status_code, is_error = 200, False

        def json(self):
            return {"choices": [{"message": {"content": '{"facts": ["ok"]}'}}]}

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(model.CLIENT, "post", lambda url, json, headers: sent.append(json) or Response())
    a = agent_with(tmp_path, page=fake_page(text="Call Mayank on 98765 43210"))
    act(a, decision("NOTE", "NOTE"))
    assert sent and "98765" not in json.dumps(sent) and "Mayank" not in json.dumps(sent)
