"""CLI contracts. The browser check runs only with -m browser."""

import json
import os

import pytest

from assistant import server


def test_chrome_runs_a_dedicated_hidden_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    command = server.chrome_command()
    assert command[:5] == ["open", "-gj", "-na", "Google Chrome", "--args"]
    assert f"--user-data-dir={tmp_path / 'chrome'}" in command
    assert "--remote-debugging-port=9333" in command
    assert "--disable-backgrounding-occluded-windows" in command


@pytest.mark.parametrize("status, text, expected", [
    (200, "Diet Coke", "ok"),
    (403, "Access denied", "http-403"),
    (429, "", "http-429"),
    (200, "Please verify you are a human", "captcha"),
    (200, "Our systems have detected unusual traffic", "captcha"),
    (500, "", "http-500"),
    (0, "", "ok"),
])
def test_probe_classifies_blocking(status, text, expected):
    assert server.classify(status, text) == expected


def test_env_file_never_overrides_the_environment(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nBU_NAME=jev\nJEV_TEST_ONLY=from-file\n\n")
    monkeypatch.setenv("BU_NAME", "already-set")
    monkeypatch.setenv("JEV_TEST_ONLY", "placeholder")
    monkeypatch.delenv("JEV_TEST_ONLY")  # recorded as absent, so the value read below is removed after the test
    server.load_environment(env)
    assert os.environ["BU_NAME"] == "already-set" and os.environ["JEV_TEST_ONLY"] == "from-file"


def test_no_command_serves_the_chat(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(server, "serve", lambda bench=None: calls.append(bench) or 0)
    assert server.main([]) == 0 and calls == [None]
    assert server.main(["--bench", "--only", "uber", "--repeat", "1"]) == 0
    assert calls[-1] == ("bench/tasks.toml", "uber", 1)


@pytest.mark.browser
def test_probe_reports_real_status_codes(browser_at, site):
    assert server.verdict(site["a"] + "/forbidden") == "http-403"
    assert server.verdict(site["a"] + "/ops.html") == "ok"


def test_traces_lists_the_latest_runs(tmp_path, capsys):
    folder = tmp_path / "traces"
    folder.mkdir()
    (folder / "20260923-101500-diet-coke.json").write_text(json.dumps({
        "status": "done", "title": "Instamart · Diet Coke", "questions": 3, "steps": [{}] * 12,
        "elapsed_ms": 61200, "result": {"text": "Ordered.", "evidence": "Order placed\n#A1B2"}}))
    (folder / "20260923-102000-uber.json").write_text("{broken")
    assert server.traces(folder=folder) == 0
    out = capsys.readouterr().out
    assert "done" in out and "Instamart · Diet Coke" in out and " 3 questions" in out and "12 steps" in out
    assert "Order placed #A1B2" in out and "20260923-102000-uber.json  unreadable" in out
    assert server.traces(folder=tmp_path / "none") == 0 and "No traces yet" in capsys.readouterr().out


def test_the_profile_command_keeps_private_details_private(tmp_path, monkeypatch, capsys):
    from assistant.profile import Profile

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("assistant.profile.HOME", tmp_path)
    assert server.main(["profile", "--name", "Mayank Churiwal", "--phone", "+91 98765 43210"]) == 0
    out = capsys.readouterr().out
    assert Profile().data["name"] == "Mayank Churiwal" and "name: set" in out and "email: not set" in out
    assert "98765" not in out and "Mayank" not in out


def test_a_second_jev_explains_the_port_is_taken(monkeypatch, capsys):
    import socket

    monkeypatch.setattr(server, "chrome", lambda: 0)
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    try:
        assert server.serve(port=taken.getsockname()[1]) == 1
    finally:
        taken.close()
    assert "Another jev is already serving the chat" in capsys.readouterr().err


def test_forget_needs_the_agent_chrome(monkeypatch, capsys):
    monkeypatch.setattr(server, "devtools_ready", lambda url=None: False)
    assert server.main(["forget", "swiggy.com"]) == 1 and "not running" in capsys.readouterr().err
