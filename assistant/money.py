"""Money rules, in code, for each money job: what the user asked (its intent, carried in the task's goal), the facts
as the page shows them, the checks before the one yes, the approval's lines, a UPI QR's link checked against the
approved amount, and for vouchers the split, an offer's worth in rupees and the masking of codes. The page model
(actor.py) only acts and reads; it never decides whether money moves."""

import math
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlparse

PAID_WINDOW = timedelta(days=25)  # a bill paid within this many days is asked about before paying it again
VOUCHER_WINDOW = timedelta(hours=24)  # the same vouchers bought within this long: asked about before buying again
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


SPLIT = r"\d+ × ₹[\d,]+(?: \+ \d+ × ₹[\d,]+)*"
VOUCHER_GOAL = re.compile(rf"^Buy ₹([\d,]+) of (.+?) vouchers on (.+?): ({SPLIT}|any vouchers that make ₹[\d,]+), "
                          r"paying with (.+?)\.(?: Compared: (.+)\.)?$")
READ_GOAL = re.compile(r"^Read the (.+?) vouchers on (.+?) for ₹([\d,]+): every amount, the reward, any discount or "
                       r"fee, and the monthly limit\. Buy nothing\.$")
MULTIPLIER = re.compile(r"(\d+(?:\.\d+)?)\s*[x×](?![a-z])", re.IGNORECASE)  # "5X Reward Points"
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")  # "5% cashback", "10% GV coins", "2.5%"
EARN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+per\s+(\d+)\s*$")  # a card note's base earn: "5 per 150"
CART = (re.compile(r"(\d+)\s*[x×]\s*₹\s?([\d,]+)"), re.compile(r"₹\s?([\d,]+)\s*[x×]\s*(\d+)"))
ORDER_LABEL = re.compile(r"\border\s*(?:id|no\.?|number)\b\s*[:#-]?\s*$", re.IGNORECASE)  # a bare label: id below it
ORDER_ID = re.compile(r"\border\s*(?:id|no\.?|number)\b\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9-]{4,})", re.IGNORECASE)
RUPEE_BEFORE = re.compile(r"(?:₹|\brs\.?|\binr)\s*$", re.IGNORECASE)
DECIMAL_AFTER = re.compile(r"\.\d")
TOKEN = re.compile(r"(?<![\w-])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{3,}(?![\w-])")  # letters and digits


def job_intent(goal):
    """The money job this goal names, with its kind: a bill's payment, a voucher purchase, or a voucher store's read;
    else None."""
    bill = bill_intent(goal)
    if bill:
        return {"kind": "bill", **bill}
    found = VOUCHER_GOAL.match(goal or "")
    if found:
        made = None if found[4].startswith("any ") else {int(size.replace(",", "")): int(n) for n, size in
                                                          re.findall(r"(\d+) × ₹([\d,]+)", found[4])}
        return {"kind": "voucher", "portal": found[3], "brand": found[2], "amount": int(found[1].replace(",", "")),
                "split": made, "card": found[5], "compared": found[6]}
    found = READ_GOAL.match(goal or "")
    if found:
        return {"kind": "voucher_read", "portal": found[2], "brand": found[1], "amount": int(found[3].replace(",", ""))}
    return None


def voucher_goal(portal, brand, amount, made, card, compared=None):
    """The worker's goal for one purchase: the total, the brand, the store, the vouchers (or any that make the total,
    for a store whose amounts couldn't be read), the card, and the reward the comparison showed."""
    vouchers = counted(made) if made else f"any vouchers that make ₹{amount:,}"
    return (f"Buy ₹{amount:,} of {brand} vouchers on {portal}: {vouchers}, paying with {card}."
            + (f" Compared: {' '.join(re.sub(r'[.](?!\d)', ' ', compared).split())}." if compared else ""))


def read_goal(portal, brand, amount):
    """The worker's goal for reading one store's offer for a brand: it buys nothing."""
    return (f"Read the {brand} vouchers on {portal} for ₹{amount:,}: every amount, the reward, any discount or fee, "
            "and the monthly limit. Buy nothing.")


BRAND_FILLER = {"money", "gift", "voucher", "vouchers", "card", "cards", "e", "egift", "evoucher", "gv", "instant"}


def voucher_key(brand):
    """A voucher purchase's record key, where a bill's names its account: the brand's own words, so "Swiggy",
    "Swiggy Money" and "Swiggy vouchers" are one record (voucher_swiggy)."""
    kept = [w for w in re.findall(r"[a-z0-9]+", str(brand).lower()) if w not in BRAND_FILLER]
    return ("voucher_" + ("_".join(kept) or "brand"))[:40].rstrip("_")


def split(amounts, total):
    """The fewest vouchers of these amounts that make the total exactly, largest first ({2000: 2, 1000: 1}); else
    None."""
    sizes = sorted({int(a) for a in amounts if int(a) > 0})
    if not sizes or total <= 0:
        return None
    step = math.gcd(*sizes)
    if total % step:
        return None
    best = {0: ()}
    for made in range(step, total + 1, step):
        tries = [best[made - size] + (size,) for size in reversed(sizes) if made - size in best]
        if tries:
            best[made] = min(tries, key=len)
    return {size: best[total].count(size) for size in sorted(set(best[total]), reverse=True)} \
        if total in best else None


RUPEES = re.compile(r"(?:₹|\brs\.?|\binr)\s*([\d,]+)", re.IGNORECASE)  # an amount marked as rupees, any way
R = r"(?:₹|\brs\.?|\binr)\s*([\d,]+)(?:\.\d+)?"  # one rupee amount, captured
RANGES = (re.compile(rf"\bmin(?:imum)?\b\W*{R}.*?\bmax(?:imum)?\b\W*{R}", re.IGNORECASE),  # "Min: ₹100, Max: …"
          re.compile(rf"\bbetween\s+{R}\s+and\s+{R}", re.IGNORECASE),  # "between ₹500 and ₹10,000"
          re.compile(rf"(?<![-–]\s){R}\s*(?:to|-|–)\s*{R}(?!\s*[-–])", re.IGNORECASE))  # "₹100 to ₹2,000", not a list


def amount_list(text):
    """Every rupee amount a store's page lists ("₹500", "Rs. 1000", "INR 2,000"; with no rupee sign anywhere, its
    numbers of 10 or more), smallest first."""
    marked = [n for n in RUPEES.findall(str(text or "")) if n.strip(",")]
    found = marked or [n for n in re.findall(r"\d[\d,]*", str(text or "")) if int(n.replace(",", "")) >= 10]
    return sorted({int(n.replace(",", "")) for n in found})


def limit_of(text):
    """A limit's rupees ("Up to 2 vouchers of ₹10,000 a month" → 10000), else None."""
    found = RUPEES.search(str(text or ""))
    return int(found[1].replace(",", "")) if found and found[1].strip(",") else None


def amount_range(text):
    """(lowest, highest) when a store takes any amount in a range ("Enter a value between ₹500 and ₹10,000",
    "Min: ₹100.00, Max: ₹10,000.00", "₹100 to ₹2,000"), whatever else the page lists; else None. A list of amounts
    ("₹250 - ₹500 - ₹1,000") is no range."""
    for pattern in RANGES:
        found = pattern.search(str(text or ""))
        if found:
            low, high = int(found[1].replace(",", "")), int(found[2].replace(",", ""))
            if low < high:
                return low, high
    return None


def nearest(total, amounts, limit):
    """The totals these vouchers make nearest the total, nearest first: the one below it and the one above, never
    over the limit (₹1,200 of ₹250 steps: ₹1,250, then ₹1,000)."""
    sizes = sorted({int(a) for a in amounts if int(a) > 0})
    if not sizes:
        return []
    top = min(limit, total + max(sizes)) if limit else total + max(sizes)
    reached, made = {0}, []  # every total these vouchers make, in one pass (review: a split per total took 79 s)
    for t in range(math.gcd(*sizes), top + 1, math.gcd(*sizes)):
        if any(t - size in reached for size in sizes):
            reached.add(t)
            made.append(t)
    return sorted([t for t in made if t < total][-1:] + [t for t in made if t > total][:1],
                  key=lambda t: (abs(t - total), t))


def counted(made):
    """Vouchers as the user reads them: "2 × ₹2,000 + 1 × ₹1,000"."""
    return " + ".join(f"{n} × ₹{size:,}" for size, n in made.items())


def cart_split(text):
    """What a cart holds, as {amount: count}, from words like "2 × ₹2,000" or "₹2,000 x 2"; else None."""
    for n, pattern in enumerate(CART):
        found = pattern.findall(str(text or ""))
        if found:
            made = {}
            for a, b in found:
                count, size = (a, b) if n == 0 else (b, a)
                made[int(size.replace(",", ""))] = made.get(int(size.replace(",", "")), 0) + int(count)
            return made
    return None


def reward(text):
    """An offer's reward from its words: a multiplier of the card's base rate ("5X Reward Points"), a percent back
    ("10% GV coins", "5% cashback"), or not known."""
    found = MULTIPLIER.search(str(text or ""))
    if found:
        return {"kind": "multiplier", "value": Decimal(found[1])}
    found = PERCENT.search(str(text or ""))
    if found:
        off = re.search(r"\b(off|discount)\b", str(text), re.IGNORECASE)  # "Flat 3% OFF": paid less, nothing back
        return {"kind": "off" if off else "back", "value": Decimal(found[1])}
    return {"kind": None, "value": None}


def money_off(text, amount):
    """A discount or fee as rupees: a percent of the amount ("2.5%"), or an amount ("₹50"); else 0."""
    found = PERCENT.search(str(text or ""))
    if found:
        return (Decimal(amount) * Decimal(found[1]) / 100).quantize(Decimal("0.01"))
    return rupees(text) if "₹" in str(text or "") or re.search(r"\brs\.?\s?\d", str(text or ""), re.I) else Decimal(0)


def card_rate(card):
    """(points, per rupees) a card earns at its base rate, from its note's earn ("5 per 150"); else None."""
    found = EARN.match(str((card or {}).get("earn") or ""))
    return (Decimal(found[1]), Decimal(found[2])) if found else None


def value(facts, card, amount, cap=None):
    """An offer in rupees for one card: points (the card's base earn × the offer's multiplier, held to the store's
    cap in points), rupees back (points × the card's rupees a point, or the offer's percent back, held to a cap in
    rupees), the discount and fee, what is payable, and the effective price. Unranked when its worth isn't known."""
    amount = Decimal(amount)
    offer, rate = reward(facts.get("reward")), card_rate(card)
    discount, fee = money_off(facts.get("discount"), amount), money_off(facts.get("fee"), amount)
    if offer["kind"] == "off":  # a discount read as the reward: the same money off, counted once
        discount = max(discount, (amount * offer["value"] / 100).quantize(Decimal("0.01")))
    payable = (amount - discount + fee).quantize(Decimal("0.01"))
    points = back = None
    point = Decimal(str(card["point"])) if (card or {}).get("point") not in (None, "") else None
    if offer["kind"] == "multiplier" and rate:
        points = int(amount * rate[0] * offer["value"] / rate[1])
        capped = re.match(r"\s*([\d,]+)\s*points", str(cap or ""), re.IGNORECASE)
        if capped:
            points = min(points, int(capped[1].replace(",", "")))
        back = (points * point).quantize(Decimal("0.01")) if point is not None else None
    elif offer["kind"] == "back":
        back = (amount * offer["value"] / 100).quantize(Decimal("0.01"))
        if "₹" in str(cap or ""):
            back = min(back, rupees(cap))
    ranked = back is not None or (offer["kind"] in {None, "off"} and bool(discount or fee))
    return {"points": points, "back": back, "discount": discount, "fee": fee, "payable": payable,
            "effective": (payable - (back or 0)).quantize(Decimal("0.01")) if ranked else None, "ranked": ranked,
            "basis": f"at ₹{point} a point" if points is not None and point is not None else ""}


def rank(offers):
    """Offers by effective price, cheapest first; those whose worth isn't known after them, in their order."""
    return sorted((o for o in offers if o["ranked"]), key=lambda o: o["effective"]) + \
        [o for o in offers if not o["ranked"]]


def voucher_lines(intent, facts):
    """A voucher purchase's approval, written in code: what is bought, where, with which card, what is payable, the
    reward the page shows now, and whether it is lower than when compared."""
    lines = [f"Buy ₹{intent['amount']:,} of {intent['brand']} vouchers on {intent['portal']}",
             counted(intent["split"]) if intent["split"] else f"Vouchers making ₹{intent['amount']:,}",
             f"Paying with {intent['card']}"]
    payable, total = rupees(facts.get("payable")), Decimal(intent["amount"])
    if payable is not None and payable > total:
        fee = money_off(facts.get("fee"), total)
        lines.append(f"Payable with the site's fee: {shown(payable)}" if fee and payable - total <= fee + 1 else
                     f"Payable: {shown(payable)} ({shown(payable - total)} more than the {shown(total)} asked)")
    elif payable is not None:
        lines.append(f"Payable: {shown(payable)}")
    if facts.get("reward"):
        lines.append(f"Reward shown now: {facts['reward']}")
    now, then = reward(facts.get("reward")), reward(intent.get("compared"))
    if then["value"] is not None and now["value"] is not None and (now["kind"] != then["kind"]
                                                                   or now["value"] < then["value"]):
        lines.append(f"Lower than when compared: {intent['compared']}")
    return lines


def mask_codes(text):
    """Page text after a purchase's yes, with every voucher code and PIN hidden: deny by default. Every run of 4 or more
    letters and digits holding a digit becomes [code], except a rupee amount (₹, Rs or INR before it, or a decimal
    after it) and the one order number an "Order ID / No / Number" label names, on its line or the next: the proof."""
    out, label_above = [], False
    for line in str(text or "").split("\n"):
        named = ORDER_ID.search(line)
        keep = named[1] if named else None
        if keep is None and label_above:
            first = TOKEN.search(line)
            keep = first[0] if first else None
        label_above = bool(ORDER_LABEL.search(line)) and named is None

        def hide(found, line=line, keep=keep):
            if found[0] == keep or RUPEE_BEFORE.search(line[:found.start()]) or DECIMAL_AFTER.match(line, found.end()):
                return found[0]
            return "[code]"

        out.append(TOKEN.sub(hide, line))
    return "\n".join(out)


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


def paid_before(payments, now, window=PAID_WINDOW):
    """The payment that makes paying again a question: one paid within the window (a bill's PAID_WINDOW, vouchers'
    VOUCHER_WINDOW), or one never settled (started or unclear), newest first; else None."""
    for payment in sorted(payments, key=lambda p: p["created_at"], reverse=True):
        if payment["status"] in {"started", "unclear"}:
            return payment
        if payment["status"] == "paid" and now - payment["created_at"] <= window:
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
