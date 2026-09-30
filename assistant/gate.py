"""Commits: whether an action commits, the facts its approval shows, asking or using a pre-authorisation, binding
the yes to its control, and watching what the site does next. The facts come from the page by code; Jev only
confirms, and it can never clear a control the commit rule matches outright."""

import re
import time

from . import jev, planner, rules, skills
from .browser import StalePage
from .errors import Halt, Retry

COMMIT_OPS = {"CLICK", "PRESS_KEY", "ACCEPT_DIALOG", "LOOK"}
TOTAL = re.compile(r"\b(total|to pay|payable|amount due)\b", re.IGNORECASE)
PAYABLE = re.compile(r"\b(to pay|grand total|order total|bill total|amount payable|total payable|net payable|"
                     r"amount due)\b", re.IGNORECASE)
NOT_TOTAL = re.compile(r"\b(item total|sub ?total|saving|saved|discount|off)\b", re.IGNORECASE)
DELIVERING = re.compile(r"\b(?:delivering to|deliver to|delivery address)\s*:?\s*([^\n]{8,160})", re.IGNORECASE)
FEE = re.compile(r"^[^\n₹]{0,20}?((?:small cart|delivery|handling|platform|packaging|convenience|surge|late night|"
                 r"rain|service)\s+(?:fee|charges?)[^\n₹]{0,10}₹\s?[\d,]+(?:\.\d+)?)", re.IGNORECASE | re.MULTILINE)
ITEMS = re.compile(r"\b(\d+) items?\b", re.IGNORECASE)
PROCEED = re.compile(r"\bproceed to (pay|checkout|buy)\b|\bgo to (payment|checkout)\b", re.IGNORECASE)
YES = re.compile(r"\b(yes|yeah|yep|yup|ok|okay|sure|go ahead|book it|confirm|do it|proceed|haan)\b", re.IGNORECASE)
NOT_YES = re.compile(r"\?|\b(no|not|don'?t|wait|but|instead|cheaper|change|stop|cancel|hold)\b", re.IGNORECASE)
CAP = re.compile(r"\b(?:under|below|max(?:imum)?|up ?to|within|budget|less than|not more than)\s*(?:of\s*)?"
                 r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
FIELDS = ["what", "where", "option", "payment", "total"]  # approval lines for a task with no skill


def cap(text):
    """The spending cap the user's own words set ("under ₹300", "max Rs 500"), or None."""
    match = CAP.search(text or "")
    return float(match[1].replace(",", "")) if match else None


def amount(text):
    """The number in the first price of `text` ("To pay ₹1,234.50" → 1234.5), or None."""
    price = rules.PRICE.search(text or "")
    return float(re.search(r"\d[\d,]*(?:\.\d+)?", price[0])[0].replace(",", "")) if price else None


def own_words(action):
    """A control's own name and the words it shows, without the context added to tell it apart from others."""
    return " ".join(filter(None, [action.get("name", action.get("label") or ""), action.get("shown")]))


def commit_label(w, operation, action, page, key=None, subgoal=None):
    """The words the approval names when this action commits, else None. The commit rule decides; Jev's final-step
    check clears only an ambiguous match (a checkout step, a payment method named “…Pay”) and, inside a commit
    subgoal, confirms a control the rule missed."""
    if operation not in COMMIT_OPS or (operation == "PRESS_KEY" and key not in {"Enter", "Space"}) or \
            action.get("hover"):
        return None
    focused = w.state["browser"].focused_label() if operation == "PRESS_KEY" and action.get("node") is None else ""
    label = rules.commit_label(operation, action, page, key, focused)
    if not label and operation == "PRESS_KEY" and key == "Enter" and action.get("node") is None:
        forms = [a for a in page["actions"] if a.get("submit") and rules.COMMIT.search(a["submit"])]
        field = next((a for a in forms if a["label"] == focused), forms[0] if forms else None)
        label = field["submit"] if field else None  # Enter submits the focused field's form
    goal = (subgoal or {}).get("do") or w.state["goal"]
    if label:
        words = own_words(action) or label
        ambiguous = operation == "CLICK" and (PROCEED.search(words) or (rules.METHOD.search(words)
                                                                         and not rules.PRICE.search(words)))
        if ambiguous:
            verdict, sure = jev.final(page, label, goal)
            if verdict == "step" and sure >= 0.8:
                return None
        return label
    if (subgoal or {}).get("kind") == "commit":
        name = action.get("label") or action.get("text") or operation.title()
        verdict, sure = jev.final(page, name, goal)
        if verdict == "final" and sure >= 0.5:
            return name
    return None


def facts(page, recent):
    """What an approval must show, read by code from the page and the pages just before it: the payable total,
    fees, the delivery address line, the item count, and the values typed into the form."""
    seen = [page["text"], *reversed(recent[-5:])]
    lines, total = page["text"].splitlines(), None
    for kind in (PAYABLE, TOTAL):  # "To pay" first; a plain "total" only when nothing says what is payable
        found = [(rules.PRICE.findall(line) or rules.PRICE.findall(" ".join(lines[i + 1:i + 2])))[-1:]
                 for i, line in enumerate(lines) if kind.search(line) and not NOT_TOTAL.search(line)]
        found = [f[0] for f in found if f]
        if found:
            total = found[-1]
            break
    typed = [f"{a['label'].rstrip(':')}: {a['value']}" for a in page["actions"]
             if a.get("kind") == "fill" and a.get("role") != "searchbox" and "search" not in a["label"].lower()
             and a.get("value") and not str(a["value"]).startswith("[")]
    return {"total": total, "fees": list(dict.fromkeys(f.strip() for t in seen for f in FEE.findall(t))),
            "address": next((m.strip() for t in seen for m in DELIVERING.findall(t)), None),
            "items": next((int(n) for n in ITEMS.findall(page["text"])), 0), "typed": list(dict.fromkeys(typed))}


def summary(w, page, found):
    """The approval's lines: the planner's one line per skill field, then every fact it left out, from the page."""
    s = w.state
    skill = w.skill()
    payload = {"goal": s["goal"], "fields": (skill or {}).get("summary") or FIELDS, "chat": s["chat"][-10:],
               "facts": found, "page": {"url": page["url"], "title": page["title"], "text": page["text"][:3000]},
               "pages_before": [t[:1500] for t in s["recent"][-3:]]}
    lines = [re.sub(r"^(ordered|booked|placed|paid|requested)\s+", "", line, flags=re.IGNORECASE)
             for line in planner.summary(payload)]  # nothing has happened until the user says yes
    missing = [x for x in rules.PRICE.findall(" ".join(lines)) if not rules.amount_shown(x, page["text"])]
    if missing:
        raise Retry(f"The page doesn't show {', '.join(missing)}. Copy amounts exactly as the page shows them.")
    joined = " ".join(lines).lower()
    extra = []
    if found["total"] and not rules.amount_shown(found["total"], " ".join(lines)):
        extra.append(f"Total: {found['total']}")
    extra += [fee for fee in found["fees"] if fee.split("₹")[0].strip().lower() not in joined]
    if found["address"] and re.sub(r"\W", "", found["address"])[:12].lower() not in re.sub(r"\W", "", joined):
        extra.append(f"Delivering to: {found['address']}")
    if found["items"] > 1 and f"{found['items']} item" not in joined:
        extra.append(f"{found['items']} items in the cart")  # the page's own count: never fewer
    extra += [value for value in found["typed"] if value.split(": ", 1)[-1].lower() not in joined]
    return list(dict.fromkeys(line.strip() for line in lines + extra if line.strip()))


def matches(approval, action, label):
    """Whether this is the action the user approved: the same control, or the same words when none was named."""
    if approval.get("look") or action.get("point"):  # a spot found by sight matches only the spot approved
        return bool(approval.get("look")) and action.get("point") == approval["look"]["point"]
    if approval.get("target"):
        return (action.get("frame"), action.get("node")) == approval["target"]
    return bool(label) and rules.overlap(label, approval["action_label"])


def bind(state, action, label, total, page, by):
    """The approval holds this control, host, and amount; it lapses when any of them changes."""
    target = (action.get("frame"), action["node"]) if action.get("node") is not None else None
    state["approval"] = {"action_label": label, "amount": total or "", "host": rules.host(page["url"]),
                         "step": len(state["history"]), "target": target, "by": by}
    if action.get("point"):
        state["approval"]["look"] = {"point": action["point"]}


def preauthorised(state, found, page):
    """Whether the user's own words cover this commit: on the task's site, and within their cap when it costs
    money. With no cap, only a commit whose page shows no amount at all (a form, a message)."""
    here, site = skills.bare(rules.host(page["url"]).lower()), state["site"]
    if not state["preauth"] or not site or not (here == site or here.endswith("." + site)):
        return False
    if found["total"] is None:
        return not rules.PRICE.search(page["text"])
    return state["cap"] is not None and amount(found["total"]) <= state["cap"]


def approved(w, page, action, label, subgoal):
    """True when this commit may run now; otherwise ask. An earlier yes counts only for this control, host, and
    amount. A fresh yes reads the page again first (StalePage); anything else is feedback (Retry)."""
    s = w.state
    approval = s["approval"]
    if approval and rules.approval_valid(approval, page, len(s["history"])) and matches(approval, action, label):
        if not s["browser"].fresh(page, action):
            raise StalePage("Page changed since the approval. Choose again.")
        return True
    if s["commit"]:
        raise Retry("An earlier committed action is still being checked; WAIT for it first.")
    found = facts(page, s["recent"])
    lines = summary(w, page, found) or [label]
    chat = " / ".join(c["text"] for c in s["chat"])
    matched = jev.matches(page, s["goal"], chat, "\n".join(lines)) == "yes"
    if not matched and s["mismatches"] < 2:
        s["mismatches"] += 1  # twice at most: then the user sees the summary and decides
        raise Retry(f"The order doesn't match what the user asked for ({s['goal']}): keep the product, brand, "
                    "quantity, and place they named, and never remove items they added. Then do the final step again.")
    if w.moved(page):
        raise StalePage("The page changed while the approval was prepared. Choose again.")
    w.steady(page)
    total = found["total"] or next(iter(rules.PRICE.findall(" ".join(lines))[-1:]), "")
    if matched and preauthorised(s, found, page):
        if w.bench:
            s["bench_preauth"] = True  # a bench run logs what it would have done, then asks like any other
        else:
            w.say("Placing: " + " · ".join(lines))
            bind(s, action, label, total, page, "preauth")
            return True
    host = rules.host(page["url"]).removeprefix("www.")
    question = "\n".join([lines[0], *(f"• {line}" for line in lines[1:])]) + \
        f"\n\nI'll press “{label}” on {host}. Reply yes to go ahead, or tell me what to change."
    reply = w.ask({"kind": "text", "text": question, "approval": True})
    answer = str(reply.get("text", "")).strip()
    s["chat"] += [{"from": "agent", "text": question}, {"from": "user", "text": f"“{answer}”"}]
    if YES.search(answer) and not NOT_YES.search(answer) and jev.approval(question, answer) == "yes":
        bind(s, action, label, total, page, "user")
        raise StalePage("Approved. Reading the page again before acting.")
    raise Retry(f"The user did not approve: “{answer}”. Nothing was committed; do what they asked.")


def watch(w, label, goal):
    """After a commit: wait (no planner) while the site works; the outcome becomes a note for the planner."""
    s = w.state
    since, told, unclear = s["commit"]["since"], False, 0
    while True:
        verdict = jev.after_commit(s["page"], goal, label)
        if verdict == "processing":
            if w.elapsed() - since >= w.processing_ms:
                s["commit"] = None
                raise Halt("blocked", f"{label} went through, but after {w.processing_ms // 60000} minutes the site "
                                      "still shows it in progress. I stopped watching; check the site or its app "
                                      "for updates.")
            if not told:
                told = True
                w.say(f"{label}: the site is still working on it. I'll tell you as soon as it's confirmed.")
        elif verdict == "unclear" and unclear == 0:
            unclear += 1
        else:
            break
        w.stop_if_interrupted()
        time.sleep(w.outcome_pause)
        w.observe()
    s["commit"] = None
    if verdict == "next_step":
        s["notes"].append(f"{label} was only a step; nothing is ordered or booked yet. The final control needs its "
                          "own approval.")
    elif verdict == "confirmed":
        s["done"].append(label)
        s["notes"].append(f"Done: {label}, confirmed by the site. Report every detail the page shows.")
    elif verdict == "failed":
        s["notes"].append(f"{label} failed; trying again needs a new approval.")
    else:
        raise Halt("blocked", f"I can't tell whether {label} went through, so I stopped rather than risk doing it "
                              "twice. Please check the site or its app before trying again.")
    s["reason"] = "commit"
