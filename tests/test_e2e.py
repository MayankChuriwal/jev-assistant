"""The real loop on a real page: scripted Jev decisions, real Chrome, real execution. Run: uv run pytest -m browser"""

import hashlib

import pytest
from reliability_fakes import assessment, current_evidence, fixture_agent_class, terminal_bindings

from assistant import agent as loop
from assistant import questions
from assistant.profile import Profile

pytestmark = pytest.mark.browser

STEPS = [
    ("SELECT", "ZENITH", "select"),
    ("CLICK", "Charged twice", "click"),
    ("TYPE_TEXT", "What happened?", "fill"),
    ("UPLOAD", "Card statement", "upload"),
    ("UPLOAD", "Drop zone", "upload"),
    ("CLICK", "Submit dispute", "click"),
]


def decision(operation, choice):
    return {"operation": operation, "choice": choice, "target": None, "key": None, "drop": None,
            "confidence": 1.0, "probabilities": {choice: 1.0}, "latency_ms": 0, "usage": {}}


def scripted(page, goal, history):
    """A stand-in for Jev: the next unfinished step, scrolling when its control is below the fold."""
    done = [h for h in history if h["operation"] not in {"SCROLL_DOWN", "WAIT"}]
    if len(done) < len(STEPS):
        operation, label, kind = STEPS[len(done)]
        action = next((a for a in page["actions"] if label in a["label"] and a["kind"] == kind), None)
        if action:
            return decision(operation, action["id"])
        return decision("SCROLL_DOWN", "page_scroll_down")
    if "Dispute submitted" in page["text"]:
        return decision("DONE", "DONE")
    return decision("WAIT", "wait")


def test_one_goal_fills_uploads_and_submits_a_dispute(chrome, site, tmp_path, monkeypatch):
    statement = tmp_path / "statement.pdf"
    statement.write_bytes(b"%PDF-1.4 statement")
    receipt = tmp_path / "receipt.png"
    receipt.write_bytes(b"\x89PNG\r\n\x1a\n receipt")
    monkeypatch.setattr(loop, "choose", scripted)
    monkeypatch.setattr(loop, "field_value", lambda context: (
        ("text", "I was charged twice for the same order at Zenith."), {"model": "fake", "latency_ms": 0}))
    def text(system, payload, **kwargs):
        if system.startswith('Classify the external effect'):
            return {'effect': 'commitment' if payload['selected_action']['label'] == 'Submit dispute'
                    else 'preparation'}, {}
        if system == questions.COORDINATE:
            task = payload['task']
            ids = current_evidence(task)
            out = assessment()
            if payload['event'] == 'start':
                out['completion'] = [dict(id='result', description='Submit ZENITH dispute', kind='action',
                    material_keys=['merchant'], evidence_ids=[], execution_id=None, artifact_id=None)]
            if 'ZENITH' in payload['untrusted_page']['text']:
                out['facts'] = [dict(key='merchant', value='ZENITH', source='page', scope='task', revision=0,
                                    depends_on=[], evidence=ids, valid=True)]
            if payload['event'] == 'confirmation':
                out['proposal'] = dict(task_id=task['task_id'], revision=0, action='Submit dispute',
                    service='Northwind Bank', account='', destination='',
                    items=['ZENITH ELECTRONICS', 'Charged twice',
                           'I was charged twice for the same order at Zenith.'], option='', timing='',
                    payment='', currency=None, total_minor=None, total_kind='not_applicable', later_terms=[],
                    lines=[], evidence_ids=ids)
            if payload['event'] == 'outcome':
                events = [eid for eid in ids if task['evidence'][eid]['kind'] == 'browser-event']
                success = any('submitted' in task['evidence'][eid]['quote'] for eid in events)
                out['outcome'] = dict(status='success' if success else 'processing', evidence_ids=events,
                                      artifact_id=None)
            return out, {}
        if system == questions.REVIEW_AMOUNTS:
            return {}, {}
        if system == questions.COMPLETION_PROOF:
            return terminal_bindings(payload), {}
        raise AssertionError(system[:80])
    monkeypatch.setattr(loop.jobs, 'text_json', text)

    def provide_file(action):
        return [str(statement if "statement" in action["label"].lower() else receipt)]

    with fixture_agent_class()(site["a"] + "/upload.html", "Dispute the ₹2,499 Zenith charge; I was charged twice.",
                    provide_file=provide_file, asker=lambda message: {"option": "yes"},
                    profile=Profile(tmp_path / "profile.json")) as agent:
        agent.coordinator_client = text
        agent.outcome_pause = 0.15  # Let the browser's 1.2-second upload finish within the bounded polling window.
        for state in agent.run():
            assert len(state["history"]) < 30
        result = agent.browser.evaluate("window.__uploadResult")
    assert agent.state["status"] == "done", (agent.state["messages"], agent.task.executions,
        agent.task.completion, getattr(agent.browser, 'receipt', None), result)
    assert result["txn"] == "T1" and result["reason"] == "charged_twice"
    assert [f["sha256"] for f in result["files"][:2]] == [
        hashlib.sha256(statement.read_bytes()).hexdigest(), hashlib.sha256(receipt.read_bytes()).hexdigest()]
