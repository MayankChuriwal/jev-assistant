"""Jev's jobs in the mini + Jev loop: pick the element mini described, check what a step did, and read the
user's replies. Each is one TypeSafe choice question over a fixed list, so Jev never writes text."""

import os
import re

from .model import action_space, criteria, post_json, validate_choice
from .questions import HEAD_PREMISE

URL = "https://api.typesafe.ai/v1/systemone"
HEAD = {"CLICK": "click_target", "HOVER": "click_target", "TYPE_TEXT": "type_text_target",
        "SELECT": "select_target", "PRESS_KEY": "press_key_target", "UPLOAD": "upload_target",
        "SCROLL_DOWN": "scroll_down_target", "SCROLL_UP": "scroll_up_target"}
CHECK = {"success": "The page now shows what the step was meant to do.",
         "failed": "Nothing changed, or the page shows an error, or something other than what the step meant.",
         "uncertain": "The page doesn't make it clear either way."}
AFTER = {"confirmed": "The site confirms it is done: order placed, booking or ride confirmed, driver or delivery "
                      "partner assigned, form submitted.",
         "failed": "The site says it failed, was declined, or can't be done (no drivers, out of stock).",
         "processing": "The site is still working on it: finding a driver, confirming a payment, processing.",
         "unclear": "The page doesn't show the result."}


def ask(page, name, options, instructions, elements=()):
    """Jev's answer to one choice question about a page: {"choice", "probabilities", "confidence"}."""
    body = {"model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
            "state": {"page": {k: page.get(k, "") for k in ("url", "title", "text")}, "elements": list(elements),
                      "recent_actions": []},
            "questions": {name: {"type": "choice", "criteria": options, "instructions": instructions}}}
    result = post_json(URL, os.environ["TYPESAFE_API_KEY"], body)
    return validate_choice(result["answers"].get(name, {}), options)


def ground(page, operation, target, next_goal, goal):
    """(action, confidence, closest labels, index) for mini's description, or (None, 0.0, [], None)."""
    elements, heads, _ = action_space(page["actions"])
    candidates = heads.get(HEAD[operation], {})
    if not candidates:
        return None, 0.0, [], None
    if not target.strip():  # "the page" or "the focused element"; a page that doesn't scroll scrolls its panel
        index = next((i for i, a in candidates.items() if a.get("node") is None), None)
        if index is None and operation.startswith("SCROLL"):
            index = next(iter(candidates))
        if index is not None:
            return candidates[index], 1.0, [], index
    answer = ask(page, "target", criteria(candidates),
                 {"goal": goal, "operation": HEAD_PREMISE[HEAD[operation]],
                  "rules": f"Choose the element that is: {target}. The step: {next_goal}. Choose only an offered "
                           "index."}, elements)
    index = answer["choice"]
    ranked = sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])
    near = [candidates[i]["label"][:60] for i, _ in ranked[:3]]
    named = re.search(r"\[(\d+)\]", target)
    confidence = 1.0 if named and named[1] == index else answer["probabilities"][index]
    return candidates[index], confidence, near, index


def check(page, step_goal, done, changed, after_commit=False):
    options = AFTER if after_commit else CHECK
    return ask(page, "result", options, {"goal": step_goal,
                                         "rules": f"Just done: {done}. What changed: {changed}. Judge the current "
                                                  "page."})["choice"]


def achieved(page, goal, report):
    return ask(page, "achieved", {"yes": "The page shows what the goal asks for: the outcome done, or the "
                                         "information it asks to find.",
                                  "no": "The page doesn't show that yet."},
               {"goal": goal, "rules": f"The agent reports: {report}. Judge only from the page."})["choice"]


def final(page, label, step_goal):
    """("final" | "step", probability): whether pressing `label` completes an order, booking, payment, or
    submission, or is only a step on the way (choosing or saving a selection, opening a panel)."""
    options = {"final": "Pressing it completes the order, booking, payment, or submission right away.",
               "step": "It is a step on the way (choosing or saving a selection, opening a panel); nothing is ordered "
                       "or paid yet."}
    answer = ask(page, "final", options, {"goal": step_goal, "rules": f"The control: “{label}”. Judge from the page."})
    return answer["choice"], answer["probabilities"][answer["choice"]]


def conversation(question, reply):
    return {"url": "", "title": "chat", "text": f"Question: {question}\nReply: {reply}"}


def read_reply(question, options, reply):
    choices = {str(n): option for n, option in enumerate(options, 1)}
    choices["other"] = "Something else, not one of these options."
    return ask(conversation(question, reply), "reply", choices,
               {"goal": "Read the user's reply.", "rules": "Which option does the reply choose?"})["choice"]


def approval(summary, reply):
    return ask(conversation(summary, reply), "approval",
               {"yes": "The user clearly agrees to go ahead exactly as summarised.",
                "no": "The user declines or wants to stop.",
                "change": "The user wants something changed first, or the reply is unclear."},
               {"goal": "Read the user's reply to an approval request.",
                "rules": "Only a clear yes is yes."})["choice"]


def judge(goal, rule, final):
    return ask({"url": "", "title": "bench", "text": final}, "judge",
               {"pass": "The final message meets the rule.", "fail": "It does not meet the rule."},
               {"goal": goal, "rules": rule})["choice"]
