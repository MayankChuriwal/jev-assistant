"""The mini + Jev loop on a mock browser: decide, ground, execute, check, feed back. No models, no Chrome."""

from fakes import fake_page, jev_fake, mini_with, out, script

PICKUP = {"id": "e1", "kind": "click", "label": "Pickup location", "role": "combobox", "node": 1}
WHERE = {"id": "e2", "kind": "fill", "label": "Where to?", "role": "textbox", "value": "", "node": 2}
WAIT = {"id": "wait", "kind": "wait", "label": "Wait", "node": None}


def ride(tmp_path, **kw):
    return mini_with(tmp_path, page=fake_page(text="Book a ride", url="https://ride.test/",
                                              actions=[PICKUP, WHERE, WAIT]), **kw)


def acted(a):
    return [(c.args[0], c.args[1]["id"]) for c in a.browser.act.call_args_list]


def test_mini_decides_jev_grounds_and_the_result_is_checked(tmp_path, monkeypatch):
    a = ride(tmp_path)
    seen = script(monkeypatch, out("CLICK", "Pickup location", next_goal="Open pickup"))
    jev_fake(monkeypatch, checks=("success",))
    a.command("tick")
    assert acted(a) == [("CLICK", "e1")]
    assert a.state["history"][-1]["check"] == "success" and a.state["history"][-1]["changed"] == "nothing changed"
    assert seen[0]["page"]["elements"][0] == '[1] combobox "Pickup location"' and seen[0]["goal"]


def test_what_happened_reaches_the_next_decision(tmp_path, monkeypatch):
    a = ride(tmp_path)
    seen = script(monkeypatch, out("CLICK", "Pickup location"), out("WAIT"))
    jev_fake(monkeypatch, checks=("failed",))
    a.command("tick")
    a.command("tick")
    last = seen[1]["steps"][-1]
    assert last["operation"] == "CLICK" and last["check"] == "failed" and last["changed"] == "nothing changed"


def test_an_element_jev_cannot_find_is_described_again_then_looked_for(tmp_path, monkeypatch):
    a = ride(tmp_path)
    seen = script(monkeypatch, out("CLICK", "Blue map pin"), out("CLICK", "Blue map pin on the map"))
    jev_fake(monkeypatch)
    monkeypatch.setattr(a, "look", lambda page, purpose: {"point": [10, 20], "label": "map pin", "text": "",
                                                          "commits": False})
    a.command("tick")
    assert a.browser.act.call_count == 0 and "couldn't tell which element" in a.state["feedback"][0]
    a.command("tick")
    assert "couldn't tell which element" in seen[1]["feedback"][0]  # mini saw why, then described it again
    assert a.browser.act.call_args.args[0] == "LOOK" and a.browser.act.call_args.kwargs["point"] == [10, 20]


def test_model_errors_are_feedback_then_a_clean_stop(tmp_path, monkeypatch):
    from assistant import driver

    a = ride(tmp_path)

    def down(payload):
        raise RuntimeError("Model connection failed; no action executed.")

    monkeypatch.setattr(driver.mind, "decide", lambda payload, image=None: down(payload))
    for _ in range(4):
        a.command("tick")
    assert a.state["status"] == "ready" and "failed" in a.state["feedback"][-1]
    a.command("tick")
    assert a.state["status"] == "failed" and "stopped answering" in a.told[-1]["text"]


def test_caps_stop_the_task(tmp_path, monkeypatch):
    a = ride(tmp_path)
    a.state["spend"] = 0.51
    script(monkeypatch, out("WAIT"))
    a.command("tick")
    assert a.state["status"] == "blocked" and "$0.50" in a.told[-1]["text"]


def test_wait_back_and_open_url_need_no_grounding(tmp_path, monkeypatch):
    a = ride(tmp_path)
    script(monkeypatch, out("WAIT"), out("OPEN_URL", text="not a url"))
    jev_fake(monkeypatch)
    a.command("tick")
    a.command("tick")
    assert acted(a) == [("WAIT", "wait")] and "full http" in a.state["feedback"][0]


OTP = {"id": "e3", "kind": "fill", "label": "Enter the 4-digit code", "role": "textbox", "value": "", "node": 3,
       "group": [3, 4, 5, 6]}
PHONE = {"id": "e4", "kind": "fill", "label": "Mobile number", "role": "textbox", "value": "", "node": 7,
         "constraints": {"type": "tel"}}


def login(tmp_path):
    return mini_with(tmp_path, page=fake_page(text="Log in", url="https://ride.test/login",
                                              actions=[PHONE, OTP, WAIT]))


def test_profile_placeholders_are_typed_as_values_and_logged_as_placeholders(tmp_path, monkeypatch):
    a = login(tmp_path)
    script(monkeypatch, out("TYPE", "Mobile number", text="[profile:phone]"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_args.kwargs["text"] == "+91 98765 43210"
    assert a.state["history"][-1]["text"] == "[profile:phone]"


def test_a_code_field_takes_only_the_users_hidden_answer(tmp_path, monkeypatch):
    a = login(tmp_path)
    script(monkeypatch, out("TYPE", "4-digit code", text="1234"), out("TYPE", "4-digit code", text="[secret:answer1]"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_count == 0 and "ASK the user" in a.state["feedback"][0]
    a.keep_secret("4821")
    a.command("tick")
    assert a.browser.act.call_args.kwargs["text"] == "4821" and a.state["history"][-1]["text"] == "[secret:answer1]"
    assert (None, 3) in a.secrets.nodes  # the boxes are masked in every later read


def test_the_same_code_is_never_typed_twice_and_three_rejections_stop(tmp_path, monkeypatch):
    a = login(tmp_path)
    for code in ("1111", "2222", "3333", "4444"):
        a.keep_secret(code)
    script(monkeypatch, *[out("TYPE", "4-digit code", text=f"[secret:answer{n}]") for n in (1, 1, 2, 3, 4)])
    jev_fake(monkeypatch)
    for _ in range(5):
        a.command("tick")
    assert [c.kwargs["text"] for c in a.browser.act.call_args_list] == ["1111", "2222", "3333"]
    assert a.state["status"] == "blocked" and "rejected 3 codes" in a.told[-1]["text"]


def test_an_unknown_placeholder_is_refused(tmp_path, monkeypatch):
    a = login(tmp_path)
    script(monkeypatch, out("TYPE", "Mobile number", text="[secret:answer9]"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert a.browser.act.call_count == 0 and "isn't one of the user's answers" in a.state["feedback"][0]


def test_repeating_a_useless_action_nudges_then_asks_the_user(tmp_path, monkeypatch):
    a = ride(tmp_path, replies=[{"text": "scroll down a bit"}])
    script(monkeypatch, *[out("CLICK", "Pickup location", next_goal="Open pickup")] * 8)
    jev_fake(monkeypatch, checks=("failed",))
    for _ in range(8):
        a.command("tick")
    assert a.state["same_nudges"] == [3, 5]
    assert a.asked[-1]["text"].startswith("I'm stuck") and a.state["chat"][-1]["text"] == "“scroll down a bit”"


def test_the_trace_keeps_minis_reasoning_and_jevs_checks(tmp_path, monkeypatch):
    a = ride(tmp_path)
    script(monkeypatch, out("CLICK", "Pickup location", next_goal="Open pickup", plan=["Set pickup", "Set drop"]))
    jev_fake(monkeypatch, checks=("success",))
    a.command("tick")
    trace = a.trace()
    assert trace["driver"] == "mini" and trace["plan"] == ["Set pickup", "Set drop"]
    assert trace["mini"][0]["next_goal"] == "Open pickup" and trace["steps"][0]["check"] == "success"


def test_the_trace_is_saved_after_every_step(tmp_path, monkeypatch):
    import json

    a = ride(tmp_path)
    a.trace_dir = tmp_path / "traces"
    script(monkeypatch, out("CLICK", "Pickup location"), out("WAIT"))
    jev_fake(monkeypatch)
    a.command("tick")
    first = a.state["trace_path"]
    a.command("tick")
    assert a.state["trace_path"] == first and len(json.loads(open(first).read())["steps"]) == 2


def test_mini_knows_todays_date(tmp_path, monkeypatch):
    import time

    a = ride(tmp_path)
    seen = script(monkeypatch, out("WAIT"))
    jev_fake(monkeypatch)
    a.command("tick")
    assert seen[0]["today"] == time.strftime("%A, %d %B %Y")


def test_being_stuck_never_shows_mini_s_note_about_the_user(tmp_path, monkeypatch):
    a = ride(tmp_path, replies=[{"text": "Indiranagar"}])
    script(monkeypatch, *[out("CLICK", "Pickup location", next_goal="Ask user for their delivery area")] * 8)
    jev_fake(monkeypatch, checks=("failed",))
    for _ in range(8):
        a.command("tick")
    assert a.asked and "Ask user" not in a.asked[-1]["text"] and a.state["goal"] in a.asked[-1]["text"]


def test_a_hover_jev_cannot_find_is_looked_for_and_only_hovers(tmp_path, monkeypatch):
    a = ride(tmp_path)
    script(monkeypatch, out("HOVER", "Sort by"), out("HOVER", "Sort by : Recommended"))
    jev_fake(monkeypatch)
    monkeypatch.setattr(a, "look", lambda page, purpose: {"point": [10, 20], "label": "Sort by", "text": "",
                                                          "commits": False})
    a.command("tick")
    a.command("tick")
    assert a.browser.act.call_args.args[0] == "LOOK" and a.browser.act.call_args.kwargs.get("hover") is True
