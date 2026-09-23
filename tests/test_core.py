"""The conversation core with scripted agents: routing, one open question, limits, timers, files. No browser."""

import threading
from types import SimpleNamespace

import pytest
from helpers import until

from assistant import core as conversation
from assistant.channel_web import WebChannel
from assistant.profile import Profile

ADDRESS = {"kind": "choice", "text": "Deliver to which address?", "remember": True,
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

    def make_agent(url, goal, title, asker, notify):
        w.agents[goal] = FakeAgent(w.scripts.get(goal, []), asker, notify, w.late.get(goal, []))
        return w.agents[goal]

    def classify(text, tasks, chat):
        w.classified.append(text)
        entry = w.intents.pop(0) if w.intents else ("NEW_TASK", "none", {"NEW_TASK": 1.0})
        return (*entry, {entry[1]: 1.0})[:4]  # the task pick is certain unless a test says otherwise

    w.core = conversation.Core(
        w.channel, profile=Profile(tmp_path / "profile.json"), make_agent=make_agent,
        start=lambda goal: (f"https://{w.hosts.get(goal, 'shop.test')}/", goal, {}),
        classify=classify, chat=lambda message, tasks: (f"About {len(tasks)} task(s).", {}),
        remember=lambda question, answer: (f"Default choice: {answer}", {}), clock=w.clock, poll=0.01)
    return w


def text(w, body):
    w.n += 1
    w.core.receive({"from": "local", "id": f"wamid.in{w.n}", "type": "text", "text": body, "context": None})


def tap(w, reply_id):
    w.core.receive({"from": "local", "id": "wamid.tap", "type": "interactive", "reply_id": reply_id, "text": "",
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


def test_code_requests_open_at_once_and_the_rest_wait_oldest_first(world):
    world.hosts.update({"A": "a.test", "B": "b.test", "C": "c.test"})
    world.scripts.update(A=[ask({"kind": "text", "text": "Where to?"})], B=[ask(ADDRESS)], C=[ask(OTP)])
    text(world, "A")
    assert until(lambda: world.core.open is not None)
    text(world, "B")
    assert until(lambda: len(world.core.queue) == 2)
    text(world, "C")
    assert until(lambda: world.core.open is not None and world.core.open.task.goal == "C")
    text(world, "482913")
    assert until(lambda: world.agents["C"].replies == [{"text": "482913"}])
    assert until(lambda: world.core.open is not None and world.core.open.task.goal == "A")
    world.intents.append(("ANSWER", "t1", {"ANSWER": 0.95}))
    text(world, "Airport")
    assert until(lambda: world.agents["A"].replies == [{"text": "Airport"}])
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
    path = world.channel.media(media)[0]
    assert until(lambda: world.agents["Dispute a charge"].replies == [{"file": str(path)}])


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
    world.scripts["Order Diet Coke"] = [ask(ADDRESS)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    tap(world, "q1:o1")
    assert until(lambda: buttons(world) == ["remember:1"])
    tap(world, "remember:1")
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
    assert until(lambda: any("huge.bin is too large to send here" in b for b in bodies(world)))


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

    def cannot(goal):
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
        tap(world, "q1:o1")
        until(lambda: world.core.open is not None and world.core.open.id == 2)
        return "ANSWER", "t1", {"ANSWER": 0.95}, {"t1": 1.0}

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
    assert world.core.tasks["t1"].updates == ["use my office address"]
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
    assert until(lambda: world.core.open is not None and world.core.open.message["kind"] == "secret")
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
    assert until(lambda: world.core.showing() == {"id": 1, "secret": True})


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
