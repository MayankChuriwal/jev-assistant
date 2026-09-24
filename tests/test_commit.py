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


def test_item_price_and_destination_cannot_keep_old_proposal_approved():
    from assistant.commit import proposal_key
    _, _, p, _ = review_fixture()
    assert proposal_key(p) != proposal_key({**p, 'total_minor': 9500})
    assert proposal_key(p) != proposal_key({**p, 'destination': 'Office'})
    assert proposal_key(p) == proposal_key({**p, 'evidence_ids': ['fresh']})
    changed = deepcopy(p)
    changed['lines'][0]['amount_minor'] += 100
    changed['lines'][1]['amount_minor'] -= 100
    assert proposal_key(p) != proposal_key(changed)


def confirmation(s, p):
    from assistant.commit import bind_proposal_effect, proposal_key
    effect_id = bind_proposal_effect(s, p, operation='CLICK', choice='e6')
    s.questions['q1'] = dict(id='q1', kind='confirm', revision=s.revision, displayed_at=1,
                             answered_at=None, proposal_key=proposal_key(p), effect_id=effect_id)


def test_approval_requires_validated_review_and_current_affirmative_question():
    from assistant.commit import approve, can_commit, consume_approval
    s, page, p, x = review_fixture()
    with pytest.raises(ValueError):
        approve(s, p, question_id='q1', response='yes')
    validate_review(s, p, x)
    confirmation(s, p)
    for response in ['no', 'yes but change address', 'maybe', '']:
        with pytest.raises(ValueError):
            approve(s, p, question_id='q1', response=response)
    a = approve(s, p, question_id='q1', response='yes')
    p = fresh_review(s, page, p, x)
    assert can_commit(s, p)
    assert consume_approval(s, p) == a['id']
    assert not can_commit(s, p)
    with pytest.raises(ValueError):
        consume_approval(s, p)


def test_approval_fresh_review_cosmetic_rerender_and_restart():
    from assistant.commit import approve, can_commit
    s, page, p, x = review_fixture()
    validate_review(s, p, x)
    confirmation(s, p)
    approve(s, p, question_id='q1', response='yes')
    oid = observe_state(s, page)
    assert not can_commit(s, p)
    fresh = deepcopy(p)
    eid = cite(s, oid, page['text'])
    fresh['evidence_ids'] = [eid]
    for row in fresh['lines']:
        row['evidence_ids'] = [eid]
    validate_review(s, fresh, x)
    assert can_commit(s, fresh)
    recovered = deepcopy(s)
    assert not can_commit(recovered, fresh)


def bound(s, page, **changes):
    from assistant.commit import bind_decision
    return bind_decision(s, dict(id='d1', operation='CLICK', choice='e1', **changes), page,
                         assessment='preparation')


def test_uncertain_dispatch_is_recorded_and_never_replayed(tmp_path):
    from assistant.browser import ExecutionUncertain
    from assistant.commit import Journal, dispatch_once
    s, page, _ = observed()
    journal = Journal(tmp_path / 'task.jsonl', redact=lambda x: x)
    calls = []
    def act():
        calls.append(1)
        assert journal.records()[-1]['phase'] == 'dispatching'
        raise ExecutionUncertain('transport disconnected')
    decision = bound(s, page)
    result = dispatch_once(s, journal, decision, page, act=act,
                           is_fresh=lambda: True, interrupted=lambda: False)
    assert result['status'] == 'unknown'
    with pytest.raises(ValueError):
        dispatch_once(s, journal, decision, page, act=act,
                      is_fresh=lambda: True, interrupted=lambda: False)
    assert calls == [1] and journal.records()[-1]['phase'] == 'unknown'


@pytest.mark.parametrize('when', ['before', 'during_write', 'amendment', 'intent_failure', 'result_failure', 'stale'])
def test_dispatch_interruptions_and_disk_failures_never_repeat(tmp_path, monkeypatch, when):
    from assistant.browser import StalePage
    from assistant.commit import Journal, dispatch_once
    from assistant.state import amend
    s, page, _ = observed()
    journal = Journal(tmp_path / 'task.jsonl', redact=lambda x: x)
    decision = bound(s, page)
    calls = []
    cancelled = when == 'before'
    append = journal.append
    def write(record):
        nonlocal cancelled
        if when == 'intent_failure' or (when == 'result_failure' and record['phase'] != 'dispatching'):
            raise OSError('disk full')
        append(record)
        if when == 'during_write':
            cancelled = True
    monkeypatch.setattr(journal, 'append', write)
    if when == 'amendment':
        amend(s, 'different address', {'address'})
    def act():
        calls.append(1)
        if when == 'stale':
            raise StalePage('pre-input stale')
        return {}
    kwargs = dict(act=act, is_fresh=lambda: True, interrupted=lambda: cancelled)
    if when in {'before', 'amendment', 'intent_failure'}:
        with pytest.raises((ValueError, OSError)):
            dispatch_once(s, journal, decision, page, **kwargs)
    else:
        result = dispatch_once(s, journal, decision, page, **kwargs)
        assert result['status'] == ('unknown' if when == 'result_failure' else 'not_dispatched')
    assert len(calls) == int(when in {'result_failure', 'stale'})


def test_journal_recovery_privacy_and_unresolved_commitments(tmp_path):
    import os

    from assistant.commit import Journal
    path = tmp_path / 'private' / 'task.jsonl'
    j = Journal(path, redact=lambda x: {k: '[redacted]' if k == 'secret' else v for k, v in x.items()})
    intent = dict(execution_id='d1', phase='dispatching', commitment=True, account_key='opaque', secret='123')
    j.append(intent)
    j.append(dict(intent, phase='executed'))
    assert j.interrupted()[0]['execution_id'] == 'd1'
    assert '123' not in path.read_text()
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    with path.open('ab') as f:
        f.write(b'{"incomplete"')
    assert len(j.records()) == 2
    with pytest.raises(ValueError):
        j.append(dict(execution_id='d2', phase='dispatching'))
    link = tmp_path / 'link.jsonl'
    link.symlink_to(path)
    with pytest.raises((ValueError, OSError)):
        Journal(link, redact=lambda x: x).records()


def test_missing_evidence_after_review_and_mutated_authority_fail_closed():
    from assistant.commit import approve, can_commit
    s, _, p, x = review_fixture()
    validate_review(s, p, x)
    confirmation(s, p)
    approve(s, p, question_id='q1', response='yes')
    s.evidence.clear()
    assert not can_commit(s, p)


def test_commit_dispatch_consumes_once_and_restores_account_reservation(tmp_path):
    from assistant.commit import Journal, approve, bind_decision, dispatch_once
    s, page, p, x = review_fixture()
    validate_review(s, p, x)
    confirmation(s, p)
    approve(s, p, question_id='q1', response='yes')
    p = fresh_review(s, page, p, x)
    s.progress['account_reservation_key'] = 'site-account-opaque'
    d = bind_decision(s, dict(id='d1', operation='CLICK', choice='e6'), page, assessment='preparation',
                      effect_id=purchase_effect(s, p))
    j = Journal.create(tmp_path / 'journal', redact=lambda x: x)
    calls = []
    def act():
        calls.append(1)
        assert s.approval['consumed']
        return {'browser_receipt': 'ok'}
    r = dispatch_once(s, j, d, page, act=act, is_fresh=lambda: True,
                      interrupted=lambda: False, proposal=p)
    assert r['status'] == 'executed' and calls == [1]
    unresolved = Journal(j.path, redact=lambda x: x).interrupted()
    assert unresolved[0]['account_key'] == 'site-account-opaque'
    assert unresolved[0]['summary']['objective'] == s.objective
    d2 = bind_decision(s, dict(id='d2', operation='CLICK', choice='e1'), page, assessment='preparation')
    with pytest.raises(ValueError):
        dispatch_once(s, j, d2, page, act=act, is_fresh=lambda: True, interrupted=lambda: False)
    assert calls == [1]
    j.append(dict(execution_id='d1', phase='outcome', outcome='success', evidence_ids=['e-confirm']))
    assert j.interrupted() == []


def test_decision_target_must_be_observed_and_owned(tmp_path):
    from assistant.commit import Journal, bind_decision, dispatch_once
    s, page, _ = observed()
    forged = deepcopy(page)
    forged['actions'][0]['label'] = 'Forged field'
    with pytest.raises(ValueError):
        bind_decision(s, dict(id='d1', operation='TYPE_TEXT', choice='e1'), forged, assessment='preparation')
    d = bound(s, page)
    calls = []
    j = Journal(tmp_path / 'j.jsonl', redact=lambda x: x)
    with pytest.raises(ValueError):
        dispatch_once(s, j, {**d, 'choice': 'e6'}, page, act=lambda: calls.append(1),
                      is_fresh=lambda: True, interrupted=lambda: False)
    assert calls == []


def test_redaction_failure_after_input_retains_unknown_and_blocks(tmp_path):
    from assistant.commit import Journal, dispatch_once
    s, page, _ = observed()
    def redact(obj):
        if obj == {'payload': 'result'}:
            raise RuntimeError('redactor failure')
        return obj
    j = Journal(tmp_path / 'j.jsonl', redact=redact)
    d = bound(s, page)
    r = dispatch_once(s, j, d, page, act=lambda: {'payload': 'result'},
                      is_fresh=lambda: True, interrupted=lambda: False)
    assert r['status'] == 'unknown'
    assert j.interrupted()


def test_challenge_expiry_and_wrong_target_block_input(tmp_path):
    from assistant.commit import Journal, dispatch_once
    s, page, oid = observed()
    s.challenges['c1'] = dict(revision=0, origin='https://shop.test', status='challenge',
        observation_id=oid, fields=['e2'], expires_at=1, purpose='login', field_role='otp')
    d = bound(s, page)
    j = Journal(tmp_path / 'j.jsonl', redact=lambda x: x)
    calls = []
    with pytest.raises(ValueError):
        dispatch_once(s, j, d, page, act=lambda: calls.append(1), is_fresh=lambda: True,
                      interrupted=lambda: False, challenge_id='c1')
    assert calls == [] and j.records() == []


def test_approval_requires_an_observation_after_the_user_reply():
    from assistant.commit import approve, can_commit
    s, _, p, x = review_fixture()
    validate_review(s, p, x)
    confirmation(s, p)
    approve(s, p, question_id='q1', response='yes')
    assert not can_commit(s, p)


def fresh_review(s, page, p, x):
    p = deepcopy(p)
    oid = observe_state(s, page)
    eid = cite(s, oid, page['text'])
    p['evidence_ids'] = [eid]
    for row in p['lines']:
        row['evidence_ids'] = [eid]
    return validate_review(s, p, x)


def test_secret_typing_requires_current_challenge_and_records_scope(tmp_path):
    import time

    from assistant.commit import Journal, bind_decision, dispatch_once
    s, page, oid = observed()
    d = bind_decision(s, dict(id='otp1', operation='TYPE_TEXT', choice='e2'), page,
                      assessment='authentication')
    j = Journal(tmp_path / 'j.jsonl', redact=lambda x: x)
    calls = []
    kwargs = dict(act=lambda: calls.append(1) or {}, is_fresh=lambda: True, interrupted=lambda: False)
    with pytest.raises(ValueError):
        dispatch_once(s, j, d, page, **kwargs)
    s.challenges['c1'] = dict(id='c1', revision=0, origin='https://shop.test', status='challenge',
        observation_id=oid, field_ids=['e2'], expires_at=time.time() + 30, flow_id='login1',
        purpose='login', field_role='otp', question_id='q-code')
    result = dispatch_once(s, j, d, page, challenge_id='c1', **kwargs)
    assert calls == [1] and result['status'] == 'executed'
    assert result['purpose'] == 'login' and result['field_role'] == 'otp' and result['flow_id'] == 'login1'


def test_same_state_approval_record_cannot_be_rewritten():
    from assistant.commit import approve, can_commit
    s, page, p, x = review_fixture()
    validate_review(s, p, x)
    confirmation(s, p)
    approve(s, p, question_id='q1', response='yes')
    p = fresh_review(s, page, p, x)
    s.approval['id'] = 'rewritten'
    assert not can_commit(s, p)


def test_freshness_is_rechecked_after_durable_intent(tmp_path, monkeypatch):
    from assistant.commit import Journal, dispatch_once
    s, page, _ = observed()
    d = bound(s, page)
    j = Journal(tmp_path / 'j.jsonl', redact=lambda x: x)
    fresh = True
    append = j.append
    def write(record):
        nonlocal fresh
        append(record)
        fresh = False
    monkeypatch.setattr(j, 'append', write)
    calls = []
    result = dispatch_once(s, j, d, page, act=lambda: calls.append(1), is_fresh=lambda: fresh,
                           interrupted=lambda: False)
    assert calls == [] and result['status'] == 'not_dispatched'


@pytest.mark.parametrize("case", ["approved", "unknown", "changed_hash", "purchase"])
def test_only_scoped_payment_authentication_can_follow_an_executed_commitment(tmp_path, case):
    import time

    from assistant.commit import Journal, approve, bind_decision, dispatch_once, proposal_key
    s, page, p, x = review_fixture()
    validate_review(s, p, x)
    confirmation(s, p)
    approve(s, p, question_id='q1', response='yes')
    p = fresh_review(s, page, p, x)
    s.progress['account_reservation_key'] = 'reserved-account'
    j = Journal.create(tmp_path / 'private', redact=lambda x: x)
    d = bind_decision(s, dict(id='purchase', operation='CLICK', choice='e6'), page, assessment='commitment',
                      effect_id=purchase_effect(s, p))
    dispatch_once(s, j, d, page, act=lambda: {}, is_fresh=lambda: True, interrupted=lambda: False, proposal=p)
    oid = observe_state(s, page)
    s.challenges['payment-otp'] = dict(id='payment-otp', revision=0, origin='https://shop.test',
        status='challenge', observation_id=oid, field_ids=['e2'], expires_at=time.time() + 30,
        flow_id='payment1', purpose='payment', field_role='otp', question_id='q-pay',
        commitment_execution_id='purchase', proposal_key=proposal_key(p))
    if case == 'unknown':
        s.executions['purchase']['status'] = 'unknown'
    if case == 'changed_hash':
        s.challenges['payment-otp']['proposal_key'] = 'changed-total'
    d2 = bind_decision(s, dict(id='otp', operation='TYPE_TEXT', choice='e2'), page,
                       assessment='commitment' if case == 'purchase' else 'authentication')
    calls = []
    kwargs = dict(act=lambda: calls.append(1) or {}, is_fresh=lambda: True, interrupted=lambda: False,
                  challenge_id='payment-otp')
    if case != 'approved':
        with pytest.raises(ValueError):
            dispatch_once(s, j, d2, page, **kwargs)
        assert calls == []
        return
    r = dispatch_once(s, j, d2, page, **kwargs)
    d4 = bind_decision(s, dict(id='repeated-code', operation='TYPE_TEXT', choice='e2'), page,
                       assessment='authentication')
    with pytest.raises(ValueError):
        dispatch_once(s, j, d4, page, **kwargs)
    assert r['status'] == 'executed' and r['commitment_execution_id'] == 'purchase' and calls == [1]
    d3 = bind_decision(s, dict(id='duplicate', operation='CLICK', choice='e6'), page, assessment='commitment')
    with pytest.raises(ValueError):
        dispatch_once(s, j, d3, page, proposal=p, **kwargs)
    assert calls == [1]
    assert j.interrupted()[0]['execution_id'] == 'purchase'


def test_equivalent_fresh_line_ids_keep_material_hash():
    from assistant.commit import proposal_key
    _, _, p, _ = review_fixture()
    changed = deepcopy(p)
    changed['lines'][0]['id'] = 'new-item-id'
    changed['lines'][1]['id'] = 'new-fee-id'
    changed['lines'].reverse()
    assert proposal_key(changed) == proposal_key(p)


def test_decision_cannot_bind_identical_content_from_another_origin():
    from assistant.commit import bind_decision
    s, page, _ = observed()
    page['url'] = 'https://other.test/'
    with pytest.raises(ValueError):
        bind_decision(s, dict(id='d1', operation='CLICK', choice='e1'), page, assessment='preparation')


@pytest.mark.parametrize("binding", ["missing", "delete_effect"])
def test_purchase_approval_never_authorizes_delete_account(tmp_path, binding):
    from assistant.commit import Journal, approve, bind_decision, bind_proposal_effect, dispatch_once
    s, page, p, x = review_fixture()
    page['actions'].append(dict(id='delete', kind='click', label='Delete account', role='button', node=20))
    p = fresh_review(s, page, p, x)
    confirmation(s, p)
    approve(s, p, question_id='q1', response='yes')
    p = fresh_review(s, page, p, x)
    s.progress['account_reservation_key'] = 'reserved-account'
    effect_id = (bind_proposal_effect(s, p, operation='CLICK', choice='delete')
                 if binding == 'delete_effect' else None)
    d = bind_decision(s, dict(id='delete-decision', operation='CLICK', choice='delete'), page,
                      assessment='commitment', effect_id=effect_id)
    j = Journal.create(tmp_path / 'private', redact=lambda value: value)
    calls = []
    with pytest.raises(ValueError):
        dispatch_once(s, j, d, page, act=lambda: calls.append(1) or {}, is_fresh=lambda: True,
                      interrupted=lambda: False, proposal=p)
    assert calls == [] and not s.approval['consumed'] and not j.records()


@pytest.mark.parametrize('fail_parent_sync', [False, True])
def test_new_journal_ancestors_are_durable_before_dispatch(tmp_path, monkeypatch, fail_parent_sync):
    import os

    from assistant.commit import Journal, dispatch_once
    s, page, _ = observed()
    d = bound(s, page)
    j = Journal(tmp_path / 'new' / 'nested' / 'j.jsonl', redact=lambda value: value)
    mkdir, fsync = os.mkdir, os.fsync
    pending, events = set(), []
    def make(path, mode=0o777, *, dir_fd=None):
        mkdir(path, mode, dir_fd=dir_fd)
        inode = os.fstat(dir_fd).st_ino
        pending.add(inode)
        events.append(('mkdir', path, inode))
    def sync(fd):
        inode = os.fstat(fd).st_ino
        if inode in pending:
            events.append(('parent_sync', inode))
            if fail_parent_sync:
                raise OSError('parent directory sync failed')
            pending.remove(inode)
        fsync(fd)
    monkeypatch.setattr(os, 'mkdir', make)
    monkeypatch.setattr(os, 'fsync', sync)
    calls = []
    def act():
        calls.append(1)
        assert not pending, events
        return {}
    kwargs = dict(act=act, is_fresh=lambda: True, interrupted=lambda: False)
    if fail_parent_sync:
        with pytest.raises(OSError, match='parent directory sync failed'):
            dispatch_once(s, j, d, page, **kwargs)
        assert calls == []
    else:
        result = dispatch_once(s, j, d, page, **kwargs)
        assert result['status'] == 'executed' and calls == [1]
        assert [event[0] for event in events] == ['mkdir', 'parent_sync', 'mkdir', 'parent_sync']


def purchase_effect(s, p):
    from assistant.commit import bind_proposal_effect
    return bind_proposal_effect(s, p, operation='CLICK', choice='e6')


@pytest.mark.parametrize('invalid', ['wrong_target', 'wrong_operation', 'stale_observation'])
def test_observed_effect_cannot_be_rebound_to_another_decision(invalid):
    from assistant.commit import bind_decision, bind_proposal_effect
    s, page, p, x = review_fixture()
    validate_review(s, p, x)
    effect_id = bind_proposal_effect(s, p, operation='CLICK', choice='e6')
    decision = dict(id='effect-bound', operation='CLICK', choice='e6')
    if invalid == 'wrong_target':
        decision['choice'] = 'e1'
    if invalid == 'wrong_operation':
        decision['operation'] = 'DOUBLE_CLICK'
    if invalid == 'stale_observation':
        fresh_review(s, page, p, x)
    with pytest.raises(ValueError):
        bind_decision(s, decision, page, assessment='commitment', effect_id=effect_id)


def test_same_approved_effect_survives_a_cosmetic_target_id_change(tmp_path):
    from assistant.commit import Journal, approve, bind_decision, bind_proposal_effect, dispatch_once
    s, page, p, x = review_fixture()
    validate_review(s, p, x)
    confirmation(s, p)
    approve(s, p, question_id='q1', response='yes')
    page['actions'][5]['id'] = 'new-pay-id'
    p = fresh_review(s, page, p, x)
    effect_id = bind_proposal_effect(s, p, operation='CLICK', choice='new-pay-id')
    d = bind_decision(s, dict(id='new-decision', operation='CLICK', choice='new-pay-id'), page,
                      assessment='commitment', effect_id=effect_id)
    s.progress['account_reservation_key'] = 'reserved'
    j = Journal.create(tmp_path / 'private', redact=lambda value: value)
    calls = []
    r = dispatch_once(s, j, d, page, act=lambda: calls.append(1) or {}, is_fresh=lambda: True,
                      interrupted=lambda: False, proposal=p)
    assert r['status'] == 'executed' and calls == [1]
    assert r['effect_key'] == s.approval['effect_key']
