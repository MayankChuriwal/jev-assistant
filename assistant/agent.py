"""The task loop: observe, one Jev decision, one executed operation, log, observe again. The agent asks its user
only for what they alone can give, and no model ever sees a secret."""

import base64
import json
import re
import time
from pathlib import Path

from . import jobs, rules
from .browser import Browser, ExecutionUncertain, StalePage
from .model import action_space, choose, field_context, field_value, url_value
from .profile import CARD_PARTS, SECRETS, Profile, Secrets, Vault
from .questions import MAX_ACTIONS, MAX_DECISIONS

STOPPED = {"done", "blocked", "failed", "cancelled"}
TALK = {"ASK_USER", "CONFIRM", "NOTE", "DONE", "BLOCKED"}
PRIVATE = {"browser", "answers", "unused", "pending_vault", "card_parts"}  # hold secret values
EDITABLE = {"textbox", "searchbox", "spinbutton", "combobox"}
SAVE_AS = {f"card.{part}" for part in CARD_PARTS}
ASK_TRIES = 5
OUTCOME_CHECKS = 10


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
    def __init__(self, url, goal, *, asker=None, notify=None, profile=None, vault=None, record_dir=None,
                 screenshots=False, provide_file=None, browser=None):
        goal = goal.strip() if isinstance(goal, str) else "\n".join(goal).strip()
        if not goal:
            raise ValueError("Supply a task")
        self.asker, self.notify, self.provide_file = asker, notify, provide_file
        self.profile = profile or Profile()
        self.vault = vault or Vault()
        self.secrets = Secrets(self.profile.hidden_values())
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        title = goal[:60]
        token = SECRETS.set(self.secrets)
        try:
            if url is None:
                url, title, _ = jobs.start(goal)
            self.browser = browser or Browser(url, geolocation=self.profile.geolocation())
            try:
                page = self.browser.observe(screenshot=self.screenshots)
            except Exception:
                self.browser.close()
                raise
        finally:
            SECRETS.reset(token)
        self.state = self.new_state(self.browser, goal, title, page, bool(self.record_dir))
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    @staticmethod
    def new_state(browser, goal, title, page, record):
        return dict(
            browser=browser, goal=goal, title=title, page=page, decision=None, history=[], status="ready",
            decisions=[], text_calls=[], files=list(page.get("downloads", [])), elapsed_ms=0, started_at=None,
            record=record, memory=[], messages=[], answers={}, unused={}, typed={}, rejected=set(), approval=None,
            commit=None, pending_vault={}, card_parts={}, help_rounds={},
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

    def observe(self):
        page = self.state["browser"].observe(screenshot=self.screenshots)
        for action in page["actions"]:
            if action.get("value") and (action.get("frame"), action.get("node")) in self.secrets.nodes:
                action["value"] = "[secret]"  # an emptied field (a rejected code) still reads as empty
        self.state["page"] = page
        self.state["files"].extend(page.get("downloads", []))
        return page

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
        commit = self.gate(operation, action, decision, page)
        if commit is False:
            return self.snapshot()
        inputs, helper, shown = self.inputs(operation, action, decision, page)
        self.execute(operation, action, decision, page, inputs, helper, shown)
        if isinstance(commit, str):  # it executed: the approval is spent and the outcome is watched
            self.state["approval"] = None
            self.state["commit"] = {"label": commit, "checks": 0}
        self.check_commit()
        return self.snapshot()

    def gate(self, operation, action, decision, page):
        """True for ordinary actions; the commit's label once the user approved it; False when they did not."""
        s = self.state
        label = rules.commit_label(operation, action, page, decision.get("key"))
        if not label:
            return True
        if not rules.approval_valid(s["approval"], page, len(s["history"])) and not self.confirm(page, label):
            return False
        if not s["browser"].fresh(page, action):  # the user may have taken a while
            raise StalePage("Page changed while waiting for approval. Choose again.")
        return label

    def check_commit(self):
        """After a commit: success carries on, an explicit failure is reported, anything unclear stops the task."""
        s = self.state
        commit = s["commit"]
        if not commit or s["status"] in STOPPED:
            return
        outcome, evidence, _ = jobs.outcome(self.context(), s["page"], commit["label"])
        commit["checks"] += 1
        if outcome == "pending" and commit["checks"] < OUTCOME_CHECKS:
            return
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

    def execute(self, operation, action, decision, page, inputs, helper, shown):
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
            "files": [Path(f).name for f in inputs["files"]] if "files" in inputs else None,
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
            (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                base64.b64decode(state["page"]["screenshot"])
            )
        recent = state["history"][-3:]
        stuck = len(recent) == 3 and all(h["page_changed"] is False and h["operation"] != "WAIT" for h in recent)
        state["status"] = "blocked" if stuck else "ready"

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
        if operation == "DRAG":
            return {"drop": next(a for a in page["actions"] if a["id"] == decision["drop"])}, None, None
        if operation == "UPLOAD":
            if self.provide_file:
                return {"files": self.provide_file(action)}, None, None
            name = action["label"].removeprefix("File input: ").removeprefix("Drop zone: ")
            reply = self.ask({"kind": "file", "text": f"This step needs a file ({name}). Send it here."})
            path = reply.get("file")
            if not path or not Path(path).is_file():
                raise Halt("blocked", "No file arrived, so nothing was attached.")
            state["memory"].append(f"User sent the file {Path(path).name}")
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
        value, shown, source, key = self.resolve(form, action, where)
        s["unused"][where] = (value, shown, source, key)
        s["text_calls"].append({**helper, "field": action["label"], "value": shown})
        return value, shown, helper

    def previous(self, where):
        return None

    def allowed(self, where):
        """Secret keys the text model may name for this field: never one the site already rejected here."""
        keys = self.profile.vault_keys() + list(self.state["answers"])
        return [key for key in keys if (where, key) not in self.state["rejected"]]

    def resolve(self, form, action, where):
        """(value, shown, source, key) for a field-value answer."""
        kind = form[0]
        if kind == "text":
            return form[1], form[1], "model", None
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
        if before and before["source"] != "model" and before["count"] == rules.attempt_limit(before["key"]) - 1:
            question += " One try left before the site may lock this."
        value = self.ask_value(question, action, secret)
        if before and before["source"] == "vault":
            key = before["key"]  # the user's replacement for a rejected Keychain value
            s["pending_vault"][key] = value  # stored only after a successful commit
        shown = value
        if secret:
            name = f"answer{len(self.state['answers']) + 1}"
            self.state["answers"][name] = value
            shown = f"[secret:{name}]"
            self.secrets.add(value, shown)
        if key in SAVE_AS:
            self.state["card_parts"][key] = value
        return value, shown, "user", key

    def after_typing(self, action, page):
        s = self.state
        where = (rules.host(page["url"]), action["label"])
        _value, shown, source, key = s["unused"].pop(where, (None, None, "model", None))
        if shown and shown.startswith("[secret:"):
            for node in action.get("group") or [action["node"]]:
                self.secrets.nodes.add((action.get("frame"), node))
        count = s["typed"].get(where, {}).get("count", 0)
        s["typed"][where] = {"source": source, "key": key, "count": count + (source != "model")}

    # ---- talking operations

    def talk(self, operation, page):
        state = self.state
        if operation == "DONE":
            return self.finish(page)
        if operation == "BLOCKED":
            return self.unblock(page)
        if operation == "NOTE":
            facts, _ = jobs.note(self.context(), page)
            state["memory"] += [f"Noted: {fact}" for fact in facts]
        elif operation == "CONFIRM":
            self.confirm(page, "the next step")
        else:
            self.ask_user(page)
        if state["status"] not in STOPPED:
            state["status"] = "ready"
        return self.snapshot()

    def ask_user(self, page):
        s = self.state
        elements, heads, _ = action_space(page["actions"])
        question, _ = jobs.compose(self.context(), page, elements)
        options = [{"id": f"o{n}", "label": o["label"], "description": o["description"]}
                   for n, o in enumerate(question["options"], 1)]
        reply = self.ask({"kind": question["kind"], "text": question["text"], "options": options,
                          "remember": question["kind"] != "secret"})
        picked = next((o for o, shown in zip(question["options"], options) if shown["id"] == reply.get("option")), None)
        answer = picked["label"] if picked else str(reply.get("text", "")).strip()
        if not answer:
            raise Halt("blocked", "I needed an answer to continue.")
        if question["kind"] == "secret":
            name = f"answer{len(s['answers']) + 1}"
            s["answers"][name] = answer
            self.secrets.add(answer, f"[secret:{name}]")
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
        page = self.observe()  # the user may have taken a while
        clicks = [a for a in page["actions"] if a["kind"] == "click"]
        same = (target.get("frame"), target.get("node"))
        match = next((a for a in clicks if (a.get("frame"), a.get("node")) == same), None) or next(
            (a for a in clicks if a["label"] == target["label"]), None)
        if match is None:
            return  # the page moved on; Jev decides with the answer in memory
        pick = {"operation": "CLICK", "choice": match["id"], "probabilities": {match["id"]: 1.0},
                "confidence": 1.0, "latency_ms": 0, "usage": {}}
        try:
            self.perform("CLICK", match, pick, page)
        except StalePage:
            self.observe()

    def confirm(self, page, label):
        """Show the summary card and wait for an explicit yes. True when approved."""
        s = self.state
        card, _ = jobs.confirm_card(self.context(), page, label)
        lines = [card["action_label"], *card["lines"]] + ([f"Total: {card['amount']}"] if card["amount"] else [])
        reply = self.ask({"kind": "confirm", "text": "\n".join(lines), "options": [
            {"id": "yes", "label": ("Yes, " + card["action_label"])[:20]}, {"id": "no", "label": "No"}]})
        if reply.get("option") == "yes":
            s["approval"] = {"action_label": card["action_label"], "amount": card["amount"],
                             "host": rules.host(page["url"]), "step": len(s["history"])}
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
        text, evidence, _ = jobs.result(self.context(), page)
        if not evidence:
            text += " I couldn't find proof of this on the page, so please check it yourself."
        self.say(text, kind="result", evidence=evidence)
        self.state["status"] = "done"
        self.state["elapsed_ms"] = self.elapsed()
        return self.snapshot()

    def unblock(self, page):
        raise Halt("blocked", "I'm stuck on this page and can't continue.")

    def run(self):
        while self.state["status"] not in STOPPED:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
