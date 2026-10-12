"""The conversation core with scripted agents: routing, one open question, limits, timers, files. No browser."""

import json
import threading
import time
from types import SimpleNamespace

import pytest
from fakes import store
from helpers import until

from assistant import core as conversation
from assistant.channel_web import WebChannel

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
                      "approval": {"action_label": "Pay ₹79"}, "commit": None,
                      "page": {"url": "https://shop.test/checkout"}}  # where a real task's last page was
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


class FakeBrain:
    """The brain, scripted: what reached it (so what any model saw), in order. By default each message starts a task,
    as the brain's tool would."""

    busy = False

    def __init__(self, heard):
        self.heard, self.core, self.starts = heard, None, True

    def hear(self, text):
        self.heard.append(text)
        if self.starts:
            self.core.new_task(text)


@pytest.fixture
def world(tmp_path):
    w = SimpleNamespace(scripts={}, hosts={}, agents={}, late={}, classified=[], clock=Clock(), n=0,
                        intakes={}, splits={}, made={}, learned=None, known=[], memory_outs=[])
    w.brain = FakeBrain(w.classified)
    w.channel = WebChannel(tmp_path / "media")

    def make_worker(task, url, asker, notify):
        w.made[task.goal] = task
        w.agents[task.goal] = FakeAgent(w.scripts.get(task.goal, []), asker, notify, w.late.get(task.goal, []))
        return w.agents[task.goal]

    def intake(message, kinds, several=True):
        info = {"several": "no", "skill": "general", "preauth": "no", **w.intakes.get(message, {})}
        return info if several else {**info, "several": "no"}

    w.core = conversation.Core(
        w.channel, memory=store(), make_worker=make_worker,
        start=lambda goal: (f"https://{w.hosts.get(goal, 'shop.test')}/", goal, True), brain=w.brain, intake=intake,
        split=lambda message: w.splits.get(message, [message]), library=EMPTY,
        remember=lambda question, answer: (f"Default choice: {answer}", {}), clock=w.clock, poll=0.01,
        learn=lambda state, memory: w.learned,
        memory_job=lambda message, known: w.known.append(known) or w.memory_outs.pop(0))
    w.brain.core = w.core
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


def test_while_a_task_runs_a_message_waits_for_the_brain_with_one_notice(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    world.brain.starts = False
    text(world, "where are we?")
    text(world, "thanks")
    gate.set()
    assert world.classified == ["Order Diet Coke", "where are we?", "thanks"]
    assert bodies(world).count("I'll get to that as soon as this finishes.") == 1


def test_a_stop_word_while_a_task_runs_stops_it_without_the_brain(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate), ask(ADDRESS)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    text(world, "stop")
    gate.set()
    assert until(lambda: world.core.tasks["t1"].status == "cancelled") and world.classified == ["Order Diet Coke"]


def test_text_typed_while_a_tasks_question_is_open_answers_it(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    text(world, "the office one")
    assert until(lambda: world.agents["Order Diet Coke"].replies == [{"text": "the office one"}])
    assert world.classified == ["Order Diet Coke"]


def test_a_tap_on_a_brain_reply_reaches_the_brain_as_the_options_words(world):
    world.brain.starts = False
    world.core.reply("You have 3 AutoPay mandates. Which should I cancel?", ["OPENAI LLC", "GOOGLE PLAY", "AISLE"])
    assert buttons(world) == ["b1:1", "b1:2", "b1:3"]
    assert interactive(world)[-1]["interactive"]["body"]["text"] == "You have 3 AutoPay mandates. Which should I cancel?"
    tap(world, "b1:3")
    assert world.classified == ["AISLE"]


def test_a_long_option_comes_back_whole_though_its_button_is_cut(world):
    world.brain.starts = False
    options = ["Cancel ONE97 COMMUNICATIONS LIMITED", "No, keep it"]
    world.core.reply("Cancel ONE97 COMMUNICATIONS LIMITED (₹ 99)?", options)
    titles = [b["reply"]["title"] for b in interactive(world)[-1]["interactive"]["action"]["buttons"]]
    assert titles == ["Cancel ONE97…", "No, keep it"]  # cut at a word, never mid-word
    tap(world, "b1:1")
    assert world.classified == ["Cancel ONE97 COMMUNICATIONS LIMITED"]


def test_a_brain_reply_never_shows_the_placeholders_the_users_details_are_hidden_as(world):
    # live: Experian's dispute offered “Name: [profile:name]” and “Mobile: [profile:phone]” as buttons
    world.brain.starts = False
    world.core.reply("Name: [profile:name]; Mobile: [profile:phone]. Which is wrong?",
                     ["Name: [profile:name]", "Mobile: [profile:phone]", "Address 1: Howrah"])
    shown = interactive(world)[-1]["interactive"]
    assert shown["body"]["text"] == "Name: your full name; Mobile: your phone number. Which is wrong?"
    assert [b["reply"]["title"] for b in shown["action"]["buttons"]] == ["Name", "Mobile", "Address 1: Howrah"]


def test_a_brain_reply_without_options_is_plain_text(world):
    world.core.reply("Done. MyUPI no longer lists AISLE.", [])
    assert bodies(world) == ["Done. MyUPI no longer lists AISLE."] and interactive(world) == []


def test_typing_shows_while_the_brain_thinks(world):
    world.brain.busy = True
    world.core.update_typing()
    assert world.channel.typing is True
    world.brain.busy = False
    world.core.update_typing()
    assert world.channel.typing is False


def test_a_remember_button_follows_an_answered_choice(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    tap(world, "q1:o1")
    assert until(lambda: buttons(world) == ["remember:1"])
    tap(world, "remember:1")
    assert until(lambda: "Saved for next time: Default choice: Home" in bodies(world))
    saved = world.core.memory.facts("preference")[0]
    assert saved["text"] == "Default choice: Home" and "shop.test" in saved["tags"]


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
    assert world.agents["Order Diet Coke"].state["errors"] == [{"step": 0, "error": "ValueError: bad JSON"}] * 3


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
    text(world, "stop")
    gate.set()
    agent = world.agents["Order Diet Coke"]
    assert until(lambda: agent.replies == [{"cancel": True}]) and flags == [True]
    assert interactive(world) == [] and world.core.open is None


CAPTCHA = {"kind": "image", "image": "cG5n", "captcha": True, "text": "The site wants a check (captcha) solved."}


@pytest.mark.parametrize("reply", ["F658", "none", "2 5 8", "2, 5 and 8"])
def test_a_captcha_reply_reaches_the_captcha_without_jev(world, reply):  # Jev read “F658” as small talk
    world.scripts["Cancel Netflix"] = [ask(CAPTCHA)]
    text(world, "Cancel Netflix")
    assert until(lambda: world.core.open is not None)
    text(world, reply)
    assert until(lambda: world.agents["Cancel Netflix"].replies) and world.classified == ["Cancel Netflix"]
    assert world.agents["Cancel Netflix"].replies[0]["text"] == reply


def test_stop_at_a_captcha_still_stops_the_task(world):
    world.scripts["Cancel Netflix"] = [ask(CAPTCHA)]
    text(world, "Cancel Netflix")
    assert until(lambda: world.core.open is not None)
    text(world, "stop")
    assert world.classified == ["Cancel Netflix"] and until(lambda: world.core.tasks["t1"].cancelled)


NPCI_NOTE = {"upihelp.npci.org.in": {"host": "upihelp.npci.org.in", "name": "NPCI UPI Help", "aliases": []}}


@pytest.mark.parametrize("message", ["log out", "Logout", "please log me out", "End session.", "sign out of NPCI"])
def test_log_out_ends_the_users_kept_sign_ins_without_any_model(world, monkeypatch, message):
    from assistant import browser

    ended = []
    monkeypatch.setattr(browser, "end_sessions", lambda owner=None: ended.append(owner) or ["upihelp.npci.org.in"])
    world.core.library = {"skills": {}, "sites": NPCI_NOTE}
    text(world, message)
    assert ended == ["local"] and world.classified == []
    assert bodies(world) == ["Logged out of NPCI UPI Help. Your next request there starts with a fresh sign-in."]


def test_a_task_that_mentions_logging_out_is_still_a_task(world, monkeypatch):
    from assistant import browser

    monkeypatch.setattr(browser, "end_sessions", lambda owner=None: pytest.fail("not a log out"))
    text(world, "log out of my Amazon account and sign in with my office one")
    assert world.classified == ["log out of my Amazon account and sign in with my office one"]


def test_a_stop_with_nothing_running_goes_to_the_brain(world):
    world.brain.starts = False
    text(world, "stop")
    assert world.classified == ["stop"] and bodies(world) == []


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

    c = conversation.Core(channel, memory=store(), make_worker=make_worker, library=EMPTY,
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
    c = conversation.Core(WebChannel(tmp_path / "media"), memory=store(),
                          make_worker=lambda *a: None, start=lambda goal: ("https://x.test/", "x", True), library=EMPTY,
                          poll=0.01)
    task = conversation.Task("t9", "Order a coke")
    task.script, task.cancelled = {"answers": [], "fallback": "ok"}, True
    assert c.ask(task, {"kind": "text", "text": "Which size?"}) == {"cancel": True}


def test_a_bench_task_never_asks_for_card_details(tmp_path):
    c = conversation.Core(WebChannel(tmp_path / "media"), memory=store(),
                          make_worker=lambda *a: None, start=lambda goal: ("https://x.test/", "x", True), library=EMPTY,
                          poll=0.01)
    task = conversation.Task("t9", "Order a coke")
    task.script = {"answers": [], "fallback": "ok"}
    replies = []
    threading.Thread(target=lambda: replies.append(c.ask(task, {"kind": "secret", "text": "Please provide the CVV "
                                                                "for your HDFC Bank Card."})), daemon=True).start()
    until(lambda: replies or c.open is not None)
    assert replies == [{"cancel": True}] and task.cancelled and "card details" in task.bench["stopped"]


@pytest.mark.parametrize("kind", ["text", "secret"])  # stuck() asks hidden when a code field is on the page
def test_a_stuck_bench_task_stops_instead_of_taking_a_default_answer(tmp_path, kind):
    c = conversation.Core(WebChannel(tmp_path / "media"), memory=store(),
                          make_worker=lambda *a: None, start=lambda goal: ("https://x.test/", "x", True), library=EMPTY,
                          poll=0.01)
    task = conversation.Task("t9", "Book a bike")
    task.script = {"answers": [], "fallback": "ok"}
    question = ("I'm stuck on Rapido, trying to Clear the pickup textbox to prepare for correct location input. "
                "What should I do?")  # round 2: the location default answered it, and the run circled
    replies = []
    threading.Thread(target=lambda: replies.append(c.ask(task, {"kind": kind, "text": question})), daemon=True).start()
    until(lambda: replies or c.open is not None)
    assert replies == [{"cancel": True}] and task.cancelled and c.open is None
    assert task.bench["stopped"] == f"stuck: {question}" and task.bench["asked"] == []


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
    c = conversation.Core(WebChannel(tmp_path / "media"), memory=store(),
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


def test_an_id_learned_mid_session_is_hidden_from_the_concierges_next_model_call(tmp_path):
    from assistant.privacy import SECRETS

    memory = store()
    c = conversation.Core(WebChannel(tmp_path / "media"), memory=memory, make_worker=lambda *a: None,
                          start=lambda goal: ("https://x.test/", "x", True), library=EMPTY, poll=0.01)
    memory.set_id("phone", "phone number", "9876543210")  # what a task's worker does while the chat runs
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


# ---- Plan 7 Task 5: the standing-approval offer

ORDER = {"label": "Pay ₹79", "site": "shop.test", "cart": [{"item": "diet coke 300 ml", "qty": 1}], "total": "₹79",
         "by": "user"}


def ended(orders, script=None):
    return SimpleNamespace(script=script, worker=SimpleNamespace(state={"orders": orders}))


def test_an_order_you_approved_and_the_site_confirmed_offers_a_standing_approval(world, monkeypatch):
    monkeypatch.setenv("JEV_PREAUTH", "on")
    world.scripts["Order Diet Coke"] = [lambda agent: agent.state.update(orders=[dict(ORDER)]), tell("Ordered.")]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    assert bodies(world)[-1] == "Next time, place this same order on shop.test without asking, up to ₹100?"
    assert buttons(world) == ["rule:1:yes", "rule:1:no"]
    tap(world, "rule:1:yes")
    rule = world.core.memory.live_rules()[0]
    assert (rule["site"], rule["cart"], rule["cap"]) == ("shop.test", ORDER["cart"], 100.0)
    assert bodies(world)[-1].startswith("Saved: your standing approval for this order on shop.test, up to ₹100, for 30 "
                                        "days.")


def test_keep_asking_saves_nothing_and_the_offer_says_when_pre_approval_is_off(world):
    world.core.offer_rules(ended([dict(ORDER)]))
    assert "Pre-approval is off" in bodies(world)[-1]
    tap(world, "rule:1:no")
    assert world.core.memory.live_rules() == [] and bodies(world)[-1] == "OK. I'll keep asking before placing it."
    tap(world, "rule:1:yes")  # an offer is decided once
    assert world.core.memory.live_rules() == []


@pytest.mark.parametrize("change", [{"by": "preauth"}, {"by": "standing"}, {"cart": []}, {"total": ""},
                                    {"total": "$79"}, {"bench": True}, {"ruled": True}])
def test_no_offer_without_your_yes_a_readable_rupee_order_or_on_the_bench_or_twice(world, change):
    change = dict(change)
    if change.pop("ruled", False):  # a live rule already covers this order
        world.core.memory.approve("shop.test", ORDER["cart"], 100, "Don't ask next time")
    bench = change.pop("bench", False)
    world.core.offer_rules(ended([{**ORDER, **change}], script=[] if bench else None))
    assert interactive(world) == []


# ---- Plan 7 Task 6: what a task taught, offered after it


def test_after_a_task_its_notes_and_guesses_are_offered_and_decided_once(world):
    ids = world.core.memory.draft_notes("shop.test", ["Search appears after the pop-up closes"])
    world.learned = {"host": "shop.test", "habit": None, "notes": [(ids[0], "Search appears after the pop-up closes")],
                     "guesses": [{"text": "You prefer Diet Coke 300 ml", "tags": ["groceries", "shop.test"]}]}
    world.scripts["Order Diet Coke"] = [tell("Ordered.")]
    text(world, "Order Diet Coke")
    assert until(lambda: len(interactive(world)) == 2)
    assert interactive(world)[0]["interactive"]["body"]["text"] == ("I learned about shop.test:\n"
                                                                    "• Search appears after the pop-up closes")
    assert buttons(world) == ["guess:2:yes", "guess:2:no"]
    assert bodies(world)[-1] == "Should I remember this? You prefer Diet Coke 300 ml"
    tap(world, "note:1:save")
    tap(world, "guess:2:yes")
    tap(world, "note:1:skip")  # decided already
    assert world.core.memory.notes("shop.test") == ["Search appears after the pop-up closes"]
    saved = world.core.memory.facts("preference")[0]
    assert saved["text"] == "You prefer Diet Coke 300 ml" and saved["tags"] == ["groceries", "shop.test"]


@pytest.mark.parametrize("key", ["shop.test", "shop.test/checkout"])  # SmartBuy's note: a path on GyFTR's host
def test_a_site_whose_note_says_learn_no_offers_nothing_learned(world, key):  # MyUPI: a wrong lesson after each request
    world.core.library = {"skills": {}, "sites": {key: {"host": key, "name": "Shop", "aliases": [], "learn": "no"}}}
    world.learned = {"host": "shop.test", "habit": None, "notes": [(1, "Search appears after the pop-up closes")],
                     "guesses": [{"text": "You prefer Diet Coke 300 ml", "tags": ["groceries"]}]}
    world.scripts["Order Diet Coke"] = [tell("Ordered.")]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status not in conversation.ACTIVE)
    time.sleep(0.1)
    assert not any(b.startswith(("I learned about", "Should I remember this?")) for b in bodies(world))


def test_skip_and_no_keep_nothing(world):
    ids = world.core.memory.draft_notes("shop.test", ["Search appears after the pop-up closes"])
    world.learned = {"host": "shop.test", "habit": None, "notes": [(ids[0], "Search appears after the pop-up closes")],
                     "guesses": [{"text": "You prefer Diet Coke 300 ml", "tags": []}]}
    world.scripts["Order Diet Coke"] = [tell("Ordered.")]
    text(world, "Order Diet Coke")
    assert until(lambda: len(interactive(world)) == 2)
    tap(world, "note:1:skip")
    tap(world, "guess:2:no")
    assert world.core.memory.notes("shop.test") == [] and world.core.memory.facts("preference") == []


# ---- Plan 7 Task 7: memory by chat

ZEPTO = {"skills": {}, "sites": {"zeptonow.com": {"name": "Zepto", "aliases": [], "host": "zeptonow.com",
                                                  "start": "https://www.zeptonow.com/", "body": ""}}}

def said(op, facts=(), key="", label="", refs=(), site="", reply=""):
    return {"op": op, "facts": list(facts), "id": {"key": key, "label": label}, "refs": list(refs), "site": site,
            "reply": reply}


def tell_memory(world, message, out):
    world.memory_outs.append(out)
    before = len(bodies(world))
    world.core.memory_message(message)  # what the brain's memory tool calls
    assert until(lambda: len(bodies(world)) > before)
    return bodies(world)[-1]


def test_you_can_tell_it_what_to_remember(world):
    office = {"kind": "address", "label": "Office", "text": "12 MG Road", "tags": ["address"]}
    assert tell_memory(world, "remember my office is 12 MG Road", said("remember", [office])) == \
        "Saved: Office: 12 MG Road"
    assert world.core.memory.facts("address")[0]["text"] == "12 MG Road"
    moved = {**office, "text": "80 Residency Road"}
    tell_memory(world, "my office moved to 80 Residency Road", said("remember", [moved]))
    assert [f["text"] for f in world.core.memory.facts("address")] == ["80 Residency Road"]  # a label is replaced


def test_an_id_in_the_message_is_cut_by_code_and_kept_encrypted(world):
    reply = tell_memory(world, "my BESCOM account number is 1234567890",
                        said("remember", key="bescom_account", label="BESCOM account number"))
    assert reply.startswith("Saved your BESCOM account number. The models never see it.")
    assert "jev memory set bescom_account" in reply
    assert world.core.memory.id_value("bescom_account") == "1234567890"


def test_two_numbers_or_none_are_never_guessed(world):
    out = said("remember", key="bescom_account", label="BESCOM account number")
    assert tell_memory(world, "my BESCOM account is 1234567890, the old one 9876543210", out) == \
        "I see more than one number there. Send your BESCOM account number on its own, please."
    assert tell_memory(world, "remember my BESCOM account", out) == \
        "I'll ask for your BESCOM account number through the hidden field the next time a task needs it."
    with pytest.raises(KeyError):
        world.core.memory.id_value("bescom_account")


def test_a_card_number_is_never_kept(world):
    card = {"kind": "fact", "label": "", "text": "My card is 4111 1111 1111 1111", "tags": []}
    reply = tell_memory(world, "remember my card 4111 1111 1111 1111", said("remember", [card]))
    assert reply.startswith("I don't keep that:") and world.core.memory.facts() == []


def test_list_forget_and_undo_by_chat(world):
    memory = world.core.memory
    office = memory.remember("address", "12 MG Road", label="Office", tags=["address"])
    memory.set_id("phone", "phone number", "+91 98765 43210")
    listed = tell_memory(world, "what do you remember about me?", said("list"))
    assert "Office: 12 MG Road" in listed and "phone number" in listed and "98765" not in listed
    assert world.known[-1]["ids"] == [{"ref": "id:phone", "label": "phone number"}]
    assert "98765" not in json.dumps(world.known, default=str)
    assert tell_memory(world, "forget my office", said("forget", refs=[str(office), "999"])) == \
        "Forgot Office: 12 MG Road."
    assert tell_memory(world, "undo that", said("undo")) == "Undone the last change to what I remember."
    assert memory.facts("address")[0]["text"] == "12 MG Road"
    assert tell_memory(world, "forget my gym", said("forget", refs=["999"])).startswith("I couldn't find that")


def test_a_standing_approval_from_your_words_needs_an_order_placed_once_and_a_rupee_cap(world):
    memory = world.core.memory
    world.core.library = ZEPTO
    out = said("approve", site="zeptonow.com")
    words = "always order my usual Zepto cart without asking, up to ₹300"
    assert tell_memory(world, words, out) == "Place this order with me once; then I can remember it."
    memory.record_habit({"host": "zeptonow.com", "skill": "groceries", "title": "Zepto · Diet Coke",
                         "goal": "Order Diet Coke on Zepto", "start": "https://www.zeptonow.com/", "plan": [],
                         "steps": [], "cart": [{"item": "diet coke 300 ml", "qty": 1}]})
    assert tell_memory(world, "always order my usual Zepto cart without asking", out) == \
        "Tell me the most it may cost, like “up to ₹300”."
    reply = tell_memory(world, words, out)
    assert reply.startswith("Saved: zeptonow.com · diet coke 300 ml × 1 · up to ₹300 · until ")
    assert "Pre-approval is off" in reply and memory.live_rules()[0]["words"] == words


def test_a_message_memory_cannot_use_says_why(world):
    assert tell_memory(world, "remember to be nice", said("none", reply="That isn't something I can keep.")) == \
        "That isn't something I can keep."


def test_id_candidates_are_emails_upi_ids_numbers_and_codes_with_digits():
    text = "mail me@example.com or pay mayank@okicici, phone +91 98765 43210, ID AB12CD, pin 560034, 4th floor"
    assert conversation.id_candidates(text) == ["me@example.com", "mayank@okicici", "+91 98765 43210", "560034",
                                                "AB12CD"]


# ---- Plan 7 Task 7 fixes: stray tokens, never-stored words, hidden replies, corroborated sites

ASK_LATER = "I'll ask for your BESCOM account number through the hidden field the next time a task needs it."
BESCOM = said("remember", key="bescom_account", label="BESCOM account number")


@pytest.mark.parametrize("message", [
    "remember my BESCOM account, the March 2026 bill",
    "my BESCOM account bill was ₹1500 this time",
    "my BESCOM account bill was Rs. 1,500 and INR 20",
    "my BESCOM account is on the bill dated 12/05/2026",
    "my BESCOM account bill of 12-05-26 and 2026-05-12"])
def test_a_year_price_or_date_is_never_taken_for_an_id(world, message):
    assert tell_memory(world, message, BESCOM) == ASK_LATER
    with pytest.raises(KeyError):
        world.core.memory.id_value("bescom_account")


def test_candidates_leave_out_noise_but_keep_numbers_and_grouped_ids():
    assert conversation.id_candidates("bill ₹1500 of 12/05/2026, March 2026") == []
    assert conversation.id_candidates("account 2026 1234 5678") == ["2026 1234 5678"]
    assert conversation.id_candidates("account 1234567890 for 2026") == ["1234567890"]


def test_a_hyphenated_id_is_saved_whole_or_not_at_all(world):
    assert conversation.id_candidates("my ID is AB-1234-CD") == ["AB-1234-CD"]
    out = said("remember", key="bescom_account", label="BESCOM account number")
    tell_memory(world, "my BESCOM account is AB-1234-CD", out)
    assert world.core.memory.id_value("bescom_account") == "AB-1234-CD"
    assert tell_memory(world, "my BESCOM account is AB-1234-CD or XY-99-ZZ", out) == \
        "I see more than one number there. Send your BESCOM account number on its own, please."
    assert world.core.memory.id_value("bescom_account") == "AB-1234-CD"


@pytest.mark.parametrize("message", ["my Netflix password is hunter2", "my bank OTP is 481516 remember it",
                                     "my card PIN is 4821 remember that"])
def test_a_secret_word_in_the_message_keeps_the_whole_message_out_of_memory(world, message):
    out = said("remember", [{"kind": "fact", "label": "", "text": "likes tea", "tags": []}], key="netflix_login",
               label="Netflix login")
    assert tell_memory(world, message, out) == conversation.UNSTORABLE
    assert world.core.memory.facts() == []
    with pytest.raises(KeyError):
        world.core.memory.id_value("netflix_login")


def test_a_model_cannot_carry_a_value_in_the_id_name(world):
    out = said("remember", key="bescom_account", label="BESCOM account 1234567890")
    assert tell_memory(world, "my BESCOM account is 1234567890", out) == \
        "I'll ask for that ID through the hidden field the next time a task needs it."
    with pytest.raises(KeyError):
        world.core.memory.id_value("bescom_account")


def test_an_id_overwrite_says_updated_and_a_value_inside_a_fact_is_hidden(world):
    out = said("remember", [{"kind": "fact", "label": "", "text": "BESCOM account is 1234567890", "tags": []}],
               key="bescom_account", label="BESCOM account number")
    reply = tell_memory(world, "my BESCOM account number is 1234567890", out)
    assert reply.startswith("Saved your BESCOM account number.") and "1234567890" not in reply
    assert "[profile:bescom_account]" in reply
    assert all("1234567890" not in f["text"] for f in world.core.memory.facts())
    assert tell_memory(world, "my BESCOM account number is 5550001111", out).startswith("Updated your BESCOM")
    assert world.core.memory.id_value("bescom_account") == "5550001111"


LOOKS_LIKE_AN_ID = ("That looks like an ID. Tell me what it is called (like “my BESCOM account number is …”) so I can "
                    "keep it hidden.")


@pytest.mark.parametrize("fact", [
    {"kind": "fact", "label": "", "text": "BESCOM account number 1234567890", "tags": []},
    {"kind": "preference", "label": "BESCOM 1234567890", "text": "pay the bill monthly", "tags": []},
    {"kind": "fact", "label": "", "text": "my gym membership is GYM2024X", "tags": []}])
def test_an_id_the_model_left_in_a_fact_is_refused_and_nothing_is_kept(world, fact):
    office = {"kind": "address", "label": "Office", "text": "12 MG Road", "tags": []}
    reply = tell_memory(world, f"remember {fact['label']} {fact['text']} and my office is 12 MG Road",
                        said("remember", [office, fact]))
    assert reply == LOOKS_LIKE_AN_ID
    assert world.core.memory.facts() == [] and world.core.memory.ids() == []
    rows = world.core.memory.run(lambda cur: cur.execute("SELECT to_jsonb(e)::text AS row FROM events e").fetchall())
    assert rows == []


def test_an_address_with_a_pincode_and_a_flat_number_is_kept(world):
    home = {"kind": "address", "label": "Home", "text": "Flat 4B2, 12 MG Road, Bengaluru 560034", "tags": []}
    assert tell_memory(world, "my home is Flat 4B2, 12 MG Road, Bengaluru 560034", said("remember", [home])) == \
        "Saved: Home: Flat 4B2, 12 MG Road, Bengaluru 560034"
    fact = world.core.memory.run(lambda cur: cur.execute("SELECT text, source FROM facts").fetchone())
    assert fact == {"text": "Flat 4B2, 12 MG Road, Bengaluru 560034", "source": ""}  # the message never kept whole


def test_a_message_without_an_id_keeps_its_words_as_the_source(world):
    tea = {"kind": "preference", "label": "", "text": "likes masala chai", "tags": []}
    tell_memory(world, "remember I like masala chai", said("remember", [tea]))
    assert world.core.memory.run(lambda cur: cur.execute("SELECT source FROM facts").fetchone()["source"]) == \
        "remember I like masala chai"


def test_a_saved_id_already_hidden_in_a_fact_is_not_refused(world):
    world.core.memory.set_id("bescom_account", "BESCOM account number", "1234567890")
    fact = {"kind": "fact", "label": "", "text": "BESCOM account 1234567890 is for the flat", "tags": []}
    reply = tell_memory(world, "remember BESCOM account 1234567890 is for the flat", said("remember", [fact]))
    assert reply == "Saved: BESCOM account [profile:bescom_account] is for the flat"
    assert world.core.memory.run(lambda cur: cur.execute("SELECT source FROM facts").fetchone()["source"]) == ""


def test_a_standing_approval_in_words_memory_cannot_keep_is_refused(world):
    world.core.library = ZEPTO
    world.core.memory.record_habit({"host": "zeptonow.com", "skill": "groceries", "title": "Zepto · Diet Coke",
                                    "goal": "Order Diet Coke on Zepto", "start": "https://www.zeptonow.com/",
                                    "plan": [], "steps": [], "cart": [{"item": "diet coke", "qty": 1}]})
    words = "always order my usual Zepto cart up to ₹300, pay with card 4111 1111 1111 1111"
    assert tell_memory(world, words, said("approve", site="zeptonow.com")) == conversation.UNSTORABLE
    assert world.core.memory.live_rules() == []


def test_a_standing_approval_finds_the_cart_of_an_order_placed_on_a_subdomain(world):
    from assistant import skills

    world.core.library = {"skills": {}, "sites": skills.load_sites()}
    world.core.memory.record_habit({"host": "book.olacabs.com", "skill": "rides", "title": "Ola · Office",
                                    "goal": "Cab to office", "start": "https://book.olacabs.com/", "plan": [],
                                    "steps": [], "cart": [{"item": "mini to office", "qty": 1}]})
    reply = tell_memory(world, "always book my usual Ola ride, up to ₹300", said("approve", site="Ola"))
    assert reply.startswith("Saved: olacabs.com · mini to office × 1 · up to ₹300")


@pytest.mark.parametrize("words", ["I always buy Amul Gold 500ml", "Tata Tea Gold 250g", "a 6-pack of Coke",
                                   "rent a 2BHK near work", "jeans in 32x30", "10kg atta and 750 ml oil",
                                   "a 2-pack of soap, 3 pcs of Dove"])
def test_a_quantity_is_never_taken_for_an_id(world, words):
    assert conversation.id_candidates(words) == [] and not world.core.unnamed_id(words)
    fact = {"kind": "preference", "label": "", "text": words, "tags": []}
    assert tell_memory(world, f"remember {words}", said("remember", [fact])) == f"Saved: {words}"
    assert world.core.memory.run(lambda cur: cur.execute("SELECT source FROM facts").fetchone()["source"]) == \
        f"remember {words}"


def test_quantities_leave_real_ids_alone():
    assert conversation.id_candidates("BESCOM account number 1234567890") == ["1234567890"]
    assert conversation.id_candidates("my account is 500ml1234") == ["500ml1234"]
    assert conversation.id_candidates("pay 500g@okicici for 2 kg") == ["500g@okicici"]
    assert conversation.id_candidates("ID AB-1234-CD, 500ml") == ["AB-1234-CD"]
    assert conversation.id_candidates("account 1234567890 mg road") == ["1234567890"]  # a long number is never a unit


def test_a_named_number_beside_a_quantity_is_still_refused(world):
    fact = {"kind": "fact", "label": "", "text": "BESCOM account number 1234567890, 500ml milk", "tags": []}
    assert tell_memory(world, "remember BESCOM account number 1234567890 and 500ml milk",
                       said("remember", [fact])) == LOOKS_LIKE_AN_ID
    assert world.core.memory.facts() == []


def test_a_model_reply_with_an_id_value_is_hidden(world):
    world.core.memory.set_id("phone", "phone number", "+91 98765 43210")
    reply = tell_memory(world, "remember nothing", said("none", reply="You said +91 98765 43210."))
    assert "98765" not in reply


def test_an_id_is_forgotten_by_its_ref(world):
    world.core.memory.set_id("phone", "phone number", "+91 98765 43210")
    assert tell_memory(world, "forget my phone", said("forget", refs=["id:phone"])) == "Forgot your phone number."
    with pytest.raises(KeyError):
        world.core.memory.id_value("phone")


def test_a_memory_model_failure_or_store_failure_is_a_chat_message(world, monkeypatch):
    def broken(message, known):
        raise RuntimeError("model down")

    world.core.memory_job = broken
    world.core.memory_message("what do you remember?")
    assert until(lambda: any("I couldn't read that: model down" in b for b in bodies(world)))
    world.core.memory_job = lambda message, known: said("list")
    monkeypatch.setattr(world.core, "memory_op", lambda *a: 1 / 0)
    world.core.memory_message("what do you remember now?")
    assert until(lambda: any("I couldn't do that: division by zero" in b for b in bodies(world)))


def test_a_standing_approval_needs_the_site_the_user_named(world):
    out = said("approve", site="https://www.zeptonow.com/some/path")
    world.core.memory.record_habit({"host": "zeptonow.com", "skill": "groceries", "title": "Zepto · Diet Coke",
                                    "goal": "Order Diet Coke on Zepto", "start": "https://www.zeptonow.com/",
                                    "plan": [], "steps": [], "cart": [{"item": "diet coke", "qty": 1}]})
    ask = "Which site is that for? Name it, like “my usual Zepto order”."
    assert tell_memory(world, "always order my usual cart, up to ₹300", out) == ask  # the user named no site
    assert tell_memory(world, "always order my cart, up to ₹300", said("approve", site="")) == ask
    assert tell_memory(world, "always order my usual cart, up to ₹300", said("approve", site="zepto")) == ask
    assert tell_memory(world, "always order from ZeptoNow.com, up to ₹300", out).startswith("Saved: zeptonow.com")
    assert world.core.memory.live_rules()[0]["site"] == "zeptonow.com"


def test_a_site_note_resolves_the_site_the_user_and_the_model_named(world):
    world.core.library = ZEPTO
    world.core.memory.record_habit({"host": "zeptonow.com", "skill": "groceries", "title": "Zepto · Diet Coke",
                                    "goal": "Order Diet Coke on Zepto", "start": "https://www.zeptonow.com/",
                                    "plan": [], "steps": [], "cart": [{"item": "diet coke", "qty": 1}]})
    assert tell_memory(world, "always order my usual Zepto cart, up to ₹300", said("approve", site="Zepto")) \
        .startswith("Saved: zeptonow.com")
    other = "always order my usual Blinkit cart, up to ₹300"
    assert tell_memory(world, other, said("approve", site="Zepto")).startswith("Which site is that for?")


# ---- cleanup wave: the site must be a whole word; a long message is scanned only so far


def test_a_site_inside_another_word_does_not_corroborate_the_site(world):
    out = said("approve", site="ola.com")
    world.core.memory.record_habit({"host": "ola.com", "skill": "cabs", "title": "Ola · Office",
                                    "goal": "Cab to office", "start": "https://ola.com/", "plan": [], "steps": [],
                                    "cart": [{"item": "mini to office", "qty": 1}]})
    ask = "Which site is that for? Name it, like “my usual Zepto order”."
    for words in ("always book my usual viola.com ride, up to ₹300", "always book my usual ola.community ride, ₹300",
                  "always book my usual my-ola.com ride, up to ₹300"):
        assert tell_memory(world, words, out) == ask, words
    for words in ("always book my usual ola.com ride, up to ₹300", "always book www.ola.com rides up to ₹300"):
        assert tell_memory(world, words, out).startswith("Saved: ola.com"), words


def test_a_long_message_is_scanned_for_ids_only_so_far_and_quickly():
    started = time.monotonic()
    assert conversation.id_candidates("1-" * 50000) == []
    assert time.monotonic() - started < 1.0
    assert conversation.id_candidates("x " * 999 + "1234567890") == []  # an ID the cut splits is not a candidate
    assert conversation.id_candidates("my BESCOM account is 1234567890 " + "x " * 2000) == ["1234567890"]


def test_saved_notes_are_listed_and_forgotten_by_their_ref_in_chat(world):
    memory = world.core.memory
    note = memory.draft_notes("zeptonow.com", ["Search appears after the location pop-up closes"])[0]
    memory.decide_notes([note], save=True)
    draft = memory.draft_notes("zeptonow.com", ["A draft nobody saved"])[0]
    listed = tell_memory(world, "what do you remember?", said("list"))
    assert "Learned site notes:\n• zeptonow.com: Search appears after the location pop-up closes" in listed
    assert "draft" not in listed
    assert world.known[-1]["notes"] == [{"ref": f"note:{note}", "site": "zeptonow.com",
                                         "text": "Search appears after the location pop-up closes"}]
    assert tell_memory(world, "forget that draft", said("forget", refs=[f"note:{draft}"])).startswith(
        "I couldn't find that")  # a draft was never listed
    assert tell_memory(world, "forget the Zepto search note", said("forget", refs=[f"note:{note}"])) == \
        "Forgot the note “Search appears after the location pop-up closes”."
    assert memory.notes("zeptonow.com") == []


def test_the_chat_lists_at_most_ten_notes():
    listing = {"facts": [], "ids": [], "rules": [], "habits": [], "notes": {"zeptonow.com": 12},
               "notes_list": [{"ref": f"note:{n}", "host": "zeptonow.com", "text": f"Line {n}"} for n in range(12)]}
    shown = conversation.memory_text(listing)
    assert "• zeptonow.com: Line 9" in shown and "Line 10" not in shown
    assert "…and 2 more: `jev memory notes` lists them all." in shown


def test_an_exception_that_escapes_the_worker_is_kept_in_its_trace_errors(world):
    def vanished(agent):
        raise KeyError("tab-1")

    world.scripts["Order Diet Coke"] = [vanished, tell("never")]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "failed")
    assert world.agents["Order Diet Coke"].state["errors"] == [{"step": 0, "error": "KeyError: 'tab-1'"}]
    assert any("I couldn't carry on with" in b for b in bodies(world)) and "never" not in bodies(world)


# ---- Demo flows: a subscription with no site of its own


def test_a_subscription_with_no_site_of_its_own_is_cancelled_at_the_upi_mandate_portal(tmp_path):
    from assistant import skills

    def no_start_job(goal):
        raise AssertionError("the start job isn't needed")

    c = conversation.Core(WebChannel(tmp_path / "media"), memory=store(), start=no_start_job,
                          library={"skills": {}, "sites": skills.load_sites()}, poll=0.01)
    url, title = c.start_page(conversation.Task("t1", "Cancel my Netflix subscription"))
    assert url == "https://www.upihelp.npci.org.in/" and title.startswith("NPCI UPI Help · ")
    url, _ = c.start_page(conversation.Task("t2", "cancel my Netflix autopay"))  # its alias names it too
    assert url == "https://www.upihelp.npci.org.in/"
    url, _ = c.start_page(conversation.Task("t3", "Cancel my Amazon Prime membership"))  # a site of its own
    assert "amazon.in" in url
    for goal in ("cancel my netflix", "Stop my Spotify", "unsubscribe from JioHotstar", "stop paying for YouTube "
                 "Premium", "cancel my ChatGPT"):  # a subscription named by its brand alone
        assert c.start_page(conversation.Task("t4", goal))[0] == "https://www.upihelp.npci.org.in/", goal
    for goal in ("What is my UPI ID?", "show my last 5 UPI payments", "What's the status of my complaints?",
                 "What should I do if I forget my UPI PIN?", "Is the UPI ID abc@okicici safe to pay?"):
        assert c.start_page(conversation.Task("t6", goal))[0] == "https://www.upihelp.npci.org.in/", goal
    c.payees.update(["AISLE", "OPENAI LLC"])  # read off the user's mandate list earlier this session
    assert c.start_page(conversation.Task("t5", "Can you cancel the aisle?"))[0] == "https://www.upihelp.npci.org.in/"


def test_an_experian_dispute_starts_on_its_dispute_page(tmp_path):
    from assistant import skills

    c = conversation.Core(WebChannel(tmp_path / "media"), memory=store(), library={"skills": {}, "sites":
                          skills.load_sites()}, poll=0.01)
    url, _ = c.start_page(conversation.Task("t1", "Raise a dispute on my Experian credit report: the HDFC Bank card "
                                                  "ending 1234 shows ACTIVE but it was closed in March 2025"))
    assert url == "https://consumer.experian.in/ECSINDIA-DCE/view/angular/index.html#!/"


# ---- a brain tool's task: Core.execute runs it to its end and hands its result back

MYUPI = "https://www.upihelp.npci.org.in/"
LIST_GOAL = "List all my UPI AutoPay mandates"


def executing(world, *args, **kwargs):
    """Core.execute on its own thread, as the brain calls it: (thread, the list its result lands in)."""
    out = []
    thread = threading.Thread(target=lambda: out.append(world.core.execute(*args, **kwargs)), daemon=True)
    thread.start()
    return thread, out


def test_a_brain_tasks_result_comes_back_to_the_brain_not_the_chat(world):
    world.scripts[LIST_GOAL] = [
        lambda agent: agent.state.update(page={"cards": ["AISLE"], "card_lines": ["AISLE ₹ 499"]}, outcome=None),
        tell("Your UPI AutoPay mandates:\n• AISLE ₹ 499")]
    result = world.core.execute(LIST_GOAL, url=MYUPI, skill="account")
    assert result == {"status": "done", "outcome": None, "report": "Your UPI AutoPay mandates:\n• AISLE ₹ 499",
                      "cards": [{"name": "AISLE", "details": "AISLE ₹ 499"}]}
    assert not any("AISLE" in b for b in bodies(world))


def test_a_brain_task_on_a_named_site_skips_intake_and_the_start_page(world):
    world.core.intake = lambda *a: pytest.fail("intake read a brain task")
    world.core.start = lambda *a: pytest.fail("the start job read a brain task")
    world.core.execute(LIST_GOAL, url=MYUPI, skill="account")
    task = world.made[LIST_GOAL]
    assert task.url == MYUPI and task.host == "upihelp.npci.org.in" and task.skill == "account"
    assert task.preauth is False and task.title == "upihelp.npci.org.in · " + LIST_GOAL


def test_a_brain_task_with_no_site_starts_like_any_task(world):
    world.hosts["Book a cab home"] = "olacabs.com"
    result = world.core.execute("Book a cab home")
    assert result["status"] == "done" and world.made["Book a cab home"].url == "https://olacabs.com/"


def test_a_brain_tasks_questions_reach_the_chat_as_they_happen(world):
    world.scripts[LIST_GOAL] = [ask(ADDRESS), tell("Done.")]
    thread, out = executing(world, LIST_GOAL, url=MYUPI, skill="account")
    assert until(lambda: interactive(world))
    tap(world, buttons(world)[0])
    thread.join(3)
    assert out[0]["status"] == "done" and world.agents[LIST_GOAL].replies == [{"option": "o1"}]


def test_a_brain_task_the_user_stops_tells_the_brain_how_it_ended(world):
    gate = threading.Event()
    world.scripts[LIST_GOAL] = [hold(gate)]
    thread, out = executing(world, LIST_GOAL, url=MYUPI, skill="account")
    task = until(lambda: world.made.get(LIST_GOAL))
    with world.core.lock:  # as the chat holds it when “stop” arrives
        world.core.cancel(task)
    gate.set()
    thread.join(3)
    assert out[0]["status"] == "cancelled" and out[0]["report"].startswith("Stopped ")
    assert not any(b.startswith("Stopped ") for b in bodies(world))


def test_a_brain_task_that_cannot_start_tells_the_brain_why(world):
    def broken(task, url, asker, notify):
        raise RuntimeError("Chrome is not running")

    world.core.make_worker = broken
    result = world.core.execute(LIST_GOAL, url=MYUPI, skill="account")
    assert result["status"] == "failed" and "Chrome is not running" in result["report"]
    assert not any("Chrome is not running" in b for b in bodies(world))


def test_a_brain_tasks_progress_line_goes_out_before_it_starts(world):
    world.scripts[LIST_GOAL] = [tell("Your UPI AutoPay mandates: none")]
    world.core.execute(LIST_GOAL, url=MYUPI, skill="account", progress="Checking your UPI AutoPay mandates on MyUPI…")
    assert bodies(world) == ["Checking your UPI AutoPay mandates on MyUPI…"]
    assert sent(world)[0]["x_status"] is True  # the chat shows it as a status line, never as a reply


# ---- the real brain on the real core: a scripted model, scripted workers, the whole cancel conversation

def test_the_brain_and_core_hold_a_whole_cancel_conversation(world, tmp_path):
    import json as jsonlib

    from assistant.brain import Brain

    calls = [("upi_mandates", {}),
             ("reply", {"text": "You have 3 AutoPay mandates. Which should I cancel?",
                        "options": ["OPENAI LLC", "GOOGLE PLAY", "AISLE"]}),
             ("upi_cancel", {"payee": "AISLE", "reason": None}),
             ("reply", {"text": "Nothing changed: AISLE's AutoPay stays active.", "options": []})]

    def model(instructions, items, tools, final=False):
        name, args = calls.pop(0)
        return ({"type": "function_call", "call_id": f"c{len(calls)}", "name": name,
                 "arguments": jsonlib.dumps(args)}, {"latency_ms": 1})

    cards = {"cards": ["OPENAI LLC", "GOOGLE PLAY", "AISLE"],
             "card_lines": ["OPENAI LLC ₹ 399", "GOOGLE PLAY ₹ 299", "AISLE ₹ 499"]}
    qr = {"kind": "choice", "text": "To cancel AISLE's UPI AutoPay, scan this QR with your UPI app.",
          "options": [{"id": "o1", "label": "I approved it"}, {"id": "o2", "label": "Not now"}]}
    world.scripts["List all my UPI AutoPay mandates"] = [lambda agent: agent.state.update(page=cards),
                                                          tell("Your UPI AutoPay mandates: …")]
    world.scripts["Cancel the UPI AutoPay mandate whose payee is AISLE"] = [
        ask(qr), lambda agent: agent.state.update(page=cards, outcome="not_approved"),
        tell("Nothing was changed: AISLE's UPI AutoPay stays active.")]
    world.core.brain = Brain(world.core, model=model, traces=tmp_path)
    text(world, "Cancel my subscriptions")
    assert until(lambda: buttons(world) == ["b1:1", "b1:2", "b1:3"] if interactive(world) else False)
    tap(world, "b1:3")
    assert until(lambda: interactive(world) and buttons(world) == ["q1:o1", "q1:o2"])
    tap(world, "q1:o2")
    assert until(lambda: "Nothing changed: AISLE's AutoPay stays active." in bodies(world))
    assert bodies(world)[:1] == ["Checking your UPI AutoPay mandates on MyUPI…"]
    assert "Cancelling AISLE's AutoPay on MyUPI…" in bodies(world)
    assert not any(b.startswith(("Your UPI AutoPay mandates", "Nothing was changed")) for b in bodies(world))
    assert until(lambda: not world.core.brain.busy)


@pytest.mark.parametrize("message", ["1791764938968", " 2824079067 ", "+91 98765 43210"])
def test_a_bare_long_number_never_reaches_a_model(world, message):
    # live: the user's ERN and UTI, typed in the open, went to the planner as answers
    world.brain.starts = False
    text(world, message)
    assert world.classified == [] and "hidden field" in bodies(world)[-1]


def test_a_bare_long_number_never_answers_an_open_question(world):
    world.scripts["Raise a dispute"] = [ask({"kind": "text", "text": "What's the correct address?"})]
    text(world, "Raise a dispute")
    assert until(lambda: world.core.open is not None)
    text(world, "2824079067")
    assert world.agents["Raise a dispute"].replies == [] and "hidden field" in bodies(world)[-1]
    assert world.core.open is not None  # the question is still open for a real answer


def test_a_pincode_or_a_number_in_words_still_goes_through(world):
    world.brain.starts = False
    text(world, "560001")
    text(world, "my order 402-1234567-1234567 hasn't arrived")
    assert world.classified == ["560001", "my order 402-1234567-1234567 hasn't arrived"]
