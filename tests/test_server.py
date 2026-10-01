"""CLI contracts. The browser check runs only with -m browser."""

import json
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

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
    assert calls[-1] == {"path": "bench/tasks.toml", "only": "uber", "repeat": 1, "unseen": 8, "seed": None,
                         "learn": False, "keep": False}


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
    note = memory.draft_notes("zeptonow.com", ["Search appears after the location pop-up closes"])[0]
    memory.decide_notes([note], save=True)
    capsys.readouterr()
    assert server.main(["memory", "notes"]) == 0
    assert f"zeptonow.com\n  note:{note}  Search appears after the location pop-up closes" in capsys.readouterr().out


def test_jev_memory_lists_each_saved_note_by_ref_and_forgets_it(monkeypatch, capsys):
    memory = store()
    monkeypatch.setattr(server, "open_memory", lambda **options: memory)
    note = memory.draft_notes("zeptonow.com", ["Search appears after the location pop-up closes"])[0]
    memory.decide_notes([note], save=True)
    other = memory.draft_notes("blinkit.com", ["Close the app banner first"])[0]
    memory.decide_notes([other], save=True)
    memory.draft_notes("zeptonow.com", ["A draft nobody saved"])
    assert server.main(["memory"]) == 0
    out = capsys.readouterr().out
    assert (f"Learned site notes:\n  note:{other}  blinkit.com: Close the app banner first\n"
            f"  note:{note}  zeptonow.com: Search appears after the location pop-up closes\n") in out
    assert "draft" not in out
    assert server.main(["memory", "notes", "zeptonow.com"]) == 0
    out = capsys.readouterr().out
    assert out == f"zeptonow.com\n  note:{note}  Search appears after the location pop-up closes\n"
    assert server.main(["memory", "forget", f"note:{note}"]) == 0
    assert "Forgot the note" in capsys.readouterr().out and memory.notes("zeptonow.com") == []


# ---- Plan 7 Task 4 fixes: the purge loop, .env handling, failures at start-up, `jev memory` edge cases


def test_purge_daily_waits_out_a_database_problem_but_not_a_bug(capsys):
    from assistant.memory import StoreError

    class Flaky:
        calls = 0

        def purge(self):
            self.calls += 1
            if self.calls < 3:
                raise StoreError("Postgres isn't reachable")
            raise RuntimeError("a programming error")

    flaky = Flaky()
    with pytest.raises(RuntimeError):  # the bug is not swallowed; in the daemon thread it ends it loudly
        server.purge_daily(flaky, every=0.001)
    assert flaky.calls == 3
    assert capsys.readouterr().err.count("couldn't clear forgotten copies: Postgres isn't reachable") == 2


def test_save_key_fills_an_empty_placeholder_and_appends_when_absent(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=abc\nJEV_MEMORY_KEY=\nBU_NAME=jev\n")
    env.chmod(0o644)
    server.save_key("KEY1", env)
    assert env.read_text() == "ANTHROPIC_API_KEY=abc\nJEV_MEMORY_KEY=KEY1\nBU_NAME=jev\n"
    assert env.stat().st_mode & 0o777 == 0o600
    other = tmp_path / "other.env"
    other.write_text("ANTHROPIC_API_KEY=abc")  # no trailing newline
    server.save_key("KEY2", other)
    assert other.read_text() == "ANTHROPIC_API_KEY=abc\nJEV_MEMORY_KEY=KEY2\n"


def test_an_empty_key_in_env_is_generated_over_and_survives_the_next_start(tmp_path, monkeypatch, database_url):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_DATABASE_URL", database_url)
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("BU_NAME=jev\nJEV_MEMORY_KEY=\n")
    monkeypatch.setenv("JEV_MEMORY_KEY", "")  # what load_environment leaves for an empty line
    schema = f"t_{uuid.uuid4().hex[:12]}"
    memory = server.open_memory(schema=schema)
    try:
        text = (tmp_path / ".env").read_text()
        assert text.count("JEV_MEMORY_KEY=") == 1 and text.startswith("BU_NAME=jev\n")
        key = text.split("JEV_MEMORY_KEY=")[1].strip()
        assert key and os.environ["JEV_MEMORY_KEY"] == key
        memory.set_id("phone", "phone number", "+91 98765 43210")
        monkeypatch.delenv("JEV_MEMORY_KEY")
        server.load_environment(tmp_path / ".env")  # the next start reads the key back from .env
        again = server.open_memory(schema=schema)
        assert again is not None and again.id_value("phone") == "+91 98765 43210"
        again.close()
    finally:
        memory.drop()


def test_new_env_file_is_never_world_readable(tmp_path, monkeypatch):
    seen = {}
    real_open = os.open

    def spy(path, flags, mode=0o777, **kwargs):
        seen["mode"] = mode
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(server.os, "open", spy)
    server.save_key("KEY", tmp_path / ".env")
    assert seen["mode"] == 0o600


def test_a_malformed_profile_stops_the_start_with_one_line_and_closes_memory(tmp_path, monkeypatch, database_url,
                                                                           capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_DATABASE_URL", database_url)
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    (tmp_path / "profile.json").write_text("{not json")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    closed = []
    from assistant.memory import Memory

    real_close = Memory.close
    monkeypatch.setattr(Memory, "close", lambda self: (closed.append(1), real_close(self))[1])
    assert server.open_memory(schema=schema) is None
    err = capsys.readouterr().err
    assert err.startswith("jev: ") and "Traceback" not in err and closed
    cleanup = Memory.open(database_url, schema=schema, key="")
    cleanup.drop()


def test_a_key_that_cannot_be_saved_stops_the_start_with_one_line(tmp_path, monkeypatch, database_url, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_DATABASE_URL", database_url)
    monkeypatch.delenv("JEV_MEMORY_KEY", raising=False)
    monkeypatch.setattr(server, "save_key", lambda key, path=None: (_ for _ in ()).throw(OSError("read-only disk")))
    schema = f"t_{uuid.uuid4().hex[:12]}"
    assert server.open_memory(schema=schema) is None
    assert "jev: read-only disk" in capsys.readouterr().err
    from assistant.memory import Memory

    Memory.open(database_url, schema=schema, key="").drop()
    assert "JEV_MEMORY_KEY" not in os.environ


def test_listing_without_the_key_says_ids_are_hidden_not_that_the_key_cannot_open_them(tmp_path, monkeypatch,
                                                                                      database_url, capsys):
    from conftest import TEST_KEY

    from assistant.memory import Memory

    schema = f"t_{uuid.uuid4().hex[:12]}"
    seeded = Memory.open(database_url, schema=schema, key=TEST_KEY)
    seeded.set_id("phone", "phone number", "+91 98765 43210")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_DATABASE_URL", database_url)
    monkeypatch.delenv("JEV_MEMORY_KEY", raising=False)
    try:
        listed = server.open_memory(strict=False, schema=schema)
        err = capsys.readouterr().err
        assert "JEV_MEMORY_KEY is missing: saved IDs are hidden from this list" in err and "can't open" not in err
        listed.close()
    finally:
        seeded.drop()


def test_memory_undo_says_what_is_wrong_as_is(monkeypatch, capsys):
    memory = store()
    monkeypatch.setattr(server, "open_memory", lambda **options: memory)
    assert server.main(["memory", "undo"]) == 1
    err = capsys.readouterr().err
    assert err.strip() == "jev: nothing to undo"


def test_memory_forget_tries_every_ref_and_fails_if_any_did(monkeypatch, capsys):
    memory = store()
    monkeypatch.setattr(server, "open_memory", lambda **options: memory)
    first = memory.remember("preference", "likes tea", tags=["general"])
    second = memory.remember("preference", "likes rice", tags=["general"])
    assert server.main(["memory", "forget", str(first), "habit:999", str(second)]) == 1
    captured = capsys.readouterr()
    assert captured.out.count("Forgot") == 2 and "nothing matches habit:999" in captured.err
    assert memory.facts() == []


def test_memory_set_checks_usage_and_the_key_before_prompting(monkeypatch, capsys):
    opened = []
    memory = store()
    monkeypatch.setattr(server, "open_memory", lambda **options: opened.append(1) or memory)

    def never(prompt):
        raise AssertionError("prompted for a value that could not be kept")

    monkeypatch.setattr(server.getpass, "getpass", never)
    assert server.main(["memory", "set"]) == 2 and not opened
    memory.fernet = None
    assert server.main(["memory", "set", "phone"]) == 1
    assert "JEV_MEMORY_KEY is missing, so IDs can't be kept." in capsys.readouterr().err


# ---- Plan 7 Task 9: the bench's own memory


def test_bench_flags_reach_serve(monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "serve", lambda bench=None: seen.update(bench=bench) or 0)
    assert server.main(["--bench", "--learn", "--keep", "--only", "a,b"]) == 0
    assert seen["bench"] == {"path": "bench/tasks.toml", "only": "a,b", "repeat": 2, "unseen": 8, "seed": None,
                             "learn": True, "keep": True}


def test_a_bench_run_opens_its_own_schema_never_your_memory(monkeypatch):
    opened = []
    monkeypatch.setattr(server, "open_memory", lambda **options: opened.append(options))  # None: serve stops here
    spec = {"path": "x", "only": None, "repeat": 1, "unseen": 0, "seed": 1, "learn": False, "keep": False}
    assert server.serve(bench=spec) == 1
    assert opened[0]["bench"] is True and re.fullmatch(r"bench_\d{8}_\d{6}", opened[0]["schema"])


def test_a_bench_schema_never_imports_your_old_profile(tmp_path, monkeypatch, database_url):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_DATABASE_URL", database_url)
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    (tmp_path / "profile.json").write_text(json.dumps({"phone": "+91 98765 43210"}))
    memory = server.open_memory(schema=f"t_{uuid.uuid4().hex[:12]}", bench=True)
    try:
        assert memory.ids() == [] and (tmp_path / "profile.json").exists()
    finally:
        memory.drop()


def test_a_bench_schema_makes_its_key_in_memory_only(tmp_path, monkeypatch, database_url, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JEV_DATABASE_URL", database_url)
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    monkeypatch.setattr(server, "save_key", lambda key, path=None: pytest.fail("a bench run wrote a key to .env"))
    memory = server.open_memory(schema=f"t_{uuid.uuid4().hex[:12]}", bench=True)
    try:
        assert memory is not None and memory.fernet is not None and "JEV_MEMORY_KEY" not in os.environ
        memory.set_id("phone", "phone number", "+91 98765 43210")
        assert memory.id_value("phone") == "+91 98765 43210"
        assert not (tmp_path / ".env").exists() and "Generated JEV_MEMORY_KEY" not in capsys.readouterr().err
    finally:
        memory.drop()


def test_save_key_replaces_env_whole_or_not_at_all(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=abc\n  # a comment  \nJEV_MEMORY_KEY=\n")
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(server.os, "replace", lambda src, dst: replaced.append((Path(src).parent, Path(dst)))
                        or real_replace(src, dst))
    server.save_key("KEY1", env)
    assert env.read_text() == "ANTHROPIC_API_KEY=abc\n  # a comment  \nJEV_MEMORY_KEY=KEY1\n"
    assert replaced == [(tmp_path, env)] and sorted(p.name for p in tmp_path.iterdir()) == [".env"]

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(server.os, "replace", broken)
    with pytest.raises(OSError, match="disk full"):
        server.save_key("KEY2", env)
    assert env.read_text().endswith("JEV_MEMORY_KEY=KEY1\n")  # the old file stands whole
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]  # and no temporary file is left behind


def test_save_key_keeps_a_symlinked_env_a_link(tmp_path):
    (tmp_path / "secrets").mkdir()
    target = tmp_path / "secrets" / "jev.env"
    target.write_text("BU_NAME=jev\n")
    env = tmp_path / ".env"
    env.symlink_to(target)
    server.save_key("KEY1", env)
    assert env.is_symlink() and env.resolve() == target.resolve()
    assert target.read_text() == "BU_NAME=jev\nJEV_MEMORY_KEY=KEY1\n" and target.stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env", "secrets"]
    assert [p.name for p in (tmp_path / "secrets").iterdir()] == ["jev.env"]


def test_learn_and_keep_need_bench(capsys):
    assert server.main(["--learn"]) == 2 and server.main(["--keep"]) == 2
    assert "--learn and --keep need --bench" in capsys.readouterr().err


@pytest.mark.parametrize("keep, dropped", [(False, [True]), (True, [])])
def test_a_bench_run_that_cannot_start_drops_its_own_schema_unless_kept(monkeypatch, keep, dropped):
    calls = []
    stub = SimpleNamespace(drop=lambda: calls.append(True), hidden_values=lambda: [])
    monkeypatch.setattr(server, "open_memory", lambda **options: stub)
    monkeypatch.setattr(server, "chrome", lambda: 1)  # Chrome won't start: serve stops before the chat
    spec = {"path": "x", "only": None, "repeat": 1, "unseen": 0, "seed": 1, "learn": False, "keep": keep}
    assert server.serve(bench=spec) == 1 and calls == dropped


def test_a_bench_run_gives_its_memory_the_bench_place_before_any_task(monkeypatch, memory):
    """The bench schema starts empty, so without its one neutral place no site could detect a location."""
    from helpers import until

    import assistant.bench as live
    import assistant.core as conversation

    seen = {}

    class Chat:
        server_port = 0

        def __init__(self, core, channel, port=None):
            self.core = core

        def serve_forever(self):
            until(lambda: "place" in seen)  # the bench thread has started its run
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(server, "open_memory", lambda **options: memory)
    monkeypatch.setattr(server, "chrome", lambda: 0)
    monkeypatch.setattr(server, "purge_daily", lambda store: None)
    monkeypatch.setattr(server, "ChatServer", Chat)
    monkeypatch.setattr(server.signal, "signal", lambda *args: None)
    monkeypatch.setattr(conversation, "Core", lambda channel, memory: SimpleNamespace(memory=memory,
                                                                                      shutdown=lambda: None))
    monkeypatch.setattr(live, "run", lambda core, *args, **options: seen.update(place=core.memory.geolocation()))
    spec = {"path": str(Path(__file__).parents[1] / "bench" / "tasks.toml"), "only": "uber", "repeat": 1,
            "unseen": 0, "seed": 1, "learn": False, "keep": True}
    assert server.serve(bench=spec) == 0
    assert seen["place"] == {"latitude": 12.9116, "longitude": 77.6389}
