"""A bill payment's money rules, in code: what the user asked (its intent, carried in the task's goal), the bill's
facts as the page shows them, the checks before the one yes, the approval's lines, and a UPI QR's link checked
against the approved amount. The page model (actor.py) only acts and reads; it never decides whether money moves."""

import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlparse

PAID_WINDOW = timedelta(days=25)  # a bill paid within this many days is asked about before paying it again
BILL_GOAL = re.compile(r"^Pay the (.+?) bill for account \[profile:([a-z][a-z0-9_]{1,39})\]: "
                       r"(?:the amount due|exactly ₹([\d,]+))(?:, by (UPI|card))?\.$")
BILL_FACTS = ("name", "amount_due", "due_date", "payable", "payee", "reference")
VOUCHER_FACTS = ("amounts", "reward", "discount", "fee", "limit", "cart", "payable", "order_id")
FACTS = {"bill": BILL_FACTS, "voucher": VOUCHER_FACTS, "voucher_read": VOUCHER_FACTS}  # what each job reads
AMOUNT = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(\d{1,2}))?(?![\d,])")
UPI_LINK = re.compile(r"upi://pay\?[^\s\"'<>\\]+", re.IGNORECASE)
HIDDEN_NAME = "[profile:name]"
TITLES = {"mr", "mrs", "ms", "miss", "dr", "shri", "smt", "kumari", "late", "m/s"}
APPS = (("GPay", "gpay://upi/pay?"), ("PhonePe", "phonepe://pay?"), ("Paytm", "paytmmp://pay?"))


def goal_for(biller, account, amount=None, method=None):
    """The worker's goal for one bill: the biller, the saved account's key, the amount (due, or exact), the method."""
    what = "the amount due" if amount is None else f"exactly ₹{amount:,}"
    return f"Pay the {biller} bill for account [profile:{account}]: {what}" + (f", by {method}" if method else "") + "."


def bill_intent(goal):
    """{biller, account, amount, method} from a bill's goal (`goal_for`), else None."""
    found = BILL_GOAL.match(goal or "")
    if not found:
        return None
    return {"biller": found[1], "account": found[2],
            "amount": int(found[3].replace(",", "")) if found[3] else None, "method": found[4]}


def job_intent(goal):
    """The money job this goal names, with its kind: a bill's payment; else None."""
    bill = bill_intent(goal)
    return {"kind": "bill", **bill} if bill else None


def account_key(biller):
    """The saved ID's key for a biller's account number: "Tata Power-DDL" → tata_power_ddl_account."""
    slug = re.sub(r"[^a-z0-9]+", "_", biller.lower()).strip("_") or "biller"
    if not slug[0].isalpha():
        slug = "b_" + slug
    return slug[:32].rstrip("_") + "_account"


def rupees(text):
    """The first rupee amount in `text` (₹ 1,240 · Rs. 1240.50 · 1,23,456 · INR 99) as a Decimal, else None."""
    found = AMOUNT.search(str(text or ""))
    if not found:
        return None
    try:
        return Decimal(found[1].replace(",", "") + (f".{found[2]}" if found[2] else ""))
    except InvalidOperation:
        return None


def amounts(text):
    return {value for m in AMOUNT.finditer(text or "")
            if (value := rupees(m[0])) is not None}


def words(text):
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def grounded(value, text):
    """Whether the page shows this fact: an amount as the same amount (₹1,240 = 1240.00), anything else as the same
    words."""
    if not str(value or "").strip():
        return False
    if re.fullmatch(r"\s*(?:₹|rs\.?|inr)?\s*[\d,]+(?:\.\d+)?\s*", str(value), re.IGNORECASE):
        return rupees(value) in amounts(text)
    return bool(words(value)) and f" {words(value)} " in f" {words(text)} "


def name_match(on_bill, profile):
    """"match" when the bill's name is the user's: its first word is their first name (or its initial) and its last
    word their surname; middle names and initials go free ("M K CHURIWAL"). The page reaches the agent with the
    user's name words hidden as [profile:name], which stands for one of their own words wherever it sits ("M K
    [profile:name]" matches; "RAJESH [profile:name]", a parent's bill, does not). "mismatch" otherwise, None when the
    page shows no name."""
    if not str(on_bill or "").strip():
        return None
    theirs = [w for w in words(profile).split() if w not in TITLES]
    on = [w for w in words(str(on_bill).replace(HIDDEN_NAME, " hiddenname ")).split() if w not in TITLES]
    if not on:
        return None
    if all(w == "hiddenname" for w in on):
        return "match"
    if len(theirs) < 2:
        return "match" if on == theirs else "mismatch"
    first = on[0] == "hiddenname" or on[0] == theirs[0] or (len(on[0]) == 1 and theirs[0].startswith(on[0]))
    last = on[-1] == "hiddenname" or on[-1] == theirs[-1]
    return "match" if first and last and len(on) > 1 else "mismatch"


def to_pay(intent, facts):
    """What this payment pays: the exact amount the user named, else the bill's amount due."""
    if intent["amount"] is not None:
        return Decimal(intent["amount"])
    return rupees(facts.get("amount_due"))


def shown(value):
    value = Decimal(value)
    return f"₹{value:,.2f}".removesuffix(".00")


def approval_lines(intent, facts, account=""):
    """The approval card's lines, written in code: what is paid, to whom, for which account, and how."""
    lines = [f"Pay {shown(to_pay(intent, facts))} {intent['biller']} bill"]
    if account:
        lines.append(f"Account ••{account[-4:]}")
    if facts.get("name"):
        hidden = HIDDEN_NAME in facts["name"]  # the user's own name, hidden from the models
        lines.append("Name on the bill: matches yours" if hidden else f"Name on the bill: {facts['name']}")
    if facts.get("due_date"):
        lines.append(f"Due {facts['due_date']}")
    payable = rupees(facts.get("payable"))
    if payable is not None and payable != to_pay(intent, facts):
        lines.append(f"Payable with the site's fee: {shown(payable)}")
    if intent.get("method"):
        lines.append(f"By {intent['method']}")
    return lines


def paid_before(payments, now):
    """The payment that makes paying again a question: one paid within PAID_WINDOW, or one never settled (started or
    unclear), newest first; else None."""
    for payment in sorted(payments, key=lambda p: p["created_at"], reverse=True):
        if payment["status"] in {"started", "unclear"}:
            return payment
        if payment["status"] == "paid" and now - payment["created_at"] <= PAID_WINDOW:
            return payment
    return None


def upi_link(text):
    """The first upi://pay link in page text or markup, else None."""
    found = UPI_LINK.search(text or "")
    return found[0].replace("&amp;", "&") if found else None


def upi_params(link):
    return {k.lower(): v for k, v in parse_qsl(urlparse(link).query, keep_blank_values=True)}


def qr_problem(link, approved):
    """Why this QR must not go to the user, else None: it pays the approved amount to a named UPI address."""
    params = upi_params(link)
    if not params.get("pa"):
        return "The UPI QR names no one to pay"
    asked = rupees(params.get("am"))
    if asked is None:
        return "The UPI QR carries no amount, so your app would ask you to type one"
    if asked != approved:
        return f"The UPI QR asks {shown(asked)}, not the {shown(approved)} you approved"
    return None


def app_links(link):
    """(label, address) buttons that open this same payment: any UPI app on Android, one app each on iPhone (where a
    bare upi:// link opens WhatsApp's payments)."""
    query = link.split("?", 1)[1]
    return [("UPI app", link), *((label, prefix + query) for label, prefix in APPS)]
