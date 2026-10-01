"""The chat core with a real Worker over a scripted browser and scripted models: one order end to end."""

from unittest.mock import Mock

from fakes import answers_for, fake_page, jev_checks, script_planner, sub, turn
from helpers import until

from assistant import core as conversation
from assistant import jev
from assistant.channel_web import WebChannel
from assistant.profile import Profile
from assistant.worker import Worker


def test_an_order_is_planned_approved_in_chat_and_reported(tmp_path, monkeypatch):
    page = fake_page()
    browser = Mock(fresh=Mock(return_value=True), observe=Mock(return_value=page), act=Mock(return_value={}),
                   focused_label=Mock(return_value=""), downloads=[], pending_downloads=[])
    script_planner(monkeypatch, turn(subgoals=[sub("choose Office"), sub("pay", kind="commit")]),
                   turn("DONE", message="Order placed for ₹79.", text="Pay ₹79"))
    steps = [{"next": "CLICK", "click_target": "Office"}, {"next": "DONE"}, {"next": "CLICK", "click_target": "Pay"}]
    monkeypatch.setattr(jev, "request",
                        lambda state, questions: answers_for(questions, steps.pop(0) if len(steps) > 1 else steps[0]))
    jev_checks(monkeypatch)
    channel = WebChannel(tmp_path / "media")
    profile = Profile(tmp_path / "profile.json")

    def make_worker(task, url, asker, notify):
        return Worker(url, task.goal, asker=asker, notify=notify, profile=profile, browser=browser,
                      trace_dir=tmp_path / "traces", skill=task.skill, preauth=task.preauth, cap=task.cap)

    routes = [("NEW_TASK", "none", {"NEW_TASK": 1.0}, {"none": 1.0}), ("ANSWER", "t1", {"ANSWER": 1.0}, {"t1": 1.0})]
    chat = conversation.Core(channel, profile=profile, make_worker=make_worker, poll=0.01,
                             route=lambda text, tasks, recent: routes.pop(0),
                             intake=lambda message, kinds, several=True: {"several": "no", "skill": "general",
                                                                          "preauth": "no"},
                             start=lambda goal: ("https://shop.test/checkout", "Shop · Diet Coke", True))
    chat.receive({"from": "local", "id": "m1", "type": "text", "text": "Order Diet Coke", "context": None})
    assert until(lambda: chat.open is not None, timeout=5)
    assert "I'll press “Pay ₹79”" in chat.open.message["text"]
    chat.receive({"from": "local", "id": "m2", "type": "text", "text": "yes", "context": None})
    assert until(lambda: chat.tasks["t1"].status == "done", timeout=5)
    texts = [m["text"]["body"] for m in channel.since(0)[0] if m["type"] == "text"]
    assert any("Order placed for ₹79." in t for t in texts) and browser.act.call_count == 2
