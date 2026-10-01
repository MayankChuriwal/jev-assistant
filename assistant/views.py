"""What mini sees of a page: a short numbered element list (new elements starred), trimmed text, and one line
saying what the last step changed. Also the page as the loop guard compares it."""

import hashlib
import re
from urllib.parse import urlparse

from .model import action_space

ELEMENTS = 120
TEXT = 3000
QUOTED = re.compile(r"[\"“][^\"”]*[\"”]")  # a rotating placeholder: Search for "Atta"
NUMBER = re.compile(r"\d+")  # a counter, a timer, a carousel's "1 of 3"


def lines(page):
    """(index, line) for each element, in page order."""
    elements, _, _ = action_space(page["actions"])
    return [(e["index"], line(e)) for e in elements]


def line(element):
    state = [k for k in ("checked", "selected", "expanded") if element.get(k) in (True, "true")]
    value = f' = "{element["value"][:60]}"' if element.get("value") else ""
    extra = f" ({', '.join(state)})" if state else ""
    return f'{element.get("role", "")} "{element["label"][:100]}"{value}{extra}'


def view(page, previous):
    old = {text for _, text in lines(previous)} if previous else set()
    shown = [f"{'*' if previous and text not in old else ''}[{index}] {text}"
             for index, text in lines(page)[:ELEMENTS]]
    return {"url": page["url"], "title": page["title"], "text": page["text"][:TEXT], "elements": shown,
            "changed": change(previous, page)}


def change(before, after):
    if before is None:
        return "first look at this page"
    parts = []
    if before["url"] != after["url"]:
        parts.append(f"now at {after['url']}")
    old, new = [t for _, t in lines(before)], [t for _, t in lines(after)]
    added = [t for t in new if t not in set(old)][:8]
    removed = [t for t in old if t not in set(new)][:8]
    if added:
        parts.append("new: " + "; ".join(added))
    if removed:
        parts.append("gone: " + "; ".join(removed))
    seen = set(before["text"].splitlines())
    fresh = [t.strip() for t in after["text"].splitlines() if t.strip() and t not in seen][:5]
    if fresh:
        parts.append("new text: " + " | ".join(fresh))
    return "; ".join(parts) or "nothing changed"


def calm(text):
    """Text as the loop guard compares it: numbers and quoted words dropped, so what ticks or rotates stays the same."""
    return " ".join(NUMBER.sub("#", QUOTED.sub("“”", str(text or ""))).lower().split())


def signature(page):
    """The page as the loop guard tells pages apart: its URL path and its elements (role, label, value, state), not
    its text, and with counters and rotating placeholders ignored. Two reads with the same signature offer the same
    controls in the same state, however much the text around them changed."""
    elements, _, _ = action_space(page["actions"])
    shown = sorted({line({**e, "label": calm(e["label"]), "value": calm(e.get("value"))}) for e in elements})
    return hashlib.sha256("\n".join([urlparse(page["url"]).path, *shown]).encode()).hexdigest()[:16]
