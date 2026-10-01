"""What no model, log, or table may hold: the per-task redaction registry, and what may never be stored at all.
Hidden values reach models only as placeholders: [profile:<key>] for the user's IDs, [secret:answerN] for an answer
typed into a hidden field."""

import contextvars
import re

from . import rules

SECRETS = contextvars.ContextVar("jev_secrets", default=None)
DIGIT_RUN = re.compile(r"\d(?:[\s-]?\d){12,18}")  # 13 to 19 digits, single spaces or dashes between them
AADHAAR = re.compile(r"(?<!\d)[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
NAMED = re.compile(r"\b(aadhaa?r|pan (card|number|no\.?))\b", re.IGNORECASE)  # "pan pizza" is food
VERHOEFF_D = ((0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5), (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
              (3, 4, 0, 1, 2, 8, 9, 5, 6, 7), (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
              (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3), (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
              (9, 8, 7, 6, 5, 4, 3, 2, 1, 0))
VERHOEFF_P = ((0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4), (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
              (8, 9, 1, 6, 0, 4, 3, 5, 2, 7), (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
              (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8))


def luhn(digits):
    """Whether a digit string passes the card-number checksum."""
    total, odd = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if odd:
            n = n * 2 - 9 if n > 4 else n * 2
        total, odd = total + n, not odd
    return bool(digits) and total % 10 == 0


def verhoeff(digits):
    """Whether a digit string passes the Verhoeff checksum (every Aadhaar number does)."""
    check = 0
    for i, ch in enumerate(reversed(digits)):
        check = VERHOEFF_D[check][VERHOEFF_P[i % 8][int(ch)]]
    return bool(digits) and check == 0


def secret_number(text):
    """Whether text holds a card number (Luhn), an Aadhaar number (Verhoeff), or a PAN."""
    text = str(text or "")
    return (any(luhn(re.sub(r"\D", "", m[0])) for m in DIGIT_RUN.finditer(text))
            or any(verhoeff(re.sub(r"\D", "", m[0])) for m in AADHAAR.finditer(text)) or bool(PAN.search(text)))


def storable(text):
    """Whether text may be kept: never a card number, an Aadhaar number, a PAN, or words naming a secret (an OTP,
    password, CVV, PIN, card number or details, expiry, UPI PIN, Aadhaar, PAN card)."""
    text = str(text or "")
    return not (secret_number(text) or NAMED.search(text) or rules.SECRET_ASK.search(text)
                or rules.CARD_DETAIL.search(text))


def variants(key, value):
    """(value, placeholder) pairs a redaction registry hides for one ID, with common reformattings: a phone number's
    digits, last ten, and leading zero; a name's parts of three letters or more."""
    value = str(value or "").strip()
    if not value:
        return []
    placeholder = f"[profile:{key}]"
    pairs = [(value, placeholder)]
    if key == "phone":
        digits = re.sub(r"\D", "", value)
        pairs += [(digits, placeholder), (digits[-10:], placeholder), ("0" + digits[-10:], placeholder)]
    if key == "name":
        pairs += [(part, placeholder) for part in value.split() if len(part) >= 3]
    return pairs


class Secrets:
    """One task's registry of values no model, log, or message may contain, and the fields that hold them."""

    def __init__(self, pairs=()):
        self.values = {}
        self.nodes = set()  # (frame, node) of fields that received a secret
        for value, placeholder in pairs:
            self.add(value, placeholder)

    def add(self, value, placeholder):
        value = str(value or "").strip()
        if len(value) < 3:
            return
        digits = re.sub(r"[\s-]", "", value)
        if digits.isdigit() and len(digits) >= 3:  # card numbers, phones, codes, and CVVs, in any spacing
            joined = r"[\s().-]*".join(digits)  # (brackets, dots, dashes)
            if len(digits) <= 6:  # CVV, OTP, PIN: never part of a larger number ("₹1,499", "₹1 499", "₹499.00")
                pattern = r"(?<![\d.,])(?<!\d[^\S\r\n])" + joined + r"(?![.,]?\d)"  # a space, never a line break
            else:  # phone, card: any neighbour but a digit ("Mob.9876543210", "Name,9876543210,City")
                pattern = r"(?<!\d)" + joined + r"(?!\d)"
        else:
            pattern = r"(?<!\w)" + re.escape(value) + r"(?!\w)"
        self.values[value] = (re.compile(pattern, re.IGNORECASE), placeholder)

    def text(self, s):
        if s.startswith("data:image/"):
            return s
        for value in sorted(self.values, key=len, reverse=True):
            regex, placeholder = self.values[value]
            s = regex.sub(lambda _match: placeholder, s)
        return s

    def redact(self, obj):
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, list):
            return [self.redact(item) for item in obj]
        if isinstance(obj, dict):
            return {self.redact(key): self.redact(value) for key, value in obj.items()}
        return obj

    def leaks(self, obj):
        """Whether a registered value survives in `obj`: any string, dictionary keys included, images excepted."""
        if isinstance(obj, str):
            return not obj.startswith("data:image/") and any(regex.search(obj) for regex, _ in self.values.values())
        if isinstance(obj, list):
            return any(self.leaks(item) for item in obj)
        if isinstance(obj, dict):
            return any(self.leaks(key) or self.leaks(value) for key, value in obj.items())
        return False
