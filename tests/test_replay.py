"""Habit replay's pure decisions: matching, the expected step, and judging Jev's answers against it."""

from fakes import fake_page, sub

from assistant import replay

HABIT = {"id": 7, "title": "Shop · Diet Coke", "goal": "Order Diet Coke", "start": "https://shop.test/checkout",
         "plan": [sub("choose the Office address"), sub("pay", kind="commit")],
         "steps": [{"subgoal": 0, "op": "CLICK", "name": "office", "key": None, "value": None, "secret": False,
                    "commit": False, "url": "shop.test/checkout", "title": "Shop"},
                   {"subgoal": 0, "op": "CLICK", "name": "home", "key": None, "value": None, "secret": False,
                    "commit": False, "url": "shop.test/checkout", "title": "Shop"}]}
ACTIONS = fake_page()["actions"]
OFFICE = next(a for a in ACTIONS if a["label"] == "Office")
HOME = next(a for a in ACTIONS if a["label"] == "Home")


def choice(pick, p):
    rest = {"none": round(1 - p, 3)} if pick != "none" else {"7": round(1 - p, 3)}
    return {"choice": pick, "probabilities": {pick: p, **rest}, "confidence": p}


def same():
    return {"choice": "same", "probabilities": {"same": 0.9, "similar": 0.1}, "confidence": 0.9}


def jev_step(kind, op="CLICK", target=OFFICE):
    step = {"kind": kind, "op": op, "targets": {"click_target": target}}
    return {**step, "operation": "CLICK", "action": target} if kind == "act" else step


def test_a_habit_matches_only_when_jev_is_sure():
    assert replay.match((choice("7", 0.9), same()), [HABIT]) == (HABIT, "same")
    assert replay.match((choice("7", 0.69), same()), [HABIT]) is None
    assert replay.match((choice("none", 0.9), same()), [HABIT]) is None
    close = {"choice": "7", "probabilities": {"7": 0.7, "8": 0.3}, "confidence": 0.7}
    assert replay.match((close, same()), [HABIT, {**HABIT, "id": 8}]) == (HABIT, "same")
    tied = {"choice": "7", "probabilities": {"7": 0.7, "8": 0.6}, "confidence": 0.7}  # within 0.2: ambiguous
    assert replay.match((tied, same()), [HABIT, {**HABIT, "id": 8}]) is None


def test_the_expected_step_follows_each_subgoals_recorded_steps():
    record = replay.start(HABIT, "same")
    assert replay.expected(HABIT, record, 0)["name"] == "office"
    record["used"]["0"] = 1
    assert replay.expected(HABIT, record, 0)["name"] == "home"
    record["used"]["0"] = 2
    assert replay.expected(HABIT, record, 0) is None and replay.expected(HABIT, record, 1) is None
    record["used"], record["left"] = {}, [0]
    assert replay.expected(HABIT, record, 0) is None
    assert replay.same_shape([sub("x"), sub("y", kind="commit")], HABIT["plan"])
    assert not replay.same_shape([sub("x", kind="ask"), sub("y", kind="commit")], HABIT["plan"])


def test_judging_jevs_answer_against_the_expected_step():
    page = fake_page()
    expected = HABIT["steps"][0]
    assert replay.judge(expected, jev_step("act"), {}, page, {}) == ("matched", ("CLICK", OFFICE, None))
    assert replay.judge(expected, jev_step("think"), {}, page, {}) == ("matched", ("CLICK", OFFICE, None))
    assert replay.judge(expected, jev_step("act", target=HOME), {}, page, {}) == ("other", "home")
    assert replay.judge(expected, jev_step("think", target=HOME), {}, page, {}) == ("unsure", None)
    assert replay.judge(expected, jev_step("think", op="THINK"), {}, page, {}) == ("unsure", None)
    gone = fake_page(actions=[a for a in ACTIONS if a["label"] != "Office"])
    assert replay.judge(expected, jev_step("act", target=HOME), {}, gone, {}) == ("absent", None)
    scroll = {"kind": "act", "op": "SCROLL_DOWN", "targets": {}, "operation": "SCROLL_DOWN", "action": HOME}
    assert replay.judge(expected, scroll, {}, page, {}) == ("quiet", None)


def test_an_expected_label_shown_twice_needs_the_gates():
    twice = fake_page(actions=[*ACTIONS, {"id": "e9", "kind": "click", "label": "Office", "role": "radio", "node": 20}])
    expected = HABIT["steps"][0]
    assert replay.present(expected, twice) == 2
    assert replay.judge(expected, jev_step("think"), {}, twice, {}) == ("unsure", None)
    assert replay.judge(expected, jev_step("act"), {}, twice, {}) == ("matched", ("CLICK", OFFICE, None))
