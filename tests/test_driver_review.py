"""Final-review findings for the mini + Jev loop, each pinned: what runs is what the user approved, questions go out
only on the page mini saw, codes stay hidden and cost attempts only when typed, and failures say what happened."""

from fakes import fake_page, jev_fake, mini_with, out, script

from assistant import bench, driver
from assistant.browser import StalePage

GO = {"id": "e1", "kind": "click", "label": "Request Uber Go", "role": "button", "node": 1}
PREMIER = {"id": "e2", "kind": "click", "label": "Request Uber Premier", "role": "button", "node": 2}
WAIT = {"id": "wait", "kind": "wait", "label": "Wait", "node": None}
OTP = {"id": "e3", "kind": "fill", "label": "Enter the code", "role": "textbox", "value": "", "node": 3,
       "group": [3, 4, 5, 6, 7, 8], "constraints": {"inputmode": "numeric", "maxlength": 1}}
CVV = {"id": "e4", "kind": "fill", "label": "CVV", "role": "textbox", "value": "", "node": 9,
       "constraints": {"type": "password"}, "submit": "Pay ₹79"}
KEY_PAGE = {"id": "key_page", "kind": "key", "node": None, "role": "page", "label": "The page (focused element)"}
RIDES = "Uber Go ₹887\nPremier ₹1,219\nRequest Uber Go\nRequest Uber Premier"


def rides(tmp_path, replies=()):
    return mini_with(tmp_path, replies=replies, page=fake_page(text=RIDES, url="https://m.uber.test/",
                                                               actions=[GO, PREMIER, WAIT]))


def jev_picks(monkeypatch, action, index, confidence=0.55):
    monkeypatch.setattr(driver.jev, "ground", lambda *a: (action, confidence, [action["label"]], index))


def test_the_approval_names_the_control_that_will_be_pressed(tmp_path, monkeypatch):
    a = rides(tmp_path, replies=[{"text": "yes"}])
    script(monkeypatch, out("CLICK", "Request Uber Go", commits=True, message="Uber Go · ₹887"))
    jev_fake(monkeypatch, approvals=["yes"])
    jev_picks(monkeypatch, PREMIER, "2")
    a.command("tick")
    assert "“Request Uber Premier”" in a.asked[0]["text"]


def test_a_commit_whose_named_number_jev_contradicts_is_sent_back(tmp_path, monkeypatch):
    a = rides(tmp_path)
    script(monkeypatch, out("CLICK", "[1] Request Uber Go", commits=True, message="Uber Go · ₹887"))
    jev_fake(monkeypatch)
    jev_picks(monkeypatch, PREMIER, "2")
    a.command("tick")
    assert a.asked == [] and "[2]" in a.state["feedback"][0]


def test_questions_go_out_only_on_the_page_mini_decided_on(tmp_path, monkeypatch):
    a = rides(tmp_path, replies=[{"text": "x"}])
    a.browser.fresh.side_effect = [True, False]
    a.browser.observe.return_value = fake_page(text=RIDES, url="https://m.uber.test/", actions=[GO, WAIT])
    script(monkeypatch, out("ASK", message="Where to?"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.asked == []
    b = rides(tmp_path, replies=[{"text": "yes"}])
    b.browser.fresh.side_effect = [True, False]
    b.browser.observe.return_value = fake_page(text=RIDES, url="https://m.uber.test/", actions=[GO, WAIT])
    script(monkeypatch, out("CLICK", "Request Uber Go", commits=True, message="Uber Go · ₹887"))
    jev_fake(monkeypatch, approvals=["yes"])
    b.command("tick")
    assert b.asked == []


def test_a_yes_needs_the_users_own_words_too(tmp_path, monkeypatch):
    a = rides(tmp_path, replies=[{"text": "hmm ok but cheaper?"}])
    script(monkeypatch, out("CLICK", "Request Uber Go", commits=True, message="Uber Go · ₹887"))
    jev_fake(monkeypatch, approvals=["yes"])
    a.command("tick")
    assert a.state["approval"] is None and "did not approve" in a.state["feedback"][0]


def test_a_code_question_is_hidden_whatever_its_wording(tmp_path, monkeypatch):
    a = mini_with(tmp_path, replies=[{"text": "482193"}, {"text": "482194"}],
                  page=fake_page(text="Enter the code we texted you", actions=[OTP, WAIT]))
    script(monkeypatch, out("ASK", message="What's the code Uber texted?"),
           out("ASK", message="Which code?", options=["Resend code", "I have it"]))
    jev_fake(monkeypatch)
    a.command("tick")
    a.command("tick")
    assert [m["kind"] for m in a.asked] == ["secret", "secret"] and "482193" not in str(a.state["chat"])


def test_a_code_is_cleaned_and_counts_only_once_typed(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(text="Enter the code", actions=[OTP, WAIT]))
    a.keep_secret("48219")  # one digit short
    a.keep_secret("482 193")  # as the SMS shows it
    script(monkeypatch, out("TYPE", "Enter the code", text="[secret:answer1]"),
           out("TYPE", "Enter the code", text="[secret:answer2]"),
           out("TYPE", "Enter the code", text="[secret:answer2]"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_count == 0 and "6 characters" in a.state["feedback"][0] and a.state["typed"] == {}
    a.browser.act.side_effect = [StalePage("moved"), {}]
    a.command("tick")
    assert a.state["typed"] == {}  # the page moved before anything was typed
    a.command("tick")
    assert a.browser.act.call_args.kwargs["text"] == "482193" and list(a.state["typed"].values())[0]["count"] == 1


def test_a_captcha_is_relayed_only_when_mini_asks_and_relayed_clicks_never_commit(tmp_path, monkeypatch):
    buy = {"id": "e5", "kind": "click", "label": "Add to cart", "role": "button", "node": 5}
    pay = {"id": "e6", "kind": "click", "label": "Pay ₹79", "role": "button", "node": 6}
    a = mini_with(tmp_path, page=fake_page(text="Press and hold the button to charge", actions=[buy, pay, WAIT]))
    relayed = []
    monkeypatch.setattr(a, "relay_captcha", lambda page: relayed.append(page))
    script(monkeypatch, out("CLICK", "Add to cart"), out("ASK", message="Please solve the check."))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_args.args[1]["id"] == "e5" and relayed == []
    a.command("tick")
    assert len(relayed) == 1
    a.perform("CLICK", pay, {}, a.state["page"])
    assert a.browser.act.call_count == 1  # the user's captcha pick never presses Pay


def test_a_failure_after_the_action_ran_says_it_ran(tmp_path, monkeypatch):
    prices = {"id": "e7", "kind": "click", "label": "See prices", "role": "button", "node": 7}
    a = mini_with(tmp_path, page=fake_page(text="Where to?", actions=[prices, WAIT]))
    script(monkeypatch, out("CLICK", "See prices"))
    jev_fake(monkeypatch)

    def down(*args, **kwargs):
        raise RuntimeError("Model connection failed; no action executed.")

    monkeypatch.setattr(driver.jev, "check", down)
    a.command("tick")
    assert a.browser.act.call_count == 1 and "already ran" in a.state["feedback"][0]


def test_stopping_with_a_commit_in_flight_says_it_may_have_gone_through(tmp_path, monkeypatch):
    a = rides(tmp_path)
    a.state["commit"] = {"label": "Request Uber Go", "checks": 0, "since": 0}

    def down(payload, image=None):
        raise RuntimeError("Model connection failed; no action executed.")

    monkeypatch.setattr(driver.mind, "decide", down)
    for _ in range(5):
        a.command("tick")
    assert a.state["status"] == "failed" and "Request Uber Go may already have gone through" in a.told[-1]["text"]


def test_browser_dialogs_can_be_accepted_or_dismissed(tmp_path, monkeypatch):
    accept = {"id": "accept_dialog", "kind": "accept_dialog", "node": None, "label": "OK"}
    dismiss = {"id": "dismiss_dialog", "kind": "dismiss_dialog", "node": None, "label": "Cancel"}
    a = mini_with(tmp_path, page=fake_page(text="Browser confirm dialog: Leave this page?",
                                           actions=[accept, dismiss]))
    script(monkeypatch, out("DISMISS_DIALOG"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_args.args[:2] == ("DISMISS_DIALOG", dismiss)


def test_enter_in_a_field_whose_form_pays_needs_approval(tmp_path, monkeypatch):
    a = mini_with(tmp_path, page=fake_page(actions=[CVV, KEY_PAGE, WAIT]))
    script(monkeypatch, out("KEY", key="Enter", commits=True, message="Diet Coke 300 ml · ₹79"))
    jev_fake(monkeypatch, approvals=["no"])
    a.command("tick")
    assert a.browser.act.call_count == 0 and a.asked and "“Pay ₹79”" in a.asked[0]["text"]


def test_the_screenshot_mini_sees_hides_profile_details(tmp_path, monkeypatch):
    a = rides(tmp_path)
    a.mind_screenshot = True
    script(monkeypatch, out("WAIT"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.screenshot.call_args.kwargs["hide"] == a.hidden_details()


def test_the_bench_flags_a_final_looking_click_it_never_approved():
    spec = {"id": "form", "goal": "g", "end": "gate", "require": [], "rule": "r"}
    history = [{"operation": "CLICK", "action": "Submit order", "committed": False}]
    task = type("T", (), {"status": "cancelled", "bench": {"gate": None, "asked": []},
                          "worker": type("A", (), {"state": {"history": history, "messages": []}})()})()
    graded = bench.grade(spec, task, 1, judge=lambda *a: "pass")
    assert graded["warnings"] == ["possible commit without approval: Submit order"]
