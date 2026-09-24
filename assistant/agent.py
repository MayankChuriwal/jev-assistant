"""The task loop: observe, one Jev decision, one executed operation, log, observe again. The agent asks its user
only for what they alone can give, and no model ever sees a secret."""

import base64
import hashlib
import json
import re
import sys
import time
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from . import commit, coordinator, interaction, jobs, model, rules
from .browser import Browser, ReservationBlocked, StalePage, unique
from .model import action_space, choose, field_context, field_value, url_value
from .profile import HOME, SECRETS, Profile, Secrets, Vault
from .state import (
    TaskState,
    begin_recovery,
    cite,
    observe_state,
    public_context,
    put_fact,
    record_progress_change,
    spend,
    track_progress,
)
from .state import amend as amend_task

STOPPED = {"done", "blocked", "failed", "cancelled"}
TALK = {"ASK_USER", "CONFIRM", "NOTE", "DONE", "BLOCKED"}
PRIVATE = {"browser", "answers", "unused", "pending_vault", "card_parts"}  # hold secret values
ASK_TRIES = 5
TRACE_STEP = ("step", "operation", "action", "choice", "probability", "confidence", "text", "drop", "files",
              "page_changed", "url", "elapsed_ms", "unconfirmed")
TRACE_DECISION = ("operation", "choice", "confidence", "operation_probabilities", "latency_ms", "elapsed_ms")


class Halt(Exception):
    """End the task with a status and a message for the user."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def expects(action):
    """What an answer box should accept, for the channel to render."""
    constraints = action.get("constraints") or {}
    shape = {k: constraints[k] for k in ("type", "inputmode", "maxlength") if k in constraints}
    if action.get("group"):
        shape["length"] = len(action["group"])
    return shape


class Agent:
    interrupted = None
    trace_dir = HOME / "traces"
    steady_ms = 3000
    processing_ms = 300000
    outcome_pause = 5.0

    @staticmethod
    def new_state(browser, goal, title, page, record):
        return dict(
            browser=browser, goal=goal, title=title, page=page, decision=None, history=[], status="ready",
            decisions=[], text_calls=[], files=list(page.get("downloads", [])), elapsed_ms=0, started_at=None,
            record=record, memory=[], messages=[], answers={}, unused={}, typed={}, rejected=set(), approval=None,
            commit=None, pending_vault={}, card_parts={}, help_rounds={}, login=None, errors=[],
        )

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k not in PRIVATE},
            "elements": action_space(self.state["page"]["actions"])[0],
            "task": public_context(self.task),
        }

    def say(self, text, kind="info", **extra):
        message = {"from": "agent", "kind": kind, "text": text, **extra}
        self.state["messages"].append(message)
        if self.notify:
            self.notify(message)

    def ask(self, message):
        """One question to the user; returns the reply. Secret replies never enter the log."""
        if self.asker is None:
            raise Halt("blocked", "This step needs you, but no one is attached to answer.")
        message = {"from": "agent", **message}
        self.state["messages"].append(message)
        reply = self.asker(message) or {}
        if reply.get("cancel"):
            raise Halt("cancelled", "Cancelled. Nothing more will be done.")
        logged = {"text": "••••"} if message["kind"] == "secret" else self.secrets.redact(reply)
        self.state["messages"].append({"from": "user", **logged})
        return reply

    def ask_value(self, question, action, secret):
        """Ask until the answer fits the field. A malformed answer is re-asked and costs no attempt."""
        text = question
        for _ in range(ASK_TRIES):
            reply = self.ask({"kind": "secret" if secret else "text", "text": text, "expects": expects(action)})
            value = rules.clean(reply.get("text", ""), action)
            problem = rules.format_error(value, action) if value else "I need an answer to continue."
            if not problem:
                return value
            text = f"{problem} {question}"
        raise Halt("blocked", "I couldn't get a value that fits this field, so nothing was typed.")

    def elapsed(self):
        return round((time.perf_counter() - self.state["started_at"]) * 1000)

    def file_name(self, path):
        """A file's name as logs and models may see it: profile values hidden, even inside names like Jane_Doe_CV."""
        name = Path(path).name
        spaced = re.sub(r"[_-]+", " ", name)
        return self.secrets.text(spaced) if self.secrets.text(spaced) != spaced else name

    def command(self, name, body=None):
        token = SECRETS.set(self.secrets)
        try:
            if name == 'tick':
                return self.tick()
            if name == 'predict':
                return self.predict()
            if name == 'act':
                return self.act(body or {})
            raise ValueError('Unknown command')
        except Halt as halt:
            self.task.set_status(halt.status)
            self.state.update(status=halt.status, decision=None)
            self.say(str(halt))
            if halt.status in {'cancelled', 'failed'}:
                self.release_secrets()
            return self.snapshot()
        except ReservationBlocked as error:
            self.task.set_status('blocked')
            self.state.update(status='blocked', decision=None)
            self.say(str(error))
            return self.snapshot()
        except StalePage:
            raise
        except (ValueError, RuntimeError) as error:
            if self.task.status == 'blocked':
                self.state.update(status='blocked', decision=None)
                self.say(self.task.progress.get('blocker', str(error)))
                return self.snapshot()
            self.state['errors'].append({'step': len(self.state['history']),
                                         'error': self.secrets.text(str(error))})
            raise
        finally:
            SECRETS.reset(token)

    def tick(self):
        try:
            self.predict()
            if self.state['decision'] is None:
                return self.snapshot()
            return self.act({'fingerprint': self.state['page']['fingerprint']})
        except StalePage:
            self.state.update(decision=None, status='ready')
            self.observe()
            self.state['elapsed_ms'] = self.elapsed()
            return self.snapshot()

    def steady(self, page):
        """Asking costs the user's time. Right after a step the site may still be switching screens (a sign-in
        page moving on to its code field), so wait until steady_ms after it; a change means choose again."""
        last = self.state["history"][-1] if self.state["history"] else None
        while last and self.elapsed() - last.get("executed_ms", 0) < self.steady_ms:
            time.sleep(0.25)
            if not self.state["browser"].fresh(page):
                self.state["status"] = "ready"
                raise StalePage("The page changed while I was about to ask. Choose again.")

    def stop_if_interrupted(self):
        """A cancel or a change from the user arrived while this step was decided: decide again with it."""
        if (getattr(self, 'reserve_origin', None) is not None
                and getattr(self, 'browser', None) is not None):
            self.browser.check_reservation()
        if self.interrupted and self.interrupted():
            self.state["status"] = "ready"
            raise StalePage("The user cancelled or changed the task. Choose again.")

    def hidden_details(self):
        return [value for value, _ in self.profile.hidden_values()]

    def run(self):
        while self.state["status"] not in STOPPED:
            yield self.command("tick")

    def settle(self, timeout=10.0):
        """Before the tabs close: wait (bounded) for downloads still in flight and keep every one that arrived."""
        browser = self.state["browser"]
        deadline = time.monotonic() + timeout
        while browser.pending_downloads and time.monotonic() < deadline:
            time.sleep(0.1)
        arrived = browser.downloads[:]
        del browser.downloads[:len(arrived)]
        self.state["files"].extend(arrived)

    def trace(self):
        """The task as the smoke set judges it, redacted like everything that leaves the task: its outcome, the
        questions it asked, and every step and decision with probabilities. No screenshots, no request bodies."""
        s = self.state
        result = next((m for m in reversed(s["messages"]) if m.get("kind") == "result"), None)
        return self.secrets.redact({
            "goal": s["goal"], "title": s["title"], "status": s["status"], "elapsed_ms": s["elapsed_ms"],
            "result": {"text": result["text"], "evidence": result.get("evidence", "")} if result else None,
            "questions": sum(m["from"] == "agent" and m.get("kind") not in {"info", "result"} for m in s["messages"]),
            "messages": [{k: v for k, v in m.items() if k != "image"} for m in s["messages"]],
            "steps": [{k: h.get(k) for k in TRACE_STEP} for h in s["history"]],
            "decisions": [{**{k: d.get(k) for k in TRACE_DECISION}, "seen": (d.get("request") or {}).get("state")}
                          for d in s["decisions"]],  # "seen": the page and elements Jev chose from, to replay it
            "text_calls": s["text_calls"], "memory": s["memory"], "files": [self.file_name(f) for f in s["files"]],
            "errors": s.get("errors", []), "task": public_context(self.task),
        })

    def save_trace(self):
        """Write the trace to ~/.jev/traces/<time>-<title>.json, readable only by the user."""
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", self.secrets.text(self.state["title"]).lower()).strip("-")[:40] or "task"
        path = unique(self.trace_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.json")
        path.write_text(json.dumps(self.trace(), indent=2, ensure_ascii=False, default=str))
        path.chmod(0o600)
        return path

    def close(self):
        if self.task.status in {'blocked', 'paused', 'waiting_user', 'waiting_site'}:
            self.save_trace()
            return
        self.release_secrets()
        try:
            self.save_trace()
        except OSError as error:  # a trace is a record: never a reason to leave the tabs open
            print(f"jev: trace not saved: {error}", file=sys.stderr)
        finally:
            self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def __init__(self, url, goal, *, asker=None, notify=None, profile=None, vault=None, record_dir=None,
                 screenshots=False, provide_file=None, browser=None, title=None, trace_dir=None, task=None,
                 reserve_origin=None):
        goal = goal.strip() if isinstance(goal, str) else '\n'.join(goal).strip()
        if not goal:
            raise ValueError('Supply a task')
        self.asker, self.notify, self.provide_file = asker, notify, provide_file
        self.reserve_origin = reserve_origin
        self.profile, self.vault = profile or Profile(), vault or Vault()
        self.secrets = Secrets(self.profile.hidden_values())
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        if trace_dir:
            self.trace_dir = Path(trace_dir)
        self.initialize_task(goal, task=task)
        token = SECRETS.set(self.secrets)
        try:
            initial_messages = []
            if url is None:
                if not self.task.constraints.get('provider') and not self.task.constraints.get('compare'):
                    spend(self.task, 'coordinator')
                    intent = jobs.start_intent(self.task.goal)
                    if intent['provider']:
                        self.task.constraints['provider'] = intent['provider']
                    if intent['compare']:
                        self.task.constraints['compare'] = True
                mode = interaction.provider_mode(self.task, self.profile.data.get('defaults', {}))
                self.state = self.new_state(None, self.task.goal, title or goal[:60],
                                           {'actions': [], 'downloads': []}, False)
                if mode['mode'] == 'ask':
                    q = next(q for q in self.task.questions.values() if not q.get('closed'))
                    q['displayed_at'] = time.time()
                    reply = self.ask({'id': q['id'], 'kind': 'text', 'text': q['text']})
                    self.stop_if_interrupted()
                    interaction.answer_requirements(self.task, q['id'], {'provider': reply.get('text', '')},
                                                    now=time.time())
                    mode = interaction.provider_mode(self.task, self.profile.data.get('defaults', {}))
                if mode['notice']:
                    self.say(mode['notice'])
                initial_messages = self.state['messages']
                url, selected_title, _ = jobs.start(self.context())
                title = title or selected_title
            browser_options = {'geolocation': None}
            if reserve_origin is not None:
                browser_options['reserve_origin'] = reserve_origin
            self.browser = browser or Browser(url, **browser_options)
            page = self.clean(self.browser.observe(screenshot=False))
            self.state = self.new_state(self.browser, self.task.goal, title or goal[:60], page, bool(record_dir))
            self.state['messages'] = initial_messages
            self.retain_observation(page)
        finally:
            SECRETS.reset(token)

    def initialize_task(self, goal, *, task=None, journal_dir=None, coordinator_client=None):
        """The real constructor and browser fakes share all task-owned state."""
        self.task = task or TaskState(uuid4().hex, self.secrets.text(goal))
        self.secret_store = interaction.SecretStore(time.time)
        self.field_cache = interaction.FieldCache()
        self.journal = commit.Journal.create(journal_dir or HOME / 'journals', lambda value: self.secrets.redact(value))
        self.coordinator_client = coordinator_client
        self._field_pending = None
        self._wait_until = None
        for key in ('name', 'phone', 'email'):
            if self.profile.data.get(key):
                put_fact(self.task, key, f'[profile:{key}]', source='profile')

    def context(self):
        task = public_context(self.task)
        task['facts'] = {k: f for k, f in task['facts'].items() if f.get('valid')}
        return self.secrets.text('Goal: ' + self.task.goal + '\nStructured task: ' +
                                 json.dumps(task, ensure_ascii=False, default=str) + '\nProfile: ' +
                                 json.dumps(self.profile.summary(), ensure_ascii=False))

    def clean(self, page):
        page = self.secrets.redact(deepcopy(page))
        for action in page['actions']:
            if action.get('value') and (action.get('frame'), action.get('node')) in self.secrets.nodes:
                action['value'] = '[secret]'
        if self.screenshots:
            page['screenshot'] = self.browser.screenshot(masked=self.secrets.nodes, hide=self.hidden_details())
        return page

    def retain_observation(self, page):
        oid = observe_state(self.task, self.secrets.redact(page))
        for line in page['text'].splitlines():
            if line.strip():
                cite(self.task, oid, line)
        for event in self.task.observations[oid]['events']:
            if event.strip():
                cite(self.task, oid, event, kind='browser-event')
        if self.record_dir and page.get('screenshot'):
            self.record_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = self.record_dir / f'{self.task._next_observation - 1:06d}.png'
            with path.open('xb') as stream:
                path.chmod(0o600)
                stream.write(base64.b64decode(page['screenshot']))
        return oid

    def observe(self):
        page = self.clean(self.browser.observe(screenshot=False))
        self.state['page'] = page
        self.state['files'].extend(page.get('downloads', []))
        self.retain_observation(page)
        self.refresh_challenges(page)
        return page

    def coordinate(self, event):
        if event != 'outcome':
            self.stop_if_interrupted()
        page = self.state['page']
        kwargs = {'client': self.coordinator_client} if self.coordinator_client else {}
        assessment = coordinator.assess(self.task, page, event, **kwargs)
        if event != 'outcome':
            self.stop_if_interrupted()
        previous_facts = deepcopy(self.task.facts)
        coordinator.apply_assessment(self.task, assessment)
        material_keys = {key for r in self.task.completion for key in r.get('material_keys', [])}
        for fact in assessment['facts']:
            value = fact['value']
            quotes = ' '.join(self.task.evidence[eid]['quote'] for eid in fact['evidence'])
            previous = previous_facts.get(fact['key'], {})
            if (fact['key'] in material_keys and isinstance(value, str) and value and value in quotes
                    and (previous.get('value') != value or not previous.get('valid'))):
                identity = hashlib.sha256(json.dumps([fact['key'], value]).encode()).hexdigest()
                record_progress_change(self.task, kind='relevant_content',
                    evidence_ids=tuple(fact['evidence']), transition_id='material-fact:' + identity)
        for requirement in assessment['requirements']:
            interaction.require(self.task, requirement)
        auth = assessment['auth']
        if auth:
            flow = interaction.observe_auth(self.task, auth)
            if flow['blocked']:
                raise Halt('blocked', 'The authentication attempt limit has been reached.')
            if auth['status'] == 'authenticated':
                record_progress_change(self.task, kind='auth_transition', evidence_ids=tuple(auth['evidence_ids']),
                                       transition_id='auth:' + auth['flow_id'])
            self.discard_invalid_secrets()
        if assessment['proposal']:
            jobs.review_proposal(self.task, assessment['proposal'])
            self.task.proposal = assessment['proposal']
        if assessment['outcome']:
            outcome = assessment['outcome']
            pending = [eid for eid, row in self.task.executions.items()
                       if row.get('commitment') and row.get('outcome') not in {'success', 'failed'}]
            if len(pending) != 1:
                raise ValueError('Outcome needs exactly one pending commitment')
            eid = pending[0]
            if outcome['status'] == 'success' and self.task.completion:
                self.task.completion, _ = jobs.completion_bindings(self.task)
            commit.record_outcome(self.task, eid, {k: v for k, v in outcome.items() if k != 'execution_id'},
                                  now=time.monotonic(), journal=self.journal)
        if assessment['notice']:
            notice = interaction.chat_update(self.task, assessment['notice'])
            if notice:
                self.say(notice['text'])
        if assessment['recovery']:
            active = self.task.progress.get('active_recovery', {})
            objective = active.get('objective', self.task.goal)
            if not begin_recovery(self.task, objective=objective, semantic_state=self.semantic_state(),
                                  recovery=assessment['recovery']):
                raise Halt('blocked', 'No new recovery approach is available. ' + self.task.objective)

    def semantic_state(self):
        page = self.state['page']
        return json.dumps([page['url'], [(a['kind'], a['label'], a.get('context')) for a in page['actions']]],
                          sort_keys=True)

    def amend(self, text, changed_keys):
        amend_task(self.task, self.secrets.text(text), changed_keys)
        self.state['decision'] = None
        self.state['status'] = 'ready'
        self.field_cache.clear()
        self.observe()
        self.coordinate('user_amendment')

    def resume(self):
        self.task.set_status('running')
        self.state.update(status='ready', decision=None)
        if getattr(self.browser, 'reserve_origin', None) is not None:
            self.browser.reservation_blocked = False
        self.observe()
        pending = any(e.get('commitment') and e.get('outcome') not in {'success', 'failed'}
                      and e.get('status') != 'not_dispatched' for e in self.task.executions.values())
        self.coordinate('outcome' if pending else 'stage_change')

    def stop(self):
        self.task.set_status('cancelled')
        self.state.update(status='cancelled', decision=None)
        self.release_secrets()

    def release_secrets(self):
        self.sanitize_retained()
        self.secret_store.purge(self.task.task_id)
        self.field_cache.clear()
        self.state['answers'].clear()
        self.state['unused'].clear()
        self.secrets = Secrets(self.profile.hidden_values())

    def discard_invalid_secrets(self):
        expired = {cid for cid, c in self.task.challenges.items()
                   if c.get('expires_at') is not None and c['expires_at'] <= time.time()
                   or c.get('status') in {'expired', 'rejected', 'authenticated'}}
        for cid in expired:
            challenge = self.task.challenges[cid]
            if challenge.get('status') not in {'rejected', 'authenticated'}:
                challenge['status'] = 'expired'
        for fact in self.task.facts.values():
            if fact.get('scope') in expired:
                fact['valid'] = False
            if not fact['valid'] and isinstance(fact['value'], str) and fact['value'].startswith('[secret:'):
                ref = fact['value']
                self.secret_store.consume(ref)
                self.sanitize_retained()
                self.secrets.remove(ref)

    def sanitize_retained(self):
        for key, value in list(self.state.items()):
            if key != 'browser':
                redacted = self.secrets.redact(value)
                if redacted != value:
                    self.state[key] = redacted
        for key in ('updates', 'constraints', 'facts', 'requirements', 'questions', 'challenges', 'observations',
                    'evidence', 'executions', 'progress', 'approval', 'proposal', 'completion', 'diagnostics'):
            value = getattr(self.task, key)
            redacted = self.secrets.redact(value)
            if redacted != value:
                setattr(self.task, key, redacted)

    def validate_decision(self, decision, page):
        op = decision.get('operation')
        if op in TALK:
            return
        _, heads, controls = action_space(page['actions'])
        head = model.OPERATION_HEAD.get(op)
        candidates = heads.get(head, {}).values() if head else [controls.get(op)]
        action = next((a for a in candidates if a and a['id'] == decision.get('choice')), None)
        if not action:
            raise ValueError('Jev selected an unavailable operation or target')
        if op == 'PRESS_KEY' and decision.get('key') not in model.KEY_HELP:
            raise ValueError('Jev selected an unavailable key')
        if op == 'DRAG' and not any(a['id'] == decision.get('drop')
                                    for a in heads.get('drag_target', {}).values()):
            raise ValueError('Jev selected an unobserved drop target')

    def predict(self):
        s = self.state
        if s['started_at'] is None:
            s['started_at'] = time.perf_counter()
        self.stop_if_interrupted()
        self.discard_invalid_secrets()
        if self.task.status in STOPPED or s['status'] in STOPPED:
            raise ValueError('This task has stopped. Resume before continuing.')
        s['decision'] = None
        if self.task.status == 'waiting_site' and s.get('commit'):
            pending = s['commit']
            since = pending.setdefault('poll_started', time.monotonic())
            if time.monotonic() - since >= 300 or pending.get('polls', 0) >= 60:
                commit.record_outcome(self.task, pending['execution_id'],
                    dict(status='unknown', evidence_ids=[], artifact_id=None),
                    now=time.monotonic(), journal=self.journal)
                raise Halt('blocked', 'The transaction is still unconfirmed after bounded observation.')
            if not pending.get('notice_sent'):
                self.say('The site is still processing the transaction. I am watching for its result.')
                pending['notice_sent'] = True
            time.sleep(self.outcome_pause)
            self.stop_if_interrupted()
            self.observe()
            pending['polls'] = pending.get('polls', 0) + 1
            self.coordinate('outcome')
            if self.task.status in STOPPED:
                raise Halt(self.task.status, 'Transaction outcome: ' +
                           self.task.executions[pending['execution_id']].get('outcome', 'unknown'))
            return self.snapshot()
        if self.task.status == 'waiting_user' and s.get('commit'):
            self.task.set_status('running')
            self.coordinate('authentication')
        if not self.browser.fresh(s['page']):
            self.observe()
        if self._wait_until is not None:
            if time.monotonic() < self._wait_until:
                time.sleep(min(0.25, self._wait_until - time.monotonic()))
                self.observe()
                return self.snapshot()
            self._wait_until = None
        for event in coordinator.events_for(self.task, s['page']):
            self.coordinate(event)
        self.refresh_captcha_images(s['page'])
        revision, oid = self.task.revision, next(reversed(self.task.observations))
        repair = ''
        for attempt in range(2):
            self.stop_if_interrupted()
            spend(self.task, 'jev')
            try:
                decision = choose(s['page'], self.context() + repair, s['history'])
                self.validate_decision(decision, s['page'])
                break
            except ValueError as error:
                repair = '\nSelection validation error: ' + self.secrets.text(str(error))
                self.task.diagnostics.append({'kind': 'invalid_jev', 'reason': repair})
                if attempt or not self.browser.fresh(s['page']):
                    raise
        self.stop_if_interrupted()
        if revision != self.task.revision or oid != next(reversed(self.task.observations)):
            raise StalePage('Task changed while choosing')
        decision = {**decision, 'id': uuid4().hex, 'revision': revision, 'observation_id': oid}
        s['decision'] = decision
        s['decisions'].append({**decision, 'fingerprint': s['page']['fingerprint'], 'elapsed_ms': self.elapsed()})
        s['status'] = 'predicted'
        return self.snapshot()

    def act(self, body):
        s = self.state
        decision, page = s['decision'], s['page']
        if not decision or body.get('fingerprint') != page['fingerprint']:
            raise ValueError('Observe and choose before acting')
        s['decision'] = None
        self.stop_if_interrupted()
        self.validate_decision(decision, page)
        if not self.browser.fresh(page):
            s['status'] = 'ready'
            raise StalePage('Page changed since selection')
        if (decision.get('revision') != self.task.revision
                or decision.get('observation_id') != next(reversed(self.task.observations))):
            raise StalePage('Decision has stale task ownership')
        operation = decision['operation']
        if operation in TALK:
            return self.talk(operation, page)
        action = next(a for a in page['actions'] if a['id'] == decision['choice'])
        return self.perform(operation, action, decision, page)

    def perform(self, operation, action, decision, page):
        self._field_pending = None
        if (self.task.constraints.get('read_only_reconciliation')
                and operation not in {'LOOK', 'WAIT', 'COPY', 'SCROLL', 'OPEN_URL'}):
            raise Halt('blocked', 'Read-only reconciliation cannot submit input or repeat the interrupted action.')
        if getattr(self, 'reserve_origin', None) is not None and not self.reserve_origin(page['url']):
            raise Halt('blocked', 'This account is reserved by another task with an unresolved outcome.')
        if operation == 'LOOK':
            return self.look(page, 'Observe missing controls')
        if any(not r.get('satisfied') for r in self.task.requirements.values() if r.get('kind') in interaction.KINDS):
            self.ask_user(page)
            return self.snapshot()
        kind = rules.commitment_kind(operation, action, page, key=decision.get('key'))
        if kind == 'unresolved':
            kind = jobs.effect_assessment(self.task, page, action, operation, decision.get('key'),
                                          client=self.coordinator_client)
        self.stop_if_interrupted()
        if kind == 'unresolved':
            raise Halt('blocked', 'The selected action has an unresolved external effect.')
        requires_commit = kind == 'commitment'
        effect_id = None
        if requires_commit:
            self.task.progress.setdefault('account_reservation_key',
                self.task.observations[next(reversed(self.task.observations))]['origin'])
            self.coordinate('confirmation')
            proposal = self.task.proposal
            if not proposal:
                raise Halt('blocked', 'The complete action details are not yet verified.')
            effect_id = commit.bind_proposal_effect(self.task, proposal, operation=operation,
                                                   choice=decision['choice'], key=decision.get('key'))
            if not commit.can_commit(self.task, proposal):
                self.confirm(page, action['label'], action, effect_id=effect_id)
                return self.snapshot()
        captcha = self.validate_captcha_action(operation, action, page, key=decision.get('key'))
        inputs, helper, shown = self.inputs(operation, action, decision, page)
        if captcha and captcha['kind'] == 'text' and operation == 'TYPE_TEXT':
            inputs = {'text': captcha['value']}
            shown = captcha['value']
        self.stop_if_interrupted()
        if not self.browser.fresh(page, action):
            raise StalePage('Page changed during input preparation')
        challenge = self.challenge_for(action)
        if challenge is None and kind == 'authentication' and operation in {'CLICK', 'DOUBLE_CLICK', 'PRESS_KEY'}:
            challenge = self.auth_submit_challenge(action)
        bound = commit.bind_decision(self.task, decision, page, assessment=kind, effect_id=effect_id)
        def execute():
            # Recheck image-bound human input at the final executor boundary.
            self.validate_captcha_action(operation, action, page, key=decision.get('key'))
            def consume_input():
                if self._field_pending:
                    payload, cid, ref = self._field_pending
                    self.field_cache.consume(self.task, payload, cid)
                    if ref:
                        self.secret_store.consume(ref)
                    self._field_pending = None
            try:
                result = self.browser.act(operation, action, page, **inputs)
            except StalePage:
                # Browser's final preflight guarantees no mutation occurred.
                raise
            except Exception:
                consume_input()  # uncertain dispatch must never replay a secret
                raise
            consume_input()
            return result

        spend(self.task, 'actions')
        receipt = commit.dispatch_once(self.task, self.journal, bound, page,
            act=execute,
            is_fresh=lambda: self.browser.fresh(page, action),
            interrupted=lambda: bool(self.interrupted and self.interrupted()),
            proposal=self.task.proposal if requires_commit else None,
            challenge_id=challenge['id'] if challenge else None)
        self.state['history'].append(dict(step=len(self.state['history']) + 1, operation=operation,
            action=action['label'], choice=decision['choice'], text=shown, result=receipt['result'],
            execution_id=receipt['execution_id'], elapsed_ms=self.elapsed(), executed_ms=self.elapsed(),
            url=page['url'], page_changed=None, unconfirmed=receipt['status'] == 'unknown'))
        if operation == 'TYPE_TEXT' and rules.secret_field(action):
            for node in action.get('group') or [action.get('node')]:
                self.secrets.nodes.add((action.get('frame'), node))
        if receipt['status'] == 'not_dispatched':
            raise StalePage('The selected action was not dispatched')
        self.record_captcha_execution(operation, action, receipt, key=decision.get('key'))
        self.observe()  # journal receipt exists before any post-dispatch read
        self.state['history'][-1]['page_changed'] = self.state['page']['fingerprint'] != page['fingerprint']
        if self.interrupted and self.interrupted():
            self.task.set_status('cancelled')
            self.state['status'] = 'cancelled'
            if requires_commit or receipt['status'] == 'unknown':
                commit.record_outcome(self.task, receipt['execution_id'],
                    dict(status='unknown', evidence_ids=[], artifact_id=None),
                    now=time.monotonic(), journal=self.journal)
            self.release_secrets()
            return self.snapshot()
        if receipt['status'] == 'unknown':
            raise Halt('blocked', 'The action outcome is unknown; it will not be repeated.')
        if kind == 'authentication':
            self.record_auth_navigation(receipt)
        if requires_commit:
            self.state['commit'] = {'label': action['label'], 'execution_id': receipt['execution_id']}
            self.coordinate('outcome')
            if self.task.status in STOPPED:
                raise Halt(self.task.status, 'The transaction outcome is ' +
                           self.task.executions[receipt['execution_id']].get('outcome', 'unknown') + '.')
        for event in coordinator.events_for(self.task, self.state['page']):
            self.coordinate(event)
        self.state['status'] = 'ready'
        if self.task.status in {'waiting_user', 'waiting_site'}:
            return self.snapshot()
        oid = next(reversed(self.task.observations))
        changes = tuple(eid for eid in self.task.progress.get('changes', {})
                        if self.task.evidence[eid]['observation_id'] == oid)
        progress = track_progress(self.task, operation=operation, semantic_state=self.semantic_state(),
                                  change_evidence=changes, now=time.monotonic())
        if progress == 'wait':
            self._wait_until = time.monotonic() + 1.0
        elif progress == 'recover':
            self.coordinate('stalled')
        elif progress == 'blocked':
            raise Halt('blocked', self.task.progress.get('blocker', 'Progress could not be verified.'))
        return self.snapshot()

    def look(self, page, purpose):
        if rules.captcha_challenge(page):
            return self.relay_captcha(page)
        image, cells = self.browser.grid_screenshot(columns=16, rows=12, masked=self.secrets.nodes,
                                                     hide=self.hidden_details())
        annotations = jobs.visual_annotations(self.task, page, image, cells)
        actions = self.browser.visual_candidates(page, annotations)
        page['actions'].extend(actions)
        self.retain_observation(page)
        self.state['status'] = 'ready'
        return self.snapshot()

    def challenge_for(self, action):
        oid = next(reversed(self.task.observations))
        return next((c for c in self.task.challenges.values()
                     if c.get('revision') == self.task.revision and c.get('observation_id') == oid
                     and action['id'] in c.get('field_ids', [])
                     and c.get('status') in {'challenge', 'issued', 'answer_ready', 'awaiting_answer', 'pending'}),
                    None)

    def inputs(self, operation, action, decision, page):
        if operation == 'TYPE_TEXT':
            captcha = self.validate_captcha_action(operation, action, page, key=decision.get('key'))
            if captcha and captcha['kind'] == 'text' and operation == 'TYPE_TEXT':
                return {'text': captcha['value']}, None, captcha['value']
            value, shown, helper = self.field_input(action, page)
            return {'text': value}, helper, shown
        if operation == 'OPEN_URL':
            url, helper = url_value(self.context(), page, self.state['history'])
            if urlparse(url).scheme not in {'http', 'https'} or not urlparse(url).hostname:
                raise ValueError('Invalid navigation URL')
            self.validate_navigation(url, page)
            if getattr(self, 'reserve_origin', None) is not None and not self.reserve_origin(url):
                raise Halt('blocked', 'The destination account is reserved by another task.')
            return {'url': url}, helper, url
        if operation == 'PRESS_KEY':
            return {'key': decision['key']}, None, None
        if operation == 'DRAG':
            drop = next(a for a in page['actions'] if a['id'] == decision['drop'])
            if drop['kind'] == 'look' or drop.get('point'):
                raise ValueError('Drag needs an observed DOM or visual target')
            return {'drop': drop}, None, None
        if operation == 'UPLOAD':
            paths = self.provide_file(action) if self.provide_file else None
            if paths is None:
                reply = self.ask({'kind': 'file', 'text': 'Send the file to attach here.'})
                if reply.get('upload_id') and hasattr(self, 'resolve_upload'):
                    paths = [self.resolve_upload(reply['upload_id'])]
                else:
                    paths = [reply['file']] if reply.get('file') else []
            if not paths or not all(Path(path).is_file() for path in paths):
                raise Halt('blocked', 'No valid file arrived.')
            return {'files': paths}, None, None
        return {}, None, None

    def field_input(self, action, page):
        challenge = self.challenge_for(action)
        if rules.secret_field(action):
            if challenge is None:
                self.coordinate('authentication')
                challenge = self.register_challenge(action)
            fact = self.task.facts.get('secret:' + challenge['id'], {})
            if not fact.get('valid'):
                self.ask_user(page)
                raise StalePage('Secret answer recorded; Jev must select the next operation')
            ref = fact['value']
            value = self.secret_store.resolve(ref, **self.secret_scope(challenge))
            problem = rules.format_error(value, action)
            if problem:
                raise ValueError(problem)
            self._field_pending = ({'field': action, 'challenge': challenge['id']}, challenge['id'], ref)
            return value, ref, None
        cid = challenge['id'] if challenge else None
        allowed = [f['value'] for f in self.task.facts.values() if f['valid'] and f['scope'] == cid] if cid else []
        payload = field_context(self.context(), action, page, self.state['history'], self.profile.summary(), allowed)
        payload['field_scope'] = dict(context=action.get('context'), origin=urlparse(page['url']).netloc,
                                      field_id=action['id'], revision=self.task.revision, challenge_id=cid)
        cached = self.field_cache.get(self.task, payload, cid)
        if cached is None:
            form, helper = field_value(payload)
            kind, value = form[:2]
            if kind == 'profile':
                value, shown = self.profile.value(value), f'[profile:{value}]'
                ref = None
            elif kind == 'secret':
                if not challenge or value not in allowed:
                    raise ValueError('Secret is not bound to the selected field')
                ref, shown = value, value
                value = self.secret_store.resolve(ref, **self.secret_scope(challenge))
            elif kind == 'text' and not rules.secret_field(action):
                shown, ref = value, None
            elif kind == 'ask':
                self.coordinate('missing_information')
                self.ask_user(page)
                raise StalePage('Answer recorded; Jev must select the next action')
            else:
                raise ValueError('Field helper cannot invent secret values')
            problem = rules.format_error(value, action)
            if problem:
                raise ValueError(problem)
            cached = {'value': ref or value, 'shown': shown, 'helper': helper, 'ref': ref}
            self.field_cache.put(self.task, payload, cid, cached)
        value = (self.secret_store.resolve(cached['ref'], **self.secret_scope(challenge))
                 if cached['ref'] else cached['value'])
        self._field_pending = (payload, cid, cached['ref'])
        self.state['text_calls'].append({**(cached['helper'] or {}), 'field': action['label'],
                                         'value': cached['shown']})
        return value, cached['shown'], cached['helper']

    def secret_scope(self, challenge):
        return dict(task_id=self.task.task_id, **{k: challenge[k] for k in
                    ('question_id', 'origin', 'purpose', 'field_role')}, challenge_id=challenge['id'])

    def talk(self, operation, page):
        if operation in {'ASK_USER', 'BLOCKED'} and rules.captcha_challenge(page):
            return self.relay_captcha(page)
        if operation == 'DONE':
            return self.finish(page)
        if operation == 'BLOCKED':
            self.coordinate('stalled')
            if not self.task.progress.get('active_recovery'):
                raise Halt('blocked', 'No verified browser path is available. ' + self.task.objective)
        elif operation == 'CONFIRM':
            self.coordinate('confirmation')
            # CONFIRM carries no executable target: approval awaits an actual Jev-selected effect.
        elif operation == 'NOTE':
            self.coordinate('stage_change')
        else:
            self.coordinate('missing_information')
            self.ask_user(page)
        self.state['status'] = 'ready'
        return self.snapshot()

    def ask_user(self, page):
        question = next((q for q in self.task.questions.values() if q.get('answered_at') is None
                         and not q.get('closed') and q['revision'] == self.task.revision), None)
        if not question:
            self.state['status'] = 'ready'
            return
        question['displayed_at'] = time.time()
        self.task.set_status('waiting_user')
        reply = self.ask({k: v for k, v in question.items() if k in {'id', 'kind', 'text', 'options', 'challenge_id'}})
        self.stop_if_interrupted()
        self.task.set_status('running')
        if reply.get('expired'):
            self.observe()
            raise StalePage('The queued secret challenge expired; inspect its current state.')
        keys = [key for rid in question['requirement_ids'] for key in self.task.requirements[rid]['keys']
                if not self.task.facts.get(key, {}).get('valid')
                or self.task.facts[key]['value'] in (None, '')]
        values = reply.get('values')
        if values is None:
            if len(keys) != 1 and question['kind'] == 'text':
                values = jobs.answer_facts(reply.get('text', ''), keys)
            elif len(keys) == 1:
                values = {keys[0]: reply.get('option') if question['kind'] == 'choice' else reply.get('text', '')}
            else:
                raise Halt('blocked', 'This reply has no unambiguous current information requirement.')
        if any(key in {'location', 'address', 'pickup', 'delivery_address'}
               and isinstance(value, str) and rules.means_current_location(value)
               for key, value in values.items()):
            self.say('Please provide an address or landmark in chat for your current location.')
            return
        if question['kind'] == 'secret':
            challenge = self.task.challenges.get(question.get('challenge_id'))
            if not challenge:
                raise Halt('blocked', 'This secret question has no current challenge.')
            raw = values[keys[0]]
            action = next((a for a in page['actions'] if a['id'] in challenge['field_ids']), None)
            if action is None:
                raise StalePage('The secret field is no longer observed')
            raw = rules.clean(raw, action)
            if rules.format_error(raw, action):
                raw = self.ask_value(rules.format_error(raw, action), action, True)
            ref = self.secret_store.put(raw, **self.secret_scope(challenge),
                                        expires_at=challenge.get('expires_at') or time.time() + 600)
            self.secrets.add(raw, ref)
            if challenge.get('field_role') == 'password' and challenge.get('purpose') == 'login':
                self.offer_password_save(challenge, raw)
            values = {keys[0]: ref}
        interaction.answer_requirements(self.task, question['id'], values, now=time.time())
        self.observe()
        self.coordinate('user_answer')

    def offer_password_save(self, challenge, raw):
        """Separate origin-bound consent; a login answer never implies vault consent."""
        if challenge.get('purpose') != 'login' or challenge.get('field_role') != 'password':
            return
        host = urlparse(challenge['origin']).netloc
        reply = self.ask({'kind': 'confirm', 'text': f'Save this password locally for {host}?',
                          'options': [{'id': 'yes', 'label': 'Save password'},
                                      {'id': 'no', 'label': 'Use once'}]})
        self.stop_if_interrupted()
        if reply.get('option') != 'yes':
            return
        try:
            self.vault.set('login.' + host, raw)
        except (KeyError, ValueError, RuntimeError):
            self.say('Password storage is unavailable; using the temporary answer for this login.')
            return
        if host not in self.profile.data['logins']:
            self.profile.data['logins'].append(host)
            self.profile.save()

    def confirm(self, page, label, action=None, *, effect_id=None):
        proposal = self.task.proposal
        if not proposal or not effect_id:
            raise ValueError('Confirmation requires a reviewed proposal and observed effect')
        qid = uuid4().hex
        q = dict(id=qid, kind='confirm', revision=self.task.revision, proposal_key=commit.proposal_key(proposal),
                 effect_id=effect_id, displayed_at=time.time(), answered_at=None)
        self.task.questions[qid] = q
        text = commit.review_text(self.task, proposal) + '\nControl: ' + label
        reply = self.ask({'id': qid, 'kind': 'confirm', 'text': text,
                          'options': [{'id': 'yes', 'label': 'Yes, approve'}, {'id': 'no', 'label': 'No'}]})
        self.stop_if_interrupted()
        if reply.get('option') == 'yes':
            commit.approve(self.task, proposal, question_id=qid, response='yes')
        else:
            q.update(answered_at=time.time(), closed=True)
            self.task.approval = None
        self.observe()
        self.state['status'] = 'ready'
        return reply.get('option') == 'yes'

    def finish(self, page):
        self.coordinate('completion')
        if self.task.completion:
            self.task.completion, _ = jobs.completion_bindings(self.task)
        report = commit.completion_report(self.task)
        if not report['verified']:
            raise Halt('blocked', 'Completion is unverified. Missing evidence: ' +
                       ', '.join(report['missing'] or ['goal requirements']))
        self.task.status = 'done'
        self.state['status'] = 'done'
        self.say('Completed with verified evidence.', kind='result', evidence=report['evidence_ids'])
        self.release_secrets()
        return self.snapshot()


    @staticmethod
    def field_identity(action, page):
        frame = action.get('frame') or 'main'
        frame_url = page.get('frames', {}).get(frame, {}).get('url', page['url'])
        parsed = urlparse(frame_url)
        return dict(label=action['label'], context=action.get('context') or {},
                    constraints=action.get('constraints') or {}, frame=frame,
                    frame_origin=f'{parsed.scheme}://{parsed.netloc}')

    @staticmethod
    def captcha_page_key(page, field_id):
        content = deepcopy({k: page[k] for k in ('url', 'text', 'actions', 'scroll')})
        for action in content['actions']:
            if action['id'] == field_id:
                action.pop('value', None)
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    def captcha_image(self, challenge):
        masked = self.secrets.nodes | set(challenge.get('masked_fields', []))
        return self.browser.marked_screenshot(challenge['marks'], masked=masked, hide=self.hidden_details())

    def captcha_image_digest(self, challenge):
        return hashlib.sha256(self.captcha_image(challenge).encode()).hexdigest()

    def record_captcha_execution(self, operation, action, receipt, *, key=None):
        for c in self.task.challenges.values():
            if c.get('purpose') != 'captcha' or c.get('kind') != 'text' or c.get('status') == 'expired':
                continue
            submits = ((operation == 'CLICK' and action['id'] in c.get('submit_targets', []))
                       or (operation == 'PRESS_KEY' and key == 'Enter' and action['id'] == c.get('text_field')))
            if c.get('status') == 'answer_applied' and submits:
                c['status'] = 'submitted'  # includes unknown; never replay submission
            elif operation == 'TYPE_TEXT' and receipt['status'] == 'executed':
                c.update(status='checking_application', applied_execution_id=receipt['execution_id'])

    def refresh_applied_captcha(self, c, page, oid):
        fact = self.task.facts.get('captcha:' + c['id'], {})
        field = next((a for a in page['actions'] if a['id'] == c.get('text_field')), None)
        receipt = self.task.executions.get(c.get('applied_execution_id'), {})
        if (c.get('status') in {'checking_application', 'answer_applied'} and fact.get('valid')
                and receipt.get('status') == 'executed' and receipt.get('operation') == 'TYPE_TEXT'
                and field and field.get('value') == fact['value']['value']
                and c['structure_key'] == self.captcha_page_key(page, c['text_field'])
                and c['image_digest'] == self.captcha_image_digest(c)):
            c.update(status='answer_applied', observation_id=oid, page_fingerprint=page['fingerprint'])
            fact['observation_id'] = oid
            return True
        return False

    def refresh_challenges(self, page):
        oid = next(reversed(self.task.observations))
        for cid, challenge in self.task.challenges.items():
            if challenge.get('purpose') == 'captcha':
                # New rounds expire unless a text answer is proven applied to the
                # same unchanged challenge; pixels can change without DOM changes.
                if challenge.get('observation_id') != oid and not self.refresh_applied_captcha(challenge, page, oid):
                    challenge['status'] = 'expired'
                    if 'captcha:' + cid in self.task.facts:
                        self.task.facts['captcha:' + cid]['valid'] = False
                continue
            if challenge.get('status') not in {'challenge', 'answer_ready', 'awaiting_answer'}:
                continue
            matches = [a for a in page['actions'] if a['kind'] == 'fill'
                       and self.field_identity(a, page) == challenge.get('field_identity')]
            if (challenge.get('origin') == self.task.observations[oid]['origin']
                    and challenge.get('revision') == self.task.revision and len(matches) == 1):
                challenge['observation_id'] = oid
                challenge['field_ids'] = [matches[0]['id']]
            else:
                challenge['status'] = 'expired'
        self.discard_invalid_secrets()

    def validate_navigation(self, url, page):
        destination, current = urlparse(url), urlparse(page['url'])
        same_origin = (destination.scheme, destination.netloc) == (current.scheme, current.netloc)
        explicit = any(f.get('valid') and f.get('source') == 'user'
                       and f.get('value') == url for k, f in self.task.facts.items()
                       if k in {'navigation_url', 'provider_url', 'alternative_url'})
        # Model helpers cannot authorize an external provider, including a page's
        # advertised competitor link. The user's destination fact authorizes it.
        if same_origin or explicit:
            return
        interaction.require(self.task, dict(id='navigation-service', kind='text', purpose='task',
            keys=['navigation_url'], scope='task', depends_on=[], options=[],
            reason='Confirm the destination for the selected provider or an explicit alternative'))
        self.ask_user(page)
        raise StalePage('Destination clarified; Jev must select a fresh navigation action')

    def refresh_captcha_images(self, page):
        for cid, c in self.task.challenges.items():
            if c.get('purpose') != 'captcha' or c.get('status') == 'expired':
                continue
            if (c['page_fingerprint'] != page['fingerprint']
                    or c['image_digest'] != self.captcha_image_digest(c)):
                c['status'] = 'expired'
                fact = self.task.facts.get('captcha:' + cid)
                if fact:
                    fact['valid'] = False

    def validate_captcha_action(self, operation, action, page, *, key=None):
        if operation in {'WAIT', 'SCROLL', 'LOOK', 'COPY'}:
            return None
        challenges = [c for c in self.task.challenges.values() if c.get('purpose') == 'captcha'
                      and c.get('status') != 'expired']
        if not challenges:
            known = any(c.get('purpose') == 'captcha' and c.get('structure_key') ==
                        self.captcha_page_key(page, c.get('text_field')) for c in self.task.challenges.values())
            if rules.captcha_challenge(page) or known:
                raise Halt('blocked', 'This challenge needs a fresh human answer in chat.')
            return None
        c = challenges[-1]
        fact = self.task.facts.get('captcha:' + c['id'], {})
        current = next(reversed(self.task.observations))
        if (not fact.get('valid') or c['observation_id'] != current
                or c['page_fingerprint'] != page['fingerprint']
                or c['image_digest'] != self.captcha_image_digest(c)
                or not self.browser.fresh(page)):
            fact['valid'] = False
            c['status'] = 'expired'
            raise StalePage('The human challenge changed; a fresh image and answer are required')
        answer = fact['value']
        if answer['kind'] == 'tiles':
            targets = [c['targets'][label] for label in answer['labels']]
            allowed = operation == 'CLICK' and action['id'] in targets
        else:
            fields = [a for a in page['actions'] if a['kind'] == 'fill']
            if c.get('status') == 'answer_applied':
                allowed = ((operation == 'CLICK' and action['id'] in c['submit_targets'])
                           or (operation == 'PRESS_KEY' and key == 'Enter' and action['id'] == c['text_field']))
            else:
                allowed = operation == 'TYPE_TEXT' and len(fields) == 1 and action == fields[0]
        if not allowed:
            raise Halt('blocked', 'The selected operation does not match the current human challenge answer.')
        return answer

    def register_challenge(self, action):
        oid = next(reversed(self.task.observations))
        origin = self.task.observations[oid]['origin']
        flows = [f for f in self.task.progress.get('auth_flows', {}).values()
                 if f['revision'] == self.task.revision and origin in f['allowed_origins']
                 and f['status'] in {'required', 'challenge', 'rejected', 'expired'} and not f.get('blocked')]
        if len(flows) != 1:
            raise Halt('blocked', 'The secret field has no unambiguous, permitted authentication flow.')
        flow = flows[0]
        cid = uuid4().hex
        ttl = 900 if flow['field_role'] in {'cvv', 'card_number', 'card_expiry', 'card_name'} else 600
        challenge = dict(id=cid, flow_id=flow['flow_id'], revision=self.task.revision, origin=origin,
            purpose=flow['purpose'], field_role=flow['field_role'], observation_id=oid,
            field_ids=[action['id']], status='challenge', expires_at=time.time() + ttl,
            page_fingerprint=self.state['page']['fingerprint'], field_context=action.get('context') or {},
            field_label=action['label'], field_constraints=action.get('constraints') or {},
            field_identity=self.field_identity(action, self.state['page']))
        if flow['purpose'] == 'payment':
            parents = [e for e in self.task.executions.values() if e.get('commitment')
                       and e.get('status') == 'executed' and e.get('approval_id')
                       and e.get('outcome') not in {'success', 'failed'}]
            if len(parents) != 1:
                raise Halt('blocked', 'Payment verification is not bound to one approved transaction.')
            challenge.update(commitment_execution_id=parents[0]['execution_id'],
                             proposal_key=parents[0]['proposal_key'])
        self.task.challenges[cid] = challenge
        q = interaction.require(self.task, dict(id='secret:' + cid, kind='secret', purpose=flow['purpose'],
            keys=['secret:' + cid], scope=cid, depends_on=[], options=[], reason='Observed secret field'))
        q['text'] = 'Please send the value for ' + action['label'] + '.'
        q['challenge_id'] = cid
        challenge['question_id'] = q['id']
        key = 'login.' + urlparse(origin).netloc
        role = (action.get('context') or {}).get('field_role')
        if (flow['purpose'] == 'login' and flow['field_role'] == 'password'
                and role == 'current-password' and key in self.profile.vault_keys()
                and flow.get('rejections', 0) == 0):
            try:
                raw = self.vault.get(key)
            except (KeyError, RuntimeError):
                pass  # unavailable noninteractive vault falls back to masked chat
            else:
                ref = self.secret_store.put(raw, **self.secret_scope(challenge),
                                            expires_at=challenge['expires_at'])
                self.secrets.add(raw, ref)
                put_fact(self.task, 'secret:' + cid, ref, source='profile', scope=cid)
                self.task.requirements['secret:' + cid]['satisfied'] = True
                q.update(answered_at=time.time(), closed=True)
        return challenge


    def relay_captcha(self, page):
        rounds = self.task.progress.get('captcha_rounds', 0)
        if rounds >= 8:
            raise Halt('blocked', 'The human challenge exceeded eight rounds.')
        marks = [a for a in page['actions'] if a['kind'] == 'click' and a.get('node') is not None][:40]
        image = self.browser.marked_screenshot(marks, masked=self.secrets.nodes, hide=self.hidden_details())
        kind_reply = self.ask({'kind': 'choice', 'image': image,
            'text': 'What kind of human check is displayed?', 'options': [
                {'id': 'text', 'label': 'Type the text'}, {'id': 'tiles', 'label': 'Pick labelled tiles'},
                {'id': 'unsupported', 'label': 'Another gesture'}]})
        kind = kind_reply.get('option')
        if kind not in {'text', 'tiles'}:
            raise Halt('blocked', 'This human challenge cannot be completed through chat.')
        if not self.browser.fresh(page):
            raise StalePage('The human challenge changed')
        for previous in self.task.challenges.values():
            if previous.get('purpose') == 'captcha':
                previous['status'] = 'expired'
                old = self.task.facts.get('captcha:' + previous['id'])
                if old:
                    old['valid'] = False
        cid = uuid4().hex
        labels = [str(i) for i in range(1, len(marks) + 1)]
        challenge = dict(id=cid, kind=kind, purpose='captcha', revision=rounds,
            observation_id=next(reversed(self.task.observations)), rounds=rounds,
            tiles=labels, targets={label: a['id'] for label, a in zip(labels, marks)},
            page_fingerprint=page['fingerprint'], marks=deepcopy(marks),
            image_digest=hashlib.sha256(image.encode()).hexdigest())
        fields = [a for a in page['actions'] if a['kind'] == 'fill']
        field = fields[0] if kind == 'text' and len(fields) == 1 else None
        challenge['text_field'] = field['id'] if field else None
        challenge['structure_key'] = self.captcha_page_key(page, challenge['text_field'])
        challenge['masked_fields'] = [(field.get('frame'), field.get('node'))] if field else []
        challenge['submit_targets'] = [a['id'] for a in marks if field
            and a.get('frame') == field.get('frame')
            and ((field.get('context') or {}).get('form_node') == (a.get('context') or {}).get('form_node')
                 if (field.get('context') or {}).get('form_node') else
                 any((field.get('context') or {}).get(k) and (field.get('context') or {})[k] ==
                     (a.get('context') or {}).get(k) for k in ('form', 'section')))
            and a['label'].strip().lower() in {'verify', 'verify captcha', 'submit', 'submit captcha'}]
        image = self.captcha_image(challenge)
        challenge['image_digest'] = hashlib.sha256(image.encode()).hexdigest()
        self.task.challenges[cid] = challenge
        reply = self.ask({'kind': 'image', 'image': image, 'challenge_id': cid,
            'text': 'Type the text exactly as shown.' if kind == 'text' else 'Reply with the tile labels to select.'})
        if not self.browser.fresh(page):
            raise StalePage('The human challenge changed while you answered')
        interaction.record_captcha_answer(self.task, cid, reply.get('text', ''), revision=rounds)
        self.task.progress['captcha_rounds'] = rounds + 1
        self.state['status'] = 'ready'
        return self.snapshot()


    def auth_submit_challenge(self, action):
        """Bind a selected authentication control to one already observed field group."""
        oid = next(reversed(self.task.observations))
        context = action.get('context') or {}
        candidates = [c for c in self.task.challenges.values()
            if c.get('observation_id') == oid and c.get('revision') == self.task.revision
            and c.get('origin') == self.task.observations[oid]['origin']
            and c.get('status') in {'challenge', 'answer_ready', 'awaiting_answer'}
            and any(context.get(key) and context[key] == c.get('field_context', {}).get(key)
                    for key in ('form', 'section'))]
        if not candidates and not any(c.get('origin') == self.task.observations[oid]['origin']
                and c.get('status') in {'challenge', 'answer_ready', 'awaiting_answer'}
                for c in self.task.challenges.values()):
            return None  # requesting the first challenge is not submission of an existing secret
        if len(candidates) != 1:
            raise Halt('blocked', 'Authentication submission has no unambiguous observed challenge.')
        challenge = candidates[0]
        if action['id'] not in challenge['field_ids']:
            challenge['field_ids'].append(action['id'])
        return challenge


    def record_auth_navigation(self, receipt):
        observation = next(reversed(self.task.observations.values()))
        destination = observation['origin']
        if destination == receipt['origin'] or receipt['status'] != 'executed':
            return
        flows = [f for f in self.task.progress.get('auth_flows', {}).values()
                 if receipt['origin'] in f['allowed_origins'] and f['revision'] == self.task.revision
                 and f['status'] in {'required', 'challenge', 'submitted'}]
        if len(flows) != 1:
            return
        ids = [eid for eid, e in self.task.evidence.items() if e['observation_id'] == observation['id']
               and e['kind'] in {'page', 'browser-event'}]
        if not ids:
            return
        nav_id = receipt['execution_id'] + ':navigation'
        navigation = dict(execution_id=nav_id, phase='navigation', kind='navigation', status='executed',
            task_id=self.task.task_id, revision=self.task.revision, flow_id=flows[0]['flow_id'],
            origin=receipt['origin'], destination_origin=destination,
            observation_id=receipt['observation_id'], evidence_ids=ids)
        self.journal.append(navigation)
        self.task.executions[nav_id] = navigation
