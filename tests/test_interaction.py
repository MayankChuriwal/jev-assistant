"""Information is reusable, while page choices retain their exact meaning."""

import pytest
from reliability_fakes import evidence_for
from reliability_fakes import observed as auth_observed

from assistant.interaction import (
    answer_requirements,
    captcha_answer,
    chat_update,
    observe_auth,
    provider_mode,
    record_captcha_answer,
    reopen,
    require,
)
from assistant.state import TaskState, amend, cite, observe_state, put_fact


def requirement(id="phone", **changes):
    return {"id": id, "kind": "text", "purpose": "login", "keys": [id],
            "scope": "https://shop.test", "depends_on": [], "options": [], "reason": "missing", **changes}


def observed(s, text="Standard: ₹50, 4 seats; Standard XL: ₹80, 6 seats"):
    oid = observe_state(s, {"url": "https://shop.test", "text": text})
    return cite(s, oid, text)


def test_rewording_does_not_reask_for_a_valid_phone():
    s = TaskState("t1", "Continue my task")
    q = require(s, requirement())
    answer_requirements(s, q["id"], {"phone": "[profile:phone]"}, now=1)
    assert require(s, requirement(reason="new wording")) is None
    assert require(s, requirement(id="new_id", keys=["phone"])) is None
    assert s.facts["phone"]["source"] == "user"


def test_pending_rewording_keeps_id_and_display_timer():
    s = TaskState("t1", "Continue")
    q = require(s, requirement())
    q["displayed_at"] = 10
    again = require(s, requirement(reason="different words"))
    assert again["id"] == "t1-q1"
    assert again["displayed_at"] == 10


def test_combine_related_undisplayed_questions_and_partial_answer():
    s = TaskState("t1", "Continue")
    q = require(s, requirement())
    q2 = require(s, requirement("email"))
    assert q2["id"] == q["id"]
    assert answer_requirements(s, q["id"], {"phone": "[profile:phone]"}, now=1) == ("email",)
    assert require(s, requirement()) is None
    assert "phone" not in require(s, requirement("email"))["text"]
    assert answer_requirements(s, q["id"], {"email": "[profile:email]"}, now=2) == ()
    assert s.questions[q["id"]]["answered_at"] == 2


def test_displayed_questions_and_secret_questions_never_expand():
    s = TaskState("t1", "Continue")
    q = require(s, requirement())
    q["displayed_at"] = 1
    other = require(s, requirement("email"))
    secret = require(s, requirement("otp", kind="secret"))
    assert len({q["id"], other["id"], secret["id"]}) == 3
    with pytest.raises(ValueError, match="opaque"):
        answer_requirements(s, secret["id"], {"otp": "123456"}, now=2)
    assert "otp" not in s.facts


def test_answer_cannot_be_redirected_or_override_answered_keys():
    s = TaskState("t1", "Continue")
    q = require(s, requirement())
    with pytest.raises(ValueError):
        answer_requirements(s, "other-q1", {"phone": "wrong"}, now=1)
    with pytest.raises(ValueError):
        answer_requirements(s, q["id"], {"address": "wrong"}, now=1)
    answer_requirements(s, q["id"], {"phone": "first"}, now=1)
    assert answer_requirements(s, q["id"], {"phone": "late"}, now=2) == ()
    assert s.facts["phone"]["value"] == "first"
    assert s.executions == {}


def test_amendment_preserves_unrelated_answers_and_rejects_stale_reply():
    s = TaskState("t1", "Continue")
    q = require(s, requirement())
    answer_requirements(s, q["id"], {"phone": "known"}, now=1)
    address = require(s, requirement("address"))
    amend(s, "Change address", {"address"})
    assert require(s, requirement()) is None
    with pytest.raises(ValueError, match="stale"):
        answer_requirements(s, address["id"], {"address": "old"}, now=2)


def test_reopen_requires_real_reason_and_current_validation_evidence():
    s = TaskState("t1", "Continue")
    q = require(s, requirement())
    answer_requirements(s, q["id"], {"phone": "known"}, now=1)
    with pytest.raises(ValueError):
        reopen(s, "phone", "new wording", ())
    with pytest.raises(ValueError):
        reopen(s, "phone", "observed validation failure", ())
    eid = observed(s, "Phone rejected")
    reopen(s, "phone", "observed validation failure", (eid,))
    new = require(s, requirement())
    assert new["id"] != q["id"]
    assert s.requirements["phone"]["reopened"]["evidence_ids"] == (eid,)


def test_scope_and_dependency_must_match_and_profile_value_is_reused():
    s = TaskState("t1", "Continue")
    put_fact(s, "phone", "[profile:phone]", source="profile", scope="https://shop.test")
    assert require(s, requirement()) is None
    assert require(s, requirement(scope="https://other.test", id="other" , keys=["phone"]))
    with pytest.raises(ValueError):
        require(s, requirement("address", depends_on=["missing"]))


def test_choice_retains_full_semantics_and_never_transient_target():
    s = TaskState("t1", "Choose ride")
    eid = observed(s)
    opts = [{"id": "standard", "label": "Standard", "description": "₹50, 4 seats", "evidence_ids": [eid]},
            {"id": "xl", "label": "Standard XL", "description": "₹80, 6 seats", "evidence_ids": [eid]}]
    q = require(s, requirement("ride", kind="choice", options=opts))
    with pytest.raises(ValueError):
        answer_requirements(s, q["id"], {"ride": "Standard"}, now=1)
    answer_requirements(s, q["id"], {"ride": "xl"}, now=1)
    assert s.facts["ride"]["value"] == {"label": "Standard XL", "description": "₹80, 6 seats"}
    assert s.facts["ride"]["evidence"] == (eid,)


def test_stale_option_evidence_cannot_be_answered():
    s = TaskState("t1", "Choose")
    eid = observed(s)
    q = require(s, requirement("ride", kind="choice", options=[
        {"id": "xl", "label": "XL", "description": "₹80", "evidence_ids": [eid]}]))
    observed(s, "XL ₹95")
    with pytest.raises(ValueError, match="stale"):
        answer_requirements(s, q["id"], {"ride": "xl"}, now=1)
    assert "ride" not in s.facts


def test_provider_choice_accepts_unknown_free_text_and_does_not_save_default():
    s = TaskState("t1", "Shop")
    result = provider_mode(s, {})
    assert result["mode"] == "ask"
    q = next(iter(s.questions.values()))
    assert "multiple OTPs" in q["text"]
    answer_requirements(s, q["id"], {"provider": "New local service"}, now=1)
    assert provider_mode(s, {})["provider"] == "New local service"
    assert s.facts["provider"]["source"] == "user"


def test_comparison_requested_once_with_login_notice_and_skipped_results_preserved():
    s = TaskState("t1", "Compare rides")
    s.constraints["compare"] = True
    s.progress["providers"] = {"Unavailable": {"status": "skipped"}}
    mode = provider_mode(s, {})
    assert mode["mode"] == "compare" and "separate logins" in mode["notice"]
    assert not s.questions
    assert s.progress["providers"]["Unavailable"]["status"] == "skipped"


def test_history_is_not_default_and_current_choice_outranks_default():
    s = TaskState("t1", "Shop")
    put_fact(s, "provider", "Past shop", source="page", evidence=(observed(s),))
    assert provider_mode(s, {"provider": "Past shop"})["mode"] == "ask"
    defaults = {"provider": {"value": "Saved shop", "source": "user", "consent": True, "scope": "task"}}
    assert provider_mode(s, defaults)["provider"] == "Saved shop"
    assert "saved default" in provider_mode(s, defaults)["notice"]
    s.constraints["provider"] = "Current shop"
    assert provider_mode(s, defaults)["provider"] == "Current shop"


def test_notices_deduplicate_semantics_not_rewording_and_require_evidence():
    s = TaskState("t1", "Shop")
    eid = observed(s, "Delivery in 20 minutes")
    notice = {"kind": "finding", "text": "Delivery in 20 minutes", "evidence_ids": [eid], "requirement_ids": []}
    assert chat_update(s, notice)["kind"] == "info"
    assert chat_update(s, {**notice, "text": "Arrives in 20 minutes"}) is None
    eid2 = observed(s, "Delivery in 20 minutes")
    assert chat_update(s, {**notice, "evidence_ids": [eid2]}) is None
    with pytest.raises(ValueError):
        chat_update(s, {**notice, "evidence_ids": []})
    with pytest.raises(ValueError, match="stale"):
        chat_update(s, notice)


def test_start_notice_is_derived_from_goal_not_unverified_progress():
    s = TaskState("t1", "Compare hotel prices")
    msg = chat_update(s, {"kind": "start", "text": "Booking confirmed!", "evidence_ids": [], "requirement_ids": []})
    assert s.goal in msg["text"] and "confirmed" not in msg["text"]


def test_historical_provider_does_not_satisfy_explicit_choice_question():
    s = TaskState("t1", "Shop")
    put_fact(s, "provider", "Past shop", source="page", evidence=(observed(s),))
    provider_mode(s, {})
    assert len(s.questions) == 1


def test_cosmetic_option_refresh_reuses_answer_but_changed_price_reopens():
    s = TaskState("t1", "Choose")
    eid = observed(s, "XL ₹80")
    req = requirement("ride", kind="choice", options=[
        {"id": "xl", "label": "XL", "description": "₹80", "evidence_ids": [eid]}])
    q = require(s, req)
    answer_requirements(s, q["id"], {"ride": "xl"}, now=1)
    fresh = observed(s, "XL ₹80")
    req["options"][0].update(id="new-render", evidence_ids=[fresh])
    assert require(s, req) is None
    changed = observed(s, "XL ₹95")
    req["options"][0].update(description="₹95", evidence_ids=[changed])
    assert require(s, req)["id"] != q["id"]


def test_pending_choice_rebinds_cosmetic_refresh_but_rejects_old_changed_offer():
    s = TaskState("t1", "Choose")
    eid = observed(s, "XL ₹80")
    req = requirement("ride", kind="choice", options=[
        {"id": "xl", "label": "XL", "description": "₹80", "evidence_ids": [eid]}])
    q = require(s, req)
    fresh = observed(s, "XL ₹80")
    req["options"][0].update(id="new-render", evidence_ids=[fresh])
    assert require(s, req)["id"] == q["id"]
    assert answer_requirements(s, q["id"], {"ride": "xl"}, now=1) == ()


def test_partial_answer_is_atomic_when_later_value_is_invalid():
    s = TaskState("t1", "Continue")
    q = require(s, requirement())
    require(s, requirement("email"))
    with pytest.raises(ValueError):
        answer_requirements(s, q["id"], {"phone": "known", "email": ""}, now=1)
    assert s.facts == {}


def test_requirement_dependencies_invalidate_only_dependent_answers():
    s = TaskState("t1", "Plan delivery")
    address = requirement("address", purpose="delivery")
    q = require(s, address)
    answer_requirements(s, q["id"], {"address": "First address"}, now=1)
    slot = requirement("slot", purpose="delivery", depends_on=["address"])
    q2 = require(s, slot)
    answer_requirements(s, q2["id"], {"slot": "Tomorrow"}, now=2)
    amend(s, "Deliver elsewhere", {"address"})
    assert s.facts["slot"]["valid"] is False


def test_reopening_dependency_invalidates_dependent_answers():
    s = TaskState("t1", "Plan delivery")
    address = requirement("address", purpose="delivery")
    q = require(s, address)
    answer_requirements(s, q["id"], {"address": "First address"}, now=1)
    slot = requirement("slot", purpose="delivery", depends_on=["address"])
    q2 = require(s, slot)
    answer_requirements(s, q2["id"], {"slot": "Tomorrow"}, now=2)
    reopen(s, "address", "invalid format", ())
    assert s.facts["slot"]["valid"] is False


def test_added_requirement_dependency_blocks_pending_question_and_old_reply():
    s = TaskState("t1", "Plan delivery")
    slot = requirement("slot", purpose="delivery")
    old = require(s, slot)
    old["displayed_at"] = 1
    address = require(s, requirement("address", purpose="delivery"))
    assert require(s, {**slot, "depends_on": ["address"]}) is None
    assert s.requirements["slot"]["depends_on"] == ["address"]
    with pytest.raises(ValueError, match="stale"):
        answer_requirements(s, old["id"], {"slot": "Tomorrow"}, now=2)
    answer_requirements(s, address["id"], {"address": "New address"}, now=3)
    fresh = require(s, {**slot, "depends_on": ["address"]})
    answer_requirements(s, fresh["id"], {"slot": "Tomorrow"}, now=4)
    assert s.facts["slot"]["depends_on"] == ("address",)
    reopen(s, "address", "invalid format", ())
    assert s.facts["slot"]["valid"] is False


def test_added_dependency_reopens_accepted_answer_and_invalidates_downstream():
    s = TaskState("t1", "Plan delivery")
    slot = requirement("slot", purpose="delivery")
    q = require(s, slot)
    answer_requirements(s, q["id"], {"slot": "Tomorrow"}, now=1)
    q2 = require(s, requirement("instructions", purpose="delivery", depends_on=["slot"]))
    answer_requirements(s, q2["id"], {"instructions": "Leave outside"}, now=2)
    require(s, requirement("address", purpose="delivery"))
    assert require(s, {**slot, "depends_on": ["address"]}) is None
    assert s.facts["slot"]["valid"] is False
    assert s.facts["instructions"]["valid"] is False
    assert s.requirements["slot"]["satisfied"] is False


def test_removed_dependency_keeps_valid_answer_without_obsolete_provenance():
    s = TaskState("t1", "Plan delivery")
    q = require(s, requirement("address"))
    answer_requirements(s, q["id"], {"address": "Home"}, now=1)
    slot = requirement("slot", depends_on=["address"])
    q = require(s, slot)
    answer_requirements(s, q["id"], {"slot": "Tomorrow"}, now=2)
    assert require(s, {**slot, "depends_on": []}) is None
    reopen(s, "address", "invalid format", ())
    assert s.facts["slot"]["valid"] is True
    assert s.facts["slot"]["depends_on"] == ()


@pytest.mark.parametrize("source,default", [("user", False), ("profile", True)])
def test_exact_user_choice_or_explicit_default_reuses_observed_option(source, default):
    s = TaskState("t1", "Choose Standard XL")
    eid = observed(s)
    put_fact(s, "ride", "Standard XL", source=source)
    if default:
        s.facts["ride"]["explicit_default"] = True
    req = requirement("ride", kind="choice", options=[
        {"id": "a", "label": "Standard", "description": "₹50, 4 seats", "evidence_ids": [eid]},
        {"id": "b", "label": "Standard XL", "description": "₹80, 6 seats", "evidence_ids": [eid]}])
    assert require(s, req) is None
    assert s.facts["ride"]["value"] == {"label": "Standard XL", "description": "₹80, 6 seats"}
    assert s.facts["ride"]["source"] == source
    assert s.facts["ride"]["evidence"] == (eid,)
    assert s.questions == {} and s.executions == {}


@pytest.mark.parametrize("value,source,labels", [
    ("Standard", "user", ["Standard XL", "Standard Premium"]),
    ("Standard XL", "user", ["Standard XL", "Standard XL"]),
    ("Standard XL", "profile", ["Standard", "Standard XL"]),
])
def test_overlapping_ambiguous_or_historical_choice_still_requires_answer(value, source, labels):
    s = TaskState("t1", "Choose ride")
    eid = observed(s)
    put_fact(s, "ride", value, source=source)
    req = requirement("ride", kind="choice", options=[
        {"id": "a", "label": labels[0], "description": "₹50, 4 seats", "evidence_ids": [eid]},
        {"id": "b", "label": labels[1], "description": "₹80, 6 seats", "evidence_ids": [eid]}])
    assert require(s, req) is not None
    assert s.facts["ride"]["valid"] is False


def test_dependency_change_cannot_create_cycle_through_canonical_requirement():
    s = TaskState("t1", "Plan delivery")
    first = requirement("address")
    require(s, first)
    require(s, requirement("slot", depends_on=["address"]))
    with pytest.raises(ValueError, match="cycle"):
        require(s, {**first, "id": "reworded_address", "depends_on": ["slot"]})
    assert s.requirements["address"]["depends_on"] == []


def test_observed_choice_fact_does_not_replace_user_choice():
    s = TaskState("t1", "Choose ride")
    eid = observed(s)
    put_fact(s, "ride", {"label": "Standard XL", "description": "₹80, 6 seats"},
             source="page", evidence=(eid,))
    req = requirement("ride", kind="choice", options=[
        {"id": "b", "label": "Standard XL", "description": "₹80, 6 seats", "evidence_ids": [eid]}])
    assert require(s, req) is not None


def test_dependency_order_change_preserves_answer_and_explicit_choice_tracks_dependencies():
    s = TaskState("t1", "Choose ride")
    for key in ("pickup", "destination"):
        q = require(s, requirement(key))
        answer_requirements(s, q["id"], {key: "Place"}, now=1)
    eid = observed(s)
    put_fact(s, "ride", "Standard XL", source="user")
    req = requirement("ride", kind="choice", depends_on=["pickup", "destination"], options=[
        {"id": "b", "label": "Standard XL", "description": "₹80, 6 seats", "evidence_ids": [eid]}])
    assert require(s, req) is None
    assert require(s, {**req, "depends_on": ["destination", "pickup"]}) is None
    assert s.questions.keys() == {"t1-q1", "t1-q2"}
    reopen(s, "pickup", "changed request", ())
    assert s.facts["ride"]["valid"] is False


def test_temporary_secret_scope_expiry_and_consumption():
    from assistant.interaction import SecretStore
    now = [10]
    store = SecretStore(clock=lambda: now[0])
    scope = dict(task_id="t1", question_id="q1", challenge_id="c1", origin="https://a.test",
                 purpose="login", field_role="otp")
    ref = store.put("秘密123456", **scope, expires_at=9999)
    assert store.resolve(ref, **scope) == "秘密123456"
    for key in scope:
        with pytest.raises(ValueError):
            store.resolve(ref, **{**scope, key: "other"})
    with pytest.raises(ValueError):
        store.resolve(ref, task_id="t1")
    assert "秘密" not in repr(store)
    now[0] = 610
    with pytest.raises(ValueError):
        store.resolve(ref, **scope)
    now[0] = 20
    ref = store.put("123456", **scope, expires_at=100)
    store.consume(ref)
    with pytest.raises(ValueError):
        store.resolve(ref, **scope)


def test_secret_resend_and_task_purge():
    from assistant.interaction import SecretStore
    store = SecretStore(clock=lambda: 0)
    scope = dict(task_id="t", question_id="q", challenge_id="c", origin="https://a.test",
                 purpose="login", field_role="otp")
    old = store.put("111111", **scope, expires_at=100)
    new = store.put("222222", **{**scope, "challenge_id": "c2"}, expires_at=100)
    with pytest.raises(ValueError):
        store.resolve(old, **scope)
    store.purge("other")
    assert store.resolve(new, **{**scope, "challenge_id": "c2"}) == "222222"
    store.purge("t")
    with pytest.raises(ValueError):
        store.resolve(new, **{**scope, "challenge_id": "c2"})


def test_field_cache_covers_all_helper_inputs_revision_and_challenge():
    from assistant.interaction import field_cache_key
    s = TaskState("t", "Continue")
    payload = {"goal": "buy", "field": {"label": "Code", "constraints": {"maxlength": 6}},
               "previous": None, "page": {"text": "Login"}}
    key = field_cache_key(s, payload, "c1")
    assert key == field_cache_key(s, dict(reversed(list(payload.items()))), "c1")
    assert key != field_cache_key(s, {**payload, "previous": "rejected"}, "c1")
    assert key != field_cache_key(s, payload, "c2")
    s.revision += 1
    assert key != field_cache_key(s, payload, "c1")


def test_secret_store_payment_expiry_and_serialization():
    import pickle

    from assistant.interaction import SecretStore
    now = [0]
    store = SecretStore(lambda: now[0])
    scope = dict(task_id="t", question_id="q", challenge_id="c", origin="https://a.test",
                 purpose="payment", field_role="cvv")
    ref = store.put("123", **scope, expires_at=9999)
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(store)
    now[0] = 899
    assert store.resolve(ref, **scope) == "123"
    now[0] = 900
    with pytest.raises(ValueError):
        store.resolve(ref, **scope)

# Task 6: observed authentication and human challenge answers.


def auth_event(s, oid, status='challenge', **changes):
    return dict(flow_id='f1', challenge_id='c1', status=status, origin='https://shop.test',
                purpose='login', field_role='otp', evidence_ids=evidence_for(s, oid, s.observations[oid]['text']),
                execution_id=None, expires_at=None, allowed_origins=['https://shop.test'], **changes)


def auth_state():
    s, page, oid = auth_observed('Enter the login code')
    s.challenges['c1'] = dict(id='c1', flow_id='f1', revision=s.revision, origin='https://shop.test',
                              purpose='login', field_role='otp', observation_id=oid, status='challenge')
    return s, page, oid


def submit_auth(s, oid):
    s.executions['x1'] = dict(id='x1', flow_id='f1', challenge_id='c1', revision=s.revision,
        observation_id=oid, origin='https://shop.test', purpose='login', field_role='otp',
        submission=True, status='dispatched')
    event = auth_event(s, oid, 'submitted')
    event['execution_id'] = 'x1'
    return observe_auth(s, event)


def test_persistent_sign_in_link_does_not_override_authenticated_evidence():
    s, page, oid = auth_observed('Sign in | Your account | Saved addresses: Home')
    event = auth_event(s, oid, 'authenticated')
    event['challenge_id'] = None
    assert observe_auth(s, event)['status'] == 'authenticated'
    assert not s.executions


def test_auth_submission_waits_for_post_submission_rejection_and_counts_once():
    s, page, oid = auth_state()
    observe_auth(s, auth_event(s, oid))
    assert submit_auth(s, oid)['rejections'] == 0
    event = auth_event(s, oid, 'rejected')
    event['execution_id'] = 'x1'
    with pytest.raises(ValueError, match='subsequent'):
        observe_auth(s, event)
    new = observe_state(s, dict(page, text='Incorrect code'))
    event = auth_event(s, new, 'rejected')
    event['execution_id'] = 'x1'
    assert observe_auth(s, event)['rejections'] == 1
    assert observe_auth(s, event)['rejections'] == 1
    s.challenges['c2'] = dict(s.challenges['c1'], id='c2', status='challenge', observation_id=new)
    event = auth_event(s, new)
    event['challenge_id'] = 'c2'
    assert observe_auth(s, event)['rejections'] == 1
    assert s.challenges['c1']['status'] == 'expired'


@pytest.mark.parametrize('field,value', [('flow_id', 'other'), ('revision', 99), ('purpose', 'payment'),
                                       ('field_role', 'cvv'), ('submission', False)])
def test_auth_rejects_incompatible_execution(field, value):
    s, page, oid = auth_state()
    submit_auth(s, oid)
    s.executions['x1'][field] = value
    new = observe_state(s, dict(page, text='Incorrect code'))
    event = auth_event(s, new, 'rejected')
    event['execution_id'] = 'x1'
    with pytest.raises(ValueError):
        observe_auth(s, event)


@pytest.mark.parametrize('field,value', [('flow_id', 'other'), ('revision', 99), ('purpose', 'payment'),
                                       ('field_role', 'cvv'), ('origin', 'https://other.test')])
def test_auth_rejects_incompatible_challenge(field, value):
    s, page, oid = auth_state()
    s.challenges['c1'][field] = value
    with pytest.raises(ValueError):
        observe_auth(s, auth_event(s, oid))


def test_auth_origin_list_needs_flow_navigation_not_just_visited_origin():
    s, page, oid = auth_state()
    observe_auth(s, auth_event(s, oid))
    new = observe_state(s, dict(page, url='https://identity.test/', text='Login provider'))
    event = auth_event(s, new, 'required')
    event.update(challenge_id=None, origin='https://identity.test',
                 allowed_origins=['https://shop.test', 'https://identity.test'])
    with pytest.raises(ValueError, match='navigation'):
        observe_auth(s, event)
    s.executions['nav'] = dict(flow_id='f1', revision=s.revision, observation_id=oid,
        origin='https://shop.test', destination_origin='https://identity.test', status='succeeded',
        kind='navigation', evidence_ids=event['evidence_ids'])
    assert observe_auth(s, event)['allowed_origins'] == event['allowed_origins']


def captcha(kind='text', **changes):
    return dict(id='captcha1', kind=kind, revision=1, tiles=[], rounds=0, observation_id='o1', **changes)


@pytest.mark.parametrize('reply', ['2468', '002468'])
def test_numeric_text_captcha_is_never_a_tile_list(reply):
    assert captcha_answer(captcha(), reply, revision=1) == {'kind': 'text', 'value': reply}


def test_tiles_are_current_labels_and_never_executable_targets():
    challenge = captcha('tiles')
    challenge['tiles'] = ['A1', 'B2', 'C3']
    assert captcha_answer(challenge, 'A1, C3', revision=1) == {'kind': 'tiles', 'labels': ['A1', 'C3']}
    for reply in ('2468', 'A1, Z9', 'A1, A1', 'click A1'):
        with pytest.raises(ValueError):
            captcha_answer(challenge, reply, revision=1)
    with pytest.raises(ValueError, match='stale'):
        captcha_answer(challenge, 'A1', revision=2)


def test_captcha_budget_and_unsupported_gestures_block_in_chat():
    challenge = captcha()
    challenge['rounds'] = 8
    with pytest.raises(ValueError, match='eight'):
        captcha_answer(challenge, '123', revision=1)
    with pytest.raises(ValueError, match='chat'):
        captcha_answer(captcha('unsupported'), 'drag', revision=1)


def test_captcha_reply_becomes_scoped_fact_only_and_refresh_invalidates_it():
    s, page, oid = auth_observed('Select tiles')
    s.challenges['captcha1'] = dict(captcha('tiles'), revision=0, observation_id=oid, tiles=['A1', 'B2'])
    fact = record_captcha_answer(s, 'captcha1', 'A1', revision=0)
    assert fact['source'] == 'user' and fact['scope'] == 'captcha1'
    assert fact['value'] == {'kind': 'tiles', 'labels': ['A1']}
    assert s.executions == {}
    s.challenges['captcha1']['revision'] = 1
    with pytest.raises(ValueError, match='stale'):
        record_captcha_answer(s, 'captcha1', 'B2', revision=0)
    assert not s.facts['captcha:captcha1']['valid']


def test_submission_cannot_return_to_challenge_while_slow_auto_submit_is_pending():
    s, _, oid = auth_state()
    submit_auth(s, oid)
    with pytest.raises(ValueError, match='pending'):
        observe_auth(s, auth_event(s, oid))
    assert s.progress['auth_flows']['f1']['rejections'] == 0


def test_auth_evidence_must_be_from_the_event_origin_and_current_revision():
    s, page, oid = auth_state()
    other = observe_state(s, dict(page, url='https://other.test', text='Incorrect code'))
    with pytest.raises(ValueError, match='origin'):
        observe_auth(s, auth_event(s, other))
    with pytest.raises(ValueError, match='stale'):
        observe_auth(s, auth_event(s, oid))


def test_captcha_round_counter_is_owned_by_recording_not_validation():
    s, _, oid = auth_observed('Type the challenge')
    s.challenges['captcha1'] = dict(captcha(), revision=0, observation_id=oid)
    record_captcha_answer(s, 'captcha1', '0012', revision=0)
    assert s.challenges['captcha1']['rounds'] == 1
    # Duplicate delivery of an already accepted reply never spends another round.
    record_captcha_answer(s, 'captcha1', '0012', revision=0)
    assert s.challenges['captcha1']['rounds'] == 1


@pytest.mark.parametrize('role,limit', [('otp', 3), ('cvv', 2)])
def test_rejection_budget_survives_every_resend(role, limit):
    s, page, oid = auth_state()
    for attempt in range(limit):
        cid, xid = f'c{attempt + 1}', f'x{attempt + 1}'
        s.challenges[cid] = dict(s.challenges['c1'], id=cid, field_role=role,
                                 status='challenge', observation_id=oid)
        event = auth_event(s, oid)
        event.update(challenge_id=cid, field_role=role)
        observe_auth(s, event)
        s.executions[xid] = dict(id=xid, flow_id='f1', challenge_id=cid, revision=0,
            observation_id=oid, origin='https://shop.test', purpose='login', field_role=role,
            submission=True, status='dispatched')
        observe_auth(s, dict(event, status='submitted', execution_id=xid))
        oid = observe_state(s, dict(page, text=f'Code rejected {attempt}'))
        event.update(status='rejected', execution_id=xid,
                     evidence_ids=evidence_for(s, oid, s.observations[oid]['text']))
        result = observe_auth(s, event)
        assert result['rejections'] == attempt + 1
    assert result['blocked']
    s.challenges['next'] = dict(s.challenges[cid], id='next', status='challenge', observation_id=oid)
    with pytest.raises(ValueError, match='limit'):
        observe_auth(s, dict(event, challenge_id='next', status='challenge', execution_id=None))


def test_expired_challenge_cannot_be_resubmitted_and_does_not_count_as_rejection():
    s, _, oid = auth_state()
    assert observe_auth(s, auth_event(s, oid, 'expired'))['rejections'] == 0
    with pytest.raises(ValueError, match='usable'):
        observe_auth(s, auth_event(s, oid))


def test_pending_submission_cannot_be_reset_by_required_event():
    s, _, oid = auth_state()
    submit_auth(s, oid)
    with pytest.raises(ValueError, match='pending'):
        observe_auth(s, auth_event(s, oid, 'required'))


def test_pending_auth_cannot_accept_a_second_submission_receipt():
    s, _, oid = auth_state()
    submit_auth(s, oid)
    s.executions['x2'] = dict(s.executions['x1'], id='x2')
    with pytest.raises(ValueError, match='pending'):
        observe_auth(s, dict(auth_event(s, oid, 'submitted'), execution_id='x2'))


def test_resend_invalidates_facts_bound_to_the_old_challenge():
    s, _, oid = auth_state()
    observe_auth(s, auth_event(s, oid))
    put_fact(s, 'login_answer', '[secret:opaque]', source='user', scope='c1')
    s.challenges['c2'] = dict(s.challenges['c1'], id='c2')
    observe_auth(s, dict(auth_event(s, oid), challenge_id='c2'))
    assert not s.facts['login_answer']['valid']


def test_missing_expiry_on_followup_does_not_extend_a_challenge():
    s, _, oid = auth_state()
    observe_auth(s, dict(auth_event(s, oid), expires_at=60))
    submit_auth(s, oid)
    assert s.challenges['c1']['expires_at'] == 60
    assert s.progress['auth_flows']['f1']['expires_at'] == 60
