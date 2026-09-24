"""Wrong OTPs, CVVs, and other rejected values: count, warn, stop, never reuse. Fakes only."""

import itertools
from copy import deepcopy

from fakes import act, agent_with, decision

from assistant import agent as loop
from assistant.browser import fingerprint

META = {"model": "fake", "latency_ms": 1}


def field(monkeypatch, *forms):
    queue, calls = list(forms), []

    def fake(context):
        calls.append(context)
        return queue.pop(0), META

    monkeypatch.setattr(loop, "field_value", fake)
    return calls


def reacting(a):
    """The site answers every attempt with a new error line, as real sites do, so these tests meet the attempt
    limits and never the no-progress rule (which asks the user for help)."""
    count = itertools.count(1)

    def observe(screenshot=False):
        page = deepcopy(a.browser.observe.return_value)
        page["text"] += f"\nThat didn't work ({next(count)})"
        page["fingerprint"] = fingerprint(page)
        return page

    a.browser.observe.side_effect = observe
    return a


def typed(a):
    return [call.kwargs["text"] for call in a.browser.act.call_args_list]


ASK = ("ask", "Enter the code Swiggy sent you.", True, None)


















WAIT_ONLY = [{"id": "wait", "kind": "wait", "label": "Wait", "node": None}]








OTP = {"id": "e1", "kind": "fill", "label": "Enter an OTP Code", "role": "textbox", "value": "", "node": 1,
       "constraints": {"type": "text"}}






def test_login_numeric_format_error_does_not_become_a_flow_rejection():
    from reliability_fakes import evidence_for, observed

    from assistant.interaction import observe_auth
    from assistant.rules import format_error
    s, _, oid = observed('Enter six digit login code')
    event = dict(flow_id='login', challenge_id=None, status='required', origin='https://shop.test',
                 purpose='login', field_role='otp', evidence_ids=evidence_for(s, oid, 'Enter six digit login code'),
                 execution_id=None, expires_at=None, allowed_origins=['https://shop.test'])
    observe_auth(s, event)
    assert format_error('12x', {'kind': 'fill', 'constraints': {'inputmode': 'numeric', 'maxlength': 6}})
    assert s.progress['auth_flows']['login']['rejections'] == 0
    assert not s.executions


def test_reselecting_phone_is_not_rejection(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    monkeypatch.setattr(loop, 'field_value', lambda payload: (('profile', 'phone'), META))
    act(a, decision('e1', 'TYPE_TEXT'))
    a.field_cache.clear()
    act(a, decision('e1', 'TYPE_TEXT'))
    assert not a.task.progress.get('auth_flows')
    assert not a.asked and not a.state['rejected']


def test_unscoped_secret_fails_closed(tmp_path):
    a = agent_with(tmp_path, replies=[{'text': '482913'}])
    act(a, decision('e2', 'TYPE_TEXT'))
    assert a.task.status == 'blocked' and not a.asked
    a.browser.act.assert_not_called()


def test_newest_unused_secret_cannot_fill_another_challenge(tmp_path):
    a = agent_with(tmp_path)
    a.state['answers']['old'] = '482913'
    act(a, decision('e2', 'TYPE_TEXT'))
    a.browser.act.assert_not_called()
    assert a.task.status == 'blocked'
