"""Distinct staged shopping, ride, and provider-comparison browser journeys."""
import json
import re

import pytest
from reliability_fakes import assessment, current_evidence, fixture_agent_class, terminal_bindings
from test_shop import choice

from assistant import model, questions
from assistant.profile import Profile
from assistant.state import public_context

pytestmark = pytest.mark.browser


def stage_of(text):
    for marker, stage in [('Write to reception', 'form-entry'), ('Stream Plan account', 'account-choice'),
                          ('Choose provider', 'provider'), ('Choose delivery address', 'address'),
                          ('Choose pickup', 'address'), ('Choose vehicle', 'option'), ('Choose tea', 'option'),
                          ('Free delivery requires', 'promotion'), ('One-time code for Market A', 'login-a'),
                          ('One-time code for Market B', 'login-b'), ('Card verification', 'payment-auth'),
                          ('Market A quote:', 'quote-a'),
                          ('Market B quote:', 'quote-b'), ('Choose quote:', 'choice'),
                          ('Final review', 'review'), ('Order placed', 'done'),
                          ('Driver confirmed', 'done'), ('Order processing', 'done'),
                          ('Finding a driver', 'done'), ('Message to reception sent', 'done'),
                          ('Stream Plan cancellation confirmed', 'done')]:
        if marker in text:
            return stage
    raise AssertionError(text)


def _fact(goal, key):
    task = json.loads(goal.split('\nStructured task: ', 1)[1].split('\nProfile: ', 1)[0])
    return task['facts'].get(key, {}).get('value')


def install_models(monkeypatch, kind, *, skip=False):
    sent = []

    def text(system, payload):
        if system.startswith('Classify the external effect'):
            label = payload['selected_action']['label']
            return {'effect': 'commitment' if label in {'Place order', 'Request ride',
                                                       'Send message', 'Cancel subscription'} else
                    'authentication' if label.startswith('Verify') or label in {'Complete payment', 'CVV'}
                    else 'preparation'}
        if system == questions.FIELD_VALUE:
            return {'text': 'Please leave the parcel at reception.'}
        if system == questions.COORDINATE:
            task = payload['task']
            page = payload['untrusted_page']
            stage = stage_of(page['text'])
            ids = current_evidence(task)
            result = assessment()
            if payload['event'] == 'start':
                result['completion'] = [dict(id='result', description='Complete requested '+kind,
                    kind='action', material_keys=['requested_item'], evidence_ids=[],
                    execution_id=None, artifact_id=None)]
            keys = {'provider': ('provider_choice', 'provider', [('a', 'Market A' if kind != 'ride' else 'Metro Cars'),
                                                                ('b', 'Market B' if kind != 'ride' else 'City Rides')]),
                    'address': ('address_choice', 'address', [('home', 'Home — 12 MG Road'),
                                                             ('office', 'Office — 4 Residency Road')]),
                    'option': ('item_choice', 'option', [('a', 'Compact — 4 seats, 3 minutes' if kind == 'ride'
                                                     else 'Green tea — 100 g'),
                                                    ('b', 'Large — 6 seats, 8 minutes' if kind == 'ride'
                                                     else 'Black tea — 100 g')]),
                    'promotion': ('pack_choice', 'promotion', [('one', 'Keep one pack'),
                                                             ('three', 'Add two packs')]),
                    'choice': ('quote_choice', 'comparison', [('a', 'Choose Market A'),
                                                           ('b', 'Choose Market B')])}
            if stage in keys:
                key, purpose, options = keys[stage]
                if key not in task['facts']:
                    if stage == 'choice' and 'Market B skipped' in page['text']:
                        options = options[:1]
                    result['requirements'] = [dict(id=key, kind='choice', purpose=purpose, keys=[key],
                        scope='task', depends_on=[], reason='Choose '+purpose, options=[
                            dict(id=oid, label=label, description=label, evidence_ids=ids)
                            for oid, label in options])]
            if stage in {'login-a', 'login-b'}:
                name = 'Market A' if stage == 'login-a' else 'Market B'
                result['auth'] = dict(flow_id=name, challenge_id=None, status='required',
                    origin=page['origin'], purpose='login', field_role='otp', evidence_ids=ids,
                    execution_id=None, expires_at=None, allowed_origins=[page['origin']])
            if stage == 'payment-auth':
                result['auth'] = dict(flow_id='payment', challenge_id=None, status='required',
                    origin=page['origin'], purpose='payment', field_role='cvv', evidence_ids=ids,
                    execution_id=None, expires_at=None, allowed_origins=[page['origin']])
            if stage == 'quote-a':
                result['auth'] = dict(flow_id='Market A', challenge_id=None, status='authenticated',
                    origin=page['origin'], purpose='login', field_role='otp', evidence_ids=ids,
                    execution_id=None, expires_at=None, allowed_origins=[page['origin']])
            if stage == 'quote-b':
                result['auth'] = dict(flow_id='Market B', challenge_id=None, status='authenticated',
                    origin=page['origin'], purpose='login', field_role='otp', evidence_ids=ids,
                    execution_id=None, expires_at=None, allowed_origins=[page['origin']])
            if stage in {'review', 'done'}:
                item = {'ride': 'Compact ride', 'form': 'Message to reception',
                        'account': 'Stream Plan cancellation'}.get(kind, 'Green tea')
                result['facts'] = [dict(key='requested_item', value=item, source='page', scope='task',
                    revision=task['revision'], depends_on=[], evidence=ids, valid=True)]
            if payload['event'] == 'confirmation':
                rows = page['text'].splitlines()
                if kind in {'form', 'account'}:
                    result['proposal'] = dict(task_id=task['task_id'], revision=task['revision'],
                        action='Send message' if kind == 'form' else 'Cancel subscription',
                        service='Reception' if kind == 'form' else 'Stream Plan', account='',
                        destination='', items=['Message to reception',
                            'Please leave the parcel at reception.'] if kind == 'form' else
                            ['Cancel subscription'], option='', timing='', payment='',
                        currency=None, total_minor=None, total_kind='not_applicable',
                        later_terms=[], lines=[], evidence_ids=ids)
                    return result
                service = 'Metro Cars' if kind == 'ride' else ('Market B' if 'Market B' in rows else 'Market A')
                item = next(row.split(' ₹')[0] for row in rows if row.startswith(('Compact ride ₹', 'Green tea ×')))
                item_amount = int(next(row.split('₹')[1] for row in rows if row.startswith(item+' ₹')))
                fee_row = next(row for row in rows if row.startswith(('Delivery fee', 'Handling charge')))
                fee = int(fee_row.split('₹')[1])
                total = int(next(row.split('₹')[1] for row in rows if row.startswith('Total ₹')))
                result['proposal'] = dict(task_id=task['task_id'], revision=task['revision'],
                    action='Request ride' if kind == 'ride' else 'Place order', service=service,
                    account='', destination='Home — 12 MG Road', items=[item],
                    option='Compact — 4 seats, 3 minutes' if kind == 'ride' else 'Green tea — 100 g',
                    timing='Tomorrow 09:00' if kind == 'ride' else 'Tomorrow',
                    payment='HDFC Visa ••1234' if 'HDFC Visa ••1234' in rows else 'Cash',
                    currency='INR', total_minor=total*100, total_kind='exact', later_terms=[],
                    lines=[dict(id='item', label=item, amount_minor=item_amount*100, currency='INR',
                        role='item', included_in=None, evidence_ids=ids),
                           dict(id='fee', label=fee_row.split(' ₹')[0], amount_minor=fee*100,
                                currency='INR', role='fee', included_in=None, evidence_ids=ids)],
                    evidence_ids=ids)
            pending_commit = any(row.get('commitment') and row.get('outcome') not in {'success', 'failed'}
                                 for row in task.get('executions', {}).values())
            if payload['event'] == 'outcome' or stage == 'done' and pending_commit:
                events = [eid for eid in ids if task['evidence'][eid]['kind'] == 'browser-event']
                success = stage == 'done' and any(token in page['text'] for token in (
                    'Order placed', 'Driver confirmed', 'Message to reception sent',
                    'Stream Plan cancellation confirmed'))
                result['outcome'] = dict(status='success' if success else
                    'pending_user' if stage == 'payment-auth' else 'processing',
                    evidence_ids=events, artifact_id=None)
            return result
        if system == questions.REVIEW_AMOUNTS:
            proposal = payload['proposal']
            if proposal['total_kind'] == 'not_applicable':
                return {}
            amounts = {row['id']: '₹'+str(row['amount_minor']//100) for row in proposal['lines']}
            return dict(exponent=2, total_text='₹'+str(proposal['total_minor']//100),
                total_quote='Total ₹'+str(proposal['total_minor']//100), amounts=amounts,
                line_quotes={row['id']: row['label']+' '+amounts[row['id']] for row in proposal['lines']})
        if system == questions.COMPLETION_PROOF:
            return terminal_bindings(payload)
        raise AssertionError(system[:90])

    class Response:
        status_code = 200
        is_error = False

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def post(url, json=None, **kwargs):
        sent.append(json)
        if 'typesafe' not in url:
            system = json['messages'][0]['content']
            result = text(system, __import__('json').loads(json['messages'][1]['content']))
            return Response({'choices': [{'message': {'content': __import__('json').dumps(result)}}]})
        q = json['questions']
        page = json['state']['page']['text']
        stage = stage_of(page)
        goal = q['operation']['instructions']['goal']
        key = {'provider': 'provider_choice', 'address': 'address_choice', 'option': 'item_choice',
               'promotion': 'pack_choice', 'choice': 'quote_choice'}.get(stage)
        selected = _fact(goal, key) if key else None
        if key and not selected:
            operation, label = 'ASK_USER', None
        elif stage == 'provider':
            operation, label = 'CLICK', 'Metro Cars' if kind == 'ride' else 'Market A'
        elif stage == 'address':
            operation, label = 'CLICK', 'Home — 12 MG Road'
        elif stage == 'option':
            operation, label = 'CLICK', 'Compact —' if kind == 'ride' else 'Green tea —'
        elif stage == 'promotion':
            operation, label = 'CLICK', 'Keep one pack'
        elif stage == 'form-entry':
            recent = json['state']['recent_actions']
            operation = 'CLICK' if recent and recent[-1]['operation'] == 'TYPE_TEXT' else 'TYPE_TEXT'
            label = 'Review message' if operation == 'CLICK' else 'Message'
        elif stage == 'account-choice':
            operation, label = 'CLICK', 'Review cancellation'
        elif stage in {'login-a', 'login-b'}:
            recent = json['state']['recent_actions']
            operation = 'CLICK' if recent and recent[-1]['operation'] == 'TYPE_TEXT' else 'TYPE_TEXT'
            label = 'Verify' if operation == 'CLICK' else 'one-time code'
        elif stage == 'payment-auth':
            recent = json['state']['recent_actions']
            operation = 'CLICK' if recent and recent[-1]['operation'] == 'TYPE_TEXT' else 'TYPE_TEXT'
            label = 'Complete payment' if operation == 'CLICK' else 'CVV'
        elif stage == 'quote-a':
            operation, label = 'CLICK', 'Skip Market B' if skip else 'Compare Market B'
        elif stage == 'quote-b':
            operation, label = 'CLICK', 'Review both quotes'
        elif stage == 'choice':
            operation, label = 'CLICK', 'Choose Market A' if skip else 'Choose Market B'
        elif stage == 'review':
            operation, label = 'CLICK', {'ride': 'Request ride', 'form': 'Send message',
                                         'account': 'Cancel subscription'}.get(kind, 'Place order')
        else:
            operation, label = 'DONE', None
        answers = {'operation': choice(q['operation']['criteria'], operation)}
        if label:
            head = 'type_text_target' if operation == 'TYPE_TEXT' else 'click_target'
            criteria = q[head]['criteria']
            index = next(i for i, row in criteria.items() if label in row['element'])
            answers[head] = choice(criteria, index)
        return Response({'model': 'scripted', 'answers': answers})

    monkeypatch.setenv('TYPESAFE_API_KEY', 'test')
    monkeypatch.setenv('OPENAI_API_KEY', 'test')
    monkeypatch.setattr(model.CLIENT, 'post', post)
    return sent


@pytest.mark.parametrize('kind,variant', [('shop', 'base'), ('shop', 'reordered'),
                                          ('shop', 'renamed'), ('shop', 'delayed'), ('ride', 'base')])
def test_staged_shopping_and_ride_journey(chrome, site, tmp_path, monkeypatch, kind, variant):
    sent = install_models(monkeypatch, kind)
    asked = []
    def answer(question):
        asked.append(question)
        return {'option': question['options'][0]['id'] if question['kind'] == 'choice' else 'yes'}
    with fixture_agent_class()(site['a']+f'/journey.html?kind={kind}&variant={variant}',
            'Book a compact ride' if kind == 'ride' else 'Order one Green tea', asker=answer,
            profile=Profile(tmp_path/'p.json')) as agent:
        agent.outcome_pause = 0.1
        for _ in agent.run():
            assert len(agent.state['decisions']) < 30, agent.state['errors']
        log = agent.browser.evaluate('window.log')
    assert agent.state['status'] == 'done', (agent.state['messages'], agent.state['errors'])
    assert len([entry for entry in log if 'commitment' in entry]) == 1
    assert [q['kind'] for q in asked].count('confirm') == 1
    assert [q['kind'] for q in asked].count('choice') == (3 if kind == 'ride' else 4)
    assert agent.task.approval['consumed']
    assert len([row for row in agent.task.executions.values() if row['commitment']]) == 1
    assert not re.search(r'(?<!\d)482913(?!\d)', str(sent))


def test_staged_cart_fee_change_requires_new_review(chrome, site, tmp_path, monkeypatch):
    install_models(monkeypatch, 'shop')
    confirms = []
    def answer(question):
        if question['kind'] == 'choice':
            return {'option': question['options'][0]['id']}
        confirms.append(question['text'])
        if len(confirms) == 1:
            agent.browser.evaluate('window.changeFee()')
        return {'option': 'yes'}
    with fixture_agent_class()(site['a']+'/journey.html?kind=shop', 'Order one Green tea',
            asker=answer, profile=Profile(tmp_path/'p.json')) as agent:
        list(agent.run())
        log = agent.browser.evaluate('window.log')
    assert agent.state['status'] == 'done', agent.state['messages'][-5:]
    assert len(confirms) == 2 and '134.00' in confirms[0] and '150.00' in confirms[1]
    assert len([entry for entry in log if 'commitment' in entry]) == 1
    assert log[-1]['total'] == 150
    assert len([row for row in agent.task.executions.values() if row['commitment']]) == 1


def test_staged_shop_cvv_belongs_to_approved_purchase(chrome, site, tmp_path, monkeypatch):
    sent = install_models(monkeypatch, 'shop')
    asked = []
    def answer(question):
        asked.append(question)
        if question['kind'] == 'choice':
            return {'option': question['options'][0]['id']}
        return {'text': '123'} if question['kind'] == 'secret' else {'option': 'yes'}
    with fixture_agent_class()(site['a']+'/journey.html?kind=shop&cvv', 'Order one Green tea',
            asker=answer, profile=Profile(tmp_path/'p.json')) as agent:
        list(agent.run())
        log = agent.browser.evaluate('window.log')
    assert agent.state['status'] == 'done', (agent.state['messages'][-5:], agent.task.executions)
    assert [q['kind'] for q in asked][-2:] == ['confirm', 'secret']
    assert len([entry for entry in log if 'commitment' in entry]) == 1
    assert sum(bool(entry.get('payment_verified')) for entry in log) == 1
    assert agent.task.approval['consumed']
    assert not re.search(r'(?<![\da-f.])123(?![\da-f.])', str(sent))


@pytest.mark.parametrize('kind', ['form', 'account'])
def test_distinct_message_and_account_change_preparation(chrome, site, tmp_path, monkeypatch, kind):
    install_models(monkeypatch, kind)
    asked = []
    def answer(question):
        asked.append(question)
        return {'option': 'yes'}
    with fixture_agent_class()(site['a']+f'/journey.html?kind={kind}',
            'Send a message to reception' if kind == 'form' else 'Cancel Stream Plan',
            asker=answer, profile=Profile(tmp_path/'p.json')) as agent:
        list(agent.run())
        log = agent.browser.evaluate('window.log')
    assert agent.state['status'] == 'done', agent.state['messages']
    assert [q['kind'] for q in asked] == ['confirm']
    assert len(log) == 1 and log[0]['commitment'] == kind
    assert log[0]['message'] == ('Please leave the parcel at reception.' if kind == 'form' else '')
    assert len([row for row in agent.task.executions.values() if row['commitment']]) == 1


@pytest.mark.parametrize('skip', [False, True])
def test_sequential_provider_comparison_and_skip(chrome, site, tmp_path, monkeypatch, skip):
    sent = install_models(monkeypatch, 'compare', skip=skip)
    asked = []
    codes = iter(['482913', '615204'])
    def answer(question):
        asked.append(question)
        if question['kind'] == 'secret':
            return {'text': next(codes)}
        return {'option': 'a' if skip else 'b'} if question['kind'] == 'choice' else {'option': 'yes'}
    with fixture_agent_class()(site['a']+'/journey.html?kind=compare',
            'Compare Market A and Market B, then order Green tea', asker=answer,
            profile=Profile(tmp_path/'p.json')) as agent:
        for _ in agent.run():
            assert len(agent.state['decisions']) < 35, agent.state['errors']
        log = agent.browser.evaluate('window.log')
    assert agent.state['status'] == 'done', (agent.state['messages'], agent.state['errors'])
    assert [entry['authentication'] for entry in log if 'authentication' in entry] == (
        ['Market A'] if skip else ['Market A', 'Market B'])
    assert len([entry for entry in log if 'commitment' in entry]) == 1
    assert [q['kind'] for q in asked].count('secret') == (1 if skip else 2)
    assert [q['kind'] for q in asked].count('confirm') == 1
    for code in ('482913', '615204'):
        assert code not in str(sent) and code not in str(public_context(agent.task))
