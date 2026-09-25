"""What mini says, asks, and reports: plain messages, numbered options read by Jev, hidden codes, proof for DONE."""

from fakes import fake_page, jev_fake, mini_with, out, script

WAIT = {"id": "wait", "kind": "wait", "label": "Wait", "node": None}


def agent(tmp_path, replies=(), text="Choose a ride\nUber Go ₹887\nPremier ₹1,219"):
    return mini_with(tmp_path, replies=replies, page=fake_page(text=text, actions=[WAIT]))


def test_options_are_a_numbered_plain_message_and_jev_reads_the_reply(tmp_path, monkeypatch):
    a = agent(tmp_path, replies=[{"text": "premier please"}])
    script(monkeypatch, out("ASK", message="Which ride?", options=["Uber Go · ₹887", "Premier · ₹1,219"]))
    jev_fake(monkeypatch, replies=["2"])
    a.command("tick")
    assert a.asked[0] == {"from": "agent", "kind": "text",
                          "text": "Which ride?\n1. Uber Go · ₹887\n2. Premier · ₹1,219"}
    assert a.state["chat"][-1] == {"from": "user", "text": "“premier please” → option 2: Premier · ₹1,219"}


def test_a_code_question_is_hidden_and_kept_as_a_placeholder(tmp_path, monkeypatch):
    a = agent(tmp_path, replies=[{"text": "4821"}])
    script(monkeypatch, out("ASK", message="Please send the 4-digit code Uber sent you."))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked[0]["kind"] == "secret" and "4821" not in str(a.state["chat"])
    assert a.state["chat"][-1]["text"].startswith("[secret:answer1]") and a.state["answers"] == {"answer1": "4821"}


def test_tell_sends_without_waiting_and_stop_ends_with_its_reason(tmp_path, monkeypatch):
    a = agent(tmp_path)
    script(monkeypatch, out("TELL", message="Uber shows 3 rides."), out("STOP", message="Uber isn't available here."))
    jev_fake(monkeypatch)
    a.command("tick")
    a.command("tick")
    assert [m["text"] for m in a.told] == ["Uber shows 3 rides.", "Uber isn't available here."]
    assert a.state["status"] == "blocked"


def test_done_needs_a_quote_from_the_page_and_jevs_agreement(tmp_path, monkeypatch):
    a = agent(tmp_path, text="Your driver Ravi is arriving\nKA 01 AB 1234\nPIN 4821")
    script(monkeypatch, out("DONE", message="Booked.", text="Your driver Sunil"),
           out("DONE", message="Booked: Ravi, KA 01 AB 1234, PIN 4821.", text="Your driver Ravi is arriving"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.state["status"] == "ready" and "quote" in a.state["feedback"][0]
    a.command("tick")
    assert a.state["status"] == "done" and a.told[-1]["kind"] == "result"
    assert a.told[-1]["evidence"] == "Your driver Ravi is arriving"


def test_done_is_refused_when_jev_says_the_page_does_not_show_it(tmp_path, monkeypatch):
    a = agent(tmp_path, text="Finding drivers nearby")
    script(monkeypatch, out("DONE", message="Booked.", text="Finding drivers nearby"))
    jev_fake(monkeypatch, achieved="no")
    a.command("tick")
    assert a.state["status"] == "ready" and "doesn't show the goal done" in a.state["feedback"][0]


def test_a_question_waits_while_the_page_is_still_changing(tmp_path, monkeypatch):
    a = agent(tmp_path, replies=[{"text": "x"}])
    a.steady_ms = 300
    a.browser.fresh.side_effect = [True, False, True, True, True]
    a.state["history"].append({"operation": "CLICK", "action": "Continue", "executed_ms": a.elapsed(),
                               "url": "https://shop.test/"})
    script(monkeypatch, out("ASK", message="What's the code?"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == []
