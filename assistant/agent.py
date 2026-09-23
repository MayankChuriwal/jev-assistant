"""The task loop: observe, one Jev decision, one executed operation, log, observe again."""

import base64
import time
from pathlib import Path

from .browser import Browser, ExecutionUncertain, StalePage
from .model import action_space, choose, field_context, field_text, url_value
from .questions import MAX_ACTIONS, MAX_DECISIONS

TERMINAL = {"DONE": "done", "BLOCKED": "blocked"}


class Agent:
    def __init__(self, url, goal, *, record_dir=None, screenshots=False, provide_file=None, browser=None):
        goal = goal.strip() if isinstance(goal, str) else "\n".join(goal).strip()
        if not goal:
            raise ValueError("Supply a task")
        self.pending_text = None
        self.provide_file = provide_file
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        self.browser = browser or Browser(url)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser, goal=goal, page=page, decision=None, history=[], status="ready",
            decisions=[], text_calls=[], files=list(page.get("downloads", [])), elapsed_ms=0,
            started_at=None, record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def elapsed(self):
        return round((time.perf_counter() - self.state["started_at"]) * 1000)

    def observe(self):
        page = self.state["browser"].observe(screenshot=self.screenshots)
        self.state["page"] = page
        self.state["files"].extend(page.get("downloads", []))
        return page

    def command(self, name, body=None):
        state = self.state
        if name == "tick":
            try:
                self.predict()
                return self.act({"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                self.observe()
                state["elapsed_ms"] = self.elapsed()
                return self.snapshot()
        if name == "predict":
            return self.predict()
        if name == "act":
            return self.act(body or {})
        raise ValueError("Unknown command")

    def predict(self):
        state = self.state
        if state["started_at"] is None:
            state["started_at"] = time.perf_counter()
        if not state["browser"].fresh(state["page"]):
            self.observe()
        state["decision"] = None
        if state["status"] in {"done", "blocked"}:
            raise ValueError("This task has stopped. Start a new one.")
        if len(state["decisions"]) >= MAX_DECISIONS:
            state["status"] = "blocked"
            raise ValueError(f"Reached the {MAX_DECISIONS}-decision budget")
        state["decision"] = choose(state["page"], state["goal"], state["history"])
        state["decisions"].append(
            {**state["decision"], "fingerprint": state["page"]["fingerprint"], "elapsed_ms": self.elapsed()}
        )
        state["status"] = "predicted"
        return self.snapshot()

    def act(self, body):
        state = self.state
        decision, page, browser = state["decision"], state["page"], state["browser"]
        if not decision or body.get("fingerprint") != page["fingerprint"]:
            raise ValueError("Observe and choose before acting")
        # Consume once, before any mutation or model call. A retry cannot double-click.
        state["decision"] = None
        operation = decision["operation"]
        if operation in TERMINAL:
            if not browser.fresh(page):
                state["status"] = "ready"
                raise StalePage("Page changed since the decision. Choose again.")
            state["status"] = TERMINAL[operation]
            state["elapsed_ms"] = self.elapsed()
            return self.snapshot()
        if len(state["history"]) >= MAX_ACTIONS:
            state["status"] = "blocked"
            raise ValueError(f"Stopped at the {MAX_ACTIONS}-action budget")
        action = next(a for a in page["actions"] if a["id"] == decision["choice"])
        inputs, helper = self.inputs(operation, action, decision, page)
        entry = {
            "step": len(state["history"]) + 1,
            "operation": operation,
            "action": action["label"],
            "kind": action["kind"],
            "choice": decision["choice"],
            "probability": decision["probabilities"][decision["choice"]],
            "confidence": decision["confidence"],
            "latency_ms": decision["latency_ms"],
            "text": inputs.get("text") or inputs.get("url"),
            "key": inputs.get("key"),
            "drop": inputs["drop"]["label"] if "drop" in inputs else None,
            "files": inputs.get("files"),
            "text_helper": helper["model"] if helper else None,
            "text_latency_ms": helper["latency_ms"] if helper else 0,
            "page_changed": None,
            "url": page["url"],
            "usage": decision["usage"],
        }
        try:
            # Browser.act checks freshness immediately before input, including after text generation.
            entry["result"] = browser.act(operation, action, page, **inputs)
        except ExecutionUncertain as error:
            # Input may have reached the page. Record it and stop: repeating it could pay or submit twice.
            state["history"].append({**entry, "unconfirmed": str(error), "elapsed_ms": self.elapsed()})
            state["status"] = "blocked"
            raise
        self.pending_text = None
        state["elapsed_ms"] = self.elapsed()
        # Record execution before observing. A stale post-action observation must not erase the action.
        state["history"].append({**entry, "executed_ms": state["elapsed_ms"], "elapsed_ms": state["elapsed_ms"]})
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
        return self.snapshot()

    def inputs(self, operation, action, decision, page):
        """Values an operation needs besides its target, and text-model metadata when one was called."""
        state = self.state
        if operation == "TYPE_TEXT":
            if not state["browser"].fresh(page, action):
                raise StalePage("Page changed before text generation. Choose again.")
            context = field_context(state["goal"], action, page, state["history"])
            if self.pending_text and self.pending_text[0] == context:
                _, text, helper = self.pending_text
            else:
                text, helper = field_text(context)
                self.pending_text = (context, text, helper)
                state["text_calls"].append({**helper, "field": action["label"], "value": text})
            return {"text": text}, helper
        if operation == "OPEN_URL":
            url, helper = url_value(state["goal"], page, state["history"])
            state["text_calls"].append({**helper, "field": "URL", "value": url})
            return {"url": url}, helper
        if operation == "PRESS_KEY":
            return {"key": decision["key"]}, None
        if operation == "DRAG":
            return {"drop": next(a for a in page["actions"] if a["id"] == decision["drop"])}, None
        if operation == "UPLOAD":
            if not self.provide_file:
                state["status"] = "blocked"
                raise ValueError("UPLOAD needs a file from the user; nothing attached.")
            return {"files": self.provide_file(action)}, None
        return {}, None

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
