"""The mini + Jev loop. GPT-4.1 mini decides each step and writes every word; Jev picks the element, checks each
result, and reads the user's replies; code keeps the safety rails. Reuses the base agent's browser, redaction,
asking, captcha relay, LOOK, and traces."""

import os
import re
import time
from urllib.parse import urlparse

from . import jev, mind, rules, views
from .agent import COMMIT_OPS, QUIET, Agent, Halt
from .browser import ExecutionUncertain, StalePage
from .model import action_space
from .questions import MAX_ACTIONS, MAX_DECISIONS

TALKING = {"ASK", "TELL", "DONE", "STOP"}
BROWSER_OP = {"CLICK": "CLICK", "TYPE": "TYPE_TEXT", "SELECT": "SELECT", "KEY": "PRESS_KEY", "HOVER": "HOVER",
              "UPLOAD": "UPLOAD"}
PRICES = (0.40e-6, 1.60e-6)  # GPT-4.1 mini, dollars per input and output token
PLACEHOLDER = re.compile(r"\[(profile|secret):([\w.]+)\]")
CODE_WORDS = re.compile(r"\b(code|otp|pin|passcode|password|cvv)\b", re.IGNORECASE)
YES = re.compile(r"\b(yes|yeah|yep|yup|ok|okay|sure|go ahead|book it|confirm|do it|proceed|haan)\b", re.IGNORECASE)
NOT_YES = re.compile(r"\?|\b(no|not|don'?t|wait|but|instead|cheaper|change|stop|cancel|hold)\b", re.IGNORECASE)
DIALOGS = {"ACCEPT_DIALOG", "DISMISS_DIALOG"}


class Retry(Exception):
    """Nothing done this step; the reason goes to mini as feedback for the next one."""


def agent_class():
    return Agent if os.environ.get("JEV_DRIVER") == "legacy" else MiniAgent


class MiniAgent(Agent):
    ground_min = 0.4
    max_minutes = 30
    max_spend = 0.50
    mind_screenshot = os.environ.get("JEV_MIND_SCREENSHOT") == "1"  # off by default; the bench compares both

    @staticmethod
    def extra_state():
        return {"plan": [], "chat": [], "feedback": [], "mini": [], "seen": None, "spend": 0.0,
                "model_errors": 0, "ground_retry": False, "same_nudges": [], "refusals": {}}

    @staticmethod
    def new_state(browser, goal, title, page, record):
        return {**Agent.new_state(browser, goal, title, page, record), **MiniAgent.extra_state()}

    # ---- the loop

    def tick(self):
        s = self.state
        if s["started_at"] is None:
            s["started_at"] = time.perf_counter()
        before = len(s["history"])
        try:
            self.step()
            s["model_errors"] = 0
        except StalePage:
            s["status"] = "ready"
            self.observe()
        except Retry as why:
            s["feedback"].append(str(why))
            self.refused()
        except (ValueError, RuntimeError) as error:  # a model or network hiccup: feedback, never the end by itself
            s["errors"].append({"step": len(s["history"]), "error": str(error)})
            s["model_errors"] += 1
            if s["model_errors"] >= 5:
                pending = (f" {s['commit']['label']} may already have gone through; check it before trying again."
                           if s["commit"] else "")
                raise Halt("failed", f"The models stopped answering, so I stopped: {error}.{pending}") from error
            if len(s["history"]) > before:  # the action itself ran; only reading its result failed
                s["feedback"].append(f"The last action already ran, but checking its result failed ({error}). "
                                     "Look at the page before repeating it.")
            else:
                s["feedback"].append(f"The last step failed ({error}); nothing was done. Decide again.")
        s["elapsed_ms"] = self.elapsed()
        return self.snapshot()

    def step(self):
        s = self.state
        self.check_caps()
        s.pop("typing", None)  # a code counts as an attempt only once it is typed
        page = s["page"] if s["browser"].fresh(s["page"]) else self.observe()
        seen = views.view(page, s["seen"])
        payload = self.payload(seen)
        started = self.elapsed()
        image = (s["browser"].screenshot(masked=self.secrets.nodes, hide=self.hidden_details())
                 if self.mind_screenshot else None)
        decision, meta = mind.decide(payload, image)
        usage = meta.get("usage") or {}
        s["spend"] += usage.get("prompt_tokens", 0) * PRICES[0] + usage.get("completion_tokens", 0) * PRICES[1]
        s["feedback"], s["seen"] = [], page
        entry = {"step": len(s["history"]) + 1, "changed": seen["changed"], "latency_ms": meta.get("latency_ms", 0),
                 **{k: decision[k] for k in ("evaluation", "memory", "plan", "next_goal", "action", "message",
                                             "options")}}
        s["mini"].append(entry)
        s["decisions"].append({"operation": decision["action"]["op"], "choice": decision["action"]["target"],
                               "latency_ms": entry["latency_ms"], "elapsed_ms": started,
                               "request": {"state": payload}})
        if decision["memory"].strip():
            s["memory"].append(decision["memory"].strip())
        if [p for p in decision["plan"] if p.strip()]:
            s["plan"] = [p.strip() for p in decision["plan"] if p.strip()][:10]
        if decision["action"]["op"] in TALKING:
            return self.talk_step(decision, page)
        return self.act_step(decision, page, entry)

    def refused(self):
        """The same step refused again and again: tell mini to stop at 3, ask the user at 6."""
        s = self.state
        act = s["mini"][-1]["action"] if s["mini"] else {}
        key = (act.get("op"), act.get("target"))
        count = s["refusals"][key] = s["refusals"].get(key, 0) + 1
        if count == 3:
            s["feedback"].append("This was refused 3 times: stop trying it. Pick a different element from the list, "
                                 "scroll, go back, or ASK the user.")
        if count >= 6:
            s["refusals"][key] = 0
            self.stuck()

    def stuck(self):
        s = self.state
        goal = s["mini"][-1]["next_goal"] if s["mini"] else s["goal"]
        title = s["page"]["title"] or rules.host(s["page"]["url"])
        reply = self.ask({"kind": "text", "text": f"I'm stuck on {title}: {goal} What should I do?"})
        s["chat"].append({"from": "user", "text": f"“{str(reply.get('text', '')).strip()}”"})

    def moved(self, page):
        """Whether the page's elements changed since mini saw it. Text that ticks (arrival times) doesn't count."""
        if self.state["browser"].fresh(page):
            return False
        return views.lines(self.observe()) != views.lines(page)

    def steady(self, page):
        """Asking costs the user's time: right after a step, wait until steady_ms for the page to settle; new or
        vanished elements mean mini decides again."""
        last = self.state["history"][-1] if self.state["history"] else None
        while last and self.elapsed() - last.get("executed_ms", 0) < self.steady_ms:
            time.sleep(0.25)
            if self.moved(page):
                self.state["status"] = "ready"
                raise StalePage("The page changed while I was about to ask. Choose again.")

    def check_caps(self):
        s = self.state
        if len(s["history"]) >= MAX_ACTIONS:
            raise Halt("blocked", f"Stopped at the {MAX_ACTIONS}-action limit.")
        if len(s["decisions"]) >= MAX_DECISIONS:
            raise Halt("blocked", f"Stopped at the {MAX_DECISIONS}-decision limit.")
        if self.elapsed() > self.max_minutes * 60000:
            raise Halt("blocked", f"Stopped after {self.max_minutes} minutes.")
        if s["spend"] > self.max_spend:
            raise Halt("blocked", f"Stopped at the ${self.max_spend:.2f} model-spend limit.")

    def payload(self, seen):
        s = self.state
        return {
            "goal": s["goal"], "chat": s["chat"][-20:], "profile": self.profile.summary(), "plan": s["plan"],
            "notes": s["memory"][-15:],
            "steps": [{k: h.get(k) for k in ("step", "operation", "action", "text", "check", "changed")}
                      for h in s["history"][-10:]],
            "committed_and_waiting": s["commit"]["label"] if s["commit"] else None,
            "feedback": s["feedback"], "page": seen,
        }

    # ---- talking

    def talk_step(self, decision, page):
        s = self.state
        act = decision["action"]
        message = decision["message"].strip() or (act["text"].strip() if act["op"] != "DONE" else "")
        if act["op"] == "TELL":
            if not message:
                raise Retry("TELL needs a message.")
            self.say(message)
            s["chat"].append({"from": "agent", "text": message})
            return None
        if act["op"] in {"ASK", "STOP"} and rules.captcha_challenge(page):
            return self.relay_captcha(page)  # only the user solves a captcha
        if act["op"] == "STOP":
            raise Halt("blocked", message or "I stopped: I can't find a way to finish this on the site.")
        if act["op"] == "DONE":
            return self.finish_step(decision, page)
        if not message:
            raise Retry("ASK needs the question in message.")
        if self.moved(page):
            raise StalePage("The page changed while mini decided. Choose again.")
        secret = rules.asks_for_secret(message) or (rules.code_field(page) and bool(CODE_WORDS.search(message)))
        options = [] if secret else [o.strip() for o in decision["options"] if o.strip()][:10]
        text = message + "".join(f"\n{n}. {o}" for n, o in enumerate(options, 1))
        self.steady(page)  # a page still switching screens gets read again first
        reply = self.ask({"kind": "secret" if secret else "text", "text": text})
        answer = str(reply.get("text", "")).strip()
        s["chat"].append({"from": "agent", "text": text})
        if secret:
            name = self.keep_secret(answer)
            s["chat"].append({"from": "user", "text": f"[secret:{name}] (hidden answer; type it as this placeholder)"})
            return None
        line = f"“{answer}”"
        if options and answer:
            pick = jev.read_reply(message, options, answer)
            line += f" → option {pick}: {options[int(pick) - 1]}" if pick != "other" else " → none of the options"
        s["chat"].append({"from": "user", "text": line})
        return None

    def finish_step(self, decision, page):
        s = self.state
        if s["commit"]:
            raise Retry("A committed action is still being checked; WAIT first.")
        quote = decision["action"]["text"].strip()
        if not quote or not rules.verbatim(quote, page["text"]):
            raise Retry("DONE needs text: a short quote copied exactly from the page that proves the goal is done.")
        if jev.achieved(page, s["goal"], decision["message"]) != "yes":
            raise Retry("The page doesn't show the goal done yet. Keep going, or TELL the user what's missing.")
        self.say(decision["message"].strip() or "Done.", kind="result", evidence=quote)
        s["status"] = "done"
        return None

    # ---- browser steps

    def act_step(self, decision, page, entry):
        act = decision["action"]
        if act["op"] == "WAIT":
            return self.run(page, "WAIT", self.control(page, "WAIT"), {}, None, decision)
        if act["op"] == "BACK":
            return self.run(page, "GO_BACK", self.control(page, "GO_BACK"), {}, None, decision)
        if act["op"] in DIALOGS:
            operation, action = act["op"], self.control(page, act["op"])
            commit = self.commit_label(operation, action, act, page, {})
            if commit and not act["commits"]:
                raise Retry(f"“{commit}” would commit. If you mean it, set commits true with the summary in message.")
            if commit and not self.approved(decision, action, page, commit):
                return None
            return self.run(page, operation, action, {}, None, decision, commit=commit)
        if act["op"] == "OPEN_URL":
            url = act["text"].strip()
            if urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
                raise Retry("OPEN_URL needs a full http(s) address in text.")
            return self.run(page, "OPEN_URL", self.control(page, "OPEN_URL"), {"url": url}, url, decision)
        operation, action, index = self.target(decision, page, entry)
        inputs, shown = self.inputs_for(operation, action, act, page)
        commit = self.commit_label(operation, action, act, page, inputs)
        if commit and not act["commits"]:
            raise Retry(f"“{commit}” would commit. If that is the step you mean, set commits true and put the "
                        "summary in message (what, where, option, payment method, and the total as the page shows it); "
                        "otherwise choose another element.")
        named = re.search(r"\[(\d+)\]", act["target"])
        if commit and named and index and named[1] != index:
            raise Retry(f"You named [{named[1]}], but the element matching “{act['target']}” is [{index}] "
                        f"“{action.get('label', '')}”. Say which one you mean; nothing was pressed.")
        if commit and not self.approved(decision, action, page, commit):
            return None
        return self.run(page, operation, action, inputs, shown, decision, commit=commit)

    def control(self, page, operation):
        _, _, controls = action_space(page["actions"])
        if operation in controls:
            return controls[operation]
        if operation == "GO_BACK":
            raise Retry("There is no earlier page to go back to.")
        if operation in DIALOGS:
            raise Retry("No browser dialog is open.")
        return {"id": operation.lower(), "kind": operation.lower(), "node": None, "label": operation.title()}

    def target(self, decision, page, entry):
        s = self.state
        act = decision["action"]
        operation = (("SCROLL_UP" if "up" in act["text"].lower() else "SCROLL_DOWN") if act["op"] == "SCROLL"
                     else BROWSER_OP[act["op"]])
        if operation == "SELECT" and not action_space(page["actions"])[1].get("select_target"):
            operation = "CLICK"  # list items and custom dropdown options are clicked
        quoted = re.search(r'"([^"]{2,})"', act["target"])
        if quoted:  # mini copied a label from the list: match it exactly, never let a guess stand in for it
            found = self.labelled(page, operation, quoted[1])
            if found:
                entry["ground"] = {"choice": found[1]["label"], "confidence": 1.0, "exact": True}
                return operation, found[1], found[0]
        action, confidence, near, index = jev.ground(page, operation, act["target"], decision["next_goal"],
                                                     s["goal"])
        entry["ground"] = {"choice": action and action["label"], "confidence": confidence}
        if action is not None and confidence >= self.ground_min:
            s["ground_retry"] = False
            return operation, action, index
        if not s["ground_retry"] and act["target"].strip():
            s["ground_retry"] = True
            raise Retry(f"I couldn't tell which element is “{act['target']}”. Closest: {'; '.join(near) or 'none'}. "
                        "Describe it again by its exact words on the page or its [n].")
        s["ground_retry"] = False
        if operation in {"CLICK", "HOVER"} and act["target"].strip():
            spot = self.look(page, act["target"])
            if spot:
                _, _, controls = action_space(page["actions"])
                look = controls.get("LOOK", {"id": "look", "kind": "look", "node": None, "label": "Look"})
                return "LOOK", {**look, **spot}, None
        raise Retry(f"There is no element for “{act['target']}” on this page. Scroll, wait, or try another way.")

    def labelled(self, page, operation, label):
        """(index, element) whose label is the one mini quoted; None when several match (Jev decides). A label on
        the page that no element carries is covered by something (an open panel): mini hears that."""
        _, heads, _ = action_space(page["actions"])
        candidates = heads.get(jev.HEAD[operation], {})
        same = [(i, a) for i, a in candidates.items() if a["label"][:100] == label[:100]]
        near = same or [(i, a) for i, a in candidates.items() if label in a["label"]]
        if len(near) == 1:
            return near[0]
        if not near and not any(label[:40] in a["label"] for a in page["actions"]):
            raise Retry(f"No element you can act on is labelled “{label}” right now. If the page text shows it, "
                        "something covers it (an open panel or pop-up): finish or close that first.")
        return None

    def inputs_for(self, operation, action, act, page):
        if operation == "TYPE_TEXT":
            value, shown = self.value(action, act["text"], page)
            return {"text": value}, shown
        if operation == "PRESS_KEY":
            return {"key": act["key"] or "Enter"}, None
        if operation == "LOOK":
            return {"point": action["point"]}, action.get("label")
        if operation == "UPLOAD":
            return Agent.inputs(self, "UPLOAD", action, {}, page)[0], None
        return {}, None

    def commit_label(self, operation, action, act, page, inputs):
        """The words the approval names when this action commits, or None."""
        key = inputs.get("key")
        if operation not in COMMIT_OPS or (operation == "PRESS_KEY" and key not in {"Enter", "Space"}):
            return None
        s = self.state
        focused = s["browser"].focused_label() if operation == "PRESS_KEY" and action.get("node") is None else ""
        label = rules.commit_label(operation, action, page, key, focused)
        if not label and operation == "PRESS_KEY" and key == "Enter" and action.get("node") is None:
            forms = [a for a in page["actions"] if a.get("submit") and rules.COMMIT.search(a["submit"])]
            field = next((a for a in forms if a["label"] == focused), forms[0] if forms else None)
            label = field["submit"] if field else None  # Enter submits the focused field's form
        return label or ((action.get("label") or "this step") if act["commits"] else None)

    def approved(self, decision, action, page, commit):
        """True when an earlier yes covers exactly this action. Otherwise ask with mini's summary; a clear yes
        records the approval and reads the page again before anything runs."""
        s = self.state
        approval = s["approval"]
        if approval and rules.approval_valid(approval, page, len(s["history"])) and self.matches(approval, action,
                                                                                                  commit):
            if not s["browser"].fresh(page, action):
                raise StalePage("Page changed since the approval. Choose again.")
            return True
        if s["commit"]:
            raise Retry("An earlier committed action is still being checked; WAIT for it first.")
        summary = decision["message"].strip()
        if not summary:
            raise Retry(f"“{commit}” commits: put the summary in message (what, where, option, payment method, and "
                        "the total exactly as the page shows it).")
        amounts = rules.PRICE.findall(summary)
        missing = [x for x in amounts if not rules.amount_shown(x, page["text"])]
        if missing:
            raise Retry(f"The page doesn't show {', '.join(missing)}. Copy amounts exactly as the page shows them.")
        if self.moved(page):
            raise StalePage("The page changed while mini decided. Choose again.")
        self.steady(page)
        question = f"{summary}\n\nI'll press “{commit}”. Reply yes to go ahead, or tell me what to change."
        reply = self.ask({"kind": "text", "text": question, "approval": True})
        answer = str(reply.get("text", "")).strip()
        s["chat"] += [{"from": "agent", "text": question}, {"from": "user", "text": f"“{answer}”"}]
        clear = bool(YES.search(answer)) and not NOT_YES.search(answer)  # a model's yes alone never commits
        if clear and jev.approval(summary, answer) == "yes":
            target = (action.get("frame"), action["node"]) if action.get("node") is not None else None
            s["approval"] = {"action_label": commit, "amount": amounts[-1] if amounts else "",
                             "host": rules.host(page["url"]), "step": len(s["history"]), "target": target}
            if action.get("point"):
                s["approval"]["look"] = {"point": action["point"]}
            raise StalePage("Approved. Reading the page again before acting.")
        raise Retry(f"The user did not approve: “{answer}”. Nothing was committed; do what they asked.")

    def value(self, action, text, page):
        """(what to type, what logs show). Placeholders become the user's values here, after every model call.
        A code, password, or CVV field takes only the user's hidden answer, once, within the attempt limit."""
        s = self.state
        if not text:
            raise Retry("TYPE needs the text to type.")
        if rules.secret_field(action):
            found = re.fullmatch(r"\s*\[secret:(answer\d+)\]\s*", text)
            if not found or found[1] not in s["answers"]:
                raise Retry(f"“{action['label']}” takes a code or password: ASK the user for it, then TYPE the "
                            "placeholder of their answer ([secret:answerN]). Never type anything else there.")
            where = (rules.host(page["url"]), action["label"])
            typed = s["typed"].get(where) or {}
            if typed.get("key") == found[1]:
                raise Retry(f"That code ({text}) was already typed into “{action['label']}”. If the site rejected "
                            "it, ASK the user for a new one.")
            count = typed.get("count", 0)
            if count >= rules.attempt_limit(found[1]):
                raise Halt("blocked", f"The site rejected {count} codes for “{action['label']}”, so I stopped before "
                                      "your account gets locked.")
            code = rules.clean(s["answers"][found[1]], action)
            problem = rules.format_error(code, action)
            if problem:
                raise Retry(f"The user's answer doesn't fit “{action['label']}”: {problem} ASK them again.")
            s["typing"] = (where, {"source": "user", "key": found[1], "count": count + 1, "url": page["url"],
                                   "origin": None})
            return code, f"[secret:{found[1]}]"

        def real(match):
            kind, key = match[1], match[2]
            if kind == "profile":
                try:
                    return self.profile.value(key)
                except KeyError:
                    raise Retry(f"The profile has no {key}; ASK the user.") from None
            if key in s["answers"]:
                return s["answers"][key]
            raise Retry(f"[secret:{key}] isn't one of the user's answers.")

        return PLACEHOLDER.sub(real, text), text

    def run(self, page, operation, action, inputs, shown, decision, commit=None):
        """Execute one operation, record it, read the page again, and have Jev check what it did."""
        s = self.state
        self.stop_if_interrupted()
        label = action.get("label") or operation
        entry = {"step": len(s["history"]) + 1, "operation": operation, "action": label, "kind": action.get("kind"),
                 "choice": action.get("id"), "text": shown, "key": inputs.get("key"),
                 "files": [self.file_name(f) for f in inputs["files"]] if "files" in inputs else None,
                 "page_changed": None, "url": page["url"], "committed": bool(commit)}
        try:
            s["browser"].act(operation, action, page, **inputs)
        except ExecutionUncertain as error:
            s["history"].append({**entry, "unconfirmed": str(error), "elapsed_ms": self.elapsed()})
            raise Halt("blocked", f"I may already have done “{label}” but can't confirm it, so I stopped instead of "
                                  "repeating it. Please check before trying again.") from error
        s["elapsed_ms"] = self.elapsed()
        s["history"].append({**entry, "executed_ms": s["elapsed_ms"], "elapsed_ms": s["elapsed_ms"]})
        typing = s.pop("typing", None)
        if typing:
            s["typed"][typing[0]] = typing[1]
        s["refusals"] = {}  # an action ran: earlier refusals no longer count
        if commit:
            s["approval"] = None
            s["commit"] = {"label": commit, "checks": 0, "since": s["elapsed_ms"]}
        if shown and str(shown).startswith("[secret:"):
            for node in action.get("group") or [action.get("node")]:
                self.secrets.nodes.add((action.get("frame"), node))
        after = self.observe()
        last = s["history"][-1]
        last.update(page_changed=after["fingerprint"] != page["fingerprint"], url=after["url"],
                    changed=views.change(page, after))
        s["status"] = "ready"
        if s.get("relaying") or operation == "WAIT" and not s["commit"]:
            return None
        if s["commit"]:
            return self.watch(decision)
        last["check"] = jev.check(after, decision["next_goal"], f"{operation} {label}", last["changed"])
        return self.track_loop(last)

    def watch(self, decision):
        """After a commit: wait (no mini calls) while the site works; confirmed or failed goes back to mini."""
        s = self.state
        commit, unclear = s["commit"], 0
        while True:
            verdict = jev.check(s["page"], decision["next_goal"], commit["label"], "", after_commit=True)
            if verdict == "processing":
                if self.elapsed() - commit["since"] >= self.processing_ms:
                    s["commit"] = None
                    raise Halt("blocked", f"{commit['label']} went through, but after {self.processing_ms // 60000} "
                                          "minutes the site still shows it in progress. I stopped watching; check the "
                                          "site or its app for updates.")
                if not commit.get("told"):
                    commit["told"] = True
                    self.say(f"{commit['label']}: the site is still working on it. I'll tell you as soon as it's "
                             "confirmed.")
            elif verdict == "unclear" and unclear == 0:
                unclear += 1
            else:
                break
            self.stop_if_interrupted()
            time.sleep(self.outcome_pause)
            self.observe()
        s["commit"] = None
        if verdict == "confirmed":
            s["memory"].append(f"Done: {commit['label']}, confirmed by the site. Report every detail the page shows.")
        elif verdict == "failed":
            s["memory"].append(f"{commit['label']} failed; trying again needs a new approval.")
        else:
            raise Halt("blocked", f"I can't tell whether {commit['label']} went through, so I stopped rather than "
                                  "risk doing it twice. Please check the site or its app before trying again.")
        return None

    NUDGES = {3: "This did nothing three times. Try something else: another element, scrolling, or another way.",
              5: "Still stuck after five tries. Rewrite your plan (plan field) before the next action."}

    def track_loop(self, last):
        """The same action on the same page doing nothing: nudge mini at 3 and 5, ask the user at 8."""
        s = self.state
        if last["page_changed"] or last["operation"] in QUIET:
            return None
        key = (last["operation"], last["action"], urlparse(last["url"]).path)
        same = sum(1 for h in s["history"][-20:] if not h.get("page_changed")
                   and (h["operation"], h["action"], urlparse(h["url"]).path) == key)
        if same in self.NUDGES:
            s["feedback"].append(self.NUDGES[same])
            s["same_nudges"].append(same)
        if same >= 8:
            goal = s["mini"][-1]["next_goal"] if s["mini"] else s["goal"]
            title = s["page"]["title"] or rules.host(s["page"]["url"])
            self.steady(s["page"])
            reply = self.ask({"kind": "text", "text": f"I'm stuck on {title}: {goal} What should I do?"})
            s["chat"].append({"from": "user", "text": f"“{str(reply.get('text', '')).strip()}”"})
            for h in s["history"][-20:]:
                h["page_changed"] = True  # the user's help starts a fresh count
        return None

    def perform(self, operation, action, decision, page):
        """The user's own picks (captcha squares): run exactly as asked, no model in between, never a commit."""
        if rules.commit_label(operation, action, page):
            label = action.get("label")
            self.state["feedback"].append(f"The user's pick “{label}” would commit, so it wasn't pressed.")
            return None
        return self.run(page, operation, action, {}, None, {"next_goal": "the user's pick"})

    def trace(self):
        data = super().trace()
        data["driver"] = "mini"
        data["plan"] = self.state["plan"]
        data["mini"] = self.secrets.redact(self.state["mini"])
        for step, h in zip(data["steps"], self.state["history"]):
            step["check"], step["changed"] = h.get("check"), h.get("changed")
        return data
