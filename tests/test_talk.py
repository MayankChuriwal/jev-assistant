"""The agent talking to its user: typing resolution, questions, choices, notes, results. Fakes only."""

import json
import threading
from copy import deepcopy
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
    card = {"action_label": "Pay ₹79", "amount": "₹79", "lines": ["Diet Coke 300 ml", "Home"], "element": ""}
    monkeypatch.setattr(loop.jobs, "confirm_card", lambda context, page, label, elements=(): (card, META))
    a = agent_with(tmp_path, replies=[{"option": "yes"}])
    assert a.confirm(a.state["page"], "Pay ₹79")
    assert a.state["approval"] == {"action_label": "Pay ₹79", "amount": "₹79", "host": "shop.test", "step": 0,
                                   "target": None}
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


def test_pages_are_redacted_before_anyone_sees_them(tmp_path):
    a = agent_with(tmp_path)
    a.browser.observe.return_value = fake_page(text="Thanks Mayank Churiwal, call 98765 43210")
    page = a.observe()
    assert page["text"] == "Thanks [profile:name], call [profile:phone]"
    assert "98765" not in json.dumps(a.snapshot(), ensure_ascii=False, default=str)


def test_screenshots_blur_fields_that_hold_secrets(tmp_path):
    a = agent_with(tmp_path)
    a.screenshots = True
    a.secrets.nodes.add((None, 8))
    page = a.observe()
    assert page["screenshot"] == "iVBORw0KGgo=" and a.browser.screenshot.call_args.kwargs == {"masked": {(None, 8)}}
    a.browser.observe.assert_called_with(screenshot=False)


def test_evidence_is_checked_against_what_the_model_saw(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    said = {"outcome": "success", "evidence": "Thanks [profile:name], order #A1 placed"}
    answer = {"choices": [{"message": {"content": json.dumps(said)}}]}
    monkeypatch.setattr(model, "post_json", Mock(return_value=answer))
    a = agent_with(tmp_path)
    a.browser.observe.return_value = fake_page(text="Thanks Mayank Churiwal, order #A1 placed")
    a.observe()
    a.state["commit"] = {"label": "Pay ₹79", "checks": 0}
    a.check_commit()
    assert a.state["memory"][-1] == "Done: Pay ₹79 (Thanks [profile:name], order #A1 placed)"


@pytest.mark.parametrize("target, form, answer", [
    ("e1", ("ask", "Card number?", False, "card.number"), "4111 1111 1111 1111"),
    ("e3", ("ask", "CVV?", False, None), "123"),
    ("e2", ("ask", "Code?", False, None), "482913"),
])
def test_card_details_passwords_and_codes_are_always_secret(tmp_path, monkeypatch, target, form, answer):
    a = agent_with(tmp_path, replies=[{"text": answer}])
    field(monkeypatch, form)
    act(a, decision(target, "TYPE_TEXT"))
    assert a.asked[0]["kind"] == "secret" and a.state["history"][-1]["text"].startswith("[secret:")


def test_a_pick_is_never_guessed_between_identical_labels(tmp_path, monkeypatch):
    page = fake_page()
    for x in page["actions"]:
        if x["id"] in ("e4", "e5"):
            x["label"] = "Deliver here"
    moved = deepcopy(page)
    for x in moved["actions"]:
        if x["id"] in ("e4", "e5"):
            x["node"] += 100
    a = agent_with(tmp_path, page=page, replies=[{"option": "o1"}])
    a.browser.observe.return_value = moved
    choice(monkeypatch, ("Home", "4"), ("Office", "5"))
    act(a, decision("ASK_USER", "ASK_USER"))
    a.browser.act.assert_not_called()


def test_a_given_title_is_kept(tmp_path):
    a = loop.Agent("https://www.swiggy.com/instamart", "Order Diet Coke", title="Swiggy · Diet Coke",
                   profile=Profile(tmp_path / "p.json"), vault=FakeVault(),
                   browser=Mock(observe=Mock(return_value=fake_page())))
    assert a.state["title"] == "Swiggy · Diet Coke"


def test_an_interrupted_step_is_chosen_again_instead_of_run(tmp_path):
    a = agent_with(tmp_path)
    a.interrupted = lambda: True  # the user cancelled or changed the task while this step was being decided
    with pytest.raises(StalePage):
        act(a, decision("e4", "CLICK"))
    a.browser.act.assert_not_called()


def test_settle_keeps_downloads_that_finish_late(tmp_path):
    a = agent_with(tmp_path)
    a.browser.pending_downloads, a.browser.downloads = {"g1": "invoice.pdf"}, []

    def finish():
        a.browser.downloads.append("/downloads/invoice.pdf")
        a.browser.pending_downloads.clear()

    threading.Timer(0.2, finish).start()
    a.settle(timeout=3)
    assert a.state["files"][-1] == "/downloads/invoice.pdf" and a.browser.downloads == []


def test_placeholders_the_model_writes_become_the_values_they_name(tmp_path):
    a = agent_with(tmp_path)
    phone = next(x for x in a.state["page"]["actions"] if x["id"] == "e1")
    where = ("shop.test", "Phone number")
    assert a.resolve(("text", "[profile:phone]"), phone, where) == (
        "+91 98765 43210", "[profile:phone]", "profile", "phone")
    assert a.resolve(("text", "Call [profile:name] at gate 4"), phone, where) == (
        "Call Mayank Churiwal at gate 4", "Call [profile:name] at gate 4", "model", None)
    for text in ("[secret:card.hdfc.cvv]", "[secret]", "[profile:nickname]"):
        with pytest.raises(ValueError, match="placeholder"):
            a.resolve(("text", text), phone, where)


def test_file_names_never_carry_the_users_name_to_a_model(tmp_path):
    cv = tmp_path / "Mayank_Churiwal_CV.pdf"
    cv.write_bytes(b"%PDF")
    a = agent_with(tmp_path, replies=[{"file": str(cv)}])
    act(a, decision("e7", "UPLOAD"))
    assert a.state["memory"][-1] == "User sent the file [profile:name] CV.pdf"
    assert a.state["history"][-1]["files"] == ["[profile:name] CV.pdf"]


def test_typing_can_use_values_the_goal_does_not_hold():
    from assistant import questions

    assert "asking the user" in questions.OPERATIONS["TYPE_TEXT"]
    assert "even by asking the user" in questions.OPERATIONS["BLOCKED"]
