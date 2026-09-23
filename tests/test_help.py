"""The help ladder when Jev says BLOCKED. Fakes only."""

import base64

from fakes import act, agent_with, decision, fake_page

from assistant import agent as loop

META = {"model": "fake", "latency_ms": 1}


def ladder(monkeypatch, *steps):
    queue, calls = list(steps), []

    def fake(context, page, elements, must_stop=False):
        calls.append(must_stop)
        step = dict(queue.pop(0))
        return ({**step, "rung": "stop"} if must_stop else step), META

    monkeypatch.setattr(loop.jobs, "unblock", fake)
    return calls


def test_show_and_ask_sends_a_masked_crop_and_remembers_the_answer(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"text": "XK7P"}])
    a.secrets.nodes.add((None, 8))
    ladder(monkeypatch, {"rung": "show", "text": "Type the characters in this captcha.", "crop_element": "4"})
    act(a, decision("BLOCKED", "BLOCKED"))
    target = a.browser.screenshot.call_args.args[0]
    assert target["id"] == "e4" and a.browser.screenshot.call_args.kwargs["masked"] == {(None, 8)}
    assert a.asked[0]["kind"] == "image" and a.asked[0]["image"] == "iVBORw0KGgo="
    assert a.state["memory"][-1] == "User read from the screen: XK7P" and a.state["status"] == "ready"


def test_guidance_screenshots_become_hints_with_a_one_time_notice(tmp_path, monkeypatch):
    shot = tmp_path / "how.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    a = agent_with(tmp_path, replies=[{"file": str(shot)}, {"text": "Accounts, then Disputes"}])
    ladder(monkeypatch, {"rung": "guide", "text": "Where is the dispute option?", "crop_element": ""},
           {"rung": "guide", "text": "Anything else?", "crop_element": ""})
    seen = []
    monkeypatch.setattr(loop.jobs, "hint", lambda context, page, text="", image=None: seen.append(image) or (
        "Open Accounts → Disputes.", META))
    act(a, decision("BLOCKED", "BLOCKED"))
    assert seen == [base64.b64encode(shot.read_bytes()).decode()]
    assert a.state["memory"][-1] == "Hint from the user: Open Accounts → Disputes."
    assert sum("OpenAI" in m["text"] for m in a.told) == 1
    a.state["page"] = a.browser.observe.return_value = fake_page(url="https://shop.test/help")
    act(a, decision("BLOCKED", "BLOCKED"))
    assert seen[-1] is None and sum("OpenAI" in m["text"] for m in a.told) == 1


def test_device_steps_wait_for_the_user(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"text": "done"}])
    ladder(monkeypatch, {"rung": "device", "text": "Approve ₹79 in your UPI app, then reply done.", "crop_element": ""})
    act(a, decision("BLOCKED", "BLOCKED"))
    assert a.asked[0]["kind"] == "text" and a.state["memory"][-1].startswith("User finished the step")


def test_two_rounds_per_page_then_a_clean_stop(tmp_path, monkeypatch):
    a = agent_with(tmp_path, replies=[{"text": "a"}, {"text": "b"}])
    stop = {"rung": "device", "text": "Stopped at the payment page: it needs a passkey. Nothing was charged.",
            "crop_element": ""}
    calls = ladder(monkeypatch, stop, stop, stop)
    for _ in range(3):
        act(a, decision("BLOCKED", "BLOCKED"))
    assert calls == [False, False, True]
    assert a.state["status"] == "failed" and "Nothing was charged" in a.told[-1]["text"]
