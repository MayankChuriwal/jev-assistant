"""Which app: the one the goal names, the skill's saved default, or asked once and remembered. No models."""

from fakes import store
from helpers import until

from assistant import core as conversation
from assistant.channel_web import WebChannel

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
    memory = store()
    made, found = [], list(starts)

    def make_worker(task, url, asker, notify):
        made.append((url, task.goal, task.title, (task.habit or {}).get("id"), task.sameness))
        return Done()

    c = conversation.Core(WebChannel(tmp_path / "media"), memory=memory, make_worker=make_worker,
                          start=lambda goal: found.pop(0) if found else ("https://www.google.com/", goal, False),
                          intake=lambda message, kinds, several=True: {"several": "no", "skill": "groceries",
                                                                       "preauth": "no"},
                          library={"skills": SKILLS, "sites": SITES}, poll=0.01, learn=lambda state, memory: None)
    return c, made, memory


def defaults(memory):
    return [f["text"] for f in memory.facts("default_app")]


def saved_rows(memory):
    return memory.run(lambda cur: cur.execute("SELECT count(*) AS n FROM facts WHERE kind = 'default_app'")
                      .fetchone()["n"])


def test_a_named_site_starts_at_its_section_for_the_skill(tmp_path):
    c, made, _ = core(tmp_path)
    c.new_task("order a diet coke on Instamart")
    until(lambda: made)
    assert made[0][0] == "https://www.swiggy.com/instamart" and made[0][2].startswith("Swiggy · ")


def test_an_unnamed_site_is_asked_once_and_remembered(tmp_path):
    c, made, memory = core(tmp_path)
    task = c.new_task("order a diet coke")
    until(lambda: c.open is not None)
    assert c.open.message["text"] == "Which app should I use for groceries: Swiggy or Zepto?"
    c.answer(c.open, {"type": "text", "text": "zepto"})
    until(lambda: made)
    assert made[0][0] == "https://www.zeptonow.com" and defaults(memory) == ["Zepto"]
    until(lambda: task.status == "done")
    c.new_task("order a sprite")
    until(lambda: len(made) == 2)
    assert made[1][0] == "https://www.zeptonow.com"  # remembered: not asked again


def test_you_choose_takes_the_skills_first_site_without_saving_it(tmp_path):
    c, made, memory = core(tmp_path)
    c.new_task("order a diet coke")
    until(lambda: c.open is not None)
    c.answer(c.open, {"type": "text", "text": "you choose"})
    until(lambda: made)
    assert made[0][0] == "https://www.swiggy.com/instamart" and defaults(memory) == []


def test_a_site_with_no_note_is_found_by_the_start_job(tmp_path):
    c, made, _ = core(tmp_path, starts=[("https://www.bigbasket.com/", "BigBasket · Rice", True)])
    c.new_task("rice on bigbasket")
    until(lambda: made)
    assert made[0][0] == "https://www.bigbasket.com/" and c.open is None


def test_a_saved_default_is_used_without_asking(tmp_path):
    c, made, memory = core(tmp_path)
    memory.remember("default_app", "Zepto", label="groceries", tags=["groceries"], supersede=True)
    c.new_task("order a diet coke")
    until(lambda: made)
    assert made[0][0] == "https://www.zeptonow.com" and c.open is None


def test_a_default_that_matches_no_app_is_asked_once_replaced_and_never_saved_twice(tmp_path):
    c, made, memory = core(tmp_path)
    memory.remember("default_app", "Dunzo", label="groceries", tags=["groceries"], supersede=True)
    asked, real = [], c.ask
    c.ask = lambda task, message: asked.append(message["text"]) or real(task, message)
    for goal in ("order a diet coke", "order a sprite", "order a fanta"):
        task = c.new_task(goal)
        until(lambda: task.status == "done" or c.open is not None)
        if c.open is not None:
            c.answer(c.open, {"type": "text", "text": "zepto"})
        until(lambda: task.status == "done")
    assert asked == ["Which app should I use for groceries: Swiggy or Zepto?"]
    assert defaults(memory) == ["Zepto"] and saved_rows(memory) == 2  # Dunzo, superseded; Zepto, once
    assert [m[0] for m in made] == ["https://www.zeptonow.com"] * 3


def test_two_tasks_asked_at_once_save_the_default_only_once(tmp_path):
    c, made, memory = core(tmp_path)
    c.new_task("order a diet coke")
    c.new_task("order a sprite")
    until(lambda: len(c.queue) == 2)
    for _ in range(2):
        until(lambda: c.open is not None)
        c.answer(c.open, {"type": "text", "text": "zepto"})
    until(lambda: len(made) == 2)
    assert defaults(memory) == ["Zepto"] and saved_rows(memory) == 1


def test_an_answer_naming_an_alias_resolves_to_that_site_and_saves_its_name(tmp_path):
    c, made, memory = core(tmp_path)
    c.new_task("order a diet coke")
    until(lambda: c.open is not None)
    c.answer(c.open, {"type": "text", "text": "Instamart"})
    until(lambda: made)
    assert made[0][0] == "https://www.swiggy.com/instamart" and made[0][2].startswith("Swiggy · ")
    assert defaults(memory) == ["Swiggy"]


def test_a_default_saved_in_the_old_wording_resolves_without_asking(tmp_path):
    c, made, memory = core(tmp_path)
    memory.remember("default_app", "Swiggy Instamart", label="groceries", tags=["groceries"], supersede=True)
    c.new_task("order a diet coke")
    until(lambda: made)
    assert made[0][0] == "https://www.swiggy.com/instamart" and c.open is None
    assert saved_rows(memory) == 1  # nothing new saved


# ---- Plan 7 Task 8: a goal that repeats a habit


def sure(habit_id, p=0.9):
    return ({"choice": str(habit_id), "probabilities": {str(habit_id): p, "none": round(1 - p, 3)}, "confidence": p},
            {"choice": "same", "probabilities": {"same": 0.9, "similar": 0.1}, "confidence": 0.9})


def saved_habit(memory):
    return memory.record_habit({"host": "zeptonow.com", "skill": "groceries", "title": "Zepto · Diet Coke",
                                "goal": "order a diet coke", "start": "https://www.zeptonow.com/cn/diet-coke",
                                "plan": [], "steps": [], "cart": []})


def test_a_goal_that_repeats_a_habit_starts_where_it_did_and_skips_the_app_question(tmp_path):
    c, made, memory = core(tmp_path)
    habit = saved_habit(memory)
    asked = []
    c.habit_job = lambda goal, habits: asked.append([h["id"] for h in habits]) or sure(habit)
    c.new_task("order a diet coke")
    until(lambda: made)
    assert made[0][0] == "https://www.zeptonow.com/cn/diet-coke" and made[0][3:] == (habit, "same")
    assert made[0][2] == "Zepto · order a diet coke" and c.open is None and asked == [[habit]]


def test_an_unsure_habit_match_is_ignored(tmp_path):
    c, made, memory = core(tmp_path)
    habit = saved_habit(memory)
    c.habit_job = lambda goal, habits: sure(habit, p=0.6)
    c.new_task("order a diet coke")
    until(lambda: c.open is not None)
    assert c.open.message["text"] == "Which app should I use for groceries: Swiggy or Zepto?"
