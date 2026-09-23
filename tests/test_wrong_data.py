"""Wrong OTPs, CVVs, and other rejected values: count, warn, stop, never reuse. Fakes only."""

from fakes import FakeVault, act, agent_with, decision, fake_page

from assistant import agent as loop

META = {"model": "fake", "latency_ms": 1}


def field(monkeypatch, *forms):
    queue, calls = list(forms), []

    def fake(context):
        calls.append(context)
        return queue.pop(0), META

    monkeypatch.setattr(loop, "field_value", fake)
    return calls


def typed(a):
    return [call.kwargs["text"] for call in a.browser.act.call_args_list]


ASK = ("ask", "Enter the code Swiggy sent you.", True, None)


def test_a_rejected_code_is_asked_again_with_a_warning_then_the_task_stops(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"text": "111111"}, {"text": "222222"}, {"text": "333333"}])
    calls = field(monkeypatch, ASK, ASK, ASK, ASK)
    for _ in range(4):
        act(a, decision("e2", "TYPE_TEXT"))
    assert typed(a) == ["111111", "222222", "333333"]
    assert calls[0]["previous"] is None
    assert calls[1]["previous"] == {"source": "user", "rejected": True, "attempts": 1, "left": 2}
    assert "One try left" not in a.asked[1]["text"] and "One try left" in a.asked[2]["text"]
    assert a.state["status"] == "blocked" and "rejected 3 values" in a.told[-1]["text"] and len(calls) == 3


def test_rerendered_code_boxes_still_count_attempts(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"text": "111111"}, {"text": "222222"}, {"text": "333333"}])
    field(monkeypatch, ASK, ASK, ASK)
    for round_ in range(4):
        page = fake_page()
        code = next(x for x in page["actions"] if x["id"] == "e2")
        code["node"], code["group"] = 100 * (round_ + 1), [100 * (round_ + 1) + n for n in range(6)]
        a.state["page"] = a.browser.observe.return_value = page
        act(a, decision("e2", "TYPE_TEXT"))
    assert len(typed(a)) == 3 and a.state["status"] == "blocked"


def test_a_rejected_keychain_value_is_never_reused_and_its_replacement_waits_for_success(tmp_path, monkeypatch):
    vault = FakeVault({"card.hdfc.cvv": "123"})
    a = agent_with(tmp_path, vault=vault, replies=[{"text": "456"}])
    replacement = ("ask", "Your saved CVV was rejected. Send it?", True, "card.cvv")
    calls = field(monkeypatch, ("secret", "card.hdfc.cvv"), replacement)
    act(a, decision("e3", "TYPE_TEXT"))
    act(a, decision("e3", "TYPE_TEXT"))
    assert typed(a) == ["123", "456"]
    assert "card.hdfc.cvv" in calls[0]["allowed"] and "card.hdfc.cvv" not in calls[1]["allowed"]
    assert "One try left" in a.asked[0]["text"]  # a CVV field allows two values
    assert a.state["pending_vault"] == {"card.hdfc.cvv": "456"} and vault.writes == []
    act(a, decision("e3", "TYPE_TEXT"))
    assert a.state["status"] == "blocked" and len(typed(a)) == 2
    a.state["status"], a.state["commit"] = "ready", {"label": "Pay ₹79", "checks": 0}
    monkeypatch.setattr(loop.jobs, "outcome", lambda context, page, label: ("success", "Pay ₹79", META))
    a.check_commit()
    assert vault.writes == [("card.hdfc.cvv", "456")]


def test_a_failed_payment_never_writes_a_replacement(tmp_path, monkeypatch):
    vault = FakeVault({"card.hdfc.cvv": "123"})
    a = agent_with(tmp_path, vault=vault)
    a.state["pending_vault"] = {"card.hdfc.cvv": "456"}
    a.state["commit"] = {"label": "Pay ₹79", "checks": 0}
    monkeypatch.setattr(loop.jobs, "outcome", lambda context, page, label: ("failed", "Pay ₹79", META))
    a.check_commit()
    assert vault.writes == [] and a.state["pending_vault"] == {"card.hdfc.cvv": "456"}


def test_values_the_model_wrote_are_not_attempts(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    field(monkeypatch, *[("text", "coke")] * 5)
    for _ in range(5):
        act(a, decision("e1", "TYPE_TEXT"))
    assert len(typed(a)) == 5  # the unchanged fake page trips the no-progress stop, never the attempt limit
    assert not any("rejected" in m["text"] for m in a.told)


def test_malformed_answers_cost_no_attempt(tmp_path, monkeypatch):
    a = agent_with(tmp_path, vault=FakeVault(), replies=[{"text": "12a"}, {"text": "1234"}, {"text": "123"}])
    field(monkeypatch, ("ask", "CVV?", True, "card.cvv"))
    act(a, decision("e3", "TYPE_TEXT"))
    assert typed(a) == ["123"] and len(a.asked) == 3
    assert a.state["typed"][("shop.test", "CVV")]["count"] == 1


def test_a_rejected_code_is_never_offered_again_by_its_answer_name(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"text": "111111"}, {"text": "482913"}])
    calls = field(monkeypatch, ASK, ASK)
    act(a, decision("e2", "TYPE_TEXT"))
    act(a, decision("e2", "TYPE_TEXT"))
    assert "answer1" not in calls[1]["allowed"] and typed(a) == ["111111", "482913"]


def test_only_the_newest_replacement_reaches_the_keychain(tmp_path, monkeypatch):
    expiry = {"id": "e1", "kind": "fill", "label": "Expiry", "role": "textbox", "value": "", "node": 1}
    page = fake_page(actions=[expiry,
                              {"id": "wait", "kind": "wait", "label": "Wait", "node": None}])
    vault = FakeVault({"card.hdfc.expiry": "12/28"})
    a = agent_with(tmp_path, page=page, vault=vault, replies=[{"text": "12/92"}, {"text": "12/29"}])
    again = ("ask", "That expiry was rejected. Send it again?", True, "card.expiry")
    field(monkeypatch, ("secret", "card.hdfc.expiry"), again, again)
    for _ in range(3):
        act(a, decision("e1", "TYPE_TEXT"))
    assert typed(a) == ["12/28", "12/92", "12/29"] and a.state["pending_vault"] == {"card.hdfc.expiry": "12/29"}
    a.state["status"], a.state["commit"] = "ready", {"label": "Pay ₹79", "checks": 0}
    monkeypatch.setattr(loop.jobs, "outcome", lambda context, page, label: ("success", "Pay ₹79", META))
    a.check_commit()
    assert vault.writes == [("card.hdfc.expiry", "12/29")]


WAIT_ONLY = [{"id": "wait", "kind": "wait", "label": "Wait", "node": None}]


def test_a_code_the_site_took_leaves_the_next_code_its_own_attempts(tmp_path):
    a = agent_with(tmp_path)
    page = a.state["page"]
    code = next(x for x in page["actions"] if x["id"] == "e2")
    where = ("shop.test", "Code")
    a.state["unused"][where] = ("482913", "••••", "user", None)
    a.after_typing(code, page)
    assert a.state["typed"][where]["count"] == 1 and a.state["typed"][where]["url"] == page["url"]
    a.browser.observe.return_value = fake_page()  # the same page, the field still there: a rejection still counts
    a.observe()
    assert where in a.state["typed"]
    a.browser.observe.return_value = fake_page(text="Order placed", url="https://shop.test/orders", actions=WAIT_ONLY)
    a.observe()
    assert where not in a.state["typed"]  # the site moved on without the field: it took the code
