"""Command line and chat server. `jev` serves the chat; `jev chrome` launches the hidden agent Chrome; `jev probe`
checks sites for blocking; `jev run` does one task in the terminal."""

import argparse
import base64
import errno
import getpass
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

import psycopg

PORT = 9333
CHAT_PORT = 8766
MAX_WEBHOOK = 64 * 1024
MAX_UPLOAD = 20 * 1024 * 1024
DRAIN_LIMIT = 64 * 1024 * 1024  # larger refused bodies are cut off instead of read
STATIC = Path(__file__).parent / "static"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
PAGE_POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; "
               "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
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


def save_key(key, path=None):
    """Put JEV_MEMORY_KEY in .env, creating it. An empty `JEV_MEMORY_KEY=` placeholder line is filled in, never
    duplicated. The file is made readable only by the user: a new one is created that way, never world-readable."""
    path = Path(path) if path else Path.cwd() / ".env"
    lines = path.read_text().splitlines() if path.exists() else []
    line, done, out = f"JEV_MEMORY_KEY={key}", False, []
    for existing in lines:
        if re.fullmatch(r"\s*JEV_MEMORY_KEY\s*=\s*", existing):
            if not done:
                out.append(line)
            done = True
        else:
            out.append(existing)
    if not done:
        out.append(line)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write("\n".join(out) + "\n")
    path.chmod(0o600)  # an .env that already existed may have been looser


def open_memory(*, strict=True, **options):
    """The user's memory, ready, or None after saying why not: Postgres reached, the key in place, and Phase A's
    profile.json imported once. With no JEV_MEMORY_KEY and no ID stored yet, a key is generated into .env. With IDs
    stored and no key, a strict open (the chat, `jev run`) refuses, because a new key would lose them; `jev memory`
    still lists and forgets. An empty JEV_MEMORY_KEY counts as missing. Any failure after the connection closes it
    and says one line. `options` (url, schema) go to Memory.open."""
    from cryptography.fernet import Fernet

    from .memory import Memory, StoreError

    memory = None
    try:
        memory = Memory.open(**options)
        missing = not os.environ.get("JEV_MEMORY_KEY")
        if missing:
            if memory.has_ids():
                if strict:
                    memory.close()
                    print("jev: JEV_MEMORY_KEY is missing: the saved IDs can't be read without it. Put it back in "
                          "your .env (or the host's secrets), or forget them with `jev memory forget id:<key>`.",
                          file=sys.stderr)
                    return None
                print("jev: JEV_MEMORY_KEY is missing: saved IDs are hidden from this list.", file=sys.stderr)
            else:
                key = Fernet.generate_key().decode()
                memory.unlock(key)
                save_key(key)
                os.environ["JEV_MEMORY_KEY"] = key
                missing = False
                print("jev: Generated JEV_MEMORY_KEY in .env. Keep it: without it, the IDs you save can't be read.",
                      file=sys.stderr)
        if memory.fernet is not None and memory.import_profile(home() / "profile.json"):
            print("jev: Imported your old profile into memory (profile.json is now profile.json.imported).",
                  file=sys.stderr)
        memory.refresh()
        if memory.unreadable and not missing:
            print(f"jev: Saved IDs this JEV_MEMORY_KEY can't open: {', '.join(memory.unreadable)}. I'll ask for them "
                  "again when a task needs them.", file=sys.stderr)
        return memory
    except (StoreError, psycopg.Error, OSError, ValueError) as error:  # ValueError: a malformed profile.json
        if memory is not None:
            memory.close()
        print(f"jev: {error}", file=sys.stderr)
        return None


def print_listing(listing):
    sections = [
        ("IDs (values are never shown):", [f"  {i['ref']}  {i['label']}" for i in listing["ids"]]),
        ("Facts:", [f"  {f['ref']}  {f['kind']}  " + (f"{f['label']}: {f['text']}" if f["label"] else f["text"])
                    for f in listing["facts"]]),
        ("Standing approvals:", [f"  {r['ref']}  {r['text']}" for r in listing["rules"]]),
        ("Learned site notes:", [f"  {host}: {n} line{'' if n == 1 else 's'}"
                                 for host, n in listing["notes"].items()]),
        ("Habits:", [f"  {h['ref']}  {h['host']} · {h['skill']} · {h['title']} ({h['replays']} replays, "
                     f"{h['clean_replays']} clean)" for h in listing["habits"]]),
    ]
    for title, lines in sections:
        print(title)
        print("\n".join(lines) if lines else "  none")


def memory_command(action="list", rest=()):
    """`jev memory`: what the agent keeps (never an ID's value); set an ID, typed hidden; forget; undo; notes."""
    from .memory import LABELS, StoreError, Unstorable

    if action == "set" and not rest:
        print("Usage: jev memory set <key> [label]", file=sys.stderr)
        return 2
    if action == "forget" and not rest:
        print("Usage: jev memory forget <ref> (refs come from `jev memory`)", file=sys.stderr)
        return 2
    memory = open_memory(strict=False)
    if memory is None:
        return 1
    failed = False
    try:
        if action == "list":
            print_listing(memory.listing())
        elif action == "set":
            if memory.fernet is None:
                print("jev: JEV_MEMORY_KEY is missing, so IDs can't be kept.", file=sys.stderr)
                return 1
            key, label = rest[0], " ".join(rest[1:])
            memory.set_id(key, label, getpass.getpass(f"{label or key} (hidden): "), actor="user")
            print(f"Saved your {label or LABELS.get(key) or key.replace('_', ' ')}. The models never see it.")
        elif action == "forget":
            for ref in rest:  # every ref is tried; one that fails doesn't stop the others
                try:
                    print(f"Forgot {memory.forget(ref)}.")
                except KeyError:
                    print(f"jev: nothing matches {ref}", file=sys.stderr)
                    failed = True
                except (StoreError, Unstorable, ValueError) as error:
                    print(f"jev: {ref}: {error}", file=sys.stderr)
                    failed = True
        elif action == "undo":
            print(f"Undone: {memory.undo()}.")
        else:  # notes
            learned = memory.learned()
            for host in ([rest[0]] if rest else sorted(learned)):
                print(host + "".join(f"\n  • {line}" for line in learned.get(host, [])))
    except KeyError as error:  # undo: its message says what's wrong ("nothing to undo")
        print(f"jev: {error.args[0] if error.args else 'nothing matches'}", file=sys.stderr)
        return 1
    except (StoreError, Unstorable, ValueError) as error:
        print(f"jev: {error}", file=sys.stderr)
        return 1
    finally:
        memory.close()
    return 1 if failed else 0


def purge_daily(memory, every=3600):
    """While jev runs: once an hour, clear the copies of anything forgotten more than a day ago. Only a database
    problem is waited out; any other error is a bug and ends the thread loudly."""
    from .memory import StoreError

    while True:
        time.sleep(every)
        try:
            memory.purge()
        except (StoreError, psycopg.Error) as error:  # Postgres away for a moment: the next hour tries again
            print(f"jev: couldn't clear forgotten copies: {error}", file=sys.stderr)


def chrome_command():
    return ["open", "-gj", "-na", "Google Chrome", "--args", f"--user-data-dir={home() / 'chrome'}",
            f"--remote-debugging-port={PORT}", "--no-first-run", "--no-default-browser-check",
            "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
            "--enable-features=StrictOriginIsolation"]  # payments.site.com inside m.site.com: its own, readable frame


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
    from . import jev, planner, skills
    from . import worker as runtime
    from .gate import cap
    from .privacy import SECRETS, Secrets

    memory = open_memory()
    if memory is None:
        return 1
    library = {"skills": skills.load(), "sites": skills.load_sites()}
    token = SECRETS.set(Secrets(memory.hidden_values()))  # the goal may hold a phone number or an email
    try:
        info = jev.intake(goal, skills.kinds(library["skills"]), several=False)
        skill = info["skill"] if info["skill"] in library["skills"] else None
        site = skills.named(goal, library["sites"])
        url = url or (skills.start(site, skill) if site else planner.start(goal)[0])
    finally:
        SECRETS.reset(token)
    worker = runtime.Worker(url, goal, asker=terminal_asker, notify=terminal_notify, skill=skill,
                            preauth=info["preauth"] == "yes", cap=cap(goal), library=library, memory=memory)
    shown = problems = 0
    try:
        while worker.state["status"] not in runtime.STOPPED:
            try:
                state = worker.command("tick")
            except (ValueError, RuntimeError) as error:  # a model or network hiccup: report it, decide again
                problems += 1
                print(f"Problem: {error}")
                commit = worker.state.get("commit")
                if commit:
                    print(f"{commit['label']} may already have gone through. Check it before running this again.")
                    break
                if problems >= 3:
                    print("Stopping after 3 problems in a row.")
                    break
                continue
            problems = 0
            for step in state["history"][shown:]:
                print(f"  {state['elapsed_ms'] / 1000:6.1f}s  {step['operation']:<12} {step['action'][:70]}")
            shown = len(state["history"])
    finally:
        worker.close()
    return 0 if worker.state["status"] == "done" else 1


def forget(sites):
    """Sign the agent Chrome out of these sites, so the next task signs in (and asks for an OTP) again."""
    if not devtools_ready():
        print("Agent Chrome is not running. Start it with: uv run jev chrome", file=sys.stderr)
        return 1
    from .browser import forget_sites

    for host, count in forget_sites(sites).items():
        print(f"{host}: signed out ({count} cookies removed, site data cleared)")
    return 0


def traces(limit=10, folder=None):
    """The latest task traces, one line each, with what the smoke set judges: outcome, questions, evidence."""
    folder = Path(folder) if folder else home() / "traces"
    paths = sorted(folder.glob("*.json"))[-limit:] if folder.is_dir() else []
    if not paths:
        print(f"No traces yet in {folder}.")
        return 0
    for path in paths:
        try:
            t = json.loads(path.read_text())
            evidence = " ".join(((t.get("result") or {}).get("evidence") or "no evidence").split())
            print(f"{path.name[:15]}  {t['status']:<9} {t['title'][:28]:<28} {t['questions']:>2} questions "
                  f"{len(t['steps']):>4} steps {t['elapsed_ms'] / 1000:7.1f}s  {evidence[:48]}")
        except (ValueError, KeyError, TypeError):
            print(f"{path.name}  unreadable")
    print(f"\nFull traces: {folder}")
    return 0


def debug_view(core):
    """Each task's recent steps and a live screenshot with secret fields masked. Development only."""
    with core.lock:
        tasks = list(core.tasks.values())
    view = []
    for t in tasks:  # screenshots are taken outside the core's lock: they are slow
        try:
            shot = t.worker.browser.screenshot(masked=t.worker.secrets.nodes) if t.worker else None
        except Exception:  # the task's tab may be closing
            shot = None
        view.append({"id": t.id, "title": t.title, "status": t.status, "host": t.host,
                     "history": t.worker.state["history"][-20:] if t.worker else [], "screenshot": shot})
    return view


class ChatHandler(BaseHTTPRequestHandler):
    server_version = "jev"

    def log_message(self, *_args):
        pass  # chat content never reaches the terminal

    def reply(self, status, body=b"", kind="application/json", headers=()):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def local(self):
        """Host and Origin name this server, so no other site and no DNS-rebound name reaches it."""
        names = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origins = {f"http://{n}" for n in names} | {os.environ.get("JEV_PUBLIC_ORIGIN") or "http://127.0.0.1"}
        origin = self.headers.get("Origin")  # JEV_PUBLIC_ORIGIN: one tunnel (ngrok) that forwards Host as local
        return self.headers.get("Host") in names and (origin is None or origin in origins)

    def authorised(self, query):
        token = self.headers.get("X-Jev-Token") or (query.get("t") or [""])[0]
        return self.local() and secrets.compare_digest(token.encode(), self.server.token.encode())

    def body(self, limit):
        """The request body, or None when its length is missing, malformed, or over `limit`."""
        length = self.headers.get("Content-Length", "")
        if length.isdigit() and int(length) <= limit:
            return self.rfile.read(int(length))
        self.close_connection = True
        if length.isdigit() and int(length) <= DRAIN_LIMIT:  # read and drop it, so the client sees the 413
            remaining = int(length)                           # instead of a reset connection
            while remaining > 0 and (chunk := self.rfile.read(min(remaining, 65536))):
                remaining -= len(chunk)
        return None

    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path in ASSETS:
            if not self.local():
                return self.reply(403, {"error": "forbidden"})
            name, kind = ASSETS[url.path]
            data = (STATIC / name).read_bytes().replace(b"{{TOKEN}}", self.server.token.encode())
            return self.reply(200, data, kind, [("Content-Security-Policy", PAGE_POLICY)])
        if not self.authorised(query):
            return self.reply(403, {"error": "forbidden"})
        if url.path == "/outbox":
            try:
                after = int(query.get("after", ["0"])[0])
                wait = max(0.0, min(float(query.get("wait", ["0"])[0]), 25.0))  # NaN becomes 0
            except ValueError:
                return self.reply(400, {"error": "bad query"})
            messages, typing = self.server.channel.since(after, wait)
            return self.reply(200, {"messages": messages, "typing": typing, "open": self.server.core.showing()})
        if url.path.startswith("/media/"):
            try:
                path, mime, name = self.server.channel.media(url.path.removeprefix("/media/"))
                data = Path(path).read_bytes()
            except (KeyError, OSError):
                return self.reply(404, {"error": "not found"})
            download = quote(name or Path(path).name)
            disposition = "inline" if mime in IMAGES else f"attachment; filename*=UTF-8''{download}"
            return self.reply(200, data, mime, [("Content-Disposition", disposition),
                                                ("Content-Security-Policy", "sandbox")])
        if url.path == "/api/debug" and os.environ.get("JEV_DEBUG") == "1":
            return self.reply(200, debug_view(self.server.core))
        return self.reply(404, {"error": "not found"})

    def do_POST(self):
        url = urlsplit(self.path)
        if not self.authorised(parse_qs(url.query)):
            return self.reply(403, {"error": "forbidden"})
        if url.path == "/webhook":
            data = self.body(MAX_WEBHOOK)
            if data is None:
                return self.reply(413, {"error": "body missing or too large"})
            try:
                self.server.core.receive_webhook(json.loads(data))
            except ValueError:  # bad JSON, or JSON that is not a WhatsApp webhook body
                return self.reply(400, {"error": "not a WhatsApp webhook body"})
            return self.reply(200, {"ok": True})
        if url.path == "/media":
            data = self.body(MAX_UPLOAD)
            if data is None:
                return self.reply(413, {"error": "body missing or too large"})
            if not data:
                return self.reply(400, {"error": "empty file"})
            mime = (self.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip()
            name = Path(unquote(self.headers.get("X-Filename") or "")).name or None
            return self.reply(200, {"id": self.server.channel.store(data, mime, name)})
        return self.reply(404, {"error": "not found"})


class ChatServer(ThreadingHTTPServer):
    """The chat page and the channel's endpoints, on loopback only."""

    daemon_threads = True

    def __init__(self, core, channel, *, port=CHAT_PORT, token=None):
        self.core, self.channel = core, channel
        self.token = token or secrets.token_urlsafe(24)
        super().__init__(("127.0.0.1", port), ChatHandler)


def serve(port=CHAT_PORT, bench=None):
    from .channel_web import WebChannel
    from .core import Core

    memory = open_memory()
    if memory is None:
        return 1
    threading.Thread(target=purge_daily, args=(memory,), daemon=True).start()  # forgotten means gone after a day
    if chrome():
        return 1
    channel = WebChannel(home() / "media")
    try:
        core = Core(channel, memory=memory)
        httpd = ChatServer(core, channel, port=port)
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        print(f"Another jev is already serving the chat on port {port}. Stop it first (Ctrl+C in its terminal), "
              "then run jev again.", file=sys.stderr)
        return 1
    print(f"Chat: http://127.0.0.1:{httpd.server_port}/  (Ctrl+C stops)", flush=True)
    if bench:
        from . import bench as live

        path, only, repeat, unseen, seed = bench
        tasks = [t for t in live.load(path) if not only or t["id"] in only.split(",")]
        pool = Path(path).with_name("unseen.toml")
        pool = pool if pool.exists() and not only and unseen else None
        seed = seed if seed is not None else int(time.time()) % 100000
        out = home() / "bench" / time.strftime("%Y%m%d-%H%M%S")
        print(f"Bench: {len(tasks)} known × {repeat}" + (f", {unseen} unseen per run, seed {seed}" if pool else ""),
              flush=True)
        threading.Thread(target=lambda: print("Bench report:", live.run(core, tasks, repeat, out, pool=pool,
                                                                        unseen=unseen, seed=seed), flush=True),
                         daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))  # stop like Ctrl+C
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        core.shutdown()  # running tasks are cancelled and write their traces
        httpd.server_close()
    return 0


def main(argv=None):
    load_environment()
    parser = argparse.ArgumentParser(prog="jev", description="Jev Assistant. With no command, serves the chat.")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("chrome", help="launch the hidden agent Chrome with its own profile")
    probe_parser = commands.add_parser("probe", help="check sites for 403s and captchas in the agent Chrome")
    probe_parser.add_argument("urls", nargs="*")
    run_parser = commands.add_parser("run", help="do one task in the agent Chrome, asking questions here")
    run_parser.add_argument("goal")
    run_parser.add_argument("--url", help="start page; found from the goal when omitted")
    forget_parser = commands.add_parser("forget", help="sign the agent Chrome out of sites, e.g. swiggy.com uber.com")
    forget_parser.add_argument("sites", nargs="+")
    traces_parser = commands.add_parser("traces", help="list the latest task traces")
    traces_parser.add_argument("--limit", type=int, default=10)
    memory_parser = commands.add_parser("memory", help="what the agent remembers: list (default), set <key> [label], "
                                                       "forget <ref>…, undo, notes [host]")
    memory_parser.add_argument("action", nargs="?", default="list", choices=["list", "set", "forget", "undo", "notes"])
    memory_parser.add_argument("rest", nargs="*")
    save_parser = commands.add_parser("bench-save", help="keep a trace step as a replay case")
    save_parser.add_argument("trace")
    save_parser.add_argument("step", type=int, help="the Jev request's number in the trace, 1 = first")
    save_parser.add_argument("expect", help='e.g. "op=CLICK, target~search"')
    commands.add_parser("replay", help="ask Jev every saved replay case again (uses the paid API)")
    parser.add_argument("--bench", nargs="?", const="bench/tasks.toml", help="run the live bench file")
    parser.add_argument("--only", help="bench task ids, comma separated")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--unseen", type=int, default=8, help="unseen bench tasks sampled per run")
    parser.add_argument("--seed", type=int, help="seed for sampling unseen tasks (default: from the clock)")
    args = parser.parse_args(argv)
    if args.command == "chrome":
        return chrome()
    if args.command == "probe":
        return probe(args.urls)
    if args.command == "run":
        return run(args.goal, args.url)
    if args.command == "traces":
        return traces(args.limit)
    if args.command == "forget":
        return forget(args.sites)
    if args.command == "memory":
        return memory_command(args.action, args.rest)
    if args.command == "bench-save":
        from . import bench as live

        print(live.save_case(args.trace, args.step, args.expect, home() / "bench" / "cases"))
        return 0
    if args.command == "replay":
        from . import bench as live

        results = live.replay(home() / "bench" / "cases")
        for r in results:
            print(("ok   " if r["ok"] else "FAIL ") + r["case"] + ("" if r["ok"] else f"  got {r['got']}"))
        return 0 if all(r["ok"] for r in results) else 1
    return serve(bench=args.bench and (args.bench, args.only, args.repeat, args.unseen, args.seed))
