"""Nothing irreversible without a clear yes; after a commit, watch the site until it confirms or fails."""

from fakes import fake_page, jev_fake, mini_with, out, script

REQUEST = {"id": "e1", "kind": "click", "label": "Request Uber Go", "role": "button", "node": 1}
WAIT = {"id": "wait", "kind": "wait", "label": "Wait", "node": None}
SUMMARY = "Uber Go · Sunnyside Elm → Airport T1 · Cash · ₹887"


def rides(tmp_path, replies=()):
    a = mini_with(tmp_path, replies=replies, page=fake_page(text="Uber Go ₹887\nCash\nRequest Uber Go",
                                                            url="https://m.uber.test/", actions=[REQUEST, WAIT]))
    a.state["chat"] += [{"from": "agent", "text": "Which payment method: Cash or UPI?"},
                        {"from": "user", "text": "“Cash”"}]  # payment was already the user's pick
    return a


def test_a_commit_asks_with_mini_summary_and_a_yes_reads_the_page_again(tmp_path, monkeypatch):
    a = rides(tmp_path, replies=[{"text": "yes book it"}])
    script(monkeypatch, out("CLICK", "Request Uber Go", commits=True, message=SUMMARY),
           out("CLICK", "Request Uber Go", commits=True, message=SUMMARY))
    jev_fake(monkeypatch, approvals=["yes"], checks=("confirmed",))
    a.command("tick")
    shown = SUMMARY.replace(" · ", "\n• ", 1).replace(" · ", "\n• ")  # one fact per line
    assert a.browser.act.call_count == 0 and a.asked[0]["approval"] is True and shown in a.asked[0]["text"]
    assert a.state["approval"]["amount"] == "₹887"
    a.command("tick")
    assert a.browser.act.call_count == 1 and a.state["history"][-1]["committed"] is True
    assert a.state["commit"] is None and a.state["memory"][-1].startswith("Done: Request Uber Go")


def test_an_unclear_reply_is_not_a_yes(tmp_path, monkeypatch):
    a = rides(tmp_path, replies=[{"text": "hmm ok but cheaper?"}])
    script(monkeypatch, out("CLICK", "Request Uber Go", commits=True, message=SUMMARY))
    jev_fake(monkeypatch, approvals=["change"])
    a.command("tick")
    assert a.browser.act.call_count == 0 and a.state["approval"] is None
    assert "did not approve" in a.state["feedback"][0] and "cheaper" in a.state["chat"][-1]["text"]


def test_an_amount_the_page_does_not_show_is_sent_back_to_mini(tmp_path, monkeypatch):
    a = rides(tmp_path)
    script(monkeypatch, out("CLICK", "Request Uber Go", commits=True, message="Uber Go · ₹850"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == [] and "₹850" in a.state["feedback"][0]


def test_a_commit_without_a_summary_is_sent_back(tmp_path, monkeypatch):
    a = rides(tmp_path)
    script(monkeypatch, out("CLICK", "Request Uber Go", commits=False, message=""))
    jev_fake(monkeypatch)
    a.command("tick")  # the label alone ("Request Uber") makes it a commit
    assert a.asked == [] and "summary" in a.state["feedback"][0]


def test_processing_is_watched_then_confirmed(tmp_path, monkeypatch):
    a = rides(tmp_path, replies=[{"text": "yes"}])
    script(monkeypatch, out("CLICK", "Request Uber Go", commits=True, message=SUMMARY),
           out("CLICK", "Request Uber Go", commits=True, message=SUMMARY))
    jev_fake(monkeypatch, approvals=["yes"], checks=("processing", "processing", "confirmed"))
    a.command("tick")
    a.command("tick")
    assert [m["text"] for m in a.told].count("Request Uber Go: the site is still working on it. I'll tell you as "
                                             "soon as it's confirmed.") == 1
    assert a.state["commit"] is None and a.state["memory"][-1].startswith("Done: Request Uber Go")


def test_processing_too_long_or_unclear_twice_stops_the_task(tmp_path, monkeypatch):
    a = rides(tmp_path, replies=[{"text": "yes"}])
    a.processing_ms = 0
    script(monkeypatch, out("CLICK", "Request Uber Go", commits=True, message=SUMMARY),
           out("CLICK", "Request Uber Go", commits=True, message=SUMMARY))
    jev_fake(monkeypatch, approvals=["yes"], checks=("processing",))
    a.command("tick")
    a.command("tick")
    assert a.state["status"] == "blocked" and "still shows it in progress" in a.told[-1]["text"]
