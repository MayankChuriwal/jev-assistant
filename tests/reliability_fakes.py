"""Small state fixtures shared by the reliability tests."""

from fakes import fake_page

from assistant.state import TaskState, cite, observe_state


def observed(text="Checkout", goal="Complete the requested task", url="https://shop.test/"):
    s = TaskState("t1", goal)
    page = fake_page(text=text, url=url)
    oid = observe_state(s, page)
    return s, page, oid


def evidence_for(s, oid, quote):
    return (cite(s, oid, quote),)


class ScriptedText:
    """Exact JSON replies; retain only the same sanitized request the model sees."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, system, payload, **kwargs):
        from assistant.coordinator import _redact
        self.requests.append(_redact((system, payload, kwargs)))
        if not self.responses:
            raise AssertionError("Unexpected additional model call")
        return self.responses.pop(0), {"model": "scripted", "latency_ms": 0, "usage": {}}


def assessment(**changes):
    return {**dict(objective="Complete the user's request", facts=[], requirements=[], completion=[],
                   auth=None, progress=None, proposal=None, outcome=None, notice=None, recovery=None), **changes}


def current_evidence(task):
    latest = task['observation_ids'][-1]
    return [eid for eid, row in task['evidence'].items() if row['observation_id'] == latest]


def fixture_agent_class(channel=None, *, disconnect_after_receipt=False):
    """Test-site adapter only. Never infer terminal status from model text.

    The fixture creates a new receipt ONLY in its accepted mutation handler. We
    bind its preissued request ID to the actual dispatch, then expose its result
    as browser-owned evidence only after execution has been journaled.
    This deliberately does not claim a production adapter.
    """
    from assistant import commit
    from assistant.agent import Agent
    from assistant.browser import Browser

    if channel is not None:
        register = channel.register
        media_ids = {}
        def stable_register(path, filename=None):
            key = str(path)
            if key not in media_ids:
                media_ids[key] = register(path, filename)
            return media_ids[key]
        channel.register = stable_register

    class ReceiptBrowser(Browser):
        receipt = None
        pdf = None
        receipt_correlation = None

        def act(self, operation, action, page, **inputs):
            node = action.get('node')
            emits_receipt = (operation == 'CLICK' and node is not None and self.evaluate(
                f"window.__jevFast.nodes.get({int(node)}).hasAttribute('data-fixture-receipt')"))
            correlation = self.evaluate('window.__taskCorrelation') if emits_receipt else None
            result = super().act(operation, action, page, **inputs)
            if operation == 'SAVE_PDF' and channel is not None:
                import hashlib
                from pathlib import Path
                path = Path(result['file'])
                data = path.read_bytes()
                assert data.startswith(b'%PDF')
                digest = hashlib.sha256(data).hexdigest()
                self.pdf = dict(path=path, digest=digest, artifact_id=channel.register(path),
                                text=digest+' '+page['text'])
                result['correlation'] = digest
            if emits_receipt:
                # This is a pre-dispatch site-owned request ID, not a terminal result.
                self.receipt_correlation = correlation
                result['correlation'] = correlation
                if disconnect_after_receipt:
                    from assistant.browser import ExecutionUncertain
                    raise ExecutionUncertain('Fixture connection lost after accepted request')
            return result

        def observe(self, **kwargs):
            page = super().observe(**kwargs)
            if self.pdf:
                import hashlib
                assert hashlib.sha256(self.pdf['path'].read_bytes()).hexdigest() == self.pdf['digest']
                page['events'] = [self.pdf['text']]
            if self.receipt_correlation:
                assert self.journal.records()[-1]['phase'] in {'executed', 'unknown', 'outcome'}
                # The executed/unknown journal record now exists before this post-dispatch read.
                # The result must still be independently present in this browser.
                actual = self.evaluate('window.__taskReceipt || null')
                if actual and actual['correlation'] == self.receipt_correlation:
                    assert set(actual) == {'correlation', 'status', 'description'}
                    assert actual['status'] in {'success', 'failed', 'processing', 'pending_user'}
                    self.receipt = actual
                    page['events'] = [self.receipt['correlation'] + ' ' + self.receipt['description']]
            return page

    class ReceiptAgent(Agent):
        def __init__(self, url, goal, **kwargs):
            kwargs['browser'] = ReceiptBrowser(url, reserve_origin=kwargs.get('reserve_origin'))
            super().__init__(url, goal, **kwargs)
            self.browser.journal = self.journal

        def retain_observation(self, page):
            oid = super().retain_observation(page)
            pdf = getattr(self.browser, 'pdf', None)
            if pdf:
                for execution in self.task.executions.values():
                    if execution.get('result', {}).get('correlation') == pdf['digest']:
                        ids = [eid for eid, row in self.task.evidence.items()
                               if row['observation_id'] == oid and row['kind'] == 'browser-event']
                        for requirement in self.task.completion:
                            if requirement['kind'] == 'artifact':
                                commit.record_artifact(self.task, pdf['artifact_id'], requirement_id=requirement['id'],
                                    evidence_ids=ids, direction='download', execution_id=execution['execution_id'])
            receipt = getattr(self.browser, 'receipt', None)
            if receipt and receipt['status'] in {'success', 'failed'}:
                for execution in self.task.executions.values():
                    if (execution.get('status') == 'executed'
                            and execution.get('result', {}).get('correlation') == receipt['correlation']):
                        ids = [eid for eid, row in self.task.evidence.items()
                               if row['observation_id'] == oid and row['kind'] == 'browser-event']
                        commit.record_terminal_receipt(self.task, execution['execution_id'],
                                                       status=receipt['status'], evidence_ids=ids)
            return oid

    return ReceiptAgent


def terminal_bindings(payload):
    task = payload['task']
    ids = [eid for eid, row in task['evidence'].items()
           if row['kind'] == 'browser-event' and row['observation_id'] == task['observation_ids'][-1]]
    execution = next(eid for eid, row in payload['executions'].items() if row['commitment'])
    return {'bindings': [dict(requirement_id='result', evidence_ids=ids,
                              execution_id=execution, artifact_id=None)]}


def shop_coordinate(payload):
    task = payload['task']
    ids = current_evidence(task)
    page = payload['untrusted_page']
    text = page['text']
    out = assessment()
    if payload['event'] == 'start':
        out['completion'] = [dict(id='result', description='Order Diet Coke', kind='action',
            material_keys=['product'], evidence_ids=[], execution_id=None, artifact_id=None)]
    if 'Diet Coke' in text:
        out['facts'] = [dict(key='product', value='Diet Coke', source='page', scope='task', revision=task['revision'],
                            depends_on=[], evidence=ids, valid=True)]
    flows = task['progress'].get('auth_flows', {})
    login = flows.get('login', {})
    challenges = task['challenges']
    challenge = next((row for row in reversed(list(challenges.values())) if row.get('flow_id') == 'login'), None)
    submitted = next((row for row in reversed(list(task.get('executions', {}).values()))
                      if row.get('submission') and challenge and row.get('challenge_id') == challenge['id']), None)
    if 'Verify' in text:
        status = 'required'
        cid = execution = None
        if 'Invalid OTP' in text and challenge and submitted and challenge.get('status') != 'rejected':
            status, cid = 'rejected', challenge['id']
            execution = submitted['execution_id']
        out['auth'] = dict(flow_id='login', challenge_id=cid, status=status, origin=page['origin'],
            purpose='login', field_role='otp', evidence_ids=ids, execution_id=execution,
            expires_at=None, allowed_origins=[page['origin']])
    elif 'Checkout' in text and login.get('status') != 'authenticated':
        # Browser navigation to authenticated checkout is evidence; the prior challenge supplies its dispatch.
        out['auth'] = dict(flow_id='login', challenge_id=None, status='authenticated', origin=page['origin'],
            purpose='login', field_role='otp', evidence_ids=ids, execution_id=None,
            expires_at=None, allowed_origins=[page['origin']])
    elif 'Checkout' in text:
        out['auth'] = dict(flow_id='payment', challenge_id=None, status='required', origin=page['origin'],
            purpose='payment', field_role='cvv', evidence_ids=ids, execution_id=None,
            expires_at=None, allowed_origins=[page['origin']])
    if 'Checkout' in text and 'delivery_address' not in task['facts']:
        out['requirements'] = [dict(id='address', kind='choice', purpose='delivery', keys=['delivery_address'],
            scope='task', depends_on=[], reason='Deliver to which address?', options=[
                dict(id='o1', label='Home', description='12 MG Road', evidence_ids=ids),
                dict(id='o2', label='Office', description='4 Residency Road', evidence_ids=ids)])]
    if payload['event'] == 'confirmation':
        out['proposal'] = dict(task_id=task['task_id'], revision=task['revision'], action='Pay ₹79',
            service='Checkout', account='', destination='Home — 12 MG Road', items=['Diet Coke 300 ml × 1'],
            option='', timing='Arriving in 12 minutes', payment='HDFC Visa ••1234', currency='INR',
            total_minor=7900, total_kind='exact', later_terms=[], evidence_ids=ids, lines=[
                dict(id='item', label='Diet Coke 300 ml × 1', amount_minor=4000, currency='INR', role='item',
                     included_in=None, evidence_ids=ids),
                dict(id='delivery', label='Delivery', amount_minor=3900, currency='INR', role='fee',
                     included_in=None, evidence_ids=ids)])
    if payload['event'] == 'outcome' or ('Order placed' in text and any(
            row.get('commitment') and row.get('outcome') != 'success'
            for row in task.get('executions', {}).values())):
        events = [eid for eid in ids if task['evidence'][eid]['kind'] == 'browser-event']
        out['outcome'] = dict(status='success' if 'Order placed' in text else 'pending_user',
                              evidence_ids=events, artifact_id=None)
    return out
