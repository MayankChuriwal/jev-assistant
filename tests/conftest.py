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
def no_settling_wait(monkeypatch):
    """Fakes never change on their own, so waiting before asking the user, or between looks at a commit the site
    is still processing, would only slow the tests down."""
    from assistant import agent

    monkeypatch.setattr(agent.Agent, "steady_ms", 0)
    monkeypatch.setattr(agent.Agent, "outcome_pause", 0)
    from assistant import driver

    monkeypatch.setattr(driver.MiniAgent, "quiet_wait", 0)
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class Handler(http.server.SimpleHTTPRequestHandler):
    """Static fixtures plus a 403 page and attachment downloads."""

    def do_GET(self):
        if self.path == "/forbidden":
            return self.reply(403, b"<title>Denied</title>Access denied", "text/html")
        if self.path.startswith("/download/"):
            name = urllib.parse.unquote(self.path.removeprefix("/download/"))
            return self.reply(200, b"Invoice 123", "text/plain",
                              {"Content-Disposition": f'attachment; filename="{name}"'})
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
