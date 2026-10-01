"""The worker over a mock browser with Jev and the planner scripted: who decides each step, what reaches the
user, and what reaches a model."""

import json
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from fakes import fake_page, jev_checks, script_jev, script_planner, store, sub, turn, worker_with

from assistant import executor, jev, planner, rules
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
    assert len(seen) == 1 and seen[0]["reason"] == "start" and len(sent) == 2  # Jev's candidates for the plan, a step
    assert [j["kind"] for j in w.state["jev"]] == ["refresh", "act"]
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
    seen = script_planner(monkeypatch, turn(subgoals=[sub("fill the form")]),
                          turn("TYPE", pick=1, text="080 4040 1234"))  # a phone box takes a phone number
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Phone number"})
    jev_checks(monkeypatch)
    for _ in range(3):
        w.command("tick")
    assert seen[1]["candidates"][0]["element"] == "Phone number"
    assert w.browser.act.call_args.kwargs["text"] == "080 4040 1234"


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


# The user chose to pay by card: only then is a card detail asked for.
CHOSE_CARD = [{"from": "agent", "text": "How would you like to pay?\n1. UPI\n2. Card"},
              {"from": "user", "text": "“Card” → option 2: Card"}]


def card_page():
    page = fake_page()
    page["actions"].append({"id": "e9", "kind": "fill", "label": "Card number", "role": "textbox", "value": "",
                            "node": 20, "constraints": {}})
    return page


def test_card_details_are_asked_hidden_and_never_reach_a_model_or_a_log(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=card_page(), replies=[{"text": "4111 1111 1111 1111"}])
    w.state["chat"] = [dict(c) for c in CHOSE_CARD]
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
    page = fake_page(text="Verify\nSelect all images with traffic lights\nClick verify once there are none left")
    w = worker_with(tmp_path, page=page, replies=[{"text": "2"}])
    w.browser.marked_screenshot = Mock(return_value="numbered")
    script_planner(monkeypatch, turn(subgoals=[sub("sign in")]))
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Home", "blocker": "captcha"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert w.asked[0]["kind"] == "image" and w.asked[0]["image"] == "numbered"
    assert w.browser.act.call_args.args[0] == "CLICK" and w.browser.act.call_args.args[1]["label"] == "Office"


def test_jev_picks_which_value_goes_into_an_unclear_field(tmp_path, monkeypatch):
    page = fake_page()  # a free-text field: a phone box would refuse a time
    page["actions"].append({"id": "e11", "kind": "fill", "label": "Special requests", "role": "textbox", "value": "",
                            "node": 22})
    page["fingerprint"] = fingerprint(page)
    w = worker_with(tmp_path, page=page)
    script_planner(monkeypatch, turn(subgoals=[sub("fill the form", values=[("customer", "Test User"),
                                                                            ("time", "19:00")])]))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Special requests"})
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
    w.state["chat"] = [dict(c) for c in CHOSE_CARD]
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
    w = worker_with(tmp_path, goal="Find the Office address on the shop")
    office, home = sub("choose the Office address"), sub("pick the Home radio button")
    # Each answer to an unsure Jev gives Jev another subgoal, so its CONTINUE is taken: an idle tick, not a refusal.
    script_planner(monkeypatch, *[turn(subgoals=[(office, home)[n % 2]]) for n in range(16)])
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
    page = fake_page()  # the only value never goes into the phone box by default: a consumer number field takes it
    page["actions"].append({"id": "e10", "kind": "fill", "label": "Consumer number", "role": "textbox", "value": "",
                            "node": 21, "constraints": {}})
    page["fingerprint"] = fingerprint(page)
    w = worker_with(tmp_path, page=page, replies=[{"text": "1234567890"}])
    script_planner(monkeypatch, turn(subgoals=[sub("enter the account", values=[("BESCOM account number",
                                                                                 "[profile:bescom_account]")])]))
    sent = script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Consumer number"})
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
    assert w.state["idle_ticks"] == 0  # the planning tick runs no step in every run: that is not idle
    w.count_idle(len(w.state["history"]), w.replies())  # a first tick with no step and no reply
    assert w.state["idle_ticks"] == 0
    w.count_idle(len(w.state["history"]), w.replies())  # a second one in a row
    assert w.state["idle_ticks"] == 1 and w.state["idle"] == 2


# ---- Plan 7 Task 8: habit replay in the worker


def hstep(subgoal, op, name, **extra):
    return {"subgoal": subgoal, "op": op, "name": name, "key": None, "value": None, "secret": False, "commit": False,
            "url": "shop.test/checkout", "title": "Shop", **extra}


def habit_for(*steps, plan=None):
    return {"id": 7, "title": "Shop · Diet Coke", "goal": "Order Diet Coke", "start": "https://shop.test/checkout",
            "plan": plan or [sub("choose the Office address", "Office is selected"), sub("pay", kind="commit")],
            "steps": list(steps), "cart": []}


def test_the_same_errand_skips_the_start_turn_and_trusts_a_unique_expected_control(tmp_path, monkeypatch):
    w = worker_with(tmp_path, habit=habit_for(hstep(0, "CLICK", "office")), sameness="same")
    seen = script_planner(monkeypatch, turn())
    script_jev(monkeypatch, {"next": ("CLICK", 0.4), "click_target": "Office"})  # under ACT_P: Phase A would escalate
    jev_checks(monkeypatch)
    w.command("tick")
    assert seen == [] and w.told[0]["text"] == "Same as last time: Shop · Diet Coke."
    assert w.browser.act.call_args.args[1]["label"] == "Office"
    assert (w.state["replay"]["expected"], w.state["replay"]["matched"]) == (1, 1)
    assert w.trace()["replay"]["matched"] == 1


def test_a_recorded_label_shown_twice_never_skips_the_gates(tmp_path, monkeypatch):
    twice = fake_page(actions=[*fake_page()["actions"],
                               {"id": "e9", "kind": "click", "label": "Office", "role": "radio", "node": 20}])
    w = worker_with(tmp_path, page=twice, habit=habit_for(hstep(0, "CLICK", "office")), sameness="same")
    seen = script_planner(monkeypatch, turn())
    script_jev(monkeypatch, {"next": ("CLICK", 0.4), "click_target": "Office"})
    jev_checks(monkeypatch)
    w.command("tick")
    assert not w.browser.act.called and seen[0]["reason"] == "unsure"
    assert w.state["replay"]["fallbacks"] == [{"subgoal": 0, "step": 1, "reason": "unsure"}]


def test_another_pick_that_passes_the_gates_leaves_replay_for_that_subgoal_only(tmp_path, monkeypatch):
    plan = [sub("choose the Office address"), sub("choose the Home address")]  # no commit: no approval asked
    w = worker_with(tmp_path, habit=habit_for(hstep(0, "CLICK", "office"), hstep(1, "CLICK", "home"), plan=plan),
                    sameness="same")
    script_planner(monkeypatch, turn())
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Home"}, {"next": "DONE"},
               {"next": ("CLICK", 0.4), "click_target": "Home"})
    jev_checks(monkeypatch)
    for _ in range(3):
        w.command("tick")
    record = w.state["replay"]
    assert record["fallbacks"] == [{"subgoal": 0, "step": 1, "reason": "Jev chose home"}] and record["left"] == [0]
    assert (record["expected"], record["matched"]) == (2, 1)  # the next subgoal re-joined replay
    assert [c.args[1]["label"] for c in w.browser.act.call_args_list] == ["Home", "Home"]


def test_a_similar_errand_plans_once_with_the_habit_and_replays_only_a_plan_of_its_shape(tmp_path, monkeypatch):
    habit = habit_for(hstep(0, "CLICK", "office"))
    w = worker_with(tmp_path, habit=habit, sameness="similar")
    seen = script_planner(monkeypatch, turn(subgoals=[sub("choose the Home address"), sub("pay", kind="commit")]))
    jev_checks(monkeypatch)
    w.command("tick")
    assert seen[0]["reason"] == "start" and seen[0]["habit"]["plan"] == habit["plan"] and w.state["replay"]["on"]
    other = worker_with(tmp_path, habit=habit, sameness="similar")
    script_planner(monkeypatch, turn(subgoals=[sub("ask which address", kind="ask"), sub("pay", kind="commit")]))
    other.command("tick")
    assert other.state["replay"]["on"] is False


def test_after_a_step_the_next_expected_control_on_the_page_skips_the_re_reads(tmp_path):
    office = next(a for a in fake_page()["actions"] if a["label"] == "Office")
    plain = worker_with(tmp_path)
    replaying = worker_with(tmp_path, habit=habit_for(hstep(0, "CLICK", "office"), hstep(0, "CLICK", "pay")),
                            sameness="same")
    reads = []
    for w in (plain, replaying):
        before = w.browser.observe.call_count
        w.run(w.state["page"], "CLICK", office, {}, None)
        reads.append(w.browser.observe.call_count - before)
    assert reads == [4, 1]  # the same page: three looks again, unless the habit's next control is already there


def test_a_secret_step_is_typed_as_in_phase_a_and_replay_moves_past_it(tmp_path):
    code = next(a for a in fake_page()["actions"] if a["label"] == "Code")
    w = worker_with(tmp_path, habit=habit_for(hstep(0, "TYPE_TEXT", "code", secret=True), hstep(0, "CLICK", "pay")),
                    sameness="same")
    w.run(w.state["page"], "TYPE_TEXT", code, {"text": "123456"}, "[secret:answer1]")
    assert w.state["replay"]["used"] == {"0": 1}


def test_an_expected_step_that_changed_nothing_twice_keeps_the_loop_ladder(tmp_path, monkeypatch):
    def looped(confidence):
        w = worker_with(tmp_path, habit=habit_for(hstep(0, "CLICK", "office")), sameness="same")
        w.state["history"] = [{"step": n, "subgoal": 0, "operation": "CLICK", "action": "Office", "page_changed": False,
                               "url": "https://shop.test/checkout"} for n in (1, 2)]
        seen = script_planner(monkeypatch, turn())
        script_jev(monkeypatch, {"next": ("CLICK", confidence), "click_target": "Office"})
        jev_checks(monkeypatch)
        w.command("tick")
        return w, seen

    unsure, seen = looped(0.4)  # replay never trusts a step the ladder hands to the planner
    assert not unsure.browser.act.called and seen[0]["reason"] == "unsure"
    sure, _ = looped(0.9)  # Jev's next-best once, as in Phase A
    assert sure.browser.act.call_args.args[1]["label"] != "Office"
    assert (sure.state["replay"]["expected"], sure.state["replay"]["fallbacks"]) == (0, [])


def test_a_captcha_square_the_user_picked_never_moves_replay(tmp_path):
    office = next(a for a in fake_page()["actions"] if a["label"] == "Office")
    w = worker_with(tmp_path, habit=habit_for(hstep(0, "CLICK", "office")), sameness="same")
    w.state["relaying"] = True
    w.run(w.state["page"], "CLICK", office, {}, None)
    assert w.state["replay"]["used"] == {}


def test_a_later_plan_of_another_shape_ends_replay_and_one_of_the_same_shape_keeps_it(tmp_path, monkeypatch):
    def replanned(*subgoals):
        w = worker_with(tmp_path, habit=habit_for(hstep(0, "CLICK", "office")), sameness="same")
        w.state["reason"] = "replan"
        script_planner(monkeypatch, turn(subgoals=list(subgoals)))
        jev_checks(monkeypatch)
        w.command("tick")
        return w.state["replay"]["on"]

    assert replanned(sub("choose the Office address"), sub("pay", kind="commit")) is True
    assert replanned(sub("open the address list"), sub("choose Office"), sub("pay", kind="commit")) is False


# ---- cleanup wave: replay never forces a commit; Phase A's blockers, DONE, and hidden asks win


def commit_habit(commit=True, kind="commit"):
    plan = [sub("choose the Office address"), sub("pay", kind=kind)]
    return habit_for(hstep(1, "CLICK", "pay", commit=commit), plan=plan)


def paying(tmp_path, monkeypatch, confidence, habit, **options):
    w = worker_with(tmp_path, habit=habit, sameness="same", **options)
    w.state["current"] = 1  # at the pay subgoal
    seen = script_planner(monkeypatch, turn())
    script_jev(monkeypatch, {"next": ("CLICK", confidence), "click_target": "Pay"})
    jev_checks(monkeypatch)
    w.command("tick")
    return w, seen


@pytest.mark.parametrize("habit", [commit_habit(), commit_habit(commit=False), commit_habit(kind="act")],
                         ids=["commit-step", "commit-subgoal", "commit-step-act-subgoal"])
def test_an_unsure_pick_of_a_unique_recorded_commit_control_goes_to_the_planner(tmp_path, monkeypatch, habit):
    w, seen = paying(tmp_path, monkeypatch, 0.4, habit)
    assert not w.browser.act.called and seen[0]["reason"] == "unsure"
    assert w.state["replay"]["fallbacks"] == [{"subgoal": 1, "step": 1, "reason": "unsure"}]


def test_a_recorded_commit_step_is_not_forced_under_pre_authorisation(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_PREAUTH", "on")
    w, seen = paying(tmp_path, monkeypatch, 0.4, commit_habit(), preauth=True, cap=100.0)
    assert not w.browser.act.called and seen[0]["reason"] == "unsure"
    assert not any(m["text"].startswith("Placing") for m in w.state["messages"])
    assert w.state["replay"]["matched"] == 0


def test_a_confident_pick_of_the_recorded_commit_step_still_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_PREAUTH", "on")
    w, seen = paying(tmp_path, monkeypatch, 0.9, commit_habit(), preauth=True, cap=100.0)
    assert seen == [] and (w.state["replay"]["expected"], w.state["replay"]["matched"]) == (1, 1)
    assert w.browser.act.call_args.args[1]["label"] == "Pay ₹79"
    assert any(m["text"].startswith("Placing") for m in w.state["messages"])


def test_a_blocker_and_done_beat_a_pending_expected_step(tmp_path, monkeypatch):
    def ticked(picks):
        w = worker_with(tmp_path, habit=habit_for(hstep(0, "CLICK", "office")), sameness="same")
        seen = script_planner(monkeypatch, turn(op="ASK", message="How should I pay?", options=["UPI", "Cash"]))
        script_jev(monkeypatch, picks)
        jev_checks(monkeypatch)
        w.command("tick")
        return w, seen

    blocked, seen = ticked({"next": ("CLICK", 0.4), "click_target": "Office", "blocker": "payment"})
    assert not blocked.browser.act.called and seen[0]["reason"] == "blocker:payment"
    assert (blocked.state["replay"]["expected"], blocked.state["replay"]["fallbacks"]) == (0, [])
    done, _ = ticked({"next": "DONE", "click_target": "Office"})
    assert not done.browser.act.called and done.state["current"] == 1
    assert (done.state["replay"]["expected"], done.state["replay"]["fallbacks"]) == (0, [])


def test_a_secret_expected_step_is_declined_and_asked_hidden_as_in_phase_a(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "4 8 1 5 1 6"}],
                    habit=habit_for(hstep(0, "TYPE_TEXT", "code", secret=True)), sameness="same")
    seen = script_planner(monkeypatch, turn())
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Code", "blocker": "code"})
    jev_checks(monkeypatch)
    page = w.state["page"]  # replay's own say on Jev's pick of the secret step: none
    state, questions, heads, controls = executor.request(page, w.subgoal(), {"goal": w.state["goal"], "answers": [],
                                                                             "recent": []}, None)
    answers = jev.request(state, questions)
    assert w.guided(executor.decide(answers, questions, heads, controls), answers, page, controls) is None
    w.command("tick")
    w.command("tick")
    assert seen == [] and w.asked[0]["kind"] == "secret"
    assert w.browser.act.call_args.kwargs["text"] == "481516" and w.state["history"][-1]["text"] == "[secret:answer1]"
    assert w.state["replay"]["expected"] == 0 and w.state["replay"]["used"] == {"0": 1}


def test_close_marks_the_worker_closed_after_the_final_trace(tmp_path):
    w = worker_with(tmp_path)
    assert w.state["closed"] is False
    w.close()
    assert w.state["closed"] is True and Path(w.state["trace_path"]).exists()
    assert "closed" not in json.loads(Path(w.state["trace_path"]).read_text())


# ---- live fix: the planner never PICKs from candidates Jev chose on another page


def loading_page():
    """An SPA search page still loading: no text, only its search box."""
    return fake_page(text="", url="https://shop.test/search", actions=[
        {"id": "s1", "kind": "fill", "label": "Search for products", "role": "searchbox", "value": "", "node": 1},
        {"id": "wait", "kind": "wait", "label": "Wait", "node": None},
    ])


def tiles_page():
    """The same search page once loaded: product tiles to add."""
    return fake_page(text="Results for diet coke\nDiet Coke Can 300 ml ₹40\nCoke Zero ₹40\nSprite ₹35",
                     url="https://shop.test/search", actions=[
        {"id": "s1", "kind": "fill", "label": "Search for products", "role": "searchbox", "value": "diet coke",
         "node": 1},
        {"id": "t1", "kind": "click", "label": "Add Coke Zero", "role": "button", "node": 21},
        {"id": "t2", "kind": "click", "label": "Add Sprite", "role": "button", "node": 22},
        {"id": "t3", "kind": "click", "label": "Add Diet Coke Can", "role": "button", "node": 23},
        {"id": "wait", "kind": "wait", "label": "Wait", "node": None},
    ])


def jev_on_both(state):
    """Jev: the loading page shows the search subgoal done; on the tiles, Diet Coke is the control to press."""
    if "Diet Coke" in state["page"]["text"]:
        return {"next": "CLICK", "click_target": "Add Diet Coke"}
    return {"next": "DONE"}


def planner_picking(monkeypatch, element, start):
    """A planner that plans with `start`, then PICKs the candidate showing `element`, or (as the live planner did) a
    number from the page's element list when no candidate shows it. Returns the payloads it was shown."""
    seen = []

    def fake_turn(payload, image=None):
        seen.append(payload)
        if len(seen) == 1:
            return start, {"model": "fake", "latency_ms": 1}
        n = next((c["n"] for c in payload["candidates"] if element in c["element"]), 16)
        return turn("PICK", pick=n), {"model": "fake", "latency_ms": 1}

    monkeypatch.setattr(planner, "turn", fake_turn)
    return seen


def test_a_page_that_changed_after_jevs_done_gets_fresh_candidates_before_the_planner_picks(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=loading_page())
    seen = planner_picking(monkeypatch, "Diet Coke", turn(subgoals=[sub("open search", "the search input shows")]))
    sent = script_jev(monkeypatch, jev_on_both)
    jev_checks(monkeypatch)
    w.command("tick")  # the plan
    w.command("tick")  # Jev: the search subgoal is done; the plan has nothing left
    assert w.state["current"] == 1 and w.state["reason"] == "next"
    w.browser.observe.return_value = tiles_page()  # the SPA finished loading
    w.browser.fresh.return_value = False
    w.command("tick")  # the planner, on the loaded page
    assert seen[-1]["reason"] == "next" and any("Diet Coke" in c["element"] for c in seen[-1]["candidates"])
    operation, action = w.browser.act.call_args.args[:2]
    assert (operation, action["label"]) == ("CLICK", "Add Diet Coke Can")
    assert not any("names one of Jev's candidates" in f for f in w.state["feedback"])
    assert w.state["jev"][-1]["kind"] == "refresh" and "Diet Coke" in sent[-1][0]["page"]["text"]
    assert w.state["plan"][-1]["do"] == "open search" and w.state["current"] == 1  # the refresh changed no plan
    assert len(w.state["history"]) == 1  # only the planner's click: the refresh itself acted on nothing


@pytest.mark.parametrize("error", [ValueError("bad answer"), RuntimeError("Jev is down")])
def test_a_refresh_that_fails_keeps_the_old_candidates_and_the_tick_goes_on(tmp_path, monkeypatch, error):
    w = worker_with(tmp_path, page=loading_page())
    seen = planner_picking(monkeypatch, "Diet Coke", turn(subgoals=[sub("open search", "the search input shows")]))
    script_jev(monkeypatch, jev_on_both)
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    calls = []

    def broken(state, questions):
        calls.append(state)
        raise error

    monkeypatch.setattr(jev, "request", broken)
    old = list(w.state["candidates"])
    w.browser.observe.return_value = tiles_page()
    w.browser.fresh.return_value = False
    w.command("tick")
    assert len(seen) == 2 and seen[-1]["candidates"] == [
        {"n": c["n"], "op": c["op"], "element": c["element"], "p": round(c["p"], 2)} for c in old]
    assert len(calls) == (2 if isinstance(error, ValueError) else 1)  # an invalid answer is asked once more
    assert w.state["status"] == "ready" and w.state["model_errors"] == 0 and w.state["errors"] == []
    assert w.state["jev"][-1]["kind"] != "refresh"


def test_a_planner_turn_on_the_page_jev_just_read_asks_jev_nothing_more(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    seen = script_planner(monkeypatch, turn(subgoals=[sub("choose the Office address")]), turn("PICK", pick=1))
    sent = script_jev(monkeypatch, {"next": ("CLICK", 0.4), "click_target": "Office"})
    jev_checks(monkeypatch)
    w.command("tick")  # the plan: Jev has read no page yet, so it is asked for candidates once
    assert len(sent) == 1 and w.state["jev"][-1]["kind"] == "refresh" and seen[0]["candidates"]
    w.command("tick")  # Jev is unsure; the planner picks from the candidates of this same page
    assert len(sent) == 2 and [j["kind"] for j in w.state["jev"]] == ["refresh", "think"]
    assert w.browser.act.call_args.args[1]["label"] == "Office"


def test_a_refused_pick_lists_the_candidates_by_number_and_name(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn(subgoals=[sub()]), turn("PICK", pick=9))
    script_jev(monkeypatch, {"next": ("CLICK", 0.4), "click_target": "Office"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    feedback = w.state["feedback"][-1]
    assert "names one of Jev's candidates by its number" in feedback and not w.browser.act.called
    listed = ", ".join(f"{c['n']} {c['element']}" for c in w.state["candidates"])
    assert f"candidates: {listed}" in feedback and "Office" in feedback


def test_planner_turns_keep_the_candidates_and_feedback_they_were_shown(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn(subgoals=[sub()]), turn("PICK", pick=9))
    script_jev(monkeypatch, {"next": ("CLICK", 0.4), "click_target": "Office"})
    jev_checks(monkeypatch)
    for _ in range(9):  # a refused pick, again and again: feedback piles up across turns until a replan
        w.command("tick")
    first, last = w.state["turns"][0], w.state["turns"][-1]
    assert first["feedback"] == [] and 0 < len(first["candidates"]) <= 5
    assert set(first["candidates"][0]) == {"n", "op", "element", "p"}
    assert any("names one of Jev's candidates" in f for t in w.state["turns"] for f in t["feedback"])
    assert all(len(t["feedback"]) <= 5 and len(t["candidates"]) <= 5 for t in w.state["turns"])
    assert last["candidates"] and last["reason"]
    w.close()
    trace = json.loads(Path(w.state["trace_path"]).read_text())
    assert trace["turns"][-1]["candidates"] == last["candidates"] and "feedback" in trace["turns"][-1]


ORDER_PLAN = [sub("open the cart"), sub("choose how to pay", kind="ask"), sub("place the order", kind="commit"),
              sub("report the order", kind="report")]


def planned_order(tmp_path, monkeypatch, *replans, goal="Order Diet Coke on Instamart", first=ORDER_PLAN):
    """A worker whose first plan is `first` (by default one ending in the commit), then a replan turn for each of
    `replans` (lists of subgoals). `w.payloads` holds what the planner was shown, one per planner call."""
    w = worker_with(tmp_path, goal=goal)
    w.payloads = script_planner(monkeypatch, turn(subgoals=first), *(turn(subgoals=r) for r in replans))
    script_jev(monkeypatch, {"next": "THINK"})
    jev_checks(monkeypatch)
    w.command("tick")
    return w, first


def replan(w):
    w.state["reason"] = "replan"
    w.command("tick")


def steps(w):
    return [(g["do"], g["kind"]) for g in w.state["plan"]]


def test_an_order_replan_that_drops_the_commit_gets_the_old_commit_and_report_back_with_no_planner_call(
        tmp_path, monkeypatch):
    w, _ = planned_order(tmp_path, monkeypatch, [sub("open the fees"), sub("ask about the small cart fee", kind="ask"),
                                                 sub("report the bill", kind="report")])
    replan(w)  # "open the fees" rewrites "open the cart"; the payment question nothing rewrote comes back too
    assert steps(w) == [("open the fees", "act"), ("ask about the small cart fee", "ask"), ("choose how to pay", "ask"),
                        ("place the order", "commit"), ("report the order", "report")]
    assert w.state["feedback"] == [] and w.state["commit_drops"] == 0 and len(w.payloads) == 2


def test_a_repaired_replan_keeps_the_done_steps_and_takes_the_tail_from_the_current_one(tmp_path, monkeypatch):
    w, _ = planned_order(tmp_path, monkeypatch, [sub("ask about the small cart fee", kind="ask")])
    w.state["current"] = 2  # at the commit: the cart is open and the payment chosen
    replan(w)
    assert steps(w) == [("open the cart", "act"), ("choose how to pay", "ask"), ("ask about the small cart fee", "ask"),
                        ("place the order", "commit"), ("report the order", "report")]


def test_a_repaired_replan_has_each_step_once_and_the_commit_as_a_commit(tmp_path, monkeypatch):
    w, _ = planned_order(tmp_path, monkeypatch, [sub("open the fees"), sub("Place  the order"),
                                                 sub("report the order", kind="report")])
    replan(w)
    assert steps(w) == [("open the fees", "act"), ("choose how to pay", "ask"), ("place the order", "commit"),
                        ("report the order", "report")]


def test_a_repair_keeps_the_new_report_when_the_old_tail_has_none(tmp_path, monkeypatch):
    first = [sub("open the cart"), sub("place the order", kind="commit")]
    w, _ = planned_order(tmp_path, monkeypatch, [sub("open the fees"), sub("report the bill", kind="report")],
                         first=first)
    replan(w)
    assert steps(w) == [("open the fees", "act"), ("place the order", "commit"), ("report the bill", "report")]


def test_an_order_replan_that_keeps_the_commit_is_taken(tmp_path, monkeypatch):
    w, _ = planned_order(tmp_path, monkeypatch, [sub("open the fees"), sub("place the order", kind="commit")])
    replan(w)
    assert [g["do"] for g in w.state["plan"]] == ["open the fees", "place the order"] and w.state["feedback"] == []


NO_COMMIT = [sub("open the cart"), sub("report the order", kind="report")]


def test_an_order_replan_with_no_commit_in_either_plan_is_refused_and_the_plan_kept(tmp_path, monkeypatch):
    w, first = planned_order(tmp_path, monkeypatch, [sub("open the fees"), sub("report the bill", kind="report")],
                             first=NO_COMMIT)
    replan(w)
    assert "must include the final commit subgoal" in w.state["feedback"][-1]
    assert [g["do"] for g in w.state["plan"]] == [g["do"] for g in first] and w.state["reason"] == "replan"


def test_a_replan_with_no_commit_in_either_plan_is_taken_after_three_refusals_and_not_refused_again(
        tmp_path, monkeypatch):
    w, _ = planned_order(tmp_path, monkeypatch, [sub("open the fees")], [sub("open the fees")], [sub("open the fees")],
                         [sub("open the fees")], [sub("open the offers")], first=NO_COMMIT)
    for _ in range(3):
        replan(w)
        assert w.state["plan"][0]["do"] == "open the cart"
    replan(w)
    assert [g["do"] for g in w.state["plan"]] == ["open the fees"]
    calls = len(w.payloads)
    replan(w)  # the planner insisted three times: a later plan without a commit costs no more refusals
    assert [g["do"] for g in w.state["plan"]] == ["open the offers"] and len(w.payloads) == calls + 1


def test_a_plan_with_a_commit_starts_the_refusals_afresh(tmp_path, monkeypatch):
    w, _ = planned_order(tmp_path, monkeypatch, *[[sub("open the fees")]] * 4,
                         [sub("open the cart"), sub("place the order", kind="commit")], [sub("open the fees")],
                         first=NO_COMMIT)
    for _ in range(5):
        replan(w)
    assert w.state["commit_drops"] == 0 and steps(w)[-1] == ("place the order", "commit")
    w.state.update(plan=[sub("open the cart")], current=0)  # the commit subgoal gone, the order not through
    replan(w)
    assert "must include the final commit subgoal" in w.state["feedback"][-1] and steps(w) == [("open the cart", "act")]


@pytest.mark.parametrize("goal", ["What is the price of Diet Coke on Instamart?", "What did I order last on Swiggy?"])
def test_a_lookup_replan_without_a_commit_is_taken(tmp_path, monkeypatch, goal):
    w = worker_with(tmp_path, goal=goal)
    first = [sub("open the cart"), sub("report the price", kind="report")]
    if not rules.commerce_goal(goal):  # a plan with a commit in it: only an order or booking must keep it
        first.insert(1, sub("place the order", kind="commit"))
    script_planner(monkeypatch, turn(subgoals=first), turn(subgoals=[sub("open the orders")]))
    script_jev(monkeypatch, {"next": "THINK"})
    jev_checks(monkeypatch)
    w.command("tick")
    replan(w)
    assert [g["do"] for g in w.state["plan"]] == ["open the orders"]


@pytest.mark.parametrize("kind", ["report", "ask"])
def test_continue_on_a_subgoal_only_the_planner_can_do_is_refused_then_replanned_then_asked(tmp_path, monkeypatch,
                                                                                            kind):
    w = worker_with(tmp_path, replies=[{"text": "place it"}])
    script_planner(monkeypatch, turn(message="Your bill is ₹79."))
    jev_checks(monkeypatch)
    w.state.update(plan=[sub("report the bill", kind=kind)], reason="next")
    for n in range(1, 7):
        w.command("tick")
        if n < 6:
            assert any("CONTINUE does nothing here" in f for f in w.state["feedback"]) and w.asked == []
        if n == 3:
            assert "The goal is not finished" in w.state["feedback"][-1]
            assert "ending with the commit step" in w.state["feedback"][-1] and w.state["reason"] == "replan"
    assert len(w.asked) == 1 and "I'm stuck" in w.asked[0]["text"] and w.state["reason"] == "reply"
    assert not w.browser.act.called


# ---- live bench fixes, batch A: loops on pages that keep changing, plans that keep their middle, safer asks


def rotating(actions):
    """A page whose text changes on every look (a rotating search placeholder) while its controls stay the same."""
    looks = iter(range(1000))
    return lambda *a, **k: fake_page(text=f"Shop\nSearch “item {next(looks)}”", actions=actions)


def test_jevs_pick_that_ran_three_times_from_the_same_controls_is_not_run_again(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(actions=BUTTONS))
    w.browser.observe.side_effect = rotating(BUTTONS)  # every click "changes" the page: only its text
    seen = script_planner(monkeypatch, turn(subgoals=[sub("choose Office")]))
    script_jev(monkeypatch, {"next": "CLICK", "click_target": ("Office", 0.8)})
    jev_checks(monkeypatch)
    for _ in range(5):
        w.command("tick")
    labels = pressed(w)
    assert labels[:3] == ["Office"] * 3 and labels[3] != "Office" and len(labels) == 4  # then Jev's next-best once
    assert all(h["page_changed"] for h in w.state["history"])  # the old guard never saw a repeat
    w.command("tick")
    assert len(pressed(w)) == 4 and seen[-1]["reason"] == "unsure"
    assert "“Office” already ran 3 times from this same page" in seen[-1]["feedback"][-1]


def test_the_planners_pick_that_ran_three_times_is_refused_then_the_user_decides(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(actions=BUTTONS), replies=[{"text": "try Home instead"}])
    w.browser.observe.side_effect = rotating(BUTTONS)
    script_planner(monkeypatch, turn(subgoals=[sub("choose the Office address")]), turn("PICK", pick=1))
    script_jev(monkeypatch, {"next": ("CLICK", 0.4), "click_target": "Office"})  # Jev is unsure every time
    jev_checks(monkeypatch)
    for _ in range(5):
        w.command("tick")
    assert pressed(w) == ["Office"] * 3 and w.asked == []
    assert "“Office” already ran 3 times from this same page" in w.state["feedback"][-1]
    w.command("tick")  # the planner insists: five times on one loop, and the user decides
    assert pressed(w) == ["Office"] * 3 and len(w.asked) == 1 and "I'm stuck" in w.asked[0]["text"]
    assert w.state["reason"] == "reply" and w.state["loop_from"] == 3


def test_a_loop_asked_about_twice_stops_and_a_reply_that_leaves_it_to_me_keeps_the_count(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(actions=BUTTONS), replies=[{"text": "you choose"}] * 3)
    w.browser.observe.side_effect = rotating(BUTTONS)
    script_planner(monkeypatch, turn(subgoals=[sub("choose the Office address")]), turn("PICK", pick=1))
    script_jev(monkeypatch, {"next": ("CLICK", 0.4), "click_target": "Office"})
    jev_checks(monkeypatch)
    ticks = 0
    while w.state["status"] not in STOPPED and ticks < 40:
        w.command("tick")
        ticks += 1
    assert w.state["status"] == "blocked" and "going in circles" in w.told[-1]["text"]
    assert len(w.asked) == 2 and pressed(w) == ["Office"] * 6  # a fresh count after the first answer only


def test_a_loop_the_user_was_asked_about_twice_stops_the_next_time_round(tmp_path):
    from assistant.errors import Halt

    w = worker_with(tmp_path, replies=[{"text": "you choose"}, {"text": "whatever works"}])
    unchanged = {"operation": "CLICK", "action": "Office", "url": w.state["page"]["url"], "page_changed": False}
    w.state["history"] = [unchanged] * 8
    w.track_loop(unchanged)
    assert len(w.asked) == 1 and w.state["loop_from"] == 8  # the first answer starts a fresh count
    w.state["history"] += [unchanged] * 8
    w.track_loop(unchanged)
    assert len(w.asked) == 2 and w.state["loop_from"] == 8  # a second answer that leaves it to me doesn't
    w.state["history"].append(unchanged)
    with pytest.raises(Halt, match="going in circles"):
        w.track_loop(unchanged)
    assert len(w.asked) == 2


def test_scrolling_is_quiet_until_eight_scrolls_in_a_row_on_one_page(tmp_path):
    w = worker_with(tmp_path)
    page, url = w.state["page"], w.state["page"]["url"]
    scroll = {"operation": "SCROLL_DOWN", "action": "Page", "url": url, "page_changed": True}
    w.state["history"] = [scroll] * 7
    assert w.forbidden("SCROLL_DOWN", "Page", page) is None and w.forbidden("WAIT", "Wait", page) is None
    w.state["history"].append({**scroll, "operation": "SCROLL_UP"})
    assert "8 scrolls in a row" in w.forbidden("SCROLL_DOWN", "Page", page)
    w.state["history"].append({**scroll, "operation": "CLICK", "action": "Office"})
    assert w.forbidden("SCROLL_DOWN", "Page", page) is None


def test_the_planner_rewriting_the_same_subgoal_keeps_its_step_count(tmp_path, monkeypatch):
    w = worker_with(tmp_path, goal="Find the price of Britannia white bread on Blinkit")
    script_planner(monkeypatch, turn(subgoals=[sub("Search for Britannia white bread")]))
    jev_checks(monkeypatch)
    w.command("tick")
    w.state.update(subgoal_steps=5, reason="unsure")
    script_planner(monkeypatch, turn("WAIT", subgoals=[sub("search for Britannia white bread using the search box")]))
    w.command("tick")
    assert w.state["subgoal_steps"] == 6  # the same subgoal, reworded: still counting toward the cap
    w.state.update(subgoal_steps=11, reason="unsure")
    w.command("tick")
    assert w.state["subgoal_steps"] == 12
    seen = script_planner(monkeypatch, turn("WAIT", subgoals=[sub("open the bread section")]))
    w.command("tick")  # the cap fires at last
    assert seen[0]["reason"] == "replan" and "12 steps" in seen[0]["feedback"][0]
    assert w.state["subgoal_steps"] == 1
    w.state.update(subgoal_steps=5, reason="unsure")
    script_planner(monkeypatch, turn("WAIT", subgoals=[sub("read the price of the 400 g loaf")]))
    w.command("tick")
    assert w.state["subgoal_steps"] == 1  # another subgoal starts its own count


@pytest.mark.parametrize("reason", ["unsure", "blocker:sign_in"])
def test_continue_when_jev_could_not_decide_is_refused(tmp_path, monkeypatch, reason):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn(subgoals=[sub("choose the Office address")]),
                   turn(message="I will close the sign-in pop-up."))
    jev_checks(monkeypatch)
    w.command("tick")
    w.state["reason"] = reason
    w.command("tick")
    assert "Jev couldn't decide: PICK a candidate, TYPE, ASK, OPEN_URL, LOOK, WAIT or BACK; CONTINUE does nothing " \
           "here" in w.state["feedback"][-1]
    assert w.state["refusals"] == {"CONTINUE::": 1} and w.state["reason"] == "unsure"


def test_continue_with_a_new_subgoal_for_jev_is_taken(tmp_path, monkeypatch):
    w = worker_with(tmp_path, goal="Find the cheapest hotel in Goa on MakeMyTrip")
    script_planner(monkeypatch, turn(subgoals=[sub("choose the Office address")]),
                   turn(subgoals=[sub("close the sign-in pop-up")]))
    jev_checks(monkeypatch)
    w.command("tick")
    w.state["reason"] = "blocker:sign_in"
    w.command("tick")
    assert w.state["feedback"] == [] and w.subgoal()["do"] == "close the sign-in pop-up"


def test_an_order_replan_that_drops_the_payment_question_gets_it_back_before_the_commit(tmp_path, monkeypatch):
    first = [sub("open the cart and view the detailed bill"), sub("choose the payment method", kind="ask"),
             sub("place the order", kind="commit"), sub("report the order", kind="report")]
    w, _ = planned_order(tmp_path, monkeypatch, [sub("ask whether to pay the small cart fee", kind="ask"),
                                                 sub("report the bill", kind="report")], first=first)
    w.state["current"] = 1  # the cart is open: at the payment question
    replan(w)
    assert steps(w) == [("open the cart and view the detailed bill", "act"),
                        ("ask whether to pay the small cart fee", "ask"), ("choose the payment method", "ask"),
                        ("place the order", "commit"), ("report the order", "report")]


def test_an_order_replan_that_drops_the_shopping_steps_gets_them_back_in_order(tmp_path, monkeypatch):
    first = [sub("search for Amul Taaza milk 500 ml"), sub("open the product page"), sub("add one pack to the cart"),
             sub("open the cart"), sub("choose the payment method", kind="ask"), sub("place the order", kind="commit"),
             sub("report the order", kind="report")]
    w, _ = planned_order(tmp_path, monkeypatch, [sub("close the location pop-up"),
                                                 sub("type the phone number into the sign-in field"),
                                                 sub("press continue to get the code")], first=first)
    replan(w)
    assert [do for do, _ in steps(w)] == [
        "close the location pop-up", "type the phone number into the sign-in field", "press continue to get the code",
        "search for Amul Taaza milk 500 ml", "open the product page", "add one pack to the cart", "open the cart",
        "choose the payment method", "place the order", "report the order"]


def test_an_order_replan_that_rewrites_a_step_gets_back_only_the_steps_it_left_out(tmp_path, monkeypatch):
    first = [sub("search for Amul Taaza milk"), sub("open the product page"), sub("place the order", kind="commit"),
             sub("report the order", kind="report")]
    w, _ = planned_order(tmp_path, monkeypatch, [sub("search for Amul Taaza milk 500 ml in the groceries box")],
                         first=first)
    replan(w)
    assert [do for do, _ in steps(w)] == ["search for Amul Taaza milk 500 ml in the groceries box",
                                          "open the product page", "place the order", "report the order"]


def test_six_failed_steps_in_a_row_on_one_page_bring_a_replan_once(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    seen = script_planner(monkeypatch, turn(subgoals=[sub("close the pop-up")]), turn("WAIT"))
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Office", "effect": "failed"})
    jev_checks(monkeypatch)
    w.command("tick")
    url = w.state["page"]["url"]
    w.state["history"] = [{"step": n, "operation": "CLICK", "action": f"Button {n}", "url": url, "page_changed": True,
                           **({"effect": "failed"} if n < 6 else {})} for n in range(1, 7)]
    w.command("tick")
    assert seen[-1]["reason"] == "replan"
    assert seen[-1]["feedback"][-1] == ("Six steps in a row changed nothing useful here: try LOOK for the control, "
                                        "OPEN_URL the target address, or ASK the user.")
    assert pressed(w) == ["Wait"]
    w.command("tick")  # the same streak goes on: Jev acts, and the planner isn't told again
    assert len(seen) == 2 and pressed(w) == ["Wait", "Office"]


RIDE_PAGE = [{"id": "p", "kind": "fill", "label": "pickup", "role": "textbox", "value": "", "node": 1},
             {"id": "l", "kind": "click", "label": "Locate me", "role": "button", "node": 2},
             {"id": "d", "kind": "fill", "label": "drop", "role": "textbox", "value": "", "node": 3}]


def test_a_field_with_no_value_keeps_jevs_other_candidates_and_a_pick_of_it_focuses_it(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(text="Pickup\nDrop", actions=RIDE_PAGE))
    seen = script_planner(monkeypatch, turn(subgoals=[sub("set the pickup to the current location")]),
                          turn("PICK", pick=1))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "pickup"})
    jev_checks(monkeypatch)
    for _ in range(3):  # the plan; Jev types into pickup with no value; the planner picks the field
        w.command("tick")
    listed = seen[1]["candidates"]
    assert listed[0]["element"] == "pickup" and len(listed) > 1 and any("Locate me" in c["element"] for c in listed)
    assert [c["n"] for c in listed] == list(range(1, len(listed) + 1))
    assert sum(c["op"] == "TYPE" and "pickup" in c["element"] for c in listed) == 1
    operation, action = w.browser.act.call_args.args[:2]
    assert (operation, action["label"]) == ("CLICK", "pickup")  # focused, so the site can offer its own options


def test_text_longer_than_the_fields_limit_is_refused_not_cut_short(tmp_path, monkeypatch):
    field = {"id": "loc", "kind": "fill", "label": "Enter your delivery location", "role": "textbox", "value": "",
             "node": 6, "constraints": {"maxlength": 30}}
    w = worker_with(tmp_path, page=fake_page(actions=[field]))
    script_planner(monkeypatch, turn(subgoals=[sub("set the delivery location")]),
                   turn("TYPE", pick=1, text="Bhive Workspace, Service Rd, Sector 6, HSR Layout, Bengaluru"))
    script_jev(monkeypatch, {"next": ("TYPE", 0.4), "type_text_target": "delivery location"})
    jev_checks(monkeypatch)
    for _ in range(2):
        w.command("tick")
    assert "takes at most 30 characters" in w.state["feedback"][-1] and not w.browser.act.called
    assert w.value(field, "HSR Layout, Bengaluru", w.state["page"]) == ({"text": "HSR Layout, Bengaluru"},
                                                                        "HSR Layout, Bengaluru")


def test_asking_for_a_code_the_site_has_not_asked_for_is_refused(tmp_path, monkeypatch):
    page = fake_page(actions=[a for a in fake_page()["actions"] if a["id"] not in {"e2", "e3"}])  # no code field
    w = worker_with(tmp_path, page=page, replies=[{"text": "123456"}])
    script_planner(monkeypatch, turn("ASK", message="Please enter the sign-in code (OTP) sent to your phone."))
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.asked == [] and w.state["code_asks"] == 0
    assert "don't ask the user for a code the site hasn't asked for" in w.state["feedback"][-1]
    script_planner(monkeypatch, turn("ASK", message="What's your card number and expiry date?"))
    w.state["chat"] += [dict(c) for c in CHOSE_CARD]
    w.command("tick")  # a card detail is not a code: the system asks it hidden, field or not
    assert len(w.asked) == 1 and w.asked[0]["kind"] == "secret"


BLANK = fake_page(text="", actions=[{"id": "wait", "kind": "wait", "label": "Wait", "node": None},
                                    {"id": "open_url", "kind": "open_url", "label": "Open a web address", "node": None},
                                    {"id": "c", "kind": "click", "label": "", "role": "generic", "node": 1}])


def test_a_blank_page_is_read_again_before_anyone_decides_on_it(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=BLANK)
    w.browser.observe.side_effect = [BLANK, fake_page()]
    seen = script_planner(monkeypatch, turn("STOP", message="Nothing to do."))
    sent = script_jev(monkeypatch, {"next": "THINK"})
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.browser.observe.call_count == 3 and w.state["blank_waits"] == 0  # two waits, then the real page
    assert seen[0]["page"]["text"] == fake_page()["text"] and seen[0]["feedback"] == []
    assert all(state["page"]["text"] for state, _ in sent)  # Jev never read the blank page either


def test_a_page_that_stays_blank_reaches_the_planner_as_blank_after_three_waits(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=BLANK)
    seen = script_planner(monkeypatch, turn("OPEN_URL", text="https://shop.test/"))
    script_jev(monkeypatch, {"next": "THINK"})
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.state["blank_waits"] == 4 and w.browser.act.call_count == 1  # three waits, then the planner
    assert seen[0]["feedback"] == ["The page has stayed blank (no text and no controls) after 3 waits: OPEN_URL "
                                   "the address you need, go BACK, or ASK the user."]
    assert pressed(w) == ["Open a web address"]


def test_a_tab_that_vanished_stops_the_task_instead_of_crashing_it(tmp_path, monkeypatch):
    from assistant.browser import TabsClosed

    w = worker_with(tmp_path)
    w.browser.fresh.side_effect = KeyError(None)  # Browser.session when its tab is gone
    w.command("tick")
    assert w.state["status"] == "blocked" and w.told[-1]["text"] == "The browser tab closed, so I stopped."
    other = worker_with(tmp_path)
    other.browser.fresh.return_value = False
    other.browser.observe.side_effect = TabsClosed("Every tab of this task has closed.")
    other.command("tick")
    assert other.state["status"] == "blocked" and other.state["errors"] == []


# ---- live bench B: codes in tel boxes, captchas only when shown, unchanged plans, lookups across pages, busy time

def otp_page():
    otp = {"id": "o1", "kind": "fill", "label": "Enter 4 digit OTP", "role": "textbox", "value": "", "node": 30,
           "constraints": {"type": "tel", "maxlength": 4}}
    login = {"id": "o2", "kind": "click", "label": "Log In", "role": "button", "node": 31}
    return fake_page(text="Enter the 4 digit OTP sent to your phone\nLog In\nResend OTP", actions=[otp, login])


def test_a_code_in_a_tel_box_is_typed_not_refused_as_a_phone_number(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=otp_page(), replies=[{"text": "48 29"}])
    script_planner(monkeypatch, turn(subgoals=[sub("sign in with the code")]))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Enter 4 digit OTP", "blocker": "code"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert w.browser.act.call_args.kwargs["text"] == "4829" and w.state["feedback"] == []
    assert w.state["code_asks"] == 1 and w.state["history"][-1]["text"] == "[secret:answer1]"


@pytest.mark.parametrize("reply", ["I didn't get any code", "none", ""])
def test_a_reply_that_is_no_code_does_not_count_as_a_code(tmp_path, monkeypatch, reply):
    w = worker_with(tmp_path, page=otp_page(), replies=[{"text": reply}])
    script_planner(monkeypatch, turn(subgoals=[sub("sign in with the code")]))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Enter 4 digit OTP", "blocker": "code"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert len(w.asked) == 1 and w.state["code_asks"] == 0 and not w.browser.act.called


def test_a_planner_question_for_a_code_counts_only_a_reply_that_is_a_code(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=otp_page(), replies=[{"text": "it never came"}, {"text": "4829"}])
    script_planner(monkeypatch, turn("ASK", message="What's the OTP sent to your phone?"))
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.state["code_asks"] == 0
    w.state["chat"], w.state["reason"] = [], "unsure"  # asked afresh
    w.command("tick")
    assert w.state["code_asks"] == 1


def test_a_captcha_jev_names_on_a_page_that_shows_none_is_not_sent_to_the_user(tmp_path, monkeypatch):
    page = fake_page(text="Log in\nThis site is protected by reCAPTCHA and the Google Privacy Policy")
    w = worker_with(tmp_path, page=page)
    w.browser.marked_screenshot = Mock(return_value="numbered")
    script_planner(monkeypatch, turn(subgoals=[sub("choose the Office address")]))
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Office", "blocker": "captcha"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.command("tick")
    assert w.asked == [] and w.state["captcha_rounds"] == 0
    assert w.browser.act.call_args.args[0] == "CLICK" and w.browser.act.call_args.args[1]["label"] == "Office"


def test_a_plan_sent_again_unchanged_keeps_the_plan_as_if_none_was_sent(tmp_path, monkeypatch):
    w, first = planned_order(tmp_path, monkeypatch, [dict(g) for g in NO_COMMIT], first=NO_COMMIT)
    w.state["subgoal_steps"] = 5
    replan(w)  # an order's plan with no commit, sent again: not a replan to refuse
    assert steps(w) == [(g["do"], g["kind"]) for g in first] and w.state["commit_drops"] == 0
    assert not any("must include" in f for f in w.state["feedback"]) and len(w.payloads) == 2


def test_a_plan_sent_again_with_a_new_value_is_taken(tmp_path, monkeypatch):
    first = [sub("set the delivery location", values=[("delivery location", "")]),
             sub("report the price", kind="report")]
    again = [sub("set the delivery location", values=[("delivery location", "HSR Layout")]),
             sub("report the price", kind="report")]
    w, _ = planned_order(tmp_path, monkeypatch, again, goal="Find the price of atta on Blinkit", first=first)
    replan(w)
    assert w.state["plan"][0]["values"] == [{"field": "delivery location", "text": "HSR Layout"}]


def test_a_lookup_is_judged_done_with_the_pages_read_just_before(tmp_path, monkeypatch):
    page = fake_page(text="Flipkart\nApple iPhone 16 (Black, 128 GB)\n₹69,900")
    goal = "Compare the price of the Apple iPhone 16 128 GB on Flipkart and Amazon.in and tell me which is cheaper"
    w = worker_with(tmp_path, page=page, goal=goal)
    w.state["recent"] = ["Amazon.in search", "Amazon.in\niPhone 16 128 GB\n₹84,999", page["text"]]
    script_planner(monkeypatch, turn("DONE", message="₹69,900 on Flipkart, ₹84,999 on Amazon.in: Flipkart is cheaper.",
                                     text="₹69,900"))
    script_jev(monkeypatch, {"next": "THINK"})
    seen = []
    monkeypatch.setattr(jev, "achieved", lambda page, goal, report, before=(): seen.append(list(before)) or "yes")
    w.command("tick")
    assert w.state["status"] == "done" and seen == [["Amazon.in search", "Amazon.in\niPhone 16 128 GB\n₹84,999"]]
    order = worker_with(tmp_path, goal="Order one Diet Coke on Instamart")
    order.state["recent"] = ["Cart\nDiet Coke"]
    script_planner(monkeypatch, turn("DONE", message="Ordered.", text="Pay ₹79"))
    order.command("tick")
    assert seen[-1] == []  # an order is judged on the page that shows it done


# ---- live bench C: answered asks move on, files before a download is done, moved sites keep their notes


def test_an_answered_ask_subgoal_is_done_and_continue_hands_the_next_one_to_jev(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "The cheapest one"}], goal="Book an Uber to the airport")
    plan = [sub("choose the ride type", kind="ask"), sub("request the ride", kind="commit"),
            sub("report the ride", kind="report")]
    seen = script_planner(monkeypatch, turn("ASK", message="Which ride type would you like?",
                                            options=["Uber Go · ₹904", "Premier · ₹1,210"]), turn())
    script_jev(monkeypatch, {"next": "THINK"})
    jev_checks(monkeypatch)
    w.state.update(plan=plan, reason="next")
    w.command("tick")
    assert len(w.asked) == 1 and w.state["current"] == 1 and w.state["reason"] == "reply"
    w.command("tick")  # the planner hears the reply and lets Jev request the ride
    assert seen[-1]["reason"] == "reply" and not any("CONTINUE does nothing" in f for f in w.state["feedback"])
    assert len(w.asked) == 1 and w.state["current"] == 1


def test_an_ask_off_the_plan_keeps_the_current_subgoal(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "Home"}])
    script_planner(monkeypatch, turn("ASK", message="Which address should I deliver to?"))
    jev_checks(monkeypatch)
    w.state.update(plan=[sub("set the delivery address"), sub("place the order", kind="commit")], reason="unsure")
    w.command("tick")
    assert len(w.asked) == 1 and w.state["current"] == 0


INVOICE = "Download the invoice for my latest Amazon.in order"
ORDER_PAGE = fake_page(text="Your Orders\nOrder # 408-3590147-1050742\nInvoice", url="https://www.amazon.in/orders")


def test_a_download_is_not_done_until_a_file_arrived_and_the_planner_sees_the_files(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=ORDER_PAGE, goal=INVOICE)
    seen = script_planner(monkeypatch, turn("DONE", message="Your invoice for order # 408-3590147-1050742 is saved.",
                                            text="Order # 408-3590147-1050742"))
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.state["status"] != "done" and "No file has been saved yet" in w.state["feedback"][-1]
    assert seen[0]["files_saved"] == []
    w.state["files"].append(str(tmp_path / "invoice.pdf"))
    w.command("tick")
    assert seen[-1]["files_saved"] == ["invoice.pdf"] and w.state["status"] == "done"


def test_a_file_that_arrives_as_the_download_is_reported_done_counts(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=ORDER_PAGE, goal=INVOICE)
    script_planner(monkeypatch, turn("DONE", message="Your invoice is saved.", text="Order # 408-3590147-1050742"))
    jev_checks(monkeypatch)
    w.browser.downloads.append(str(tmp_path / "invoice.pdf"))  # finished, not read into the task yet
    w.command("tick")
    assert w.state["status"] == "done" and w.state["files"] == [str(tmp_path / "invoice.pdf")]


def test_a_download_that_never_arrives_stops_honestly_or_is_named_in_a_paid_tasks_report(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=ORDER_PAGE, goal=INVOICE)
    script_planner(monkeypatch, turn("DONE", message="Your invoice is downloaded.", text="Order # 408-3590147-1050742"))
    jev_checks(monkeypatch)
    for _ in range(3):
        w.command("tick")
        assert w.state["status"] not in STOPPED
    w.command("tick")
    assert w.state["status"] == "blocked" and "no file was saved" in w.told[-1]["text"]
    assert not any(m.get("kind") == "result" for m in w.told)
    paid = worker_with(tmp_path, page=fake_page(text="Payment successful\nReceipt"),
                       goal="Pay my BESCOM bill and download the receipt")
    paid.state["history"].append({"step": 1, "operation": "CLICK", "action": "Pay ₹79", "committed": True})
    script_planner(monkeypatch, turn("DONE", message="Your bill is paid.", text="Payment successful"))
    for _ in range(4):
        paid.command("tick")
    assert paid.state["status"] == "done"  # the payment went through: reported, with the missing file said
    assert paid.told[-1]["text"].startswith("Your bill is paid. The document didn't download")


def test_a_search_address_on_the_host_a_moved_site_now_lives_on_is_opened(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(url="https://www.zepto.com/"), goal="Order Amul milk on Zepto")
    seen = script_planner(monkeypatch, turn("OPEN_URL", text="https://www.zepto.com/search?query=amul+milk"))
    jev_checks(monkeypatch)
    w.command("tick")
    assert "Search address: https://www.zeptonow.com/search" in seen[0]["skill"]  # its note, found by name
    assert w.browser.act.call_args.args[0] == "OPEN_URL" and w.state["feedback"] == []


def test_time_spent_waiting_for_the_user_never_exceeds_the_time_recorded(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(time, "perf_counter", lambda: clock[0])
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn("ASK", message="Where should I deliver it?"))
    jev_checks(monkeypatch)

    def slow(message):
        clock[0] += 300  # the user didn't answer for five minutes, then the task was cancelled
        return {"cancel": True}

    w.asker = slow
    w.command("tick")
    assert w.state["status"] == "cancelled" and w.state["waited_ms"] == 300000
    assert w.state["elapsed_ms"] >= w.state["waited_ms"] and w.trace()["elapsed_ms"] >= 300000


# ---- live bench D1: element numbers, loops by page, middles kept per step, safer asks and values, honest results

LOGIN = [{"id": "x", "kind": "click", "label": "Close", "role": "button", "node": 1},
         {"id": "g", "kind": "click", "label": "Continue", "role": "button", "node": 2},
         {"id": "m", "kind": "fill", "label": "Enter mobile number", "role": "textbox", "value": "", "node": 3,
          "constraints": {"type": "tel"}},
         {"id": "s1", "kind": "select", "label": "Country → India", "role": "combobox", "value": "IN",
          "current_value": "Choose", "node": 4},
         {"id": "s2", "kind": "select", "label": "Country → Nepal", "role": "combobox", "value": "NP",
          "current_value": "Choose", "node": 4},
         {"id": "wait", "kind": "wait", "label": "Wait", "node": None}]
ONLY_CONTINUE = [{"n": 1, "op": "CLICK", "operation": "CLICK", "element": "[2] Continue", "p": 0.4,
                  "action": LOGIN[1]}]


def login_worker(tmp_path, **options):
    w = worker_with(tmp_path, page=fake_page(text="Log in\nEnter mobile number", actions=LOGIN), **options)
    w.state["candidates"] = list(ONLY_CONTINUE)  # Jev ranked only the Continue button
    return w


def test_a_planner_type_onto_an_element_number_types_into_that_field(tmp_path):
    from assistant import views

    w = login_worker(tmp_path)
    page = w.state["page"]
    assert '[3] textbox "Enter mobile number"' in views.view(page, None)["elements"]  # the number the planner reads
    w.do({"op": "TYPE", "pick": "[3]", "text": "[profile:phone]"}, page)
    operation, action = w.browser.act.call_args.args[:2]
    assert (operation, action["label"]) == ("TYPE_TEXT", "Enter mobile number")
    assert w.browser.act.call_args.kwargs["text"] == "+91 98765 43210"
    assert w.state["history"][-1]["text"] == "[profile:phone]"


def test_a_planner_pick_or_type_by_element_number_clicks_selects_and_is_held_to_the_gates(tmp_path, monkeypatch):
    w = login_worker(tmp_path, replies=[{"text": "no"}])
    script_planner(monkeypatch, turn())
    jev_checks(monkeypatch)
    page = w.state["page"]
    w.do({"op": "PICK", "pick": "[2]", "text": ""}, page)
    assert pressed(w) == ["Continue"]
    w.do({"op": "TYPE", "pick": "[4]", "text": "Nepal"}, page)  # a dropdown takes one of its options
    operation, action = w.browser.act.call_args.args[:2]
    assert (operation, action["value"]) == ("SELECT", "NP")
    with pytest.raises(Retry, match="not a field to type into"):
        w.do({"op": "TYPE", "pick": "[1]", "text": "hello"}, page)
    pay = fake_page()  # the Pay button is element [6]: a commit by number still needs the user's yes
    w.state["page"], w.state["candidates"] = pay, []
    with pytest.raises(Retry, match="did not approve"):
        w.do({"op": "PICK", "pick": "[6]", "text": ""}, pay)
    assert w.asked[-1]["approval"] and "I'll press “Pay ₹79”" in w.asked[-1]["text"]


def test_an_element_number_that_isnt_on_the_page_and_a_bare_number_that_isnt_a_candidate_are_refused(tmp_path):
    w = login_worker(tmp_path)
    page = w.state["page"]
    with pytest.raises(Retry) as missing:
        w.do({"op": "TYPE", "pick": "[99]", "text": "hello"}, page)
    assert "[99]" in str(missing.value) and "candidates: 1 [2] Continue" in str(missing.value)
    with pytest.raises(Retry) as bare:
        w.do({"op": "PICK", "pick": "3", "text": ""}, page)
    assert "names one of Jev's candidates by its number" in str(bare.value)
    assert "candidates: 1 [2] Continue" in str(bare.value)
    assert "use its element number in brackets, like [12]" in str(bare.value)
    assert not w.browser.act.called


def test_the_planner_is_told_how_to_name_a_control_jev_did_not_rank():
    assert "for a control that isn't a candidate use its element number in brackets, like [12]" in \
        " ".join(planner.SYSTEM.split())
    assert "never PICK a different candidate instead" not in " ".join(planner.SYSTEM.split())


def toggle(url, n):
    """A seat that is pressed and unpressed: page A shows it free, page B shows it taken."""
    return {"step": n, "operation": "CLICK", "action": "Select seat 01" if n % 2 else "seat 01 selected",
            "url": url, "page_changed": True, "spot": "A" if n % 2 else "B"}


def test_a_control_pressed_and_unpressed_three_times_asks_the_user(tmp_path):
    w = worker_with(tmp_path, replies=[{"text": "pick any two seats together"}])
    url = w.state["page"]["url"]
    for n in range(1, 5):  # A B A B: two undo pairs
        w.state["history"].append(toggle(url, n))
        w.track_loop(w.state["history"][-1])
    assert w.asked == []
    w.state["history"].append(toggle(url, 5))  # A B A B A: three
    w.track_loop(w.state["history"][-1])
    assert len(w.asked) == 1 and "I'm stuck" in w.asked[0]["text"] and w.state["loop_from"] == 5


def test_six_steps_from_one_page_state_ask_the_user_however_far_apart(tmp_path):
    w = worker_with(tmp_path, replies=[{"text": "try the other cinema"}])
    url, n = w.state["page"]["url"], 0
    for visit in range(1, 7):
        for other in range(4):  # four steps on other page states in between: 26 steps, past any 20-step window
            n += 1
            w.state["history"].append({"step": n, "operation": "CLICK", "action": f"Tile {n}",
                                       "url": "https://shop.test/other",
                                       "page_changed": True, "spot": f"elsewhere {n}"})
            w.track_loop(w.state["history"][-1])
        n += 1
        w.state["history"].append({"step": n, "operation": "CLICK", "action": f"Row {visit}", "url": url,
                                   "page_changed": True, "spot": "seat map"})
        w.track_loop(w.state["history"][-1])
        assert len(w.asked) == (visit == 6), visit
    assert w.state["loop_from"] == n


def test_thirty_steps_on_one_path_ask_once_and_forty_five_stop_even_after_an_answer(tmp_path):
    from assistant.errors import Halt

    w = worker_with(tmp_path, replies=[{"text": "keep going"}])
    url = w.state["page"]["url"]
    for n in range(1, 45):
        op = "WAIT" if n % 5 == 0 else "CLICK"  # waits spend the path's budget too
        w.state["history"].append({"step": n, "operation": op, "action": f"Tile {n}", "url": url,
                                   "page_changed": True, "spot": f"state {n}"})
        w.track_loop(w.state["history"][-1])
        assert len(w.asked) == (n >= 30), n
    w.state["history"].append({"step": 45, "operation": "CLICK", "action": "Tile 45", "url": url,
                               "page_changed": True, "spot": "state 45"})
    with pytest.raises(Halt, match="going in circles"):
        w.track_loop(w.state["history"][-1])
    assert len(w.asked) == 1


def test_failed_scrolls_that_each_show_something_new_are_not_fruitless(tmp_path):
    w = worker_with(tmp_path)
    url = w.state["page"]["url"]
    w.state["history"] = [{"step": n, "operation": "SCROLL_DOWN", "action": "Panel: All billers", "url": url,
                           "effect": "failed", "spot": f"list {n}", "after_spot": f"list {n + 1}"}
                          for n in range(1, 7)]
    assert not w.fruitless()
    w.state["history"] = [{**h, "after_spot": h["spot"]} for h in w.state["history"]]
    assert w.fruitless() and "Six steps in a row" in w.state["feedback"][-1]


def test_a_step_records_the_page_it_left_and_the_one_it_reached(tmp_path, monkeypatch):
    from assistant import views

    w = office_click(tmp_path, monkeypatch)
    w.command("tick")
    entry, page = w.state["history"][-1], w.state["page"]
    assert entry["spot"] == views.signature(page) and entry["after_spot"] == views.signature(page)


UBER_PLAN = [sub("set the pickup to your current location"),
             sub("set the drop to Kempegowda International Airport Terminal 1"),
             sub("choose the ride type", kind="ask"), sub("choose how to pay", kind="ask"),
             sub("request the ride", kind="commit"), sub("report the driver and the arrival time", kind="report")]


def test_a_replan_that_shares_words_with_one_old_step_keeps_the_others(tmp_path, monkeypatch):
    goal = "Book an Uber from my current location to Kempegowda International Airport Terminal 1"
    w, _ = planned_order(tmp_path, monkeypatch, [sub("ask for the pickup location since detecting it failed",
                                                     kind="ask")], goal=goal, first=UBER_PLAN)
    replan(w)
    assert steps(w) == [("ask for the pickup location since detecting it failed", "ask"),
                        ("set the pickup to your current location", "act"),
                        ("set the drop to Kempegowda International Airport Terminal 1", "act"),
                        ("choose the ride type", "ask"), ("choose how to pay", "ask"), ("request the ride", "commit"),
                        ("report the driver and the arrival time", "report")]


INSTAMART_PAY = fake_page(text="Payment\nHDFC Bank Card ••1234\nUPI\nPay on Delivery\nTo pay ₹121\nPay ₹121",
                          url="https://www.swiggy.com/instamart/payment")


def test_a_card_detail_is_asked_only_after_the_user_chose_how_to_pay(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=INSTAMART_PAY, replies=[{"text": "123"}], goal="Order one Diet Coke on Instamart")
    script_planner(monkeypatch, turn("ASK", message="Please provide the CVV for your HDFC Bank Card to continue the "
                                                    "payment."))
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.asked == [] and "ASK which payment method first, listing every method the page shows; a " \
                             "pre-selected saved card is not the user's pick" in w.state["feedback"][-1]
    w.state["chat"] += [{"from": "agent", "text": "How would you like to pay?\n1. HDFC Bank Card\n2. UPI\n"
                                                  "3. Pay on Delivery"},
                        {"from": "user", "text": "“card” → option 1: HDFC Bank Card"}]
    w.state["reason"] = "reply"
    w.command("tick")
    assert len(w.asked) == 1 and w.asked[0]["kind"] == "secret"


def test_a_question_that_asks_how_to_pay_is_never_held_back(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=INSTAMART_PAY, replies=[{"text": "UPI"}])
    script_planner(monkeypatch, turn("ASK", message="How would you like to pay? Card details are asked hidden.",
                                     options=["HDFC Bank Card", "UPI", "Pay on Delivery"]))
    jev_checks(monkeypatch)
    w.command("tick")
    assert len(w.asked) == 1


def test_asking_for_a_phone_number_in_the_chat_is_refused_when_memory_has_none(tmp_path, monkeypatch):
    w = worker_with(tmp_path, profile={"phone": ""})
    script_planner(monkeypatch, turn("ASK", message="Please provide your mobile number to sign in to MakeMyTrip."))
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.asked == [] and "TYPE [profile:phone] into the field instead" in w.state["feedback"][-1]


def test_text_that_doesnt_fit_a_phone_field_is_refused_before_typing(tmp_path):
    w = worker_with(tmp_path)
    page = w.state["page"]
    mobile = {"id": "m", "kind": "fill", "label": "Enter Mobile Number", "role": "textbox", "value": "", "node": 40}
    for field in (mobile, {**mobile, "constraints": {"type": "tel"}}):
        with pytest.raises(Retry, match="doesn't look like a phone number"):
            w.value(field, "Goa", page)
        assert w.value(field, "[profile:phone]", page)[0] == {"text": "+91 98765 43210"}
    with pytest.raises(Retry, match="Dates go in as YYYY-MM-DD"):
        w.value({**mobile, "label": "Check-in", "constraints": {"type": "date"}}, "3 Oct 2026", page)


def test_an_empty_value_only_focuses_the_field_and_typing_nothing_is_refused(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(text="Pickup\nDrop", actions=RIDE_PAGE))
    script_planner(monkeypatch, turn(subgoals=[sub("set the pickup to the current location",
                                                   values=[("pickup", ""), ("drop", "Indiranagar")])]))
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.state["plan"][0]["values"] == [{"field": "drop", "text": "Indiranagar"}]
    with pytest.raises(Retry, match="typing replaces"):
        w.value(RIDE_PAGE[0], "", w.state["page"])


def test_an_address_that_redirects_elsewhere_is_said_in_the_step_and_the_notes(tmp_path, monkeypatch):
    prime = fake_page(text="Amazon Prime\nShopping plans", url="https://www.amazon.in/amazonprime")
    w = worker_with(tmp_path, page=prime, goal="Cancel my Amazon Prime membership")
    script_planner(monkeypatch, turn("OPEN_URL", text="https://www.amazon.in/mc"))
    jev_checks(monkeypatch)
    w.command("tick")
    said = "OPEN_URL to https://www.amazon.in/mc ended at https://www.amazon.in/amazonprime (redirected)"
    assert said in w.state["history"][-1]["changed"] and said in w.state["notes"]


def test_a_download_that_was_never_opened_says_so_instead_of_guessing_it_opened(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=ORDER_PAGE, goal=INVOICE)
    script_planner(monkeypatch, turn("DONE", message="You have no recent orders.", text="Your Orders"))
    jev_checks(monkeypatch)
    w.command("tick")
    assert "open the document's download link first" in w.state["feedback"][-1]
    for _ in range(3):
        w.command("tick")
    assert w.state["status"] == "blocked" and "no file was saved" in w.told[-1]["text"]
    assert "download link was never opened" in w.told[-1]["text"] and "opened in the browser" not in w.told[-1]["text"]
    clicked = worker_with(tmp_path, page=ORDER_PAGE, goal=INVOICE)
    clicked.state["history"].append({"step": 1, "operation": "CLICK", "action": "Download invoice", "url": ""})
    for _ in range(4):
        clicked.command("tick")
    assert "may have opened in the browser" in clicked.told[-1]["text"]


FORM = [{"id": "t", "kind": "fill", "label": "Date picker", "role": "textbox", "value": "", "node": 1},
        {"id": "b", "kind": "click", "label": "Submit", "role": "button", "node": 2}]


def test_a_field_that_reads_back_what_was_typed_counts_as_worked_whatever_jev_says_next(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(text="Web form", actions=FORM), goal="Fill the web form")
    script_planner(monkeypatch, turn(subgoals=[sub("set the date", values=[("Date picker", "2026-10-15")])]))
    typing = {"next": "TYPE", "type_text_target": "Date picker"}
    script_jev(monkeypatch, typing, typing, {"next": "THINK", "effect": "failed"})  # plan's look, step, next look
    jev_checks(monkeypatch)
    w.command("tick")
    filled = fake_page(text="Web form", actions=[{**FORM[0], "value": "2026-10-15"}, FORM[1]])
    w.browser.observe.return_value = filled
    w.command("tick")
    assert w.state["history"][-1]["effect"] == "worked" and w.state["history"][-1]["typed_ok"]
    w.command("tick")  # Jev now judges that step failed: the field itself says otherwise
    assert w.state["history"][-1]["effect"] == "worked"
    assert "done_when quotes values exactly as the plan types them" in " ".join(planner.SYSTEM.split())


def test_a_field_that_reads_back_something_else_is_left_to_jev(tmp_path, monkeypatch):
    w = worker_with(tmp_path, page=fake_page(text="Web form", actions=FORM), goal="Fill the web form")
    script_planner(monkeypatch, turn(subgoals=[sub("set the date", values=[("Date picker", "2026-10-15")])]))
    script_jev(monkeypatch, {"next": "TYPE", "type_text_target": "Date picker"})
    jev_checks(monkeypatch)
    w.command("tick")
    w.browser.observe.return_value = fake_page(text="Web form", actions=[{**FORM[0], "value": "10/20/2026"}, FORM[1]])
    w.command("tick")
    assert "effect" not in w.state["history"][-1] and not w.state["history"][-1].get("typed_ok")


def test_the_planner_reads_each_note_once_and_every_site_the_goal_names(tmp_path, monkeypatch):
    goal = "Compare the price of the Apple iPhone 16 128 GB on Flipkart and Amazon.in"
    w = worker_with(tmp_path, page=fake_page(url="https://www.flipkart.com/"), goal=goal)
    seen = script_planner(monkeypatch, turn("STOP", message="Done here."))
    w.state["notes"] = ["BESCOM isn't listed.", "Price ₹69,900 on Flipkart.", "BESCOM isn't listed."]
    w.command("tick")
    assert seen[0]["notes"] == ["Price ₹69,900 on Flipkart.", "BESCOM isn't listed."]
    assert [(s["name"], s["start"], s["search"]) for s in seen[0]["sites"]] == [
        ("Flipkart", "https://www.flipkart.com", "https://www.flipkart.com/search?q={query}"),
        ("Amazon", "https://www.amazon.in", "https://www.amazon.in/s?k={query}")]


def test_the_planner_is_told_to_send_no_plan_unless_it_changes(tmp_path):
    assert "Return subgoals: [] unless you change the plan. Never repeat the plan." in " ".join(planner.SYSTEM.split())
