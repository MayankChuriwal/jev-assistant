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


def text_captcha_with_submit(tmp_path):
    from copy import deepcopy

    from assistant.browser import fingerprint
    page = fake_page(text='Select all images captcha — type the shown text', actions=[
        dict(id='code', kind='fill', label='CAPTCHA text', node=1, value='', context={'form': 'captcha'}),
        dict(id='verify', kind='click', label='Verify', node=2, context={'form': 'captcha'}),
        dict(id='other', kind='click', label='Delete account', node=3, context={'form': 'settings'})])
    a = agent_with(tmp_path, page=page, replies=[{'option': 'text'}, {'text': 'abCD'}])
    a.browser.marked_screenshot = Mock(return_value='unchanged-challenge')
    a.relay_captcha(page)
    after = deepcopy(page)
    after['actions'][0]['value'] = 'abCD'
    after['fingerprint'] = fingerprint(after)
    a.browser.observe.return_value = after
    return a


@pytest.mark.parametrize('operation,target,key', [('CLICK', 'verify', None), ('PRESS_KEY', 'code', 'Enter')])
def test_text_captcha_applied_answer_allows_fresh_selected_submit(tmp_path, operation, target, key):
    a = text_captcha_with_submit(tmp_path)
    act(a, decision('code', 'TYPE_TEXT'))
    assert a.browser.act.call_count == 1
    act(a, decision(target, operation, key=key))
    assert [call.args[0] for call in a.browser.act.call_args_list] == ['TYPE_TEXT', operation]
    act(a, decision(target, operation, key=key))
    assert a.browser.act.call_count == 2


@pytest.mark.parametrize('change', ['pixels', 'missing_value', 'wrong_action', 'retype'])
def test_text_captcha_submit_needs_unchanged_applied_answer(tmp_path, change):
    a = text_captcha_with_submit(tmp_path)
    if change == 'missing_value':
        a.browser.observe.return_value['actions'][0]['value'] = ''
    act(a, decision('code', 'TYPE_TEXT'))
    if change == 'pixels':
        a.browser.marked_screenshot.return_value = 'replacement-challenge'
    target, operation = ('code', 'TYPE_TEXT') if change == 'retype' else ('verify', 'CLICK')
    if change == 'wrong_action':
        target = 'other'
    try:
        act(a, decision(target, operation))
    except loop.StalePage:
        pass
    assert a.browser.act.call_count == 1


@pytest.mark.browser
def test_real_text_captcha_type_observe_and_verify(browser_at, tmp_path):
    b = browser_at('/reliability.html?captcha')
    b.evaluate("document.body.innerHTML = `<p>Select all images captcha — type the shown text</p>"
               "<div>abCD</div><form id='human'><label>CAPTCHA text<input name='answer'></label>"
               "<button type='button' onclick=\"document.body.dataset.verified='yes'\">Verify</button></form>`")
    page = b.observe()
    a = agent_with(tmp_path, page=page, replies=[{'option': 'text'}, {'text': 'abCD'}])
    a.browser = b
    a.relay_captcha(page)
    field = next(e for e in page['actions'] if e['kind'] == 'fill')
    act(a, decision(field['id'], 'TYPE_TEXT'))
    challenge = next(c for c in a.task.challenges.values() if c.get('purpose') == 'captcha')
    assert challenge.get('status') == 'answer_applied', {
        'challenge': challenge, 'after': a.state['page'], 'before': page,
        'digest': a.captcha_image_digest(challenge), 'executions': a.task.executions}
    verify = next(e for e in a.state['page']['actions'] if e['kind'] == 'click' and e['label'] == 'Verify')
    assert verify['id'] in challenge['submit_targets'], {'field': field, 'verify': verify}
    result = act(a, decision(verify['id'], 'CLICK'))
    assert b.evaluate('document.body.dataset.verified') == 'yes', result


def test_text_captcha_submit_does_not_cross_same_named_forms(tmp_path):
    a = text_captcha_with_submit(tmp_path)
    # Recreate the displayed round with code-owned form identity and a namesake form.
    page = a.state['page']
    page['actions'][0]['context']['form_node'] = 10
    page['actions'][1]['context']['form_node'] = 10
    page['actions'][2].update(label='Verify', context={'form': 'captcha', 'form_node': 20})
    a.asker = lambda message: {'option': 'text'} if message['kind'] == 'choice' else {'text': 'abCD'}
    a.relay_captcha(page)
    c = list(a.task.challenges.values())[-1]
    assert c['submit_targets'] == ['verify']


@pytest.mark.parametrize('kind,answer', [('text', 'new text'), ('tiles', '1')])
def test_captcha_answer_image_matches_digest_after_kind_wait_replacement(tmp_path, kind, answer):
    import hashlib

    a = agent_with(tmp_path, page=fake_page(text='Select all images captcha'))
    a.browser.marked_screenshot = Mock(return_value='old-image')
    shown = []
    def ask(message):
        shown.append(message['image'])
        if message['kind'] == 'choice':
            a.browser.marked_screenshot.return_value = 'new-image'
            return {'option': kind}
        return {'text': answer}
    a.asker = ask
    a.relay_captcha(a.state['page'])
    c = next(iter(a.task.challenges.values()))
    assert shown == ['old-image', 'new-image']
    assert c['image_digest'] == hashlib.sha256(shown[-1].encode()).hexdigest()


def test_text_captcha_wait_preserves_applied_answer_for_verify(tmp_path):
    a = text_captcha_with_submit(tmp_path)
    wait = dict(id='wait', kind='wait', label='Wait', node=None)
    a.state['page']['actions'].append(wait)
    a.browser.observe.return_value['actions'].append(wait)
    a.asker = lambda message: {'option': 'text'} if message['kind'] == 'choice' else {'text': 'abCD'}
    a.relay_captcha(a.state['page'])
    act(a, decision('code', 'TYPE_TEXT'))
    act(a, decision('wait', 'WAIT'))
    assert list(a.task.challenges.values())[-1]['status'] == 'answer_applied'
    act(a, decision('verify', 'CLICK'))
    assert [c.args[0] for c in a.browser.act.call_args_list] == ['TYPE_TEXT', 'WAIT', 'CLICK']
