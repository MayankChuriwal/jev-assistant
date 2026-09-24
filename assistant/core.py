"""The conversation: routes every chat message, keeps one question open at a time, and runs each task's agent in
its own thread — at most three at once and one per site."""

import base64
import itertools
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import commit, coordinator, interaction, jobs, messages, model, rules
from .agent import STOPPED, Agent
from .browser import ReservationBlocked
from .profile import SECRETS, Profile, Secrets, Vault
from .state import TaskState, observe_state, put_fact

MAX_RUNNING = 3
PAUSE_AFTER = 15 * 60
CANCEL_AFTER = 24 * 3600
MAX_DOWNLOAD = 100 * 1024 * 1024
FIRST = {"secret", "confirm"}  # asked before anything older
STOP_WORDS = {"cancel", "stop", "/cancel"}  # stop a task even at a hidden prompt, without any model
ACTIVE = {"starting", "waiting", "running", "paused", "blocked", "waiting_user", "waiting_site"}


def site(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().encode("idna").decode("ascii")
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port
    if port is not None and (parsed.scheme, port) not in {("http", 80), ("https", 443)}:
        host += f":{port}"
    for prefix in ("www.", "m."):
        host = host.removeprefix(prefix)
    return host


class Question:
    def __init__(self, qid, task, message, asked_at):
        self.id, self.task, self.message, self.asked_at = qid, task, message, asked_at
        self.reply, self.sent, self.answered = None, [], threading.Event()
        self.displayed_at = None


class Task:
    def __init__(self, tid, goal):
        self.id, self.goal, self.title, self.host = tid, goal, goal[:60], None
        self.status, self.agent, self.updates = "starting", None, []
        self.cancelled, self.sent_files, self.problems = False, 0, 0
        self.flow = TaskState(tid, goal)
        self.last_activity = 0.0
        self.reservations, self.file_ids = set(), {}
        self.worker = None
        self.recovery_rows = []

    def view(self, question=None):
        history = self.agent.state["history"] if self.agent else []
        view = {"id": self.id, "title": self.title, "goal": self.goal,
                "step": history[-1]["action"] if history else self.status}
        if question:
            view["open_question"] = question.message["text"]
        return view


class Core:
    def __init__(self, channel, *, user="local", profile=None, make_agent=None, start=None, classify=None,
                 chat=None, remember=None, clock=time.monotonic, poll=1.0,
                 assess_start=None, start_intent=None, answer_facts=None, journal_dir=None):
        self.channel, self.user = channel, user
        self.profile = profile or Profile()
        self.make_agent = make_agent or self.agent_for
        self.start = start or jobs.start
        self.assess_start = assess_start or coordinator.assess
        self.start_intent = start_intent or jobs.start_intent
        self.answer_facts = answer_facts or jobs.answer_facts
        self.journal_dir = Path(journal_dir) if journal_dir else Path.home() / '.jev' / 'journals'
        self.reservations, self.interrupted_runs = {}, {}
        self.classify = classify or coordinator.interpret_message
        self.chat = chat or jobs.chat
        self.remember_job = remember or jobs.remember
        self.clock, self.poll = clock, poll
        self.lock = threading.RLock()
        self.reservation_lock = threading.RLock()
        self.inbound = threading.Lock()
        self.seen_events = set()
        self.slots = threading.Condition(self.lock)
        self.tasks, self.queue, self.open, self.log = {}, [], None, []
        self.remembering, self.routes = {}, {}
        self.task_ids, self.question_ids = itertools.count(1), itertools.count(1)
        self.secrets = Secrets(self.profile.hidden_values())  # classification never sees the profile's values

    def agent_for(self, url, goal, title, asker, notify, *, task):
        agent = Agent(url, goal, title=title, asker=asker, notify=notify, profile=self.profile, task=task.flow,
                      reserve_origin=lambda origin: self.reserve_access(task, origin))
        agent.resolve_upload = lambda media_id: str(self.channel.media(media_id)[0])
        return agent

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
        message = self.secrets.redact(message)
        if task and task.agent and hasattr(task.agent, 'secrets'):
            message = task.agent.secrets.redact(message)
        title = task.title if task and self.several() else None
        image_id = self.channel.store(base64.b64decode(message["image"]), "image/png") if message.get("image") else None
        for payload in messages.render(message, question.id if question else None, image_id, title):
            sent = self.channel.send(self.user, payload)
            if question:
                question.sent.append(sent)
        self.log.append({"from": "agent", "text": message["text"]})

    def say(self, text, task=None):
        self.send({"kind": "info", "text": text}, task)

    def buttons(self, body, options, question=None):
        sent = self.channel.send(self.user, {"x_question": str(question.id) if question else None,
                          "type": "interactive", "interactive": {"type": "button", "body": {"text": body},
                          "action": {"buttons": [{"type": "reply", "reply": {"id": rid, "title": title[:20]}}
                                                 for rid, title in options]}}})
        if question:
            question.sent.append(sent)
            question.displayed_at = self.clock()

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
        # One accepted event retains the ingress lock through classification and routing.
        # Scheduling and the agent's question delivery use a different lock.
        with self.inbound:
            key = event.get("id")
            if not isinstance(key, str) or not key or key in self.seen_events:
                return None
            self.seen_events.add(key)
            return self._receive(event)

    def _receive(self, event):
        qid, secret_reply = event.get("question_id"), event.get("secret_reply", False)
        if qid is not None and not isinstance(qid, str) or type(secret_reply) is not bool:
            return self.say("That reply has invalid question information. Please answer the current question.")
        with self.lock:
            current = self.open
            if ((qid is not None and (current is None or qid != str(current.id)))
                    or secret_reply and (current is None or qid != str(current.id)
                                         or current.message["kind"] != "secret")):
                return self.say("That question has expired; please answer the current question.")
            # Unbound text at a secret prompt is never classified or recorded.
            if current and current.message["kind"] == "secret" and not event.get("reply_id"):
                if (event.get("text") or "").strip().lower() in STOP_WORDS:
                    return self.cancel(current.task)
                if qid != str(current.id) or not secret_reply:
                    return self.say("Please submit the hidden answer with the current question.")
                # Bind, redact and route without releasing the scheduling lock. A timeout
                # cannot turn this protected event into an ordinary message between checks.
                self.log.append({"from": "user", "text": "••••"})
                return self.answer(current, event)
        if not event.get("reply_id") and not secret_reply:
            text = (event.get("text") or "").strip()
            if text == "/cleanup-legacy-cards" or text.startswith("/default "):
                return self.local_choice(text)
        with self.lock:
            if self.open is not current:
                return self.say("That question has expired; please answer the current question.")
            question = current
            secret = question is not None and question.message["kind"] == "secret"
            text = (event.get("text") or "").strip()
            self.log.append({"from": "user", "text": "" if event.get("reply_id") else ("••••" if secret else text)})
            if event.get("reply_id"):
                return self.tapped(event)
            if secret and text.lower() in STOP_WORDS:
                return self.cancel(question.task)
            takes_file = question is not None and question.message["kind"] in {"file", "image", "text"}
            if question and (secret or (event.get("media_id") and takes_file)
                             or self.numbered(question, text)):
                return self.answer(question, event)  # a secret answer never reaches any model
            if event.get("media_id"):
                return self.say("I wasn't expecting a file just now.")
            if not text:
                return None
            views, chat = self.views(), self.log[-7:-1]
        try:
            result = self.hidden(self.classify, text, views, chat)
            intent, target = result["intent"], result["task_id"]
            probabilities = {"unclear": 0.5, "other": 0.5} if result["ambiguous"] else {intent: 1.0}
            certainty = {target: 1.0}
        except Exception as error:  # a model or network failure is a chat message, never a failed webhook
            return self.say(f"I couldn't read that message: {error}")
        if intent in {"OTHER", "STATUS"} and not result["ambiguous"] and result["reply"]:
            return self.say(result["reply"])
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
                return self.which(event, text, active,
                                  intent if intent in {"CANCEL", "AMEND", "RESUME"} else None, asked)
            if intent == "ANSWER" and self.open:
                if self.open is not asked or (task is not None and self.open.task is not task):
                    return self.say("The question changed while I was reading your message. Please answer again.")
                return self.answer(self.open, event)
            if intent == "NEW_TASK":
                return self.new_task(text)
            if intent in {"CANCEL", "AMEND", "RESUME"}:
                if not active:
                    return self.say("Nothing is running right now.")
                if task is None or model.ambiguous(certainty):  # never stop or change a task by guess
                    return self.which(event, text, active, intent, asked)
                if intent == "RESUME":
                    return self.resume(task.id)
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
        question = self.open
        if question is None:
            question = Question(next(self.question_ids), active[0],
                                {"kind": "text", "text": "Which task is this about?"}, self.clock())
            question.routing_only = True
            self.queue.append(question)
            self.open = question
        question.routing = event["id"]
        self.routes[event["id"]] = (text, intent, asked, question)
        options = [(f"route:{t.id}:{event['id']}", t.title) for t in active[:3 if intent else 2]]
        if intent is None:
            return self.buttons("Is this about one of your tasks, or something new?",
                                options + [(f"route:new:{event['id']}", "New task")], question)
        self.buttons("Which task should I stop?" if intent == "CANCEL" else "Which task is this for?",
                     options, question)

    def tapped(self, event):
        kind, _, rest = event["reply_id"].partition(":")
        if kind == "route":
            target, _, key = rest.partition(":")
            routed = self.routes.pop(key, None)
            if routed is None:
                return None
            text, intent, asked, clarification = routed
            if (self.open is not clarification or clarification.answered.is_set()
                    or getattr(clarification, "routing", None) != key):
                return self.say("That question has expired; please answer the current question.")
            clarification.routing = None
            if getattr(clarification, "routing_only", False):
                self.queue.remove(clarification)
                self.open = None
                clarification.answered.set()
                self.show_next()
            elif target != clarification.task.id or intent in {"AMEND", "CANCEL", "RESUME"}:
                self.send(clarification.message, clarification.task, clarification)
            if target == "new":
                return self.new_task(text)
            task = self.tasks.get(target)
            if not task or task.status not in ACTIVE:
                return self.say(f"{task.title if task else 'That task'} already finished.")
            if intent == "CANCEL":
                return self.cancel(task)
            if intent == "RESUME":
                return self.resume(task.id)
            if intent is None and asked is not None and self.open is asked and asked.task is task:
                return self.answer(asked, {"type": "text", "text": text})  # still the question it was sent to
            return self.amend(task, text)
        question = next((q for q in self.queue if q is self.open and q.sent
                         and event["reply_id"].startswith(f"q{q.id}:")), None)
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
            reply["upload_id"] = event["media_id"]
        with self.lock:
            question.task.last_activity = self.clock()
            question.reply = reply
            if question in self.queue:
                self.queue.remove(question)
            if self.open is question:
                self.open = None
            if question.message.get("remember") and reply.get("option"):
                self.offer_remember(question, reply["option"])
            if getattr(question, 'remember_consent', False):
                if reply.get('option') == 'yes':
                    threading.Thread(target=self.remember, args=(str(question.id),), daemon=True).start()
                else:
                    self.remembering.pop(str(question.id), None)
            self.show_next()
        question.answered.set()

    def offer_remember(self, question, option):
        label = next((o["label"] for o in question.message["options"] if o["id"] == option), None)
        if label:
            consent = Question(next(self.question_ids), question.task,
                {"kind": "confirm", "text": f"Remember “{label}” for next time?",
                 "options": [{"id": "yes", "label": "Remember this"}, {"id": "no", "label": "No"}]}, self.clock())
            consent.remember_consent = True
            self.remembering[str(consent.id)] = (question.message["text"], label, question.task.goal)
            self.queue.append(consent)

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
        """Priority chooses the next question, never preempts one already displayed."""
        if self.open is None and self.queue:
            question = min(self.queue, key=lambda q: (q.message["kind"] not in FIRST, q.asked_at, q.id))
        else:
            return
        challenge = question.task.flow.challenges.get(question.message.get("challenge_id"), {})
        expires = challenge.get("expires_at")
        if question.message["kind"] == "secret" and expires is not None and expires <= time.time():
            self.queue.remove(question)
            question.reply = {"expired": True}
            question.answered.set()
            return self.show_next()
        self.open = question
        self.send(question.message, question.task, question)
        question.displayed_at = self.clock()
        structured = question.task.flow.questions.get(question.message.get("id"))
        if structured is not None:
            structured["displayed_at"] = question.displayed_at

    def showing(self):
        """The open question as the chat page needs it: which one, and whether what is typed must be hidden."""
        with self.lock:
            question = self.open
            return {"id": str(question.id), "secret": question.message["kind"] == "secret",
                    "message_id": question.sent[-1] if question.sent else None,
                    "challenge_id": question.message.get("challenge_id")} if question else None

    # ---- tasks

    def new_task(self, goal):
        task = Task(f"t{next(self.task_ids)}", goal)
        self.tasks[task.id] = task
        task.last_activity = self.clock()
        self.launch(task)
        return task

    def cancel(self, task):
        task.cancelled = True
        task.last_activity = self.clock()
        task.flow.approval = None
        if task.status == "blocked" and task.agent:
            task.worker = threading.Thread(target=self.finish_cancel, args=(task,), daemon=True)
            task.worker.start()
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
                clash = not self.reserve(task, task.host, self.account(task))
                if len(busy) < MAX_RUNNING and not clash:
                    task.status = "running"
                    return True
                task.status = "waiting"
                self.release(task)
                if not told:
                    told = True
                    self.say(f"Waiting for your other {task.host} task to finish first." if clash else
                             f"Queued: {task.title} starts when one of your other tasks finishes.", task)
                self.slots.wait(self.poll)
            return False

    def run(self, task):
        try:
            if task.agent is None:
                if task.recovery_rows:
                    url = task.recovery_rows[0]['origin'] + '/'
                    task.flow.constraints['read_only_reconciliation'] = True
                else:
                    facts = self.prepare(task)
                    if task.cancelled:
                        return
                    url, task.title, _ = self.hidden(self.start, task.goal, facts)
                task.host = site(url)
            if not self.acquire(task):
                return self.say(f"Cancelled {task.title} before it started.")
            if task.agent is None:
                task.agent = self.make_agent(url, task.goal, task.title, lambda m: self.ask(task, m),
                                             lambda m: self.send(m, task), task=task)
            else:
                self.deliver_files(task)
                # Dispatched authorization remains private in commit runtime for
                # read-only reconciliation; these live records cannot dispatch again.
                task.flow.approval = None
                task.flow.proposal = None
                task.agent.resume()
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
                try:
                    self.deliver_files(task)
                except (OSError, ValueError, RuntimeError):
                    task.agent.state['status'] = 'blocked'
                    task.flow.set_status('blocked')
                    self.say('The file could not be delivered. Resume to retry delivery.', task)
                    break
        except ReservationBlocked as error:
            task.status = 'blocked'
            task.flow.set_status('blocked')
            self.say(str(error), task)
        except Exception as error:  # the start job, launching the browser, anything: say it once
            self.say(f"I couldn't carry on with {task.title}: {error}", task)
        finally:
            if task.cancelled and task.agent:
                self.reconcile_cancel(task)
                if hasattr(task.agent, 'stop'):
                    task.agent.stop()
            state = task.agent.state["status"] if task.agent else None
            task.status = (state if state in STOPPED else 'blocked' if task.status == 'blocked'
                           else "cancelled" if task.cancelled else "failed")
            if task.recovery_rows and not task.cancelled:
                task.status = 'blocked'
                task.flow.status = 'blocked'
                self.say('The interrupted outcome still needs a matching independent receipt. '
                         'Its account remains reserved; the old action has not been replayed.', task)
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
                    task.status = 'blocked' if not task.cancelled else 'cancelled'
                    if task.status == 'blocked':
                        task.agent.state['status'] = 'blocked'
                try:
                    if task.status != "blocked":
                        task.agent.close()
                except Exception:
                    pass
            self.release(task)
            task.last_activity = self.clock()
            with self.slots:
                self.slots.notify_all()
            self.update_typing()

    def apply_updates(self, task):
        while task.updates:
            update = task.updates.pop(0)
            if hasattr(task.agent, "amend"):
                task.agent.amend(update, tuple(task.flow.facts))
            else:
                task.agent.state["memory"].append(f"User update: {update}")
            task.flow.approval = None
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
            if task.cancelled:  # cancelled while this step was deciding: never show its question
                return {"cancel": True}
            self.queue.append(question)
            self.show_next()
            self.update_typing()
        paused = False
        while not question.answered.wait(self.poll):
            waited = self.clock() - question.displayed_at if question.displayed_at is not None else 0
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
                media_id = task.file_ids.get(name)
                if media_id is None:
                    media_id = self.channel.register(path)
                    task.file_ids[name] = media_id
                receipt = self.channel.send(self.user, {"type": "document", "document": {
                    "id": media_id, "filename": path.name}})
                commit.record_delivery(task.flow, media_id, message_id=receipt)
                if hasattr(task.agent, 'bind_delivered_export'):
                    registered_path = self.channel.media(media_id)[0]
                    if Path(registered_path) == path:
                        task.agent.bind_delivered_export(path, media_id)
            else:
                self.say("A downloaded file could not be delivered here.", task)
                return
            task.sent_files += 1

    def prepare(self, task):
        """Resolve requirements in chat before any Browser constructor/navigation."""
        page = {"url": "https://chat.invalid/", "text": "", "actions": []}
        observe_state(task.flow, page)
        intent = self.hidden(self.start_intent, task.goal)
        for key in ("provider", "compare"):
            if intent.get(key):
                task.flow.constraints[key] = intent[key]
                put_fact(task.flow, key, intent[key], source='user')
        for key, saved in self.profile.data.get('defaults', {}).items():
            if (key not in task.flow.facts and saved.get('consent') is True
                    and saved.get('source') == 'user' and saved.get('scope') == 'task'):
                put_fact(task.flow, key, saved['value'], source='profile')
                task.flow.facts[key]['explicit_default'] = True
        assessment = self.hidden(self.assess_start, task.flow, page, "start")
        if assessment is not None:
            coordinator.apply_assessment(task.flow, assessment)
            for requirement in assessment["requirements"]:
                if requirement['kind'] in {'secret', 'file', 'image'}:
                    continue  # These need an observed browser challenge/field, never a start-time model guess.
                if task.flow.constraints.get('compare') and requirement['keys'] == ['provider']:
                    continue
                interaction.require(task.flow, requirement)
        mode = interaction.provider_mode(task.flow, self.profile.data.get("defaults", {}))
        while not task.cancelled:
            pending = [q for q in task.flow.questions.values() if not q.get("closed")
                       and q.get("answered_at") is None]
            if not pending:
                break
            q = pending[0]
            reply = self.ask(task, {"kind": q["kind"], "text": q["text"], "id": q["id"],
                                    "options": q["options"]})
            if reply.get("cancel"):
                task.cancelled = True
                return {}
            keys = [key for rid in q["requirement_ids"] for key in task.flow.requirements[rid]["keys"]
                    if not task.flow.facts.get(key, {}).get("valid")]
            values = self.hidden(self.answer_facts, reply.get("text", ""), keys)
            if values.get('provider'):
                choice = self.hidden(self.start_intent, reply.get('text', ''))
                if choice.get('compare'):
                    task.flow.constraints['compare'] = True
                    values['provider'] = 'compare'
            for key in list(values):
                if key in {'location', 'address', 'pickup', 'delivery_address'} and isinstance(values[key], str):
                    if rules.means_current_location(values[key]):
                        del values[key]
                        self.say('Please provide an address or landmark in chat for your current location.', task)
            interaction.answer_requirements(task.flow, q["id"], values, now=self.clock())
        mode = interaction.provider_mode(task.flow, self.profile.data.get("defaults", {}))
        if mode["mode"] == "ask" and not task.cancelled:
            raise ValueError("Provider choice is still unresolved")
        if mode["notice"]:
            self.say(mode["notice"], task)
        facts = {k: f["value"] for k, f in task.flow.facts.items() if f.get("valid")}
        facts.update(provider=mode["provider"], compare=mode["mode"] == "compare")
        return facts

    @staticmethod
    def account(task):
        fact = task.flow.facts.get("account", {})
        return fact.get("value") if fact.get("valid") and isinstance(fact.get("value"), str) else None

    @staticmethod
    def pending(task):
        return bool(task.recovery_rows) or any(e.get("commitment") and e.get("status") != "not_dispatched"
                   and e.get("outcome") not in {"failed", "success"} for e in task.flow.executions.values())

    def reserve(self, task, origin, account):
        host = site(origin) if "://" in origin else origin
        if not host:
            return False
        with self.reservation_lock:
            # Drop an unused old provider even if the destination is busy; no hold-and-wait.
            if not self.pending(task):
                for key in list(task.reservations):
                    if key != (host, account):
                        self.reservations.pop(key, None)
                        task.reservations.remove(key)
            for (other_host, other_account), owner in self.reservations.items():
                if owner != task.id and host == other_host and (
                        account is None or other_account is None or account == other_account):
                    return False
            # Relinquish unused providers before adding another: no multi-site hold-and-wait.
            if not self.pending(task):
                for key in list(task.reservations):
                    self.reservations.pop(key, None)
                    task.reservations.remove(key)
            key = (host, account)
            self.reservations[key] = task.id
            task.reservations.add(key)
            task.flow.progress["account_reservation_key"] = host + ":" + (account or "unknown")
            return True

    def reserve_access(self, task, origin):
        """Network callbacks use a separate lock and never wait for scheduling/browser work."""
        host = site(origin)
        if not host:
            return False
        with self.reservation_lock:
            if any(other_host == host and owner != task.id
                   for (other_host, _account), owner in self.reservations.items()):
                return False
            key = (host, None)  # A redirect/data request has no independently verified account identity yet.
            self.reservations[key] = task.id
            task.reservations.add(key)
            return True

    def release(self, task):
        with self.reservation_lock:
            if self.pending(task):
                return
            for key in task.reservations:
                self.reservations.pop(key, None)
            task.reservations.clear()

    def launch(self, task):
        task.worker = threading.Thread(target=self.run, args=(task,), name=f"jev-{task.id}", daemon=True)
        task.worker.start()

    def resume(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None or task.status != "blocked" or (task.worker and task.worker.is_alive()):
                return
            task.last_activity = self.clock()
            task.status = "waiting"
            self.launch(task)

    def reap_idle(self):
        with self.lock:
            for task in self.tasks.values():
                if task.status == "blocked" and self.clock() - task.last_activity >= CANCEL_AFTER:
                    if task.agent:
                        if hasattr(task.agent, 'stop'):
                            task.agent.stop()
                        task.agent.close()
                    task.status = "cancelled"
                    self.release(task)

    def recover_interrupted(self):
        for path in self.journal_dir.glob("*.jsonl"):
            if path.name in self.interrupted_runs:
                continue
            try:
                rows = commit.Journal(path, self.secrets.redact).interrupted()
            except (OSError, ValueError):
                self.say("A prior task journal could not be read; its outcome needs inspection.")
                continue
            if not rows:
                continue
            self.interrupted_runs[path.name] = rows
            tid = 'interrupted-' + path.stem
            objective = rows[0].get('summary', {}).get('objective', 'a prior external action')
            task = Task(tid, 'Inspect account history read-only to reconcile: ' + self.secrets.text(objective))
            task.recovery_rows = rows
            task.status = 'blocked'
            task.last_activity = self.clock()
            task.flow.constraints['read_only_reconciliation'] = True
            self.tasks[tid] = task
            for row in rows:
                origin = row.get("origin")
                if origin:
                    key = (site(origin), None)
                    self.reservations[key] = tid
                    task.reservations.add(key)
            self.say("A prior task was interrupted with an unknown outcome. Its account remains reserved. "
                     "Ask to resume its read-only account-history inspection to reconcile the outcome.", task)

    def local_choice(self, text):
        task = Task(f"t{next(self.task_ids)}", text)
        task.last_activity = self.clock()
        self.tasks[task.id] = task

        def work():
            try:
                cleanup = self.profile.legacy_card_cleanup() if text == "/cleanup-legacy-cards" else None
                if text == "/cleanup-legacy-cards" and cleanup is None:
                    return self.say("No legacy card details are saved.", task)
                if cleanup:
                    message = cleanup["text"]
                else:
                    parts = text.split(" ", 2)
                    if len(parts) != 3 or not parts[2].strip():
                        return self.say("Use /default followed by a field and its value.", task)
                    _, key, value = parts
                    message = f"Save {value} as your default {key}?"
                reply = self.ask(task, {"kind": "confirm", "text": message,
                    "options": [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}]})
                if reply.get("option") != "yes":
                    return
                if cleanup:
                    Vault().delete_legacy_cards(cleanup["keys"], consent=True)
                    self.profile.data["cards"] = []
                    self.profile.save()
                    self.say("Deleted the legacy local card details.", task)
                else:
                    self.profile.set_default(key, value, consent=True)
                    self.say("Saved your explicit default.", task)
            except (ValueError, RuntimeError, OSError):
                self.say("The local preference could not be updated.", task)
            finally:
                task.status = "cancelled" if task.cancelled else "done"
        task.worker = threading.Thread(target=work, daemon=True)
        task.worker.start()

    def reconcile_cancel(self, task):
        # Bounded read-only reconciliation: one observation/assessment, never a tick or dispatch.
        if self.pending(task) and hasattr(task.agent, 'coordinate'):
            try:
                task.agent.observe()
                task.agent.coordinate('outcome')
            except Exception:
                self.say('The earlier external action remains unconfirmed; its account stays reserved.', task)

    def finish_cancel(self, task):
        self.reconcile_cancel(task)
        task.agent.stop()
        task.agent.close()
        with self.slots:
            task.status = 'cancelled'
            self.release(task)
            self.slots.notify_all()
