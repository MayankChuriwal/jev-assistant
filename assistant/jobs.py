"""Text-model jobs besides field values. Every answer is validated before the agent acts on it."""

import re
from urllib.parse import urlparse

from .model import text_json
from .questions import (
    CHAT,
    COMPOSE,
    CONFIRM_CARD,
    HELP,
    HINT,
    LOOK,
    LOOK_CLOSER,
    NOTE_FACTS,
    OUTCOME,
    REMEMBER,
    RESULT,
    START,
)
from .rules import verbatim

KINDS = {"text", "secret", "choice"}
RUNGS = {"show", "guide", "device", "stop"}
OUTCOMES = {"success", "failed", "pending", "unclear"}


def invalid(job):
    return ValueError(f"The text model returned an invalid {job}; nothing executed.")


def view(page):
    return {"url": page["url"], "title": page["title"], "text": page["text"][:6000]}


def words(out, key, limit):
    value = out.get(key) if isinstance(out, dict) else None
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def start(goal):
    out, meta = text_json(START, {"goal": goal})
    url, title = out.get("start_url"), words(out, "title", 60)
    parsed = urlparse(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme not in {"http", "https"} or not parsed.netloc or not title:
        raise invalid("start")
    return url, title, meta


def compose(context, page, elements):
    out, meta = text_json(COMPOSE, {"context": context, "page": view(page), "elements": elements})
    kind, text, options = out.get("kind"), words(out, "text", 4096), out.get("options") or []
    indices = {e["index"] for e in elements}
    if kind not in KINDS or not text or not isinstance(options, list):
        raise invalid("question")
    clean = []
    for option in options[:10]:
        label = words(option, "label", 20)
        element = str(option.get("element") or "") if isinstance(option, dict) else ""
        if not label or (element and element not in indices):
            raise invalid("question")
        clean.append({"label": label, "description": str(option.get("description") or "")[:72], "element": element})
    if kind == "choice" and len(clean) < 2:
        raise invalid("question")
    return {"kind": kind, "text": text, "options": clean if kind == "choice" else []}, meta


MONEY = re.compile(r"[₹$€£]|\bRs\.?|\bINR\b|\bUSD\b")


def confirm_card(context, page, commit, elements=()):
    payload = {"context": context, "page": view(page), "commit": commit, "elements": list(elements)}
    out, meta = text_json(CONFIRM_CARD, payload)
    action, amount, lines = words(out, "action_label", 40), out.get("amount", ""), out.get("lines")
    element = str(out.get("element") or "")
    if element and element not in {e["index"] for e in elements}:
        raise invalid("confirmation card")
    if not action or not isinstance(amount, str) or not isinstance(lines, list) or not all(
            isinstance(line, str) for line in lines):
        raise invalid("confirmation card")
    amount = amount.strip()
    if amount and not verbatim(amount, page["text"]):
        raise invalid("confirmation card")  # the model may not invent a total
    if not amount and MONEY.search(commit):
        raise invalid("confirmation card")  # a paying commit always shows its amount
    lines = [x.strip() for x in lines if x.strip()][:8]
    return {"action_label": action, "amount": amount, "lines": lines, "element": element}, meta


def outcome(context, page, commit):
    out, meta = text_json(OUTCOME, {"context": context, "page": view(page), "commit": commit})
    result, evidence = out.get("outcome"), out.get("evidence", "")
    if result not in OUTCOMES or not isinstance(evidence, str):
        raise invalid("outcome")
    if result in {"success", "failed"} and not verbatim(evidence, page["text"]):
        result = "unclear"  # a claimed success or failure needs the page's own words
    return result, evidence.strip(), meta


def result(context, page):
    out, meta = text_json(RESULT, {"context": context, "page": view(page)})
    text, evidence = words(out, "text", 1000), out.get("evidence", "")
    if not text or not isinstance(evidence, str):
        raise invalid("result")
    return text, evidence.strip() if verbatim(evidence, page["text"]) else "", meta


def note(context, page):
    out, meta = text_json(NOTE_FACTS, {"context": context, "page": view(page)})
    facts = out.get("facts")
    if not isinstance(facts, list) or not all(isinstance(fact, str) for fact in facts):
        raise invalid("note")
    return [fact.strip()[:200] for fact in facts if fact.strip()][:5], meta


def unblock(context, page, elements, must_stop=False):
    payload = {"context": context, "page": view(page), "elements": elements, "must_stop": must_stop}
    out, meta = text_json(HELP, payload)
    rung, text, crop = out.get("rung"), words(out, "text", 4096), str(out.get("crop_element") or "")
    if rung not in RUNGS or not text or (crop and crop not in {e["index"] for e in elements}):
        raise invalid("help message")
    return {"rung": "stop" if must_stop else rung, "text": text, "crop_element": crop}, meta


def hint(context, page, text="", image=None):
    payload = {"context": context, "page": view(page), "user_message": text}
    out, meta = text_json(HINT, payload, images=[image] if image else ())
    value = words(out, "hint", 500)
    if not value:
        raise invalid("hint")
    return value, meta


def remember(question, answer):
    out, meta = text_json(REMEMBER, {"question": question, "answer": answer})
    value = words(out, "preference", 200)
    if not value:
        raise invalid("preference")
    return value, meta


def chat(message, tasks):
    out, meta = text_json(CHAT, {"message": message, "tasks": tasks})
    text = words(out, "text", 1000)
    if not text:
        raise invalid("reply")
    return text, meta


def look(context, page, purpose, image, cells):
    """The grid cell holding what the task needs, and its name as the page shows it; None when nothing fits."""
    payload = {"context": context, "page": view(page), "purpose": purpose, "cells": sorted(cells)}
    out, meta = text_json(LOOK, payload, images=[image])
    if out.get("cell") is None:
        return None, meta
    label = words(out, "label", 80)
    if out["cell"] not in cells or not label or not isinstance(out.get("commits", False), bool):
        raise invalid("look")
    return {"cell": out["cell"], "label": label, "text": words(out, "text", 120) or "",
            "commits": out.get("commits") is True}, meta


def look_closer(label, image, cells):
    out, meta = text_json(LOOK_CLOSER, {"control": label, "cells": sorted(cells)}, images=[image])
    if out.get("cell") not in cells:
        raise invalid("look")
    return out["cell"], meta
