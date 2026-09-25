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
