"""Live Agent scope regressions; no models or real browser calls."""
from unittest.mock import Mock

import pytest
from fakes import act, agent_with, decision, fake_page

from assistant import agent as loop
from assistant.state import put_fact


def challenge_agent(tmp_path, **field_extra):
    field = dict(id='e1', kind='fill', label='OTP', node=1,
                 constraints={'inputmode': 'numeric'}, context={'form': 'login'}, **field_extra)
    a = agent_with(tmp_path, page=fake_page(actions=[field]), replies=[{'text': '482913'}])
    a.task.progress['auth_flows'] = {'login': dict(flow_id='login', status='required', origin='https://shop.test',
        purpose='login', field_role='otp', revision=0, allowed_origins=['https://shop.test'], blocked=False)}
    with pytest.raises(loop.StalePage):
        act(a, decision('e1', 'TYPE_TEXT'))
    return a, field


def test_renumbered_secret_field_rebinds_only_unique_identity(tmp_path):
    a, field = challenge_agent(tmp_path)
    page = fake_page(actions=[dict(field, id='e2'), dict(field, id='e1', label='Search', context={})])
    a.browser.observe.return_value = page
    a.observe()
    assert a.challenge_for(page['actions'][1]) is None
    assert a.challenge_for(page['actions'][0])['field_ids'] == ['e2']


@pytest.mark.parametrize('change', ['duplicate', 'frame', 'frame_origin'])
def test_secret_rebinding_invalidates_ambiguous_or_changed_frame(tmp_path, change):
    a, field = challenge_agent(tmp_path, frame='frame1')
    c = next(iter(a.task.challenges.values()))
    page = fake_page(actions=[dict(field)])
    if change == 'duplicate':
        page['actions'].append(dict(field, id='e2'))
    elif change == 'frame':
        page['actions'][0]['frame'] = 'frame2'
    else:
        page['frames'] = {'frame1': {'url': 'https://evil.test/login'}}
    a.browser.observe.return_value = page
    a.observe()
    assert a.challenge_for(page['actions'][0]) is None
    assert not a.task.facts['secret:' + c['id']]['valid']


def test_final_dispatch_stale_preserves_secret_for_fresh_selection(tmp_path):
    a, field = challenge_agent(tmp_path)
    a.browser.fresh.side_effect = [True, True, True, False]
    with pytest.raises(loop.StalePage):
        act(a, decision('e1', 'TYPE_TEXT'))
    assert not a.browser.act.called
    a.browser.fresh.side_effect = None
    a.browser.fresh.return_value = True
    act(a, decision('e1', 'TYPE_TEXT'))
    assert a.browser.act.call_args.kwargs['text'] == '482913'


def captcha_agent(tmp_path, kind='tiles', answer='1'):
    page = fake_page(text='Select all images captcha', actions=[
        dict(id='e1', kind='click', label='Tile one', node=1),
        dict(id='e2', kind='click', label='Tile two', node=2),
        dict(id='e3', kind='fill', label='CAPTCHA text', node=3)])
    a = agent_with(tmp_path, page=page, replies=[{'option': kind}, {'text': answer}])
    a.browser.marked_screenshot = Mock(return_value='image-one')
    a.relay_captcha(page)
    return a


def test_captcha_new_observation_invalidates_answer(tmp_path):
    a = captcha_agent(tmp_path)
    a.browser.observe.return_value = fake_page(text='New images captcha')
    a.observe()
    assert not next(f for k, f in a.task.facts.items() if k.startswith('captcha:'))['valid']


@pytest.mark.parametrize('change', ['wrong_target', 'replaced_image'])
def test_captcha_dispatch_checks_answer_target_and_current_image(tmp_path, change):
    a = captcha_agent(tmp_path)
    target = 'e2' if change == 'wrong_target' else 'e1'
    if change == 'replaced_image':
        a.browser.marked_screenshot.return_value = 'image-two'
    if change == 'replaced_image':
        with pytest.raises(loop.StalePage):
            act(a, decision(target, 'CLICK'))
    else:
        assert act(a, decision(target, 'CLICK'))['status'] == 'blocked'
    assert not a.browser.act.called


def test_captcha_literal_text_uses_fresh_jev_selected_field(tmp_path, monkeypatch):
    a = captcha_agent(tmp_path, kind='text', answer='abCD')
    helper = Mock(side_effect=AssertionError('must use literal human answer'))
    monkeypatch.setattr(loop, 'field_value', helper)
    act(a, decision('e3', 'TYPE_TEXT'))
    assert a.browser.act.call_args.kwargs['text'] == 'abCD'


def test_model_url_cannot_silently_switch_selected_provider(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{'text': 'https://shop.test/search'}])
    a.task.constraints['provider'] = 'Shop'
    monkeypatch.setattr(loop, 'url_value', lambda *args: ('https://competitor.test/', {}))
    with pytest.raises((loop.StalePage, loop.Halt)):
        act(a, decision('open_url', 'OPEN_URL'))
    assert not a.browser.act.called
    assert a.asked


def test_explicit_user_destination_allows_fresh_jev_navigation(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    put_fact(a.task, 'navigation_url', 'https://alternate.test/', source='user')
    monkeypatch.setattr(loop, 'url_value', lambda *args: ('https://alternate.test/', {}))
    act(a, decision('open_url', 'OPEN_URL'))
    assert a.browser.act.call_args.kwargs['url'] == 'https://alternate.test/'


def test_browser_preflight_stale_preserves_secret_but_unknown_consumes(tmp_path):
    a, field = challenge_agent(tmp_path)
    a.browser.act.side_effect = loop.StalePage('DOM changed before input')
    with pytest.raises(loop.StalePage):
        act(a, decision('e1', 'TYPE_TEXT'))
    c = next(iter(a.task.challenges.values()))
    ref = a.task.facts['secret:' + c['id']]['value']
    assert a.secret_store.resolve(ref, **a.secret_scope(c)) == '482913'
    a.browser.act.side_effect = RuntimeError('connection lost after input')
    assert act(a, decision('e1', 'TYPE_TEXT'))['status'] == 'blocked'
    with pytest.raises(ValueError):
        a.secret_store.resolve(ref, **a.secret_scope(c))


def test_replaced_captcha_pixels_leave_no_active_answer_for_jev(tmp_path, monkeypatch):
    a = captcha_agent(tmp_path, kind='text', answer='uniqueOldCaptchaAnswer')
    a.browser.marked_screenshot.return_value = 'replacement-image'
    choices = []
    monkeypatch.setattr(loop, 'choose', lambda page, goal, history:
                        choices.append(goal) or decision(None, 'ASK_USER'))
    a.predict()
    assert 'uniqueOldCaptchaAnswer' not in choices[0]
    assert not next(f for k, f in a.task.facts.items() if k.startswith('captcha:'))['valid']


def test_selected_tile_executes_once_then_requires_new_round(tmp_path):
    a = captcha_agent(tmp_path)
    act(a, decision('e1', 'CLICK'))
    assert a.browser.act.call_count == 1
    assert act(a, decision('e1', 'CLICK'))['status'] == 'blocked'
    assert a.browser.act.call_count == 1
