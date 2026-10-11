"""Pure checks the agent applies before acting: commits, verbatim evidence, answer formats, approvals."""

import re
from urllib.parse import urlparse

COMMIT = re.compile(  # outright: Jev can never clear these. "Buy it again" opens a list of past items to choose from
    r"\b(pay|place (your )?order|buy(?!\s+(?:things\s+|it\s+)?again\b)|purchase|book now"
    r"|confirm (order|booking|ride|payment|purchase)"
    r"|request (ride|uber\w*|trip)|cancel (my )?(order|booking|subscription|ride|membership|mandate|auto ?pay)|delete"
    r"|revoke|pause (my )?(mandate|auto ?pay)"
    r"|submit (complaint|dispute|claim|request)|book (a )?(ride|cab|bike|auto|table|tickets?|room|hotel|flight)"
    r"|end (my )?(membership|subscription)|submit (the )?order|order now|recharge now|make (a )?payment"
    r"|complete (the )?(payment|order|booking|purchase)|continue (to )?booking)\b",
    re.IGNORECASE,
)
# Words that may commit ("Book Ola Mini", "Confirm UberGo", "Submit", "Proceed to Pay"): Jev's final check clears
# one only when it is sure (step ≥ 0.8) that the control is a step on the way.
MAYBE = re.compile(r"\b(book|confirm|request|reserve|submit|transfer|recharge|subscribe|(end|cancel)\s+\w+"
                   r"|continue to cancel|proceed to (pay|payment|buy|checkout|recharge)|make\b.{0,30}\bpayment"
                   r"|complete|place|pay)\b", re.IGNORECASE)
SEND = re.compile(r"\bsend\b", re.IGNORECASE)
SENDS_A_CODE = re.compile(r"\b(code|otp|one[- ]?time|verification|passcode)\b", re.IGNORECASE)  # "Send OTP"
REDACTED = re.compile(r"\[(secret|profile):", re.IGNORECASE)  # a hidden value may hide the commit word itself
PROCEED_STEP = re.compile(r"\bproceed to (buy|checkout|pay)\b", re.IGNORECASE)
CHECKOUT_STEP = re.compile(r"\b(?:proceed|continue) to (?:buy|checkout|pay|payment)\b", re.IGNORECASE)  # words shown
APPROVAL_STEPS = 20
SECRET_ASK = re.compile(r"\b(otp|one[- ]?time (pass(word|code)?|code)|verification code|security code|passcode|"
                        r"password|cvv|cvc|pin(?![\s-]?code)|\d[- ]?digit code|"
                        r"code (we |that was |you )?(sent|received))\b", re.IGNORECASE)
TO_RECEIVE = re.compile(r"\b(phone|mobile|e-?mail)\b.*\b(to|for) (receive|get)\b", re.IGNORECASE)
CHALLENGE = re.compile(r"i'?m not a robot|select all (the )?(images|squares)|click verify once|"
                       r"type the (text|characters|word)|verify (that )?you are (a )?human|press and hold|"
                       r"are you a robot|refresh captcha|enter (the )?captcha|captcha code", re.IGNORECASE)
# Payment methods named with "pay" (UPI Scan and Pay, Pay on Delivery): choosing one pays nothing by itself.
METHOD = re.compile(r"\b(upi|cash|card|wallets?|net ?banking|pay later|pay on delivery|cash on delivery|scan and pay|"
                    r"amazon pay|google pay|gpay|phonepe|paytm)\b", re.IGNORECASE)
COMMERCE = re.compile(r"\b(book|order|re-?order|buy|purchase|pay|reserve)\b", re.IGNORECASE)
# "order" as a thing the user already has, not a verb: "my latest Amazon.in order", "the last order", "did I order",
# "order status". "Order a…", "order 2…", "order the…", and "the pizza order form" stay commerce.
HAD = re.compile(r"\b(?:my|our|last|latest|previous|recent|past|undelivered|pending)\b(?:\s+[\w.'&-]+){0,3}?\s+$|"
                 r"\b(?:did|have|had|has)\s+(?:i|we)\s+$", re.IGNORECASE)
ORDER_NOUN = re.compile(r"\s+(?:status|history|details?|id|number|tracking|summary|invoice)\b", re.IGNORECASE)
# A goal that only finds out: it asks, finds, compares, or checks, and names no commit of any kind.
LOOKS = re.compile(r"^\W*(?:please\s+)?(?:find|compare|search|look\s*up|check(?!\s*-?\s*out)|what|which|where|when|"
                   r"how|who|is|are|tell me|show me)\b", re.IGNORECASE)
COMMITS = re.compile(r"\b(?:cancel|submit|send|delete|remove|end|request|subscribe|unsubscribe|transfer|confirm|"
                     r"recharge|fill|apply|register|complete|payment|checkout|check\s*-?\s*out|renew|activate|upgrade|"
                     r"sign\s*up|join|message|reply|claim|return(?!\s+polic)|exchange(?!\s+rate)|refund|ordering|"
                     r"booking(?!\.)|buying|paying|purchasing|reserving|renting|hiring)\b", re.IGNORECASE)
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
QR = re.compile(r"\bQR\b")  # a page showing a code to scan with the user's phone
# Cancelling something that bills on its own: with no site note for the merchant, its UPI AutoPay mandate is the handle.
SUBSCRIPTION_CANCEL = re.compile(r"\b(?:cancel|stop|end|revoke)\b.{0,40}\b(?:subscription|auto ?pay|mandate|"
                                 r"recurring payment)s?\b", re.IGNORECASE)
INDIAN_MOBILE = re.compile(r"(?:\+?91)?[\s-]*([6-9]\d{4})[\s-]*(\d{5})")


def host(url):
    return urlparse(url).netloc


CLICKS = {"CLICK", "DOUBLE_CLICK"}


def control_words(operation, action, page, key=None, focused=""):
    """(label, words shown on it) of the control an operation presses, or None when the operation presses none.
    `focused` names the control a page-level key reaches."""
    if operation in CLICKS:  # its own name and the words it shows: context added to tell controls apart isn't either
        return action.get("name", action.get("label", "")) or "", action.get("shown") or ""
    if operation == "LOOK":  # found by sight: its name and the words on it
        return action.get("label", "") or "", action.get("text") or ""
    if operation == "PRESS_KEY" and key in {"Enter", "Space"}:
        # Enter submits the field's form; on the page, Enter or Space presses whatever has focus.
        label = (action.get("submit", "") if key == "Enter" else "") or (focused if action.get("node") is None else "")
        return label or "", ""
    if operation == "ACCEPT_DIALOG":
        return page.get("text", "") or "", ""
    return None


def commit_label(operation, action, page, key=None, focused=""):
    """The words that make this action an outright commit, or None."""
    words = control_words(operation, action, page, key, focused)
    if words is None:
        return None
    label, shown = words
    if operation == "LOOK" and COMMIT.search(shown):  # the look's own "commits" flag is a maybe: Jev confirms it
        return label or shown or "the control found on screen"
    if operation in CLICKS:  # a commit word in its name or on show; "Proceed to Pay" opens the checkout page: a maybe
        if not COMMIT.search(PROCEED_STEP.sub("", f"{label} {shown}")):
            return None
        return label if COMMIT.search(PROCEED_STEP.sub("", label)) else shown
    return label if COMMIT.search(label) else None


def maybe_commits(words):
    """Whether these words may commit: a maybe word, a send that is not a code's, or a value the redactor hid."""
    words = words or ""
    sends = SEND.search(words) and not SENDS_A_CODE.search(words)
    return bool(MAYBE.search(words) or REDACTED.search(words) or sends)


def maybe_commit_label(operation, action, page, key=None, focused=""):
    """The words of a control that may commit (the maybe tier), or None: read like `commit_label` reads them."""
    words = control_words(operation, action, page, key, focused)
    if words is None:
        return None
    label, shown = words
    if operation == "LOOK" and (action.get("commits") or maybe_commits(shown)):
        return label or shown or "the control found on screen"
    if operation in CLICKS and not maybe_commits(label) and maybe_commits(shown):
        return shown  # "Continue" showing “Proceed to Pay”
    return label if maybe_commits(label) else None


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
    return not approval["amount"] or amount_shown(approval["amount"], page["text"])


ZERO_FRACTION = r"(?:\.0{1,2}|,00)"  # ".0", ".00", ",00": no paise. ",000" and "€1.000" are thousands, never this


def amount_shown(amount, text):
    """The approved amount on the page as a whole number: ₹79 is not ₹790, ₹79.50, or 179. A fraction of only
    zeros, on either side, is the same value spelled another way: ₹50 is ₹50.00, and ₹1,299.00 is ₹1,299."""
    fragment = re.sub(r"(?<=\d)" + ZERO_FRACTION + "$", "", normal(amount))
    return bool(fragment) and re.search(r"(?<!\d)" + re.escape(fragment) + ZERO_FRACTION + r"?(?![\d.,]*\d)",
                                        normal(text)) is not None


def asks_for_secret(text):
    """Whether a question asks for a code, password, CVV, or PIN: its answer must be hidden, whatever a model said."""
    return bool(SECRET_ASK.search(text or "")) and not TO_RECEIVE.search(text or "")  # "your phone, to get a code"


def secret_field(action):
    """Whether a field takes a one-time code or a password: only the user knows its value, never memory or a model."""
    limits = action.get("constraints") or {}
    return action.get("kind") == "fill" and bool(action.get("group") or SECRET_ASK.search(action.get("label", ""))
                                                 or limits.get("type") == "password"
                                                 or limits.get("autocomplete") == "one-time-code")


def code_field(page):
    """Whether the page asks for a one-time code or a password: signing in is not finished yet."""
    return any(secret_field(a) for a in page["actions"])


def captcha_challenge(page):
    """Whether the page shows a captcha challenge to solve, in its text or on a control ("I'm not a robot"): not
    just a "protected by reCAPTCHA" badge."""
    return bool(CHALLENGE.search(page.get("text", "")) or
                any(CHALLENGE.search(a.get("label") or "") for a in page.get("actions") or ()))


CAPTCHA_BOX = re.compile(r"captcha|security verification", re.IGNORECASE)  # where a captcha's text is typed
CARD_DETAIL = re.compile(r"\b(cvv|cvc|card (number|no\.?|details?)|expir(y|ation)( date)?|upi pin)\b", re.IGNORECASE)
PRICE = re.compile(r"[₹$€£]\s?\d(?:[\d,]*\d)?(?:\.\d+)?|\bRs\.?\s?\d(?:[\d,]*\d)?(?:\.\d+)?", re.IGNORECASE)


def commerce_goal(goal):
    """Whether the goal is finished only by a booking, an order, or a payment going through. An order the user
    already has ("my last Swiggy order", "did I order", "order status") is a thing to look up, not a verb."""
    goal = goal or ""
    for match in COMMERCE.finditer(goal):
        if match[1].lower() == "order" and (HAD.search(goal[:match.start()]) or ORDER_NOUN.match(goal, match.end())):
            continue
        return True
    return False


def lookup_goal(goal, skill=None):
    """Whether the goal only finds, compares, or checks something: no order, booking, or payment, and no other commit
    word (submit, send, cancel, fill...). Such a goal never asks to press a commit."""
    goal = goal or ""
    if commerce_goal(goal) or COMMITS.search(goal):
        return False
    return skill == "lookup" or bool(LOOKS.match(goal))  # never by a "?" alone: "Can you get me a coffee?" orders


DOWNLOAD = re.compile(r"\bdownload|\b(?:save|export)\b(?:\s+[\w.'’-]+){0,6}?\s+(?:files?|invoices?|receipts?|"
                      r"statements?|pdfs?|documents?|bills?|tickets?|certificates?|copy|copies)\b", re.IGNORECASE)
HOW_TO = re.compile(r"^\W*(?:how|what|why|where|when|which|who)\b", re.IGNORECASE)  # a question about downloading


def download_goal(goal):
    """Whether the goal is done only once a file reaches the user: it downloads, saves, or exports a document."""
    goal = goal or ""
    return bool(DOWNLOAD.search(goal)) and not HOW_TO.match(goal)


def attempt_limit(key):
    return 2 if key and key.endswith(".cvv") else 3


def numeric(action):
    constraints = action.get("constraints") or {}
    return constraints.get("inputmode") in {"numeric", "decimal"} or constraints.get("type") == "number"


def tel_code(action):
    """A one-time code, password, or card detail in a phone-number box (type=tel): its digits, never a phone number."""
    secret = secret_field(action) or bool(CARD_DETAIL.search(action.get("label") or ""))
    return secret and (action.get("constraints") or {}).get("type") == "tel"


def clean(value, action):
    """The answer as it should be typed: trimmed, and without spaces or dashes in codes and number fields."""
    value = str(value).strip()
    return re.sub(r"[\s-]", "", value) if action.get("group") or numeric(action) or tel_code(action) else value


def fit_phone(value, action):
    """An Indian mobile number as a 10-digit phone box takes it: "+91 98765 43210" becomes "9876543210" when the box
    holds 10 characters, expects 10 digits, or asks for the national number. Anything else is left as it is."""
    constraints = action.get("constraints") or {}
    national = str(constraints.get("maxlength")) == "10" or constraints.get("pattern") in ("[0-9]{10}", r"\d{10}") \
        or constraints.get("autocomplete") == "tel-national"
    found = INDIAN_MOBILE.fullmatch(str(value).strip())
    return found[1] + found[2] if national and found else value


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
    card = bool(CARD_DETAIL.search(action.get("label") or ""))  # an expiry date keeps its "/"
    if tel_code(action) and not re.fullmatch(r"[\d/]+" if card else r"\d+", value):  # digits, held to maxlength below
        return "This code takes digits only."
    if kind == "tel" and not tel_code(action) and not re.fullmatch(r"[\d\s+()-]{6,}", value):
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


ERROR_PAGE = re.compile(r"\b404\b|\bnot found\b|could not be found|page you(’|')?re looking for|\buh-oh\b|"
                        r"something went wrong|should not have happened", re.IGNORECASE)


def error_page(page):
    """A "not found" or "something went wrong" page: by its title, or a short page that opens with it."""
    return bool(ERROR_PAGE.search(page.get("title") or "") or
                (len(page["text"]) < 1500 and ERROR_PAGE.search(page["text"][:300])))
