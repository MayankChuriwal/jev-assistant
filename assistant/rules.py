"""Pure checks the agent applies before acting: commits, verbatim evidence, answer formats, approvals."""

import re
from urllib.parse import urlparse

COMMIT = re.compile(
    r"\b(pay|place order|buy|purchase|book now|confirm (order|booking|ride|payment|purchase)"
    r"|request (ride|uber\w*|trip)|cancel (order|booking|subscription|ride)|delete"
    r"|submit (complaint|dispute|claim|request))\b",
    re.IGNORECASE,
)
APPROVAL_STEPS = 20
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def host(url):
    return urlparse(url).netloc


def commit_label(operation, action, page, key=None, focused=""):
    """The words that make this action a commit, or None. `focused` names the control a page-level key reaches."""
    if operation in {"CLICK", "DOUBLE_CLICK"}:
        label = action.get("label", "")
    elif operation == "PRESS_KEY" and key in {"Enter", "Space"}:
        # Enter submits the field's form; on the page, Enter or Space presses whatever has focus.
        label = (action.get("submit", "") if key == "Enter" else "") or (focused if action.get("node") is None else "")
    elif operation == "ACCEPT_DIALOG":
        label = page.get("text", "")
    else:
        return None
    return label if COMMIT.search(label or "") else None


STOP_WORDS = {"the", "your", "and", "for", "now", "with", "this", "all", "you"}


def words(text):
    return {w for w in re.findall(r"[a-z]{3,}", str(text).lower())} - STOP_WORDS


def overlap(label, approved):
    """Whether an action's label names the same commit as the approved one."""
    return bool(words(label) & words(approved))


def normal(text):
    return " ".join(str(text).split())


def verbatim(fragment, text):
    """True when `fragment` occurs in `text`, ignoring whitespace differences."""
    fragment = normal(fragment)
    return bool(fragment) and fragment in normal(text)


def approval_valid(approval, page, steps):
    if not approval or approval["host"] != host(page["url"]) or steps - approval["step"] >= APPROVAL_STEPS:
        return False
    return not approval["amount"] or verbatim(approval["amount"], page["text"])


def attempt_limit(key):
    return 2 if key and key.endswith(".cvv") else 3


def numeric(action):
    constraints = action.get("constraints") or {}
    return constraints.get("inputmode") in {"numeric", "decimal"} or constraints.get("type") == "number"


def clean(value, action):
    """The answer as it should be typed: trimmed, and without spaces or dashes in codes and number fields."""
    value = str(value).strip()
    return re.sub(r"[\s-]", "", value) if action.get("group") or numeric(action) else value


def format_error(value, action):
    """Why `value` cannot go into this field, or None. Checked before typing, so a typo costs no attempt."""
    constraints = action.get("constraints") or {}
    group = action.get("group")
    if group:
        if len(value) != len(group):
            return f"This code needs exactly {len(group)} characters."
        if numeric(action) and not value.isdigit():
            return f"This code needs {len(group)} digits."
    if numeric(action) and not re.fullmatch(r"[\d.,]+", value):
        return "This field takes numbers only."
    kind = constraints.get("type")
    if kind == "tel" and not re.fullmatch(r"[\d\s+()-]{6,}", value):
        return "That doesn't look like a phone number."
    if kind == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        return "That doesn't look like an email address."
    if kind == "date" and not DATE.fullmatch(value):
        return "Dates go in as YYYY-MM-DD."
    if constraints.get("maxlength") and not group and len(value) > constraints["maxlength"]:  # a box holds one
        return f"This field takes at most {constraints['maxlength']} characters."
    if constraints.get("pattern"):
        try:
            if not re.fullmatch(constraints["pattern"], value):
                return "That doesn't match the format this field expects."
        except re.error:
            pass  # JavaScript-only pattern syntax: let the site judge
    return None


def grid(region, columns, rows):
    """Labelled cells over a region of the viewport: A1 is top-left; letters run across and numbers down."""
    width, height = region["w"] / columns, region["h"] / rows
    return {f"{chr(65 + c)}{r + 1}": {"x": region["x"] + c * width, "y": region["y"] + r * height, "w": width,
                                      "h": height}
            for r in range(rows) for c in range(columns)}


def centre(cell):
    return cell["x"] + cell["w"] / 2, cell["y"] + cell["h"] / 2
