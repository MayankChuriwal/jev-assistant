"""CLI contracts. The browser check runs only with -m browser."""

import json
import os
import uuid

import pytest
from fakes import store

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
    assert calls[-1] == ("bench/tasks.toml", "uber", 1, 8, None)


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


def test_a_second_jev_explains_the_port_is_taken(monkeypatch, capsys):
    import socket

    monkeypatch.setattr(server, "chrome", lambda: 0)
    monkeypatch.setattr(server, "open_memory", store)
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


def test_the_agent_chrome_isolates_every_origin():
    """Same-site frames from another origin (payments.site.com inside m.site.com) get their own process, so the
    agent can read them like any other cross-origin frame."""
    assert "--enable-features=StrictOriginIsolation" in server.chrome_command()


def test_a_memory_that_cannot_open_is_explained_and_not_used(monkeypatch, capsys):
    from assistant.memory import Memory, StoreError

    def refuse(*_args, **_kwargs):
        raise StoreError("Postgres isn't running. Start it.")

    monkeypatch.setattr(Memory, "open", refuse)
    assert server.open_memory() is None
    assert "jev: Postgres isn't running. Start it." in capsys.readouterr().err


# ---- Plan 7 Task 4: memory at start-up, and `jev memory`

OTHER_KEY = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8="


def test_first_start_makes_a_key_imports_the_old_profile_and_says_so(tmp_path, monkeypatch, database_url, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_DATABASE_URL", database_url)
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    (tmp_path / "profile.json").write_text(json.dumps({"phone": "+91 98765 43210", "addresses": [],
                                                       "preferences": []}))
    memory = server.open_memory(schema=f"t_{uuid.uuid4().hex[:12]}")
    try:
        key = (tmp_path / ".env").read_text().split("JEV_MEMORY_KEY=")[1].strip()
        assert os.environ["JEV_MEMORY_KEY"] == key and (tmp_path / ".env").stat().st_mode & 0o777 == 0o600
        assert memory.id_value("phone") == "+91 98765 43210" and (tmp_path / "profile.json.imported").exists()
        err = capsys.readouterr().err
        assert "Generated JEV_MEMORY_KEY in .env" in err and "Imported your old profile" in err
    finally:
        memory.drop()


def test_start_refuses_without_the_key_when_ids_are_stored(tmp_path, monkeypatch, database_url, capsys):
    from conftest import TEST_KEY

    from assistant.memory import Memory

    schema = f"t_{uuid.uuid4().hex[:12]}"
    seeded = Memory.open(database_url, schema=schema, key=TEST_KEY)
    seeded.set_id("phone", "phone number", "+91 98765 43210")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_DATABASE_URL", database_url)
    try:
        assert server.open_memory(schema=schema) is None
        assert "JEV_MEMORY_KEY is missing: the saved IDs can't be read without it" in capsys.readouterr().err
        assert not (tmp_path / ".env").exists()  # never a new key over saved IDs
        listed = server.open_memory(strict=False, schema=schema)  # `jev memory` still lists and forgets
        assert listed is not None and listed.has_ids()
        listed.close()
    finally:
        seeded.drop()


def test_saved_ids_another_key_cannot_open_are_reported_once(tmp_path, monkeypatch, database_url, capsys):
    from conftest import TEST_KEY

    from assistant.memory import Memory

    schema = f"t_{uuid.uuid4().hex[:12]}"
    seeded = Memory.open(database_url, schema=schema, key=TEST_KEY)
    seeded.set_id("phone", "phone number", "+91 98765 43210")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_DATABASE_URL", database_url)
    monkeypatch.setenv("JEV_MEMORY_KEY", OTHER_KEY)
    try:
        memory = server.open_memory(schema=schema)
        assert memory is not None and memory.ids() == []
        assert "can't open: phone" in capsys.readouterr().err
        memory.close()
    finally:
        seeded.drop()


def test_jev_memory_lists_sets_forgets_and_undoes(monkeypatch, capsys):
    memory = store()
    monkeypatch.setattr(server, "open_memory", lambda **options: memory)
    monkeypatch.setattr(server.getpass, "getpass", lambda prompt: "1234567890")
    fact = memory.remember("address", "12 MG Road", label="Office", tags=["address"])
    assert server.main(["memory", "set", "bescom_account", "BESCOM", "account", "number"]) == 0
    assert memory.id_value("bescom_account") == "1234567890"
    assert server.main(["memory"]) == 0
    out = capsys.readouterr().out
    assert "id:bescom_account  BESCOM account number" in out and f"{fact}  address  Office: 12 MG Road" in out
    assert "1234567890" not in out
    assert server.main(["memory", "forget", str(fact)]) == 0 and memory.facts() == []
    assert server.main(["memory", "undo"]) == 0 and memory.facts()[0]["text"] == "12 MG Road"
    assert server.main(["memory", "forget", "habit:999"]) == 1
    memory.decide_notes(memory.draft_notes("zeptonow.com", ["Search appears after the location pop-up closes"]),
                        save=True)
    capsys.readouterr()
    assert server.main(["memory", "notes"]) == 0
    assert "zeptonow.com\n  • Search appears after the location pop-up closes" in capsys.readouterr().out
