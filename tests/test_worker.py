"""The worker over a mock browser with Jev and the planner scripted: who decides each step, what reaches the
user, and what reaches a model."""

import json
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from fakes import fake_page, jev_checks, script_jev, script_planner, store, sub, turn, worker_with

from assistant import jev, planner, rules
from assistant.browser import ExecutionUncertain, StalePage, fingerprint
from assistant.errors import Retry
from assistant.worker import STOPPED, Worker, secret_field


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
    assert w.browser.act.call_args.kwargs["text"] == "me@example.com" and w.memory.id_value("email") == "me@example.com"


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


def test_an_otp_question_is_asked_through_the_hidden_field_with_its_options_listed(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "482913"}])
    script_planner(monkeypatch, turn("ASK", message="Enter your OTP", options=["Resend OTP", "Use another number"]))
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.asked[0]["kind"] == "secret"  # options stay listed; a reply that is not one of them stays hidden
    assert w.asked[0]["text"] == "Enter your OTP\n1. Resend OTP\n2. Use another number"
    assert w.state["chat"][-1]["text"].startswith("[secret:answer1]")
    assert "482913" not in json.dumps(w.state["messages"]) and "482913" not in json.dumps(w.state["chat"])


def card_page():
    page = fake_page()
    page["actions"].append({"id": "e9", "kind": "fill", "label": "Card number", "role": "textbox", "value": "",
                            "node": 20, "constraints": {}})
    return page


def test_card_details_are_asked_hidden_and_never_reach_a_model_or_a_log(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=card_page(), replies=[{"text": "4111 1111 1111 1111"}])
    seen = script_planner(monkeypatch,
                          turn("ASK", message="What's your card number and expiry date?", options=["Visa", "Amex"]),
                          turn(subgoals=[sub("enter the card number")]),
                          turn("TYPE", pick=1, text="[secret:answer1]"))
    sent = script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Card number"})
    jev_checks(monkeypatch)
    for _ in range(4):
        w.command("tick")
    assert w.asked[0]["kind"] == "secret"
    assert w.asked[0]["text"] == "What's your card number and expiry date?\n1. Visa\n2. Amex"
    assert w.browser.act.call_args.args[0] == "TYPE_TEXT"
    assert w.browser.act.call_args.kwargs["text"].replace(" ", "") == "4111111111111111"
    assert w.state["history"][-1]["text"] == "[secret:answer1]" and (None, 20) in w.secrets.nodes
    assert len(seen) == 3
    assert "4111" not in json.dumps([w.state["chat"], w.state["messages"], w.trace(), seen, sent])


def test_literal_digits_are_refused_for_a_card_field(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=card_page())
    script_planner(monkeypatch, turn(subgoals=[sub("enter the card number")]),
                   turn("TYPE", pick=1, text="4111111111111111"))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Card number"})
    jev_checks(monkeypatch)
    for _ in range(3):
        w.command("tick")
    assert "takes a code or password" in w.state["feedback"][-1] and not w.browser.act.called


def test_a_pending_commit_is_watched_again_before_anyone_acts(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    w.state["commit"] = {"label": "Pay ₹79", "since": 0, "goal": "Order Diet Coke"}
    watched = []

    def after_commit(page, goal, label):
        watched.append((goal, label))
        return "confirmed"

    def refuse(*_args, **_kwargs):
        raise AssertionError("Jev or the planner acted on a page whose commit outcome is unknown")

    monkeypatch.setattr(jev, "after_commit", after_commit)
    monkeypatch.setattr(jev, "request", refuse)
    monkeypatch.setattr(planner, "turn", refuse)
    w.command("tick")
    assert watched == [("Order Diet Coke", "Pay ₹79")] and w.state["done"] == ["Pay ₹79"]
    assert any("Pay ₹79" in note for note in w.state["notes"]) and not any("None" in n for n in w.state["notes"])
    assert w.state["commit"] is None and w.state["reason"] == "commit"


def test_a_commit_watch_cut_short_resumes_with_its_own_label_and_goal(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "yes"}])
    script_planner(monkeypatch, turn(subgoals=[sub("place the order", kind="commit")]), turn())
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Pay"})
    jev_checks(monkeypatch)
    watched, verdicts = [], ["processing", "confirmed"]
    monkeypatch.setattr(jev, "after_commit", lambda page, goal, label: watched.append((goal, label)) or verdicts.pop(0))
    w.interrupted = Mock(side_effect=[False, True, False, False, False])
    for _ in range(4):
        w.command("tick")
    assert w.browser.act.call_count == 1 and watched == [("place the order", "Pay ₹79")] * 2
    assert w.state["done"] == ["Pay ₹79"] and w.state["commit"] is None


def test_a_code_question_that_was_dropped_is_not_counted(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "123456"}])
    script_planner(monkeypatch, turn("ASK", message="What's the verification code we sent you?"))
    jev_checks(monkeypatch)
    calls = []
    w.moved = lambda page: calls.append(page) or len(calls) <= 4
    for _ in range(4):
        w.command("tick")
    assert w.asked == [] and w.state["status"] != "blocked" and w.state["code_asks"] == 0
    w.command("tick")
    assert len(w.asked) == 1 and w.asked[0]["kind"] == "secret" and w.state["code_asks"] == 1
    w.state["code_asks"], w.state["reason"] = 3, None
    script_planner(monkeypatch, turn("ASK", message="Please type the OTP from the SMS"))
    w.command("tick")
    assert w.state["status"] == "blocked" and "isn't working after 3 codes" in w.told[-1]["text"]


BUTTONS = [{"id": f"b{n}", "kind": "click", "label": label, "role": "button", "node": n + 1}
           for n, label in enumerate(["Office", "Home", "Work"])]


def test_a_code_field_is_asked_hidden_and_typed_without_the_planner(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "4 8 1 5 1 6"}])
    seen = script_planner(monkeypatch, turn(subgoals=[sub("sign in")]))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Code", "blocker": "code"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert len(seen) == 1 and w.asked[0]["kind"] == "secret" and "code or password" in w.asked[0]["text"]
    assert w.browser.act.call_args.kwargs["text"] == "481516" and w.state["history"][-1]["text"] == "[secret:answer1]"


def test_a_fourth_code_stops_instead_of_sending_more(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn(subgoals=[sub("sign in")]))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Code", "blocker": "code"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.state["code_asks"] = 3
    w.command("tick")
    assert w.state["status"] == "blocked" and "after 3 codes" in w.told[-1]["text"]


def test_a_code_question_dropped_for_a_changed_page_is_not_counted(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "481516"}])
    script_planner(monkeypatch, turn(subgoals=[sub("sign in")]))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Code", "blocker": "code"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.steady = Mock(side_effect=StalePage("The page changed while I was about to ask. Decide again."))
    w.command("tick")
    assert w.asked == [] and w.state["code_asks"] == 0 and w.state["status"] == "ready"
    w.steady = Mock()
    w.command("tick")
    assert len(w.asked) == 1 and w.state["code_asks"] == 1


def test_a_card_detail_is_asked_by_name_hidden_and_does_not_count_as_a_code(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=card_page(), replies=[{"text": "4111 1111 1111 1111"}])
    seen = script_planner(monkeypatch, turn(subgoals=[sub("pay by card")]))
    sent = script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Card number", "blocker": "code"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert len(seen) == 1 and w.asked[0]["kind"] == "secret" and "card's Card number" in w.asked[0]["text"]
    assert w.browser.act.call_args.kwargs["text"].replace(" ", "") == "4111111111111111"
    assert w.state["code_asks"] == 0 and w.state["history"][-1]["text"] == "[secret:answer1]"
    assert "4111" not in json.dumps([w.state["chat"], w.state["messages"], w.trace(), seen, sent])


def test_a_code_blocker_without_a_code_field_goes_to_the_planner(tmp_path, monkeypatch):
    page = fake_page(actions=[a for a in fake_page()["actions"] if a["id"] not in {"e2", "e3"}])
    w = worker_with(tmp_path, page=page)
    seen = script_planner(monkeypatch, turn(subgoals=[sub("sign in")]))
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Home", "blocker": "code"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert seen[-1]["reason"] == "blocker:code" and not w.asked


def test_a_captcha_goes_to_the_user_as_a_numbered_picture(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "2"}])
    w.browser.marked_screenshot = Mock(return_value="numbered")
    script_planner(monkeypatch, turn(subgoals=[sub("sign in")]))
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Home", "blocker": "captcha"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert w.asked[0]["kind"] == "image" and w.asked[0]["image"] == "numbered"
    assert w.browser.act.call_args.args[0] == "CLICK" and w.browser.act.call_args.args[1]["label"] == "Office"


def test_jev_picks_which_value_goes_into_an_unclear_field(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn(subgoals=[sub("fill the form", values=[("customer", "Test User"),
                                                                            ("time", "19:00")])]))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Phone number"})
    jev_checks(monkeypatch, value_for="time")
    w.command("tick")
    w.command("tick")
    assert w.browser.act.call_args.kwargs["text"] == "19:00"


def test_a_step_that_changes_nothing_tries_jevs_next_best_then_the_planner(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(actions=BUTTONS))
    seen = script_planner(monkeypatch, turn(subgoals=[sub("choose Office")]))
    script_jev(monkeypatch, {"next": "CLICK", "click_target": ("Office", 0.8)})
    jev_checks(monkeypatch)
    for _ in range(4):
        w.command("tick")
    labels = [c.args[1]["label"] for c in w.browser.act.call_args_list]
    assert labels[:2] == ["Office", "Office"] and labels[2] != "Office"
    w.command("tick")
    assert seen[-1]["reason"] == "unsure" and "changed nothing" in seen[-1]["feedback"][0]


def test_eight_unchanged_repeats_ask_the_user(tmp_path):
    w = worker_with(tmp_path, replies=[{"text": "use the other address"}])
    url = w.state["page"]["url"]
    w.state["history"] = [{"operation": "CLICK", "action": "Office", "url": url, "page_changed": False}] * 8
    w.track_loop(w.state["history"][-1])
    assert "I'm stuck" in w.asked[0]["text"] and w.state["loop_from"] == 8 and w.state["reason"] == "reply"


def test_card_digits_typed_as_the_stuck_reply_are_asked_hidden_and_kept_out_of_every_log(tmp_path):
    page = fake_page(actions=[{"id": "e9", "kind": "fill", "label": "Card number", "role": "textbox", "value": "",
                               "node": 20, "constraints": {}}])
    w = worker_with(tmp_path, page=page, replies=[{"text": "4111 1111 1111 1111"}])
    w.stuck()
    assert w.asked[0]["kind"] == "secret" and w.state["reason"] == "reply"
    assert w.state["chat"][-1]["text"].startswith("[secret:answer1]")
    assert "4111" not in json.dumps([w.state["chat"], w.state["messages"], w.trace()])


def captcha_relay(tmp_path, reply, labels=None):
    """A worker over a 3x3 captcha, the user's reply given, relay_captcha run once."""
    squares = [{"id": f"s{n}", "kind": "click", "label": label, "role": "button", "node": n}
               for n, label in enumerate(labels or [f"Tile {n}" for n in range(1, 10)], 1)]
    w = worker_with(tmp_path, page=fake_page(actions=squares), replies=[{"text": reply}])
    w.browser.marked_screenshot = Mock(return_value="numbered")
    w.relay_captcha(w.state["page"])
    return w


def pressed(w):
    return [c.args[1]["label"] for c in w.browser.act.call_args_list]


def test_a_numeric_text_captcha_is_text_not_squares(tmp_path):
    for reply in ("4829", "2 40", "0 3"):  # a number outside the squares: the whole reply is the text it shows
        w = captcha_relay(tmp_path, reply)
        assert pressed(w) == [] and not any("Clicked" in note for note in w.state["notes"]), reply
        assert reply in w.state["chat"][-1]["text"] and w.state["reason"] == "reply", reply


def test_squares_may_be_joined_by_and_commas_or_ampersands(tmp_path):
    assert pressed(captcha_relay(tmp_path, "2 and 5")) == ["Tile 2", "Tile 5"]
    w = captcha_relay(tmp_path, "2, 5 & 7")
    assert pressed(w) == ["Tile 2", "Tile 5", "Tile 7"] and w.state["reason"] != "reply"
    assert "picked (2, 5, 7)" in w.state["notes"][-1]


def test_a_square_named_twice_is_pressed_once(tmp_path):
    w = captcha_relay(tmp_path, "2 2")
    assert pressed(w) == ["Tile 2"] and "picked (2)" in w.state["notes"][-1]


def test_a_square_that_would_commit_is_not_pressed_and_not_reported_as_pressed(tmp_path):
    labels = ["Tile 1", "Pay ₹79", "Tile 3"]
    w = captcha_relay(tmp_path, "1 2", labels)
    assert pressed(w) == ["Tile 1"]
    assert any("would commit" in note for note in w.state["notes"]) and "picked (1)" in w.state["notes"][-1]
    w = captcha_relay(tmp_path, "2", labels)
    assert pressed(w) == [] and not any("Clicked" in note for note in w.state["notes"])
    assert any("would commit" in note for note in w.state["notes"])
    assert "None of the squares" in w.state["notes"][-1]


def test_a_page_with_only_a_card_field_asks_for_the_card_without_the_planner(tmp_path, monkeypatch):
    page = fake_page(actions=[{"id": "e9", "kind": "fill", "label": "Card number", "role": "textbox", "value": "",
                               "node": 20, "constraints": {}}])
    w = worker_with(tmp_path, page=page, replies=[{"text": "4111 1111 1111 1111"}])
    seen = script_planner(monkeypatch, turn(subgoals=[sub("pay by card")]))
    sent = script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Card number", "blocker": "code"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert len(seen) == 1 and w.asked[0]["kind"] == "secret" and w.state["code_asks"] == 0
    assert w.browser.act.call_args.kwargs["text"].replace(" ", "") == "4111111111111111"
    assert w.state["history"][-1]["text"] == "[secret:answer1]"
    assert "4111" not in json.dumps([w.state["chat"], w.state["messages"], w.trace(), seen, sent])


def test_a_button_called_card_details_is_not_a_secret_field(tmp_path):
    button = {"id": "b1", "kind": "click", "label": "Card details", "role": "button", "node": 3}
    w = worker_with(tmp_path, page=fake_page(actions=[button]), replies=[{"text": "use the saved one"}])
    w.stuck()
    assert w.asked[0]["kind"] == "text"
    assert secret_field({**button, "kind": "fill", "label": "Card number"}) and not secret_field(button)


def code_page(value):
    page = fake_page()
    next(a for a in page["actions"] if a["id"] == "e2")["value"] = value
    return page


def test_a_code_already_typed_is_not_asked_for_again(tmp_path, monkeypatch):
    page = code_page("[secret]")
    page["actions"] = [a for a in page["actions"] if a["id"] != "e3"]  # no other secret field waits (no CVV)
    w = worker_with(tmp_path, page=page, replies=[{"text": "481516"}])
    seen = script_planner(monkeypatch, turn(subgoals=[sub("sign in")]))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Code", "blocker": "code"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.state["code_asks"] = 3  # even at the limit: nothing is asked, so nothing is stopped
    w.command("tick")
    assert w.asked == [] and w.state["status"] != "blocked" and seen[-1]["reason"] == "blocker:code"


def test_an_emptied_code_field_is_asked_for_again(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=code_page(""), replies=[{"text": "481516"}])
    script_planner(monkeypatch, turn(subgoals=[sub("sign in")]))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Code", "blocker": "code"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert len(w.asked) == 1 and w.asked[0]["kind"] == "secret"


# ---- LOOK: a control found by sight is held to the same gate as a listed one

LOOK = {"id": "look", "kind": "look", "node": None, "label": "Look at the screen"}
VIEW = {"x": 0, "y": 0, "w": 800, "h": 600}
PAY_SPOT = {"cell": "C4", "label": "Pay ₹79", "text": "Pay ₹79", "commits": True}
# An "act" subgoal, not a "commit" one: it is the found control's own flag or words that must make it a commit.
LOOK_PLAN = turn("LOOK", text="the orange button", subgoals=[sub("press the orange button")])


def looking(tmp_path, monkeypatch, *found, replies=()):
    """A worker over a mock browser with an 800×600 screen. Each `found` is what the model's look returns, in
    order: a cell dict, or None when nothing on screen fits. The closer look always picks B2."""
    page = fake_page()
    page["actions"].append(dict(LOOK))
    page["fingerprint"] = fingerprint(page)
    w = worker_with(tmp_path, page=page, replies=replies)

    def shot(region=None, columns=8, rows=6, masked=(), scale=1, hide=()):
        return ("CLOSE" if region else "IMAGE"), rules.grid(region or VIEW, columns, rows)

    w.browser.grid_screenshot = Mock(side_effect=shot)
    queue = list(found)
    monkeypatch.setattr(planner, "look", lambda *a: (queue.pop(0), {}))
    monkeypatch.setattr(planner, "look_closer", lambda *a: ("B2", {}))
    return w


@pytest.mark.parametrize("found", [
    {"cell": "C4", "label": "Orange button", "text": "", "commits": True},
    {"cell": "C4", "label": "Orange button", "text": "Place order", "commits": False}])
def test_a_control_found_by_sight_that_commits_is_asked_about_before_any_click(tmp_path, monkeypatch, found):
    w = looking(tmp_path, monkeypatch, found, replies=[{"text": "no"}])
    script_planner(monkeypatch, LOOK_PLAN)
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.asked[0]["approval"] and "I'll press “Orange button”" in w.asked[0]["text"]
    w.browser.act.assert_not_called()
    assert "did not approve" in w.state["feedback"][-1] and w.state["approval"] is None


def test_an_approved_spot_is_clicked_when_looking_finds_the_same_spot(tmp_path, monkeypatch):
    w = looking(tmp_path, monkeypatch, PAY_SPOT, dict(PAY_SPOT), replies=[{"text": "yes"}])
    script_planner(monkeypatch, LOOK_PLAN)
    jev_checks(monkeypatch)
    look = {"op": "LOOK", "pick": "", "text": "the pay button"}
    with pytest.raises(StalePage):
        w.do(look, w.state["page"])
    assert w.state["approval"]["look"] == {"point": [237.5, 337.5]} and w.browser.act.call_count == 0
    w.do(look, w.state["page"])  # the page is unchanged and the same spot is found again
    assert w.browser.act.call_count == 1 and w.browser.act.call_args.kwargs == {"point": [237.5, 337.5]}
    assert w.state["history"][-1]["committed"] and w.state["history"][-1]["approved"] == "user"
    assert len(w.asked) == 1 and w.state["approval"] is None


def test_a_yes_to_one_spot_does_not_cover_another_spot(tmp_path, monkeypatch):
    other = {**PAY_SPOT, "cell": "A1"}  # the same words, found somewhere else on the screen
    w = looking(tmp_path, monkeypatch, PAY_SPOT, other, replies=[{"text": "yes"}, {"text": "no"}])
    script_planner(monkeypatch, LOOK_PLAN)
    jev_checks(monkeypatch)
    look = {"op": "LOOK", "pick": "", "text": "the pay button"}
    with pytest.raises(StalePage):
        w.do(look, w.state["page"])
    with pytest.raises(Retry, match="did not approve"):
        w.do(look, w.state["page"])
    assert len(w.asked) == 2 and w.browser.act.call_count == 0


def test_a_yes_to_a_spot_does_not_cover_a_listed_control_with_the_same_label(tmp_path, monkeypatch):
    w = looking(tmp_path, monkeypatch, PAY_SPOT, replies=[{"text": "yes"}, {"text": "no"}])
    script_planner(monkeypatch, LOOK_PLAN)
    jev_checks(monkeypatch)
    page = w.state["page"]
    with pytest.raises(StalePage):
        w.do({"op": "LOOK", "pick": "", "text": "the pay button"}, page)
    listed = next(a for a in page["actions"] if a["label"] == "Pay ₹79")
    with pytest.raises(Retry, match="did not approve"):  # the page's own button needs its own yes
        w.act("CLICK", listed, page, None)
    assert len(w.asked) == 2 and w.browser.act.call_count == 0


def test_looking_masks_secret_fields_and_blurs_the_users_details(tmp_path, monkeypatch):
    w = looking(tmp_path, monkeypatch, {"cell": "C4", "label": "Blue button", "text": "", "commits": False})
    w.secrets.nodes.add((None, 8))
    script_planner(monkeypatch, turn("LOOK", text="the blue button", subgoals=[sub("pick the blue colour")]))
    jev_checks(monkeypatch)
    w.command("tick")
    first, closer = w.browser.grid_screenshot.call_args_list
    for call in (first, closer):  # the wide look and the closer one both hide what must not be seen
        assert call.kwargs["masked"] == {(None, 8)}
        assert "Mayank Churiwal" in call.kwargs["hide"] and any("98765" in value for value in call.kwargs["hide"])
    assert closer.kwargs["region"] == rules.grid(VIEW, 8, 6)["C4"]
    assert w.browser.act.call_args.args[0] == "LOOK" and w.browser.act.call_args.kwargs == {"point": [237.5, 337.5]}


def test_looking_and_finding_nothing_is_feedback_and_clicks_nothing(tmp_path, monkeypatch):
    w = looking(tmp_path, monkeypatch, None)
    script_planner(monkeypatch, turn("LOOK", text="the seat map", subgoals=[sub("choose a seat")]))
    jev_checks(monkeypatch)
    w.command("tick")
    w.browser.act.assert_not_called()
    assert "Looking at the screen found nothing" in w.state["feedback"][-1]
    assert w.browser.grid_screenshot.call_count == 1 and w.state["history"] == []


# ---- a mutation is never retried, and the task has limits

def office_click(tmp_path, monkeypatch):
    """A worker whose first act step clicks Office, with the plan already made."""
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn(subgoals=[sub("choose the Office address")]))
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Office"})
    jev_checks(monkeypatch)
    w.command("tick")
    return w


def test_a_click_that_may_have_landed_is_never_repeated(tmp_path, monkeypatch):
    w = office_click(tmp_path, monkeypatch)
    w.browser.act.side_effect = ExecutionUncertain("the click may have landed")
    for _ in range(5):  # a driver that keeps ticking until the task stops
        if w.state["status"] in STOPPED:
            break
        w.command("tick")
    assert w.state["status"] == "blocked" and w.browser.act.call_count == 1
    assert w.state["history"][-1]["unconfirmed"] == "the click may have landed"
    assert "can't confirm it, so I stopped instead of repeating it" in w.told[-1]["text"]
    assert list(w.steps()) == [] and w.browser.act.call_count == 1  # a stopped task is not driven again


def test_the_executed_step_is_logged_before_its_result_is_read(tmp_path, monkeypatch):
    w = office_click(tmp_path, monkeypatch)
    w.browser.observe.side_effect = RuntimeError("the page went away")
    w.command("tick")
    assert w.browser.act.call_count == 1 and [h["action"] for h in w.state["history"]] == ["Office"]
    assert "The last action already ran, but checking its result failed" in w.state["feedback"][-1]
    assert w.state["reason"] == "unsure"  # the planner hears it next; Jev does not click again


def test_149_actions_are_not_the_limit(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn(subgoals=[sub("choose Office")]))
    w.state["history"] += [{"step": n, "operation": "CLICK", "action": "Tile"} for n in range(1, 150)]
    w.command("tick")
    assert w.state["status"] not in STOPPED and not any("action limit" in m["text"] for m in w.told)


def test_the_150_action_limit_stops_before_any_model_is_asked(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    monkeypatch.setattr(planner, "turn", Mock(side_effect=AssertionError("the planner was asked")))
    monkeypatch.setattr(jev, "request", Mock(side_effect=AssertionError("Jev was asked")))
    w.state["history"] += [{"step": n, "operation": "CLICK", "action": "Tile"} for n in range(1, 151)]
    w.command("tick")
    assert w.state["status"] == "blocked" and "150-action limit" in w.told[-1]["text"]
    w.browser.act.assert_not_called()


def test_the_30_minute_limit_stops_before_any_model_is_asked(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    monkeypatch.setattr(planner, "turn", Mock(side_effect=AssertionError("the planner was asked")))
    monkeypatch.setattr(jev, "request", Mock(side_effect=AssertionError("Jev was asked")))
    w.state["started_at"] = time.perf_counter() - 31 * 60
    w.command("tick")
    assert w.state["status"] == "blocked" and "30 minutes" in w.told[-1]["text"]
    w.browser.act.assert_not_called()


# ---- a secret goes only where it belongs, and only as its placeholder

@pytest.mark.parametrize("field, text, refusal", [
    ("Phone number", "[secret:answer1]", "goes only into its code field"),  # a real answer, in the wrong field
    ("Phone number", "[secret:answer9]", "isn't one of the user's answers"),
    ("Code", "[secret:answer9]", "takes a code or password"),
    ("Code", "481516", "takes a code or password")])  # literal digits: only the placeholder may be typed
def test_a_secret_is_typed_only_as_the_placeholder_of_a_real_answer_into_a_code_field(tmp_path, monkeypatch, field,
                                                                                    text, refusal):
    w = worker_with(tmp_path)
    assert w.keep_secret("481516") == "answer1"
    script_planner(monkeypatch, turn(subgoals=[sub("fill the form")]), turn("TYPE", pick=1, text=text))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": field})
    jev_checks(monkeypatch)
    for _ in range(3):
        w.command("tick")
    assert refusal in w.state["feedback"][-1] and not w.browser.act.called
    assert "481516" not in json.dumps([w.state["feedback"], w.state["chat"], w.trace()])
    if field == "Code":
        assert "placeholder" in w.state["feedback"][-1]


# ---- a guessed deep link that lands on an error page starts again from the home page

def cold_start(tmp_path, url, *pages):
    browser = Mock(fresh=Mock(return_value=True), observe=Mock(side_effect=list(pages)), navigate=Mock(),
                   downloads=[], pending_downloads=[])
    w = Worker(url, "Order Diet Coke", memory=store(), browser=browser,
               trace_dir=tmp_path / "traces")
    return w, browser


def test_an_error_page_behind_a_deep_link_reopens_the_home_page(tmp_path):
    lost = fake_page(text="404\nPage not found", url="https://shop.test/p/diet-coke-9?ref=ad")
    home = fake_page(text="Shop\nSearch for groceries", url="https://shop.test/")
    w, browser = cold_start(tmp_path, lost["url"], lost, home)
    browser.navigate.assert_called_once_with("https://shop.test/")
    assert browser.observe.call_count == 2 and w.state["page"]["text"] == "Shop\nSearch for groceries"


@pytest.mark.parametrize("url, text", [
    ("https://shop.test/p/diet-coke-9", "Diet Coke 300 ml\nAdd to cart"),  # an ordinary page behind a deep link
    ("https://shop.test/", "404\nPage not found")])  # an error page at the home page itself: nowhere else to go
def test_only_an_error_page_behind_a_deep_link_is_reopened_from_the_home_page(tmp_path, url, text):
    w, browser = cold_start(tmp_path, url, fake_page(text=text, url=url))
    browser.navigate.assert_not_called()
    assert browser.observe.call_count == 1 and w.state["page"]["text"] == text


# ---- final review, Group A: a final control is a commit on every path, and Jev's clearances are kept for the audit

RIDES = [{"id": f"r{n}", "kind": "click", "label": label, "role": "button", "node": n + 1}
         for n, label in enumerate(["Bike ₹45", "Book Bike", "Book Ola Mini"])]


def test_a_planner_pick_of_a_final_control_in_an_act_subgoal_asks_first(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(text="Bike ₹45\nTotal ₹79\nBook Bike", actions=RIDES),
                    replies=[{"text": "no"}], goal="Book a bike to Indiranagar")
    script_planner(monkeypatch, turn(subgoals=[sub("choose Bike")]), turn("PICK", pick=1))
    script_jev(monkeypatch, {"next": ("CLICK", 0.4), "click_target": "Book Bike"})
    jev_checks(monkeypatch, final=("step", 0.6))
    for _ in range(2):
        w.command("tick")
    assert w.asked and w.asked[0]["approval"] and "I'll press “Book Bike”" in w.asked[0]["text"]
    w.browser.act.assert_not_called()


def test_a_maybe_commit_jev_clears_runs_and_is_recorded_for_the_bench(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(text="Ola Mini ₹120\nTotal ₹79\nBook Ola Mini", actions=RIDES),
                    replies=[{"text": "no"}], goal="Book a bike to Indiranagar")
    script_planner(monkeypatch, turn(subgoals=[sub("open the Ola Mini details")]))
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Book Ola Mini"})
    jev_checks(monkeypatch, final=("step", 0.9))
    w.command("tick")
    w.command("tick")
    assert w.browser.act.call_count == 1 and w.state["cleared_maybe"] == ["Book Ola Mini"]
    assert not w.state["history"][-1]["committed"] and w.asked == []
    jev_checks(monkeypatch, final=("step", 0.7))  # not sure enough: the same control now needs a yes
    w.state["candidates"] = [{"n": 1, "op": "CLICK", "operation": "CLICK", "element": "Book Ola Mini", "p": 0.5,
                              "action": RIDES[2]}]
    with pytest.raises(Retry, match="did not approve"):
        w.do({"op": "PICK", "pick": "1", "text": ""}, w.state["page"])
    assert w.browser.act.call_count == 1 and w.state["cleared_maybe"] == ["Book Ola Mini"]


def test_a_captcha_square_that_may_commit_is_not_pressed_either(tmp_path):
    w = captcha_relay(tmp_path, "2", ["Tile 1", "Book Ola Mini", "Tile 3"])
    assert pressed(w) == [] and any("would commit" in note for note in w.state["notes"])


# ---- final review, Group B: secrets never corrupt money or commit words, and only real secrets are hidden

def test_a_cvv_whose_digits_end_the_total_leaves_the_total_whole(tmp_path):
    from assistant import gate

    page = fake_page(text="Checkout\nOne cushion\nTo pay ₹1,499\nPay ₹1,499")
    w = worker_with(tmp_path, page=page, preauth=True, cap=100.0)
    w.keep_secret("499")
    seen = w.observe()
    assert seen["text"] == "Checkout\nOne cushion\nTo pay ₹1,499\nPay ₹1,499"
    found = gate.facts(seen, [seen["text"]])
    assert found["total"] == "₹1,499" and gate.worst(seen["text"]) == 1499.0
    w.state["site"] = "shop.test"
    assert gate.preauthorised(w.state, found, seen) is False


def test_a_hidden_reply_that_is_a_commit_word_does_not_hide_the_commit(tmp_path):
    from assistant import gate

    w = worker_with(tmp_path, replies=[{"text": "pay"}])
    w.state["plan"] = [sub("pay", kind="act")]
    w.stuck()  # the page has a CVV and a code field, so the reply is hidden
    assert w.state["chat"][-1]["text"].startswith("[secret:answer1]") and w.state["answers"]["answer1"] == "pay"
    page = w.observe()
    pay = next(a for a in page["actions"] if a.get("node") == 11)
    assert pay["label"] == "Pay ₹79" and "Pay ₹79" in page["text"]
    assert gate.commit_label(w, "CLICK", pay, page, None, {"do": "x", "kind": "act"}) == "Pay ₹79"


PAYMENT = "Payment\nUPI\nCash on delivery\nSaved HDFC card\nTo pay ₹79\nPay ₹79"


def test_a_choice_question_that_mentions_card_details_keeps_its_options_and_a_picked_option_is_not_a_secret(
        tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(text=PAYMENT), replies=[{"text": "UPI"}, {"text": "2"}])
    script_planner(monkeypatch, turn("ASK", message="How would you like to pay? Card details are asked through a "
                                                    "hidden field.",
                                     options=["UPI", "Cash on delivery", "Saved HDFC card"]))
    jev_checks(monkeypatch)
    w.command("tick")
    # Safer than a visible box: what is typed may be a card number, so the field stays hidden; the options stay.
    assert w.asked[0]["kind"] == "secret" and w.asked[0]["text"].endswith("\n1. UPI\n2. Cash on delivery\n"
                                                                         "3. Saved HDFC card")
    assert w.state["chat"][-1]["text"] == "“UPI” → option 1: UPI" and w.state["answers"] == {}
    assert "UPI" in w.observe()["text"] and w.secrets.text("Pay by UPI") == "Pay by UPI"
    w.state["reason"] = None
    w.state["chat"] = []
    w.command("tick")  # a number picks an option too
    assert w.state["chat"][-1]["text"] == "“2” → option 2: Cash on delivery"


def test_a_choice_question_answered_with_something_else_keeps_that_answer_hidden(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(text=PAYMENT), replies=[{"text": "4111 1111 1111 1111"}])
    script_planner(monkeypatch, turn("ASK", message="Which card? Card details are asked through a hidden field.",
                                     options=["Saved HDFC card", "Add a new card"]))
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.asked[0]["kind"] == "secret" and w.state["chat"][-1]["text"].startswith("[secret:answer1]")
    assert "4111" not in json.dumps([w.state["chat"], w.state["messages"], w.trace()])


def test_a_plain_choice_question_is_never_hidden(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(text=PAYMENT), replies=[{"text": "UPI"}])
    script_planner(monkeypatch, turn("ASK", message="How would you like to pay?",
                                     options=["UPI", "Cash on delivery"]))
    jev_checks(monkeypatch, read_reply="1")
    w.command("tick")
    assert w.asked[0]["kind"] == "text" and w.state["chat"][-1]["text"] == "“UPI” → option 1: UPI"


def test_a_trivial_hidden_answer_is_kept_but_never_hides_words_on_later_pages(tmp_path, monkeypatch):
    from assistant import jev, model
    from assistant.privacy import SECRETS

    w = worker_with(tmp_path, page=fake_page(text="Yes, deliver here\nPay ₹79"))
    for value in ("yes", "ok", "no", "Use the saved card"):
        w.keep_secret(value)
    assert w.observe()["text"] == "Yes, deliver here\nPay ₹79" and not w.secrets.leaks("yes ok no")
    password = {"id": "pw", "kind": "fill", "label": "Password", "role": "textbox", "value": "", "node": 40,
                "constraints": {"type": "password"}}
    assert w.value(password, "[secret:answer1]", w.state["page"]) == ({"text": "yes"}, "[secret:answer1]")
    sent = []

    class Response:
        status_code, is_error = 200, False

        def json(self):
            options = ["yes", "no", "change"]
            return {"answers": {"approval": {"choice": "yes", "probabilities": dict(zip(options, [0.9, 0.05, 0.05])),
                                             "confidence": 0.9}}}

    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test")
    monkeypatch.setattr(model.CLIENT, "post", lambda url, json, headers: sent.append(json) or Response())
    token = SECRETS.set(w.secrets)
    try:
        assert jev.approval("Diet Coke ₹79. Reply yes to go ahead.", "yes") == "yes"
    finally:
        SECRETS.reset(token)
    assert "Reply: yes" in sent[0]["state"]["page"]["text"]  # the user's yes reaches Jev as it was typed


def test_profile_values_learned_by_one_task_are_hidden_from_the_next_model_call_of_another(tmp_path, monkeypatch):
    first = worker_with(tmp_path, replies=[{"text": "me@example.com"}], profile={"email": ""})
    second = worker_with(tmp_path, memory=first.memory)  # the chat core gives every task the same memory
    assert first.profile_value("email") == "me@example.com"
    assert second.secrets.text("mail me@example.com") == "mail me@example.com"  # not yet: no command since
    script_planner(monkeypatch, turn("STOP", message="Done here."))
    second.command("tick")
    assert second.secrets.text("mail me@example.com") == "mail [profile:email]"


# ---- final review, Group C: caps count only the agent's own time, loops are bounded by ticks, stopped means stopped

def test_time_the_user_took_to_answer_does_not_count_toward_the_30_minutes(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn(subgoals=[sub("choose Office")]))
    jev_checks(monkeypatch)
    w.state["started_at"] = time.perf_counter() - 31 * 60
    w.state["waited_ms"] = 25 * 60 * 1000  # the user answered a question after 25 minutes
    w.command("tick")
    assert w.state["status"] not in STOPPED and not any("30 minutes" in m["text"] for m in w.told)
    w.state["waited_ms"] = 0
    w.command("tick")
    assert w.state["status"] == "blocked" and "Stopped after 30 minutes" in w.told[-1]["text"]


def test_a_pending_commit_is_watched_before_the_caps_and_a_cap_with_one_pending_warns(tmp_path, monkeypatch):
    from assistant.errors import Halt

    w = worker_with(tmp_path)
    w.state["commit"] = {"label": "Pay ₹79", "since": 0, "goal": "Order Diet Coke"}
    w.state["meter"]["mini"] = 0.26  # over the spend cap while the outcome of a commit is unknown
    monkeypatch.setattr(jev, "after_commit", lambda *a: "confirmed")
    w.command("tick")
    assert w.state["done"] == ["Pay ₹79"] and w.state["commit"] is None and w.state["status"] not in STOPPED
    w.command("tick")
    assert w.state["status"] == "blocked" and "model-spend limit" in w.told[-1]["text"]
    w.state["commit"] = {"label": "Pay ₹79", "since": 0, "goal": "Order Diet Coke"}
    with pytest.raises(Halt, match="Pay ₹79 may already have gone through; check it before trying again."):
        w.check_caps()


def test_the_planner_saying_continue_to_a_jev_that_thinks_is_bounded_by_ticks(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "keep trying"}])
    script_planner(monkeypatch, turn(subgoals=[sub("choose the Office address")]), turn())
    script_jev(monkeypatch, {"next": "THINK"})
    jev_checks(monkeypatch)
    ticks = 0
    while w.state["status"] not in STOPPED and ticks < 40:
        w.command("tick")
        ticks += 1
        if ticks == 7:
            assert w.asked == []
        if ticks == 8:
            assert len(w.asked) == 1 and "I'm stuck" in w.asked[0]["text"]
    assert ticks == 16 and w.state["status"] == "blocked" and len(w.asked) == 1
    assert "going in circles on shop.test" in w.told[-1]["text"] and not w.browser.act.called


def test_an_executed_step_resets_the_count_of_idle_ticks(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn(subgoals=[sub("choose the Office address")]), turn())
    script_jev(monkeypatch, *([{"next": "THINK"}] * 6), {"next": "CLICK", "click_target": "Office"},
               {"next": "THINK"})
    jev_checks(monkeypatch)
    for _ in range(14):
        w.command("tick")
    assert w.browser.act.call_count == 1 and w.asked == [] and w.state["status"] not in STOPPED


def test_the_code_question_asks_for_the_field_still_empty(tmp_path, monkeypatch):
    page = code_page("[secret]")  # the code was typed; the CVV is still empty
    w = worker_with(tmp_path, page=page, replies=[{"text": "123"}])
    script_planner(monkeypatch, turn(subgoals=[sub("pay")]))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Code", "blocker": "code"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert len(w.asked) == 1 and "card's CVV" in w.asked[0]["text"]
    operation, action = w.browser.act.call_args.args[:2]
    assert (operation, action["label"]) == ("TYPE_TEXT", "CVV") and w.browser.act.call_args.kwargs["text"] == "123"


def test_a_stopped_task_does_nothing_when_ticked_again(tmp_path, monkeypatch):
    w = office_click(tmp_path, monkeypatch)
    w.browser.act.side_effect = ExecutionUncertain("the click may have landed")
    w.command("tick")
    assert w.state["status"] == "blocked" and w.browser.act.call_count == 1
    monkeypatch.setattr(planner, "turn", Mock(side_effect=AssertionError("the planner was asked")))
    monkeypatch.setattr(jev, "request", Mock(side_effect=AssertionError("Jev was asked")))
    told = len(w.told)
    assert w.command("tick")["status"] == "blocked"
    assert w.browser.act.call_count == 1 and len(w.told) == told


@pytest.mark.parametrize("text", ["What's your pin code?", "Do you have a promo code?", "Which zip code?",
                                  "Your postal code, please", "Enter the PIN-code of the address"])
def test_a_pin_code_or_promo_code_is_not_a_secret_ask(tmp_path, monkeypatch, text):
    from assistant.worker import CODE_WORDS

    assert not CODE_WORDS.search(text)
    w = worker_with(tmp_path, replies=[{"text": "560001"}])  # the page has a code field
    script_planner(monkeypatch, turn("ASK", message=text))
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.asked[0]["kind"] == "text" and "560001" in w.state["chat"][-1]["text"]


@pytest.mark.parametrize("text", ["Enter the code", "What's the OTP?", "Your PIN, please", "Enter your passcode"])
def test_a_code_otp_or_pin_is_still_a_secret_ask(text):
    from assistant.worker import CODE_WORDS

    assert CODE_WORDS.search(text)


# ---- Plan 7 Task 3: memory instead of the profile


def test_an_id_the_task_needs_is_asked_hidden_once_and_kept_encrypted(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "1234567890"}])
    script_planner(monkeypatch, turn(subgoals=[sub("enter the account", values=[("BESCOM account number",
                                                                                 "[profile:bescom_account]")])]))
    sent = script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Phone number"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert w.asked[0] == {"from": "agent", "kind": "secret", "text": "What's your BESCOM account number? It stays "
                          "hidden from the models, and I'll remember it for next time."}
    assert w.browser.act.call_args.kwargs["text"] == "1234567890"
    assert w.memory.id_value("bescom_account") == "1234567890"
    assert "Saved your BESCOM account number. The models never see it." in [m["text"] for m in w.told]
    assert "1234567890" not in json.dumps([s for s, _ in sent]) + json.dumps(w.trace(), default=str)


def test_the_planner_reads_memory_and_never_an_id_value(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    w.memory.remember("address", "12, 4th Cross, Koramangala", label="Home", tags=["address"])
    seen = script_planner(monkeypatch, turn("STOP", message="Done here."))
    w.command("tick")
    block = seen[0]["memory"]
    assert {"key": "phone", "label": "phone number"} in block["ids"] and block["addresses"][0]["label"] == "Home"
    assert "profile" not in seen[0] and "98765" not in json.dumps(seen[0], default=str)


def test_asking_for_an_id_memory_holds_is_refused(tmp_path, monkeypatch):
    w = worker_with(tmp_path, profile={"bescom_account": "1234567890"})
    script_planner(monkeypatch, turn("ASK", message="What is your BESCOM account number?"))
    w.command("tick")
    assert "Memory has it: use [profile:bescom_account]" in w.state["feedback"][-1] and not w.asked


def test_a_forgotten_id_stays_hidden_in_the_running_task_and_is_asked_again_next_time(tmp_path, monkeypatch):
    w = worker_with(tmp_path, profile={"email": "me@example.com"})
    script_planner(monkeypatch, turn("STOP", message="Done here."))
    w.memory.forget("id:email")
    w.command("tick")  # the running task still hides what it already knew
    assert w.secrets.text("mail me@example.com") == "mail [profile:email]"
    later = worker_with(tmp_path, replies=[{"text": "new@example.com"}], memory=w.memory)
    assert later.profile_value("email") == "new@example.com" and later.asked[0]["kind"] == "secret"


def test_an_id_that_is_never_kept_is_used_for_this_task_only(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "4111 1111 1111 1111"}])
    assert w.profile_value("consumer_number") == "4111 1111 1111 1111"
    assert w.profile_value("consumer_number") == "4111 1111 1111 1111" and len(w.asked) == 1
    assert any("for this task only" in m["text"] for m in w.told)
    with pytest.raises(KeyError):
        w.memory.id_value("consumer_number")
    assert w.secrets.text("no. 4111 1111 1111 1111") == "no. [profile:consumer_number]" and "ids" not in w.snapshot()


def test_screenshots_blur_an_id_kept_for_this_task_only_and_one_forgotten_mid_task(tmp_path):
    w = worker_with(tmp_path, replies=[{"text": "4111 1111 1111 1111"}], profile={"email": "me@example.com"})
    w.profile_value("consumer_number")
    assert "4111 1111 1111 1111" in w.hidden_details() and "me@example.com" in w.hidden_details()
    w.memory.forget("id:email")
    assert "me@example.com" in w.hidden_details() and "4111 1111 1111 1111" in w.hidden_details()


def test_a_label_taken_from_the_page_is_cleaned_and_capped(tmp_path):
    w = worker_with(tmp_path, replies=[{"text": "ABC-123"}])
    w.profile_value("shop_code", {"label": "Your\nshop\tcode " + "x" * 100})
    assert len(w.asked[0]["text"].split("What's your ")[1].split("? It stays")[0]) == 60
    assert "\n" not in w.asked[0]["text"] and "\t" not in w.asked[0]["text"]


# ---- Plan 7 Task 5: the commit carries what was approved


def test_a_confirmed_order_records_the_approved_cart_total_and_who_said_yes(tmp_path, monkeypatch):
    page = fake_page(text="Checkout\nDiet Coke 300 ml × 1 — ₹40\nDelivery ₹39\nTotal ₹79\nPay ₹79")
    w = worker_with(tmp_path, replies=[{"text": "yes"}], page=page)
    script_planner(monkeypatch, turn(subgoals=[sub("place the order", kind="commit")]), turn())
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Pay"})
    jev_checks(monkeypatch)
    for _ in range(3):  # plan; ask and bind the yes; place the order and see it confirmed
        w.command("tick")
    assert w.state["orders"] == [{"label": "Pay ₹79", "site": "shop.test", "total": "₹79", "by": "user",
                                  "cart": [{"item": "diet coke 300 ml", "qty": 1}]}]


# ---- Plan 7 Task 6: what a habit is made from


def test_history_keeps_the_page_title_and_the_start(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn(subgoals=[sub("deliver to home")]))
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Home"})
    jev_checks(monkeypatch)
    w.command("tick")  # the plan
    w.command("tick")  # Jev's click
    last = w.state["history"][-1]
    assert (last["action"], last["title"], last["relayed"]) == ("Home", "Shop", False)
    assert w.state["start"] == "https://shop.test/checkout"
    assert w.state["idle_ticks"] == 1  # the planning tick ran no step; the click tick did
    w.count_idle(len(w.state["history"]), w.replies())  # a tick with no step and no reply
    assert w.state["idle_ticks"] == 2
