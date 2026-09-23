"""Every task leaves a redacted trace: what the smoke set judges a run by. Fakes only."""

import json

from fakes import act, agent_with, decision


def test_traces_are_redacted_records_of_the_run(tmp_path):
    a = agent_with(tmp_path)
    a.trace_dir = tmp_path / "traces"
    a.secrets.add("4111 1111 1111 1111", "[secret:card.hdfc.number]")
    a.state["memory"] += ["Noted: call +91 98765 43210 at the gate", "Card 4111111111111111 was typed"]
    a.state["messages"] += [
        {"from": "agent", "kind": "secret", "text": "Enter the OTP."}, {"from": "user", "text": "••••"},
        {"from": "agent", "kind": "info", "text": "Checking out."},
        {"from": "agent", "kind": "choice", "text": "Deliver to which address?", "options": []},
        {"from": "user", "option": "o1"},
        {"from": "agent", "kind": "result", "text": "Ordered.", "evidence": "Order placed #A1B2", "image": "iVBORw0"},
    ]
    a.state["decisions"].append({**decision("e4", "CLICK"), "operation_probabilities": {"CLICK": 0.9, "DONE": 0.1},
                                 "request": {"state": {"page": {"text": "Checkout"}}, "questions": "every rule"}})
    a.state["status"] = "done"
    path = a.save_trace()
    trace, text = json.loads(path.read_text()), path.read_text()
    assert path.name.endswith("-diet-coke.json") and oct(path.stat().st_mode & 0o777) == "0o600"
    assert trace["status"] == "done" and trace["questions"] == 2
    assert trace["result"] == {"text": "Ordered.", "evidence": "Order placed #A1B2"}
    assert trace["decisions"] == [{"operation": "CLICK", "choice": "e4", "confidence": 1.0, "latency_ms": 1,
                                   "operation_probabilities": {"CLICK": 0.9, "DONE": 0.1}, "elapsed_ms": None,
                                   "seen": {"page": {"text": "Checkout"}}}]  # what Jev saw, to replay a live step
    for leak in ("98765", "4111", "iVBORw0", "every rule"):
        assert leak not in text, leak
    assert "[profile:phone]" in text and "[secret:card.hdfc.number]" in text


def test_closing_a_task_saves_its_trace_and_then_its_tabs(tmp_path):
    a = agent_with(tmp_path)
    a.trace_dir = tmp_path / "traces"
    a.close()
    assert len(list(a.trace_dir.glob("*.json"))) == 1 and a.browser.close.call_count == 1
    a.trace_dir = tmp_path / "blocked"
    a.trace_dir.write_text("a file where the folder should be")
    a.close()  # a trace that can't be written never keeps the tabs open
    assert a.browser.close.call_count == 2


def test_errors_are_kept_in_the_trace(tmp_path, monkeypatch):
    import pytest

    from assistant import agent as loop

    def broken(*_args, **_kwargs):
        raise ValueError("The text model returned an invalid confirmation; nothing executed.")

    monkeypatch.setattr(loop.jobs, "confirm_card", broken)
    a = agent_with(tmp_path)
    a.trace_dir = tmp_path / "traces"
    with pytest.raises(ValueError):
        act(a, decision("e6", "CLICK"))  # "Pay ₹79": the summary card cannot be written
    trace = json.loads(a.save_trace().read_text())
    error = "The text model returned an invalid confirmation; nothing executed."
    assert trace["errors"] == [{"step": 0, "error": error}]
