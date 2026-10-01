"""System One: the step request Jev answers and the gates on its answers. No models."""

import pytest
from fakes import answers_for, fake_page

from assistant import executor

SUB = {"do": "choose the Office address", "done_when": "Office is selected", "kind": "act", "values": []}
TASK = {"goal": "Order Diet Coke", "answers": [], "recent": []}


def build(page=None, sub=SUB, task=TASK, previous=None):
    return executor.request(page or fake_page(), sub, task, previous)


def decide(picks, page=None, task=TASK):
    state, questions, heads, controls = build(page=page, task=task)
    return executor.decide(answers_for(questions, picks), questions, heads, controls)


def test_only_operations_with_targets_are_offered_plus_done_and_think():
    state, questions, _, _ = build()
    offered = set(questions["next"]["options"])
    assert {"CLICK", "TYPE", "KEY", "UPLOAD", "HOVER", "WAIT", "DONE", "THINK"} <= offered
    assert "SELECT" not in offered and "BACK" not in offered
    assert {"click_target", "type_text_target", "press_key_target", "upload_target", "key", "blocker"} <= set(questions)
    assert "effect" not in questions  # no step yet to judge
    assert state["subgoal"] == {"do": SUB["do"], "done_when": SUB["done_when"]} and len(state["page"]["text"]) <= 2500


def test_the_effect_question_follows_a_step_and_new_elements_are_marked():
    before = fake_page(actions=[a for a in fake_page()["actions"] if a["id"] != "e5"])
    state, questions, _, _ = build(task={**TASK, "recent": [{"operation": "CLICK", "action": "Home"}]}, previous=before)
    assert "effect" in questions
    assert next(e for e in state["elements"] if e["label"] == "Office").get("new")
    assert not next(e for e in state["elements"] if e["label"] == "Home").get("new")


def test_a_long_menu_stays_within_jevs_option_limit():
    actions = [{"id": f"c{n}", "kind": "click", "label": f"Item {n}", "role": "link", "node": n + 100}
               for n in range(300)]
    state, questions, _, _ = build(page=fake_page(actions=actions))
    assert len(questions["click_target"]["options"]) == executor.OPTIONS
    assert len(state["elements"]) == executor.ELEMENTS
    assert decide({"next": "CLICK", "click_target": "Item 7"}, page=fake_page(actions=actions))["kind"] == "act"


def test_a_confident_pick_acts_on_its_element():
    step = decide({"next": "CLICK", "click_target": "Office"})
    assert step["kind"] == "act" and step["operation"] == "CLICK" and step["action"]["label"] == "Office"


def test_a_blocker_comes_before_any_action():
    assert decide({"next": "CLICK", "click_target": "Office", "blocker": "code"})["kind"] == "blocker"


def test_done_think_and_low_confidence():
    assert decide({"next": "DONE"})["kind"] == "done"
    assert decide({"next": "THINK"})["kind"] == "think"
    assert decide({"next": ("CLICK", 0.4), "click_target": "Office"})["kind"] == "think"
    assert decide({"next": "CLICK", "click_target": ("Office", 0.3)})["kind"] == "think"  # low and no lead


def test_same_label_duplicates_count_as_one_choice():
    actions = [{"id": "a", "kind": "click", "label": "Search", "role": "button", "node": 1},
               {"id": "b", "kind": "click", "label": "Search", "role": "button", "node": 2},
               {"id": "c", "kind": "click", "label": "Cart", "role": "link", "node": 3}]
    state, questions, heads, controls = build(page=fake_page(actions=actions))
    answers = answers_for(questions, {"next": "CLICK"})
    answers["click_target"] = {"choice": "1", "probabilities": {"1": 0.45, "2": 0.45, "3": 0.10}, "confidence": 0.4}
    step = executor.decide(answers, questions, heads, controls)
    assert step["kind"] == "act" and step["margin"] == pytest.approx(0.8)


def duplicate_step(probabilities, choice):
    actions = [{"id": "a", "kind": "click", "label": "Search", "role": "button", "node": 1},
               {"id": "b", "kind": "click", "label": "Search", "role": "button", "node": 2},
               {"id": "c", "kind": "click", "label": "Cart", "role": "link", "node": 3},
               {"id": "d", "kind": "click", "label": "Help", "role": "link", "node": 4}]
    state, questions, heads, controls = build(page=fake_page(actions=actions))
    answers = answers_for(questions, {"next": "CLICK"})
    answers["click_target"] = {"choice": choice, "probabilities": probabilities, "confidence": probabilities[choice]}
    return executor.decide(answers, questions, heads, controls)


def test_a_raw_leader_that_trails_the_merged_label_does_not_act():
    # Search x2 holds 0.60 between them; Cart's 0.34 is the biggest single element but not the merged leader.
    step = duplicate_step({"1": 0.30, "2": 0.30, "3": 0.34, "4": 0.06}, choice="3")
    assert step["kind"] == "think" and step["margin"] == pytest.approx(0.26)


def test_duplicates_that_together_reach_the_target_probability_act():
    step = duplicate_step({"1": 0.35, "2": 0.35, "3": 0.25, "4": 0.05}, choice="1")
    assert step["kind"] == "act" and step["action"]["label"] == "Search"
    assert step["margin"] == pytest.approx(0.45)


def test_key_steps_carry_their_key():
    step = decide({"next": "KEY", "press_key_target": "Phone", "key": "Enter"})
    assert step["operation"] == "PRESS_KEY" and step["key"] == "Enter"


def test_candidates_rank_operation_times_target():
    state, questions, heads, controls = build()
    answers = answers_for(questions, {"next": ("CLICK", 0.6), "click_target": ("Office", 0.7)})
    top = executor.candidates(answers, questions, heads, controls)
    assert top[0]["n"] == 1 and top[0]["op"] == "CLICK" and top[0]["action"]["label"] == "Office"
    assert len(top) <= 5 and all(a["p"] >= b["p"] for a, b in zip(top, top[1:]))


def test_typed_values_follow_the_field_name():
    values = [{"field": "customer name", "text": "Test User"}, {"field": "telephone", "text": "[profile:phone]"}]
    assert executor.value({"label": "Customer name:"}, values) == "Test User"
    assert executor.value({"label": "Size"}, values) is None
    assert executor.value({"label": "Search"}, [{"field": "query", "text": "Diet Coke"}]) == "Diet Coke"


def test_repeats_count_only_unchanged_steps_on_the_same_path():
    history = [{"operation": "CLICK", "action": "Add", "url": "https://s.test/p?x=1", "page_changed": False},
               {"operation": "CLICK", "action": "Add", "url": "https://s.test/p?x=2", "page_changed": False},
               {"operation": "CLICK", "action": "Add", "url": "https://s.test/q", "page_changed": False},
               {"operation": "CLICK", "action": "Add", "url": "https://s.test/p", "page_changed": True}]
    assert executor.repeats(history, "CLICK", "Add", "https://s.test/p") == 2


def test_circles_count_one_action_from_one_page_whatever_each_run_changed():
    run = {"operation": "CLICK", "action": "Sign In", "spot": "a", "page_changed": True}
    history = [run, {**run, "page_changed": False}, {**run, "spot": "b"}, {**run, "action": "Close"},
               {**run, "relayed": True}, {"operation": "TYPE_TEXT", "action": "Search", "spot": "a", "text": "milk"},
               {"operation": "TYPE_TEXT", "action": "Search", "spot": "a", "text": "bread"}]
    assert executor.circles(history, "CLICK", "Sign In", "a") == 2
    assert executor.circles(history, "TYPE_TEXT", "Search", "a", "milk") == 1
    assert executor.circles(history, "TYPE_TEXT", "Search", "a") == 2  # Jev's own pick: whatever its text
    assert executor.circles(history, "CLICK", "Sign In", None) == 0
    assert executor.circles([run] * 25, "CLICK", "Sign In", "a") == 20  # the last 20 steps only


def test_scrolls_count_only_the_last_run_on_one_path():
    down = {"operation": "SCROLL_DOWN", "action": "Page", "url": "https://s.test/p?x=1"}
    history = [down, {"operation": "CLICK", "action": "Add", "url": "https://s.test/p"}, down,
               {**down, "operation": "SCROLL_UP"}, {**down, "url": "https://s.test/p?x=2"}]
    assert executor.scrolls(history, "https://s.test/p") == 3
    assert executor.scrolls(history, "https://s.test/q") == 0
