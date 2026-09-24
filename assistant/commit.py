"""Exact cart review primitives; no browser actions or permission grants.

The coordinator proposal shape is unchanged. validate_review additionally needs a
separate extraction record: exponent, total_text, total_quote, amounts (line ID ->
original amount text), and line_quotes (line ID -> observed contiguous row).
Task 11/12 must obtain this record after Jev inspects all review/charge sections;
then re-extract and validate after approval. A coordinator proposal alone is not
an approval-ready review. Unknown fees and unresolved material choices block it.
"""

import re
import unicodedata
from copy import deepcopy
from decimal import Decimal, localcontext

SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
# Review conversion uses code-owned exponents; unsupported currencies stay unresolved.
CURRENCY_EXPONENTS = dict.fromkeys("INR USD EUR GBP AUD CAD CHF CNY HKD SGD NZD AED SAR ZAR BRL MXN".split(), 2)
CURRENCY_EXPONENTS.update(JPY=0, KRW=0, KWD=3, BHD=3, OMR=3)
ROLES = {"item", "subtotal", "discount", "fee", "tax", "tip", "rounding", "later_estimate"}
LINE_KEYS = {"id", "label", "amount_minor", "currency", "role", "included_in", "evidence_ids"}


def money_minor(text: str, *, currency: str, exponent: int) -> int:
    """Accept dot decimals and unambiguous Western/Indian comma grouping only."""
    if (not isinstance(text, str) or len(text) > 128 or not isinstance(currency, str)
            or not re.fullmatch(r"[A-Z]{3}", currency) or type(exponent) is not int
            or exponent not in {0, 1, 2, 3}):
        raise ValueError("money: unsupported currency, exponent or representation")
    value = text.strip()
    sign = ""
    if value.startswith(("-", "+")):
        sign, value = value[0], value[1:].strip()
    tokens = [currency] + ([SYMBOLS[currency]] if currency in SYMBOLS else [])
    for token in tokens:
        if value.startswith(token):
            value = value[len(token):].strip()
            break
        if value.endswith(token):
            value = value[:-len(token)].strip()
            break
    if not sign and value.startswith(("-", "+")):
        sign, value = value[0], value[1:]
    integer = r"(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]?(?:,[0-9]{2})+,[0-9]{3})"
    if not re.fullmatch(integer + r"(?:\.[0-9]+)?", value):
        raise ValueError("money: ambiguous separators or unsupported representation")
    with localcontext() as context:
        context.prec = 140
        minor = Decimal(sign + value.replace(",", "")) * (10 ** exponent)
        if minor != minor.to_integral_value():
            raise ValueError("money: fractional minor units")
        return int(minor)


def review_total(lines: list[dict], total_minor: int, *, currency: str) -> dict:
    """Validate the inclusion graph and coverage before summing payable roots."""
    reasons = []
    if (type(total_minor) is not int or total_minor < 0 or not isinstance(currency, str)
            or not re.fullmatch(r"[A-Z]{3}", currency)):
        reasons.append("invalid total or currency")
    if not isinstance(lines, list) or not lines:
        return dict(valid=False, computed_minor=None, reasons=[*reasons, "missing breakdown"])
    by_id = {}
    for line in lines:
        if (not isinstance(line, dict) or set(line) != LINE_KEYS
                or any(not isinstance(line[k], str) or not line[k].strip() for k in ("id", "label"))
                or type(line["amount_minor"]) is not int or line["currency"] != currency
                or not isinstance(line["role"], str) or line["role"] not in ROLES
                or not isinstance(line["evidence_ids"], list) or not line["evidence_ids"]
                or not all(isinstance(e, str) and e for e in line["evidence_ids"])
                or (line["included_in"] is not None and not isinstance(line["included_in"], str))):
            reasons.append("invalid monetary line")
            continue
        if line["id"] in by_id:
            reasons.append("duplicate line ID")
        by_id[line["id"]] = line
        if ((line["role"] == "discount" and line["amount_minor"] > 0)
                or (line["role"] not in {"discount", "rounding"} and line["amount_minor"] < 0)):
            reasons.append("invalid signed amount")
    if reasons:
        return dict(valid=False, computed_minor=None, reasons=reasons)
    for line in lines:
        seen = {line["id"]}
        parent = line["included_in"]
        while parent is not None:
            if parent not in by_id or parent in seen:
                reasons.append("unknown inclusion reference or cycle")
                break
            seen.add(parent)
            if by_id[parent]["role"] == "later_estimate" and line["role"] != "later_estimate":
                reasons.append("payable line hidden in later estimate")
            parent = by_id[parent]["included_in"]
    roots = [line for line in lines if line["included_in"] is None and line["role"] != "later_estimate"]
    if not roots:
        reasons.append("missing payable coverage")
    if any(row["role"] == "subtotal" for row in roots) and any(row["role"] == "item" for row in roots):
        reasons.append("item/subtotal coverage is ambiguous")
    for subtotal in (row for row in lines if row["role"] == "subtotal"):
        children = [row for row in lines if row["included_in"] == subtotal["id"]
                    and row["role"] in {"item", "subtotal"}]
        adjustments = [row for row in lines if row["included_in"] == subtotal["id"]
                       and row["role"] in {"discount", "fee", "tip", "rounding"}]
        if children and sum(row["amount_minor"] for row in children + adjustments) != subtotal["amount_minor"]:
            reasons.append("item amounts do not cover subtotal")
    computed = sum(line["amount_minor"] for line in roots)
    if computed != total_minor:
        reasons.append("unexplained total mismatch")
    return dict(valid=not reasons, computed_minor=computed, reasons=reasons)


def _evidence_ids(value):
    return isinstance(value, list) and bool(value) and all(isinstance(e, str) and e.strip() for e in value)


def promotion_advice(base: dict, alternative: dict, terms: dict) -> dict:
    """Compare verified baskets; ineligibility and unknown conditions are distinct."""
    unknown = dict(eligible=False, extra_spend_minor=None, delivery_saving_minor=None, verified=False)
    for basket in (base, alternative):
        if (not isinstance(basket, dict) or not _evidence_ids(basket.get("evidence_ids"))
                or not isinstance(basket.get("currency"), str)
                or not re.fullmatch(r"[A-Z]{3}", basket["currency"])
                or any(type(basket.get(k)) is not int or basket[k] < 0
                       for k in ("total_minor", "delivery_minor", "qualifying_subtotal_minor"))
                or basket["delivery_minor"] > basket["total_minor"]):
            return unknown
    if (not isinstance(terms, dict) or base["currency"] != alternative["currency"]
            or not isinstance(terms.get("operator"), str) or terms["operator"] not in {"gt", "gte"}
            or not _evidence_ids(terms.get("evidence_ids"))
            or terms.get("conditions_verified") is not True or type(terms.get("eligible")) is not bool
            or type(terms.get("threshold_minor")) is not int or terms["threshold_minor"] < 0):
        return unknown
    subtotal, threshold = alternative["qualifying_subtotal_minor"], terms["threshold_minor"]
    threshold_met = subtotal > threshold if terms["operator"] == "gt" else subtotal >= threshold
    return dict(eligible=terms["eligible"] and threshold_met,
                extra_spend_minor=alternative["total_minor"] - base["total_minor"],
                delivery_saving_minor=base["delivery_minor"] - alternative["delivery_minor"], verified=True)


def _amount_in_quote(text, quote):
    # Substrings of larger numeric tokens are not observed amounts.
    return bool(re.search(r"(?<![0-9.,+\-])" + re.escape(text) + r"(?![0-9.,])", quote))


def _money_in_source(text, quote, source):
    """Keep adjacent currency/sign context at the exact row occurrence in the page.

    Checking only the model's row quote would allow it to trim a suffix currency
    from that quote as well as from the amount. Unsupported markers fail closed.
    """
    for row in re.finditer(re.escape(quote), source):
        for amount in re.finditer(r"(?<![0-9.,+\-])" + re.escape(text) + r"(?![0-9.,])", quote):
            start, end = row.start() + amount.start(), row.start() + amount.end()
            left, right = source[:start].rstrip(" \t\u00a0"), source[end:].lstrip(" \t\u00a0")
            if (left and (unicodedata.category(left[-1]) == "Sc" or left[-1] in "+-")):
                continue
            if right and unicodedata.category(right[0]) == "Sc":
                continue
            if re.search(r"(?<![A-Za-z])[A-Z]{3}$", left) or re.match(r"[A-Z]{3}(?![A-Za-z])", right):
                continue
            return True
    return False


def validate_review(state, proposal: dict, extraction: dict) -> dict:
    """Return a copied proposal only when current page evidence supports its review.

    This does not assert cart inspection is complete or authorize a commitment.
    Integration must resolve unread panels and material choices before calling it.
    """
    from .coordinator import _validate_proposal

    _validate_proposal(state, proposal)
    latest = next(reversed(state.observations), None)
    observation = state.observations.get(latest, {})
    completeness = observation.get("completeness", {})
    if any(v for k, v in completeness.items()
           if "truncat" in k or "missing" in k or "incomplete" in k or k == "omitted_actions"):
        raise ValueError("review: incomplete observation")

    def quotes(ids):
        if not isinstance(ids, list) or not ids:
            raise ValueError("review: missing evidence")
        result = []
        for eid in ids:
            e = state.evidence.get(eid, {})
            if (e.get("observation_id") != latest or e.get("revision") != state.revision
                    or e.get("kind") != "page"
                    or not _amount_in_quote(e.get("quote", ""), observation.get("text", ""))):
                raise ValueError("review: stale or non-page evidence")
            result.append(e["quote"])
        return result

    if re.search(r"(?<![0-9])(?:[0-9][ -]?){12,19}(?![0-9])", proposal["payment"]):
        raise ValueError("review: payment label must be masked")
    material = quotes(proposal["evidence_ids"])
    for value in [proposal[k] for k in ("service", "account", "destination", "option", "timing", "payment")] + (
            proposal["items"] + proposal["later_terms"]):
        if value and not any(_amount_in_quote(value, quote) for quote in material):
            raise ValueError("review: material detail absent from evidence")
    if proposal["total_kind"] == "not_applicable":
        return deepcopy(proposal)
    if not proposal["items"] or not proposal["destination"] or not proposal["timing"] or not proposal["payment"]:
        raise ValueError("review: missing material purchase details")
    if proposal["total_kind"] == "estimate" and not proposal["later_terms"]:
        raise ValueError("review: estimate needs observed variable-charge terms")
    if not isinstance(extraction, dict) or set(extraction) != {
            "exponent", "total_text", "total_quote", "amounts", "line_quotes"}:
        raise ValueError("review: missing original amount extraction")
    total_text, total_quote = extraction["total_text"], extraction["total_quote"]
    if (not isinstance(total_quote, str) or not isinstance(total_text, str) or not total_text
            or not _amount_in_quote(total_text, total_quote)
            or not _money_in_source(total_text, total_quote, observation["text"])
            or not any(_amount_in_quote(total_quote, q) for q in material)):
        raise ValueError("review: total not observed")
    currency, exponent = proposal["currency"], extraction["exponent"]
    if type(exponent) is not int or currency not in CURRENCY_EXPONENTS or exponent != CURRENCY_EXPONENTS[currency]:
        raise ValueError("review: unsupported currency exponent")
    if money_minor(total_text, currency=currency, exponent=exponent) != proposal["total_minor"]:
        raise ValueError("review: total extraction mismatch")
    ids = {line["id"] for line in proposal["lines"]}
    if (not isinstance(extraction["amounts"], dict) or not isinstance(extraction["line_quotes"], dict)
            or set(extraction["amounts"]) != ids or set(extraction["line_quotes"]) != ids):
        raise ValueError("review: missing line extraction")
    for line in proposal["lines"]:
        text, quote = extraction["amounts"][line["id"]], extraction["line_quotes"][line["id"]]
        if (not isinstance(text, str) or not text or not isinstance(quote, str)
                or not _amount_in_quote(text, quote) or line["label"] not in quote
                or not _money_in_source(text, quote, observation["text"])
                or not any(_amount_in_quote(quote, q) for q in quotes(line["evidence_ids"]))
                or money_minor(text, currency=currency, exponent=exponent) != line["amount_minor"]):
            raise ValueError("review: line extraction mismatch")
        if line["role"] == "later_estimate" and not proposal["later_terms"]:
            raise ValueError("review: later charges need disclosed terms")
    result = review_total(proposal["lines"], proposal["total_minor"], currency=currency)
    if not result["valid"]:
        raise ValueError("review: " + "; ".join(result["reasons"]))
    return deepcopy(proposal)
