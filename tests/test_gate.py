"""The commit gate: nothing irreversible without one explicit yes for that exact action. Fakes only."""

import pytest
from fakes import act, agent_with, decision

from assistant import agent as loop
from assistant.browser import StalePage

META = {"model": "fake", "latency_ms": 1}
CARD = {"action_label": "Pay ₹79", "amount": "₹79", "lines": ["Diet Coke 300 ml", "Home"], "element": ""}
WAIT = {"id": "wait", "kind": "wait", "label": "Wait", "node": None}


@pytest.fixture(autouse=True)
def card(monkeypatch):
    monkeypatch.setattr(loop.jobs, "confirm_card", lambda context, page, label, elements=(): (dict(CARD), META))


def outcomes(monkeypatch, *results):
    queue = list(results)
    monkeypatch.setattr(loop.jobs, "outcome", lambda context, page, label: (*queue.pop(0), META))


def clicked(a):
    return [call.args[1]["id"] for call in a.browser.act.call_args_list]


def approve(a, d):
    """After a yes the page is read again before acting: the first act stops as stale, the next one runs."""
    with pytest.raises(StalePage):
        act(a, d)
    return act(a, d)


































@pytest.mark.parametrize('operation,action,assessment,expected', [
    ('CLICK', {'label': 'Continue'}, None, 'unresolved'),
    ('CLICK', {'label': 'Continue'}, 'commitment', 'commitment'),
    ('CLICK', {'label': 'Send message'}, 'preparation', 'commitment'),
    ('TYPE_TEXT', {'label': 'Account name', 'auto_submit': True}, None, 'unresolved'),
    ('TYPE_TEXT', {'label': 'Account name', 'account_change': True}, 'preparation', 'commitment'),
    ('TYPE_TEXT', {'label': 'OTP', 'auto_submit': True}, 'authentication', 'authentication'),
    ('PRESS_KEY', {'label': 'Message', 'submit': 'Send'}, 'preparation', 'commitment'),
    ('ACCEPT_DIALOG', {}, None, 'unresolved'),
    ('CONFIRM', {}, 'preparation', 'commitment'),
    ('TYPE_TEXT', {'label': 'Search'}, 'preparation', 'preparation'),
])
def test_contextual_commitment_backstops(operation, action, assessment, expected):
    from assistant.rules import commitment_kind
    assert commitment_kind(operation, action, {}, assessment=assessment, key='Enter') == expected


def reviewed_agent(tmp_path, monkeypatch, replies=()):
    from copy import deepcopy

    from test_commit import review_fixture

    from assistant.commit import validate_review
    state, page, proposal, extraction = review_fixture()
    a = agent_with(tmp_path, page=page, replies=replies)
    a.task = state
    def coordinate(event):
        if event == 'confirmation':
            current = deepcopy(proposal)
            ids = [eid for eid, e in a.task.evidence.items()
                   if e['observation_id'] == next(reversed(a.task.observations))]
            current['evidence_ids'] = ids
            for line in current['lines']:
                line['evidence_ids'] = ids
            validate_review(a.task, current, extraction)
            a.task.proposal = current
    monkeypatch.setattr(a, 'coordinate', coordinate)
    a.task.progress['account_reservation_key'] = 'shop:test-account'
    return a


def test_exact_review_waits_for_new_jev_choice_after_approval(tmp_path, monkeypatch):
    a = reviewed_agent(tmp_path, monkeypatch, [{'option': 'yes'}])
    act(a, decision('e6', 'CLICK'))
    a.browser.act.assert_not_called()
    assert a.task.approval and not a.task.approval['consumed']
    assert 'Rain fee' in a.asked[0]['text']
    act(a, decision('e6', 'CLICK'))
    assert a.browser.act.call_count == 1 and a.task.approval['consumed']
    assert [r['phase'] for r in a.journal.records()] == ['dispatching', 'executed']


def test_declining_full_review_executes_nothing(tmp_path, monkeypatch):
    a = reviewed_agent(tmp_path, monkeypatch, [{'option': 'no'}])
    act(a, decision('e6', 'CLICK'))
    a.browser.act.assert_not_called()
    assert a.task.approval is None and len(a.asked) == 1


def test_commit_without_complete_review_blocks(tmp_path):
    a = agent_with(tmp_path)
    act(a, decision('e6', 'CLICK'))
    assert a.task.status == 'blocked' and not a.asked
    a.browser.act.assert_not_called()


def test_ambiguous_commit_receipt_never_repeats(tmp_path, monkeypatch):
    from assistant.browser import ExecutionUncertain
    a = reviewed_agent(tmp_path, monkeypatch, [{'option': 'yes'}])
    act(a, decision('e6', 'CLICK'))
    a.browser.act.side_effect = ExecutionUncertain('lost acknowledgment')
    act(a, decision('e6', 'CLICK'))
    assert a.task.approval['consumed'] and a.task.status == 'blocked'
    assert a.journal.interrupted()
    assert a.browser.act.call_count == 1


def test_persisted_execution_survives_failed_observation(tmp_path, monkeypatch):
    a = reviewed_agent(tmp_path, monkeypatch, [{'option': 'yes'}])
    act(a, decision('e6', 'CLICK'))
    a.browser.observe.side_effect = StalePage('navigation')
    with pytest.raises(StalePage):
        act(a, decision('e6', 'CLICK'))
    assert a.task.approval['consumed'] and a.journal.records()[-1]['phase'] == 'executed'
