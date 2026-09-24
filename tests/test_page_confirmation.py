"""A generic confirmation page may prove one dispatch only with fresh material evidence."""
import json

import pytest
from reliability_fakes import assessment, current_evidence
from test_shop import choice

from assistant import agent as loop
from assistant import model, questions
from assistant.profile import Profile

pytestmark = pytest.mark.browser


@pytest.mark.parametrize('variant,expected', [('success', 'done'), ('delayed', 'done'), ('old', 'blocked'),
                                              ('processing', 'blocked'), ('wrong-amount', 'blocked'),
                                              ('old-total', 'blocked'), ('wrong-item', 'blocked'),
                                              ('unrelated', 'blocked')])
def test_page_confirmation_is_bound_to_current_order(chrome, site, tmp_path, monkeypatch, variant, expected):
    sent = []

    def text(system, payload):
        if system.startswith('Classify the external effect'):
            return {'effect': 'commitment'}
        if system == questions.COORDINATE:
            task = payload['task']
            ids = current_evidence(task)
            result = assessment()
            if payload['event'] == 'start':
                result['completion'] = [dict(id='order', description='Order Green tea × 1',
                    kind='action', material_keys=['ordered_item'], evidence_ids=[],
                    execution_id=None, artifact_id=None)]
            if 'Final review' in payload['untrusted_page']['text']:
                result['facts'] = [dict(key='ordered_item', value='Green tea × 1', source='page',
                    scope='task', revision=task['revision'], depends_on=[], evidence=ids, valid=True)]
            if payload['event'] == 'confirmation':
                result['proposal'] = dict(task_id=task['task_id'], revision=task['revision'],
                    action='Place order', service='Market A', account='', destination='Home — 12 MG Road',
                    items=['Green tea × 1'], option='', timing='Tomorrow', payment='Cash',
                    currency='INR', total_minor=13400, total_kind='exact', later_terms=[],
                    lines=[dict(id='item', label='Green tea × 1', amount_minor=9500, currency='INR',
                                role='item', included_in=None, evidence_ids=ids),
                           dict(id='fee', label='Delivery fee', amount_minor=3900, currency='INR',
                                role='fee', included_in=None, evidence_ids=ids)], evidence_ids=ids)
            if payload['event'] == 'outcome':
                # Deliberately optimistic even for adversarial pages. Code must reject them.
                status = ('processing' if variant == 'delayed' and
                          'Order processing' in payload['untrusted_page']['text'] else 'success')
                result['outcome'] = dict(status=status, evidence_ids=ids, artifact_id=None)
            return result
        if system == questions.REVIEW_AMOUNTS:
            return dict(exponent=2, total_text='₹134', total_quote='Total ₹134',
                        amounts={'item': '₹95', 'fee': '₹39'},
                        line_quotes={'item': 'Green tea × 1 ₹95', 'fee': 'Delivery fee ₹39'})
        if system == questions.COMPLETION_PROOF:
            task = payload['task']
            ids = current_evidence(task)
            execution = next(eid for eid, row in payload['executions'].items() if row['commitment'])
            return {'bindings': [dict(requirement_id='order', evidence_ids=ids,
                                      execution_id=execution, artifact_id=None)]}
        raise AssertionError(system[:100])

    class Response:
        status_code = 200
        is_error = False

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def post(url, json=None, **kwargs):
        sent.append(json)
        if 'typesafe' in url:
            q = json['questions']
            op = 'CLICK' if 'Final review' in json['state']['page']['text'] else 'DONE'
            answers = {'operation': choice(q['operation']['criteria'], op)}
            if op == 'CLICK':
                criteria = q['click_target']['criteria']
                target = next(i for i, row in criteria.items() if 'Place order' in row['element'])
                answers['click_target'] = choice(criteria, target)
            return Response({'model': 'scripted', 'answers': answers})
        output = text(json['messages'][0]['content'], __import__('json').loads(json['messages'][1]['content']))
        return Response({'choices': [{'message': {'content': __import__('json').dumps(output)}}]})

    monkeypatch.setenv('TYPESAFE_API_KEY', 'test')
    monkeypatch.setenv('OPENAI_API_KEY', 'test')
    monkeypatch.setattr(model.CLIENT, 'post', post)
    approvals = []
    def answer(question):
        approvals.append(question)
        return {'option': 'yes'}
    with loop.Agent(site['a']+f'/page-confirmation.html?variant={variant}', 'Order Green tea × 1',
                    asker=answer, profile=Profile(tmp_path/'p.json')) as agent:
        agent.outcome_pause = 0.1
        list(agent.run())
        log = agent.browser.evaluate('window.log')
    assert agent.state['status'] == expected, (variant, agent.state['messages'], agent.state['errors'])
    assert log == ['place'] and len(approvals) == 1
    assert len([row for row in agent.task.executions.values() if row.get('commitment')]) == 1
    outcome = next(row.get('outcome') for row in agent.task.executions.values() if row.get('commitment'))
    assert outcome == ('success' if expected == 'done' else 'unknown')
    assert not agent.state['errors']
    assert len([body for body in sent if body.get('messages', [{}])[0].get('content') == questions.COORDINATE
                and json.loads(body['messages'][1]['content'])['event'] == 'outcome']) >= 2
