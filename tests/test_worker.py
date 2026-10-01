"""The worker over a mock browser with Jev and the planner scripted: who decides each step, what reaches the
user, and what reaches a model."""

import json
import time
from pathlib import Path
from unittest.mock import Mock

from fakes import fake_page, jev_checks, script_jev, script_planner, sub, turn, worker_with

from assistant import planner


def test_the_planner_plans_once_and_jev_does_the_steps(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    seen = script_planner(monkeypatch, turn(subgoals=[sub("choose the Office address", "Office is selected")]))
    sent = script_jev(monkeypatch, {"next": "CLICK", "click_target": "Office"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert len(seen) == 1 and seen[0]["reason"] == "start" and len(sent) == 1
    operation, action = w.browser.act.call_args.args[:2]
    assert (operation, action["label"]) == ("CLICK", "Office") and w.state["history"][-1]["action"] == "Office"


def test_a_done_subgoal_moves_on_and_an_ask_subgoal_calls_the_planner(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "Cash"}])
    seen = script_planner(monkeypatch, turn(subgoals=[sub("open the cart"), sub("ask how to pay", kind="ask")]),
                          turn("ASK", message="How should I pay?", options=["UPI", "Cash on delivery"]), turn())
    script_jev(monkeypatch, {"next": "DONE"})
    jev_checks(monkeypatch, read_reply="2")
    for _ in range(3):
        w.command("tick")
    assert [p["reason"] for p in seen] == ["start", "next"]
    assert w.asked[0]["text"] == "How should I pay?\n1. UPI\n2. Cash on delivery"
    assert w.state["chat"][-1]["text"] == "“Cash” → option 2: Cash on delivery" and w.state["reason"] == "reply"


def test_an_unsure_step_goes_to_the_planner_with_jevs_candidates(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    seen = script_planner(monkeypatch, turn(subgoals=[sub("choose the Office address")]), turn("PICK", pick=1))
    script_jev(monkeypatch, {"next": ("CLICK", 0.4), "click_target": "Office"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert seen[1]["reason"] == "unsure" and seen[1]["candidates"][0]["n"] == 1
    assert w.browser.act.call_args.args[0] == "CLICK" and w.browser.act.call_args.args[1]["label"] == "Office"


def test_a_pick_that_isnt_listed_is_refused(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn(subgoals=[sub()]), turn("PICK", pick=9))
    script_jev(monkeypatch, {"next": "THINK"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert "names one of Jev's candidates" in w.state["feedback"][-1] and not w.browser.act.called


def test_continue_with_nothing_planned_is_refused(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn())
    w.command("tick")
    assert "plan has nothing left" in w.state["feedback"][-1]


def test_typed_values_come_from_the_subgoal_and_profile_values_never_reach_a_model(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn(subgoals=[sub("enter the phone", values=[("phone number", "[profile:phone]")])]))
    sent = script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Phone number"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert w.browser.act.call_args.args[0] == "TYPE_TEXT"
    assert w.browser.act.call_args.kwargs["text"] == "+91 98765 43210"
    assert w.state["history"][-1]["text"] == "[profile:phone]" and "98765" not in json.dumps(sent[0][0])


def test_a_missing_profile_detail_is_asked_hidden_and_remembered(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "me@example.com"}], profile={"email": ""})
    script_planner(monkeypatch, turn(subgoals=[sub("enter the email", values=[("phone number", "[profile:email]")])]))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Phone number"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert w.asked[0]["kind"] == "secret" and "email address" in w.asked[0]["text"]
    assert w.browser.act.call_args.kwargs["text"] == "me@example.com" and w.profile.data["email"] == "me@example.com"


def test_a_field_with_no_value_is_written_by_the_planner(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    seen = script_planner(monkeypatch, turn(subgoals=[sub("fill the form")]), turn("TYPE", pick=1, text="Test User"))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Phone number"})
    jev_checks(monkeypatch)
    for _ in range(3):
        w.command("tick")
    assert seen[1]["candidates"][0]["element"] == "Phone number"
    assert w.browser.act.call_args.kwargs["text"] == "Test User"


def test_an_answer_told_just_before_done_goes_out_once(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(text="Flights\nIndiGo 06:10 ₹4,321"), goal="Cheapest flight BLR to DEL")
    script_planner(monkeypatch, turn("TELL", message="IndiGo at 06:10 for ₹4,321."),
                   turn("DONE", message="IndiGo at 06:10 for ₹4,321.", text="IndiGo 06:10 ₹4,321"))
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert [m["text"] for m in w.told].count("IndiGo at 06:10 for ₹4,321.") == 1 and w.state["status"] == "done"


def test_done_needs_a_quote_and_stop_ends_the_task(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn("DONE", message="Ordered.", text="Order placed"))
    jev_checks(monkeypatch)
    w.command("tick")
    assert "DONE needs a quote" in w.state["feedback"][-1]
    script_planner(monkeypatch, turn("STOP", message="The shop is closed today."))
    w.command("tick")
    assert w.state["status"] == "blocked" and w.told[-1]["text"] == "The shop is closed today."


def test_a_commit_waits_for_a_clear_yes_then_runs_and_is_watched(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "yes"}])
    seen = script_planner(monkeypatch, turn(subgoals=[sub("place the order", kind="commit")]),
                          turn("DONE", message="Order placed: Diet Coke, ₹79.", text="Pay ₹79"))
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Pay"})
    jev_checks(monkeypatch)
    for _ in range(4):
        w.command("tick")
    assert "I'll press “Pay ₹79”" in w.asked[0]["text"] and w.browser.act.call_count == 1
    assert w.state["history"][0]["committed"] and w.state["history"][0]["approved"] == "user"
    assert seen[-1]["reason"] == "commit" and w.state["status"] == "done" and w.state["done"] == ["Pay ₹79"]


def test_spend_over_the_cap_stops_the_task(tmp_path):
    w = worker_with(tmp_path)
    w.state["meter"]["mini"] = 0.26
    w.command("tick")
    assert w.state["status"] == "blocked" and "model-spend limit" in w.told[-1]["text"]


def test_five_model_errors_in_a_row_stop_the_task(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    monkeypatch.setattr(planner, "turn", Mock(side_effect=ValueError("cut off")))
    for _ in range(5):
        w.command("tick")
    assert w.state["status"] == "failed" and len(w.state["errors"]) == 5


def test_the_trace_keeps_jev_the_planner_and_spend_without_secrets(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn(subgoals=[sub("enter the phone", values=[("phone number", "[profile:phone]")])]))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Phone number"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    w.close()
    trace = json.loads(Path(w.state["trace_path"]).read_text())
    assert trace["turns"][0]["reason"] == "start" and trace["jev"][0]["op"] == "TYPE" and "meter" in trace
    assert "98765" not in json.dumps(trace)


def test_a_change_from_the_user_voids_the_approval_and_reaches_the_planner(tmp_path):
    w = worker_with(tmp_path)
    w.state["approval"], w.state["reason"] = {"action_label": "Pay ₹79"}, None
    w.update("make it 2 cans")
    assert w.state["approval"] is None and w.state["reason"] == "reply"
    assert "make it 2 cans" in w.state["chat"][-1]["text"]


def test_a_subgoal_that_takes_too_long_is_replanned(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    seen = script_planner(monkeypatch, turn(subgoals=[sub()]))
    jev_checks(monkeypatch)
    w.command("tick")
    w.state["subgoal_steps"] = 12
    w.command("tick")
    assert seen[-1]["reason"] == "replan" and "12 steps" in seen[-1]["feedback"][0]
    assert w.state["subgoal_steps"] == 0


def test_a_payment_blocker_brings_the_payment_skill_to_the_planner(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    seen = script_planner(monkeypatch, turn(subgoals=[sub("pay")]),
                          turn("ASK", message="How should I pay?", options=["UPI", "Cash"]))
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Pay", "blocker": "payment"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert seen[1]["reason"] == "blocker:payment" and "# Situation: payment" in seen[1]["skill"]


def test_look_finds_a_control_by_sight_and_clicks_its_spot(tmp_path, monkeypatch):
    page = fake_page()
    page["actions"].append({"id": "look", "kind": "look", "node": None, "label": "Look at the screen"})
    w = worker_with(tmp_path, page=page)
    w.browser.grid_screenshot = Mock(side_effect=[("image", {"A1": {"x": 0, "y": 0, "w": 80, "h": 60}}),
                                                  ("close", {"B2": {"x": 20, "y": 15, "w": 20, "h": 15}})])
    monkeypatch.setattr(planner, "look", lambda *a: ({"cell": "A1", "label": "Seat 12A", "text": "12A",
                                                      "commits": False}, {}))
    monkeypatch.setattr(planner, "look_closer", lambda *a: ("B2", {}))
    script_planner(monkeypatch, turn("LOOK", text="seat 12A", subgoals=[sub("choose seat 12A")]))
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.browser.act.call_args.args[0] == "LOOK" and w.browser.act.call_args.kwargs["point"] == [30.0, 22.5]


def test_an_upload_uses_a_file_the_user_already_sent(tmp_path):
    statement = tmp_path / "statement.pdf"
    statement.write_bytes(b"%PDF")
    w = worker_with(tmp_path)
    w.state["messages"].append({"from": "user", "file": str(statement)})
    assert w.files_for({"label": "File input: Statement"}) == [str(statement)]
    asked = worker_with(tmp_path, replies=[{"file": str(statement)}])
    assert asked.files_for({"label": "File input: Statement"}) == [str(statement)]
    assert asked.asked[0]["kind"] == "file"


def test_steps_tick_until_the_task_stops(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn("STOP", message="Nothing to do."))
    w.state["started_at"] = time.perf_counter()
    assert [state["status"] for state in w.steps()] == ["blocked"]


def test_an_otp_question_is_asked_through_the_hidden_field_without_options(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "482913"}])
    script_planner(monkeypatch, turn("ASK", message="Enter your OTP", options=["Resend OTP", "Use another number"]))
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.asked[0]["kind"] == "secret" and w.asked[0]["text"] == "Enter your OTP"
    assert "482913" not in json.dumps(w.state["messages"]) and "482913" not in json.dumps(w.state["chat"])
