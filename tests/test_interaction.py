"""Information is reusable, while page choices retain their exact meaning."""

import pytest

from assistant.interaction import answer_requirements, chat_update, provider_mode, reopen, require
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
