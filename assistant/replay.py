"""Habit replay: whether a goal repeats a saved habit, the step the habit expects next, and what Jev's answers mean
against it. Replay only chooses which of Jev's picks runs; the gate, approvals, hidden asks, the captcha relay, the
loop ladder, and the caps stay as they are."""

from . import executor, jev
from .learn import QUIET, control_name

MATCH_P = 0.7


def match(found, habits):
    """(habit, sameness) when Jev picked a habit at MATCH_P or more and the pick isn't ambiguous; else None."""
    pick, sameness = found
    p = pick["probabilities"].get(pick["choice"], 0.0)
    if pick["choice"] == "none" or p < MATCH_P or jev.ambiguous(pick["probabilities"]):
        return None
    habit = next((h for h in habits if str(h["id"]) == pick["choice"]), None)
    return (habit, sameness["choice"]) if habit else None


def start(habit, sameness):
    """What a replayed task records; it is written to the trace."""
    return {"habit": habit["id"], "sameness": sameness, "on": True, "expected": 0, "matched": 0, "fallbacks": [],
            "used": {}, "left": []}


def same_shape(plan, habit_plan):
    """Whether a new plan can follow the habit's steps: as many subgoals, of the same kinds."""
    return len(plan) == len(habit_plan) and all(a["kind"] == b["kind"] for a, b in zip(plan, habit_plan))


def expected(habit, record, subgoal):
    """The habit's next recorded step for this subgoal, or None: replay is off, this subgoal left it, or its steps
    are used up."""
    if not record["on"] or subgoal in record["left"]:
        return None
    steps = [step for step in habit["steps"] if step["subgoal"] == subgoal]
    used = record["used"].get(str(subgoal), 0)
    return steps[used] if used < len(steps) else None


def present(step, page):
    """How many controls on the page carry the step's label."""
    return sum(control_name(a.get("label")) == step["name"] for a in page["actions"])


def top_pick(step, answers, controls):
    """Jev's first choice, whatever the gates say: (operation, action, key), or None for DONE or THINK."""
    op = step["op"]
    if op in executor.CONTROLS:
        operation, kind = executor.CONTROLS[op]
        return (operation, controls[kind], None) if kind in controls else None
    if op in executor.TARGETED and executor.TARGETED[op][1] in step["targets"]:
        operation, head = executor.TARGETED[op]
        key = answers["key"]["choice"] if op == "KEY" and "key" in answers else None
        return operation, step["targets"][head], key
    return None


def judge(expected_step, step, answers, page, controls):
    """(verdict, chosen) for one step against the habit's expected step:
    - ("absent", None): its control isn't on the page yet, so Phase A decides and nothing counts;
    - ("matched", (operation, action, key)): Jev's top pick is the expected step, and it passes the gates or its
      control is the only one on the page with that label;
    - ("quiet", None): Jev's gated pick is a wait or a scroll, which no habit records;
    - ("other", label): Jev's pick passes the gates but is another step;
    - ("unsure", None): neither, so the planner decides."""
    count = present(expected_step, page)
    if not count:
        return "absent", None
    top = top_pick(step, answers, controls)
    if (top and top[0] == expected_step["op"] and control_name(top[1].get("label")) == expected_step["name"]
            and (top[0] != "PRESS_KEY" or top[2] == expected_step["key"]) and (step["kind"] == "act" or count == 1)):
        return "matched", top
    if step["kind"] == "act":
        if step["operation"] in QUIET:
            return "quiet", None
        return "other", control_name(step["action"].get("label"))
    return "unsure", None
