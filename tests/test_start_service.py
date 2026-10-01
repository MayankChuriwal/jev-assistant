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


def test_the_newest_saved_default_wins_over_an_older_one_that_matches_no_app(tmp_path):
    c, made, profile = core(tmp_path)
    profile.remember("Default app for groceries: Dunzo", "older goal")
    profile.remember("Default app for groceries: Zepto", "newer goal")
    c.new_task("order a diet coke")
    until(lambda: made)
    assert made[0][0] == "https://www.zeptonow.com" and c.open is None


def test_a_default_that_matches_no_app_is_asked_once_and_never_saved_twice(tmp_path):
    c, made, profile = core(tmp_path)
    profile.remember("Default app for groceries: Dunzo", "older goal")
    asked, real = [], c.ask
    c.ask = lambda task, message: asked.append(message["text"]) or real(task, message)
    for goal in ("order a diet coke", "order a sprite", "order a fanta"):
        task = c.new_task(goal)
        until(lambda: task.status == "done" or c.open is not None)
        if c.open is not None:
            c.answer(c.open, {"type": "text", "text": "zepto"})
        until(lambda: task.status == "done")
    assert asked == ["Which app should I use for groceries: Swiggy or Zepto?"]
    assert [p["text"] for p in profile.data["preferences"]] == ["Default app for groceries: Dunzo",
                                                                  "Default app for groceries: Zepto"]
    assert [m[0] for m in made] == ["https://www.zeptonow.com"] * 3


def test_two_tasks_asked_at_once_save_the_default_only_once(tmp_path):
    c, made, profile = core(tmp_path)
    c.new_task("order a diet coke")
    c.new_task("order a sprite")
    until(lambda: len(c.queue) == 2)
    for _ in range(2):
        until(lambda: c.open is not None)
        c.answer(c.open, {"type": "text", "text": "zepto"})
    until(lambda: len(made) == 2)
    assert [p["text"] for p in profile.data["preferences"]] == ["Default app for groceries: Zepto"]


def test_an_answer_naming_an_alias_resolves_to_that_site_and_saves_its_name(tmp_path):
    c, made, profile = core(tmp_path)
    c.new_task("order a diet coke")
    until(lambda: c.open is not None)
    c.answer(c.open, {"type": "text", "text": "Instamart"})
    until(lambda: made)
    assert made[0][0] == "https://www.swiggy.com/instamart" and made[0][2].startswith("Swiggy · ")
    assert [p["text"] for p in profile.data["preferences"]] == ["Default app for groceries: Swiggy"]


def test_a_default_saved_in_the_old_wording_resolves_without_asking(tmp_path):
    c, made, profile = core(tmp_path)
    profile.remember("Default app for groceries: Swiggy Instamart", "older goal")
    c.new_task("order a diet coke")
    until(lambda: made)
    assert made[0][0] == "https://www.swiggy.com/instamart" and c.open is None
    assert len(profile.data["preferences"]) == 1  # nothing new saved
