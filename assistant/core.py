"""The conversation's code: every chat message goes, by rule and never by a model, to the open question it answers,
to a stop, to a log out, or to the brain (brain.py), which talks to the user and runs tasks as its tools. Core keeps
one question open at a time and runs each task's worker — at most three at once and one per site."""

import base64
import itertools
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import browser, jev, messages, planner, replay, rules, skills
from . import learn as learning
from .brain import Brain
from .gate import cap, rule_cap, same_cart
from .memory import LABELS, RULE_DAYS, UNSTORABLE, Memory, StoreError, Unstorable, rule_line, rupees
from .privacy import SECRETS, Secrets, storable
from .worker import DELEGATE, STOPPED, STUCK, Worker

MAX_RUNNING = 3
MAX_ERRANDS = 5  # tasks one message may start
PAUSE_AFTER = 15 * 60
CANCEL_AFTER = 24 * 3600
BENCH_CODE_WAIT = 5 * 60  # a bench run skips a site whose sign-in code doesn't come
MAX_DOWNLOAD = 100 * 1024 * 1024
FIRST = {"secret", "confirm"}  # asked before anything older
STOP_WORDS = {"cancel", "stop", "/cancel", "never mind", "nevermind", "abort", "ruko", "bas"}  # stop a task, no model
ACTIVE = {"starting", "waiting", "running", "paused"}
MAX_NOTES_SHOWN = 10  # learned site notes the chat lists
MANDATES = "upihelp.npci.org.in"  # NPCI's portal listing every UPI AutoPay mandate, across all UPI apps
LOOKS_LIKE_AN_ID = ("That looks like an ID. Tell me what it is called (like “my BESCOM account number is …”) so I "
                    "can keep it hidden.")
PLACEHOLDER = re.compile(r"\[(?:profile|secret):[\w.]+\]")
CAPTCHA_REPLY = re.compile(r"\S{1,12}|\d+(?:\s*(?:,|&|and)?\s*\d+)*", re.IGNORECASE)  # “F658”, “none”, “2, 5 and 8”
BARE_ID = re.compile(r"[\d\s()+-]*\d[\d\s()+-]*|[A-Za-z0-9]*\d[A-Za-z0-9]*")  # the whole message: a number or a code
KEPT_OUT = ("That looks like an ID, so I kept it out of the models. When a site needs one I ask for it in the hidden "
            "field, where it goes straight to the page.")
LOG_OUT = re.compile(r"\s*(?:please\s+)?(?:log|sign)\s*(?:me\s+)?out(?:\s+of\s+[\w .'-]{1,30})?[.!]?\s*|"
                     r"\s*end\s+(?:the\s+|this\s+|my\s+)?session[.!]?\s*", re.IGNORECASE)  # the whole message


def site(url):
    host = urlparse(url).netloc.lower()
    for prefix in ("www.", "m."):
        host = host.removeprefix(prefix)
    return host


ID_SCAN = 2000  # characters of a message read for an ID
AMOUNTS = re.compile(r"(?:₹|\brs\.?|\binr)\s*\d[\d,]*(?:\.\d+)?", re.I)  # ₹1,500  Rs 300  INR 99.50
DATES = (re.compile(r"(?<!\d)(?:0?[1-9]|[12]\d|3[01])[/-](?:0?[1-9]|1[0-2])[/-](?:19|20)?\d{2}(?!\d)"),  # 12/05/2026
         re.compile(r"(?<!\d)(?:19|20)\d{2}[/-](?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])(?!\d)"))  # 2026-05-12
YEARS = re.compile(r"(?<![\w+])(?<!\d[\s-])(?:19|20)\d{2}(?!\w)(?![\s-]\d)")  # a year on its own, not inside a number
# a quantity: 500ml, 250 g, 10kg, a 6-pack, 3 pcs, 32x30, 2BHK. A number of six digits or more is never one.
UNITS = re.compile(r"(?<![\w.])(?:\d{1,5}(?:\.\d+)?\s?(?:ml|l|ltrs?|litres?|liters?|g|gms?|grams?|kgs?|mg|oz|pcs?|"
                   r"pieces?|packs?|units?|cm|mm|m)|\d{1,4}\s?-\s?(?:packs?|pcs?|pieces?|count)|\d{1,4}\s?x\s?\d{1,4}|"
                   r"\d{1,2}\s?bhk)(?![\w.@-])", re.I)
# an email or UPI ID; a hyphenated code (AB-1234-CD) whole; a number of six digits or more, spaced or not; a code
ID_CANDIDATES = (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*"),
                 re.compile(r"\b(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b"),
                 re.compile(r"\+?\d(?:[\s-]?\d){5,}"),
                 re.compile(r"\b(?=[A-Za-z]*\d)[A-Za-z0-9]{4,}\b"))


def bare_id(text):
    """A message that is only a long number or code (an ERN, a UTI, a phone, a PAN): never sent to a model in the open.
    A pincode (six digits) and a number among words still go through."""
    digits = sum(c.isdigit() for c in text)
    return bool(BARE_ID.fullmatch(text)) and (digits >= 8 or (len(text) >= 8 and digits >= 4 and
                                                               any(c.isalpha() for c in text)))


def captcha_reply(question, text):
    """The text the user read off a captcha, or its squares' numbers: Jev can't tell “F658” from small talk. A stop
    word still goes to Jev."""
    return bool(question.message.get("captcha")) and text.lower() not in STOP_WORDS and \
        bool(CAPTCHA_REPLY.fullmatch(text))


def id_candidates(text):
    """The values in a message that can be an ID, each once: emails and UPI IDs, then hyphenated codes, numbers, and
    codes. Quantities (500ml, a 6-pack, 2BHK), prices, dates, and lone years are not candidates: a stray one must
    never become the ID. Only the first ID_SCAN characters are read (the patterns slow down on a long unbroken run),
    without the word the cut splits."""
    if len(text) > ID_SCAN:
        text = text[:ID_SCAN]
        text = text[:max(0, *map(text.rfind, " \n\t"))]
    found = []
    for n, pattern in enumerate(ID_CANDIDATES):
        found += [match[0].strip() for match in pattern.finditer(text)]
        text = pattern.sub(" ", text)
        if n == 0:  # after emails, before codes: a quantity is never an ID
            text = UNITS.sub(" ", text)
        if n == 1:  # after emails and hyphenated codes, before the plain numbers
            for noise in (AMOUNTS, *DATES, YEARS):
                text = noise.sub(" ", text)
    return list(dict.fromkeys(found))


def host_of(site):
    """The bare host in a site string a model wrote, with any scheme and path dropped."""
    text = site.strip().lower()
    return skills.bare(urlparse(text if "//" in text else "//" + text).hostname or "")


def note_host(key):
    """The bare host of a site note's key, its path dropped ("google.com/travel/flights" → "google.com")."""
    return skills.bare(key.partition("/")[0].lower())


def memory_text(listing):
    """What memory holds, for the chat: never an ID's value."""
    lines = [f"• {f['label']}: {f['text']}" if f["label"] else f"• {f['text']}" for f in listing["facts"]]
    lines = (["What I know:"] + lines) if lines else []
    if listing["ids"]:
        lines.append("IDs I keep hidden: " + ", ".join(i["label"] for i in listing["ids"]))
    if listing["rules"]:
        lines += ["Standing approvals:"] + [f"• {r['text']}" for r in listing["rules"]]
    if listing["notes_list"]:
        notes = listing["notes_list"]
        lines += ["Learned site notes:"] + [f"• {n['host']}: {n['text']}" for n in notes[:MAX_NOTES_SHOWN]]
        if len(notes) > MAX_NOTES_SHOWN:
            lines.append(f"…and {len(notes) - MAX_NOTES_SHOWN} more: `jev memory notes` lists them all.")
    if listing["habits"]:
        lines.append("Habits: " + ", ".join(h["title"] for h in listing["habits"]))
    return "\n".join(lines) + "\nSay “forget …” to remove one." if lines else "I don't remember anything about you yet."


class Question:
    def __init__(self, qid, task, message, asked_at):
        self.id, self.task, self.message, self.asked_at = qid, task, message, asked_at
        self.reply, self.sent, self.answered = None, [], threading.Event()


class Task:
    def __init__(self, tid, goal, single=False):
        self.id, self.goal, self.title, self.host = tid, goal, goal[:60], None
        self.status, self.worker, self.noticed = "starting", None, False  # noticed: “I'll get to that…” said
        self.cancelled, self.sent_files, self.problems = False, 0, 0
        self.skill, self.preauth, self.cap, self.url, self.single = None, False, None, None, single
        self.script, self.bench = None, {"gate": None, "asked": []}  # a bench run: scripted replies
        self.habit, self.sameness = None, None  # the saved habit this goal repeats
        self.capture, self.result = False, None  # a brain tool's task: how it ended goes back to the brain


class Core:
    def __init__(self, channel, *, user="local", memory=None, make_worker=None, start=None, brain=None, intake=None,
                 split=None, remember=None, learn=None, memory_job=None, habit_job=None, library=None,
                 clock=time.monotonic, poll=1.0):
        self.channel, self.user = channel, user
        self.memory = memory if memory is not None else Memory.open()
        self.make_worker = make_worker or self.worker_for
        self.start = start or planner.start
        self.intake = intake or jev.intake
        self.split = split or planner.split
        self.remember_job = remember or planner.remember
        self.learn = learn or learning.after
        self.memory_job = memory_job or planner.memory
        self.habit_job = habit_job or jev.habit
        self.library = library if library is not None else {"skills": skills.load(), "sites": skills.load_sites()}
        self.clock, self.poll = clock, poll
        self.lock = threading.RLock()
        self.slots = threading.Condition(self.lock)
        self.tasks, self.queue, self.open, self.log = {}, [], None, []
        self.remembering, self.offered = {}, {}  # offered: a brain reply's option ids → the option's own words
        self.offers, self.offer_ids = {}, itertools.count(1)  # buttons offered after a task, by key
        self.payees = set()  # the user's mandates read off MyUPI this session: “cancel the aisle” names one
        self.task_ids, self.question_ids = itertools.count(1), itertools.count(1)
        self.brain = brain if brain is not None else Brain(self)  # reads every message no task's question takes

    def worker_for(self, task, url, asker, notify):
        library = {**self.library, "sites": skills.learned(self.library["sites"], self.memory.learned())}
        return Worker(url, task.goal, title=task.title, asker=asker, notify=notify, memory=self.memory,
                      habit=task.habit, sameness=task.sameness, skill=task.skill, preauth=task.preauth, cap=task.cap,
                      library=library, owner=self.user)

    def hidden(self, call, *args):
        """A model call with the user's IDs redacted: read afresh each time, so a value a task learned
        a moment ago is hidden too."""
        token = SECRETS.set(Secrets(self.memory.hidden_values()))
        try:
            return call(*args)
        finally:
            SECRETS.reset(token)

    # ---- sending

    def several(self):
        with self.lock:  # task threads call this while the webhook thread may be adding a task
            return sum(t.status in ACTIVE for t in self.tasks.values()) > 1

    def send(self, message, task=None, question=None):
        title = task.title if task and self.several() else None
        image_id = self.channel.store(base64.b64decode(message["image"]), "image/png") if message.get("image") else None
        for payload in messages.render(message, question.id if question else None, image_id, title):
            sent = self.channel.send(self.user, payload)
            if question:
                question.sent.append(sent)
        self.log.append({"from": "agent", "text": message["text"]})

    def say(self, text, task=None):
        self.send({"kind": "info", "text": text}, task)

    def reply(self, text, options):
        """The brain's message. Its options become reply buttons (≤ 3) or a list (≤ 10), else numbered lines; a tap comes
        back to the brain as the option's own words, however its button cut them."""
        with self.lock:
            tag = f"b{next(self.offer_ids)}"
            self.offered.update({f"{tag}:{n}": label for n, label in enumerate(options, 1)})
        message = {"kind": "info", "text": text,
                   "options": [{"id": str(n), "label": label} for n, label in enumerate(options, 1)]}
        for payload in messages.render(message, tag=tag):
            self.channel.send(self.user, payload)
        self.log.append({"from": "agent", "text": text})

    def buttons(self, body, options):
        self.channel.send(self.user, {"type": "interactive", "interactive": {"type": "button", "body": {"text": body},
                          "action": {"buttons": [{"type": "reply", "reply": {"id": rid, "title": title[:20]}}
                                                 for rid, title in options]}}})

    def update_typing(self):
        """Typing shows while the brain thinks or a task works, and never while the user is being asked."""
        with self.lock:
            asking = {q.task for q in self.queue}
            working = any(t.status == "running" and t not in asking for t in self.tasks.values())
            self.channel.set_typing(working or (bool(self.brain.busy) and not self.queue))

    # ---- receiving

    def receive_webhook(self, body):
        for event in self.channel.events(body):
            if event["from"] == self.user:  # one user; on WhatsApp, strangers messaging the number are ignored
                self.receive(event)

    def receive(self, event):
        """Code decides where a message goes, in order: a tap; an open question's answer (a stop word cancels the task
        that asked); “log out”; a stop word while a task runs; else the brain, after one notice while a task runs. No
        model sees a captcha's text, a code, or a tap on a task's question."""
        if (event.get("reply_id") or "").startswith("remember:"):
            return self.remember(event["reply_id"].partition(":")[2])  # a model call: outside the lock
        with self.lock:
            question = self.open
            secret = question is not None and question.message["kind"] == "secret"
            text = (event.get("text") or "").strip()
            self.log.append({"from": "user", "text": "••••" if secret and not event.get("reply_id") else text})
            if event.get("reply_id"):
                return self.tapped(event)
            stop = text.lower().strip(" .!") in STOP_WORDS
            if question and stop:
                return self.cancel(question.task)  # even at a hidden prompt or a captcha
            takes_file = question is not None and question.message["kind"] in {"file", "image", "text"}
            if question and (secret or (event.get("context") and event["context"] in question.sent)
                             or (event.get("media_id") and takes_file) or self.numbered(question, text)
                             or captcha_reply(question, text)):
                return self.answer(question, event)  # a secret answer never reaches any model
            if event.get("media_id"):
                return self.say("I wasn't expecting a file just now.")
            if not text:
                return None
            if not secret and LOG_OUT.fullmatch(text):
                return self.log_out()
            if bare_id(text):  # live: an ERN and a UTI typed in the open went to the planner as answers
                self.log[-1]["text"] = "••••"
                return self.say(KEPT_OUT)
            if question:  # only a running task asks: what is typed while its question is open answers it
                return self.answer(question, event)
            running = [t for t in self.tasks.values() if t.status in ACTIVE]
            if running and stop:
                for task in running:
                    self.cancel(task)
                return None
            if running and not any(t.noticed for t in running):
                running[0].noticed = True
                self.say("I'll get to that as soon as this finishes.")
            self.brain.hear(text)

    def log_out(self):
        """“Log out”: this user's kept sign-ins end now, each site's own Logout pressed; a task of theirs still running
        keeps none when it ends. No model reads it."""
        ended = browser.end_sessions(self.user)
        names = [(skills.site_for(f"https://{site}/", self.library["sites"]) or {}).get("name", site) for site in ended]
        return self.say(f"Logged out of {', '.join(names)}. Your next request there starts with a fresh sign-in."
                        if names else "Logged out. Your next request on a site that needs a sign-in starts fresh.")

    def numbered(self, question, text):
        options = question.message.get("options") or []
        return len(options) > messages.ROWS and text.isdigit() and 1 <= int(text) <= len(options)

    def tapped(self, event):
        reply_id = event["reply_id"]
        if reply_id in self.offered:  # a brain reply's option: its own words go back to the brain
            words = self.offered[reply_id]
            self.log[-1]["text"] = words
            return self.brain.hear(words)
        kind, _, rest = reply_id.partition(":")
        if kind in {"rule", "note", "guess"}:  # an offer posted after a task
            return {"rule": self.decide_rule, "note": self.decide_note, "guess": self.decide_guess}[kind](rest)
        question = next((q for q in self.queue if q.sent and reply_id.startswith(f"q{q.id}:")), None)
        if question and messages.reply_for(question.id, question.message.get("options") or [], event) is not None:
            return self.answer(question, event)  # any question on screen, even one a code request moved aside
        return None  # a tap on a question that is already answered

    def answer(self, question, event):
        reply = messages.reply_for(question.id, question.message.get("options") or [], event)
        if reply is None:
            return None
        if "media_id" in reply:
            try:
                path, _mime, _name = self.channel.media(reply.pop("media_id"))
            except KeyError:
                return self.say("That file didn't arrive. Please send it again.")
            reply["file"] = str(path)
        with self.lock:
            question.reply = reply
            if question in self.queue:
                self.queue.remove(question)
            if self.open is question:
                self.open = None
            if question.message.get("remember") and reply.get("option"):
                self.offer_remember(question, reply["option"])
            self.show_next()
        question.answered.set()

    def offer_remember(self, question, option):
        label = next((o["label"] for o in question.message["options"] if o["id"] == option), None)
        if label:
            task = question.task
            tags = [t for t in (task.skill, task.host) if t]
            self.remembering[str(question.id)] = (question.message["text"], label, task.goal, tags)
            self.buttons(f"Remember “{label}” for next time?", [(f"remember:{question.id}", "Remember this")])

    def remember(self, key):
        item = self.remembering.pop(key, None)
        if item:
            question, label, goal, tags = item
            try:
                preference, _ = self.hidden(self.remember_job, question, label)
                self.memory.remember("preference", preference, tags=tags, source=f"{question} → {label}", task=goal,
                                     actor="user")
            except Unstorable:
                return self.say(UNSTORABLE)
            except Exception as error:
                return self.say(f"I couldn't save that: {error}")
            self.say(f"Saved for next time: {preference}")

    def after_task(self, task):
        """After a task ends: learn from it, then offer what it taught (notes: Save or Skip; each guess: Remember or
        No) and any standing approval. A bench run learns only in its learn mode, after grading."""
        if task.script is not None or not task.worker:
            return
        note = skills.site_for(f"https://{task.host}/", self.library["sites"]) if task.host else None
        try:
            if (note or {}).get("learn") != "no":  # a site with a hand-written note (MyUPI) learns nothing more
                self.offer_learned(task)
        except Exception as error:  # a failed reflection never costs the standing-approval offer below
            self.say(f"I couldn't learn from {task.title}: {error}", task)
        try:
            self.offer_rules(task)
        except Exception as error:
            self.say(f"I couldn't offer to remember that order: {error}", task)

    def offer_learned(self, task):
        found = self.hidden(self.learn, task.worker.state, self.memory) or {}
        if found.get("notes"):
            key = f"note:{next(self.offer_ids)}"
            self.offers[key] = (found["host"], [note_id for note_id, _ in found["notes"]])
            self.buttons(f"I learned about {found['host']}:\n" + "\n".join(f"• {line}" for _, line in found["notes"]),
                         [(f"{key}:save", "Save"), (f"{key}:skip", "Skip")])
        for guess in found.get("guesses", []):
            key = f"guess:{next(self.offer_ids)}"
            self.offers[key] = (guess, task.goal)
            self.buttons(f"Should I remember this? {guess['text']}", [(f"{key}:yes", "Remember"), (f"{key}:no", "No")])

    def decide_note(self, rest):
        number, _, choice = rest.partition(":")
        offer = self.offers.pop(f"note:{number}", None)
        if offer is None:
            return None  # decided already
        host, ids = offer
        try:
            self.memory.decide_notes(ids, save=choice == "save", actor="user")
        except Exception as error:
            return self.say(f"I couldn't save that: {error}")
        self.say(f"Saved what I learned about {host}." if choice == "save" else "Skipped: I won't keep those notes.")

    def decide_guess(self, rest):
        number, _, choice = rest.partition(":")
        offer = self.offers.pop(f"guess:{number}", None)
        if offer is None:
            return None  # decided already
        guess, goal = offer
        if choice != "yes":
            return self.say("OK, I won't keep that.")
        try:
            self.memory.remember("preference", guess["text"], tags=guess["tags"], source="learned from a run",
                                 task=goal, actor="user")
        except Unstorable:
            return self.say(UNSTORABLE)
        except Exception as error:
            return self.say(f"I couldn't save that: {error}")
        self.say(f"Saved for next time: {guess['text']}")

    # ---- memory by chat

    def memory_message(self, text):
        """A message about what the agent remembers: remember, forget, list, approve, undo, or none. A model reads it;
        code saves."""
        try:
            listing = self.memory.listing()
            known = {"facts": listing["facts"], "ids": listing["ids"], "rules": listing["rules"],
                     "habits": [{"ref": h["ref"], "site": h["host"], "title": h["title"]} for h in listing["habits"]],
                     "notes": [{"ref": n["ref"], "site": n["host"], "text": n["text"]} for n in listing["notes_list"]]}
            out = self.hidden(self.memory_job, text, known)
        except Exception as error:  # a model or network failure is a chat message
            return self.say(f"I couldn't read that: {error}")
        try:
            reply = self.memory_op(out, text, listing)
        except Unstorable:
            reply = UNSTORABLE
        except (StoreError, ValueError) as error:
            reply = f"I couldn't save that: {error}"
        except Exception as error:  # the receive thread never dies on a memory message
            reply = f"I couldn't do that: {error}"
        self.say(reply)

    def memory_op(self, out, text, listing):
        op = out["op"]
        if op == "list":
            return memory_text(listing)
        if op == "undo":
            try:
                self.memory.undo(actor="user")
            except KeyError:
                return "There's nothing to undo."
            return "Undone the last change to what I remember."
        if op == "forget":
            shown = ("facts", "ids", "rules", "habits", "notes_list")
            refs = {item["ref"] for group in shown for item in listing[group]}
            done = []
            for ref in dict.fromkeys(out["refs"]):
                if ref in refs:  # only what the listing showed: a model's other refs are ignored
                    try:
                        done.append(self.memory.forget(ref, actor="user"))
                    except KeyError:  # it went between the listing and now
                        continue
            return f"Forgot {'; '.join(done)}." if done else \
                "I couldn't find that in what I remember. Ask me what I remember to see the list."
        if op == "approve":
            return self.approve_words(out["site"], text)
        if op == "remember":
            return self.remember_words(out, text)
        return self.memory.hide(out["reply"]) or "I didn't save anything."

    def remember_words(self, out, text):
        """Save what the message gives. An ID's value is the one candidate code finds in the message, never a model's
        words; with two candidates nothing is saved, and with none it is asked through the hidden field later. With no
        ID named, a fact (other than an address) holding something that can be an ID saves nothing: the user is
        asked to name it. The message itself is kept as a fact's source only when nothing in it can be an ID."""
        key, label = out["id"]["key"].strip(), out["id"]["label"].strip()
        if not (storable(text) and storable(key) and storable(label)):  # a password, OTP, PIN, or card stays out of
            return UNSTORABLE  # memory, whatever the model made of the message
        if not key and any(fact["kind"] != "address" and self.unnamed_id(f"{fact['label']} {fact['text']}")
                           for fact in out["facts"]):
            return LOOKS_LIKE_AN_ID
        source = "" if id_candidates(text) else text
        said = []
        if key:
            if id_candidates(key) or id_candidates(label):  # a model must never carry a value in a name
                return "I'll ask for that ID through the hidden field the next time a task needs it."
            label = label or LABELS.get(key) or key.replace("_", " ")
            found = id_candidates(text)
            if len(found) > 1:
                return f"I see more than one number there. Send your {label} on its own, please."
            if not found:
                said.append(f"I'll ask for your {label} through the hidden field the next time a task needs it.")
            else:
                had = any(i["key"] == key for i in self.memory.ids())
                self.memory.set_id(key, label, found[0], actor="user")
                said.append(f"{'Updated' if had else 'Saved'} your {label}. The models never see it. Next time, "
                            f"`jev memory set {key}` keeps an ID out of the chat entirely.")
        for fact in out["facts"]:  # after the ID: its value is hidden in every fact from here on
            label = fact["label"].strip()
            self.memory.remember(fact["kind"], fact["text"], label=label, tags=fact["tags"], source=source,
                                 actor="user", supersede=bool(label))
            shown, shown_label = self.memory.hide(fact["text"]), self.memory.hide(label)
            said.append(f"Saved: {shown_label}: {shown}" if label else f"Saved: {shown}")
        return self.memory.hide(" ".join(said)) or "I didn't find anything to save."

    def unnamed_id(self, text):
        """Whether text holds something that can be an ID once the saved IDs are hidden."""
        return bool(id_candidates(PLACEHOLDER.sub(" ", self.memory.hide(text))))

    def approve_words(self, site, text):
        """A standing approval from the user's own words: the site they name, the cart of their latest order there
        (or on a subdomain of it, where the order ran), and the cap their words set in rupees. The site a model names
        must be one the message names too. Words memory never keeps (a card number, a PIN) save nothing."""
        if not storable(text):
            return UNSTORABLE
        note = skills.named(site, self.library["sites"]) if site.strip() else None
        host = note["host"].partition("/")[0] if note else host_of(site)
        mine = skills.named(text, self.library["sites"])
        named = mine is not None and mine["host"].partition("/")[0] == host
        spelled = re.search(rf"(?<![\w.-])(?:www\.)?{re.escape(host)}(?![\w-])", text.lower())  # not in viola.com
        if "." not in host or not (named or spelled):
            return "Which site is that for? Name it, like “my usual Zepto order”."
        items = self.memory.last_cart(host)
        if not items:
            return "Place this order with me once; then I can remember it."
        limit = cap(text)
        if limit is None:
            return "Tell me the most it may cost, like “up to ₹300”."
        self.memory.approve(host, items, limit, text, actor="user")
        off = "" if os.environ.get("JEV_PREAUTH", "shadow") == "on" else (
            " Pre-approval is off, so until JEV_PREAUTH=on I'll still ask, and tell you this rule would have covered "
            "it.")
        return (f"Saved: {rule_line(self.memory.rules_for(host)[0])}. I'll place this order without asking. Say “stop "
                f"that rule” to end it.{off}")

    def offer_rules(self, task):
        """After an order you approved that the site confirmed, with its cart and rupee total read: offer to place the
        same order next time without asking, up to the total and a fifth. Never on a bench run, and never twice."""
        if task.script is not None or not task.worker:
            return
        for order in task.worker.state.get("orders", []):
            limit = rule_cap(order["total"])
            if order["by"] != "user" or not order["cart"] or limit is None or any(
                    same_cart(rule["cart"], order["cart"]) for rule in self.memory.rules_for(order["site"])):
                continue
            key = f"rule:{next(self.offer_ids)}"
            self.offers[key] = (order["site"], order["cart"], limit)
            off = "" if os.environ.get("JEV_PREAUTH", "shadow") == "on" else (
                "\n(Pre-approval is off, so until JEV_PREAUTH=on I'd still ask, and tell you the rule would have "
                "covered it.)")
            self.buttons(f"Next time, place this same order on {order['site']} without asking, up to "
                         f"{rupees(limit)}?{off}",
                         [(f"{key}:yes", "Don't ask next time"), (f"{key}:no", "Keep asking")])

    def decide_rule(self, rest):
        number, _, choice = rest.partition(":")
        offer = self.offers.pop(f"rule:{number}", None)
        if offer is None:
            return None  # decided already
        if choice != "yes":
            return self.say("OK. I'll keep asking before placing it.")
        site, items, limit = offer
        try:
            self.memory.approve(site, items, limit, "Don't ask next time", actor="user")
        except Exception as error:
            return self.say(f"I couldn't save that: {error}")
        self.say(f"Saved: your standing approval for this order on {site}, up to {rupees(limit)}, for {RULE_DAYS} "
                 "days. Say “stop that rule” to end it.")

    def show_next(self):
        """Open the next question. A code or password request opens at once, moving an ordinary question aside
        (its buttons still work), so a code typed as it arrives never reaches a model call."""
        if self.open is None and self.queue:
            question = min(self.queue, key=lambda q: (q.message["kind"] not in FIRST, q.asked_at, q.id))
        elif self.open is not None and self.open.message["kind"] != "secret":
            waiting = [q for q in self.queue if q.message["kind"] == "secret"]
            if not waiting:
                return
            question = min(waiting, key=lambda q: (q.asked_at, q.id))
        else:
            return
        self.open = question
        self.send(question.message, question.task, question)

    def showing(self):
        """The open question as the chat page needs it: which one, and whether what is typed must be hidden."""
        with self.lock:
            question = self.open
            return {"id": question.id, "secret": question.message["kind"] == "secret"} if question else None

    # ---- tasks

    def new_task(self, goal, script=None, single=False):
        with self.lock:  # the chat, a task's split, and the bench thread all add tasks while others read them
            task = Task(f"t{next(self.task_ids)}", goal, single=single or script is not None)  # bench never splits
            task.script = script
            self.tasks[task.id] = task
            threading.Thread(target=self.run, args=(task,), name=f"jev-{task.id}", daemon=True).start()
        return task

    def execute(self, goal, url=None, skill=None, progress=None):
        """A brain tool's task, run to its end on the calling thread: {status, outcome, report, cards}. Its result, its
        “Stopped …” line, or the error that ended it come back here for the brain to say in its own words; its
        questions, pictures and info lines reach the chat as they happen. With a `url` the brain chose the site: no
        intake, start page or habit, and never pre-authorised."""
        with self.lock:
            task = Task(f"t{next(self.task_ids)}", goal, single=True)
            task.capture, task.url, task.skill = True, url, skill
            self.tasks[task.id] = task
        if progress:  # a status line, as the Rivo app shows its own (“Processing”): never a reply
            self.channel.send(self.user, {"type": "text", "text": {"body": progress}, "x_status": True})
            self.log.append({"from": "agent", "text": progress})
        self.run(task)
        state = task.worker.state if task.worker else {}
        page = state.get("page") or {}
        return {"status": task.status, "outcome": state.get("outcome"), "report": task.result or "",
                "cards": [{"name": c, "details": d} for c, d in zip(page.get("cards") or [], page.get("card_lines") or [])]}

    def notify(self, task, message):
        """A task's message: a brain task's result goes back to the brain; everything else reaches the chat now."""
        if task.capture and message.get("kind") == "result":
            task.result = message["text"]
        else:
            self.send(message, task)

    def ended(self, task, text):
        """How a task ended, when no result said it: back to the brain for a brain task, else in the chat."""
        if task.capture:
            task.result = text
        else:
            self.say(text, task)

    def cancel(self, task):
        task.cancelled = True
        waiting = next((q for q in self.queue if q.task is task), None)
        if waiting:
            waiting.reply = {"cancel": True}
            self.queue.remove(waiting)
            if self.open is waiting:
                self.open = None
                self.show_next()
            waiting.answered.set()
        else:
            self.say(f"Stopping {task.title} after its current step.")
        self.slots.notify_all()

    def shutdown(self, timeout=15.0):
        """Before jev exits: cancel every running task and wait (bounded) for each to close and write its trace."""
        with self.lock:
            running = [t for t in self.tasks.values() if t.status in ACTIVE]
            for task in running:
                self.cancel(task)
        deadline = time.monotonic() + timeout
        while any(t.status in ACTIVE for t in running) and time.monotonic() < deadline:
            time.sleep(0.1)

    def acquire(self, task):
        """Wait for a slot: at most MAX_RUNNING tasks, and never two on the same site."""
        with self.slots:
            told = False
            while not task.cancelled:
                busy = [t for t in self.tasks.values() if t is not task and t.status in {"running", "paused"}]
                clash = next((t for t in busy if t.host == task.host), None)
                if len(busy) < MAX_RUNNING and not clash:
                    task.status = "running"
                    return True
                task.status = "waiting"
                if not told:
                    told = True
                    self.say(f"Waiting for your other {task.host} task to finish first." if clash else
                             f"Queued: {task.title} starts when one of your other tasks finishes.", task)
                self.slots.wait(self.poll)
            return False

    def run(self, task):
        try:
            self.prepare(task)
            if not self.acquire(task):
                return self.ended(task, f"Cancelled {task.title} before it started.")
            task.worker = self.make_worker(task, task.url, lambda m: self.ask(task, m), lambda m: self.notify(task, m))
            task.worker.bench = task.script is not None
            task.worker.interrupted = lambda: task.cancelled
            self.update_typing()
            while task.worker.state["status"] not in STOPPED and not task.cancelled:
                try:
                    task.worker.command("tick")
                    task.problems = 0
                except (ValueError, RuntimeError) as error:
                    if not self.problem(task, error):
                        break
                self.deliver_files(task)
        except Exception as error:  # the start, launching the browser, anything: say it once
            if task.capture:
                task.result = f"I couldn't carry on with {task.title}: {error}"
            else:
                self.say(f"I couldn't carry on with {task.title}: {error}")
            self.record(task, error)
        finally:
            state = task.worker.state["status"] if task.worker else None
            task.status = state if state in STOPPED else ("cancelled" if task.cancelled else "failed")
            if task.worker and state not in STOPPED:
                task.worker.state["status"] = task.status  # so its trace says how it ended
            if task.cancelled and task.worker and state not in STOPPED:  # it stopped between steps: say so
                commit = task.worker.state.get("commit")
                pending = f" {commit['label']} may already have gone through; check it before trying again." \
                    if commit else ""
                self.ended(task, f"Stopped {task.title}.{pending}")
            if task.worker:
                self.payees.update((task.worker.state.get("page") or {}).get("cards") or [])  # its mandates, by name
                try:
                    task.worker.settle()  # downloads that finish as the task ends still reach chat
                    self.deliver_files(task)
                except Exception:
                    pass
                try:
                    task.worker.close()
                except Exception:
                    pass
            with self.slots:
                self.slots.notify_all()
            try:
                self.after_task(task)  # after the slot is free: a reflection call never holds up a queued task
            except Exception as error:
                self.say(f"I couldn't learn from {task.title}: {error}", task)
            self.update_typing()

    def prepare(self, task):
        """Before a task starts: its skill, pre-authorisation, cap, and start page. A message with several errands
        becomes several tasks; this one keeps the first."""
        if task.url:  # a brain tool chose the site
            note = skills.site_for(task.url, self.library["sites"])
            task.host, task.preauth, task.cap = site(task.url), False, cap(task.goal)
            task.title = f"{note['name'] if note else task.host} · {task.goal}"[:60]
            return
        kinds = skills.kinds(self.library["skills"])
        info = self.hidden(self.intake, task.goal, kinds, not task.single)  # the user's own words only
        if info.get("several") == "yes":
            goals = self.hidden(self.split, task.goal)
            if len(goals) > 1:
                more = len(goals) > MAX_ERRANDS
                goals = goals[:MAX_ERRANDS]
                self.say(f"On it: {len(goals)} tasks." +
                         (" I'll start with the first five; ask again for the rest." if more else ""))
                task.goal, task.title = goals[0], goals[0][:60]
                with self.lock:  # other threads iterate self.tasks under the lock
                    for goal in goals[1:]:
                        self.new_task(goal, single=True)
                info = self.hidden(self.intake, task.goal, kinds, False)
        task.skill = info["skill"] if info["skill"] in self.library["skills"] else None
        task.preauth, task.cap = info["preauth"] == "yes", cap(task.goal)
        matched = self.match_habit(task)
        if matched:  # the habit's site and start page; no "which app?" question
            task.habit, task.sameness = matched
            note = skills.site_for(task.habit["start"], self.library["sites"])
            task.url = task.habit["start"]
            task.title = f"{note['name'] if note else task.habit['host']} · {task.goal}"[:60]
        else:
            task.url, task.title = self.start_page(task)
        task.host = site(task.url)

    def match_habit(self, task):
        """(habit, sameness) when Jev is sure this goal repeats a saved habit: the skill's live habits on the site the
        goal names, else on any of the skill's sites; a habit kept under a site's subdomain (book.olacabs.com, where
        the run started) counts for the site. None otherwise, and None when the habit question fails: the task starts
        as if no habit were saved."""
        skill = self.library["skills"].get(task.skill) if task.skill else None
        if not skill:
            return None
        note = skills.named(task.goal, self.library["sites"])
        keys = [note["host"]] if note else skill["sites"]
        hosts = list(dict.fromkeys(note_host(key) for key in keys)) or None
        try:
            habits = self.memory.habits(skill=task.skill, hosts=hosts)
            return replay.match(self.hidden(self.habit_job, task.goal, habits), habits) if habits else None
        except Exception:  # Jev or the store failed: Phase A, without a habit
            return None

    def start_page(self, task):
        """(start URL, title): the site the goal names; else, for cancelling a subscription whose merchant has no note,
        NPCI's UPI mandate portal; else the one the start job finds when the goal names a site without a note; else the
        skill's saved or asked default app."""
        sites = self.library["sites"]
        skill = self.library["skills"].get(task.skill) if task.skill else None
        note = skills.named(task.goal, sites)
        payee = rules.MANDATE_ACTION.search(task.goal) and any(rules.words(p) & rules.words(task.goal)
                                                               for p in self.payees)
        if note is None and MANDATES in sites and (rules.MANDATE_GOAL.search(task.goal) or payee
                                                   or rules.UPI_TOPIC.search(task.goal)):
            note = sites[MANDATES]  # no site of its own: the subscription's UPI AutoPay mandate is managed at NPCI
        if note is None:
            url, title, named = self.hidden(self.start, task.goal)
            if named or not (skill and skill["sites"]):
                return url, title
            chosen = self.default_service(task, skill)
            if chosen in sites:
                note = sites[chosen]
            elif chosen:  # an app the skill doesn't list, in the user's own words
                return self.hidden(self.start, f"{task.goal} (on {chosen})")[:2]
            else:
                return url, title
        return skills.start(note, task.skill), f"{note['name']} · {task.goal}"[:60]

    def default_service(self, task, skill):
        """The app for a skill when the goal names none: the saved default, else asked once and saved. Returns a
        site note's host, the user's own words for an app the skill doesn't list, or None."""
        sites = self.library["sites"]
        notes = {host: sites.get(host) or {"host": host, "name": host, "aliases": []} for host in skill["sites"]}
        names = {host: note["name"] for host, note in notes.items()}

        def resolve(words):  # which of the skill's sites these words name: its name, an alias, or its host
            note = skills.named(words, notes)
            return next((host for host, n in notes.items() if n is note), None) if note else None

        saved = self.memory.default_app(skill["name"])
        if saved and (host := resolve(saved)):
            self.say(f"Using {names[host]} for {skill['name']}, as you chose before.", task)
            return host
        listed = list(names.values())
        options = ", ".join(listed[:-1]) + f" or {listed[-1]}" if len(listed) > 1 else listed[0]
        reply = self.ask(task, {"kind": "text", "text": f"Which app should I use for {skill['name']}: {options}?"})
        text = str(reply.get("text", "")).strip()
        if reply.get("cancel") or not text:
            return None
        if DELEGATE.search(text):
            return next(iter(names))  # left to me: the skill's usual first choice, not saved as a preference
        host = resolve(text)
        if host is None:
            return text  # an app the skill doesn't list: the user's own words, not saved
        if self.memory.default_app(skill["name"]) != names[host]:  # two tasks asked at once save it once
            self.memory.remember("default_app", names[host], label=skill["name"], tags=[skill["name"]], source=text,
                                 task=task.goal, actor="user", supersede=True)
            self.say(f"Saved: I'll use {names[host]} for {skill['name']} from now on.", task)
        return host

    @staticmethod
    def record(task, error):
        """An error that escaped the worker goes into its trace too (redacted, like the rest of it), so the trace says
        why the task stopped."""
        if task.worker:
            state = task.worker.state
            state.setdefault("errors", []).append({"step": len(state.get("history", [])),
                                                   "error": f"{type(error).__name__}: {error}"})

    def problem(self, task, error):
        """Report a model or network error. False when the task must stop."""
        task.problems += 1
        self.say(f"Problem: {error}", task)
        self.record(task, error)
        commit = task.worker.state.get("commit")
        if commit:
            self.say(f"{commit['label']} may already have gone through. Check it before asking me again.", task)
            return False
        if task.problems >= 3:
            self.say("Stopping after 3 problems in a row.", task)
            return False
        return True

    def ask(self, task, message):
        """The agent's asker: queue the question and wait for its answer, pausing and finally cancelling."""
        stop = None
        if task.script is not None and message["kind"] == "secret" and rules.CARD_DETAIL.search(message["text"]):
            stop = f"asked for card details: {message['text']}"  # a bench run never uses a card
        elif task.script is not None and message["text"].startswith(STUCK):
            stop = f"stuck: {message['text']}"  # no scripted default can unstick it: a default answer only circles
        if stop:
            task.bench["stopped"] = stop
            self.say(f"(bench) Stopped: {stop}", task)
            task.cancelled = True
            return {"cancel": True}
        if task.script is not None and message["kind"] not in {"secret", "image", "file"}:
            if task.cancelled:
                return {"cancel": True}
            reply = self.scripted(task, message)
            if reply is not None:
                return reply  # None: the script sends this question to the user's chat, like a code
        question = Question(next(self.question_ids), task, message, self.clock())
        with self.lock:
            if task.cancelled:  # cancelled while this step was deciding: never show its question
                return {"cancel": True}
            self.queue.append(question)
            self.show_next()
            self.update_typing()
        paused = False
        while not question.answered.wait(self.poll):
            waited = self.clock() - question.asked_at
            if task.script is not None and waited >= BENCH_CODE_WAIT:
                task.bench["stopped"] = "needs you: no answer came within 5 minutes"
                with self.lock:
                    if question in self.queue:
                        self.queue.remove(question)
                    if self.open is question:
                        self.open = None
                        self.show_next()
                task.cancelled = True
                return {"cancel": True}
            if waited >= CANCEL_AFTER:
                with self.lock:
                    if question in self.queue:
                        self.queue.remove(question)
                    if self.open is question:
                        self.open = None
                        self.show_next()
                self.say(f"No answer for a day, so I cancelled {task.title}.", task)
                return {"cancel": True}
            if waited >= PAUSE_AFTER and not paused:
                paused, task.status = True, "paused"
                self.say("Paused until you answer.", task)
        task.status = "running"
        self.update_typing()
        return question.reply

    def scripted(self, task, message):
        """A bench task's reply: NO at the final approval (the run ends there), None to send the question to the
        user's chat, else the script's answer."""
        from . import bench

        text = message["text"]
        if message.get("approval"):
            task.bench["gate"] = text
            self.say(f"(bench) Final approval reached; answering no:\n{text}", task)
            task.cancelled = True
            return {"cancel": True}
        answer = bench.reply(task.script, text)
        if answer == bench.FORWARD:
            return None
        task.bench["asked"].append({"question": text, "answer": answer})
        self.say(f"(bench) {text}\n→ {answer}", task)
        return {"text": answer}

    def deliver_files(self, task):
        files = task.worker.state["files"]
        for name in files[task.sent_files:]:
            path = Path(name)
            if path.is_file() and path.stat().st_size <= MAX_DOWNLOAD:
                media_id = self.channel.register(path)
                self.channel.send(self.user, {"type": "document", "document": {"id": media_id, "filename": path.name}})
            else:
                self.say(f"{path.name} is too large to send here; it's saved at {path}.", task)
        task.sent_files = len(files)
