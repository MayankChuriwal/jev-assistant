"""Signing in comes first on any site that offers it: the account holds saved addresses and payment. Fakes only."""

from fakes import agent_with, decision, fake_page

from assistant import agent as loop

SIGN_IN = {"id": "e9", "kind": "click", "label": "Sign In", "role": "button", "node": 13}
WAIT = {"id": "wait", "kind": "wait", "label": "Wait", "node": None}


def test_a_site_that_offers_sign_in_is_signed_in_to_first(tmp_path):
    a = agent_with(tmp_path, page=fake_page(text="Enter your delivery location", actions=[SIGN_IN, WAIT]))
    a.track_login(a.state["page"])
    assert a.context().split("\n")[1].startswith("Current step: sign in to shop.test first.")
    a.state["history"].append({"operation": "TYPE_TEXT", "text": "[secret:answer1]"})  # the code was typed
    a.track_login(fake_page(text="Saved addresses: Home, Office", actions=[WAIT]))
    assert "Current step" not in a.context() and a.state["memory"][-1] == "Signed in to shop.test."


def test_sign_in_stays_the_next_step_until_the_site_shows_it_is_done(tmp_path):
    a = agent_with(tmp_path, page=fake_page(actions=[SIGN_IN, WAIT]))
    a.track_login(a.state["page"])
    a.track_login(fake_page(actions=[WAIT]))  # a page without the button, but nothing was typed to sign in
    assert "Current step: sign in to shop.test first." in a.context()


def test_google_and_apple_buttons_are_not_the_sign_in_to_use(tmp_path):
    a = agent_with(tmp_path, page=fake_page(actions=[{**SIGN_IN, "label": "Continue with Google"}, WAIT]))
    a.track_login(a.state["page"])
    assert "Current step" not in a.context()


def test_the_sign_in_step_reaches_jev(tmp_path, monkeypatch):
    goals = []
    monkeypatch.setattr(loop, "choose", lambda page, goal, history: goals.append(goal) or decision("wait", "WAIT"))
    a = agent_with(tmp_path, page=fake_page(actions=[SIGN_IN, WAIT]))
    a.command("predict")
    assert "Current step: sign in to shop.test first." in goals[0]


CODE = {"id": "e2", "kind": "fill", "label": "One time password", "role": "textbox", "value": "", "node": 2}


def test_typing_the_phone_number_is_not_yet_signing_in(tmp_path):
    a = agent_with(tmp_path, page=fake_page(actions=[SIGN_IN, WAIT]))
    a.track_login(a.state["page"])
    a.state["history"].append({"operation": "TYPE_TEXT", "text": "98765 43210"})
    a.track_login(fake_page(text="Enter OTP", actions=[CODE, WAIT]))  # the code screen has no sign-in button
    assert "Current step: sign in to shop.test first." in a.context() and "Signed in" not in str(a.state["memory"])
