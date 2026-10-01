"""Learning from a finished run, by code: whether it is verified, the habit it records, whether it taught something
worth one reflection call, what that call reads, and the lint every learned line passes."""

import json
import re
from urllib.parse import urlparse

from . import planner, privacy, rules, skills

QUIET = {"WAIT", "SCROLL_UP", "SCROLL_DOWN"}  # incidental: never part of a habit
COUNTS = re.compile(r"\(\s*\d+\s*\)|\b\d+\s*(?:items?|left|in stock)\b", re.IGNORECASE)
MAX_LINE = 140
UNSAFE = re.compile(r"approv|payments?\b|\bpa(?:y|id)\b|\botp\b|\bcodes?\b|passwords?|\bpins?\b|\bcards?\b|\bcvv\b|"
                    r"\bskip\w*\b.{0,30}\b(?:checks?|confirm\w*|approv\w*|verif\w*)|without (?:asking|confirm\w*)|"
                    r"no need to (?:confirm|check|verif|approv)\w*|\bupi\b|net[\s-]?banking|\bwallets?\b|\bcod\b|"
                    r"cash on delivery", re.IGNORECASE)
TLDS = "com|in|io|net|org|co|app|dev|shop|store|xyz|info|me|ly|bike|online|site|link|to|ai|gg|tv|biz|us|uk"
LINK = re.compile(rf"(?:https?://|\bwww\.)([^/\s]+)|\b((?:[a-z0-9-]+\.)+(?:{TLDS}))\b", re.IGNORECASE)
HELD = re.compile(r"\[(?:profile|secret):", re.IGNORECASE)


def control_name(text):
    """A control's label as habits keep it: lowercased, without prices or counts ("Add (2) ₹40" → "add"). The label
    keeps the context a page adds to tell like controls apart, so "add · diet coke 300 ml" is not "add · sprite"."""
    return " ".join(COUNTS.sub(" ", rules.PRICE.sub(" ", str(text or ""))).lower().split())


def verified(state, judged=None):
    """Whether the run did what was asked: DONE passed its checks, or the site confirmed a commit. On the bench the
    judge decides."""
    if judged is not None:
        return bool(judged)
    return state["status"] == "done" or bool(state.get("orders"))


def where(url):
    parts = urlparse(url or "")
    return parts.netloc + parts.path


def habit_from(state):
    """The habit a verified run records, or None without a skill and a site: its plan, the steps that moved each
    subgoal forward, the confirmed order's cart, and the start page. A failed step, one that changed nothing, one a
    BACK undid (and the BACK; a BACK right after a BACK undoes the kept step before it), the same step again, an
    unconfirmed step, a captcha square, a wait, and a scroll are left out. A step keeps its control's label and
    the name of the subgoal value it typed, never the text."""
    if not state.get("skill") or not state.get("site"):
        return None
    plan = [{"do": g["do"], "done_when": g["done_when"], "kind": g["kind"], "values": g["values"]}
            for g in state["plan"]]
    steps, at, undoable = [], state.get("start") or "", False
    for entry in state["history"]:
        before, at = at, entry.get("url") or at
        op = entry["operation"]
        if op == "GO_BACK":
            if undoable:
                steps.pop()  # the kept step just before it, or the one before that after another BACK
            undoable = bool(undoable and steps)
            continue
        undoable = False
        if (entry.get("unconfirmed") or entry.get("effect") == "failed" or entry.get("page_changed") is False
                or entry.get("relayed") or op in QUIET or entry["subgoal"] >= len(plan)):
            continue
        text = str(entry.get("text") or "")
        secret = text.startswith("[secret:")
        field = None if secret else next((v["field"] for v in plan[entry["subgoal"]]["values"] if v["text"] == text),
                                         None)
        step = {"subgoal": entry["subgoal"], "op": op, "name": control_name(entry["action"]),
                "key": entry.get("key"), "value": field, "secret": secret, "commit": bool(entry.get("committed")),
                "url": where(before), "title": entry.get("title") or ""}
        if steps and {k: v for k, v in steps[-1].items() if k != "title"} == \
                {k: v for k, v in step.items() if k != "title"}:
            continue  # the same step again: a loop's repeat
        steps.append(step)
        undoable = True
    cart = next((o["cart"] for o in reversed(state.get("orders") or []) if o.get("cart")), [])
    return {"host": state["site"], "skill": state["skill"], "title": state["title"], "goal": state["goal"],
            "start": state.get("start") or "", "plan": plan, "steps": steps, "cart": cart}


def taught(state, ok, first):
    """Whether a run is worth one reflection call: the first run on its site, a run that wasn't verified, any
    escalation, loop, idle tick, or replay fallback, or a new answer from the user (a hidden code is none)."""
    answered = any(c["from"] == "user" and not c["text"].startswith("[secret:") for c in state["chat"])
    return bool(first or not ok or state["escalations"] or state["switched"] or state["loop_from"]
                or state.get("idle_ticks") or (state.get("replay") or {}).get("fallbacks") or answered)


def digest(state, memory, site):
    """What the reflection call reads. Page-derived text is already redacted; the call itself runs with every saved
    ID hidden."""
    failed = [{"op": h["operation"], "name": control_name(h["action"]), "changed": h.get("changed")}
              for h in state["history"] if h.get("effect") == "failed" or h.get("page_changed") is False]
    result = next((m["text"] for m in reversed(state["messages"]) if m.get("kind") == "result"), "")
    return {"goal": state["goal"], "skill": state.get("skill"), "site": site, "status": state["status"],
            "result": result, "plan": [g["do"] for g in state["plan"]], "notes": state["notes"][-10:],
            "feedback": state["feedback"][-5:], "failed_steps": failed[-10:], "chat": state["chat"][-12:],
            "saved_notes": memory.notes(site),
            "facts": [f["text"] for f in memory.facts() if f["kind"] in {"preference", "fact"}][:12]}


def private_words(memory, site):
    """Words a learned line may never hold: the parts of the user's IDs and addresses, but not the site's own name."""
    words = {w for value, _ in memory.hidden_values() for w in re.findall(r"[a-z0-9]{3,}", value.lower())}
    words |= {w for f in memory.facts("address") for w in re.findall(r"[a-z]{5,}", f["text"].lower())}
    return words - set(re.findall(r"[a-z0-9]+", site.lower()))


def note_ok(line, site, private):
    """A learned site note: short, holding nothing of the user's (no placeholder, no run of 4 digits, no word of
    their IDs or addresses), nothing about approvals, payments, codes, passwords, PINs, or cards, no skipped check,
    and no link off the site."""
    hosts = [skills.bare((url or bare).lower().rstrip(".,;:)!?")) for url, bare in LINK.findall(line)]
    return (0 < len(line) <= MAX_LINE and not HELD.search(line) and not re.search(r"\d{4}", line)
            and not set(re.findall(r"[a-z0-9]+", line.lower())) & private and not UNSAFE.search(line)
            and all(host == site or host.endswith("." + site) for host in hosts))


def guess_ok(text):
    """A guess about the user: short, no placeholder, no run of 6 digits, and storable."""
    return (0 < len(text) <= MAX_LINE and not HELD.search(text) and not re.search(r"\d{6}", text)
            and privacy.storable(text))


def after(state, memory, *, judged=None, reflect=None, save=False, actor="agent"):
    """Learn from a finished run. A verified run with a skill and a site records its habit (every saved ID hidden;
    never one holding a card, Aadhaar, or PAN number). A run that taught something gets one reflection call: its
    note lines are drafted for the user's Save and its guesses returned for their Remember; `save` (the bench's
    learn mode) keeps both at once. Returns {"host", "habit", "notes": [(id, line)], "guesses": [{"text", "tags"}]}."""
    site = state.get("site") or ""
    found = {"host": site, "habit": None, "notes": [], "guesses": []}
    if not site:
        return found
    ok = verified(state, judged)
    if state.get("replay"):  # three replays in a row that didn't verify retire the habit
        memory.replayed(state["replay"]["habit"], not state["replay"]["fallbacks"], ok, actor=actor)
    first = not memory.habits(hosts=[site], limit=1) and not memory.notes(site)
    habit = habit_from(state) if ok else None
    if habit:
        hidden = memory.hide(json.dumps(habit, ensure_ascii=False))
        if not privacy.secret_number(hidden):
            found["habit"] = memory.record_habit(json.loads(hidden), actor=actor)
    if not taught(state, ok, first):
        return found
    lines, guesses = (reflect or planner.reflect)(digest(state, memory, site))
    private = private_words(memory, site)
    lines = [line for line in lines if note_ok(line, site, private)]
    guesses = [{"text": g["text"], "tags": sorted({*g["tags"], *(t for t in (state.get("skill"), site) if t)})}
               for g in guesses if guess_ok(g["text"])]
    if lines:
        ids = memory.draft_notes(site, lines, task=state["goal"])
        if save:
            memory.decide_notes(ids, save=True, actor=actor)
        found["notes"] = list(zip(ids, lines))
    if not save:
        found["guesses"] = guesses
        return found
    for guess in guesses:
        memory.remember("preference", guess["text"], tags=guess["tags"], source="learned from a run",
                        task=state["goal"], actor=actor)
    return found
