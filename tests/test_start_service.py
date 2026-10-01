"""Which app: the one the goal names, the skill's saved default, or asked once and remembered. No models."""

from helpers import until

from assistant import core as conversation
from assistant.channel_web import WebChannel
from assistant.profile import Profile

SKILLS = {"groceries": {"name": "groceries", "description": "Buy groceries", "sites": ["swiggy.com", "zeptonow.com"],
                        "inputs": [], "uses": [], "summary": [], "report": [], "body": "", "situation": False}}
SITES = {"swiggy.com": {"host": "swiggy.com", "name": "Swiggy", "aliases": ["Instamart"], "body": "",
                        "start": "https://www.swiggy.com", "start.groceries": "https://www.swiggy.com/instamart"},
         "zeptonow.com": {"host": "zeptonow.com", "name": "Zepto", "aliases": [], "body": "",
                          "start": "https://www.zeptonow.com"}}


class Done:
    def __init__(self):
        self.state = {"status": "done", "memory": [], "done": [], "files": [], "history": [], "messages": []}

    def command(self, name):
        return None

    def settle(self):
        pass

    def close(self):
        pass


def core(tmp_path, starts=()):
    profile = Profile(tmp_path / "profile.json")
    made, found = [], list(starts)

    def make_worker(task, url, asker, notify):
        made.append((url, task.goal, task.title))
        return Done()

    c = conversation.Core(WebChannel(tmp_path / "media"), profile=profile, make_worker=make_worker,
                          start=lambda goal: found.pop(0) if found else ("https://www.google.com/", goal, False),
                          intake=lambda message, chat, kinds, several=True: {"several": "no", "skill": "groceries",
                                                                             "preauth": "no"},
                          library={"skills": SKILLS, "sites": SITES}, poll=0.01)
    return c, made, profile


def test_a_named_site_starts_at_its_section_for_the_skill(tmp_path):
    c, made, _ = core(tmp_path)
    c.new_task("order a diet coke on Instamart")
    until(lambda: made)
    assert made[0][0] == "https://www.swiggy.com/instamart" and made[0][2].startswith("Swiggy · ")


def test_an_unnamed_site_is_asked_once_and_remembered(tmp_path):
    c, made, profile = core(tmp_path)
    task = c.new_task("order a diet coke")
    until(lambda: c.open is not None)
    assert c.open.message["text"] == "Which app should I use for groceries: Swiggy or Zepto?"
    c.answer(c.open, {"type": "text", "text": "zepto"})
    until(lambda: made)
    assert made[0][0] == "https://www.zeptonow.com"
    assert profile.data["preferences"][-1]["text"] == "Default app for groceries: Zepto"
    until(lambda: task.status == "done")
    c.new_task("order a sprite")
    until(lambda: len(made) == 2)
    assert made[1][0] == "https://www.zeptonow.com"  # remembered: not asked again


def test_you_choose_takes_the_skills_first_site_without_saving_it(tmp_path):
    c, made, profile = core(tmp_path)
    c.new_task("order a diet coke")
    until(lambda: c.open is not None)
    c.answer(c.open, {"type": "text", "text": "you choose"})
    until(lambda: made)
    assert made[0][0] == "https://www.swiggy.com/instamart" and not profile.data["preferences"]


def test_a_site_with_no_note_is_found_by_the_start_job(tmp_path):
    c, made, _ = core(tmp_path, starts=[("https://www.bigbasket.com/", "BigBasket · Rice", True)])
    c.new_task("rice on bigbasket")
    until(lambda: made)
    assert made[0][0] == "https://www.bigbasket.com/" and c.open is None
