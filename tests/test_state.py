import json
from unittest.mock import Mock

import pytest

from assistant.state import TaskState, amend, cite, observe_state, public_context, put_fact


def test_address_change_preserves_phone_and_invalidates_dependent_offer():
    s = TaskState("t1", "Get groceries")
    put_fact(s, "phone", "[profile:phone]", source="user")
    put_fact(s, "address", "Home", source="user")
    put_fact(s, "offer", "o1", source="user", depends_on=("address",))
    s.approval = {"revision": 0}
    amend(s, "Deliver to Office", {"address"})
    assert s.facts["phone"]["valid"]
    assert not s.facts["offer"]["valid"]
    assert s.approval is None and s.revision == 1
    assert public_context(s)["goal"] == "Get groceries"


def test_evidence_cannot_be_invented_or_taken_from_another_observation():
    s = TaskState("t1", "Find a fare")
    oid = observe_state(s, {"url": "https://cab.test/", "text": "Fare estimate ₹200", "actions": []})
    assert cite(s, oid, "Fare estimate ₹200") in s.evidence
    with pytest.raises(ValueError):
        cite(s, oid, "Driver assigned")


@pytest.mark.parametrize("kind,quote", [
    ("page", ""), ("page", "Absent"), ("browser-event", "fare"),
    ("visual-observation", "fare"), ("artifact", "fare"), ("unknown", "fare"),
])
def test_citation_rejects_unsupported_provenance(kind, quote):
    s = TaskState("t1", "Find a fare")
    oid = observe_state(s, {"url": "https://cab.test/", "text": "fare", "actions": []})
    with pytest.raises(ValueError):
        cite(s, oid, quote, kind=kind)


def test_citation_rejects_foreign_and_stale_observations():
    first = TaskState("first", "Find a fare")
    second = TaskState("second", "Find a fare")
    foreign = observe_state(second, {"url": "https://cab.test", "text": "fare", "actions": []})
    with pytest.raises(ValueError):
        cite(first, foreign, "fare")
    own = observe_state(first, {"url": "https://cab.test", "text": "fare", "actions": []})
    amend(first, "Find a different fare", set())
    with pytest.raises(ValueError):
        cite(first, own, "fare")


def test_visual_evidence_has_a_screenshot_reference_without_image_bytes():
    s = TaskState("t1", "Inspect chart")
    oid = observe_state(s, {"url": "https://chart.test/", "text": "Chart", "actions": [],
                            "screenshot": "raw-image-bytes"})
    eid = cite(s, oid, "A downward trend", kind="visual-observation")
    assert s.evidence[eid]["screenshot_ref"] == s.observations[oid]["screenshot_ref"]
    assert "raw-image-bytes" not in json.dumps(public_context(s))


def test_event_and_artifact_citations_use_only_public_descriptions():
    s = TaskState("t1", "Download receipt")
    oid = observe_state(s, {"url": "https://shop.test", "text": "Receipt", "actions": [],
                            "events": [{"text": "Download finished", "raw_secret": "hidden"}],
                            "artifacts": [{"text": "Invoice PDF", "path": "/private/file.pdf"}]})
    assert cite(s, oid, "Download finished", kind="browser-event") in s.evidence
    assert cite(s, oid, "Invoice PDF", kind="artifact") in s.evidence
    with pytest.raises(ValueError):
        cite(s, oid, "private", kind="artifact")
    assert "hidden" not in json.dumps(s.observations)
    assert "/private/file.pdf" not in json.dumps(s.observations)


def test_page_fact_needs_current_evidence_and_user_fact_wins():
    s = TaskState("t1", "Ship package")
    oid = observe_state(s, {"url": "https://ship.test/", "text": "Office", "actions": []})
    eid = cite(s, oid, "Office")
    with pytest.raises(ValueError):
        put_fact(s, "address", "Office", source="page")
    put_fact(s, "address", "Home", source="user")
    put_fact(s, "address", "Office", source="page", evidence=(eid,))
    assert s.facts["address"]["value"] == "Home"
    assert s.facts["address"]["source"] == "user"
    amend(s, "Use Office", {"address"})
    with pytest.raises(ValueError):
        put_fact(s, "address", "Office", source="page", evidence=(eid,))


def test_amendment_invalidates_transitive_dependents_only():
    s = TaskState("t1", "Get groceries")
    put_fact(s, "phone", "[profile:phone]", source="user")
    put_fact(s, "address", "Home", source="user")
    put_fact(s, "offer", "o1", source="user", depends_on=("address",))
    put_fact(s, "total", "₹99", source="user", depends_on=("offer",))
    amend(s, "Use Office", {"address"})
    assert [s.facts[key]["valid"] for key in ("address", "offer", "total", "phone")] == [
        False, False, False, True]


def test_new_fact_cannot_depend_on_an_invalidated_answer():
    s = TaskState("t1", "Get groceries")
    put_fact(s, "address", "Home", source="user")
    amend(s, "Deliver to Office", {"address"})
    with pytest.raises(ValueError):
        put_fact(s, "delivery_offer", "o1", source="user", depends_on=("address",))
    assert "delivery_offer" not in s.facts
    put_fact(s, "address", "Office", source="user")
    put_fact(s, "delivery_offer", "o2", source="user", depends_on=("address",))
    assert s.facts["delivery_offer"]["valid"]


def test_new_requirement_cannot_cite_evidence_from_before_amendment():
    s = TaskState("t1", "Find a fare")
    old = observe_state(s, {"url": "https://cab.test/", "text": "Fare ₹200", "actions": []})
    old_evidence = cite(s, old, "Fare ₹200")
    amend(s, "Find a fare to the airport", {"destination"})
    with pytest.raises(ValueError):
        s.add_requirement("fare", "Airport fare observed", "page", evidence_ids=(old_evidence,))
    assert "fare" not in s.requirements
    current = observe_state(s, {"url": "https://cab.test/", "text": "Airport fare ₹250", "actions": []})
    current_evidence = cite(s, current, "Airport fare ₹250")
    s.add_requirement("fare", "Airport fare observed", "page", evidence_ids=(current_evidence,))
    assert s.requirements["fare"]["evidence_ids"] == (current_evidence,)


def test_original_goal_cannot_be_replaced():
    s = TaskState("t1", "Find a fare")
    amend(s, "Make it an airport fare", set())
    assert s.goal == "Find a fare"
    assert public_context(s)["goal"] == "Find a fare"
    with pytest.raises(AttributeError):
        s.goal = "Book a hotel"


@pytest.mark.parametrize("status", ["unknown", "done", "ready"])
def test_status_rejects_unsupported_or_ungated_completion(status):
    s = TaskState("t1", "Find a fare")
    with pytest.raises(ValueError):
        s.set_status(status)
    assert s.status == "running"


def test_terminal_status_cannot_resume_and_requirement_kind_is_validated():
    s = TaskState("t1", "Find a fare")
    with pytest.raises(ValueError):
        s.add_requirement("r1", "Quote obtained", "taxi")
    s.add_requirement("r1", "Quote obtained", "page", material_keys=("fare",))
    assert s.requirements["r1"]["kind"] == "page"
    assert s.requirements["r1"]["material_keys"] == ("fare",)
    s.set_status("cancelled")
    with pytest.raises(ValueError):
        s.set_status("running")


def test_public_context_drops_browser_objects_and_secret_fields():
    s = TaskState("t1", "Inspect page")
    page = {"url": "https://user:pass@shop.test/path?token=123", "text": "Checkout 246810",
            "actions": [{"id": "e1", "kind": "fill", "label": "OTP", "value": "246810",
                         "node": Mock(), "raw_secret": "246810"}], "browser": Mock(),
            "screenshot": "image-data"}
    oid = observe_state(s, page)
    s.diagnostics.append({"browser": Mock(), "raw_secret": "246810", "count": 1})
    serialized = json.dumps(public_context(s))
    assert s.observations[oid]["text"] == "Checkout [redacted]"
    assert "246810" not in serialized
    assert "image-data" not in serialized
    assert "user:pass" not in s.observations[oid]["url"]
    assert "token=123" not in s.observations[oid]["url"]
    assert "count" in serialized


def test_observation_preserves_public_control_container_context():
    s = TaskState('context', 'Review purchase')
    oid = observe_state(s, dict(url='https://shop.test', text='Confirm', actions=[
        dict(id='confirm', kind='click', label='Confirm', context={
            'form': 'Checkout', 'section': 'Review purchase', 'dialog': 'Complete purchase',
            'raw_secret': 'must-not-retain'})]))
    assert s.observations[oid]['actions'][0]['context'] == {
        'form': 'Checkout', 'section': 'Review purchase', 'dialog': 'Complete purchase'}


@pytest.mark.parametrize('operation', ['SCROLL_DOWN', 'LOOK', 'CLICK'])
def test_unproductive_actions_cannot_avoid_stall_detection(operation):
    from assistant.state import track_progress

    s = TaskState('t1', 'Find a specific option')
    results = [track_progress(s, operation=operation, semantic_state='same-options',
                              change_evidence=(), now=float(i)) for i in range(3)]
    assert results[-1] == 'recover'


def test_wait_uses_elapsed_time_and_cancel_stops_waiting():
    from assistant.state import track_progress

    s = TaskState('t1', 'Wait for requested document')
    for now in [0, 0, 0.01, 1, 29.9]:
        assert track_progress(s, operation='WAIT', semantic_state='pending',
                              change_evidence=(), now=now) == 'wait'
    assert track_progress(s, operation='WAIT', semantic_state='pending',
                          change_evidence=(), now=30) == 'recover'
    assert s.progress.get('counts', {}) == {}
    s.set_status('cancelled')
    assert track_progress(s, operation='WAIT', semantic_state='pending',
                          change_evidence=(), now=31) == 'blocked'
    assert s.status == 'cancelled'


def test_semantic_cycle_survives_question_rewording_and_wait():
    from assistant.state import track_progress

    s = TaskState('t1', 'Find a specific option')
    for i, (op, semantic) in enumerate([('LOOK', 'a'), ('WAIT', 'a'), ('SCROLL_DOWN', 'b')]):
        track_progress(s, operation=op, semantic_state=semantic, change_evidence=(), now=i)
    s.questions['q'] = {'text': 'Rephrased question'}
    assert track_progress(s, operation='LOOK', semantic_state='a', change_evidence=(), now=3) == 'recover'


def test_progress_needs_registered_current_nonreplayed_semantic_evidence():
    from assistant.state import record_progress_change, track_progress

    s = TaskState('t1', 'Find requested option')
    oid = observe_state(s, {'url': 'https://shop.test', 'text': 'Requested option available'})
    eid = cite(s, oid, 'Requested option available')
    with pytest.raises(ValueError, match='progress evidence'):
        track_progress(s, operation='LOOK', semantic_state='a', change_evidence=(eid,), now=0)
    record_progress_change(s, kind='requested_options', evidence_ids=(eid,))
    assert track_progress(s, operation='LOOK', semantic_state='a', change_evidence=(eid,), now=0) == 'continue'
    for now in range(1, 4):
        result = track_progress(s, operation='LOOK', semantic_state='a', change_evidence=(eid,), now=now)
    assert result == 'recover'
    fresh = observe_state(s, {'url': 'https://shop.test', 'text': 'Requested option available'})
    repeated = cite(s, fresh, 'Requested option available')
    record_progress_change(s, kind='requested_options', evidence_ids=(repeated,))
    assert track_progress(s, operation='LOOK', semantic_state='a', change_evidence=(repeated,), now=4) == 'recover'


@pytest.mark.parametrize('text,kind', [('Resend in 30 seconds', 'page'), ('new image hash', 'visual-observation')])
def test_countdown_and_screenshot_are_not_progress(text, kind):
    from assistant.state import record_progress_change

    s = TaskState('t1', 'Authenticate')
    oid = observe_state(s, {'url': 'https://shop.test', 'text': text, 'screenshot': 'image'})
    eid = cite(s, oid, text, kind=kind)
    with pytest.raises(ValueError, match='progress evidence'):
        record_progress_change(s, kind='relevant_content', evidence_ids=(eid,))


def test_two_recovery_approaches_then_block_with_remaining_objective():
    from assistant.state import begin_recovery

    s = TaskState('t1', 'Find requested option')
    oid = observe_state(s, {'url': 'https://shop.test', 'text': 'No results'})
    eid = cite(s, oid, 'No results')
    def attempt(approach, reason='No results'):
        return begin_recovery(s, objective='Find requested option', semantic_state='empty',
                              recovery={'objective': approach, 'reason': reason, 'evidence_ids': [eid]})
    assert attempt('Inspect another region')
    assert not attempt('  inspect ANOTHER region ', 'Different wording')
    assert s.status == 'running'
    assert attempt('Resolve search ambiguity')
    assert not attempt('Revisit observed route')
    assert s.status == 'blocked'
    assert 'Find requested option' in s.progress['blocker']
    assert 'click' not in s.progress['blocker'].lower()


@pytest.mark.parametrize('kind,limit', [('actions', 150), ('jev', 300), ('coordinator', 60)])
def test_budget_is_checked_before_extra_work_and_blocks(kind, limit):
    from assistant.state import spend

    s = TaskState('t1', 'Obtain receipt')
    for _ in range(limit):
        spend(s, kind)
    with pytest.raises(ValueError, match=f'{kind} budget exhausted'):
        spend(s, kind)
    assert s.progress['counts'][kind] == limit
    assert s.status == 'blocked'
    assert 'Obtain receipt' in s.progress['blocker']


def test_failed_second_recovery_blocks_on_next_stall_without_third_request():
    from assistant.state import begin_recovery, track_progress

    s = TaskState('t1', 'Find requested option')
    oid = observe_state(s, {'url': 'https://shop.test', 'text': 'No results'})
    eid = cite(s, oid, 'No results')
    for approach in ['Inspect region', 'Resolve ambiguity']:
        assert begin_recovery(s, objective=s.objective, semantic_state='empty',
                              recovery={'objective': approach, 'reason': 'No results', 'evidence_ids': [eid]})
    for now in range(3):
        result = track_progress(s, operation='LOOK', semantic_state='empty', change_evidence=(), now=now)
    assert result == 'blocked'
    assert s.status == 'blocked'


def test_stale_evidence_and_backwards_clock_cannot_reset_stall():
    from assistant.state import record_progress_change, track_progress

    s = TaskState('t1', 'Find result')
    oid = observe_state(s, {'url': 'https://shop.test', 'text': 'Result available'})
    eid = cite(s, oid, 'Result available')
    record_progress_change(s, kind='relevant_content', evidence_ids=(eid,))
    track_progress(s, operation='WAIT', semantic_state='pending', change_evidence=(), now=10)
    with pytest.raises(ValueError, match='clock'):
        track_progress(s, operation='WAIT', semantic_state='pending', change_evidence=(), now=9)
    amend(s, 'Find another result', set())
    with pytest.raises(ValueError, match='stale'):
        track_progress(s, operation='LOOK', semantic_state='pending', change_evidence=(eid,), now=11)
