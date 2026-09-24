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
