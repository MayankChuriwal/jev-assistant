"""The real loop on a real page: scripted Jev decisions, real Chrome, real execution. Run: uv run pytest -m browser"""

import hashlib

import pytest

from assistant import agent as loop

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
    monkeypatch.setattr(loop, "field_text", lambda context: (
        "I was charged twice for the same order at Zenith.", {"model": "fake", "latency_ms": 0}))

    def provide_file(action):
        return [str(statement if "statement" in action["label"].lower() else receipt)]

    with loop.Agent(site["a"] + "/upload.html", "Dispute the ₹2,499 Zenith charge; I was charged twice.",
                    provide_file=provide_file) as agent:
        for state in agent.run():
            assert len(state["history"]) < 30
        result = agent.browser.evaluate("window.__uploadResult")
    assert agent.state["status"] == "done"
    assert result["txn"] == "T1" and result["reason"] == "charged_twice"
    assert [f["sha256"] for f in result["files"][:2]] == [
        hashlib.sha256(statement.read_bytes()).hexdigest(), hashlib.sha256(receipt.read_bytes()).hexdigest()]
