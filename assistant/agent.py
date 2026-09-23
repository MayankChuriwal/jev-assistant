"""The task loop: observe, one Jev decision, one executed operation, log, observe again. The agent asks its user
only for what they alone can give, and no model ever sees a secret."""

import base64
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from . import jobs, rules
from .browser import Browser, ExecutionUncertain, StalePage, unique
from .model import action_space, choose, field_context, field_value, url_value
from .profile import CARD_PARTS, HOME, SECRETS, Profile, Secrets, Vault
from .questions import LOGIN_FIRST, MAX_ACTIONS, MAX_DECISIONS

STOPPED = {"done", "blocked", "failed", "cancelled"}
TALK = {"ASK_USER", "CONFIRM", "NOTE", "DONE", "BLOCKED"}
PRIVATE = {"browser", "answers", "unused", "pending_vault", "card_parts"}  # hold secret values
EDITABLE = {"textbox", "searchbox", "spinbutton", "combobox"}
COMMIT_OPS = {"CLICK", "DOUBLE_CLICK", "PRESS_KEY", "ACCEPT_DIALOG", "LOOK"}
SAVE_AS = {f"card.{part}" for part in CARD_PARTS}
ASK_TRIES = 5
OUTCOME_CHECKS = 10
HELP_ROUNDS = 2
CAPTCHA_ROUNDS = 8  # a captcha can take several rounds of new images
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
QUIET = {"WAIT", "SCROLL_UP", "SCROLL_DOWN", "SCROLL_LEFT", "SCROLL_RIGHT"}  # repeating these is not going in circles
TRACE_STEP = ("step", "operation", "action", "choice", "probability", "confidence", "text", "drop", "files",
              "page_changed", "url", "elapsed_ms", "unconfirmed")
TRACE_DECISION = ("operation", "choice", "confidence", "operation_probabilities", "latency_ms", "elapsed_ms")


class Halt(Exception):
    """End the task with a status and a message for the user."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def expects(action):
    """What an answer box should accept, for the channel to render."""
    constraints = action.get("constraints") or {}
    shape = {k: constraints[k] for k in ("type", "inputmode", "maxlength") if k in constraints}
    if action.get("group"):
        shape["length"] = len(action["group"])
    return shape


class Agent:
    interrupted = None  # set by the chat: true once the user cancelled or changed the task during a step
    trace_dir = HOME / "traces"
    def __init__(self, url, goal, *, asker=None, notify=None, profile=None, vault=None, record_dir=None,
                 screenshots=False, provide_file=None, browser=None, title=None, trace_dir=None):
        goal = goal.strip() if isinstance(goal, str) else "\n".join(goal).strip()
        if not goal:
            raise ValueError("Supply a task")
        self.asker, self.notify, self.provide_file = asker, notify, provide_file
        self.profile = profile or Profile()
        self.vault = vault or Vault()
        self.secrets = Secrets(self.profile.hidden_values())
        self.record_dir = Path(record_dir) if record_dir else None
        if trace_dir:
            self.trace_dir = Path(trace_dir)
        self.screenshots = screenshots or bool(record_dir)
        title = title or goal[:60]
        token = SECRETS.set(self.secrets)
        try:
            if url is None:
                url, title, _ = jobs.start(goal)
            self.browser = browser or Browser(url, geolocation=self.profile.geolocation())
            try:
                page = self.clean(self.browser.observe(screenshot=False))
            except Exception:
                self.browser.close()
                raise
        finally:
            SECRETS.reset(token)
        self.state = self.new_state(self.browser, goal, title, page, bool(self.record_dir))
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.png").write_bytes(base64.b64decode(page["screenshot"]))

    @staticmethod
    def new_state(browser, goal, title, page, record):
        return dict(
            browser=browser, goal=goal, title=title, page=page, decision=None, history=[], status="ready",
            decisions=[], text_calls=[], files=list(page.get("downloads", [])), elapsed_ms=0, started_at=None,
            record=record, memory=[], messages=[], answers={}, unused={}, typed={}, rejected=set(), approval=None,
            commit=None, pending_vault={}, card_parts={}, help_rounds={}, login=None, errors=[],
        )

    # ---- what the user and the models see

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k not in PRIVATE},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def context(self):
        """The goal as Jev and the text model read it: goal, task memory, profile summary, approval."""
        s = self.state
        lines = [f"Goal: {s['goal']}"]
        if s.get("login") and not s["login"]["done"]:
            lines.append(LOGIN_FIRST.format(host=s["login"]["host"]))
        if s["memory"]:
            lines.append("Task memory:\n" + "\n".join(f"- {item}" for item in s["memory"][-30:]))
        lines.append("Profile: " + json.dumps(self.profile.summary(), ensure_ascii=False))
        approval = s["approval"]
        if approval:
            amount = f" ({approval['amount']})" if approval["amount"] else ""
            lines.append(f"Approval: the user approved {approval['action_label']}{amount}.")
        else:
            lines.append("Approval: none.")
        return "\n".join(lines)

    def say(self, text, kind="info", **extra):
        message = {"from": "agent", "kind": kind, "text": text, **extra}
        self.state["messages"].append(message)
        if self.notify:
            self.notify(message)

    def ask(self, message):
        """One question to the user; returns the reply. Secret replies never enter the log."""
        if self.asker is None:
            raise Halt("blocked", "This step needs you, but no one is attached to answer.")
        message = {"from": "agent", **message}
        self.state["messages"].append(message)
        reply = self.asker(message) or {}
        if reply.get("cancel"):
            raise Halt("cancelled", "Cancelled. Nothing more will be done.")
        logged = {**reply, "text": "••••"} if message["kind"] == "secret" and "text" in reply else reply
        self.state["messages"].append({"from": "user", **logged})
        return reply

    def ask_value(self, question, action, secret):
        """Ask until the answer fits the field. A malformed answer is re-asked and costs no attempt."""
        text = question
        for _ in range(ASK_TRIES):
            reply = self.ask({"kind": "secret" if secret else "text", "text": text, "expects": expects(action)})
            value = rules.clean(reply.get("text", ""), action)
            problem = rules.format_error(value, action) if value else "I need an answer to continue."
            if not problem:
                return value
            text = f"{problem} {question}"
        raise Halt("blocked", "I couldn't get a value that fits this field, so nothing was typed.")

    # ---- the loop

    def elapsed(self):
        return round((time.perf_counter() - self.state["started_at"]) * 1000)

    def clean(self, page):
        """The page as the agent keeps it: hidden values replaced before anything reads or truncates the text,
        fields that hold secrets masked, and screenshots taken with those fields blurred."""
        page["text"] = self.secrets.text(page["text"])
        page["title"] = self.secrets.text(page["title"])
        page["actions"] = self.secrets.redact(page["actions"])
        for action in page["actions"]:
            if action.get("value") and (action.get("frame"), action.get("node")) in self.secrets.nodes:
                action["value"] = "[secret]"  # an emptied field (a rejected code) still reads as empty
        if self.screenshots:
            page["screenshot"] = self.browser.screenshot(masked=self.secrets.nodes)
        return page

    def observe(self):
        page = self.clean(self.state["browser"].observe(screenshot=False))
        self.state["page"] = page
        self.state["files"].extend(page.get("downloads", []))
        self.forget_accepted(page)
        return page

    def forget_accepted(self, page):
        """A value the site took no longer counts against its field: the page moved on and the field is gone.
        The next code for that field (another login, a bank's OTP) starts with its own attempts."""
        labels = {a["label"] for a in page["actions"]}
        for where, typed in list(self.state["typed"].items()):
            if typed["source"] in {"vault", "profile"}:  # a saved value may be wrong: it counts until it works
                continue
            moved = typed.get("url") and urlparse(typed["url"])[1:3] != urlparse(page["url"])[1:3]
            if moved and where[1] not in labels:
                if typed["key"]:
                    self.state["rejected"].add((where, typed["key"]))  # a code is used once: never offer it again
                del self.state["typed"][where]

    def file_name(self, path):
        """A file's name as logs and models may see it: profile values hidden, even inside names like Jane_Doe_CV."""
        name = Path(path).name
        spaced = re.sub(r"[_-]+", " ", name)
        return self.secrets.text(spaced) if self.secrets.text(spaced) != spaced else name

    def command(self, name, body=None):
        token = SECRETS.set(self.secrets)  # every model request inside this step is redacted for this task
        try:
            if name == "tick":
                return self.tick()
            if name == "predict":
                return self.predict()
            if name == "act":
                return self.act(body or {})
            raise ValueError("Unknown command")
        except Halt as halt:
            self.state["status"] = halt.status
            self.state["decision"] = None
            self.say(str(halt))
            return self.snapshot()
        except StalePage:
            raise
        except (ValueError, RuntimeError) as error:  # a model or network problem: the trace keeps what it was
            self.state["errors"].append({"step": len(self.state["history"]), "error": str(error)})
            raise
        finally:
            SECRETS.reset(token)

    def tick(self):
        state = self.state
        try:
            self.predict()
            return self.act({"fingerprint": state["page"]["fingerprint"]})
        except StalePage:
            state["decision"] = None
            state["status"] = "ready"
            self.observe()
            state["elapsed_ms"] = self.elapsed()
            return self.snapshot()

    def predict(self):
        state = self.state
        if state["started_at"] is None:
            state["started_at"] = time.perf_counter()
        if not state["browser"].fresh(state["page"]):
            self.observe()
        state["decision"] = None
        if state["status"] in STOPPED:
            raise ValueError("This task has stopped. Start a new one.")
        if len(state["decisions"]) >= MAX_DECISIONS:
            raise Halt("blocked", f"Reached the {MAX_DECISIONS}-decision budget.")
        self.track_login(state["page"])
        state["decision"] = choose(state["page"], self.context(), state["history"])
        state["decisions"].append(
            {**state["decision"], "fingerprint": state["page"]["fingerprint"], "elapsed_ms": self.elapsed()}
        )
        state["status"] = "predicted"
        return self.snapshot()

    def act(self, body):
        state = self.state
        decision, page = state["decision"], state["page"]
        if not decision or body.get("fingerprint") != page["fingerprint"]:
            raise ValueError("Observe and choose before acting")
        # Consume once, before any mutation or model call. A retry cannot double-click.
        state["decision"] = None
        operation = decision["operation"]
        if operation in TALK:
            if not state["browser"].fresh(page):
                state["status"] = "ready"
                raise StalePage("Page changed since the decision. Choose again.")
            return self.talk(operation, page)
        action = next(a for a in page["actions"] if a["id"] == decision["choice"])
        return self.perform(operation, action, decision, page)

    def perform(self, operation, action, decision, page):
        """Execute one browser operation through the commit gate, for Jev's choices and the user's picks alike."""
        if len(self.state["history"]) >= MAX_ACTIONS:
            raise Halt("blocked", f"Stopped at the {MAX_ACTIONS}-action budget.")
        self.stop_if_interrupted()
        if operation == "CLICK" and rules.saved_address(action["label"]) and not self.state.get("address_chosen"):
            addresses = [a for a in page["actions"] if a["kind"] == "click" and rules.saved_address(a["label"])]
            if len(addresses) >= 2 and not self.prefers(action, addresses):
                return self.ask_address(page, addresses)  # the user decides where it goes
        if operation == "LOOK":
            approved = (self.state["approval"] or {}).get("look")
            if approved and approved["fingerprint"] == page["fingerprint"]:
                spot = {**approved, "commits": True}  # the spot the user said yes to, on the page they saw
            else:
                spot = self.look(page, "the control the task needs next")
            if spot is None:
                misses = self.state.setdefault("look_misses", {})
                misses[page["fingerprint"]] = misses.get(page["fingerprint"], 0) + 1
                if misses[page["fingerprint"]] >= 2:
                    raise Halt("blocked", "I looked at this page twice and couldn't find what the next step needs.")
                self.state["memory"].append("Looked at the screen: nothing there fits the next step.")
                self.state["status"] = "ready"
                return self.snapshot()
            action = {**action, **spot}  # the gate reads its name, its words, and whether it commits
        commit = self.gate(operation, action, decision, page)
        if commit is False:
            return self.snapshot()
        inputs, helper, shown = self.inputs(operation, action, decision, page)
        self.stop_if_interrupted()  # the last moment before anything reaches the page
        self.execute(operation, action, decision, page, inputs, helper, shown,
                     commit=commit if isinstance(commit, str) else None)
        self.check_commit()
        return self.snapshot()

    def track_login(self, page):
        """The user wants to be signed in before anything else. A site that offers sign-in makes it the next step,
        until its sign-in controls are gone after something was typed to sign in."""
        s = self.state
        offered, host = rules.login_offered(page), rules.host(page["url"])
        if s.get("login") is None:
            if offered:
                s["login"] = {"host": host, "since": len(s["history"]), "done": False}
        elif not s["login"]["done"] and not offered and host == s["login"]["host"] and not rules.code_field(page):
            # ponytail: "controls gone after a code or password"; a sign-in banner left up needs a user update.
            if any(h.get("operation") == "TYPE_TEXT" and str(h.get("text") or "").startswith("[secret")
                   for h in s["history"][s["login"]["since"]:]):
                s["login"]["done"] = True
                s["memory"].append(f"Signed in to {host}.")

    def prefers(self, action, addresses):
        """Whether a saved preference names this address: its own words, not ones every listed address shares."""
        others = set().union(*(rules.words(a["label"]) for a in addresses if a is not action))
        mine = rules.words(action["label"]) - others
        return any(mine & rules.words(p["text"]) for p in self.profile.data["preferences"])

    def ask_address(self, page, addresses):
        """Which saved address: always the user's choice, unless they saved a preference for one of them."""
        s = self.state
        extras = [a for a in page["actions"] if a["kind"] == "click" and rules.address_extra(a["label"])]
        choices = (addresses[:8] + extras[:2])[:10]
        options = [{"id": f"o{n}", "label": a["label"].split(",")[0].strip()[:20], "description": a["label"][:72]}
                   for n, a in enumerate(choices, 1)]
        reply = self.ask({"kind": "choice", "text": "Which address should I deliver to?", "options": options,
                          "remember": True})
        s["address_chosen"] = True
        picked = next((a for a, o in zip(choices, options) if o["id"] == reply.get("option")), None)
        if picked:
            s["memory"].append(f"User chose the address: {picked['label']}")
            self.click_same(picked)
        else:
            s["memory"].append(f"User wants it delivered to: {str(reply.get('text', '')).strip()} (not a saved "
                               "address: add it or search for it)")
        s["status"] = "ready"
        return self.snapshot()

    def keep_secret(self, value):
        """Hold a secret answer for this task: registered for redaction, usable by its name, never logged."""
        name = f"answer{len(self.state['answers']) + 1}"
        self.state["answers"][name] = value
        self.secrets.add(value, f"[secret:{name}]")
        return name

    def learn(self, key, value):
        """A detail the user gave joins their profile: hidden from every model from now on, never asked again."""
        self.profile.data[key] = value
        self.profile.save()
        for hidden, placeholder in self.profile.hidden_values():
            self.secrets.add(hidden, placeholder)
        names = {"phone": "phone number", "email": "email address", "name": "name"}
        self.say(f"Saved your {names[key]} to your profile, so it stays hidden from the models and isn't asked again.")

    def stop_if_interrupted(self):
        """A cancel or a change from the user arrived while this step was decided: decide again with it."""
        if self.interrupted and self.interrupted():
            self.state["status"] = "ready"
            raise StalePage("The user cancelled or changed the task. Choose again.")

    def look(self, page, purpose):
        """Find by sight what the element list can't name: one grid question, then a closer one inside the chosen
        cell. The text model answers with cell names only; code turns the cell into a point. Returns
        {"point", "label", "text", "commits"}, or None when nothing on screen fits."""
        s = self.state
        hide = self.hidden_details()
        image, cells = s["browser"].grid_screenshot(masked=self.secrets.nodes, hide=hide)
        found, meta = jobs.look(self.context(), page, purpose, image, cells)
        s["text_calls"].append({**meta, "field": "LOOK", "value": found and found["cell"]})
        if found is None:
            return None
        close, finer = s["browser"].grid_screenshot(region=cells[found["cell"]], columns=4, rows=4,
                                                    masked=self.secrets.nodes, hide=hide, scale=2)
        cell, meta = jobs.look_closer(found["label"], close, finer)
        s["text_calls"].append({**meta, "field": "LOOK closer", "value": cell})
        return {"point": list(rules.centre(finer[cell])), "label": found["label"], "text": found.get("text", ""),
                "commits": found.get("commits", False)}

    def gate(self, operation, action, decision, page):
        """True for ordinary actions; the commit's label once the user approved exactly this action; False when
        they declined. A fresh yes re-reads the page before anything runs."""
        s = self.state
        key = decision.get("key")
        if operation not in COMMIT_OPS or (operation == "PRESS_KEY" and key not in {"Enter", "Space"}):
            return True
        focused = s["browser"].focused_label() if operation == "PRESS_KEY" and action.get("node") is None else ""
        label = rules.commit_label(operation, action, page, key, focused)
        approval = s["approval"]
        steps = len(s["history"])
        if approval and rules.approval_valid(approval, page, steps) and self.matches(approval, action, label):
            if not s["browser"].fresh(page, action):
                raise StalePage("Page changed since the approval. Choose again.")
            return label or approval["action_label"]
        if not label:
            return True
        if self.confirm(page, label, action):
            if operation == "LOOK":  # the yes is for this spot, whatever a later look would call it
                s["approval"]["look"] = {"point": action["point"], "label": action["label"],
                                         "text": action.get("text", ""), "fingerprint": page["fingerprint"]}
            # The user may have taken a while and a total outside this control may have moved: read the page
            # again, and act only if the approval still holds there.
            raise StalePage("Approved. Reading the page again before acting.")
        return False

    def matches(self, approval, action, label):
        """Whether this is the action the user approved: the same control, or the same words when none was named."""
        if approval.get("look") or action.get("point"):  # a spot found by sight matches only the spot approved
            return bool(approval.get("look")) and action.get("point") == approval["look"]["point"]
        if approval.get("target"):
            return (action.get("frame"), action.get("node")) == approval["target"]
        return bool(label) and rules.overlap(label, approval["action_label"])

    def check_commit(self):
        """After a commit: success carries on, an explicit failure is reported, anything unclear stops the task.
        Returns the outcome, or None when no commit is waiting."""
        s = self.state
        commit = s["commit"]
        if not commit or s["status"] in STOPPED:
            return None
        try:
            outcome, evidence, _ = jobs.outcome(self.context(), s["page"], commit["label"])
        except ValueError:
            outcome, evidence = "unclear", ""  # the commit already ran; an unreadable answer is not a failure
        commit["checks"] += 1
        if outcome == "pending" and commit["checks"] < OUTCOME_CHECKS:
            return "pending"
        s["commit"] = None
        if outcome == "success":
            s["memory"].append(f"Done: {commit['label']} ({evidence})")
            for key, value in s["pending_vault"].items():
                self.vault.set(key, value)
            s["pending_vault"].clear()
            self.offer_card_save()
        elif outcome == "failed":
            s["memory"].append(f"{commit['label']} failed: {evidence}. Trying again needs a new approval.")
            self.say(f"{commit['label']} didn't go through: {evidence}")
        else:
            raise Halt("blocked", f"I can't tell whether {commit['label']} went through. Please check your bank "
                                  "or account before anything is tried again.")
        return outcome

    def offer_card_save(self):
        """After a successful payment with a card typed in this task, offer to keep it in the Keychain."""
        parts = self.state["card_parts"]
        number = re.sub(r"\D", "", parts.get("card.number", ""))
        if len(number) >= 12:
            last4 = number[-4:]
            reply = self.ask({"kind": "choice", "text": f"Save the card ending {last4} for next time? It stays in "
                              "this Mac's Keychain.", "options": [{"id": "save", "label": "Save card"},
                                                                  {"id": "skip", "label": "Not now"}]})
            if reply.get("option") == "save":
                card = f"card{last4}"
                for key, value in parts.items():
                    self.vault.set(f"card.{card}.{key.split('.')[1]}", value)
                self.profile.add_card(card, f"Card ••{last4}", last4)
                self.say(f"Saved the card ending {last4}.")
        parts.clear()

    def execute(self, operation, action, decision, page, inputs, helper, shown, commit=None):
        state = self.state
        entry = {
            "step": len(state["history"]) + 1,
            "operation": operation,
            "action": action["label"],
            "kind": action["kind"],
            "choice": decision["choice"],
            "probability": decision["probabilities"].get(decision["choice"], 1.0),
            "confidence": decision.get("confidence", 1.0),
            "latency_ms": decision.get("latency_ms", 0),
            "text": shown,
            "key": inputs.get("key"),
            "drop": inputs["drop"]["label"] if "drop" in inputs else None,
            "files": [self.file_name(f) for f in inputs["files"]] if "files" in inputs else None,
            "text_helper": helper["model"] if helper else None,
            "text_latency_ms": helper["latency_ms"] if helper else 0,
            "page_changed": None,
            "url": page["url"],
            "usage": decision.get("usage", {}),
        }
        try:
            # Browser.act checks freshness immediately before input, including after text generation.
            entry["result"] = state["browser"].act(operation, action, page, **inputs)
        except ExecutionUncertain as error:
            state["history"].append({**entry, "unconfirmed": str(error), "elapsed_ms": self.elapsed()})
            raise Halt("blocked", f"I may already have done “{action['label']}” but can't confirm it, so I "
                                  "stopped instead of repeating it. Please check before trying again.") from error
        state["elapsed_ms"] = self.elapsed()
        # Record execution before observing. A stale post-action observation must not erase the action.
        state["history"].append({**entry, "executed_ms": state["elapsed_ms"], "elapsed_ms": state["elapsed_ms"]})
        if commit:  # it ran: spend the approval and watch the outcome, even if the next observation fails
            state["approval"] = None
            state["commit"] = {"label": commit, "checks": 0}
        if operation == "TYPE_TEXT":
            self.after_typing(action, page)
        self.observe()
        state["elapsed_ms"] = self.elapsed()
        state["history"][-1].update(
            page_changed=state["page"]["fingerprint"] != page["fingerprint"],
            url=state["page"]["url"],
            elapsed_ms=state["elapsed_ms"],
        )
        if state["record"]:
            (self.record_dir / f"{state['elapsed_ms']:06d}.png").write_bytes(
                base64.b64decode(state["page"]["screenshot"])
            )
        recent = state["history"][-3:]
        # A canvas changes no text the page reports, so looking-and-clicking never counts as making no progress.
        stuck = len(recent) == 3 and all(h["page_changed"] is False and h["operation"] not in {"WAIT", "LOOK"}
                                         for h in recent)
        state["status"] = "ready"
        state["asks_since_step"] = 0
        last = state["history"][-1]
        again = [h for h in state["history"][-20:] if (h["operation"], h["action"], urlparse(h["url"]).path)
                 == (last["operation"], last["action"], urlparse(last["url"]).path)]
        looping = last["operation"] not in QUIET and len(again) >= 4
        if (stuck or looping) and not state.get("relaying"):  # ask for help, never stop silently or circle
            tried = ", ".join(dict.fromkeys(h["action"] for h in recent))
            state["memory"].append(f"My last 3 steps ({tried}) changed nothing on this page." if stuck else
                                   f"I've done “{last['action']}” {len(again)} times recently without getting closer.")
            self.unblock(state["page"])

    # ---- values an operation needs

    def inputs(self, operation, action, decision, page):
        """(inputs for the browser, text-model metadata, what the history shows)."""
        state = self.state
        if operation == "TYPE_TEXT":
            value, shown, helper = self.field_input(action, page)
            return {"text": value}, helper, shown
        if operation == "OPEN_URL":
            url, helper = url_value(state["goal"], page, state["history"])
            state["text_calls"].append({**helper, "field": "URL", "value": url})
            return {"url": url}, helper, url
        if operation == "PRESS_KEY":
            return {"key": decision["key"]}, None, None
        if operation == "LOOK":
            return {"point": action["point"]}, None, action["label"]
        if operation == "DRAG":
            drop = next(a for a in page["actions"] if a["id"] == decision["drop"])
            if drop["kind"] == "look":  # a free spot on a canvas or a map
                spot = self.look(page, f"a free spot to drop “{action['label']}” where the task needs it")
                if spot is None:
                    self.state["memory"].append(f"Looked for a spot to drop {action['label']}: nothing fits.")
                    raise StalePage("Nothing on screen fits the drop. Choose again.")
                drop = {**drop, "label": spot["label"], "point": spot["point"]}
            return {"drop": drop}, None, None

        if operation == "UPLOAD":
            if self.provide_file:
                return {"files": self.provide_file(action)}, None, None
            name = action["label"].removeprefix("File input: ").removeprefix("Drop zone: ")
            reply = self.ask({"kind": "file", "text": f"This step needs a file ({name}). Send it here."})
            path = reply.get("file")
            if not path or not Path(path).is_file():
                raise Halt("blocked", "No file arrived, so nothing was attached.")
            state["memory"].append(f"User sent the file {self.file_name(path)}")
            return {"files": [path]}, None, None
        return {}, None, None

    def field_input(self, action, page):
        """(value to type, what logs show, text-model metadata) for one field."""
        s = self.state
        if not s["browser"].fresh(page, action):
            raise StalePage("Page changed before text generation. Choose again.")
        where = (rules.host(page["url"]), action["label"])
        if where in s["unused"]:  # resolved but never typed (a stale page interrupted): reuse, never re-ask
            value, shown, _source, _key = s["unused"][where]
            return value, shown, None
        previous = self.previous(where)  # first: it records a rejection that allowed() must see
        context = field_context(self.context(), action, page, s["history"], self.profile.summary(),
                                self.allowed(where), previous)
        form, helper = field_value(context)
        if (form[0] == "text" and rules.addressed_to_user(form[1]) and not rules.free_text(action)) or \
                (form[0] == "ask" and not form[2] and rules.direction_question(form[1])):
            reply = self.ask({"kind": "text", "text": form[1]})  # a question about the next step, not a value
            s["memory"].append(f"User answered “{form[1]}”: {str(reply.get('text', '')).strip()}")
            raise StalePage("The user answered a question about the next step. Choose again with it.")
        value, shown, source, key = self.resolve(form, action, where)
        s["unused"][where] = (value, shown, source, key)
        s["text_calls"].append({**helper, "field": action["label"], "value": shown})
        return value, shown, helper

    def previous(self, where):
        """How the last value for this field fared. Choosing the same field again after a user, Keychain, or
        profile value means the site rejected it: count it, stop at the limit, and never offer that value again."""
        s = self.state
        typed = s["typed"].get(where)
        if not typed or typed["source"] == "model":
            return None
        limit = rules.attempt_limit(typed["key"])
        if typed["count"] >= limit:
            raise Halt("blocked", f"The site rejected {typed['count']} values for “{where[1]}”, so I stopped before "
                                  "your account gets locked.")
        if typed["key"]:
            s["rejected"].add((where, typed["key"]))
        if typed["source"] == "user" and typed.get("origin"):
            s["pending_vault"].pop(typed["origin"], None)  # a rejected replacement never reaches the Keychain
        return {"source": typed["source"], "rejected": True, "attempts": typed["count"], "left": limit - typed["count"]}

    def allowed(self, where):
        """Secret keys the text model may name for this field: never one the site already rejected here."""
        keys = self.profile.vault_keys() + list(self.state["answers"])
        return [key for key in keys if (where, key) not in self.state["rejected"]]

    def resolve(self, form, action, where):
        """(value, shown, source, key) for a field-value answer."""
        kind = form[0]
        if kind == "text":
            text = form[1]
            whole = re.fullmatch(r"\s*\[profile:(\w+)\]\s*", text)
            try:
                if "[secret" in text:  # "[secret]" marks a filled secret field; "[secret:<key>]" names one
                    raise KeyError(text)
                if whole:
                    return self.profile.value(whole[1]), f"[profile:{whole[1]}]", "profile", whole[1]
                return re.sub(r"\[profile:(\w+)\]", lambda m: self.profile.value(m[1]), text), text, "model", None
            except KeyError:  # a placeholder copied from the model's redacted view: never type it
                raise ValueError("The text model wrote a placeholder instead of a value; nothing typed.") from None
        if kind == "profile":
            return self.profile.value(form[1]), f"[profile:{form[1]}]", "profile", form[1]
        if kind == "secret":
            key = form[1]
            if key in self.state["answers"]:
                return self.state["answers"][key], f"[secret:{key}]", "user", key
            try:
                value = self.vault.get(key)
            except KeyError:
                question = f"I couldn't read your saved {key.rsplit('.', 1)[-1]} from the Keychain. Please send it."
                return self.ask_field(question, action, True, key, where)
            self.secrets.add(value, f"[secret:{key}]")
            return value, f"[secret:{key}]", "vault", key
        _, question, secret, save_as = form
        return self.ask_field(question, action, secret, save_as, where)

    def ask_field(self, question, action, secret, key, where):
        s = self.state
        before = s["typed"].get(where)
        # Card details, passwords, and codes are secret whatever the model said.
        secret = (secret or key in SAVE_AS or (action.get("constraints") or {}).get("type") == "password"
                  or bool(action.get("group")) or rules.asks_for_secret(question))
        if before and before["source"] != "model" and before["count"] == rules.attempt_limit(before["key"]) - 1:
            question += " One try left before the site may lock this."
        value = self.ask_value(question, action, secret)
        origin = before and (before["key"] if before["source"] == "vault" else before.get("origin"))
        if origin:
            key = origin  # the user's replacement for a rejected Keychain value
            s["pending_vault"][origin] = value  # the newest one; stored only after a successful commit
        shown = value
        if secret:
            name = self.keep_secret(value)
            shown = f"[secret:{name}]"
            key = key or name  # so a rejection can exclude this exact answer
        if key in SAVE_AS:
            self.state["card_parts"][key] = value
        return value, shown, "user", key

    def after_typing(self, action, page):
        s = self.state
        where = (rules.host(page["url"]), action["label"])
        value, shown, source, key = s["unused"].pop(where, (None, None, "model", None))
        detail = rules.profile_detail(action)
        told = source == "user" or any(m.startswith("User answered") and value and value in m for m in s["memory"])
        if detail and value and told and not str(shown).startswith("[") and not self.profile.data.get(detail):
            self.learn(detail, value)
        if shown and shown.startswith("[secret:"):
            for node in action.get("group") or [action["node"]]:
                self.secrets.nodes.add((action.get("frame"), node))
        before = s["typed"].get(where, {})
        origin = key if source == "vault" else before.get("origin")
        s["typed"][where] = {"source": source, "key": key, "count": before.get("count", 0) + (source != "model"),
                             "origin": origin, "url": page["url"]}

    # ---- talking operations

    def talk(self, operation, page):
        state = self.state
        if operation in {"ASK_USER", "BLOCKED"} and rules.captcha_challenge(page):
            return self.relay_captcha(page)
        if operation == "DONE":
            return self.finish(page)
        if operation == "BLOCKED":
            return self.unblock(page)
        if operation == "NOTE":
            facts, _ = jobs.note(self.context(), page)
            state["memory"] += [f"Noted: {fact}" for fact in facts]
        elif operation == "CONFIRM":
            self.confirm(page, "the next step")
        elif state.get("asks_since_step", 0) >= 2:  # asking again would repeat a question already answered
            state["memory"].append("I asked the user twice without taking a step in between; their answers are in "
                                   "task memory. Use them: type them into the field, or scroll or look for where "
                                   "they go.")
            state["asks_since_step"] = 0
            return self.unblock(page)
        else:
            state["asks_since_step"] = state.get("asks_since_step", 0) + 1
            self.ask_user(page)
        if state["status"] not in STOPPED:
            state["status"] = "ready"
        return self.snapshot()

    def ask_user(self, page):
        s = self.state
        elements, heads, _ = action_space(page["actions"])
        question, _ = jobs.compose(self.context(), page, elements)
        if question["kind"] != "choice" and rules.asks_for_secret(question["text"]):
            question["kind"] = "secret"  # a code or password is hidden whatever the model called it
        options = [{"id": f"o{n}", "label": o["label"], "description": o["description"]}
                   for n, o in enumerate(question["options"], 1)]
        reply = self.ask({"kind": question["kind"], "text": question["text"], "options": options,
                          "remember": question["kind"] == "choice" and question.get("preference", False)})
        picked = next((o for o, shown in zip(question["options"], options) if shown["id"] == reply.get("option")), None)
        answer = picked["label"] if picked else str(reply.get("text", "")).strip()
        if not answer:
            raise Halt("blocked", "I needed an answer to continue.")
        if not picked and question["kind"] != "secret" and rules.means_current_location(answer):
            home = next((a["text"] for a in self.profile.data["addresses"] if a.get("text")), None)
            if home:
                answer = f"{home} (their current location)"
            else:  # the agent's browser has no location: never let a site guess one
                where = self.ask({"kind": "text", "text": "My browser can't see where you are. What's the address "
                                                          "where you are now?"})
                answer = str(where.get("text", "")).strip() or answer
        if question["kind"] == "secret":
            name = self.keep_secret(answer)
            s["memory"].append(f"User answered “{question['text']}” with [secret:{name}] (secret key {name})")
            return
        detail = f" ({picked['description']})" if picked and picked["description"] else ""
        s["memory"].append(f"User answered “{question['text']}”: {answer}{detail}")
        if reply.get("remember"):
            preference, _ = jobs.remember(question["text"], answer)
            self.profile.remember(preference, s["goal"])
            self.say(f"Saved for next time: {preference}")
        target = heads.get("click_target", {}).get(picked["element"]) if picked and picked["element"] else None
        if target and target.get("role") not in EDITABLE:
            self.choose_option(target)

    def choose_option(self, target):
        """Click the element the user's pick is tied to, through the same gate as any other click."""
        self.click_same(target)

    def click_same(self, target):
        """Click the element the user named on a fresh read of the page, through the same gate as any other click."""
        page = self.observe()  # the user may have taken a while
        clicks = [a for a in page["actions"] if a["kind"] == "click"]
        same = (target.get("frame"), target.get("node"))
        match = next((a for a in clicks if (a.get("frame"), a.get("node")) == same), None)
        if match is None:
            named = [a for a in clicks if a["label"] == target["label"]]
            match = named[0] if len(named) == 1 else None  # two "Deliver here" buttons: never guess
        if match is None:
            return  # the page moved on; Jev decides with the answer in memory
        pick = {"operation": "CLICK", "choice": match["id"], "probabilities": {match["id"]: 1.0},
                "confidence": 1.0, "latency_ms": 0, "usage": {}}
        try:
            self.perform("CLICK", match, pick, page)
        except StalePage:
            self.observe()

    def confirm(self, page, label, action=None):
        """Show the summary card and wait for an explicit yes. True when approved; the approval is tied to the
        control that commits (the action itself, or the one the card names)."""
        s = self.state
        elements, heads, _ = action_space(page["actions"])
        card, _ = jobs.confirm_card(self.context(), page, label, elements)
        target = (action.get("frame"), action["node"]) if action and action.get("node") is not None else None
        if target is None and card["element"] and not (action and action.get("point")):  # LOOK binds its spot
            bound = next((a for head in heads.values() for index, a in head.items() if index == card["element"]
                          and a.get("node") is not None), None)
            target = (bound.get("frame"), bound["node"]) if bound else None
        lines = [card["action_label"], *card["lines"]] + ([f"Total: {card['amount']}"] if card["amount"] else [])
        reply = self.ask({"kind": "confirm", "text": "\n".join(lines), "options": [
            {"id": "yes", "label": ("Yes, " + card["action_label"])[:20]}, {"id": "no", "label": "No"}]})
        if reply.get("option") == "yes":
            s["approval"] = {"action_label": card["action_label"], "amount": card["amount"],
                             "host": rules.host(page["url"]), "step": len(s["history"]), "target": target}
            return True
        change = self.ask({"kind": "choice", "text": "What should I change?", "options": [
            {"id": "payment", "label": "Payment"}, {"id": "items", "label": "Items"},
            {"id": "address", "label": "Address"}, {"id": "cancel", "label": "Cancel the task"}]})
        if change.get("option") == "cancel":
            raise Halt("cancelled", "Cancelled before anything was committed.")
        what = change.get("option") or str(change.get("text", "")).strip() or "something"
        s["memory"].append(f"User declined {card['action_label']} and wants to change the {what}.")
        return False

    def finish(self, page):
        s = self.state
        if s["commit"]:  # a payment still settling decides whether the task is done
            label = s["commit"]["label"]
            outcome = self.check_commit()
            if outcome != "success":
                if outcome == "pending":
                    s["memory"].append(f"Waiting for the result of {label} before finishing.")
                s["status"] = "ready"
                return self.snapshot()
        if rules.commerce_goal(s["goal"]) and not any(m.startswith("Done: ") for m in s["memory"]):
            return self.not_done(page, "nothing was booked, ordered, or paid for in this task yet")
        text, evidence, achieved, _ = jobs.result(self.context(), page)
        if not achieved:
            return self.not_done(page, "the page doesn't show the goal done yet")
        if not evidence:
            text += " I couldn't find proof of this on the page, so please check it yourself."
        self.say(text, kind="result", evidence=evidence)
        self.state["status"] = "done"
        self.state["elapsed_ms"] = self.elapsed()
        return self.snapshot()

    def not_done(self, page, why):
        """DONE was premature: say why in task memory and carry on; after three, ask the user for help."""
        s = self.state
        s["memory"].append(f"Not done: {why}. Keep going.")
        s["done_refusals"] = s.get("done_refusals", 0) + 1
        s["status"] = "ready"
        if s["done_refusals"] >= 3:
            s["done_refusals"] = 0
            return self.unblock(page)
        return self.snapshot()

    def relay_captcha(self, page):
        """A captcha only the user may solve: they see it with its squares numbered, and the agent clicks exactly
        the squares they name, or keeps the text they read for typing. Nothing is solved automatically."""
        s = self.state
        s["captcha_rounds"] = s.get("captcha_rounds", 0) + 1
        if s["captcha_rounds"] > CAPTCHA_ROUNDS:
            raise Halt("blocked", f"The site kept showing new checks after {CAPTCHA_ROUNDS} rounds, so I stopped. "
                                  "Please try this step on your own device.")
        clicks = [a for a in page["actions"] if a["kind"] == "click" and a.get("node") is not None]
        marks = ([a for a in clicks if a.get("frame")] or clicks)[:40]  # a captcha lives in its own frame
        image = s["browser"].marked_screenshot(marks, masked=self.secrets.nodes, hide=self.hidden_details())
        reply = self.ask({"kind": "image", "image": image, "text": "The site wants a check (captcha) solved. Reply "
                          "with the numbers of the squares to pick, like 2 5 8; or type the text it shows; or say "
                          "none if there is nothing to pick."})
        text = str(reply.get("text", "")).strip()
        picked = [int(n) for n in re.findall(r"\d+", text)] if re.fullmatch(r"[\d\s,]+", text) else None
        if picked is None:
            s["memory"].append("User says the captcha has nothing to pick: press its verify or skip button."
                               if text.lower() in {"none", ""} else f"User read the captcha: {text}")
        else:
            s["relaying"] = True  # these clicks are the user's: no loop or no-progress help in between
            try:
                for n in picked:
                    if 1 <= n <= len(marks):
                        self.click_same(marks[n - 1])
            finally:
                s["relaying"] = False
            s["memory"].append(f"Clicked the captcha squares the user picked ({', '.join(map(str, picked))}). "
                               "Press its verify or next button now.")
        s["status"] = "ready"
        return self.snapshot()

    def hidden_details(self):
        return [value for value, _ in self.profile.hidden_values()]  # screenshots blur them where the page shows them

    def unblock(self, page):
        """BLOCKED: show and ask, ask for guidance, ask for a step on the user's device, or stop cleanly."""
        s = self.state
        if rules.captcha_challenge(page):
            return self.relay_captcha(page)
        where = rules.host(page["url"]) + urlparse(page["url"]).path
        rounds = s["help_rounds"].get(where, 0)
        elements, heads, _ = action_space(page["actions"])
        step, _ = jobs.unblock(self.context(), page, elements, must_stop=rounds >= HELP_ROUNDS)
        if step["rung"] == "stop":
            raise Halt("failed", step["text"])
        s["help_rounds"][where] = rounds + 1
        image = None
        if step["crop_element"] or step["rung"] == "show":
            target = next((a for head in heads.values() for index, a in head.items() if index == step["crop_element"]),
                          None)
            image = s["browser"].screenshot(target, masked=self.secrets.nodes)
        message = {"kind": "image", "text": step["text"], "image": image} if image else {"kind": "text",
                                                                                         "text": step["text"]}
        secret = rules.asks_for_secret(step["text"])
        if secret:
            message["kind"] = "secret"  # a code or password is hidden, and never sent to the hint model
        reply = self.ask(message)
        text = str(reply.get("text", "")).strip()
        path = Path(reply["file"]) if reply.get("file") else None
        if secret:
            name = self.keep_secret(text) if text else "none"
            s["memory"].append(f"User answered “{step['text']}” with [secret:{name}] (secret key {name})")
        elif step["rung"] == "device":
            s["memory"].append(f"User finished the step on their device: {text or 'done'}")
        elif path and path.suffix.lower() in IMAGE_TYPES and path.is_file():
            if not s.get("told_images"):
                self.say("Images you send are read by OpenAI to work out the next step.")
                s["told_images"] = True
            hint, _ = jobs.hint(self.context(), page, text=text, image=base64.b64encode(path.read_bytes()).decode())
            s["memory"].append(f"Hint from the user: {hint}")
        elif step["rung"] == "guide":
            hint, _ = jobs.hint(self.context(), page, text=text)
            s["memory"].append(f"Hint from the user: {hint}")
        else:
            s["memory"].append(f"User answered “{step['text']}”: {text}")  # later steps reuse it as an answer
        s["status"] = "ready"
        return self.snapshot()

    def run(self):
        while self.state["status"] not in STOPPED:
            yield self.command("tick")

    def settle(self, timeout=10.0):
        """Before the tabs close: wait (bounded) for downloads still in flight and keep every one that arrived."""
        browser = self.state["browser"]
        deadline = time.monotonic() + timeout
        while browser.pending_downloads and time.monotonic() < deadline:
            time.sleep(0.1)
        arrived = browser.downloads[:]
        del browser.downloads[:len(arrived)]
        self.state["files"].extend(arrived)

    def trace(self):
        """The task as the smoke set judges it, redacted like everything that leaves the task: its outcome, the
        questions it asked, and every step and decision with probabilities. No screenshots, no request bodies."""
        s = self.state
        result = next((m for m in reversed(s["messages"]) if m.get("kind") == "result"), None)
        return self.secrets.redact({
            "goal": s["goal"], "title": s["title"], "status": s["status"], "elapsed_ms": s["elapsed_ms"],
            "result": {"text": result["text"], "evidence": result.get("evidence", "")} if result else None,
            "questions": sum(m["from"] == "agent" and m.get("kind") not in {"info", "result"} for m in s["messages"]),
            "messages": [{k: v for k, v in m.items() if k != "image"} for m in s["messages"]],
            "steps": [{k: h.get(k) for k in TRACE_STEP} for h in s["history"]],
            "decisions": [{**{k: d.get(k) for k in TRACE_DECISION}, "seen": (d.get("request") or {}).get("state")}
                          for d in s["decisions"]],  # "seen": the page and elements Jev chose from, to replay it
            "text_calls": s["text_calls"], "memory": s["memory"], "files": [self.file_name(f) for f in s["files"]],
            "errors": s.get("errors", []),
        })

    def save_trace(self):
        """Write the trace to ~/.jev/traces/<time>-<title>.json, readable only by the user."""
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", self.state["title"].lower()).strip("-")[:40] or "task"
        path = unique(self.trace_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.json")
        path.write_text(json.dumps(self.trace(), indent=2, ensure_ascii=False, default=str))
        path.chmod(0o600)
        return path

    def close(self):
        try:
            self.save_trace()
        except OSError as error:  # a trace is a record: never a reason to leave the tabs open
            print(f"jev: trace not saved: {error}", file=sys.stderr)
        finally:
            self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
