"""CLI contracts. The browser check runs only with -m browser."""

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
    monkeypatch.setattr(server, "serve", lambda: calls.append("serve") or 0)
    assert server.main([]) == 0 and calls == ["serve"]


@pytest.mark.browser
def test_probe_reports_real_status_codes(browser_at, site):
    assert server.verdict(site["a"] + "/forbidden") == "http-403"
    assert server.verdict(site["a"] + "/ops.html") == "ok"
