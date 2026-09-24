from copy import deepcopy

import pytest
from reliability_fakes import observed

from assistant.commit import money_minor, promotion_advice, review_total, validate_review
from assistant.state import cite, observe_state


def line(id, amount, role="fee", parent=None):
    return dict(id=id, label=id, amount_minor=amount, currency="INR", role=role,
                included_in=parent, evidence_ids=["e1"])


@pytest.mark.parametrize("text,expected", [("₹1,23,456.78", 12345678),
    ("INR 123,456.78", 12345678), ("-₹10.50", -1050), ("₹0.01", 1), ("123", 12300)])
def test_exact_money(text, expected):
    assert money_minor(text, currency="INR", exponent=2) == expected


@pytest.mark.parametrize("text", ["1.234,56", "1,23", "1,234,56", "1.001", "NaN",
                                  "Infinity", "1e3", "$1", "1 234", "₹1 USD"])
def test_unsupported_money(text):
    with pytest.raises(ValueError):
        money_minor(text, currency="INR", exponent=2)


def test_exponent_and_bool():
    for exponent in [True, -1, 4, None]:
        with pytest.raises(ValueError):
            money_minor("1", currency="INR", exponent=exponent)
    assert not review_total([line("fee", True)], 1, currency="INR")["valid"]


def test_included_tax_items_discounts_rounding_and_later_charges():
    lines = [line("subtotal", 10000, "subtotal"), line("item", 10000, "item", "subtotal"),
             line("tax", 500, "tax", "subtotal"), line("rain", 1000),
             line("discount", -101, "discount"), line("rounding", 1, "rounding"),
             line("toll", 900, "later_estimate")]
    assert review_total(lines, 10900, currency="INR") == dict(valid=True, computed_minor=10900, reasons=[])
    assert not review_total(lines, 10000, currency="INR")["valid"]


@pytest.mark.parametrize("lines", [[], [line("a", 1, parent="b")],
    [line("a", 1, parent="b"), line("b", 1, parent="a")],
    [line("a", 1), line("a", 1)], [line("a", 1, "discount")],
    [line("s", 100, "subtotal"), line("i", 99, "item", "s")],
    [line("s", 100, "subtotal"), line("i", 100, "item")],
    [dict(line("a", 1), currency="USD")]])
def test_invalid_breakdown(lines):
    assert not review_total(lines, 100, currency="INR")["valid"]


def basket(total, delivery, subtotal):
    return dict(total_minor=total, delivery_minor=delivery, qualifying_subtotal_minor=subtotal,
                currency="INR", evidence_ids=["e"])


@pytest.mark.parametrize("operator,subtotal,eligible", [("gt", 19900, False), ("gte", 19900, True),
                                                      ("gt", 19000, False), ("gt", 29000, True)])
def test_promotion_threshold(operator, subtotal, eligible):
    result = promotion_advice(basket(19000, 3500, 14500), basket(30000, 0, subtotal),
        dict(operator=operator, threshold_minor=19900, eligible=True,
             conditions_verified=True, evidence_ids=["terms"]))
    assert result == dict(eligible=eligible, extra_spend_minor=11000, delivery_saving_minor=3500, verified=True)


@pytest.mark.parametrize("change", [dict(eligible=None), dict(conditions_verified=False),
                                    dict(evidence_ids=[]), dict(operator="unknown")])
def test_unknown_membership_payment_or_terms_cannot_promise(change):
    terms = dict(operator="gt", threshold_minor=19900, eligible=True,
                 conditions_verified=True, evidence_ids=["terms"], **{})
    terms.update(change)
    assert not promotion_advice(basket(19000, 3500, 14500), basket(30000, 0, 29000), terms)["verified"]


def test_expired_or_excluded_offer():
    result = promotion_advice(basket(19000, 3500, 14500), basket(30000, 0, 29000),
        dict(operator="gte", threshold_minor=19900, eligible=False,
             conditions_verified=True, evidence_ids=["expired"]))
    assert result["verified"] and not result["eligible"]


def review_fixture():
    text = "Shop\nHome\nTomorrow\nVisa ••1234\nTea × 2 ₹100\nRain fee ₹10\nTotal ₹110"
    s, page, oid = observed(text)
    eid = cite(s, oid, text)
    p = dict(task_id=s.task_id, revision=s.revision, action="purchase", service="Shop", account="",
             destination="Home", items=["Tea × 2"], option="", timing="Tomorrow", payment="Visa ••1234",
             currency="INR", total_minor=11000, total_kind="exact", later_terms=[],
             lines=[dict(line("tea", 10000, "item"), label="Tea × 2", evidence_ids=[eid]),
                    dict(line("rain", 1000), label="Rain fee", evidence_ids=[eid])], evidence_ids=[eid])
    extraction = dict(exponent=2, total_text="₹110", total_quote="Total ₹110",
                      amounts={"tea": "₹100", "rain": "₹10"},
                      line_quotes={"tea": "Tea × 2 ₹100", "rain": "Rain fee ₹10"})
    return s, page, p, extraction


def test_evidence_bound_review():
    s, _, p, x = review_fixture()
    assert validate_review(s, p, x) == p
    assert len(p["lines"]) == 2


@pytest.mark.parametrize("change", ["quantity", "line_terms", "amount", "fee", "stale", "truncated", "total"])
def test_rejects_changed_or_unobserved_materials(change):
    s, page, p, x = review_fixture()
    if change == "quantity":
        p["items"] = ["Tea × 3"]
    elif change == "line_terms":
        p["lines"][0]["label"] = "Tea × 3"
    elif change == "amount":
        x["amounts"]["tea"] = "₹99"
    elif change == "fee":
        p["lines"][1]["label"] = "Invented fee"
    elif change == "stale":
        observe_state(s, page)
    elif change == "truncated":
        s.observations[next(reversed(s.observations))]["completeness"] = {"text_truncated": True}
    else:
        x["total_quote"] = "Tea × 2 ₹100"
    with pytest.raises(ValueError):
        validate_review(s, deepcopy(p), x)


def test_extraction_job_uses_full_observation_and_validates(monkeypatch):
    from assistant import jobs
    s, page, p, x = review_fixture()
    calls = []
    def client(system, payload, **kwargs):
        calls.append(payload)
        return x, {"fake": True}
    monkeypatch.setattr(jobs, "text_json", client)
    result, metadata = jobs.review_proposal(s, p)
    assert result == p and metadata == {"fake": True}
    assert calls[0]["observation"]["text"] == page["text"]
    x["amounts"]["rain"] = "₹0"
    with pytest.raises(ValueError):
        jobs.review_proposal(s, p)


@pytest.mark.parametrize("key,value", [("omitted_actions", 1), ("frames_incomplete", ["frame"] )])
def test_unread_regions_block_review(key, value):
    s, _, p, x = review_fixture()
    s.observations[next(reversed(s.observations))]["completeness"] = {key: value}
    with pytest.raises(ValueError):
        validate_review(s, p, x)


def test_coordinator_rejects_unreconciled_proposal():
    from assistant.coordinator import _validate_proposal
    s, _, p, _ = review_fixture()
    p["total_minor"] = 10000
    with pytest.raises(ValueError, match="breakdown"):
        _validate_proposal(s, p)


def test_amount_substring_cannot_be_extracted_as_smaller_fee():
    s, _, p, x = review_fixture()
    p["lines"][0]["amount_minor"] = 1000
    p["total_minor"] = 2000
    x["amounts"]["tea"] = "₹10"
    x["total_text"] = "₹20"
    x["total_quote"] = "Total ₹20"
    # Evidence contains the new total but the item still costs 100, not 10.
    text = "Shop Home Tomorrow Visa ••1234 Tea × 2 ₹100 Rain fee ₹10 Total ₹20"
    oid = observe_state(s, {"url": "https://shop.test", "text": text})
    eid = cite(s, oid, text)
    p["evidence_ids"] = [eid]
    for row in p["lines"]:
        row["evidence_ids"] = [eid]
    with pytest.raises(ValueError):
        validate_review(s, p, x)


def test_model_cannot_choose_wrong_currency_exponent():
    s, _, p, x = review_fixture()
    x["exponent"] = 0
    p["total_minor"] = 110
    for row in p["lines"]:
        row["amount_minor"] //= 100
    with pytest.raises(ValueError):
        validate_review(s, p, x)


def test_subtotal_coverage_includes_applied_discount():
    lines = [line("s", 9000, "subtotal"), line("i", 10000, "item", "s"),
             line("d", -1000, "discount", "s"), line("tax", 500, "tax", "i")]
    assert review_total(lines, 9000, currency="INR")["valid"]


@pytest.mark.parametrize("bad", ["e", [True], [], [""]])
def test_promotion_requires_evidence_id_lists(bad):
    base = basket(19000, 3500, 14500)
    base["evidence_ids"] = bad
    assert not promotion_advice(base, basket(30000, 0, 29000),
        dict(operator="gte", threshold_minor=19900, eligible=True,
             conditions_verified=True, evidence_ids=["terms"]))["verified"]


def test_review_rejects_unmasked_card_label():
    s, _, p, x = review_fixture()
    p["payment"] = "Visa 4111 1111 1111 1111"
    text = "Shop Home Tomorrow Visa 4111 1111 1111 1111 Tea × 2 ₹100 Rain fee ₹10 Total ₹110"
    oid = observe_state(s, {"url": "https://shop.test", "text": text})
    eid = cite(s, oid, text)
    p["evidence_ids"] = [eid]
    for row in p["lines"]:
        row["evidence_ids"] = [eid]
    with pytest.raises(ValueError):
        validate_review(s, p, x)


def test_truncated_row_quote_cannot_shorten_an_observed_amount():
    s, _, p, x = review_fixture()
    p["total_minor"] = 1100
    for row in p["lines"]:
        row["amount_minor"] //= 10
    x["total_text"], x["total_quote"] = "₹11", "Total ₹11"
    x["amounts"] = {"tea": "₹10", "rain": "₹1"}
    x["line_quotes"] = {"tea": "Tea × 2 ₹10", "rain": "Rain fee ₹1"}
    with pytest.raises(ValueError):
        validate_review(s, p, x)


@pytest.mark.parametrize("token", ["$100", "$ 100", "USD 100", "100 USD", "€100", "100 EUR"])
@pytest.mark.parametrize("target", ["line", "total"])
def test_review_rejects_stripped_foreign_currency(token, target):
    s, _, p, x = review_fixture()
    row = "Tea × 2 " + token if target == "line" else "Total " + token.replace("100", "110")
    text = "Shop Home Tomorrow Visa ••1234\n" + (
        row + "\nRain fee ₹10\nTotal ₹110" if target == "line"
        else "Tea × 2 ₹100\nRain fee ₹10\n" + row)
    oid = observe_state(s, {"url": "https://shop.test", "text": text})
    eid = cite(s, oid, text)
    p["evidence_ids"] = [eid]
    for item in p["lines"]:
        item["evidence_ids"] = [eid]
    if target == "line":
        x["amounts"]["tea"], x["line_quotes"]["tea"] = "100", row
    else:
        x["total_text"], x["total_quote"] = "110", row
    with pytest.raises(ValueError):
        validate_review(s, p, x)


@pytest.mark.parametrize("outer,inner,item,valid", [(10000, 20000, 20000, False),
    (20000, 20000, 10000, False), (20000, 20000, 20000, True)])
def test_nested_subtotals_reconcile_at_every_level(outer, inner, item, valid):
    lines = [line("outer", outer, "subtotal"), line("inner", inner, "subtotal", "outer"),
             line("item", item, "item", "inner"), line("tax", 500, "tax", "inner")]
    assert review_total(lines, outer, currency="INR")["valid"] is valid


def test_currency_context_cannot_be_removed_from_row_and_evidence_quotes():
    s, _, p, x = review_fixture()
    text = "Shop Home Tomorrow Visa ••1234\nTea × 2 ₹100\nRain fee ₹10\nTotal 110 USD"
    oid = observe_state(s, {"url": "https://shop.test", "text": text})
    eid = cite(s, oid, text.removesuffix(" USD"))
    p["evidence_ids"] = [eid]
    for item in p["lines"]:
        item["evidence_ids"] = [eid]
    x["total_text"], x["total_quote"] = "110", "Total 110"
    with pytest.raises(ValueError):
        validate_review(s, p, x)


@pytest.mark.parametrize("token", ["INR 110", "110 INR", "₹110"])
def test_complete_matching_currency_token_remains_valid(token):
    s, _, p, x = review_fixture()
    text = "Shop Home Tomorrow Visa ••1234\nTea × 2 ₹100\nRain fee ₹10\nTotal " + token
    oid = observe_state(s, {"url": "https://shop.test", "text": text})
    eid = cite(s, oid, text)
    p["evidence_ids"] = [eid]
    for item in p["lines"]:
        item["evidence_ids"] = [eid]
    x["total_text"], x["total_quote"] = token, "Total " + token
    assert validate_review(s, p, x) == p


@pytest.mark.parametrize("separator", ["\u202f", "\u2009", "\u2007", "\u2002", "\u3000"])
@pytest.mark.parametrize("prefix", [True, False])
def test_unicode_spacing_cannot_hide_currency_context(separator, prefix):
    s, _, p, x = review_fixture()
    token = "€" + separator + "110" if prefix else "110" + separator + "EUR"
    text = "Shop Home Tomorrow Visa ••1234\nTea × 2 ₹100\nRain fee ₹10\nTotal " + token
    oid = observe_state(s, {"url": "https://shop.test", "text": text})
    eid = cite(s, oid, text)
    p["evidence_ids"] = [eid]
    for item in p["lines"]:
        item["evidence_ids"] = [eid]
    x["total_text"], x["total_quote"] = "110", "Total " + token
    with pytest.raises(ValueError):
        validate_review(s, p, x)


@pytest.mark.parametrize("token,amount", [("₹\u202f1,23,456.78", 12345678),
                                        ("123,456.78\u2009INR", 12345678)])
def test_supported_grouping_with_unicode_currency_spacing(token, amount):
    assert money_minor(token, currency="INR", exponent=2) == amount
