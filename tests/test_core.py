"""The conversation core with scripted agents: routing, one open question, limits, timers, files. No browser."""

import threading
from types import SimpleNamespace

import pytest
from helpers import until

from assistant import core as conversation
from assistant.channel_web import WebChannel
from assistant.profile import Profile

ADDRESS = {"kind": "choice", "text": "Deliver to which address?", "remember": False,
           "options": [{"id": "o1", "label": "Home"}, {"id": "o2", "label": "Office"}]}
OTP = {"kind": "secret", "text": "Enter the OTP.", "expects": {"length": 6}}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeAgent:
    """Each tick runs one scripted step; with no steps left the task is done."""

    def __init__(self, script, asker, notify, late=()):
        self.script, self.asker, self.notify, self.late = list(script), asker, notify, list(late)
        self.state = {"status": "ready", "history": [], "files": [], "memory": [],
                      "approval": {"action_label": "Pay ₹79"}, "commit": None}
        self.replies, self.closed = [], False

    def command(self, name):
        if self.script:
            self.script.pop(0)(self)
        else:
            self.state["status"] = "done"
        return self.state

    def settle(self):
        self.state["files"].extend(self.late)  # downloads that finished after the last step

    def close(self):
        self.closed = True


def ask(message):
    def step(agent):
        reply = agent.asker({"from": "agent", **message})
        agent.replies.append(reply)
        if reply.get("cancel"):
            agent.state["status"] = "cancelled"
    return step


def tell(text):
    return lambda agent: agent.notify({"from": "agent", "kind": "result", "text": text})


def hold(gate):
    return lambda agent: gate.wait(5)


@pytest.fixture
def world(tmp_path):
    w = SimpleNamespace(scripts={}, hosts={}, agents={}, late={}, classified=[], intents=[], clock=Clock(), n=0)
    w.channel = WebChannel(tmp_path / "media")

    def make_agent(url, goal, title, asker, notify, *, task):
        w.agents[goal] = FakeAgent(w.scripts.get(goal, []), asker, notify, w.late.get(goal, []))
        return w.agents[goal]

    def classify(text, tasks, chat):
        w.classified.append(text)
        entry = w.intents.pop(0) if w.intents else ("NEW_TASK", "none", {"NEW_TASK": 1.0})
        intent, target, probabilities, certainty = (*entry, {entry[1]: 1.0})[:4]
        return {"intent": "OTHER" if conversation.model.ambiguous(probabilities) else intent,
                "task_id": target if target != "none" else None,
                "ambiguous": conversation.model.ambiguous(probabilities) or conversation.model.ambiguous(certainty),
                "reply": ""}

    w.core = conversation.Core(
        w.channel, profile=Profile(tmp_path / "profile.json"), make_agent=make_agent,
        start=lambda goal, facts=None: (f"https://{w.hosts.get(goal, 'shop.test')}/", goal, {}),
        classify=classify, chat=lambda message, tasks: (f"About {len(tasks)} task(s).", {}),
        assess_start=lambda state, page, event: None,
        start_intent=lambda goal: {"provider": "shop.test", "compare": False},
        remember=lambda question, answer: (f"Default choice: {answer}", {}), clock=w.clock, poll=0.01)
    return w


def text(w, body):
    w.n += 1
    w.core.receive({"from": "local", "id": f"wamid.in{w.n}", "type": "text", "text": body, "context": None,
                    "question_id": str(w.core.open.id) if w.core.open else None,
                    "secret_reply": bool(w.core.open and w.core.open.message["kind"] == "secret")})


def tap(w, reply_id):
    w.n += 1
    w.core.receive({"from": "local", "id": f"wamid.tap{w.n}", "type": "interactive", "reply_id": reply_id, "text": "",
                    "context": None})


def sent(w):
    return w.channel.since(0)[0]


def bodies(w):
    return [m["text"]["body"] if m["type"] == "text" else m["interactive"]["body"]["text"]
            for m in sent(w) if m["type"] in {"text", "interactive"}]


def interactive(w):
    return [m for m in sent(w) if m["type"] == "interactive"]


def buttons(w):
    action = interactive(w)[-1]["interactive"]["action"]
    return [b["reply"]["id"] for b in action["buttons"]] if "buttons" in action else [
        r["id"] for r in action["sections"][0]["rows"]]


def test_a_goal_starts_a_task_whose_questions_arrive_as_buttons(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS), tell("Ordered.")]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world)) and buttons(world) == ["q1:o1", "q1:o2"]
    tap(world, "q1:o1")
    assert until(lambda: "Ordered." in bodies(world))
    agent = world.agents["Order Diet Coke"]
    assert agent.replies == [{"option": "o1"}] and until(lambda: agent.closed)
    assert until(lambda: world.core.tasks["t1"].status == "done")


def test_direct_booking_request_reaches_task_without_conversational_fallback(world):
    from assistant import coordinator

    world.core.classify = coordinator.interpret_message
    text(world, "Book an Uber")
    assert until(lambda: "Book an Uber" in world.agents)
    assert all("can't book" not in body.lower() for body in bodies(world))


def test_secret_answers_never_reach_the_classifier(world):
    world.hosts.update({"Book a cab": "uber.test", "Log in to Swiggy": "swiggy.test"})
    world.scripts["Book a cab"] = [ask(ADDRESS)]
    world.scripts["Log in to Swiggy"] = [ask(OTP)]
    text(world, "Book a cab")
    assert until(lambda: interactive(world))
    text(world, "Log in to Swiggy")
    assert until(lambda: len(world.core.queue) == 2)
    tap(world, "q1:o1")
    assert until(lambda: any(m.get("x_secret") for m in sent(world)))
    text(world, "482913")
    assert until(lambda: world.agents["Log in to Swiggy"].replies == [{"text": "482913"}])
    assert "482913" not in world.classified and "482913" not in str(world.core.log)


def test_code_requests_wait_for_open_question_then_take_priority(world):
    world.hosts.update({"A": "a.test", "B": "b.test", "C": "c.test"})
    world.scripts.update(A=[ask({"kind": "text", "text": "Where to?"})], B=[ask(ADDRESS)], C=[ask(OTP)])
    text(world, "A")
    assert until(lambda: world.core.open is not None)
    text(world, "B")
    assert until(lambda: len(world.core.queue) == 2)
    text(world, "C")
    assert until(lambda: len(world.core.queue) == 3)
    assert world.core.open.task.goal == "A"
    world.intents.append(("ANSWER", "t1", {"ANSWER": 0.95}))
    text(world, "Airport")
    assert until(lambda: world.agents["A"].replies == [{"text": "Airport"}])
    assert until(lambda: world.core.open is not None and world.core.open.task.goal == "C")
    text(world, "482913")
    assert until(lambda: world.agents["C"].replies == [{"text": "482913"}])
    assert until(lambda: world.core.open is not None and world.core.open.task.goal == "B")
    assert "482913" not in world.classified


def test_taps_on_answered_questions_are_ignored(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS), ask({**ADDRESS, "text": "Which size?", "remember": False})]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    tap(world, "q1:o1")
    assert until(lambda: world.core.open is not None and world.core.open.id == 2)
    tap(world, "q1:o2")
    assert world.agents["Order Diet Coke"].replies == [{"option": "o1"}] and world.core.open.id == 2


def test_long_lists_are_answered_by_number_without_classifying(world):
    cities = [{"id": f"o{i}", "label": f"City {i}"} for i in range(1, 13)]
    world.scripts["Pick a city"] = [ask({"kind": "choice", "text": "Which city?", "options": cities})]
    text(world, "Pick a city")
    assert until(lambda: any("Reply with a number" in b for b in bodies(world)))
    text(world, "11")
    assert until(lambda: world.agents["Pick a city"].replies == [{"option": "o11"}])
    assert "11" not in world.classified


def test_files_answer_the_question_that_asked_for_them(world):
    world.scripts["Dispute a charge"] = [ask({"kind": "file", "text": "Send the statement."})]
    text(world, "Dispute a charge")
    assert until(lambda: any(m.get("x_expects") == {"file": True} for m in sent(world)))
    media = world.channel.store(b"%PDF", "application/pdf", "statement.pdf")
    world.core.receive({"from": "local", "id": "wamid.f", "type": "document", "media_id": media,
                        "filename": "statement.pdf", "text": "", "context": None})
    assert world.channel.media(media)[0].is_file()
    assert until(lambda: world.agents["Dispute a charge"].replies == [{"upload_id": media}])


def test_amendments_status_small_talk_and_cancel(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate), ask(ADDRESS)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    world.intents += [("AMEND", "t1", {"AMEND": 0.9}), ("STATUS", "t1", {"STATUS": 0.9}),
                      ("OTHER", "none", {"OTHER": 0.95}), ("CANCEL", "t1", {"CANCEL": 0.97})]
    text(world, "make it 2 cans")
    text(world, "where are we?")
    text(world, "thanks")
    gate.set()
    agent = world.agents["Order Diet Coke"]
    assert until(lambda: "User update: make it 2 cans" in agent.state["memory"]) and agent.state["approval"] is None
    assert until(lambda: interactive(world))
    text(world, "stop the coke order")
    assert until(lambda: agent.replies == [{"cancel": True}])
    got = bodies(world)
    assert "Got it. I'll use that for Order Diet Coke." in got and got.count("About 1 task(s).") == 2


def test_amending_after_a_payment_says_it_already_went_through(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [lambda agent: agent.state["memory"].append("Done: Pay ₹79 (Order placed)"),
                                        hold(gate)]
    text(world, "Order Diet Coke")
    assert until(lambda: "Order Diet Coke" in world.agents and world.agents["Order Diet Coke"].state["memory"])
    world.intents.append(("AMEND", "t1", {"AMEND": 0.9}))
    text(world, "make it 2 cans")
    assert until(lambda: any(b.startswith("Pay ₹79 already went through.") for b in bodies(world)))
    gate.set()
    assert until(lambda: "User update: make it 2 cans" in world.agents["Order Diet Coke"].state["memory"])


def test_unclear_messages_ask_which_task_they_are_for(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    world.intents.append(("AMEND", "t1", {"AMEND": 0.5, "NEW_TASK": 0.45}))
    text(world, "2 please")
    assert until(lambda: interactive(world))
    assert buttons(world) == ["route:t1:wamid.in2", "route:new:wamid.in2"]
    tap(world, "route:t1:wamid.in2")
    gate.set()
    assert until(lambda: "User update: 2 please" in world.agents["Order Diet Coke"].state["memory"])


def test_a_remember_button_follows_an_answered_choice(world):
    world.scripts["Order Diet Coke"] = [ask({**ADDRESS, "remember": True})]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    tap(world, "q1:o1")
    assert until(lambda: buttons(world) == ["q2:yes", "q2:no"])
    tap(world, "q2:yes")
    assert until(lambda: "Saved for next time: Default choice: Home" in bodies(world))
    assert world.core.profile.data["preferences"][-1] == {"text": "Default choice: Home",
                                                          "from_task": "Order Diet Coke"}


def test_three_tasks_at_once_and_one_per_site(world):
    gates = {g: threading.Event() for g in "ABCDE"}
    world.hosts.update(A="a.test", B="b.test", C="c.test", D="d.test", E="b.test")
    for g in "ABCDE":
        world.scripts[g] = [hold(gates[g])]
    for g in "ABC":
        text(world, g)
    assert until(lambda: sum(t.status == "running" for t in world.core.tasks.values()) == 3)
    text(world, "D")
    text(world, "E")
    assert until(lambda: "*D*\nQueued: D starts when one of your other tasks finishes." in bodies(world))
    assert until(lambda: "*E*\nWaiting for your other b.test task to finish first." in bodies(world))
    gates["A"].set()
    assert until(lambda: world.core.tasks["t4"].status == "running") and world.core.tasks["t5"].status == "waiting"
    gates["B"].set()
    assert until(lambda: world.core.tasks["t5"].status == "running")
    for g in "CDE":
        gates[g].set()


def test_unanswered_questions_pause_their_task_then_cancel_it(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    world.clock.now += conversation.PAUSE_AFTER
    assert until(lambda: "Paused until you answer." in bodies(world))
    assert world.core.tasks["t1"].status == "paused"
    world.clock.now += conversation.CANCEL_AFTER
    assert until(lambda: world.agents["Order Diet Coke"].replies == [{"cancel": True}]) and world.core.open is None


def test_files_the_agent_gets_are_sent_as_documents(world, tmp_path, monkeypatch):
    invoice, huge = tmp_path / "invoice.pdf", tmp_path / "huge.bin"
    invoice.write_bytes(b"%PDF")
    huge.write_bytes(b"x" * 10)
    monkeypatch.setattr(conversation, "MAX_DOWNLOAD", 5)
    world.scripts["Get my invoice"] = [lambda agent: agent.state["files"].extend([str(invoice), str(huge)])]
    text(world, "Get my invoice")
    assert until(lambda: [m for m in sent(world) if m["type"] == "document"])
    document = next(m for m in sent(world) if m["type"] == "document")["document"]
    assert document["filename"] == "invoice.pdf" and world.channel.media(document["id"])[0] == invoice
    assert until(lambda: any("downloaded file could not be delivered" in b for b in bodies(world)))


def test_model_errors_are_reported_and_three_in_a_row_stop_the_task(world):
    def broken(agent):
        raise ValueError("bad JSON")

    world.scripts["Order Diet Coke"] = [broken, broken, broken, tell("never")]
    text(world, "Order Diet Coke")
    assert until(lambda: "Stopping after 3 problems in a row." in bodies(world))
    assert bodies(world).count("Problem: bad JSON") == 3 and "never" not in bodies(world)


def test_a_problem_after_a_commit_stops_with_a_warning(world):
    def committed(agent):
        agent.state["commit"] = {"label": "Pay ₹79", "checks": 1}
        raise RuntimeError("HTTP 503")

    world.scripts["Order Diet Coke"] = [committed, tell("never")]
    text(world, "Order Diet Coke")
    assert until(lambda: any("Pay ₹79 may already have gone through" in b for b in bodies(world)))
    assert "never" not in bodies(world)


def test_titles_prefix_messages_only_when_several_tasks_exist(world):
    first, second = threading.Event(), threading.Event()
    world.hosts["Book a cab"] = "uber.test"
    world.scripts["Order Diet Coke"] = [tell("First."), hold(first), tell("Third.")]
    world.scripts["Book a cab"] = [hold(second)]
    text(world, "Order Diet Coke")
    assert until(lambda: "First." in bodies(world))
    text(world, "Book a cab")
    assert until(lambda: world.core.tasks["t2"].status == "running")
    first.set()
    assert until(lambda: "*Order Diet Coke*\nThird." in bodies(world))
    second.set()


def test_typing_shows_while_a_task_works_and_a_failed_start_says_why(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.channel.typing)
    gate.set()
    assert until(lambda: not world.channel.typing)

    def cannot(goal, facts=None):
        raise ValueError("The text model returned an invalid start; nothing executed.")

    world.core.start = cannot
    text(world, "Do the impossible")
    assert until(lambda: any("invalid start" in b for b in bodies(world)))


def test_a_message_that_cannot_be_read_says_so(world):
    def down(text, tasks, chat):
        raise RuntimeError("Model connection failed; no action executed.")

    world.core.classify = down
    text(world, "Order Diet Coke")
    assert "I couldn't read that message: Model connection failed; no action executed." in bodies(world)
    assert world.core.tasks == {}


def test_only_the_configured_user_is_heard(world):
    stranger = {"from": "someone-else", "id": "wamid.x", "type": "text", "text": {"body": "Order Diet Coke"}}
    body = {"object": "whatsapp_business_account",
            "entry": [{"id": "x", "changes": [{"field": "messages", "value": {"messages": [stranger]}}]}]}
    world.core.receive_webhook(body)
    assert world.classified == [] and world.core.tasks == {}


def test_a_cancel_mid_step_stops_it_before_it_asks(world):
    gate, flags = threading.Event(), []

    def step(agent):
        gate.wait(5)
        flags.append(agent.interrupted())
        ask(ADDRESS)(agent)

    world.scripts["Order Diet Coke"] = [step]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    world.intents.append(("CANCEL", "t1", {"CANCEL": 0.97}))
    text(world, "stop")
    gate.set()
    agent = world.agents["Order Diet Coke"]
    assert until(lambda: agent.replies == [{"cancel": True}]) and flags == [True]
    assert interactive(world) == [] and world.core.open is None


def test_an_amendment_mid_step_interrupts_it(world):
    gate, flags = threading.Event(), []

    def step(agent):
        gate.wait(5)
        flags.append(agent.interrupted())

    world.scripts["Order Diet Coke"] = [step]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    world.intents.append(("AMEND", "t1", {"AMEND": 0.9}))
    text(world, "make it 2 cans")
    gate.set()
    assert until(lambda: "User update: make it 2 cans" in world.agents["Order Diet Coke"].state["memory"])
    assert flags == [True]


def test_cancel_and_changes_never_guess_the_task(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    world.intents.append(("CANCEL", "none", {"CANCEL": 0.97}))
    text(world, "cancel the chips order")
    assert until(lambda: interactive(world)) and buttons(world) == ["route:t1:wamid.in2"]
    assert interactive(world)[-1]["interactive"]["body"]["text"] == "Which task should I stop?"
    assert not world.core.tasks["t1"].cancelled
    tap(world, "route:t1:wamid.in2")
    assert world.core.tasks["t1"].cancelled and "Stopping Order Diet Coke after its current step." in bodies(world)
    gate.set()


def test_an_unsure_task_pick_asks_which(world):
    gates = {g: threading.Event() for g in "AB"}
    world.hosts.update(A="a.test", B="b.test")
    world.scripts.update(A=[hold(gates["A"])], B=[hold(gates["B"])])
    text(world, "A")
    text(world, "B")
    assert until(lambda: sum(t.status == "running" for t in world.core.tasks.values()) == 2)
    world.intents.append(("AMEND", "t1", {"AMEND": 0.9}, {"t1": 0.55, "t2": 0.45}))
    text(world, "make it 2")
    assert until(lambda: interactive(world)) and buttons(world) == ["route:t1:wamid.in3", "route:t2:wamid.in3"]
    assert world.core.tasks["t1"].updates == []
    tap(world, "route:t2:wamid.in3")
    assert world.core.tasks["t2"].updates == ["make it 2"]
    for gate in gates.values():
        gate.set()


def test_changes_with_nothing_running_say_so(world):
    world.intents.append(("CANCEL", "none", {"CANCEL": 0.97}))
    text(world, "cancel it")
    assert bodies(world) == ["Nothing is running right now."] and world.core.tasks == {}


def test_an_answer_goes_only_to_the_question_that_was_open(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS), ask(OTP)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))

    def slow(message, tasks, chat):  # the address is tapped and the OTP opens while Jev reads "Home"
        world.core.answer(world.core.open, {"reply_id": "q1:o1"})
        until(lambda: world.core.open is not None and world.core.open.id == 2)
        return {"intent": "ANSWER", "task_id": "t1", "ambiguous": False, "reply": ""}

    world.core.classify = slow
    text(world, "Home")
    assert "The question changed while I was reading your message. Please answer again." in bodies(world)
    assert world.agents["Order Diet Coke"].replies == [{"option": "o1"}] and world.core.open.id == 2


def test_a_late_which_tap_never_answers_a_newer_question(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS), ask(OTP)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    world.intents.append(("ANSWER", "t1", {"ANSWER": 0.5, "AMEND": 0.45}))
    text(world, "use my office address")
    assert until(lambda: buttons(world) == ["route:t1:wamid.in2", "route:new:wamid.in2"])
    tap(world, "q1:o1")
    assert until(lambda: world.core.open is not None and world.core.open.id == 2)
    tap(world, "route:t1:wamid.in2")
    assert world.core.tasks["t1"].updates == []  # stale clarification cannot amend a later state
    assert world.agents["Order Diet Coke"].replies == [{"option": "o1"}] and world.core.open.id == 2


def test_a_which_tap_for_a_finished_task_starts_nothing(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    world.intents.append(("AMEND", "t1", {"AMEND": 0.5, "NEW_TASK": 0.45}))
    text(world, "2 please")
    assert until(lambda: interactive(world))
    gate.set()
    assert until(lambda: world.core.tasks["t1"].status == "done")
    tap(world, "route:t1:wamid.in2")
    assert "Order Diet Coke already finished." in bodies(world) and list(world.core.tasks) == ["t1"]


def test_cancel_works_even_while_a_secret_is_asked(world):
    world.scripts["Log in to Swiggy"] = [ask(OTP)]
    text(world, "Log in to Swiggy")
    assert until(lambda: any(m.get("x_secret") for m in sent(world)))
    text(world, " Cancel ")
    assert until(lambda: world.agents["Log in to Swiggy"].replies == [{"cancel": True}])
    assert world.classified == ["Log in to Swiggy"]


def test_a_code_request_opens_at_once_and_earlier_buttons_still_work(world):
    gate = threading.Event()
    world.hosts.update({"Book a cab": "uber.test", "Log in to Swiggy": "swiggy.test"})
    world.scripts["Book a cab"] = [ask(ADDRESS)]
    world.scripts["Log in to Swiggy"] = [hold(gate), ask(OTP)]
    text(world, "Book a cab")
    assert until(lambda: interactive(world))
    text(world, "Log in to Swiggy")
    assert until(lambda: world.core.tasks["t2"].status == "running")
    gate.set()
    assert until(lambda: len(world.core.queue) == 2)
    assert world.core.open.message["kind"] == "choice"
    tap(world, "q1:o2")
    assert until(lambda: world.agents["Book a cab"].replies == [{"option": "o2"}])
    assert world.core.open.message["kind"] == "secret"
    text(world, "482913")
    assert until(lambda: world.agents["Log in to Swiggy"].replies == [{"text": "482913"}])
    assert "482913" not in world.classified


def test_the_page_learns_which_question_is_open(world):
    assert world.core.showing() is None
    world.scripts["Log in to Swiggy"] = [ask(OTP)]
    text(world, "Log in to Swiggy")
    assert until(lambda: world.core.showing() is not None and world.core.showing()["id"] == "1"
                 and world.core.showing()["secret"])


def test_files_that_arrive_as_the_task_ends_are_still_sent(world, tmp_path):
    late = tmp_path / "late.pdf"
    late.write_bytes(b"%PDF")
    world.late["Get my invoice"] = [str(late)]
    text(world, "Get my invoice")
    assert until(lambda: [m for m in sent(world) if m["type"] == "document"])


def test_a_stopped_task_says_when_it_has_stopped(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate), tell("never")]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    world.intents.append(("CANCEL", "t1", {"CANCEL": 0.97}))
    text(world, "stop")
    gate.set()
    assert until(lambda: "Stopped Order Diet Coke." in bodies(world)) and "never" not in bodies(world)
    assert world.agents["Order Diet Coke"].state["status"] == "cancelled"  # what its trace records


def test_a_stop_after_a_payment_says_it_may_have_gone_through(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [lambda agent: agent.state.update(commit={"label": "Pay ₹79", "checks": 0}),
                                        hold(gate)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.agents.get("Order Diet Coke") and world.agents["Order Diet Coke"].state["commit"])
    world.intents.append(("CANCEL", "t1", {"CANCEL": 0.97}))
    text(world, "stop")
    gate.set()
    assert until(lambda: "Stopped Order Diet Coke. Pay ₹79 may already have gone through; check it before trying "
                         "again." in bodies(world))


def test_a_task_that_finished_is_never_reported_stopped(world):
    gate = threading.Event()

    def last(agent):
        gate.wait(5)
        agent.state["status"] = "done"

    world.scripts["Order Diet Coke"] = [last]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    world.intents.append(("CANCEL", "t1", {"CANCEL": 0.97}))
    text(world, "stop")
    gate.set()
    assert until(lambda: world.core.tasks["t1"].status == "done")
    assert not any(b.startswith("Stopped") for b in bodies(world))


def test_duplicate_inbound_id_starts_one_task(world):
    event = {'from': 'local', 'id': 'same-id', 'type': 'text', 'text': 'Find options',
             'context': None, 'question_id': None, 'secret_reply': False}
    world.core.receive(event)
    world.core.receive(event)
    assert len(world.core.tasks) == 1


def test_stale_secret_never_answers_new_question_or_reaches_logs(world):
    task = conversation.Task('t1', 'A')
    old = conversation.Question(1, task, OTP, 0)
    old.answered.set()
    current = conversation.Question(2, task, {'kind': 'text', 'text': 'Where?'}, 0)
    world.core.open, world.core.queue = current, [current]
    world.core.receive({'id': 'late', 'text': '482913', 'question_id': '1', 'secret_reply': True})
    assert not current.answered.is_set()
    assert not world.classified
    assert '482913' not in str(world.core.log)
    assert any('expired' in b or 'stale' in b for b in bodies(world))


def test_waiting_secret_does_not_preempt_displayed_question(world):
    task = conversation.Task('t1', 'A')
    ordinary = conversation.Question(1, task, ADDRESS, 0)
    world.core.queue = [ordinary]
    world.core.show_next()
    world.core.queue.append(conversation.Question(2, task, OTP, 0))
    world.core.show_next()
    assert world.core.open is ordinary
    assert world.core.showing()['message_id'] == ordinary.sent[-1]


def test_forged_secret_identity_never_reaches_classifier(world):
    world.core.receive({'id': 'forged', 'text': '919292', 'question_id': 'bogus', 'secret_reply': True})
    assert world.classified == []
    assert '919292' not in str(world.core.log)


def test_inbound_processing_serializes_classifier_and_routing(world):
    entered, release = threading.Event(), threading.Event()
    order = []
    def classify(text, tasks, chat):
        order.append(text)
        if text == 'first':
            entered.set()
            release.wait(2)
        return {'intent': 'OTHER', 'task_id': None, 'ambiguous': False, 'reply': text}
    world.core.classify = classify
    first = threading.Thread(target=world.core.receive, args=({'id': 'a', 'text': 'first'},))
    second = threading.Thread(target=world.core.receive, args=({'id': 'b', 'text': 'second'},))
    first.start()
    assert entered.wait(1)
    second.start()
    assert order == ['first']
    release.set()
    first.join(2)
    second.join(2)
    assert order == ['first', 'second']
    assert bodies(world)[-2:] == ['first', 'second']


def test_task_owns_flow_before_any_agent_is_allocated(world):
    task = conversation.Task('t7', 'Find options')
    assert task.flow.task_id == 't7'
    assert task.flow.goal == 'Find options'


def test_blocked_agent_is_retained_and_resume_reobserves(world):
    def block(agent):
        agent.state['status'] = 'blocked'
    world.scripts['A'] = [block]
    text(world, 'A')
    assert until(lambda: world.core.tasks['t1'].status == 'blocked')
    task = world.core.tasks['t1']
    assert not task.agent.closed
    seen = []
    def resume():
        seen.append('fresh observation')
        task.agent.state['status'] = 'ready'
    task.agent.resume = resume
    world.core.resume(task.id)
    assert until(lambda: task.status == 'done')
    assert seen == ['fresh observation']


def test_reservations_serialize_unknown_accounts_and_keep_pending(world):
    a, b = conversation.Task('a', 'A'), conversation.Task('b', 'B')
    assert world.core.reserve(a, 'https://shop.test/a', None)
    assert not world.core.reserve(b, 'https://shop.test/b', 'user2')
    assert world.core.reserve(a, 'https://shop.test/', 'user1')
    assert world.core.reserve(b, 'https://shop.test/', 'user2')


def test_failed_delivery_has_no_receipt_and_can_retry_without_agent_tick(world, tmp_path):
    path = tmp_path / 'invoice.pdf'
    path.write_bytes(b'%PDF')
    task = conversation.Task('a', 'Get invoice')
    task.agent = FakeAgent([], None, None)
    task.agent.state['files'] = [str(path)]
    original = world.channel.send
    world.channel.send = lambda *_: (_ for _ in ()).throw(OSError('channel unavailable'))
    with pytest.raises(OSError):
        world.core.deliver_files(task)
    assert task.sent_files == 0 and not task.flow.delivered
    world.channel.send = original
    world.core.deliver_files(task)
    assert task.sent_files == 1
    assert len(task.flow.delivered) == 1
    assert next(iter(task.flow.delivered.values()))['message_id'].startswith('wamid.out')


def test_queued_question_does_not_age_as_displayed(world):
    a, b = conversation.Task('a', 'A'), conversation.Task('b', 'B')
    first = conversation.Question(1, a, ADDRESS, 0)
    world.core.queue = [first]
    world.core.show_next()
    second = conversation.Question(2, b, OTP, 0)
    world.core.queue.append(second)
    world.clock.now = 1000
    assert second.displayed_at is None
    world.core.answer(first, {'reply_id': 'q1:o1'})
    assert second.displayed_at == 1000


def test_preflight_collects_provider_and_partial_location_before_start(world, monkeypatch):
    from assistant import coordinator
    started = []
    world.core.start = lambda goal, facts=None: (started.append(facts) or ('https://shop.test', goal, {}))
    world.core.start_intent = lambda goal: {'provider': None, 'compare': False}
    world.core.assess_start = coordinator.assess
    def assessment(state, page, event):
        return {'objective': state.goal, 'facts': [], 'requirements': [
            {'id': 'location', 'kind': 'text', 'purpose': 'task', 'keys': ['address'],
             'scope': 'task', 'depends_on': [], 'options': [], 'reason': 'delivery address needed'}],
            'completion': [], 'auth': None, 'progress': None, 'proposal': None, 'outcome': None,
            'notice': None, 'recovery': None}
    world.core.assess_start = assessment
    monkeypatch.setattr(coordinator, 'apply_assessment', lambda state, result: None)
    world.core.answer_facts = lambda text, keys: {
        k: text for k in keys if k == ('provider' if text == 'Shop' else 'address')}
    text(world, 'Deliver groceries')
    assert until(lambda: world.core.open is not None)
    assert not started and not world.agents
    world.intents.append(('ANSWER', 't1', {'ANSWER': 1}))
    text(world, 'Shop')
    assert until(lambda: world.core.open is not None and world.core.open.id == 2)
    assert not started
    world.intents.append(('ANSWER', 't1', {'ANSWER': 1}))
    text(world, '12 Main Street')
    assert until(lambda: started)
    assert started[0]['provider'] == 'Shop'
    assert started[0]['address'] == '12 Main Street'


def test_startup_unknown_journal_reserves_site_without_browser(world, tmp_path):
    from assistant.commit import Journal
    world.core.journal_dir = tmp_path / 'journals'
    journal = Journal.create(world.core.journal_dir, lambda x: x)
    journal.append({'task_id': 'old', 'execution_id': 'old:1', 'phase': 'dispatching',
                    'commitment': True, 'origin': 'https://shop.test', 'account_key': 'shop:user'})
    world.core.recover_interrupted()
    assert world.core.interrupted_runs and not world.agents
    assert not world.core.reserve(conversation.Task('new', 'A'), 'https://shop.test', None)
    before = len(sent(world))
    world.core.recover_interrupted()
    assert len(sent(world)) == before


def test_secret_without_identity_is_not_logged_or_classified(world):
    task = conversation.Task('t', 'A')
    world.core.open = conversation.Question(1, task, OTP, 0)
    world.core.receive({'id': 'missing', 'text': '761234'})
    assert not world.classified and '761234' not in str(world.core.log)


def test_idle_blocked_task_closes_tab_but_preserves_unknown_reservation(world):
    task = conversation.Task('t', 'A')
    task.agent = FakeAgent([], None, None)
    task.status = 'blocked'
    task.flow.executions['e'] = {'commitment': True, 'status': 'unknown'}
    world.core.tasks['t'] = task
    world.core.reserve(task, 'https://shop.test', None)
    world.clock.now = conversation.CANCEL_AFTER + 1
    world.core.reap_idle()
    assert task.agent.closed
    assert not world.core.reserve(conversation.Task('new', 'B'), 'https://shop.test', None)


def test_local_cleanup_requires_bound_consent_and_never_opens_browser(world, monkeypatch):
    deleted = []
    world.core.profile.data['cards'] = [{'id': 'legacy', 'label': 'Old', 'last4': '1234'}]
    monkeypatch.setattr(conversation.Vault, 'delete_legacy_cards',
                        lambda self, keys, consent: deleted.append((keys, consent)))
    text(world, '/cleanup-legacy-cards')
    assert until(lambda: world.core.open is not None)
    assert not deleted and not world.agents and not world.classified
    tap(world, f'q{world.core.open.id}:yes')
    assert until(lambda: deleted)
    assert deleted[0][1] is True
    assert world.core.profile.data['cards'] == []


def test_expired_queued_secret_is_not_displayed(world):
    task = conversation.Task('t', 'A')
    task.flow.challenges['expired'] = {'expires_at': 1}
    q = conversation.Question(1, task, {**OTP, 'challenge_id': 'expired'}, 0)
    world.core.queue = [q]
    world.core.show_next()
    assert not any(m.get('x_secret') for m in sent(world))
    assert q.answered.is_set() and q.reply == {'expired': True}


def test_local_provider_default_requires_consent(world):
    text(world, '/default provider Shop')
    assert until(lambda: world.core.open is not None)
    assert not world.core.profile.data.get('defaults') and not world.agents
    tap(world, f'q{world.core.open.id}:yes')
    assert until(lambda: world.core.profile.data.get('defaults'))
    assert world.core.profile.data['defaults']['provider']['value'] == 'Shop'


def test_error_chat_uses_task_secret_registry(world):
    from assistant.profile import Secrets
    task = conversation.Task('t', 'A')
    task.agent = FakeAgent([], None, None)
    task.agent.secrets = Secrets()
    task.agent.secrets.add('unsafe-password', '[secret]')
    world.core.say('Error: unsafe-password', task)
    assert 'unsafe-password' not in str(sent(world)) + str(world.core.log)


def test_routing_clarification_keeps_one_question_identity(world):
    task = conversation.Task('t', 'A')
    task.status = 'running'
    world.core.tasks['t'] = task
    original = conversation.Question(1, task, ADDRESS, 0)
    world.core.queue = [original]
    world.core.show_next()
    world.core.which({'id': 'ambiguous'}, 'some reply', [task], None, original)
    assert world.core.showing()['id'] == '1'
    assert world.core.open.message == ADDRESS
    assert getattr(original, 'routing', None) == 'ambiguous'
    world.core.answer(original, {'reply_id': 'q1:o1'})
    world.core.receive({'id': 'late-route', 'reply_id': 'route:t:ambiguous', 'text': ''})
    assert original.reply == {'option': 'o1'}
    assert not task.updates


def test_idle_reaper_terminates_real_retained_agent(world, tmp_path):
    from fakes import agent_with
    task = conversation.Task('t', 'A')
    task.agent = agent_with(tmp_path)
    task.flow = task.agent.task
    task.flow.set_status('blocked')
    task.status = 'blocked'
    world.core.tasks['t'] = task
    world.clock.now = conversation.CANCEL_AFTER + 1
    world.core.reap_idle()
    task.agent.browser.close.assert_called_once()


def test_secret_payload_that_looks_like_local_command_is_still_secret(world):
    task = conversation.Task('t', 'A')
    q = conversation.Question(1, task, OTP, 0)
    world.core.open, world.core.queue = q, [q]
    value = '/default provider private-password'
    world.core.receive({'id': 'secret-command', 'text': value, 'question_id': '1', 'secret_reply': True})
    assert q.answered.is_set() and q.reply == {'text': value}
    assert not world.core.tasks and value not in str(world.core.log)


def test_unresolved_commitment_reservation_is_never_released_on_provider_contention(world):
    a, b = conversation.Task('a', 'A'), conversation.Task('b', 'B')
    assert world.core.reserve(a, 'https://first.test', None)
    a.flow.executions['e'] = {'commitment': True, 'status': 'unknown'}
    assert world.core.reserve(b, 'https://second.test', None)
    assert not world.core.reserve(a, 'https://second.test', None)
    assert not world.core.reserve(b, 'https://first.test', None)
    assert ('first.test', None) in a.reservations


def test_unused_reservation_is_relinquished_when_switch_blocked(world):
    a, b = conversation.Task('a', 'A'), conversation.Task('b', 'B')
    assert world.core.reserve(a, 'https://first.test', None)
    assert world.core.reserve(b, 'https://second.test', None)
    assert not world.core.reserve(a, 'https://second.test', None)
    assert world.core.reserve(b, 'https://first.test', None)


def test_recovered_task_can_only_resume_as_fresh_read_only_inspection(world, tmp_path):
    from assistant.commit import Journal
    world.core.journal_dir = tmp_path / 'journals'
    journal = Journal.create(world.core.journal_dir, lambda x: x)
    journal.append({'task_id': 'old', 'execution_id': 'old:1', 'phase': 'dispatching',
                    'commitment': True, 'origin': 'https://shop.test', 'account_key': 'shop:user',
                    'summary': {'objective': 'Book trip'}})
    world.core.recover_interrupted()
    task = next(iter(world.core.tasks.values()))
    assert task.status == 'blocked' and task.agent is None
    assert task.flow.approval is None and not task.flow.executions
    world.core.start = lambda *_: pytest.fail('reconciliation must not plan/replay the old request')
    world.core.resume(task.id)
    assert until(lambda: task.agent is not None)
    assert task.flow.constraints['read_only_reconciliation'] is True
    assert until(lambda: task.status == 'blocked')
    assert not world.core.reserve(conversation.Task('fresh', 'Buy'), 'https://shop.test', None)


def test_explicit_provider_satisfies_coordinator_requirement_without_reasking(world, monkeypatch):
    from assistant import coordinator
    world.core.start_intent = lambda goal: {'provider': 'Shop', 'compare': False}
    def assess(state, page, event):
        return {'requirements': [{'id': 'provider', 'kind': 'text', 'purpose': 'task',
            'keys': ['provider'], 'scope': 'task', 'depends_on': [], 'options': [], 'reason': 'provider needed'}]}
    world.core.assess_start = assess
    monkeypatch.setattr(coordinator, 'apply_assessment', lambda *_: None)
    task = conversation.Task('t', 'Use Shop')
    world.core.ask = lambda *_: pytest.fail('explicit provider must not be requested again')
    facts = world.core.prepare(task)
    assert facts['provider'] == 'Shop'


def test_preflight_rejects_inferred_current_location(world, monkeypatch):
    from assistant import coordinator
    world.core.assess_start = lambda *_: {'requirements': [
        {'id': 'address', 'kind': 'text', 'purpose': 'task', 'keys': ['address'], 'scope': 'task',
         'depends_on': [], 'options': [], 'reason': 'address required'}]}
    monkeypatch.setattr(coordinator, 'apply_assessment', lambda *_: None)
    world.core.answer_facts = lambda text, keys: {'address': text}
    replies = iter([{'text': 'current location'}, {'text': '12 Main Street'}])
    world.core.ask = lambda *_: next(replies)
    task = conversation.Task('t', 'Deliver here')
    assert world.core.prepare(task)['address'] == '12 Main Street'


def test_channel_failure_blocks_retains_and_resume_retries_delivery(world, tmp_path):
    invoice = tmp_path / 'invoice.pdf'
    invoice.write_bytes(b'%PDF')
    world.scripts['Invoice'] = [lambda a: a.state['files'].append(str(invoice))]
    original = world.channel.send
    def fail_documents(user, payload):
        if payload['type'] == 'document':
            raise OSError('transport down')
        return original(user, payload)
    world.channel.send = fail_documents
    text(world, 'Invoice')
    assert until(lambda: world.core.tasks['t1'].status == 'blocked')
    task = world.core.tasks['t1']
    assert not task.agent.closed and task.sent_files == 0
    world.channel.send = original
    task.agent.resume = lambda: task.agent.state.update(status='ready')
    world.core.resume(task.id)
    assert until(lambda: task.sent_files == 1)
    assert len([m for m in sent(world) if m['type'] == 'document']) == 1


def test_cancel_unknown_task_reconciles_before_closing(world):
    task = conversation.Task('t', 'A')
    task.status = 'blocked'
    task.agent = FakeAgent([], None, None)
    task.flow.executions['e'] = {'commitment': True, 'status': 'unknown'}
    calls = []
    task.agent.observe = lambda: calls.append('observe')
    task.agent.coordinate = lambda event: calls.append(event)
    task.agent.stop = lambda: calls.append('stop')
    world.core.tasks['t'] = task
    world.core.reserve(task, 'https://shop.test', None)
    with world.core.lock:
        world.core.cancel(task)
    assert until(lambda: calls == ['observe', 'outcome', 'stop'])
    assert until(lambda: task.agent.closed)
    assert not world.core.reserve(conversation.Task('other', 'B'), 'https://shop.test', None)


def test_preflight_never_collects_unscoped_secrets(world, monkeypatch):
    from assistant import coordinator
    world.core.assess_start = lambda *_: {'requirements': [
        {'id': 'password', 'kind': 'secret', 'purpose': 'login', 'keys': ['password'], 'scope': 'task',
         'depends_on': [], 'options': [], 'reason': 'login may be needed'}]}
    monkeypatch.setattr(coordinator, 'apply_assessment', lambda *_: None)
    world.core.ask = lambda *_: pytest.fail('secret requires a current observed challenge')
    world.core.prepare(conversation.Task('t', 'Log in'))


def test_provider_reply_can_explicitly_choose_comparison(world):
    world.core.start_intent = lambda text: {'provider': None, 'compare': 'Compare' in text}
    world.core.answer_facts = lambda text, keys: {'provider': text}
    world.core.ask = lambda *_: {'text': 'Compare the available providers'}
    facts = world.core.prepare(conversation.Task('t', 'Find delivery'))
    assert facts['compare'] is True and facts['provider'] is None
    assert any('OTP' in b or 'login' in b for b in bodies(world))


def test_secret_advance_at_validation_lock_exit_never_reclassifies(world):
    task = conversation.Task('t', 'A')
    secret = conversation.Question(1, task, OTP, 0)
    ordinary = conversation.Question(2, task, {'kind': 'text', 'text': 'Where?'}, 0)
    world.core.open, world.core.queue = secret, [secret, ordinary]
    base = world.core.lock
    class AdvanceOnUnlock:
        depth = 0
        advanced = False
        def __enter__(self):
            base.acquire()
            self.depth += 1
            return self
        def __exit__(self, *_):
            self.depth -= 1
            if not self.depth and not self.advanced:
                self.advanced = True
                world.core.open = ordinary
                world.core.queue = [ordinary]
            base.release()
    world.core.lock = AdvanceOnUnlock()
    world.core.classify = lambda *_: pytest.fail('bound secret must never reach a classifier')
    world.core.receive({'id': 'race', 'text': '482913', 'question_id': '1', 'secret_reply': True})
    assert not ordinary.answered.is_set()
    assert '482913' not in str(world.core.log)


def test_remember_consent_is_a_tracked_question_and_old_tap_is_inert(world):
    task = conversation.Task('t', 'A')
    original = conversation.Question(1, task, ADDRESS, 0)
    world.core.offer_remember(original, 'o1')
    world.core.show_next()
    consent = world.core.open
    assert consent is not None and consent.message['kind'] == 'confirm'
    assert world.core.showing()['id'] == str(consent.id)
    tap(world, f'q{consent.id}:yes')
    assert until(lambda: world.core.profile.data['preferences'])
    newer = conversation.Question(999, task, OTP, 0)
    world.core.open, world.core.queue = newer, [newer]
    tap(world, f'q{consent.id}:yes')
    assert len(world.core.profile.data['preferences']) == 1 and not newer.answered.is_set()


def test_network_reservations_use_canonical_host_not_url_credentials(world):
    holder, visitor = conversation.Task('h', 'Hold'), conversation.Task('v', 'Visit')
    assert world.core.reserve(holder, 'https://shop.test', None)
    assert not world.core.reserve_access(visitor, 'https://user:password@shop.test/private')
    assert not world.core.reserve_access(visitor, 'https://shop.test:443/private')
    assert world.core.reserve(holder, 'https://bücher.test', None)
    assert not world.core.reserve_access(visitor, 'https://xn--bcher-kva.test/private')
