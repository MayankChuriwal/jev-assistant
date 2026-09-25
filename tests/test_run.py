"""Terminal questions and answers for `jev run`."""

from unittest.mock import Mock

from assistant import server


def asker(*answers, secret=()):
    lines, hidden, out = list(answers), list(secret), []
    reply = lambda message: server.terminal_asker(message, read=lambda _p: lines.pop(0),  # noqa: E731
                                                  secret_read=lambda _p: hidden.pop(0), write=out.append)
    return reply, out


def test_choices_are_numbered_and_can_be_remembered():
    reply, out = asker("2", "y")
    message = {"kind": "choice", "text": "Deliver where?", "remember": True,
               "options": [{"id": "o1", "label": "Home", "description": "12 MG Road"}, {"id": "o2", "label": "Office"}]}
    assert reply(message) == {"option": "o2", "remember": True}
    assert "  1. Home — 12 MG Road" in out and "  2. Office" in out


def test_secrets_are_read_without_echo_and_files_as_paths():
    reply, _ = asker(secret=["482913"])
    assert reply({"kind": "secret", "text": "OTP?"}) == {"text": "482913"}
    reply, _ = asker("~/statement.pdf")
    assert reply({"kind": "file", "text": "Send the statement"})["file"].endswith("/statement.pdf")


def test_cancel_and_free_text():
    reply, _ = asker("cancel")
    assert reply({"kind": "text", "text": "Where to?"}) == {"cancel": True}
    reply, _ = asker("Airport, Terminal 2")
    assert reply({"kind": "choice", "text": "Where to?", "options": [{"id": "o1", "label": "Home"}]}) == {
        "text": "Airport, Terminal 2"}


def test_images_are_saved_for_the_user_to_open(tmp_path, monkeypatch):
    monkeypatch.setattr(server.tempfile, "gettempdir", lambda: str(tmp_path))
    reply, out = asker("XK7P")
    reply({"kind": "image", "text": "Type the captcha", "image": "iVBORw0KGgo="})
    assert any(line.startswith("(image: ") for line in out) and list(tmp_path.glob("jev-*.png"))


def fake_agent(monkeypatch, command, **state):
    agent = Mock(state={"status": "ready", "commit": None, "history": [], "elapsed_ms": 0, **state})
    agent.command = Mock(side_effect=command)
    made = []
    monkeypatch.setattr("assistant.driver.agent_class",
                        lambda: lambda url, goal, **kw: made.append((url, goal, kw)) or agent)
    return agent, made


def test_run_drives_the_agent_until_it_stops(monkeypatch):
    def tick(name):
        agent.state.update(status="done", history=[{"operation": "CLICK", "action": "Continue"}], elapsed_ms=900)
        return agent.state

    agent, made = fake_agent(monkeypatch, tick)
    assert server.main(["run", "Order Diet Coke", "--url", "https://www.swiggy.com"]) == 0
    assert made[0][:2] == ("https://www.swiggy.com", "Order Diet Coke")
    assert made[0][2]["asker"] is server.terminal_asker
    agent.close.assert_called_once()


def test_run_survives_model_errors_and_stops_after_three(monkeypatch, capsys):
    agent, _ = fake_agent(monkeypatch, [ValueError("bad JSON")] * 3)
    assert server.main(["run", "x", "--url", "https://a.test"]) == 1
    out = capsys.readouterr().out
    assert out.count("Problem: bad JSON") == 3 and "Stopping after 3 problems" in out
    agent.close.assert_called_once()


def test_run_warns_when_a_commit_may_be_in_progress(monkeypatch, capsys):
    agent, _ = fake_agent(monkeypatch, RuntimeError("HTTP 503"), commit={"label": "Pay ₹79", "checks": 1})
    assert server.main(["run", "x", "--url", "https://a.test"]) == 1
    assert "Pay ₹79 may already have gone through" in capsys.readouterr().out
