"""Command line. `jev chrome` launches the hidden agent Chrome; `jev probe` checks sites for blocking."""

import argparse
import base64
import getpass
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PORT = 9333
SMOKE_SITES = ("https://www.swiggy.com/instamart", "https://www.swiggy.com", "https://m.uber.com",
               "https://www.amazon.in")
BLOCKED = re.compile(r"captcha|verify (that )?you are (a )?human|are you a robot|unusual traffic|access denied"
                     r"|request blocked", re.I)


def load_environment(path=None):
    """Read .env before Browser Harness is imported: it fixes BU_NAME at import time."""
    path = Path(path) if path else Path.cwd() / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def home():
    return Path(os.environ.get("JEV_HOME") or Path.home() / ".jev")


def chrome_command():
    return ["open", "-gj", "-na", "Google Chrome", "--args", f"--user-data-dir={home() / 'chrome'}",
            f"--remote-debugging-port={PORT}", "--no-first-run", "--no-default-browser-check",
            "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling"]


def devtools_ready(url=None):
    url = url or os.environ.get("BU_CDP_URL") or f"http://127.0.0.1:{PORT}"
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/json/version", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def chrome():
    url = f"http://127.0.0.1:{PORT}"
    if devtools_ready(url):
        print(f"Agent Chrome is already running on {url}.")
        return 0
    (home() / "chrome").mkdir(parents=True, exist_ok=True)
    subprocess.run(chrome_command(), check=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if devtools_ready(url):
            print(f"Agent Chrome is running hidden on {url} with profile {home() / 'chrome'}.")
            return 0
        time.sleep(0.25)
    print("Agent Chrome did not open its debugging port.", file=sys.stderr)
    return 1


def classify(status, text):
    if status in (401, 403, 429):
        return f"http-{status}"
    if BLOCKED.search(text or ""):
        return "captcha"
    if status and status >= 400:
        return f"http-{status}"
    return "ok"


def verdict(url):
    from .browser import Browser

    browser = Browser(url)
    try:
        status = browser.evaluate("performance.getEntriesByType('navigation')[0]?.responseStatus ?? 0")
        text = browser.evaluate("document.body?.innerText?.slice(0, 5000) ?? ''")
        return classify(status, text)
    finally:
        browser.close()


def probe(urls):
    if not devtools_ready():
        print("Agent Chrome is not running. Start it with: uv run jev chrome", file=sys.stderr)
        return 1
    urls = list(urls) or list(SMOKE_SITES)
    width = max(map(len, urls))
    failures = 0
    for url in urls:
        try:
            result = verdict(url)
        except Exception as error:  # report every site; one failure must not hide the rest
            result = f"error: {error}"
        failures += result != "ok"
        print(f"{url.ljust(width)}  {result}", flush=True)
    return 1 if failures else 0


def terminal_asker(message, *, read=input, secret_read=getpass.getpass, write=print):
    """Answer the agent in a terminal: a number picks an option, "cancel" stops the task."""
    write("")
    write(message["text"])
    if message.get("image"):
        path = Path(tempfile.gettempdir()) / f"jev-{time.time_ns()}.png"
        path.write_bytes(base64.b64decode(message["image"]))
        write(f"(image: {path})")
    options = message.get("options") or []
    for n, option in enumerate(options, 1):
        detail = f" — {option['description']}" if option.get("description") else ""
        write(f"  {n}. {option['label']}{detail}")
    if message["kind"] == "secret":
        answer = secret_read("(hidden) > ").strip()
    else:
        answer = read("file path > " if message["kind"] == "file" else "> ").strip()
    if answer.lower() in {"cancel", "/cancel"}:
        return {"cancel": True}
    if options and answer.isdigit() and 1 <= int(answer) <= len(options):
        reply = {"option": options[int(answer) - 1]["id"]}
        if message.get("remember") and read("Remember this for next time? [y/N] > ").strip().lower() == "y":
            reply["remember"] = True
        return reply
    if message["kind"] == "file":
        return {"file": str(Path(answer).expanduser())}
    return {"text": answer}


def terminal_notify(message, *, write=print):
    write(("Result: " if message["kind"] == "result" else "Note: ") + message["text"])


def run(goal, url=None):
    from .agent import Agent

    agent = Agent(url, goal, asker=terminal_asker, notify=terminal_notify)
    shown = 0
    try:
        for state in agent.run():
            for step in state["history"][shown:]:
                print(f"  {state['elapsed_ms'] / 1000:6.1f}s  {step['operation']:<12} {step['action'][:70]}")
            shown = len(state["history"])
    finally:
        agent.close()
    return 0 if agent.state["status"] == "done" else 1


def main(argv=None):
    load_environment()
    parser = argparse.ArgumentParser(prog="jev", description="Jev Assistant")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("chrome", help="launch the hidden agent Chrome with its own profile")
    probe_parser = commands.add_parser("probe", help="check sites for 403s and captchas in the agent Chrome")
    probe_parser.add_argument("urls", nargs="*")
    run_parser = commands.add_parser("run", help="do one task in the agent Chrome, asking questions here")
    run_parser.add_argument("goal")
    run_parser.add_argument("--url", help="start page; found from the goal when omitted")
    args = parser.parse_args(argv)
    if args.command == "chrome":
        return chrome()
    if args.command == "probe":
        return probe(args.urls)
    if args.command == "run":
        return run(args.goal, args.url)
    parser.print_help()
    return 0
