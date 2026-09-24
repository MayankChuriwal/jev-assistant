"""Live-loop authority: facts cannot substitute a browser decision."""
import pytest
from fakes import agent_with, decision

from assistant import agent as loop
from assistant.state import put_fact


def test_answered_choice_waits_for_a_fresh_jev_decision(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{'option': 'home'}])
    goals = []
    monkeypatch.setattr(loop, 'choose', lambda page, goal, history:
                        goals.append(goal) or decision('wait', 'WAIT'))
    put_fact(a.task, 'address', 'Home', source='user')
    a.command('tick')
    assert len(goals) == 1 and 'Home' in goals[0]
    assert all(call.args[0] == 'WAIT' for call in a.browser.act.call_args_list)


def test_satisfied_phone_ask_never_substitutes_typing(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    put_fact(a.task, 'phone', '[profile:phone]', source='profile')
    monkeypatch.setattr(loop, 'choose', lambda *args: decision('ASK_USER', 'ASK_USER'))
    a.command('tick')
    assert a.asked == []
    a.browser.act.assert_not_called()
    assert a.state['status'] == 'ready'


def test_done_without_requirements_is_blocked(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    monkeypatch.setattr(loop, 'choose', lambda *args: decision('DONE', 'DONE'))
    a.command('tick')
    assert a.task.status == 'blocked'
    a.browser.act.assert_not_called()


def test_temporary_secret_is_scoped_and_requires_a_new_decision(tmp_path, monkeypatch):
    from fakes import act
    a = agent_with(tmp_path, replies=[{'text': '482913'}])
    a.task.progress['auth_flows'] = {'login': dict(flow_id='login', status='required', origin='https://shop.test',
        purpose='login', field_role='otp', revision=0, allowed_origins=['https://shop.test'], blocked=False)}
    monkeypatch.setattr(loop.jobs, 'effect_assessment', lambda *args, **kwargs: 'authentication')
    with pytest.raises(loop.StalePage):
        act(a, decision('e2', 'TYPE_TEXT'))
    assert len(a.asked) == 1 and a.asked[0]['kind'] == 'secret'
    a.browser.act.assert_not_called()
    act(a, decision('e2', 'TYPE_TEXT'))
    assert a.browser.act.call_args.kwargs == {'text': '482913'}
    assert '482913' not in str(a.trace()) + str(a.task)


def test_cancel_clears_temporary_redaction_values(tmp_path):
    a = agent_with(tmp_path)
    a.secrets.add('482913', '[secret:test]')
    a.state['memory'].append('482913')
    a.stop()
    assert '482913' not in str(a.state) + str(a.secrets.values)


def test_no_ordinary_step_recoordinates_unchanged_page(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    monkeypatch.setattr(loop, 'choose', lambda *args: decision('wait', 'WAIT'))
    a.command('tick')
    assert a.task.progress['coordinator_calls'] == 1


def test_auth_submission_receipt_uses_journal_status_without_fake_success(tmp_path, monkeypatch):
    from fakes import act

    from assistant.interaction import observe_auth
    a = agent_with(tmp_path, replies=[{'text': '482913'}])
    a.state['page']['actions'][1]['auto_submit'] = True
    a.retain_observation(a.state['page'])
    a.task.progress['auth_flows'] = {'login': dict(flow_id='login', status='required', origin='https://shop.test',
        purpose='login', field_role='otp', revision=0, allowed_origins=['https://shop.test'], blocked=False)}
    monkeypatch.setattr(loop.jobs, 'effect_assessment', lambda *args, **kwargs: 'authentication')
    with pytest.raises(loop.StalePage):
        act(a, decision('e2', 'TYPE_TEXT'))
    act(a, decision('e2', 'TYPE_TEXT'))
    receipt = next(iter(a.task.executions.values()))
    cid = receipt['challenge_id']
    assert receipt['status'] == 'executed' and receipt['submission']
    eid = next(e['id'] for e in reversed(list(a.task.evidence.values()))
               if e['observation_id'] == next(reversed(a.task.observations)))
    flow = observe_auth(a.task, dict(flow_id='login', challenge_id=cid, status='submitted',
        origin='https://shop.test', purpose='login', field_role='otp', evidence_ids=[eid],
        execution_id=receipt['execution_id'], expires_at=None, allowed_origins=['https://shop.test']))
    assert flow['status'] == 'submitted' and not flow['rejections']


def test_invalid_selection_gets_one_repair_and_never_executes_unused_heads(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    replies = iter([decision('e6', 'TYPE_TEXT', click_target='e6'), decision('wait', 'WAIT', click_target='e6')])
    monkeypatch.setattr(loop, 'choose', lambda *args: next(replies))
    a.command('tick')
    assert a.task.progress['counts']['jev'] == 2
    assert [call.args[0] for call in a.browser.act.call_args_list] == ['WAIT']


def test_secret_registry_does_not_survive_through_journal_redactor(tmp_path):
    a = agent_with(tmp_path)
    old = a.secrets
    old.add('482913', '[secret:code]')
    a.stop()
    assert not getattr(a.journal.redact, '__self__', None)


def test_explicit_provider_start_preserves_task_and_disables_guessed_location(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from fakes import FakeVault, fake_page

    from assistant.profile import Profile
    p = Profile(tmp_path / 'p.json')
    p.data['addresses'] = [{'label': 'Home', 'lat': 1, 'lng': 2}]
    browser = Mock(observe=Mock(return_value=fake_page()))
    made = []
    monkeypatch.setattr(loop.jobs, 'start_intent', lambda goal: {'provider': 'Shop', 'compare': False})
    monkeypatch.setattr(loop.jobs, 'start', lambda goal: ('https://shop.test/', 'Shop task', {}))
    monkeypatch.setattr(loop, 'Browser', lambda url, **kw: made.append((url, kw)) or browser)
    a = loop.Agent(None, 'Find tea on Shop', profile=p, vault=FakeVault())
    assert made == [('https://shop.test/', {'geolocation': None})]
    assert a.task.constraints['provider'] == 'Shop'


def test_malformed_scoped_code_is_corrected_before_storage(tmp_path, monkeypatch):
    from fakes import act
    a = agent_with(tmp_path, replies=[{'text': '12'}, {'text': '482913'}])
    a.task.progress['auth_flows'] = {'login': dict(flow_id='login', status='required', origin='https://shop.test',
        purpose='login', field_role='otp', revision=0, allowed_origins=['https://shop.test'], blocked=False)}
    monkeypatch.setattr(loop.jobs, 'effect_assessment', lambda *args, **kwargs: 'authentication')
    with pytest.raises(loop.StalePage):
        act(a, decision('e2', 'TYPE_TEXT'))
    assert len(a.asked) == 2
    assert all(q['kind'] == 'secret' for q in a.asked)
    assert not a.task.progress['auth_flows']['login'].get('rejections')
    act(a, decision('e2', 'TYPE_TEXT'))
    assert a.browser.act.call_args.kwargs['text'] == '482913'


def test_expired_secret_redaction_copy_is_removed_after_sanitizing(tmp_path):
    import time
    a = agent_with(tmp_path)
    ref = '[secret:expired]'
    a.secrets.add('864209', ref)
    a.task.challenges['expired'] = dict(id='expired', expires_at=time.time() - 1, status='challenge')
    put_fact(a.task, 'otp', ref, source='user', scope='expired')
    a.state['memory'].append('Typed 864209')
    a.discard_invalid_secrets()
    assert '864209' not in str(a.secrets.values) + str(a.state['memory'])
    assert not a.task.facts['otp']['valid']


def test_processing_poll_does_not_request_another_jev_action(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    a.task.set_status('waiting_site')
    a.state['commit'] = {'execution_id': 'pending', 'label': 'Booking'}
    calls = []
    monkeypatch.setattr(a, 'coordinate', lambda event: calls.append(event))
    monkeypatch.setattr(loop, 'choose', lambda *args: pytest.fail('No Jev decision while processing'))
    monkeypatch.setattr(loop.time, 'sleep', lambda _: None)
    a.command('tick')
    assert calls == ['outcome'] and a.browser.observe.call_count == 1
    a.browser.act.assert_not_called()


def test_goal_linked_fact_change_registers_stable_semantic_progress(tmp_path):
    a = agent_with(tmp_path)
    eid = next(eid for eid, e in a.task.evidence.items() if 'Diet Coke' in e['quote'])
    a.task.completion = [dict(id='read', kind='page', description='Read product', material_keys=['product'],
                              evidence_ids=[], execution_id=None, artifact_id=None)]
    def reply(*args, **kwargs):
        return dict(objective=a.task.goal, facts=[dict(key='product', value='Diet Coke', source='page', scope='task',
            revision=0, depends_on=[], evidence=[eid], valid=True)], requirements=[], completion=[], auth=None,
            progress=None, proposal=None, outcome=None, notice=None, recovery=None), {}
    a.coordinator_client = reply
    a.coordinate('stage_change')
    assert a.task.progress['changes'][eid]['kind'] == 'relevant_content'
    before = a.task.progress['changes'][eid]['identity']
    a.coordinate('stage_change')
    assert a.task.progress['changes'][eid]['identity'] == before


def test_saved_password_is_read_only_for_matching_login_origin(tmp_path, monkeypatch):
    from fakes import FakeVault, act, fake_page
    page = fake_page(actions=[dict(id='password', kind='fill', label='Password', node=1,
                                   constraints={'type': 'password'}, context={'field_role': 'current-password'})])
    a = agent_with(tmp_path, page=page, profile={'logins': ['shop.test']},
                   vault=FakeVault({'login.shop.test': 'correct horse'}))
    a.task.progress['auth_flows'] = {'login': dict(flow_id='login', status='required', origin='https://shop.test',
        purpose='login', field_role='password', revision=0, allowed_origins=['https://shop.test'], blocked=False)}
    monkeypatch.setattr(loop.jobs, 'effect_assessment', lambda *args, **kwargs: 'authentication')
    act(a, decision('password', 'TYPE_TEXT'))
    assert not a.asked and a.browser.act.call_args.kwargs['text'] == 'correct horse'
    assert 'correct horse' not in str(a.task) + str(a.trace())


def test_secret_structured_reply_never_enters_chat_history(tmp_path):
    a = agent_with(tmp_path, replies=[{'values': {'otp': '864209'}}])
    a.ask({'kind': 'secret', 'text': 'Code'})
    assert '864209' not in str(a.state['messages'])


def test_separate_auth_submit_is_jev_selected_and_receipt_bound(tmp_path, monkeypatch):
    from fakes import act, fake_page
    fields = [dict(id='otp', kind='fill', label='OTP', node=1, group=[1, 2, 3, 4, 5, 6],
                   constraints={'inputmode': 'numeric'}, context={'section': 'Verify login'}),
              dict(id='verify', kind='click', label='Verify', node=7, context={'section': 'Verify login'})]
    a = agent_with(tmp_path, page=fake_page(text='Verify login', actions=fields), replies=[{'text': '482913'}])
    a.task.progress['auth_flows'] = {'login': dict(flow_id='login', status='required', origin='https://shop.test',
        purpose='login', field_role='otp', revision=0, allowed_origins=['https://shop.test'], blocked=False)}
    monkeypatch.setattr(loop.jobs, 'effect_assessment', lambda *args, **kwargs: 'authentication')
    with pytest.raises(loop.StalePage):
        act(a, decision('otp', 'TYPE_TEXT'))
    act(a, decision('otp', 'TYPE_TEXT'))
    act(a, decision('verify', 'CLICK'))
    assert [c.args[0] for c in a.browser.act.call_args_list] == ['TYPE_TEXT', 'CLICK']
    typed, submitted = a.task.executions.values()
    assert not typed['submission'] and submitted['submission']
    assert submitted['challenge_id'] == typed['challenge_id']


def test_auth_redirect_records_code_owned_navigation_for_origin_gate(tmp_path, monkeypatch):
    from fakes import act, fake_page
    a = agent_with(tmp_path)
    a.task.progress['auth_flows'] = {'login': dict(flow_id='login', status='required', origin='https://shop.test',
        purpose='login', field_role='account', revision=0, allowed_origins=['https://shop.test'], blocked=False)}
    a.browser.observe.return_value = fake_page(url='https://identity.test/', text='Enter your account')
    monkeypatch.setattr(loop.jobs, 'effect_assessment', lambda *args, **kwargs: 'authentication')
    act(a, decision('e5', 'CLICK'))
    nav = next(e for e in a.task.executions.values() if e.get('kind') == 'navigation')
    assert nav['origin'] == 'https://shop.test' and nav['destination_origin'] == 'https://identity.test'
    assert nav['flow_id'] == 'login' and nav['evidence_ids']


def test_record_directory_keeps_masked_captures(tmp_path):
    from unittest.mock import Mock

    from fakes import FakeVault, fake_page

    from assistant.profile import Profile
    b = Mock(observe=Mock(return_value=fake_page()), screenshot=Mock(return_value='aW1hZ2U='))
    a = loop.Agent('https://shop.test/', 'Inspect the page', browser=b, vault=FakeVault(),
                   profile=Profile(tmp_path / 'p.json'), record_dir=tmp_path / 'record')
    assert (tmp_path / 'record' / '000001.png').read_bytes() == b'image'
    assert b.screenshot.call_args.kwargs['masked'] == a.secrets.nodes


def test_confirmation_formats_exact_reviewed_currency_units(tmp_path, monkeypatch):
    from fakes import act
    from test_gate import reviewed_agent
    a = reviewed_agent(tmp_path, monkeypatch, [{'option': 'no'}])
    act(a, decision('e6', 'CLICK'))
    assert 'Rain fee: INR 10.00' in a.asked[0]['text']
    assert 'Total (exact): INR 110.00' in a.asked[0]['text']


def test_challenge_refresh_does_not_cross_origin_with_identical_fields(tmp_path):
    from fakes import fake_page
    a = agent_with(tmp_path)
    a.task.challenges['otp'] = dict(revision=0, origin='https://shop.test', status='answer_ready',
        observation_id='old', field_label='Code', field_context={}, field_constraints={})
    page = fake_page(url='https://other.test/', actions=[dict(id='otp', kind='fill', label='Code')])
    a.retain_observation(page)
    a.refresh_challenges(page)
    assert a.task.challenges['otp']['observation_id'] == 'old'


def test_expired_challenge_cannot_be_rebound_as_current(tmp_path, monkeypatch):
    a = agent_with(tmp_path)
    a.task.challenges['otp'] = dict(status='answer_ready', expires_at=1)
    monkeypatch.setattr(loop.time, 'time', lambda: 2)
    a.discard_invalid_secrets()
    assert a.task.challenges['otp']['status'] == 'expired'
