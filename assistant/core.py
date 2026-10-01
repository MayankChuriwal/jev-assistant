"""The conversation: routes every chat message, keeps one question open at a time, and runs each task's worker in
its own thread — at most three at once and one per site."""

import base64
import itertools
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import jev, messages, planner, rules, skills
from . import learn as learning
from .gate import cap, rule_cap, same_cart
from .memory import LABELS, RULE_DAYS, UNSTORABLE, Memory, StoreError, Unstorable, rule_line, rupees
from .privacy import SECRETS, Secrets
from .worker import DELEGATE, STOPPED, Worker

MAX_RUNNING = 3
MAX_ERRANDS = 5  # tasks one message may start
PAUSE_AFTER = 15 * 60
CANCEL_AFTER = 24 * 3600
BENCH_CODE_WAIT = 5 * 60  # a bench run skips a site whose sign-in code doesn't come
MAX_DOWNLOAD = 100 * 1024 * 1024
FIRST = {"secret", "confirm"}  # asked before anything older
STOP_WORDS = {"cancel", "stop", "/cancel"}  # stop a task even at a hidden prompt, without any model
ACTIVE = {"starting", "waiting", "running", "paused"}


def site(url):
    host = urlparse(url).netloc.lower()
    for prefix in ("www.", "m."):
        host = host.removeprefix(prefix)
    return host


ID_CANDIDATES = (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*"),  # an email address or a UPI ID
                 re.compile(r"\+?\d(?:[\s-]?\d){5,}"),  # a number of six digits or more, spaced or not
                 re.compile(r"\b(?=[A-Za-z]*\d)[A-Za-z0-9]{4,}\b"))  # four or more letters and digits, with a digit


def id_candidates(text):
    """The values in a message that can be an ID, each once: emails and UPI IDs, then numbers, then codes."""
    found = []
    for pattern in ID_CANDIDATES:
        found += [match[0].strip() for match in pattern.finditer(text)]
        text = pattern.sub(" ", text)
    return list(dict.fromkeys(found))


def memory_text(listing):
    """What memory holds, for the chat: never an ID's value."""
    lines = [f"• {f['label']}: {f['text']}" if f["label"] else f"• {f['text']}" for f in listing["facts"]]
    lines = (["What I know:"] + lines) if lines else []
    if listing["ids"]:
        lines.append("IDs I keep hidden: " + ", ".join(i["label"] for i in listing["ids"]))
    if listing["rules"]:
        lines += ["Standing approvals:"] + [f"• {r['text']}" for r in listing["rules"]]
    if listing["notes"]:
        lines.append("Learned site notes: " + ", ".join(f"{host} ({n})" for host, n in listing["notes"].items()))
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
        self.status, self.worker, self.updates = "starting", None, []
        self.cancelled, self.sent_files, self.problems = False, 0, 0
        self.skill, self.preauth, self.cap, self.url, self.single = None, False, None, None, single
        self.script, self.bench = None, {"gate": None, "asked": []}  # a bench run: scripted replies

    def view(self, question=None):
        history = self.worker.state["history"] if self.worker else []
        view = {"id": self.id, "title": self.title, "goal": self.goal,
                "step": history[-1]["action"] if history else self.status}
        if question:
            view["open_question"] = question.message["text"]
        return view


class Core:
    def __init__(self, channel, *, user="local", memory=None, make_worker=None, start=None, route=None, intake=None,
                 split=None, chat=None, remember=None, learn=None, memory_job=None, library=None, clock=time.monotonic,
                 poll=1.0):
        self.channel, self.user = channel, user
        self.memory = memory if memory is not None else Memory.open()
        self.make_worker = make_worker or self.worker_for
        self.start = start or planner.start
        self.route_message = route or jev.route
        self.intake = intake or jev.intake
        self.split = split or planner.split
        self.chat = chat or planner.chat
        self.remember_job = remember or planner.remember
        self.learn = learn or learning.after
        self.memory_job = memory_job or planner.memory
        self.library = library if library is not None else {"skills": skills.load(), "sites": skills.load_sites()}
        self.clock, self.poll = clock, poll
        self.lock = threading.RLock()
        self.slots = threading.Condition(self.lock)
        self.tasks, self.queue, self.open, self.log = {}, [], None, []
        self.remembering, self.routes = {}, {}
        self.offers, self.offer_ids = {}, itertools.count(1)  # buttons offered after a task, by key
        self.task_ids, self.question_ids = itertools.count(1), itertools.count(1)

    def worker_for(self, task, url, asker, notify):
        library = {**self.library, "sites": skills.learned(self.library["sites"], self.memory.learned())}
        return Worker(url, task.goal, title=task.title, asker=asker, notify=notify, memory=self.memory,
                      skill=task.skill, preauth=task.preauth, cap=task.cap, library=library)

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

    def buttons(self, body, options):
        self.channel.send(self.user, {"type": "interactive", "interactive": {"type": "button", "body": {"text": body},
                          "action": {"buttons": [{"type": "reply", "reply": {"id": rid, "title": title[:20]}}
                                                 for rid, title in options]}}})

    def update_typing(self):
        with self.lock:
            asking = {q.task for q in self.queue}
            self.channel.set_typing(any(t.status == "running" and t not in asking for t in self.tasks.values()))

    # ---- receiving

    def receive_webhook(self, body):
        for event in self.channel.events(body):
            if event["from"] == self.user:  # one user; on WhatsApp, strangers messaging the number are ignored
                self.receive(event)

    def receive(self, event):
        if (event.get("reply_id") or "").startswith("remember:"):
            return self.remember(event["reply_id"].partition(":")[2])  # a model call: outside the lock
        with self.lock:
            question = self.open
            secret = question is not None and question.message["kind"] == "secret"
            text = (event.get("text") or "").strip()
            self.log.append({"from": "user", "text": "••••" if secret and not event.get("reply_id") else text})
            if event.get("reply_id"):
                return self.tapped(event)
            if secret and text.lower() in STOP_WORDS:
                return self.cancel(question.task)
            takes_file = question is not None and question.message["kind"] in {"file", "image", "text"}
            if question and (secret or (event.get("context") and event["context"] in question.sent)
                             or (event.get("media_id") and takes_file) or self.numbered(question, text)):
                return self.answer(question, event)  # a secret answer never reaches any model
            if event.get("media_id"):
                return self.say("I wasn't expecting a file just now.")
            if not text:
                return None
            views, chat = self.views(), self.log[-7:-1]
        try:
            intent, target, probabilities, certainty = self.hidden(self.route_message, text, views, chat)
        except Exception as error:  # a model or network failure is a chat message, never a failed webhook
            return self.say(f"I couldn't read that message: {error}")
        return self.route(event, text, intent, target, probabilities, certainty, question)

    def numbered(self, question, text):
        options = question.message.get("options") or []
        return len(options) > messages.ROWS and text.isdigit() and 1 <= int(text) <= len(options)

    def views(self):
        return [t.view(self.open if self.open and self.open.task is t else None)
                for t in self.tasks.values() if t.status in ACTIVE]

    def route(self, event, text, intent, target, probabilities, certainty, asked):
        """Act on Jev's reading. `asked` is the question that was open when the message arrived."""
        with self.lock:
            active = [t for t in self.tasks.values() if t.status in ACTIVE]
            task = self.tasks.get(target)
            task = task if task in active else None
            if active and jev.ambiguous(probabilities):
                return self.which(event, text, active, None, asked)
            if intent == "ANSWER" and self.open:
                if self.open is not asked:  # another question opened while Jev read this one's answer
                    return self.say("The question changed while I was reading your message. Please answer again.")
                return self.answer(self.open, event)
            if intent == "NEW_TASK":
                return self.new_task(text)
            if intent in {"CANCEL", "AMEND"}:
                if not active:
                    return self.say("Nothing is running right now.")
                if task is None or jev.ambiguous(certainty):  # never stop or change a task by guess
                    return self.which(event, text, active, intent, asked)
                return self.cancel(task) if intent == "CANCEL" else self.amend(task, text)
            views = self.views()
        if intent == "MEMORY":
            return self.memory_message(text)  # outside the lock: a model call
        try:
            reply, _ = self.hidden(self.chat, text, views)  # STATUS, OTHER, or nothing to act on
        except Exception as error:
            reply = f"I couldn't answer that: {error}"
        self.say(reply)

    def amend(self, task, text):
        task.updates.append(text)
        done = task.worker.state.get("done", []) if task.worker else []  # commits the site confirmed
        if done:
            return self.say(f"{done[-1]} already went through. I'll try to change it on the site; anything "
                            "that costs money again waits for your OK. Say “cancel” to stop instead.", task)
        return self.say(f"Got it. I'll use that for {task.title}.")

    def which(self, event, text, active, intent, asked):
        """Ask which task a message is about. A stop or a change names a running task; anything else may be new."""
        self.routes[event["id"]] = (text, intent, asked)
        options = [(f"route:{t.id}:{event['id']}", t.title) for t in active[:3 if intent else 2]]
        if intent is None:
            return self.buttons("Is this about one of your tasks, or something new?",
                                options + [(f"route:new:{event['id']}", "New task")])
        self.buttons("Which task should I stop?" if intent == "CANCEL" else "Which task is this change for?", options)

    def tapped(self, event):
        kind, _, rest = event["reply_id"].partition(":")
        if kind == "route":
            target, _, key = rest.partition(":")
            routed = self.routes.pop(key, None)
            if routed is None:
                return None
            text, intent, asked = routed
            if target == "new":
                return self.new_task(text)
            task = self.tasks.get(target)
            if not task or task.status not in ACTIVE:
                return self.say(f"{task.title if task else 'That task'} already finished.")
            if intent == "CANCEL":
                return self.cancel(task)
            if intent is None and asked is not None and self.open is asked and asked.task is task:
                return self.answer(asked, {"type": "text", "text": text})  # still the question it was sent to
            return self.amend(task, text)
        if kind in {"rule", "note", "guess"}:  # an offer posted after a task
            return {"rule": self.decide_rule, "note": self.decide_note, "guess": self.decide_guess}[kind](rest)
        question = next((q for q in self.queue if q.sent and event["reply_id"].startswith(f"q{q.id}:")), None)
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
        try:
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
        listing = self.memory.listing()
        known = {"facts": listing["facts"], "ids": listing["ids"], "rules": listing["rules"],
                 "habits": [{"ref": h["ref"], "site": h["host"], "title": h["title"]} for h in listing["habits"]]}
        try:
            out = self.hidden(self.memory_job, text, known)
        except Exception as error:  # a model or network failure is a chat message
            return self.say(f"I couldn't read that: {error}")
        try:
            reply = self.memory_op(out, text, listing)
        except Unstorable:
            reply = UNSTORABLE
        except (StoreError, ValueError) as error:
            reply = f"I couldn't save that: {error}"
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
            refs = {item["ref"] for group in ("facts", "ids", "rules", "habits") for item in listing[group]}
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
        return out["reply"].strip() or "I didn't save anything."

    def remember_words(self, out, text):
        """Save what the message gives. An ID's value is the one candidate code finds in the message, never a model's
        words; with two candidates nothing is saved, and with none it is asked through the hidden field later."""
        said = []
        key = out["id"]["key"].strip()
        if key:
            label = out["id"]["label"].strip() or LABELS.get(key) or key.replace("_", " ")
            found = id_candidates(text)
            if len(found) > 1:
                return f"I see more than one number there. Send your {label} on its own, please."
            if not found:
                said.append(f"I'll ask for your {label} through the hidden field the next time a task needs it.")
            else:
                if found[0] in label:
                    label = LABELS.get(key) or key.replace("_", " ")
                self.memory.set_id(key, label, found[0], actor="user")
                said.append(f"Saved your {label}. The models never see it. Next time, `jev memory set {key}` keeps an "
                            "ID out of the chat entirely.")
        for fact in out["facts"]:  # after the ID: its value is hidden in every fact from here on
            label = fact["label"].strip()
            self.memory.remember(fact["kind"], fact["text"], label=label, tags=fact["tags"], source=text,
                                 actor="user", supersede=bool(label))
            said.append(f"Saved: {label}: {fact['text'].strip()}" if label else f"Saved: {fact['text'].strip()}")
        return " ".join(said) or "I didn't find anything to save."

    def approve_words(self, site, text):
        """A standing approval from the user's own words: the site they name, the cart of their latest order there,
        and the cap their words set in rupees."""
        note = skills.named(site, self.library["sites"]) if site.strip() else None
        host = note["host"] if note else skills.bare(site.strip().lower())
        if "." not in host:
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
                return self.say(f"Cancelled {task.title} before it started.")
            task.worker = self.make_worker(task, task.url, lambda m: self.ask(task, m), lambda m: self.send(m, task))
            task.worker.bench = task.script is not None
            task.worker.interrupted = lambda: task.cancelled or bool(task.updates)
            self.update_typing()
            while task.worker.state["status"] not in STOPPED and not task.cancelled:
                self.apply_updates(task)
                try:
                    task.worker.command("tick")
                    task.problems = 0
                except (ValueError, RuntimeError) as error:
                    if not self.problem(task, error):
                        break
                self.deliver_files(task)
        except Exception as error:  # the start, launching the browser, anything: say it once
            self.say(f"I couldn't carry on with {task.title}: {error}")
        finally:
            state = task.worker.state["status"] if task.worker else None
            task.status = state if state in STOPPED else ("cancelled" if task.cancelled else "failed")
            if task.worker and state not in STOPPED:
                task.worker.state["status"] = task.status  # so its trace says how it ended
            if task.cancelled and task.worker and state not in STOPPED:  # it stopped between steps: say so
                commit = task.worker.state.get("commit")
                pending = f" {commit['label']} may already have gone through; check it before trying again." \
                    if commit else ""
                self.say(f"Stopped {task.title}.{pending}", task)
            if task.worker:
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
        task.url, task.title = self.start_page(task)
        task.host = site(task.url)

    def start_page(self, task):
        """(start URL, title): the site the goal names; else the one the start job finds when the goal names a site
        without a note; else the skill's saved or asked default app."""
        sites = self.library["sites"]
        skill = self.library["skills"].get(task.skill) if task.skill else None
        note = skills.named(task.goal, sites)
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

    def apply_updates(self, task):
        while task.updates:
            task.worker.update(task.updates.pop(0))  # the planner hears it next; any approval is void

    def problem(self, task, error):
        """Report a model or network error. False when the task must stop."""
        task.problems += 1
        self.say(f"Problem: {error}", task)
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
        if task.script is not None and message["kind"] == "secret" and rules.CARD_DETAIL.search(message["text"]):
            task.bench["stopped"] = f"asked for card details: {message['text']}"  # a bench run never uses a card
            self.say(f"(bench) Stopped: {task.bench['stopped']}", task)
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
