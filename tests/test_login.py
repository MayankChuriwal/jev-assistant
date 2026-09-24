"""Signing in comes first on any site that offers it: the account holds saved addresses and payment. Fakes only."""

import pytest
from fakes import agent_with, decision, fake_page

from assistant import agent as loop

SIGN_IN = {"id": "e9", "kind": "click", "label": "Sign In", "role": "button", "node": 13}
WAIT = {"id": "wait", "kind": "wait", "label": "Wait", "node": None}










CODE = {"id": "e2", "kind": "fill", "label": "One time password", "role": "textbox", "value": "", "node": 2}




def asking(monkeypatch, a, fresh):
    """Jev chooses ASK_USER right after a step; `fresh` says whether the page is unchanged at each check."""
    monkeypatch.setattr(loop, "choose", lambda page, goal, history: decision("ASK_USER", "ASK_USER"))
    question = {"kind": "text", "text": "What's your phone number?", "options": [], "preference": False}
    monkeypatch.setattr(loop.jobs, "compose", lambda context, page, elements: (question, {}))
    a.steady_ms = 300
    a.browser.fresh.side_effect = fresh
    a.state["history"].append({"operation": "CLICK", "action": "Continue", "executed_ms": a.elapsed()})






def test_public_search_with_sign_in_link_does_not_create_an_auth_flow():
    from reliability_fakes import observed

    from assistant.coordinator import apply_assessment, assess
    s, page, _ = observed('Search public products | Sign in', goal='Find a desk lamp')
    result = dict(objective='Find a desk lamp', facts=[], requirements=[], completion=[], auth=None,
                  progress=None, proposal=None, outcome=None, notice=None, recovery=None)
    assessment = assess(s, page, 'start', client=lambda *a, **kw: (result, {}))
    apply_assessment(s, assessment)
    assert not s.questions and not s.challenges and not s.progress.get('auth_flows')


@pytest.mark.browser
def test_human_captcha_fixture_exposes_text_and_tiles_without_answer_clicks(browser_at):
    from assistant.interaction import record_captcha_answer
    from assistant.state import TaskState, observe_state
    browser = browser_at('/reliability.html?captcha')
    page = browser.observe()
    s = TaskState('human', 'Complete human challenge')
    oid = observe_state(s, page)
    s.challenges['c'] = dict(id='c', kind='tiles', revision=0, tiles=['A1', 'B2'], rounds=0,
                             observation_id=oid)
    assert any('CAPTCHA text' in a['label'] for a in page['actions'])
    assert any('Tile A1' in a['label'] for a in page['actions'])
    fact = record_captcha_answer(s, 'c', 'A1', revision=0)
    assert fact['value'] == {'kind': 'tiles', 'labels': ['A1']}
    assert not s.executions


def test_sign_in_banner_does_not_create_auth_requirement(tmp_path, monkeypatch):
    a = agent_with(tmp_path, page=fake_page(actions=[SIGN_IN, WAIT]))
    monkeypatch.setattr(loop, 'choose', lambda *args: decision('wait', 'WAIT'))
    a.command('tick')
    assert not a.task.progress.get('auth_flows')
    assert 'sign in to shop.test first' not in a.context()


def test_existing_authenticated_session_ignores_persistent_banner(tmp_path):
    a = agent_with(tmp_path, page=fake_page(text='Signed in as customer | Sign In', actions=[SIGN_IN, WAIT]))
    from assistant.interaction import observe_auth
    eid = next(iter(a.task.evidence))
    observe_auth(a.task, dict(flow_id='login', challenge_id=None, status='authenticated',
        origin='https://shop.test', purpose='login', field_role='account', evidence_ids=[eid],
        execution_id=None, expires_at=None, allowed_origins=['https://shop.test']))
    assert a.task.progress['auth_flows']['login']['status'] == 'authenticated'
    assert not a.asked
