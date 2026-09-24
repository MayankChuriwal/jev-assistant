"""Production PDF receipt boundary in throwaway Chrome; model decisions are scripted."""
import json
from pathlib import Path

import pytest
from reliability_fakes import assessment, current_evidence
from test_shop import choice

from assistant import commit, model, questions
from assistant.agent import Agent
from assistant.browser import Browser
from assistant.channel_web import WebChannel
from assistant.core import Core, Task
from assistant.profile import Profile

pytestmark = pytest.mark.browser


@pytest.mark.parametrize('variant', ['success', 'wrong-material', 'stale', 'wrong-bytes',
                                     'invoice', 'compound-goal', 'changed-page', 'delivery-failure'])
def test_current_page_pdf_requires_owned_bytes_material_and_delivery(chrome, site, tmp_path, monkeypatch, variant):
    calls = []

    class Response:
        status_code, is_error = 200, False

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def post(url, json=None, **kwargs):
        calls.append(json)
        if 'typesafe' in url:
            criteria = json['questions']['operation']['criteria']
            saved = any(a['operation'] == 'SAVE_PDF' for a in json['state']['recent_actions'])
            operation = 'DONE' if saved else 'SAVE_PDF'
            return Response({'model': 'scripted', 'answers': {'operation': choice(criteria, operation)}})
        payload = __import__('json').loads(json['messages'][1]['content'])
        system = json['messages'][0]['content']
        if system == questions.COMPLETION_PROOF:
            requirement = payload['task']['completion'][0]
            binding = {key: requirement[key] for key in ('evidence_ids', 'execution_id', 'artifact_id')}
            result = {'bindings': [dict(requirement_id='pdf', **binding)]}
            return Response({'choices': [{'message': {'content': __import__('json').dumps(result)}}]})
        assert system == questions.COORDINATE, system
        result = assessment()
        if payload['event'] == 'start':
            result['completion'] = [dict(id='pdf', description='Export the current page as PDF', kind='artifact',
                material_keys=['document'], evidence_ids=[], execution_id=None, artifact_id=None,
                transfer_direction='download', delivery_required=True)]
            result['facts'] = [dict(key='document', value='Second page.', source='page', scope='task', revision=0,
                                   depends_on=[], evidence=current_evidence(payload['task']), valid=True)]
        return Response({'choices': [{'message': {'content': __import__('json').dumps(result)}}]})

    monkeypatch.setenv('TYPESAFE_API_KEY', 'test')
    monkeypatch.setenv('OPENAI_API_KEY', 'test')
    monkeypatch.setattr(model.CLIENT, 'post', post)
    goal = 'Download my latest invoice as PDF' if variant == 'invoice' else 'Save this page as PDF'
    if variant == 'compound-goal':
        goal = 'Save this page as PDF and get my latest invoice'
    task = Task('export-task', goal)
    profile = Profile(tmp_path/'profile.json')
    channel = WebChannel(tmp_path/'media')
    core = Core(channel, profile=profile)
    browser = Browser(site['a']+'/next.html', downloads=tmp_path/'exports')
    with Agent(site['a']+'/next.html', goal, task=task.flow, profile=profile, browser=browser) as agent:
        task.agent = agent
        agent.command('tick')
        assert len(agent.task.executions) == 1
        execution = next(iter(agent.task.executions.values()))
        assert execution['status'] == 'executed'
        assert execution['operation'] == 'SAVE_PDF'
        assert agent.journal.records()[-1]['phase'] == 'executed'
        path = Path(agent.state['files'][0])
        assert path.read_bytes().startswith(b'%PDF-')
        assert not commit.completion_report(task.flow)['verified']
        if variant == 'wrong-material':
            task.flow.facts['document']['value'] = 'Unrelated invoice'
        elif variant == 'stale':
            agent.amend('Use another page', {'document'})
        elif variant == 'changed-page':
            agent.browser.navigate(site['a']+'/events.html')
            agent.observe()
        elif variant == 'wrong-bytes':
            path.write_bytes(b'%PDF- substituted file')
        elif variant == 'delivery-failure':
            def fail(*args, **kwargs):
                raise OSError('Delivery failed')
            monkeypatch.setattr(channel, 'send', fail)
        if variant == 'delivery-failure':
            with pytest.raises(OSError, match='Delivery failed'):
                core.deliver_files(task)
            assert not task.flow.delivered
        else:
            core.deliver_files(task)
            media_id = task.file_ids[str(path)]
            assert channel.media(media_id)[0].read_bytes() == path.read_bytes()
            assert task.flow.delivered[media_id]['message_id']
        assert commit.completion_report(task.flow)['verified'] is (variant == 'success')
        assert len(agent.task.executions) == 1  # no browser retry for a failed delivery/binding
        if variant == 'success':
            requirement = task.flow.completion[0]
            assert requirement['artifact_id'] == task.file_ids[str(path)]
            assert requirement['execution_id'] == execution['execution_id']
            agent.command('tick')
            assert agent.state['status'] == 'done'
            assert len(agent.task.executions) == 1
        else:
            assert not commit.artifact_ids(task.flow)
    assert 'Unrelated invoice' not in json.dumps(calls)
