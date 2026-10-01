"""Commits: whether an action commits, the facts its approval shows, asking or using a pre-authorisation, binding
the yes to its control, and watching what the site does next. The facts come from the page by code; Jev only
confirms, and it can never clear a control the commit rule matches outright."""

import re
import time
import unicodedata

from . import jev, planner, rules, skills
from .browser import StalePage
from .errors import Halt, Retry

COMMIT_OPS = {"CLICK", "DOUBLE_CLICK", "PRESS_KEY", "ACCEPT_DIALOG", "LOOK"}
CLICKS = {"CLICK", "DOUBLE_CLICK"}
TOTAL = re.compile(r"\b(total|to pay|to be paid|you pay|you will pay|payable|amount due|net amount|final amount)\b",
                   re.IGNORECASE)
PAYABLE = re.compile(r"\b(to pay|to be paid|you pay|you will pay|grand total|order total|bill total|amount payable|"
                     r"total payable|net payable|payable amount|net amount|final amount|amount due)\b", re.IGNORECASE)
# A line about what is saved, given back, or still to add is not the payable total.
REMARKS = r"\b(?:save|saving|savings|saved|cashback|rewards?|coupons?|discount|off|free)\b|\badd\b.{0,30}\bmore\b"
REMARK_WORDS = re.compile(REMARKS, re.IGNORECASE)
NOT_TOTAL = re.compile(r"\b(?:items? total|sub ?total)\b|" + REMARKS, re.IGNORECASE)
ASIDE = re.compile(r"\([^()]*\)")
REMARK = re.compile(NOT_TOTAL.pattern + r"|\bwas\b|\bmrp\b", re.IGNORECASE)  # "(you save ₹200)", "(was ₹1,499)"
NUMBER = r"\d(?:[\d,]*\d)?(?:\.\d+)?"
RUPEE = re.compile(r"(?:₹|\bRs\.?|\bINR\b)\s?(" + NUMBER + ")", re.IGNORECASE)
SYMBOLS = "[" + re.escape("".join(c for c in map(chr, range(0x10000)) if unicodedata.category(c) == "Sc")) + "]"
# A suffix after a number: ₹1.5 L · ₹15 K · ₹2 crore
TOKEN = r"(?:thousands?|lakhs?|lacs?|crores?|cr|millions?|billions?|mn|bn|[KLM])(?![A-Za-z])"
SUFFIX = r"(?:\s*" + TOKEN + ")?"
PRICES = re.compile("(?:" + rules.PRICE.pattern + r"|\bINR\s?" + NUMBER + ")" + SUFFIX, re.IGNORECASE)  # INR too
WORDS = re.compile(r"\b(?:total|to pay|you pay|you will pay|pay|payable|bill|billed|charged|paid|fees?|fare|price|cost|"
                   r"charges?|rate|amount|due|grand)\b", re.IGNORECASE)  # words that put a price on their line
CURRENCY = re.compile(
    SYMBOLS + r"|\bRs(?![A-Za-z])|\b(?:INR|USD|EUR|GBP|AED|SGD|Rp)(?![A-Za-z])|(?-i:\bRM(?![A-Za-z]))|"
    r"\b(?:rupees?|euros?|dollars?|pounds?|pesos?|baht|riyals?|dirhams?)(?![A-Za-z])|रुपये|रुपया|zł|"
    r"(?-i:\b(?!ETA\b)[A-Z]{3}\s*\d|\d\s*(?!ETA\b)[A-Z]{3}\b)|\d\s*/-", re.IGNORECASE)
MARK_WORD = re.compile(r"Rs|INR|USD|EUR|GBP|AED|SGD|RM|Rp|rupees?|euros?|dollars?|रुपये|रुपया", re.IGNORECASE)
MARK_END = re.compile("(?:" + SYMBOLS + r"|\b(?:Rs|INR|USD|EUR|GBP|AED|SGD|RM|Rp)\.?)\s?$", re.IGNORECASE)
AFTER_MARK = re.compile(r"\s*(?:INR|USD|EUR|GBP|AED|SGD|rupees?|euros?|dollars?|pounds?|pesos?|baht|riyals?|dirhams?|"
                        r"रुपये|रुपया|/-)(?![A-Za-z])", re.IGNORECASE)  # a currency written after its number
READING = re.compile(r"(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s*(" + TOKEN + "))?", re.IGNORECASE)
SCALES = {"k": 1e3, "thousand": 1e3, "thousands": 1e3, "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
          "m": 1e6, "mn": 1e6, "million": 1e6, "millions": 1e6, "cr": 1e7, "crore": 1e7, "crores": 1e7, "bn": 1e9,
          "billion": 1e9, "billions": 1e9}
LETTERS = {"k", "l", "m"}  # the suffixes that can also be a unit: litres, metres
UNIT = re.compile(r"\s?(?:ml|ltrs?|litres?|liters?|l|g|gms?|grams?|kgs?|mg|oz|lbs?|pcs?|pieces?|packs?|units?|cm|mm|"
                  r"km|m|%)(?![A-Za-z])", re.IGNORECASE)  # "300 ml" is a size, not an amount
TICKING = re.compile(  # a clock or a countdown changes by itself: these tokens, and their number, are not money
    r"\b\d{1,2}:\d{2}(?::\d{2})?\b|\b(?<![\d,.])\d+(?:\.\d+)?\s*(?:mins?|minutes?|secs?|seconds?|hrs?|hours?|left)\b|"
    r"\b(?:left|expires?|expiring|eta|arriv\w+|by)\b", re.IGNORECASE)
EXEMPT = re.compile(r"\b(?:items? total|sub ?total|total (?:savings?|saved|discount|cashback|rewards?|coupons?))\b",
                    re.IGNORECASE)  # these lines name a part of the bill, not what is payable
DELIVERING = re.compile(r"\b(?:delivering to|deliver to|delivery address)\s*:?\s*([^\n]{8,160})", re.IGNORECASE)
FEE = re.compile(r"^[^\n₹]{0,20}?((?:small cart|delivery|handling|platform|packaging|convenience|surge|late night|"
                 r"rain|service)\s+(?:fee|charges?)[^\n₹]{0,10}₹\s?[\d,]+(?:\.\d+)?)", re.IGNORECASE | re.MULTILINE)
ITEMS = re.compile(r"\b(\d+) items?\b", re.IGNORECASE)
PROCEED = re.compile(r"\bproceed to (pay|checkout|buy)\b|\bgo to (payment|checkout)\b", re.IGNORECASE)
YES = re.compile(r"\b(yes|yeah|yep|yup|ok|okay|sure|go ahead|book it|confirm|do it|proceed|haan)\b", re.IGNORECASE)
NOT_YES = re.compile(r"\?|\b(no|not|don'?t|wait|but|instead|cheaper|change|stop|cancel|hold)\b", re.IGNORECASE)
CAP_WORD = r"\b(?:under|below|max(?:imum)?|up ?to|within|budget|less than|not more than)\s*(?:of\s*)?"
CAP_BEFORE = re.compile(CAP_WORD + r"(?:₹|rs\.?|inr\b)\s*(" + NUMBER + ")", re.IGNORECASE)  # "under ₹300"
CAP_AFTER = re.compile(CAP_WORD + "(" + NUMBER + r")\s*(?:inr\b|rupees?\b|rs\b|₹)", re.IGNORECASE)  # "300 rupees"
FIELDS = ["what", "where", "option", "payment", "total"]  # approval lines for a task with no skill


def cap(text):
    """The spending cap the user's own words set ("under ₹300", "max Rs 500", "budget of 500 INR"), or None. The
    number must carry a rupee marker: "within 500 m" or "under 30 minutes" is not money."""
    found = [m for rule in (CAP_BEFORE, CAP_AFTER) for m in rule.finditer(text or "")]
    first = min(found, key=lambda m: m.start(), default=None)
    return float(first[1].replace(",", "")) if first else None


def readings(line):
    """(value, quantity) for every number on the line, after the ticking tokens (a clock, "4 mins", "left", "by")
    are cut out. An attached suffix (lakh, crore, million, billion, Mn, bn, Cr, or K, L, M, with an optional gap
    before it) scales the number. K, L and M also mean units, so they scale only when the number sits beside a
    currency marker or the line is a money-word line: "₹1.5 L" and "Total 1.5L" read 150000, "Diet Coke 2L ₹95"
    reads 2. `quantity` marks a size ("300 ml", "2 L") on a line that is not a money-word line."""
    def cut(m):
        if m[0][0].isdigit() and ":" not in m[0]:  # "<n> mins", "<n> left": a price keeps its number
            if MARK_END.search(line[:m.start()]) or (m[0].lower().endswith("left") and WORDS.search(line)):
                return m[0]  # "₹300 Min order", "Amount due 299 left"
        return " "

    line = TICKING.sub(cut, line)
    worded = bool(WORDS.search(line))
    found = []
    for m in READING.finditer(line):
        value, token = float(m[1].replace(",", "")), (m[2] or "").lower()
        beside = bool(MARK_END.search(line[:m.start()]) or AFTER_MARK.match(line, m.end()))
        if token and (token not in LETTERS or beside or worded):
            found.append((value * SCALES[token], False))
        else:
            found.append((value, not worded and not beside and bool(token or UNIT.match(line, m.end()))))
    return found


def numbers(line):
    """Every number on the line: 1,499.00 → 1499.0, ₹1.5L → 150000.0."""
    return [value for value, _ in readings(line)]


def amounts(line):
    """The numbers on the line that can be money: not a size or quantity with a unit."""
    return [value for value, quantity in readings(line) if not quantity]


def amount(text):
    """The number in the first rupee amount of `text` ("To pay ₹1,234.50" → 1234.5), or None. Another currency
    (“$120”) is not read: the caller must treat it as unknown."""
    price = RUPEE.search(text or "")
    return numbers(text[price.start():])[0] if price else None


def number(price):
    """The number in a price of any currency, for comparing prices; None when there is none."""
    found = numbers(price or "")
    return found[0] if found else None


def worth(price):
    """A price as the rupee amount and the bare number: two prices are the same only when both agree."""
    return amount(price), number(price)


def marker_only(line):
    """Whether the line is just a currency marker ("₹", "Rs.", "USD:", "रुपये")."""
    token = line.strip().rstrip(":.").strip()
    symbols = all(unicodedata.category(c) == "Sc" for c in token)
    return bool(token) and (symbols or MARK_WORD.fullmatch(token) is not None)


SUFFIX_LINE = re.compile(TOKEN, re.IGNORECASE)


def flat(text):
    """The page text with a currency marker on a line of its own joined to the next line ("₹\\n1,299" → "₹ 1,299"),
    a "/-" or a suffix (L, K, Cr, lakh...) joined to the number before it ("1.5\\nL" → "1.5 L"), and a number the
    page split into parts ("1", ",", "299") put back together: the snapshot writes every text node on a line of its
    own. Empty lines go. Every money check reads this form."""
    text = re.sub("[\u200b-\u200d\u2060\ufeff]", "", text or "")  # zero-width characters hide nothing
    text = re.sub(r"(?<=\d)[\u00a0\u2009\u202f](?=\d{3}(?!\d))", ",", text).replace("\u00a0", " ")
    joined, sep = [], ""
    for line in (line.strip() for line in text.splitlines() if line.strip()):
        if sep and re.fullmatch(r"\d[\d,]*", line):
            joined[-1] += sep + line  # "1", ",", "299" and "79", ".", "50": a number split into parts
            sep = ""
            continue
        if sep:
            joined.append(sep)  # a stray comma or dot stays on its own line
            sep = ""
        ends_in_digit = bool(joined) and bool(re.search(r"\d$", joined[-1]))
        if ends_in_digit and line in {",", "."}:
            sep = line
        elif ends_in_digit and (line == "/-" or SUFFIX_LINE.fullmatch(line)):
            joined[-1] += " " + line  # "1,499" then "/-", "1.5" then "L"
        elif joined and re.search(r"\d[,.]$", joined[-1]) and re.fullmatch(r"\d+", line):
            joined[-1] += line  # "1," then "299"
        elif ends_in_digit and re.fullmatch(r"[,.]\d+", line):
            joined[-1] += line  # "1" then ",299"
        else:
            joined.append(line)
    joined += [sep] if sep else []
    out, pending = [], []
    for line in joined:
        if marker_only(line):
            pending.append(line)
        else:
            out.append(" ".join([*pending, line]))
            pending = []
    return "\n".join(out + pending)


def tight(text):
    """The text with no space between a currency marker and its number ("₹ 79" → "₹79")."""
    return re.sub("(" + SYMBOLS + r"|\bRs\.?|\bINR)\s+(?=\d)", r"\1", text, flags=re.IGNORECASE)


def shown(figure, text):
    """Whether the page text shows this amount, ignoring a space after the currency marker."""
    return rules.amount_shown(figure, text) or rules.amount_shown(tight(figure), tight(text))


def money_lines(text):
    """The lines that can hold money, in page order: any line with a money word or a currency marker, and the
    lines on either side of a money word ("Qty 2", "Total" then "1,499"). One set for the snapshot, the worst
    number, and the money check; a clock or countdown is cut out of a line when its numbers are read."""
    lines, keep = flat(text).splitlines(), set()
    for i, line in enumerate(lines):
        if WORDS.search(line):
            keep |= {i - 1, i, i + 1}
        elif CURRENCY.search(line):
            keep.add(i)
    return [lines[i] for i in sorted(keep) if 0 <= i < len(lines)]


def snapshot(text):
    """Every number on the money lines, in page order: an approval holds only while it is unchanged."""
    return tuple(n for line in money_lines(text) for n in numbers(line))


def worst(text):
    """The largest amount on the money lines, brackets included; 0.0 when there is none."""
    return max((n for line in money_lines(text) for n in amounts(line)), default=0.0)


def shows_money(text):
    """Whether the page shows money in any form, even one `amount` cannot read: a number on a money line, or a
    currency marker with a digit on its line or beside it."""
    lines = flat(text).splitlines()
    return any(numbers(line) for line in money_lines(text)) or any(
        CURRENCY.search(line) and any(numbers(near) for near in lines[max(i - 1, 0):i + 2])
        for i, line in enumerate(lines))


def unbracket(line):
    """A line without its remarks in brackets ("(you save ₹200)", "(was ₹1,499)"). Any other bracket ("(₹2,999 incl.
    GST)") keeps its words: its price may be the real one."""
    while ASIDE.search(line):
        line = ASIDE.sub(lambda m: " " if REMARK.search(m[0]) else f" {m[0][1:-1]} ", line)
    return line


def payable(text):
    """The payable total a page shows: the last line that says what is payable (else the last plain total), and
    the largest price on it. A total line turned away for a remark (savings, cashback, free, add-more) hides
    what is payable: its prices (own line and the next; the next three when it has none) are kept, and the
    answer is None when any is larger than the total chosen, or when no line qualifies. An item total, subtotal
    or "total savings" line is no such hint. A label with no price takes the largest price among the next three
    lines, except those that are remarks ("To pay", "₹50 cashback applied", "₹1,299"): their prices are kept the
    same way."""
    lines = [unbracket(line) for line in flat(text).splitlines()]
    turned_away = []
    for i, line in enumerate(lines):
        rest = EXEMPT.sub(" ", line)
        if TOTAL.search(rest) and NOT_TOTAL.search(rest):
            own = PRICES.findall(line)
            turned_away += own + PRICES.findall(lines[i + 1] if i + 1 < len(lines) else "") if own else \
                [price for near in lines[i + 1:i + 4] for price in PRICES.findall(near)]
    chosen = None
    for kind in (PAYABLE, TOTAL):  # "To pay" first; a plain "total" only when nothing says what is payable
        found = []
        for i, line in enumerate(lines):
            if not kind.search(line) or NOT_TOTAL.search(line):
                continue
            prices = PRICES.findall(line)
            if not prices:
                for near in lines[i + 1:i + 4]:
                    if REMARK_WORDS.search(near):
                        turned_away += PRICES.findall(near)
                    else:
                        prices += PRICES.findall(near)
            if prices:
                found.append(max(prices, key=lambda price: number(price) or 0.0))
        if found:
            chosen = found[-1]
            break
    if chosen is None or any((number(price) or 0.0) > (number(chosen) or 0.0) for price in turned_away):
        return None
    return chosen


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
        rest = rules.METHOD.sub("", PROCEED.sub("", words))  # a real commit word left over is never ambiguous
        ambiguous = operation in CLICKS and not rules.COMMIT.search(rest) and (
            PROCEED.search(words) or (rules.METHOD.search(words) and not rules.PRICE.search(words)))
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
    fees, the delivery address line, the item count, and the values typed into the form. Fees count from an
    earlier page only while that page's payable total is the current one."""
    text = flat(page["text"])
    seen = [text, *(flat(t) for t in reversed(recent[-5:]))]
    total = payable(text)
    current = [text] + [t for t in seen[1:] if total is not None and payable(t) is not None
                        and worth(payable(t)) == worth(total)]
    typed = [f"{a['label'].rstrip(':')}: {a['value']}" for a in page["actions"]
             if a.get("kind") == "fill" and a.get("role") != "searchbox" and "search" not in a["label"].lower()
             and a.get("value") and not str(a["value"]).startswith("[")]
    return {"total": total, "fees": list(dict.fromkeys(f.strip() for t in current for f in FEE.findall(t))),
            "address": next((m.strip() for t in seen for m in DELIVERING.findall(t)), None),
            "items": next((int(n) for n in ITEMS.findall(text)), 0), "typed": list(dict.fromkeys(typed))}


def summary(w, page, found):
    """The approval's lines: the planner's one line per skill field, then every fact it left out, from the page."""
    s = w.state
    skill = w.skill()
    text = flat(page["text"])
    payload = {"goal": s["goal"], "fields": (skill or {}).get("summary") or FIELDS, "chat": s["chat"][-10:],
               "facts": found, "page": {"url": page["url"], "title": page["title"], "text": text[:3000]},
               "pages_before": [flat(t)[:1500] for t in s["recent"][-3:]]}
    lines = [re.sub(r"^(ordered|booked|placed|paid|requested)\s+", "", line, flags=re.IGNORECASE)
             for line in planner.summary(payload)]  # nothing has happened until the user says yes
    missing = [x for x in rules.PRICE.findall(" ".join(lines)) if not shown(x, text)]
    if missing:
        raise Retry(f"The page doesn't show {', '.join(missing)}. Copy amounts exactly as the page shows them.")
    joined = " ".join(lines).lower()
    extra = []
    if found["total"] and not shown(found["total"], " ".join(lines)):
        extra.append(f"Total: {tight(found['total'])}")
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
                         "step": len(state["history"]), "target": target, "by": by,
                         "snapshot": snapshot(page["text"])}
    if action.get("point"):
        state["approval"]["look"] = {"point": action["point"]}


def preauthorised(state, found, page):
    """Whether the user's own words cover this commit: on the task's site, and within their cap when it costs
    money. A commit whose page shows no money at all (a form, a message) needs no cap; a total that is not in
    rupees, money the page shows but no total could be read for, or any number on a money line above the cap, is
    never covered."""
    here, site = skills.bare(rules.host(page["url"]).lower()), state["site"]
    if not state["preauth"] or not site or not (here == site or here.endswith("." + site)):
        return False
    if found["total"] is None:
        return not shows_money(page["text"])
    value = amount(found["total"])
    return (value is not None and state["cap"] is not None and value <= state["cap"]
            and worst(page["text"]) <= state["cap"])


def approved(w, page, action, label, subgoal):
    """True when this commit may run now; otherwise ask. An earlier yes counts only for this control, host, and
    amount. A fresh yes reads the page again first (StalePage); anything else is feedback (Retry)."""
    s = w.state
    approval = s["approval"]
    if approval and rules.approval_valid(approval, page, len(s["history"])) and matches(approval, action, label):
        if snapshot(page["text"]) != approval.get("snapshot"):
            s["approval"] = None  # any money line changed (or none was kept): the user approved something else
        else:
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
    total = found["total"] or ""  # never a fee from the summary: the snapshot guards what was shown
    if total and not rules.amount_shown(total, page["text"]):
        total = ""  # the raw page spells it differently (a number split in parts): approval_valid would void the yes
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
    s["approval"] = None  # nothing but a clear yes keeps an earlier approval alive
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
