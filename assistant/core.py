"""The conversation: routes every chat message, keeps one question open at a time, and runs each task's agent in
its own thread — at most three at once and one per site."""

import base64
import itertools
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import jobs, messages, model, rules
from .agent import STOPPED
from .driver import agent_class
from .profile import SECRETS, Profile, Secrets

MAX_RUNNING = 3
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


class Question:
    def __init__(self, qid, task, message, asked_at):
        self.id, self.task, self.message, self.asked_at = qid, task, message, asked_at
        self.reply, self.sent, self.answered = None, [], threading.Event()


class Task:
    def __init__(self, tid, goal):
        self.id, self.goal, self.title, self.host = tid, goal, goal[:60], None
        self.status, self.agent, self.updates = "starting", None, []
        self.cancelled, self.sent_files, self.problems = False, 0, 0
        self.script, self.bench = None, {"gate": None, "asked": []}  # a bench run: scripted replies

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
        return agent_class()(url, goal, title=title, asker=asker, notify=notify, profile=self.profile)

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
            intent, target, probabilities, certainty = self.hidden(self.classify, text, views, chat)
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
            if active and model.ambiguous(probabilities):
                return self.which(event, text, active, None, asked)
            if intent == "ANSWER" and self.open:
                if self.open is not asked:  # another question opened while Jev read this one's answer
                    return self.say("The question changed while I was reading your message. Please answer again.")
                return self.answer(self.open, event)
            if intent == "NEW_TASK":
                return self.switch(event, text, active) if active else self.new_task(text)
            if intent in {"CANCEL", "AMEND"}:
                if not active:
                    return self.say("Nothing is running right now.")
                if task is None or model.ambiguous(certainty):  # never stop or change a task by guess
                    return self.which(event, text, active, intent, asked)
                return self.cancel(task) if intent == "CANCEL" else self.amend(task, text)
            views = self.views()
        try:
            reply, _ = self.hidden(self.chat, text, views)  # STATUS, OTHER, or nothing to act on
        except Exception as error:
            reply = f"I couldn't answer that: {error}"
        self.say(reply)

    def amend(self, task, text):
        task.updates.append(text)
        done = [m.removeprefix("Done: ").rpartition(" (")[0] for m in task.agent.state["memory"]
                if m.startswith("Done: ")] if task.agent else []  # the agent's record of a finished commit
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

    def switch(self, event, text, active):
        """One task at a time: a new request while one runs asks whether to drop the running one."""
        self.routes[event["id"]] = (text, "SWITCH", None)
        titles = " and ".join(f"“{t.title}”" for t in active)
        self.buttons(f"You're in the middle of {titles}. Drop it and start “{text[:60]}” instead?",
                     [(f"switch:yes:{event['id']}", "Yes, start the new one"),
                      (f"switch:no:{event['id']}", f"No, keep {active[0].title}"[:20])])

    def tapped(self, event):
        kind, _, rest = event["reply_id"].partition(":")
        if kind == "switch":
            answer, _, key = rest.partition(":")
            routed = self.routes.pop(key, None)
            if routed is None:
                return None
            active = [t for t in self.tasks.values() if t.status in ACTIVE]
            if answer == "no":
                return self.say(f"Okay, I'll keep going with {', '.join(t.title for t in active) or 'what I was doing'}"
                                " and won't start the new request.")
            for task in active:
                self.cancel(task)
            return self.new_task(routed[0])
        if kind == "route":
            target, _, key = rest.partition(":")
            routed = self.routes.pop(key, None)
            if routed is None:
                return None
            text, intent, asked = routed
            if target == "new":
                active = [t for t in self.tasks.values() if t.status in ACTIVE]
                return self.switch(event | {"id": key}, text, active) if active else self.new_task(text)
            task = self.tasks.get(target)
            if not task or task.status not in ACTIVE:
                return self.say(f"{task.title if task else 'That task'} already finished.")
            if intent == "CANCEL":
                return self.cancel(task)
            if intent is None and asked is not None and self.open is asked and asked.task is task:
                return self.answer(asked, {"type": "text", "text": text})  # still the question it was sent to
            return self.amend(task, text)
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
        """Open the next question. A code or password request opens at once, moving an ordinary question aside
        (its buttons still work), so a code typed as it arrives never goes to a model."""
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

    def new_task(self, goal, script=None):
        task = Task(f"t{next(self.task_ids)}", goal)
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
            url, task.title, info = self.hidden(self.start, task.goal)
            service = self.default_service(task, info or {})
            if service:
                url, task.title, _ = self.hidden(self.start, f"{task.goal} (on {service})")
            task.host = site(url)
            if not self.acquire(task):
                return self.say(f"Cancelled {task.title} before it started.")
            task.agent = self.make_agent(url, task.goal, task.title, lambda m: self.ask(task, m),
                                         lambda m: self.send(m, task))
            task.agent.interrupted = lambda: task.cancelled or bool(task.updates)
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
            if task.agent and state not in STOPPED:
                task.agent.state["status"] = task.status  # so its trace says how it ended (P4-02)
            if task.cancelled and task.agent and state not in STOPPED:  # it stopped between steps: say so
                commit = task.agent.state.get("commit")
                pending = f" {commit['label']} may already have gone through; check it before trying again." \
                    if commit else ""
                self.say(f"Stopped {task.title}.{pending}", task)
            if task.agent:
                try:
                    task.agent.settle()  # downloads that finish as the task ends still reach chat
                    self.deliver_files(task)
                except Exception:
                    pass
                try:
                    task.agent.close()
                except Exception:
                    pass
            with self.slots:
                self.slots.notify_all()
            self.update_typing()

    def default_service(self, task, info):
        """The app for a goal that names none: the saved default for its kind, or ask once and save it."""
        kind, services = (info.get("kind") or "").strip(), info.get("services") or []
        if info.get("service_named", True) or not kind or not services:
            return None
        prefix = f"default app for {kind.lower()}:"
        saved = next((p["text"] for p in self.profile.data["preferences"] if p["text"].lower().startswith(prefix)),
                     None)
        if saved:
            service = saved.split(":", 1)[1].strip()
            self.say(f"Using {service} for {kind}, as you chose before.", task)
            return service
        listed = ", ".join(services[:-1]) + f" or {services[-1]}" if len(services) > 1 else services[0]
        reply = self.ask(task, {"kind": "text", "text": f"Which app should I use for {kind}: {listed}?"})
        text = str(reply.get("text", "")).strip()
        if reply.get("cancel") or not text:
            return None
        service = next((s for s in services if s.lower() in text.lower() or text.lower() in s.lower()), text)
        self.profile.remember(f"Default app for {kind}: {service}", task.goal)
        self.say(f"Saved: I'll use {service} for {kind} from now on.", task)
        return service

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
        if task.script is not None and message["kind"] == "secret" and rules.CARD_DETAIL.search(message["text"]):
            task.bench["stopped"] = f"asked for card details: {message['text']}"  # a bench run never uses a card
            self.say(f"(bench) Stopped: {task.bench['stopped']}", task)
            task.cancelled = True
            return {"cancel": True}
        if task.script is not None and message["kind"] not in {"secret", "image", "file"}:
            return {"cancel": True} if task.cancelled else self.scripted(task, message)  # codes still go to chat
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
                task.bench["stopped"] = "needs sign-in: no code came within 5 minutes"
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
        """A bench task's reply: NO at the final approval (the run ends there), else the script's answer."""
        from . import bench

        text = message["text"]
        if message.get("approval"):
            task.bench["gate"] = text
            self.say(f"(bench) Final approval reached; answering no:\n{text}", task)
            task.cancelled = True
            return {"cancel": True}
        answer = bench.reply(task.script, text)
        task.bench["asked"].append({"question": text, "answer": answer})
        self.say(f"(bench) {text}\n→ {answer}", task)
        return {"text": answer}

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
