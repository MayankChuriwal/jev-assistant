"""Which app: asked once per kind of task, then remembered. Scripted start job, no models."""

from helpers import until

from assistant import core as conversation
from assistant.channel_web import WebChannel
from assistant.profile import Profile


class Done:
    def __init__(self):
        self.state = {"status": "done", "memory": [], "files": [], "history": [], "messages": []}

    def command(self, name):
        return None

    def settle(self):
        pass

    def close(self):
        pass


def core(tmp_path, starts):
    profile = Profile(tmp_path / "profile.json")
    made = []

    def make_agent(url, goal, title, asker, notify):
        made.append((url, goal))
        return Done()

    c = conversation.Core(WebChannel(tmp_path / "media"), profile=profile, make_agent=make_agent,
                          start=lambda goal: starts[0 if "(on " not in goal else 1], poll=0.01)
    return c, made, profile


GROCERY = ("https://www.swiggy.com/instamart", "Diet Coke",
           {"kind": "groceries", "service_named": False, "services": ["Swiggy Instamart", "Zepto", "Blinkit"]})
ZEPTO = ("https://www.zeptonow.com/", "Zepto · Diet Coke", {"kind": "groceries", "service_named": True,
                                                            "services": []})


def test_an_unnamed_service_is_asked_once_and_remembered(tmp_path):
    c, made, profile = core(tmp_path, [GROCERY, ZEPTO])
    task = c.new_task("order a diet coke")
    until(lambda: c.open is not None)
    assert c.open.message["text"] == "Which app should I use for groceries: Swiggy Instamart, Zepto or Blinkit?"
    c.answer(c.open, {"type": "text", "text": "zepto"})
    until(lambda: made)
    assert made[0][0] == "https://www.zeptonow.com/"
    assert profile.data["preferences"][-1]["text"] == "Default app for groceries: Zepto"
    until(lambda: task.status == "done")
    c.new_task("order a sprite")
    until(lambda: len(made) == 2)
    assert made[1][0] == "https://www.zeptonow.com/"  # remembered: not asked again
