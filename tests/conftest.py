"""Shared fixtures. Browser tests get a throwaway headless Chrome and two local origins; pure tests touch neither."""

import os
import tempfile

# Browser Harness fixes BU_NAME at import: set it before anything imports assistant.browser.
os.environ["BU_NAME"] = "jev-test"
os.environ["BH_TELEMETRY"] = "0"
os.environ["BH_TAB_MARKER"] = "0"
os.environ["JEV_HOME"] = tempfile.mkdtemp(prefix="jev-test-")

import functools  # noqa: E402
import http.server  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
import uuid  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

FIXTURES = Path(__file__).parents[1] / "assistant" / "fixtures"


@pytest.fixture(autouse=True)
def no_real_models(monkeypatch):
    """No test reaches a paid model: the developer's .env never loads, the keys are gone, and the HTTP client
    refuses unless a test replaces it with a scripted one."""
    from assistant import model, server

    load = server.load_environment
    monkeypatch.setattr(server, "load_environment", lambda path=None: load(path) if path else None)
    for key in ("OPENAI_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    def refuse(*_args, **_kwargs):
        raise AssertionError("A test tried to call a real model")

    monkeypatch.setattr(model.CLIENT, "post", refuse)


@pytest.fixture(autouse=True)
def preauth_shadowed(monkeypatch):
    """Pre-authorisation is shadowed unless JEV_PREAUTH=on: a test that needs it on sets it itself, so a developer's
    own environment never changes what a test sees."""
    monkeypatch.delenv("JEV_PREAUTH", raising=False)


@pytest.fixture(autouse=True)
def no_real_database(monkeypatch):
    """No test reaches the developer's own memory: a store opened without an address fails at once, and no real key
    is in the environment. Tests pass their own `memory` (or use fakes.store())."""
    monkeypatch.setenv("JEV_DATABASE_URL", "postgresql://never-the-real-memory@127.0.0.1:1/none")
    monkeypatch.delenv("JEV_MEMORY_KEY", raising=False)


@pytest.fixture(autouse=True)
def no_settling_wait(monkeypatch):
    """Fakes never change on their own, so waiting before asking the user, between looks at a commit the site is
    still processing, or for a click to show its effect would only slow the tests down."""
    from assistant import worker

    monkeypatch.setattr(worker.Worker, "steady_ms", 0)
    monkeypatch.setattr(worker.Worker, "outcome_pause", 0)
    monkeypatch.setattr(worker.Worker, "quiet_wait", 0)
    monkeypatch.setattr(worker.Worker, "blank_pause", 0, raising=False)
    monkeypatch.setattr(worker.Worker, "suggest_wait", 0)


TEST_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="  # a fixed Fernet key: tests never see a real one


@pytest.fixture(scope="session")
def database_url():
    """A throwaway database on the local Postgres for this test session, dropped at the end. The run stops at once,
    with the fix, when Postgres can't be reached."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    base = os.environ.get("JEV_TEST_DATABASE_URL", "postgresql:///postgres")
    name = f"jev_test_{os.getpid()}"
    try:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    except psycopg.OperationalError as error:
        pytest.exit("Postgres isn't running: brew services start postgresql@18 (or set JEV_TEST_DATABASE_URL). "
                    f"({str(error).strip().splitlines()[0] if str(error).strip() else 'no answer'})", returncode=1)
    yield make_conninfo(base, dbname=name)
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def memory(database_url):
    """This test's own Memory: a fresh schema in the session's database, dropped afterwards."""
    from assistant.memory import Memory

    store = Memory.open(database_url, schema=f"t_{uuid.uuid4().hex[:12]}", key=TEST_KEY)
    yield store
    store.drop()


@pytest.fixture(autouse=True)
def lazy_memory(request, monkeypatch):
    """fakes.store() hands a test its own Memory on first use: tests that never touch memory never touch Postgres."""
    import fakes

    monkeypatch.setattr(fakes, "MEMORY", lambda: request.getfixturevalue("memory"))


CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


class Handler(http.server.SimpleHTTPRequestHandler):
    """Static fixtures plus a 403 page, attachment downloads, and documents shown inline (a PDF in the browser)."""

    def do_GET(self):
        if self.path == "/forbidden":
            return self.reply(403, b"<title>Denied</title>Access denied", "text/html")
        if self.path.startswith("/download/"):
            name = urllib.parse.unquote(self.path.removeprefix("/download/"))
            return self.reply(200, b"Invoice 123", "text/plain",
                              {"Content-Disposition": f'attachment; filename="{name}"'})
        if self.path.startswith("/documents/"):  # no attachment header: Chrome shows it in its PDF viewer
            return self.reply(200, PDF, "application/pdf")
        return super().do_GET()

    def reply(self, status, body, mime, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def site():
    """Fixtures on two origins: 127.0.0.1 and localhost are different sites, so frames between them are cross-origin."""
    port = free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(Handler, directory=str(FIXTURES)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield {"a": f"http://127.0.0.1:{port}", "b": f"http://localhost:{port}"}
    server.shutdown()


@pytest.fixture(scope="session")
def chrome(tmp_path_factory):
    if not Path(CHROME).exists():
        pytest.skip("Google Chrome is not installed")
    from browser_harness.admin import restart_daemon

    port = free_port()
    process = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={tmp_path_factory.mktemp('chrome')}", "--no-first-run", "--no-default-browser-check",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while True:
        try:
            urllib.request.urlopen(url + "/json/version", timeout=1).close()
            break
        except OSError:
            if time.monotonic() > deadline:
                process.kill()
                raise
            time.sleep(0.1)
    os.environ["BU_CDP_URL"] = url
    restart_daemon("jev-test")  # a daemon left by an earlier run points at a dead Chrome
    yield url
    restart_daemon("jev-test")
    process.terminate()
    process.wait(10)


@pytest.fixture
def browser_at(chrome, site):
    """Open fixture pages in the test Chrome. Every Browser opened here is closed afterwards."""
    from assistant.browser import Browser

    opened = []

    def open_page(path, origin="a", **options):
        b = Browser(site[origin] + path, **options)
        opened.append(b)
        return b

    yield open_page
    for b in opened:
        b.close()
