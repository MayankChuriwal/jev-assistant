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
                "model_errors": 0, "ground_retry": False}

    @staticmethod
    def new_state(browser, goal, title, page, record):
        return {**Agent.new_state(browser, goal, title, page, record), **MiniAgent.extra_state()}

    # ---- the loop

    def tick(self):
        s = self.state
        if s["started_at"] is None:
            s["started_at"] = time.perf_counter()
        try:
            self.step()
            s["model_errors"] = 0
        except StalePage:
            s["status"] = "ready"
            self.observe()
        except Retry as why:
            s["feedback"].append(str(why))
        except (ValueError, RuntimeError) as error:  # a model or network hiccup: feedback, never the end by itself
            s["errors"].append({"step": len(s["history"]), "error": str(error)})
            s["model_errors"] += 1
            if s["model_errors"] >= 5:
                raise Halt("failed", f"The models stopped answering, so I stopped: {error}") from error
            s["feedback"].append(f"The last step failed ({error}); nothing was done. Decide again.")
        s["elapsed_ms"] = self.elapsed()
        return self.snapshot()

    def step(self):
        s = self.state
        self.check_caps()
        page = s["page"] if s["browser"].fresh(s["page"]) else self.observe()
        if rules.captcha_challenge(page):
            return self.relay_captcha(page)
        seen = views.view(page, s["seen"])
        payload = self.payload(seen)
        started = self.elapsed()
        image = s["browser"].screenshot(masked=self.secrets.nodes) if self.mind_screenshot else None
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

    # ---- browser steps

    def act_step(self, decision, page, entry):
        act = decision["action"]
        if act["op"] == "WAIT":
            return self.run(page, "WAIT", self.control(page, "WAIT"), {}, None, decision)
        if act["op"] == "BACK":
            return self.run(page, "GO_BACK", self.control(page, "GO_BACK"), {}, None, decision)
        if act["op"] == "OPEN_URL":
            url = act["text"].strip()
            if urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
                raise Retry("OPEN_URL needs a full http(s) address in text.")
            return self.run(page, "OPEN_URL", self.control(page, "OPEN_URL"), {"url": url}, url, decision)
        operation, action = self.target(decision, page, entry)
        inputs, shown = self.inputs_for(operation, action, act, page)
        commit = self.commit_label(operation, action, act, page, inputs)
        if commit and not self.approved(decision, action, page, commit):
            return None
        return self.run(page, operation, action, inputs, shown, decision, commit=commit)

    def control(self, page, operation):
        _, _, controls = action_space(page["actions"])
        if operation in controls:
            return controls[operation]
        if operation == "GO_BACK":
            raise Retry("There is no earlier page to go back to.")
        return {"id": operation.lower(), "kind": operation.lower(), "node": None, "label": operation.title()}

    def target(self, decision, page, entry):
        s = self.state
        act = decision["action"]
        operation = (("SCROLL_UP" if "up" in act["text"].lower() else "SCROLL_DOWN") if act["op"] == "SCROLL"
                     else BROWSER_OP[act["op"]])
        action, confidence, near, _ = jev.ground(page, operation, act["target"], decision["next_goal"], s["goal"])
        entry["ground"] = {"choice": action and action["label"], "confidence": confidence}
        if action is not None and confidence >= self.ground_min:
            s["ground_retry"] = False
            return operation, action
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
                return "LOOK", {**look, **spot}
        raise Retry(f"There is no element for “{act['target']}” on this page. Scroll, wait, or try another way.")

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
        return label or ((action.get("label") or "this step") if act["commits"] else None)

    def approved(self, decision, action, page, commit):  # replaced in Task 7
        raise Retry(f"“{commit}” commits; approval comes in Task 7.")

    def value(self, action, text, page):  # replaced in Task 5
        return text, text

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

    def watch(self, decision):  # replaced in Task 7
        self.state["commit"] = None

    def track_loop(self, last):  # replaced in Task 8
        return None
