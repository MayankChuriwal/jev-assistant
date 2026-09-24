"""Signing in comes first on any site that offers it: the account holds saved addresses and payment. Fakes only."""

import pytest
from fakes import agent_with, decision, fake_page

from assistant import agent as loop

SIGN_IN = {"id": "e9", "kind": "click", "label": "Sign In", "role": "button", "node": 13}
WAIT = {"id": "wait", "kind": "wait", "label": "Wait", "node": None}


def test_a_site_that_offers_sign_in_is_signed_in_to_first(tmp_path):
    a = agent_with(tmp_path, page=fake_page(text="Enter your delivery location", actions=[SIGN_IN, WAIT]))
    a.track_login(a.state["page"])
    assert a.context().split("\n")[1].startswith("Current step: sign in to shop.test first.")
    a.state["history"].append({"operation": "TYPE_TEXT", "text": "[secret:answer1]"})  # the code was typed
    a.track_login(fake_page(text="Saved addresses: Home, Office", actions=[WAIT]))
    assert "Current step" not in a.context() and a.state["memory"][-1] == "Signed in to shop.test."


def test_sign_in_stays_the_next_step_until_the_site_shows_it_is_done(tmp_path):
    a = agent_with(tmp_path, page=fake_page(actions=[SIGN_IN, WAIT]))
    a.track_login(a.state["page"])
    a.track_login(fake_page(actions=[WAIT]))  # a page without the button, but nothing was typed to sign in
    assert "Current step: sign in to shop.test first." in a.context()


def test_google_and_apple_buttons_are_not_the_sign_in_to_use(tmp_path):
    a = agent_with(tmp_path, page=fake_page(actions=[{**SIGN_IN, "label": "Continue with Google"}, WAIT]))
    a.track_login(a.state["page"])
    assert "Current step" not in a.context()


def test_the_sign_in_step_reaches_jev(tmp_path, monkeypatch):
    goals = []
    monkeypatch.setattr(loop, "choose", lambda page, goal, history: goals.append(goal) or decision("wait", "WAIT"))
    a = agent_with(tmp_path, page=fake_page(actions=[SIGN_IN, WAIT]))
    a.command("predict")
    assert "Current step: sign in to shop.test first." in goals[0]


CODE = {"id": "e2", "kind": "fill", "label": "One time password", "role": "textbox", "value": "", "node": 2}


def test_typing_the_phone_number_is_not_yet_signing_in(tmp_path):
    a = agent_with(tmp_path, page=fake_page(actions=[SIGN_IN, WAIT]))
    a.track_login(a.state["page"])
    a.state["history"].append({"operation": "TYPE_TEXT", "text": "98765 43210"})
    a.track_login(fake_page(text="Enter OTP", actions=[CODE, WAIT]))  # the code screen has no sign-in button
    assert "Current step: sign in to shop.test first." in a.context() and "Signed in" not in str(a.state["memory"])


def asking(monkeypatch, a, fresh):
    """Jev chooses ASK_USER right after a step; `fresh` says whether the page is unchanged at each check."""
    monkeypatch.setattr(loop, "choose", lambda page, goal, history: decision("ASK_USER", "ASK_USER"))
    question = {"kind": "text", "text": "What's your phone number?", "options": [], "preference": False}
    monkeypatch.setattr(loop.jobs, "compose", lambda context, page, elements: (question, {}))
    a.steady_ms = 300
    a.browser.fresh.side_effect = fresh
    a.state["history"].append({"operation": "CLICK", "action": "Continue", "executed_ms": a.elapsed()})


def test_a_page_still_changing_after_a_step_is_read_again_before_asking(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"text": "later"}], page=fake_page(actions=[WAIT]))
    asking(monkeypatch, a, [True, True, False, True, True, True, True])  # the next screen arrives mid-wait
    a.command("tick")
    assert a.asked == [] and a.state["status"] == "ready"


def test_a_page_that_stays_put_after_a_step_is_asked_about(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"text": "later"}], page=fake_page(actions=[WAIT]))
    asking(monkeypatch, a, lambda page, action=None: True)
    a.command("tick")
    assert [m["text"] for m in a.asked] == ["What's your phone number?"]


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
