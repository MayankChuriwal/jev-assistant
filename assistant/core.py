"""The conversation: routes every chat message, keeps one question open at a time, and runs each task's agent in
its own thread — at most three at once and one per site."""

import base64
import itertools
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import jobs, messages, model
from .agent import STOPPED, Agent
from .profile import SECRETS, Profile, Secrets

MAX_RUNNING = 3
PAUSE_AFTER = 15 * 60
CANCEL_AFTER = 24 * 3600
MAX_DOWNLOAD = 100 * 1024 * 1024
FIRST = {"secret", "confirm"}  # asked before anything older
ACTIVE = {"starting", "waiting", "running", "paused"}


def site(url):
    host = urlparse(url).netloc.lower()
    for prefix in ("www.", "m."):
        host = host.removeprefix(prefix)
    return host


class Question:
    def __init__(self, qid, task, message, asked_at):
        self.id, self.task, self.message, self.asked_at = qid, task, message, asked_at
        self.reply, self.sent, self.answered = None, [], threading.Event()


class Task:
    def __init__(self, tid, goal):
        self.id, self.goal, self.title, self.host = tid, goal, goal[:60], None
        self.status, self.agent, self.updates = "starting", None, []
        self.cancelled, self.sent_files, self.problems = False, 0, 0

    def view(self, question=None):
        history = self.agent.state["history"] if self.agent else []
        view = {"id": self.id, "title": self.title, "goal": self.goal,
                "step": history[-1]["action"] if history else self.status}
        if question:
            view["open_question"] = question.message["text"]
        return view


class Core:
    def __init__(self, channel, *, user="local", profile=None, make_agent=None, start=None, classify=None,
                 chat=None, remember=None, clock=time.monotonic, poll=1.0):
        self.channel, self.user = channel, user
        self.profile = profile or Profile()
        self.make_agent = make_agent or self.agent_for
        self.start = start or jobs.start
        self.classify = classify or model.classify
        self.chat = chat or jobs.chat
        self.remember_job = remember or jobs.remember
        self.clock, self.poll = clock, poll
        self.lock = threading.RLock()
        self.slots = threading.Condition(self.lock)
        self.tasks, self.queue, self.open, self.log = {}, [], None, []
        self.remembering, self.routes = {}, {}
        self.task_ids, self.question_ids = itertools.count(1), itertools.count(1)
        self.secrets = Secrets(self.profile.hidden_values())  # classification never sees the profile's values

    def agent_for(self, url, goal, title, asker, notify):
        return Agent(url, goal, title=title, asker=asker, notify=notify, profile=self.profile)

    def hidden(self, call, *args):
        """A model call with the user's profile values redacted."""
        token = SECRETS.set(self.secrets)
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
            intent, target, probabilities = self.hidden(self.classify, text, views, chat)
        except Exception as error:  # a model or network failure is a chat message, never a failed webhook
            return self.say(f"I couldn't read that message: {error}")
        return self.route(event, text, intent, target, probabilities)

    def numbered(self, question, text):
        options = question.message.get("options") or []
        return len(options) > messages.ROWS and text.isdigit() and 1 <= int(text) <= len(options)

    def views(self):
        return [t.view(self.open if self.open and self.open.task is t else None)
                for t in self.tasks.values() if t.status in ACTIVE]

    def route(self, event, text, intent, target, probabilities):
        with self.lock:
            active = [t for t in self.tasks.values() if t.status in ACTIVE]
            task = self.tasks.get(target)
            if task not in active:
                task = active[0] if len(active) == 1 else None
            if active and model.ambiguous(probabilities):
                return self.which(event, text, active)
            if intent == "ANSWER" and self.open:
                return self.answer(self.open, event)
            if intent == "NEW_TASK":
                return self.new_task(text)
            if intent == "CANCEL" and task:
                return self.cancel(task)
            if intent == "AMEND" and task:
                task.updates.append(text)
                done = [m.removeprefix("Done: ").rpartition(" (")[0] for m in task.agent.state["memory"]
                        if m.startswith("Done: ")] if task.agent else []  # the agent's record of a finished commit
                if done:
                    return self.say(f"{done[-1]} already went through. I'll try to change it on the site; anything "
                                    "that costs money again waits for your OK. Say “cancel” to stop instead.", task)
                return self.say(f"Got it. I'll use that for {task.title}.")
            views = self.views()
        try:
            reply, _ = self.hidden(self.chat, text, views)  # STATUS, OTHER, or nothing to act on
        except Exception as error:
            reply = f"I couldn't answer that: {error}"
        self.say(reply)

    def which(self, event, text, active):
        self.routes[event["id"]] = text
        options = [(f"route:{t.id}:{event['id']}", t.title) for t in active[:2]]
        self.buttons("Is this about one of your tasks, or something new?",
                     options + [(f"route:new:{event['id']}", "New task")])

    def tapped(self, event):
        kind, _, rest = event["reply_id"].partition(":")
        if kind == "route":
            target, _, key = rest.partition(":")
            text = self.routes.pop(key, None)
            task = self.tasks.get(target)
            if text is None:
                return None
            if target == "new" or not task or task.status not in ACTIVE:
                return self.new_task(text)
            if self.open and self.open.task is task:
                return self.answer(self.open, {"type": "text", "text": text})
            task.updates.append(text)
            return self.say(f"Got it. I'll use that for {task.title}.")
        question = self.open
        if question and messages.reply_for(question.id, question.message.get("options") or [], event) is not None:
            return self.answer(question, event)
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
            self.remembering[str(question.id)] = (question.message["text"], label, question.task.goal)
            self.buttons(f"Remember “{label}” for next time?", [(f"remember:{question.id}", "Remember this")])

    def remember(self, key):
        item = self.remembering.pop(key, None)
        if item:
            question, label, goal = item
            try:
                preference, _ = self.hidden(self.remember_job, question, label)
            except Exception as error:
                return self.say(f"I couldn't save that: {error}")
            self.profile.remember(preference, goal)
            self.say(f"Saved for next time: {preference}")

    def show_next(self):
        if self.open or not self.queue:
            return
        question = min(self.queue, key=lambda q: (q.message["kind"] not in FIRST, q.asked_at, q.id))
        self.open = question
        self.send(question.message, question.task, question)

    # ---- tasks

    def new_task(self, goal):
        task = Task(f"t{next(self.task_ids)}", goal)
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
            url, task.title, _ = self.hidden(self.start, task.goal)
            task.host = site(url)
            if not self.acquire(task):
                return self.say(f"Cancelled {task.title} before it started.")
            task.agent = self.make_agent(url, task.goal, task.title, lambda m: self.ask(task, m),
                                         lambda m: self.send(m, task))
            self.update_typing()
            while task.agent.state["status"] not in STOPPED and not task.cancelled:
                self.apply_updates(task)
                try:
                    task.agent.command("tick")
                    task.problems = 0
                except (ValueError, RuntimeError) as error:
                    if not self.problem(task, error):
                        break
                self.deliver_files(task)
        except Exception as error:  # the start job, launching the browser, anything: say it once
            self.say(f"I couldn't carry on with {task.title}: {error}")
        finally:
            state = task.agent.state["status"] if task.agent else None
            task.status = state if state in STOPPED else ("cancelled" if task.cancelled else "failed")
            if task.agent:
                try:
                    task.agent.close()
                except Exception:
                    pass
            with self.slots:
                self.slots.notify_all()
            self.update_typing()

    def apply_updates(self, task):
        while task.updates:
            task.agent.state["memory"].append(f"User update: {task.updates.pop(0)}")
            task.agent.state["approval"] = None  # an amendment voids any approval given before it

    def problem(self, task, error):
        """Report a model or network error. False when the task must stop."""
        task.problems += 1
        self.say(f"Problem: {error}", task)
        commit = task.agent.state.get("commit")
        if commit:
            self.say(f"{commit['label']} may already have gone through. Check it before asking me again.", task)
            return False
        if task.problems >= 3:
            self.say("Stopping after 3 problems in a row.", task)
            return False
        return True

    def ask(self, task, message):
        """The agent's asker: queue the question and wait for its answer, pausing and finally cancelling."""
        question = Question(next(self.question_ids), task, message, self.clock())
        with self.lock:
            self.queue.append(question)
            self.show_next()
            self.update_typing()
        paused = False
        while not question.answered.wait(self.poll):
            waited = self.clock() - question.asked_at
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

    def deliver_files(self, task):
        files = task.agent.state["files"]
        for name in files[task.sent_files:]:
            path = Path(name)
            if path.is_file() and path.stat().st_size <= MAX_DOWNLOAD:
                media_id = self.channel.register(path)
                self.channel.send(self.user, {"type": "document", "document": {"id": media_id, "filename": path.name}})
            else:
                self.say(f"{path.name} is too large to send here; it's saved at {path}.", task)
        task.sent_files = len(files)
