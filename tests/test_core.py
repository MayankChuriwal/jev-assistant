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
EMPTY = {"skills": {}, "sites": {}}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeAgent:
    """Each tick runs one scripted step; with no steps left the task is done."""

    def __init__(self, script, asker, notify, late=()):
        self.script, self.asker, self.notify, self.late = list(script), asker, notify, list(late)
        self.state = {"status": "ready", "history": [], "files": [], "memory": [], "done": [],
                      "approval": {"action_label": "Pay ₹79"}, "commit": None}
        self.replies, self.closed = [], False

    def command(self, name):
        if self.script:
            self.script.pop(0)(self)
        else:
            self.state["status"] = "done"
        return self.state

    def update(self, text):
        self.state["memory"].append(f"User update: {text}")
        self.state["approval"] = None

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
    w = SimpleNamespace(scripts={}, hosts={}, agents={}, late={}, classified=[], intents=[], clock=Clock(), n=0,
                        intakes={}, splits={}, made={})
    w.channel = WebChannel(tmp_path / "media")

    def make_worker(task, url, asker, notify):
        w.made[task.goal] = task
        w.agents[task.goal] = FakeAgent(w.scripts.get(task.goal, []), asker, notify, w.late.get(task.goal, []))
        return w.agents[task.goal]

    def route(text, tasks, chat):
        w.classified.append(text)
        entry = w.intents.pop(0) if w.intents else ("NEW_TASK", "none", {"NEW_TASK": 1.0})
        return (*entry, {entry[1]: 1.0})[:4]  # the task pick is certain unless a test says otherwise

    def intake(message, kinds, several=True):
        info = {"several": "no", "skill": "general", "preauth": "no", **w.intakes.get(message, {})}
        return info if several else {**info, "several": "no"}

    w.core = conversation.Core(
        w.channel, profile=Profile(tmp_path / "profile.json"), make_worker=make_worker,
        start=lambda goal: (f"https://{w.hosts.get(goal, 'shop.test')}/", goal, True), route=route, intake=intake,
        split=lambda message: w.splits.get(message, [message]), library=EMPTY,
        chat=lambda message, tasks: (f"About {len(tasks)} task(s).", {}),
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
    world.core.new_task("Log in to Swiggy")  # chat runs one task at a time; the core can run several
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
    world.core.new_task("B")
    assert until(lambda: len(world.core.queue) == 2)
    world.core.new_task("C")
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
    world.scripts["Order Diet Coke"] = [lambda agent: agent.state["done"].append("Pay ₹79"), hold(gate)]
    text(world, "Order Diet Coke")
    assert until(lambda: "Order Diet Coke" in world.agents and world.agents["Order Diet Coke"].state["done"])
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
        world.core.new_task(g)
    assert until(lambda: sum(t.status == "running" for t in world.core.tasks.values()) == 3)
    world.core.new_task("D")
    world.core.new_task("E")
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
    world.core.new_task("Book a cab")
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

    world.core.route_message = down
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
    world.core.new_task("B")
    assert until(lambda: sum(t.status == "running" for t in world.core.tasks.values()) == 2)
    world.intents.append(("AMEND", "t1", {"AMEND": 0.9}, {"t1": 0.55, "t2": 0.45}))
    text(world, "make it 2")
    assert until(lambda: interactive(world)) and buttons(world) == ["route:t1:wamid.in2", "route:t2:wamid.in2"]
    assert world.core.tasks["t1"].updates == []
    tap(world, "route:t2:wamid.in2")
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

    world.core.route_message = slow
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
    world.core.new_task("Log in to Swiggy")  # chat runs one task at a time; the core can run several
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


def test_a_bench_task_answers_from_its_script_and_says_no_at_approval(tmp_path):
    channel = WebChannel(tmp_path / "media")
    asked = []

    def make_worker(task, url, asker, notify):
        asked.append(asker({"kind": "text", "text": "Where to?"}))
        asked.append(asker({"kind": "text", "text": "Uber Go ₹887. Book it?", "approval": True}))
        return SimpleNamespace(state={"status": "cancelled", "memory": [], "done": [], "files": [], "history": [],
                                      "messages": []}, command=lambda n: None, settle=lambda: None,
                               close=lambda: None)

    c = conversation.Core(channel, profile=Profile(tmp_path / "p.json"), make_worker=make_worker, library=EMPTY,
                          start=lambda goal: ("https://m.uber.com/", "Uber", True), poll=0.01,
                          intake=lambda message, kinds, several=True: {"several": "no", "skill": "general",
                                                                       "preauth": "no"})
    task = c.new_task("Book an Uber", script={"answers": [["where to", "Terminal 1"]], "fallback": "ok"})
    until(lambda: len(asked) == 2)
    assert asked == [{"text": "Terminal 1"}, {"cancel": True}]
    assert task.bench["gate"] == "Uber Go ₹887. Book it?"


def test_shutting_down_cancels_running_tasks_so_their_traces_are_written(world):
    world.scripts["order a coke"] = [ask({"kind": "text", "text": "Which size?"})]
    task = world.core.new_task("order a coke")
    until(lambda: world.core.open is not None)
    world.core.shutdown(timeout=5)
    assert task.status == "cancelled" and world.agents["order a coke"].closed


def test_a_cancelled_bench_task_gets_cancel_not_a_scripted_answer(tmp_path):
    c = conversation.Core(WebChannel(tmp_path / "media"), profile=Profile(tmp_path / "p.json"),
                          make_worker=lambda *a: None, start=lambda goal: ("https://x.test/", "x", True), library=EMPTY,
                          poll=0.01)
    task = conversation.Task("t9", "Order a coke")
    task.script, task.cancelled = {"answers": [], "fallback": "ok"}, True
    assert c.ask(task, {"kind": "text", "text": "Which size?"}) == {"cancel": True}


def test_a_bench_task_never_asks_for_card_details(tmp_path):
    c = conversation.Core(WebChannel(tmp_path / "media"), profile=Profile(tmp_path / "p.json"),
                          make_worker=lambda *a: None, start=lambda goal: ("https://x.test/", "x", True), library=EMPTY,
                          poll=0.01)
    task = conversation.Task("t9", "Order a coke")
    task.script = {"answers": [], "fallback": "ok"}
    replies = []
    threading.Thread(target=lambda: replies.append(c.ask(task, {"kind": "secret", "text": "Please provide the CVV "
                                                                "for your HDFC Bank Card."})), daemon=True).start()
    until(lambda: replies or c.open is not None)
    assert replies == [{"cancel": True}] and task.cancelled and "card details" in task.bench["stopped"]


def test_a_bench_task_moves_on_when_no_sign_in_code_comes(world):
    world.scripts["Order a coke"] = [ask({"kind": "secret", "text": "Please send the 4-digit OTP."})]
    task = world.core.new_task("Order a coke", script={"answers": [], "fallback": "ok"})
    until(lambda: world.core.open is not None)
    world.clock.now += conversation.BENCH_CODE_WAIT + 1
    until(lambda: task.status not in conversation.ACTIVE)
    assert task.bench["stopped"] == "needs you: no answer came within 5 minutes"


def test_a_new_request_while_a_task_runs_starts_alongside_it(world):
    gate = threading.Event()
    world.hosts["Book a cab"] = "cab.test"
    world.scripts["Order Diet Coke"] = [hold(gate)]
    world.scripts["Book a cab"] = [hold(gate)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    text(world, "Book a cab")
    assert until(lambda: "t2" in world.core.tasks and world.core.tasks["t2"].status == "running")
    assert not any("Drop it and start" in b for b in bodies(world))
    gate.set()


def test_a_message_with_two_errands_becomes_two_tasks(world):
    message = "milk from Zepto and a cab home"
    world.intakes[message] = {"several": "yes"}
    world.splits[message] = ["Buy milk on Zepto", "Book a cab home"]
    world.hosts["Book a cab home"] = "cab.test"
    text(world, message)
    assert until(lambda: {"Buy milk on Zepto", "Book a cab home"} <= set(world.agents))
    assert "On it: 2 tasks." in bodies(world)


def test_preauthorisation_and_its_cap_reach_the_task(world):
    goal = "Order coke, don't ask, under ₹300"
    world.intakes[goal] = {"preauth": "yes"}
    text(world, goal)
    assert until(lambda: goal in world.made)
    assert world.made[goal].preauth and world.made[goal].cap == 300.0


def test_errands_split_from_one_message_each_get_their_own_intake_from_their_own_words(world):
    message = "Buy milk on Zepto, don't ask me, under 300 rupees. And book a cab home, ask me first."
    goals = ["Buy milk on Zepto, don't ask me, under 300 rupees", "Book a cab home, ask me first"]
    world.intakes[message] = {"several": "yes"}
    world.splits[message] = goals
    calls, real = [], world.core.intake
    world.core.intake = lambda m, kinds, several=True: calls.append((m, several)) or real(m, kinds, several)
    world.core.log.extend([{"from": "user", "text": "just order it, don't ask"}, {"from": "agent", "text": "hi"}])
    text(world, message)
    assert until(lambda: set(goals) <= set(world.agents))
    assert sorted(calls) == sorted([(message, True), (goals[0], False), (goals[1], False)])


def test_intake_reads_only_the_users_message_never_the_chat_before_it(world):
    """A "don't ask" for an earlier errand must not pre-authorise a later one: intake takes no chat at all."""
    calls, real = [], world.core.intake
    world.core.intake = lambda m, kinds, several=True: calls.append((m, several)) or real(m, kinds, several)
    world.core.log.extend([{"from": "user", "text": "order it, don't ask"}, {"from": "agent", "text": "hi"}])
    world.core.new_task("Book a cab home", script={"answers": [], "fallback": "ok"})
    assert until(lambda: "Book a cab home" in world.agents)
    assert calls == [("Book a cab home", False)]
    text(world, "Order Diet Coke")
    assert until(lambda: "Order Diet Coke" in world.agents)
    assert calls[-1] == ("Order Diet Coke", True) and not world.made["Order Diet Coke"].preauth


def test_new_task_takes_the_core_lock_so_the_bench_thread_cannot_race_the_chat(world):
    started = threading.Event()
    made = []
    with world.core.lock:  # held here; new_task on another thread must wait for it
        thread = threading.Thread(target=lambda: (started.set(), made.append(world.core.new_task("Order a coke"))))
        thread.start()
        started.wait(2)
        thread.join(0.3)
        assert thread.is_alive() and not world.core.tasks
    thread.join(2)
    assert made and "t1" in world.core.tasks


def test_a_bench_question_scripted_for_the_user_goes_to_the_chat(tmp_path):
    c = conversation.Core(WebChannel(tmp_path / "media"), profile=Profile(tmp_path / "p.json"),
                          make_worker=lambda *a: None, start=lambda goal: ("https://x.test/", "x", True),
                          library=EMPTY, poll=0.01)
    task = conversation.Task("t9", "Pay my bill")
    task.script = {"answers": [["account", "@user"]], "fallback": "ok"}
    replies = []
    threading.Thread(target=lambda: replies.append(c.ask(task, {"kind": "text", "text": "What is your account "
                                                                "number?"})), daemon=True).start()
    assert until(lambda: c.open is not None)
    c.answer(c.open, {"type": "text", "text": "1234567"})
    assert until(lambda: replies) and replies == [{"text": "1234567"}]


def test_a_profile_value_learned_mid_session_is_hidden_from_the_concierges_next_model_call(tmp_path):
    from assistant.profile import SECRETS

    profile = Profile(tmp_path / "p.json")
    c = conversation.Core(WebChannel(tmp_path / "media"), profile=profile, make_worker=lambda *a: None,
                          start=lambda goal: ("https://x.test/", "x", True), library=EMPTY, poll=0.01)
    profile.data["phone"] = "9876543210"  # what a task's Worker.learn does while the chat runs
    assert c.hidden(lambda: SECRETS.get().text("recharge 9876543210")) == "recharge [profile:phone]"


def test_a_message_with_more_than_five_errands_starts_five_and_says_so(world):
    message = "seven errands"
    goals = [f"Errand {n}" for n in range(1, 8)]
    world.intakes[message] = {"several": "yes"}
    world.splits[message] = goals
    for n, goal in enumerate(goals):
        world.hosts[goal] = f"site{n}.test"
    text(world, message)
    assert until(lambda: set(goals[:5]) <= set(world.agents))
    assert "On it: 5 tasks. I'll start with the first five; ask again for the rest." in bodies(world)
    assert not set(goals[5:]) & set(world.agents)
