"""System One: one Jev request per browser step, and the gates that decide whether Jev's pick runs, finishes the
subgoal, meets a blocker, or goes to the planner. Pure decisions: the worker executes what comes back."""

import re
from urllib.parse import urlparse

from . import jev, views
from .model import action_space, criteria

ACT_P = 0.55  # minimum probability of the chosen operation
TARGET_P = 0.6  # a target is accepted at this probability…
TARGET_MARGIN = 0.25  # …or at this lead over the next target (same-label duplicates merged first)
DONE_P = 0.6  # DONE at this probability completes the subgoal
BLOCKER_P = 0.6  # a blocker other than none at this probability is handled before any action
TEXT = 2500  # page characters Jev reads
ELEMENTS = 200  # elements Jev reads
OPTIONS = 250  # targets per question: Jev takes at most 255 options
RECENT = 6  # steps Jev reads
TARGETED = {"CLICK": ("CLICK", "click_target"), "TYPE": ("TYPE_TEXT", "type_text_target"),
            "SELECT": ("SELECT", "select_target"), "KEY": ("PRESS_KEY", "press_key_target"),
            "SCROLL_UP": ("SCROLL_UP", "scroll_up_target"), "SCROLL_DOWN": ("SCROLL_DOWN", "scroll_down_target"),
            "HOVER": ("HOVER", "click_target"), "UPLOAD": ("UPLOAD", "upload_target"),
            "SWITCH_TAB": ("SWITCH_TAB", "tab_target")}
CONTROLS = {"BACK": ("GO_BACK", "GO_BACK"), "WAIT": ("WAIT", "WAIT"),
            "ACCEPT_DIALOG": ("ACCEPT_DIALOG", "ACCEPT_DIALOG"), "DISMISS_DIALOG": ("DISMISS_DIALOG", "DISMISS_DIALOG")}


def words(text):
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


def request(page, subgoal, task, previous=None):
    """(state, questions, heads, controls) for one step. `task` holds the goal, the user's answers, and the recent
    steps; `previous` is the page Jev last saw, to mark the elements that are new since then."""
    elements, heads, controls = action_space(page["actions"])
    if previous:
        old = {text for _, text in views.lines(previous)}
        for element in elements:
            if views.line(element) not in old:
                element["new"] = True
    offered = {op: jev.OPERATIONS[op] for op, (_, head) in TARGETED.items() if heads.get(head)}
    offered |= {op: jev.OPERATIONS[op] for op, (_, kind) in CONTROLS.items() if kind in controls}
    offered |= {"DONE": jev.OPERATIONS["DONE"], "THINK": jev.OPERATIONS["THINK"]}
    goal = {"goal": task["goal"], "subgoal": subgoal["do"], "done_when": subgoal["done_when"]}
    questions = {"next": {"options": offered, "instructions": {**goal, "rules": jev.NEXT_RULE}}}
    for head in dict.fromkeys(TARGETED[op][1] for op in offered if op in TARGETED):
        premise = " or ".join(op for op in offered if TARGETED.get(op, (None, None))[1] == head)
        questions[head] = {"options": dict(list(criteria(heads[head]).items())[:OPTIONS]),
                           "instructions": {**goal, "operation": premise, "rules": jev.TARGET_RULE}}
    if "KEY" in offered:
        questions["key"] = {"options": jev.KEYS, "instructions": {**goal, "rules": jev.KEY_RULE}}
    questions["blocker"] = {"options": jev.BLOCKERS, "instructions": {**goal, "rules": jev.BLOCKER_RULE}}
    if task["recent"]:
        questions["effect"] = {"options": jev.EFFECTS, "instructions": {**goal, "rules": jev.EFFECT_RULE}}
    state = {"goal": task["goal"], "subgoal": {"do": subgoal["do"], "done_when": subgoal["done_when"]},
             "values": [f"{v['field']}: {v['text']}" for v in subgoal["values"]], "answers": task["answers"],
             "page": {"url": page["url"], "title": page["title"], "text": page["text"][:TEXT]},
             "elements": elements[:ELEMENTS], "recent": task["recent"][-RECENT:]}
    return state, questions, heads, controls


def margin(probabilities, labels):
    """The top option's lead over the next one with a different label (same-label duplicates merged)."""
    merged = {}
    for index, p in probabilities.items():
        label = labels.get(index, index)
        merged[label] = merged.get(label, 0.0) + p
    top = sorted(merged.values(), reverse=True) + [0.0]
    return top[0] - top[1]


def candidates(answers, questions, heads, controls, limit=5):
    """Jev's best (operation, element) pairs, most likely first, numbered from 1: what the planner may PICK."""
    found = []
    for op, p in answers["next"]["probabilities"].items():
        if op in CONTROLS:
            operation, kind = CONTROLS[op]
            found.append({"op": op, "operation": operation, "element": controls[kind]["label"], "p": p,
                          "action": controls[kind]})
        elif op in TARGETED and TARGETED[op][1] in answers:
            operation, head = TARGETED[op]
            ranked = sorted(answers[head]["probabilities"].items(), key=lambda kv: -kv[1])[:3]
            for index, q in ranked:
                found.append({"op": op, "operation": operation, "p": p * q, "action": heads[head][index],
                              "element": questions[head]["options"][index]["element"],
                              **({"key": answers["key"]["choice"]} if op == "KEY" else {})})
    found.sort(key=lambda c: -c["p"])
    return [{"n": n, **c} for n, c in enumerate(found[:limit], 1)]


def decide(answers, questions, heads, controls):
    """What Jev's answers mean: {"kind": "blocker" | "done" | "act" | "think", ...}, with the last step's effect, the
    element each target question chose ("targets"), and the top candidates for the planner."""
    nxt, blocker = answers["next"], answers["blocker"]
    op = nxt["choice"]
    step = {"op": op, "p": nxt["probabilities"][op], "blocker": blocker["choice"], "margin": None,
            "effect": answers["effect"]["choice"] if "effect" in answers else None,
            "targets": {head: heads[head][answers[head]["choice"]] for head in heads if head in answers},
            "candidates": candidates(answers, questions, heads, controls)}
    if blocker["choice"] != "none" and blocker["probabilities"][blocker["choice"]] >= BLOCKER_P:
        return {**step, "kind": "blocker"}
    if op == "DONE" and step["p"] >= DONE_P:
        return {**step, "kind": "done"}
    if op in CONTROLS and step["p"] >= ACT_P:
        operation, kind = CONTROLS[op]
        return {**step, "kind": "act", "operation": operation, "action": controls[kind]}
    if op in TARGETED and step["p"] >= ACT_P:
        operation, head = TARGETED[op]
        target = answers[head]
        labels = {i: heads[head][i].get("label", i) for i in target["probabilities"]}
        step["margin"] = margin(target["probabilities"], labels)
        if target["probabilities"][target["choice"]] >= TARGET_P or step["margin"] >= TARGET_MARGIN:
            chosen = {**step, "kind": "act", "operation": operation, "action": heads[head][target["choice"]]}
            if op == "KEY":
                chosen["key"] = answers["key"]["choice"]
            return chosen
    return {**step, "kind": "think"}


def value(action, values):
    """The subgoal value for a field: the one whose name shares at least half its words with the field's label, or
    the only one. None when that can't be told: Jev picks among the names, or the planner writes the text."""
    if not values:
        return None
    label, best, share = words(action.get("label", "")), None, 0.0
    for v in values:
        name = words(v["field"])
        overlap = len(name & label) / len(name) if name else 0.0
        if overlap > share:
            best, share = v, overlap
    if best and share >= 0.5:
        return best["text"]
    return values[0]["text"] if len(values) == 1 else None


def repeats(history, operation, label, url):
    """How often, among the last 20 steps, this operation on this element changed nothing on this URL path."""
    path = urlparse(url).path
    return sum(1 for h in history[-20:] if h.get("page_changed") is False and h.get("operation") == operation
               and h.get("action") == label and urlparse(h.get("url", "")).path == path)
