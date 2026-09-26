"""Jev's jobs: one TypeSafe choice question each. Fake HTTP only."""

from fakes import fake_page

from assistant import jev

CLICKS = [{"id": "e1", "kind": "click", "label": "Pickup location", "role": "combobox", "node": 1},
          {"id": "e2", "kind": "click", "label": "Terminal 1, Kempegowda Airport", "role": "option", "node": 2},
          {"id": "wait", "kind": "wait", "node": None, "label": "Wait"}]


def answers(monkeypatch, name, choice, probabilities):
    sent = []

    def post(url, key, body):
        sent.append(body)
        return {"answers": {name: {"choice": choice, "probabilities": probabilities, "confidence": 0.9}},
                "model": "jev-latest"}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(jev, "post_json", post)
    return sent


def test_grounding_picks_the_described_element(monkeypatch):
    sent = answers(monkeypatch, "target", "2", {"1": 0.1, "2": 0.9})
    action, confidence, near, index = jev.ground(fake_page(actions=CLICKS), "CLICK", "Terminal 1 suggestion",
                                                 "Pick the airport", "Book a cab")
    assert action["id"] == "e2" and confidence == 0.9 and index == "2"
    assert near[0].startswith("Terminal 1")
    question = sent[0]["questions"]["target"]
    assert set(question["criteria"]) == {"1", "2"} and "Terminal 1 suggestion" in question["instructions"]["rules"]


def test_a_named_number_that_jev_agrees_with_is_trusted(monkeypatch):
    answers(monkeypatch, "target", "2", {"1": 0.45, "2": 0.55})
    _, confidence, _, _ = jev.ground(fake_page(actions=CLICKS), "CLICK", "[2] Terminal 1", "Pick it", "Book a cab")
    assert confidence == 1.0


def test_no_candidates_means_no_element(monkeypatch):
    answers(monkeypatch, "target", "1", {"1": 1.0})
    assert jev.ground(fake_page(actions=CLICKS), "UPLOAD", "Statement", "Attach", "x") == (None, 0.0, [], None)


def test_result_checks_and_reply_reading(monkeypatch):
    answers(monkeypatch, "result", "processing", {"confirmed": 0.1, "failed": 0.0, "processing": 0.8,
                                                  "next_step": 0.0, "unclear": 0.1})
    assert jev.check(fake_page(), "Request the ride", "CLICK Request", "new text: Finding drivers",
                     after_commit=True) == "processing"
    answers(monkeypatch, "reply", "2", {"1": 0.1, "2": 0.8, "other": 0.1})
    assert jev.read_reply("Which ride?", ["Uber Go · ₹887", "Premier · ₹1,219"], "premier please") == "2"
    sent = answers(monkeypatch, "approval", "change", {"yes": 0.1, "no": 0.1, "change": 0.8})
    assert jev.approval("Book Uber Go ₹887?", "hmm ok but cheaper?") == "change"
    assert "hmm ok but cheaper?" in sent[0]["state"]["page"]["text"]
