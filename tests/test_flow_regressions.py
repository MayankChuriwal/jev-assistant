"""Real Chrome + Agent gates; fixture-owned receipts, scripted model transport only."""
import json

import pytest
from reliability_fakes import assessment, current_evidence, fixture_agent_class
from test_shop import choice

from assistant import model, questions
from assistant.profile import Profile

pytestmark = pytest.mark.browser

ITEMS = {'shop': 'Tea × 1', 'ride': 'Compact ride', 'form': 'Message to reception',
         'account': 'Cancel subscription'}


def install_models(monkeypatch, family, *, pending=False, with_choice=False):
    requests = []
    item = ITEMS[family]

    def text(system, payload):
        if system == questions.COORDINATE:
            task = payload['task']
            ids = current_evidence(task)
            events = [eid for eid in ids if task['evidence'][eid]['kind'] == 'browser-event']
            result = assessment()
            if payload['event'] == 'start':
                result['completion'] = [dict(id='result', description='Confirm '+item, kind='action',
                    material_keys=['requested_item'], evidence_ids=[], execution_id=None, artifact_id=None)]
                result['facts'] = [dict(key='requested_item', value=item, source='page', scope='task',
                    revision=task['revision'], depends_on=[], evidence=ids, valid=True)]
            if with_choice and 'ride_option' not in task['facts']:
                result['requirements'] = [dict(id='ride-option', kind='choice', purpose='ride', keys=['ride_option'],
                    scope='task', depends_on=[], reason='Choose a ride', options=[
                        dict(id='compact', label='Compact', description='4 seats, 3 minutes', evidence_ids=ids),
                        dict(id='large', label='Large', description='6 seats, 8 minutes', evidence_ids=ids)])]
            if payload['event'] == 'confirmation':
                rows = payload['untrusted_page']['text'].splitlines()
                total = next(row for row in rows if row.startswith('Total ₹'))
                fee_row = next(row for row in rows if row.startswith(('Service fee', 'Handling charge')))
                fee = int(fee_row.split('₹')[1])
                result['proposal'] = dict(task_id=task['task_id'], revision=task['revision'],
                    action='Confirm request', service=family, account='', destination='Home — 12 MG Road',
                    items=[item], option='', timing='Tomorrow', payment='Cash', currency='INR',
                    total_minor=int(total.split('₹')[1])*100, total_kind='exact', later_terms=[],
                    lines=[dict(id='item', label=item, amount_minor=9500, currency='INR', role='item',
                                included_in=None, evidence_ids=ids),
                           dict(id='fee', label=fee_row.split(' ₹')[0], amount_minor=fee*100, currency='INR',
                                role='fee', included_in=None, evidence_ids=ids)], evidence_ids=ids)
            if payload['event'] == 'outcome':
                result['outcome'] = dict(status='processing' if pending else 'success',
                                         evidence_ids=events or ids, artifact_id=None)
            return result
        if system == questions.REVIEW_AMOUNTS:
            p = payload['proposal']
            amounts = {row['id']: '₹'+str(row['amount_minor']//100) for row in p['lines']}
            return dict(exponent=2, total_text='₹'+str(p['total_minor']//100),
                total_quote='Total ₹'+str(p['total_minor']//100), amounts=amounts,
                line_quotes={row['id']: row['label']+' '+amounts[row['id']] for row in p['lines']})
        if system == questions.COMPLETION_PROOF:
            task = payload['task']
            events = [eid for eid, row in task['evidence'].items()
                      if row['kind'] == 'browser-event' and row['observation_id'] == task['observation_ids'][-1]]
            execution = next(eid for eid, row in payload['executions'].items() if row['commitment'])
            return {'bindings': [dict(requirement_id='result', evidence_ids=events,
                                      execution_id=execution, artifact_id=None)]}
        if system.startswith('Classify the external effect'):
            return {'effect': 'preparation' if payload['selected_action'].get('role') == 'radio' else 'commitment'}
        raise AssertionError('Unexpected model job: '+system[:100])

    class Response:
        is_error = False
        status_code = 200

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def post(url, json=None, **kwargs):
        import json as codec
        requests.append(json)
        if 'typesafe' in url:
            q = json['questions']
            operation = 'DONE' if 'confirmed' in json['state']['page']['text'] else 'CLICK'
            selected_label = 'request'
            if with_choice and operation != 'DONE':
                elements = json['state']['elements']
                if not any(e.get('checked') == 'true' for e in elements):
                    goal = q['operation']['instructions']['goal']
                    operation = 'CLICK' if '"ride_option"' in goal else 'ASK_USER'
                    selected_label = 'Compact —'
            answers = {'operation': choice(q['operation']['criteria'], operation)}
            if operation == 'CLICK':
                criteria = q['click_target']['criteria']
                index = next(i for i, row in criteria.items() if selected_label in row['element'])
                answers['click_target'] = choice(criteria, index)
            return Response({'model': 'scripted', 'answers': answers})
        result = text(json['messages'][0]['content'], codec.loads(json['messages'][1]['content']))
        return Response({'choices': [{'message': {'content': codec.dumps(result)}}]})

    monkeypatch.setenv('TYPESAFE_API_KEY', 'test')
    monkeypatch.setenv('OPENAI_API_KEY', 'test')
    monkeypatch.setattr(model.CLIENT, 'post', post)
    return requests


@pytest.mark.parametrize('family', ['shop', 'ride', 'form', 'account'])
@pytest.mark.parametrize('variant', ['base', 'reordered', 'renamed', 'countdown'])
def test_confirmed_fixture_flow_uses_one_approval_and_one_dispatch(chrome, site, tmp_path, monkeypatch,
                                                                family, variant):
    requests = install_models(monkeypatch, family)
    asked = []
    def answer(question):
        asked.append(question)
        return {'option': 'yes'}
    Agent = fixture_agent_class()
    with Agent(site['a']+f'/task-flow.html?family={family}&variant={variant}',
               'Confirm '+ITEMS[family], asker=answer, profile=Profile(tmp_path/'p.json')) as agent:
        for _ in agent.run():
            assert len(agent.state['decisions']) < 8
        log = agent.browser.evaluate('window.log')
    assert agent.state['status'] == 'done', (agent.state['messages'], agent.task.executions)
    assert len(log) == 1 and log[0]['amount'] == 95
    assert [q['kind'] for q in asked] == ['confirm']
    assert '95.00' in asked[0]['text']
    assert ('Handling charge' if variant == 'renamed' else 'Service fee') in asked[0]['text']
    executions = list(agent.task.executions.values())
    assert len(executions) == 1 and executions[0]['outcome'] == 'success'
    assert agent.task.approval['consumed']
    assert executions[0]['execution_id'] in {d['id'] for d in agent.state['decisions']}
    assert agent.state['messages'][-1]['kind'] == 'result'
    assert any(request.get('messages', [{}])[0].get('content') == questions.COORDINATE for request in requests)
    assert not agent.state['errors']


def test_changed_fee_with_old_amount_still_visible_needs_new_approval(chrome, site, tmp_path, monkeypatch):
    install_models(monkeypatch, 'shop')
    asked = []
    def answer(question):
        asked.append(question)
        if len(asked) == 1:
            agent.browser.evaluate('window.changeFee()')
        return {'option': 'yes'}
    with fixture_agent_class()(site['a']+'/task-flow.html', 'Confirm Tea × 1', asker=answer,
                               profile=Profile(tmp_path/'p.json')) as agent:
        for _ in agent.run():
            assert len(agent.state['decisions']) < 10
        log = agent.browser.evaluate('window.log')
    assert agent.state['status'] == 'done', (agent.state['messages'], agent.task.executions)
    assert len(asked) == 2 and '95.00' in asked[0]['text'] and '111.00' in asked[1]['text']
    assert len(log) == 1 and log[0]['amount'] == 111
    assert len(agent.task.executions) == 1


def test_pending_driver_never_retries_or_reports_confirmed(chrome, site, tmp_path, monkeypatch):
    install_models(monkeypatch, 'ride', pending=True)
    with fixture_agent_class()(site['a']+'/task-flow.html?family=ride&pending', 'Confirm Compact ride',
             asker=lambda _: {'option': 'yes'}, profile=Profile(tmp_path/'p.json')) as agent:
        import time
        from types import SimpleNamespace

        from assistant import agent as loop
        ticks = [time.monotonic()]
        monkeypatch.setattr(loop, 'time', SimpleNamespace(monotonic=lambda: ticks[0],
            time=time.time, strftime=time.strftime, perf_counter=time.perf_counter,
            sleep=lambda _: ticks.__setitem__(0, ticks[0]+301)))
        for _ in agent.run():
            assert len(agent.state['decisions']) < 5
        log = agent.browser.evaluate('window.log')
    assert len(log) == 1
    assert agent.state['status'] == 'blocked'
    assert next(iter(agent.task.executions.values()))['outcome'] == 'unknown'
    assert not any(m.get('kind') == 'result' for m in agent.state['messages'])
    assert agent.task.approval['consumed']


def test_scripted_text_rejects_unexpected_calls_and_sanitizes():
    from reliability_fakes import ScriptedText

    from assistant.profile import SECRETS, Secrets
    secrets = Secrets([])
    secrets.add('482913', '[secret:test]')
    token = SECRETS.set(secrets)
    try:
        script = ScriptedText([{'ok': True}])
        assert script('job', {'value': '482913'})[0] == {'ok': True}
        assert '482913' not in json.dumps(script.requests)
        with pytest.raises(AssertionError, match='Unexpected additional'):
            script('job', {})
    finally:
        SECRETS.reset(token)


def test_cancel_at_confirmation_dispatches_nothing(chrome, site, tmp_path, monkeypatch):
    install_models(monkeypatch, 'account')
    asked = []
    def cancel(question):
        asked.append(question)
        return {'cancel': True}
    with fixture_agent_class()(site['a']+'/task-flow.html?family=account', 'Cancel subscription',
            asker=cancel, profile=Profile(tmp_path/'p.json')) as agent:
        list(agent.run())
        assert agent.browser.evaluate('window.log') == []
    assert agent.state['status'] == 'cancelled'
    assert not agent.task.executions and len(asked) == 1


def test_disconnect_after_accepted_request_never_retries(chrome, site, tmp_path, monkeypatch):
    install_models(monkeypatch, 'shop')
    with fixture_agent_class(disconnect_after_receipt=True)(site['a']+'/task-flow.html', 'Confirm Tea × 1',
            asker=lambda _: {'option': 'yes'}, profile=Profile(tmp_path/'p.json')) as agent:
        list(agent.run())
        assert len(agent.browser.evaluate('window.log')) == 1
    assert agent.state['status'] == 'blocked'
    assert agent.task.approval['consumed']
    assert len(agent.task.executions) == 1
    assert next(iter(agent.task.executions.values()))['status'] == 'unknown'
    assert all(m.get('kind') != 'result' for m in agent.state['messages'])


@pytest.mark.parametrize('require_artifact', [False, True])
def test_read_only_lookup_and_missing_download_use_real_completion_gate(chrome, site, tmp_path, monkeypatch,
                                                                       require_artifact):
    from fakes import decision
    from reliability_fakes import ScriptedText

    from assistant import agent as loop
    with loop.Agent(site['b']+'/next.html', 'Read the second page'+(' and deliver a PDF' if require_artifact else ''),
                    profile=Profile(tmp_path/'p.json')) as agent:
        ids = [eid for eid, row in agent.task.evidence.items() if 'Second page.' in row['quote']]
        requirement = dict(id='read', description='Second page', kind='artifact' if require_artifact else 'page',
                           material_keys=['document'], evidence_ids=[], execution_id=None, artifact_id=None)
        if require_artifact:
            requirement.update(transfer_direction='download', delivery_required=True)
        script = ScriptedText([
            assessment(facts=[dict(key='document', value='Second page.', source='page', scope='task', revision=0,
                                   depends_on=[], evidence=ids, valid=True)], completion=[requirement]),
            assessment(),
            {'bindings': [] if require_artifact else [dict(requirement_id='read', evidence_ids=ids,
                                                          execution_id=None, artifact_id=None)]}])
        agent.coordinator_client = script
        monkeypatch.setattr(loop.jobs, 'text_json', script)
        monkeypatch.setattr(loop, 'choose', lambda *_: decision('DONE', 'DONE'))
        list(agent.run())
    assert not script.responses and len(script.requests) == 3
    assert agent.state['status'] == ('blocked' if require_artifact else 'done')
    assert not agent.task.executions
    assert bool(agent.state['messages'][-1]['kind'] == 'result') is not require_artifact


def test_ride_choice_becomes_fact_before_fresh_jev_click(chrome, site, tmp_path, monkeypatch):
    install_models(monkeypatch, 'ride', with_choice=True)
    asked = []
    def answer(question):
        asked.append(question)
        if question['kind'] == 'choice':
            assert '4 seats' in str(question['options']) and '8 minutes' in str(question['options'])
            assert not agent.task.executions
            return {'option': 'compact'}
        return {'option': 'yes'}
    with fixture_agent_class()(site['a']+'/task-flow.html?family=ride&choice', 'Book a ride',
                               asker=answer, profile=Profile(tmp_path/'p.json')) as agent:
        list(agent.run())
        assert len(agent.browser.evaluate('window.log')) == 1
    assert agent.state['status'] == 'done', agent.state['messages']
    assert [q['kind'] for q in asked] == ['choice', 'confirm']
    assert agent.task.facts['ride_option']['source'] == 'user'
    assert [h['operation'] for h in agent.state['history']] == ['CLICK', 'CLICK']
    assert all(eid in {d['id'] for d in agent.state['decisions']} for eid in agent.task.executions)


def test_model_success_cannot_turn_pending_browser_receipt_into_confirmation(chrome, site, tmp_path, monkeypatch):
    # Deliberately wrong model answer: real fixture is still finding a driver.
    install_models(monkeypatch, 'ride', pending=False)
    with fixture_agent_class()(site['a']+'/task-flow.html?family=ride&pending', 'Confirm Compact ride',
            asker=lambda _: {'option': 'yes'}, profile=Profile(tmp_path/'p.json')) as agent:
        list(agent.run())
        assert len(agent.browser.evaluate('window.log')) == 1
    assert agent.state['status'] == 'blocked'
    assert next(iter(agent.task.executions.values()))['outcome'] == 'unknown'
    assert not any(m.get('kind') == 'result' for m in agent.state['messages'])
