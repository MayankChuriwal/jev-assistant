"""The help ladder when Jev says BLOCKED. Fakes only."""

import pytest
from fakes import act, agent_with, decision, fake_page

from assistant import agent as loop

META = {"model": "fake", "latency_ms": 1}


def ladder(monkeypatch, *steps):
    queue, calls = list(steps), []

    def fake(context, page, elements, must_stop=False):
        calls.append(must_stop)
        step = dict(queue.pop(0))
        return ({**step, "rung": "stop"} if must_stop else step), META

    monkeypatch.setattr(loop.jobs, "unblock", fake)
    return calls












TILES = [{"id": f"e{n}", "kind": "click", "label": "clickable", "role": "button", "node": 100 + n, "frame": "F1"}
         for n in range(1, 5)]
VERIFY = {"id": "e9", "kind": "click", "label": "Verify", "role": "button", "node": 200, "frame": "F1"}
WAIT = {"id": "wait", "kind": "wait", "label": "Wait", "node": None}






def test_a_captcha_badge_alone_is_not_a_challenge(tmp_path, monkeypatch):
    from assistant import rules

    assert not rules.captcha_challenge(fake_page(text="LOG IN protected by reCAPTCHA Privacy - Terms"))
    assert rules.captcha_challenge(fake_page(text="I'm not a robot reCAPTCHA"))


def test_chat_only_help_rejects_device_handoff_and_considers_alternatives(monkeypatch):
    import pytest

    from assistant import jobs
    responses = [dict(rung='device', text='Approve in your banking app', options=[]),
                 dict(rung='guide', text='Click the button on the website', options=[]),
                 dict(rung='stop', text='Passkey required', options=[]),
                 dict(rung='stop', text='No chat-compatible sign-in method is available', options=[])]
    monkeypatch.setattr(jobs, 'text_json', lambda *a, **kw: (responses.pop(0), META))
    for checked in (False, False, False):
        with pytest.raises(ValueError):
            jobs.unblock({}, fake_page(), {}, chat_only=True, alternatives_checked=checked)
    result, _ = jobs.unblock({}, fake_page(), {}, chat_only=True, alternatives_checked=True)
    assert result['rung'] == 'stop'


def test_blocker_never_asks_for_device_takeover(tmp_path):
    a = agent_with(tmp_path)
    act(a, decision('BLOCKED', 'BLOCKED'))
    assert a.task.status == 'blocked' and not a.asked
    assert all('device' not in m['text'] for m in a.told)


@pytest.mark.parametrize('kind,answer,expected', [
    ('text', '012345', {'kind': 'text', 'value': '012345'}),
    ('tiles', '1 2', {'kind': 'tiles', 'labels': ['1', '2']})])
def test_captcha_answer_is_fact_without_clicks(tmp_path, kind, answer, expected):
    a = agent_with(tmp_path, page=fake_page(text='Select all images captcha'),
                   replies=[{'option': kind}, {'text': answer}])
    a.browser.marked_screenshot.return_value = 'IMAGE'
    act(a, decision('ASK_USER', 'ASK_USER'))
    a.browser.act.assert_not_called()
    fact = next(f for k, f in a.task.facts.items() if k.startswith('captcha:'))
    assert fact['value'] == expected


def test_captcha_stale_answer_executes_nothing(tmp_path):
    a = agent_with(tmp_path, page=fake_page(text='Select all images captcha'),
                   replies=[{'option': 'tiles'}, {'text': '1'}])
    a.browser.marked_screenshot.return_value = 'IMAGE'
    a.browser.fresh.side_effect = [True, True, False]
    with pytest.raises(loop.StalePage):
        act(a, decision('ASK_USER', 'ASK_USER'))
    a.browser.act.assert_not_called()
    assert not any(k.startswith('captcha:') for k in a.task.facts)
