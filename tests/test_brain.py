"""The brain: one user's conversation agent over the worker's tasks. A scripted model and a fake Core: no paid APIs,
no browser."""

import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from helpers import until

from assistant import brain as brains
from assistant import money, rules
from assistant import skills

MYUPI = "https://www.upihelp.npci.org.in/"
LIST = "List all my UPI AutoPay mandates"
NOW = datetime(2026, 10, 12, 2, 41, tzinfo=ZoneInfo("Asia/Kolkata"))
CARDS = [{"name": "OPENAI LLC", "details": "OPENAI LLC ₹ 399 Active on NAVI APP"},
         {"name": "GOOGLE PLAY", "details": "GOOGLE PLAY ₹ 299 Active on NAVI APP"},
         {"name": "AISLE", "details": "AISLE ₹ 499 Active on Google Pay"}]
CARD = ("ChatGPT bills as OPENAI LLC. Cancel OPENAI LLC (₹ 399)? You'll approve it in your UPI app with your UPI "
        "PIN.")
CHATGPT = ("upi_cancel", {"payee": "OPENAI LLC", "reason": "ChatGPT bills as OPENAI LLC."})


def done(report="", cards=(), outcome=None, status="done"):
    return {"status": status, "outcome": outcome, "report": report, "cards": list(cards)}


class Memory:
    def __init__(self):
        self.forgotten, self.saved = [], []

    def remember(self, kind, text, *, label="", supersede=False, **_):
        if supersede:
            self.saved = [f for f in self.saved if (f["kind"], f["label"]) != (kind, label)]
        self.saved.insert(0, {"kind": kind, "label": label, "text": text})
        return len(self.saved)

    def facts(self, kind=None):
        return [f for f in self.saved if kind is None or f["kind"] == kind]

    def forget(self, ref, actor="user"):
        self.forgotten.append(ref)

    def ids(self):
        return [{"key": "phone", "label": "phone number"},
                {"key": "experian_ern", "label": "Experian Reference Number (ERN)"}]

    def hidden_values(self):
        return [("98765 43210", "[profile:phone]")]


class FakeCore:
    """What the brain uses of Core: hidden model calls, tasks run to their end, replies, the memory flow."""

    def __init__(self, *results):
        self.results, self.executed, self.replies, self.said, self.remembered = list(results), [], [], [], []
        self.memory = Memory()
        self.library = {"skills": {}, "sites": skills.load_sites()}

    def hidden(self, call, *args):
        return call(*args)

    def execute(self, goal, url=None, skill=None, progress=None):
        self.executed.append({"goal": goal, "url": url, "skill": skill, "progress": progress})
        return self.results.pop(0)

    def reply(self, text, options):
        self.replies.append((text, options))

    def say(self, text):
        self.said.append(text)

    def memory_message(self, words):
        self.remembered.append(words)

    def update_typing(self):
        pass


class Model:
    """The model's tool calls in order; each step records what it was shown."""

    def __init__(self, *calls):
        self.calls, self.seen = list(calls), []

    def __call__(self, instructions, items, tools, final=False):
        self.seen.append({"instructions": instructions, "items": json.loads(json.dumps(items)), "final": final,
                          "tools": [t["name"] for t in tools]})
        name, args = self.calls.pop(0)
        arguments = args if isinstance(args, str) else json.dumps(args)
        return ({"type": "function_call", "call_id": f"call_{len(self.seen)}", "name": name, "arguments": arguments},
                {"model": "fake", "latency_ms": 1})


def never(card, reply):
    pytest.fail("Jev was asked")


def make(core, model, tmp_path, confirms=never):
    return brains.Brain(core, model=model, confirms=confirms, clock=lambda: NOW, traces=tmp_path)


def known(brain):
    """The brain, having read the three mandates at 02:03."""
    brain.mandates = {"read_at": "02:03", "list": [{"payee": c["name"], "details": c["details"]} for c in CARDS]}
    return brain


def results(brain, name):
    """The results the brain's calls to one tool got back."""
    calls = {i["call_id"]: i["name"] for i in brain.items if i.get("type") == "function_call"}
    return [json.loads(i["output"]) for i in brain.items
            if i.get("type") == "function_call_output" and calls[i["call_id"]] == name]


def test_the_brain_offers_its_tools_and_reads_its_playbooks(tmp_path):
    model = Model(("reply", {"text": "Hi!", "options": []}))
    make(FakeCore(), model, tmp_path).turn("hi")
    assert model.seen[0]["tools"] == ["upi_mandates", "upi_cancel", "upi_pause", "upi_task", "bureau_details",
                                      "bureau_dispute", "set_cards", "voucher_compare", "buy_voucher",
                                      "pay_bill", "browse", "memory", "reply"]
    assert "UPI AutoPay mandates" in model.seen[0]["instructions"]
    assert "Bills (paying an electricity bill" in model.seen[0]["instructions"]
    assert "Credit report disputes" in model.seen[0]["instructions"]
    assert "Gift vouchers" in model.seen[0]["instructions"]


def test_a_vague_cancel_reads_the_list_then_asks_which_with_the_payees(tmp_path):
    core = FakeCore(done("Your UPI AutoPay mandates: …", CARDS))
    model = Model(("upi_mandates", {}), ("reply", {"text": "You have 3. Which should I cancel?",
                                                   "options": ["OPENAI LLC", "GOOGLE PLAY", "AISLE"]}))
    brain = make(core, model, tmp_path)
    brain.turn("Cancel my subscriptions")
    assert core.executed == [{"goal": LIST, "url": MYUPI, "skill": "account",
                              "progress": "Checking your UPI AutoPay mandates on MyUPI…"}]
    assert results(brain, "upi_mandates") == [{"status": "done", "read_at": "02:41", "mandates": [
        {"payee": c["name"], "details": c["details"]} for c in CARDS]}]
    assert core.replies == [("You have 3. Which should I cancel?", ["OPENAI LLC", "GOOGLE PLAY", "AISLE"])]
    assert "OPENAI LLC ₹ 399" in model.seen[1]["items"][-1]["content"]  # the snapshot now holds the list


def test_a_payee_the_user_named_is_cancelled_then_checked_on_the_list(tmp_path):
    core = FakeCore(done("Done: you approved cancelling AISLE's UPI AutoPay …", CARDS, outcome="approved"),
                    done("Your UPI AutoPay mandates: …", CARDS[:2]))
    model = Model(("upi_cancel", {"payee": "AISLE", "reason": None}), ("reply", {"text": "Done.", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("Cancel my aisle subscription")
    assert [e["goal"] for e in core.executed] == ["Cancel the UPI AutoPay mandate whose payee is AISLE", LIST]
    assert core.executed[0]["progress"] == "Cancelling AISLE's AutoPay on MyUPI…"
    assert results(brain, "upi_cancel")[0] == {
        "status": "approved", "verified": "gone", "report": "Done: you approved cancelling AISLE's UPI AutoPay …",
        "next": "Confirmed on MyUPI: tell the user it's cancelled. Call no other tool."}
    assert [m["payee"] for m in brain.mandates["list"]] == ["OPENAI LLC", "GOOGLE PLAY"]


def test_an_approved_cancel_still_on_the_list_says_so(tmp_path):
    core = FakeCore(done("Done: …", CARDS, outcome="approved"), done("…", CARDS))
    model = Model(("upi_cancel", {"payee": "AISLE", "reason": None}), ("reply", {"text": "…", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("cancel aisle")
    out = results(brain, "upi_cancel")[0]
    assert out["verified"] == "still_listed" and "few minutes" in out["next"]


def test_an_inferred_payee_gets_a_card_written_by_code_and_the_turn_ends(tmp_path):
    core = FakeCore()
    model = Model(CHATGPT)
    brain = known(make(core, model, tmp_path))
    brain.turn("cancel chatgpt")
    assert core.executed == [] and len(model.seen) == 1
    assert core.replies == [(CARD, ["Cancel OPENAI LLC", "No, keep it"])]
    assert results(brain, "upi_cancel")[0]["status"] == "needs_confirmation"


@pytest.mark.parametrize("answer", ["Cancel OPENAI LLC", "yes", "Ok, go ahead", "haan kar do"])
def test_after_the_card_a_tap_or_a_plain_yes_runs_the_cancel(tmp_path, answer):
    core = FakeCore(done("Nothing was changed: OPENAI LLC's UPI AutoPay stays active.", CARDS,
                         outcome="not_approved"))
    model = Model(CHATGPT, CHATGPT, ("reply", {"text": "OK, it stays active.", "options": []}))
    brain = known(make(core, model, tmp_path))
    brain.turn("cancel chatgpt")
    brain.turn(answer)
    assert [e["goal"] for e in core.executed] == ["Cancel the UPI AutoPay mandate whose payee is OPENAI LLC"]
    assert results(brain, "upi_cancel")[-1]["status"] == "not_approved"


def test_a_typed_reply_that_isnt_a_plain_yes_or_no_is_read_by_jev(tmp_path):
    asked = []
    core = FakeCore(done("Nothing was changed: …", CARDS, outcome="not_approved"))
    model = Model(CHATGPT, CHATGPT, ("reply", {"text": "OK.", "options": []}))
    brain = known(make(core, model, tmp_path, confirms=lambda card, reply: asked.append((card, reply)) or "yes"))
    brain.turn("cancel chatgpt")
    brain.turn("chalo kar dete hain")
    assert asked == [(CARD, "chalo kar dete hain")] and len(core.executed) == 1


@pytest.mark.parametrize("answer", ["no", "nahi, keep it"])
def test_a_no_after_the_card_never_runs_the_cancel(tmp_path, answer):
    core = FakeCore()
    model = Model(CHATGPT, CHATGPT, ("reply", {"text": "OK, it stays active.", "options": []}))
    brain = known(make(core, model, tmp_path))
    brain.turn("cancel chatgpt")
    brain.turn(answer)
    assert core.executed == [] and results(brain, "upi_cancel")[-1]["status"] == "rejected"


def test_a_card_counts_only_for_the_next_message(tmp_path):
    core = FakeCore()
    model = Model(CHATGPT, ("reply", {"text": "Your UPI ID is on your home page.", "options": []}), CHATGPT)
    brain = known(make(core, model, tmp_path))
    brain.turn("cancel chatgpt")
    brain.turn("what's my UPI ID?")
    brain.turn("yes")
    assert core.executed == [] and [r[0] for r in core.replies] == [CARD, "Your UPI ID is on your home page.", CARD]


def test_a_payee_off_the_list_is_refused_with_the_list(tmp_path):
    core = FakeCore()
    model = Model(("upi_cancel", {"payee": "NETFLIX", "reason": None}),
                  ("reply", {"text": "No Netflix mandate.", "options": ["OPENAI LLC", "GOOGLE PLAY", "AISLE"]}))
    brain = known(make(core, model, tmp_path))
    brain.turn("cancel netflix")
    out = results(brain, "upi_cancel")[0]
    assert core.executed == [] and out["status"] == "rejected"
    assert [m["payee"] for m in out["mandates"]] == ["OPENAI LLC", "GOOGLE PLAY", "AISLE"]


def test_a_cancel_no_card_is_for_comes_back_not_found_with_the_list(tmp_path):
    core = FakeCore(done("None of your UPI AutoPay mandates is for …", CARDS[:2], outcome="not_found"))
    model = Model(("upi_cancel", {"payee": "AISLE", "reason": None}), ("reply", {"text": "No AISLE.", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("cancel aisle")
    out = results(brain, "upi_cancel")[0]
    assert out["status"] == "not_found" and [m["payee"] for m in out["mandates"]] == ["OPENAI LLC", "GOOGLE PLAY"]


def test_a_task_the_user_stopped_comes_back_stopped(tmp_path):
    core = FakeCore(done("Stopped NPCI UPI Help · List all my UPI AutoPay mandates.", status="cancelled"))
    model = Model(("upi_mandates", {}), ("reply", {"text": "Stopped.", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("what autopays do I have")
    assert results(brain, "upi_mandates")[0]["status"] == "stopped"


def test_the_last_step_may_only_reply(tmp_path):
    core = FakeCore(*[done("Your UPI ID is shown on the home page.") for _ in range(7)])
    model = Model(*[("upi_task", {"goal": f"Show my UPI ID, try {n}"}) for n in range(7)],  # no repeats
                  ("reply", {"text": "Done.", "options": []}))
    make(core, model, tmp_path).turn("what's my UPI ID?")
    assert [s["final"] for s in model.seen] == [False] * 7 + [True]


def test_a_model_failure_ends_the_turn_with_one_plain_line(tmp_path):
    def broken(instructions, items, tools, final=False):
        raise RuntimeError("Model provider returned HTTP 500; no action executed.")

    core = FakeCore()
    brain = make(core, broken, tmp_path)
    brain.turn("Cancel my subscriptions")
    assert core.said == ["I couldn't work that out just now (Model provider returned HTTP 500; no action executed.). "
                         "Please try again."]
    assert brain.items == [{"role": "user", "content": "Cancel my subscriptions"}]


def test_invalid_arguments_come_back_as_a_tool_error(tmp_path):
    model = Model(("upi_task", "{not json"), ("reply", {"text": "Sorry.", "options": []}))
    brain = make(FakeCore(), model, tmp_path)
    brain.turn("what's my UPI ID?")
    assert results(brain, "upi_task") == [{"status": "error", "message": "The arguments weren't valid JSON."}]


def test_the_snapshot_names_saved_ids_but_never_their_values(tmp_path):
    model = Model(("reply", {"text": "Hi!", "options": []}))
    make(FakeCore(), model, tmp_path).turn("hi")
    snapshot = model.seen[0]["items"][-1]
    assert snapshot["role"] == "developer" and "Monday 12 Oct 2026, 02:41 IST" in snapshot["content"]
    assert "phone number" in snapshot["content"] and "Experian Reference Number (ERN)" in snapshot["content"]
    assert "98765" not in snapshot["content"] and "not read yet" in snapshot["content"]


def test_the_model_reads_the_conversation_from_one_of_the_users_messages(tmp_path):
    brain = make(FakeCore(), Model(), tmp_path)
    brain.items = [{"role": "user", "content": f"m{n}"} if n % 3 == 0 else
                   {"type": "function_call_output", "call_id": "x", "output": "{}"} for n in range(100)]
    window = brain.window()
    assert 0 < len(window) <= brains.WINDOW and window[0] == {"role": "user", "content": "m42"}


def test_a_dispute_runs_on_the_bureaus_page_with_an_exact_goal(tmp_path):
    core = FakeCore(done("Dispute raised."))
    model = Model(("bureau_dispute", {"bureau": "Experian", "kind": "Account Details",
                                      "account": "HDFC Bank credit card ending 4321", "wrong": "Amount overdue ₹12,000",
                                      "correct": "₹0", "fresh_ids": False}),
                  ("reply", {"text": "Raised.", "options": []}))
    make(core, model, tmp_path).turn("dispute the HDFC overdue on my Experian report")
    assert core.executed == [{
        "goal": "Raise a dispute on my Experian credit report. Dispute type: Account Details. Account: HDFC Bank credit "
                "card ending 4321. The report shows: Amount overdue ₹12,000. It should be: ₹0.",
        "url": "https://consumer.experian.in/ECSINDIA-DCE/view/angular/index.html#!/", "skill": "account",
        "progress": "Opening Experian's dispute page…"}]


def test_browse_runs_any_other_task_from_its_own_start(tmp_path):
    core = FakeCore(done("Your Ola is booked."))
    model = Model(("browse", {"goal": "Book an Ola to the office"}), ("reply", {"text": "Booked.", "options": []}))
    make(core, model, tmp_path).turn("book an ola to office")
    assert core.executed == [{"goal": "Book an Ola to the office", "url": None, "skill": None,
                              "progress": "On it: Book an Ola to the office"}]


def test_memory_words_go_to_the_memory_flow_which_replies_itself(tmp_path):
    core = FakeCore()
    model = Model(("memory", {"words": "remember I use Navi for UPI"}), ("reply", {"text": "", "options": []}))
    make(core, model, tmp_path).turn("remember I use Navi for UPI")
    assert core.remembered == ["remember I use Navi for UPI"] and core.replies == []


def test_each_turn_writes_one_redacted_trace_line(tmp_path):
    model = Model(("reply", {"text": "Noted.", "options": []}))
    make(FakeCore(), model, tmp_path).turn("my phone is 98765 43210")
    [line] = (tmp_path / "brain-20261012.jsonl").read_text().splitlines()
    trace = json.loads(line)
    assert trace["heard"] == "my phone is [profile:phone]"
    assert [s["tool"] for s in trace["steps"]] == ["reply"] and trace["steps"][0]["status"] == "sent"


def test_messages_heard_are_read_one_at_a_time_in_order(tmp_path):
    gate, heard = threading.Event(), []

    def model(instructions, items, tools, final=False):
        heard.append(next(i["content"] for i in reversed(items) if i.get("role") == "user"))
        gate.wait(3)
        return ({"type": "function_call", "call_id": f"c{len(heard)}", "name": "reply",
                 "arguments": json.dumps({"text": "ok", "options": []})}, {"latency_ms": 1})

    brain = make(FakeCore(), model, tmp_path)
    brain.hear("first")
    brain.hear("second")
    assert until(lambda: heard == ["first"]) and brain.busy
    gate.set()
    assert until(lambda: heard == ["first", "second"]) and until(lambda: not brain.busy)


@pytest.mark.parametrize("account, wrong, correct, missing", [
    ("", "", "", "the account"), ("HDFC Bank card ending 4321", "", "₹0", "what the report shows"),
    ("HDFC Bank card ending 4321", "₹12,000 overdue", " ", "what it should show")])
def test_a_dispute_missing_its_details_is_refused_before_any_page_opens(tmp_path, account, wrong, correct, missing):
    # the eval: “I want to dispute something on my Experian report” raised one with no details at all
    core = FakeCore()
    model = Model(("bureau_dispute", {"bureau": "Experian", "kind": "Account Details", "account": account,
                                      "wrong": wrong, "correct": correct, "fresh_ids": False}), ("reply", {"text": "What's wrong?",
                                                                                         "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("I want to dispute something on my Experian report")
    out = results(brain, "bureau_dispute")[0]
    assert core.executed == [] and out["status"] == "rejected" and missing in out["reason"]


def test_the_same_call_twice_in_a_turn_runs_once(tmp_path):
    # the eval: “what's my UPI ID?” ran the same MyUPI task three times
    core = FakeCore(done("Your UPI ID is jev.user@okaxis."))
    model = Model(("upi_task", {"goal": "Show my UPI ID"}), ("upi_task", {"goal": "Show my UPI ID"}),
                  ("reply", {"text": "Your UPI ID is jev.user@okaxis.", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("what's my UPI ID?")
    assert len(core.executed) == 1 and results(brain, "upi_task")[1]["status"] == "repeat"


def test_a_qr_the_user_didnt_approve_tells_the_brain_to_stop_there(tmp_path):
    # live: after “Not now” the brain cancelled again (the repeat guard caught it) and read the list again
    core = FakeCore(done("Nothing was changed: AISLE's UPI AutoPay stays active.", CARDS, outcome="not_approved"))
    model = Model(("upi_cancel", {"payee": "AISLE", "reason": None}),
                  ("reply", {"text": "Nothing changed: AISLE stays active.", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("AISLE")
    out = results(brain, "upi_cancel")[0]
    assert out["status"] == "not_approved" and "don't retry" in out["next"]


def test_after_a_repeated_call_the_brain_may_only_reply(tmp_path):
    # live: after AISLE's approved cancel the brain asked for the same list five times (each one blocked as a repeat)
    core = FakeCore(done("Your UPI AutoPay mandates: …", CARDS))
    model = Model(("upi_mandates", {}), ("upi_mandates", {}), ("reply", {"text": "Here they are.", "options": []}))
    make(core, model, tmp_path).turn("what autopays do I have")
    assert [s["final"] for s in model.seen] == [False, False, True] and len(core.executed) == 1


def test_a_cancel_confirmed_gone_tells_the_brain_to_say_so_and_stop(tmp_path):
    core = FakeCore(done("Done: you approved cancelling AISLE's UPI AutoPay …", CARDS, outcome="approved"),
                    done("Your UPI AutoPay mandates: …", CARDS[:2]))
    model = Model(("upi_cancel", {"payee": "AISLE", "reason": None}), ("reply", {"text": "Done.", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("cancel aisle")
    out = results(brain, "upi_cancel")[0]
    assert out["verified"] == "gone" and "Call no other tool" in out["next"]


def test_a_dispute_with_a_placeholder_for_a_detail_is_refused(tmp_path):
    # live: the brain filed “It should be: The user's current correct address (not yet provided)”
    core = FakeCore()
    model = Model(("bureau_dispute", {"bureau": "Experian", "kind": "Personal Details", "account": "",
                                      "wrong": "Address shown on the report is incorrect",
                                      "correct": "The user's current correct address (not yet provided)", "fresh_ids": False}),
                  ("reply", {"text": "What's your correct address?", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("my address is wrong on my Experian report")
    out = results(brain, "bureau_dispute")[0]
    assert core.executed == [] and out["status"] == "rejected" and "what it should show" in out["reason"]



def test_a_retry_with_fresh_ids_forgets_the_saved_pair_first(tmp_path):
    # live: “Retry with fresh IDs” typed the saved ERN and UTI again, and Experian refused them again
    core = FakeCore(done("Dispute raised."))
    model = Model(("bureau_dispute", {"bureau": "Experian", "kind": "Personal Details", "account": "",
                                      "wrong": "Address", "correct": "19, Example Lane, Howrah", "fresh_ids": True}),
                  ("reply", {"text": "Raised.", "options": []}))
    make(core, model, tmp_path).turn("Retry with fresh IDs")
    assert core.memory.forgotten == ["id:experian_ern", "id:experian_uti"] and len(core.executed) == 1


def test_a_dispute_reads_the_reports_entries_before_asking_which(tmp_path):
    # live: it asked for “the new address” with three addresses on the report and no word of which one
    core = FakeCore(done("Name: Jev User; Address 1: 12 Old Road; Address 2: 4 Park Street; Address 3: 9 Lake View"))
    model = Model(("bureau_details", {"bureau": "Experian", "kind": "Personal Details", "fresh_ids": False}),
                  ("reply", {"text": "Which address should I correct?",
                             "options": ["12 Old Road", "4 Park Street", "9 Lake View"]}))
    brain = make(core, model, tmp_path)
    brain.turn("Personal details")
    assert core.executed == [{
        "goal": "Show my Personal Details as Experian's dispute page lists them: every row with its current value. "
                "Change nothing.",
        "url": "https://consumer.experian.in/ECSINDIA-DCE/view/angular/index.html#!/", "skill": "account",
        "progress": "Reading your personal details on Experian…"}]
    assert results(brain, "bureau_details")[0]["report"].startswith("Name: Jev User")


def test_a_report_read_once_is_not_read_again_for_the_same_kind(tmp_path):
    # live: “Address 1: Howrah” was tapped and the report was read again: a fresh sign-in each time locked the portal
    core = FakeCore(done("Name: Jev User; Address 1: 12 Old Road; Address 2: 4 Park Street"))
    model = Model(("bureau_details", {"bureau": "Experian", "kind": "Personal Details", "fresh_ids": False}),
                  ("reply", {"text": "Which address?", "options": ["12 Old Road", "4 Park Street"]}),
                  ("bureau_details", {"bureau": "Experian", "kind": "Personal Details", "fresh_ids": False}),
                  ("reply", {"text": "What should it show?", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("Personal details")
    brain.turn("Address 1: 12 Old Road")
    again = results(brain, "bureau_details")[1]
    assert len(core.executed) == 1 and again["status"] == "already_read"
    assert again["report"].startswith("Name: Jev User") and "don't read" in again["next"].lower()


def test_the_report_is_read_only_for_a_kind_the_user_named(tmp_path):
    # the eval: “I want to dispute something on my Experian report” read Account Details, a guess, before asking
    core = FakeCore()
    model = Model(("bureau_details", {"bureau": "Experian", "kind": "Account Details", "fresh_ids": False}),
                  ("reply", {"text": "What's wrong on it?", "options": ["An account", "Personal details"]}))
    brain = make(core, model, tmp_path)
    brain.turn("I want to dispute something on my Experian report")
    out = results(brain, "bureau_details")[0]
    assert core.executed == [] and out["status"] == "rejected" and "which kind" in out["next"]


# ---- gift vouchers: the user's cards asked once, the stores they fit read and ranked in code, the pick bought

class VoucherCore(FakeCore):
    """A Core whose stores' reads are scripted by store name: a dict is what the page showed, a string why it
    couldn't be read; a purchase ends at the user's No."""

    def __init__(self, reads=None, portals_any=True):
        super().__init__()
        self.reads = reads or {}
        if not portals_any:
            self.library["sites"] = {h: n for h, n in self.library["sites"].items() if n.get("banks") != ["any"]}

    def execute(self, goal, url=None, skill=None, progress=None):
        self.executed.append({"goal": goal, "url": url, "skill": skill, "progress": progress})
        intent = money.job_intent(goal)
        if intent and intent["kind"] == "voucher_read":
            got = self.reads.get(intent["portal"], "The store didn't load.")
            if isinstance(got, dict):
                return {**done(f"Read {intent['portal']}.", outcome="read"), "facts": got}
            return {**done(got, outcome="unread", status="blocked"), "facts": {}}
        return {**done("You chose not to pay. Nothing was charged.", outcome="not_approved"), "facts": {}}


def voucher_brain(tmp_path, said=(), cards=(), reads=None, portals_any=True):
    core = VoucherCore(reads, portals_any)
    brain = make(core, Model(), tmp_path)
    brain.items += [{"role": "user", "content": text} for text in said]
    if cards:
        brain.set_cards(list(cards))
    return brain, core


def read_portals(core):
    return sorted(money.job_intent(e["goal"])["portal"] for e in core.executed
                  if money.job_intent(e["goal"])["kind"] == "voucher_read")


def test_cards_are_asked_before_comparing(tmp_path):
    brain, core = voucher_brain(tmp_path, said=["Get me 5000 of Swiggy vouchers"])
    result = brain.voucher_compare("Swiggy", 5000)
    assert result["status"] == "rejected" and "set_cards" in result["next"] and core.executed == []


def test_set_cards_saves_names_only_and_flags_unknown_cards(tmp_path):
    brain, core = voucher_brain(tmp_path, said=["I have HDFC Infinia, an ICICI Sapphiro and a Kotak League"])
    result = brain.set_cards(["HDFC Infinia", "ICICI Sapphiro", "Kotak League"])
    assert result["cards"] == ["Infinia", "Sapphiro", "Kotak League"] and result["unranked"] == ["Kotak League"]
    assert core.memory.facts("preference")[0]["text"] == "Infinia; Sapphiro; Kotak League"
    assert "Your cards: Infinia (HDFC), Sapphiro (ICICI), Kotak League." in brain.snapshot()["content"]


def test_compare_reads_only_the_stores_the_cards_fit_and_ranks_them(tmp_path):
    reads = {"SmartBuy": {"amounts": "₹500 ₹1,000 ₹2,000", "reward": "5X Reward Points"},
             "iShop": {"amounts": "₹1,000 ₹2,000", "reward": "6X Reward Points", "fee": "2.5%"},
             "Woohoo": {"amounts": "₹500 ₹1,000", "reward": "", "discount": "2%"}}
    brain, core = voucher_brain(tmp_path, said=["Get me 5000 of Swiggy vouchers"], cards=["Infinia", "Sapphiro"],
                                reads=reads)
    result = brain.voucher_compare("Swiggy", 5000)
    assert read_portals(core) == ["SmartBuy", "Woohoo", "iShop"]
    best = result["offers"][0]
    assert (best["portal"], best["card"], best["vouchers"], best["effective"]) == (
        "SmartBuy", "Infinia", "2 × ₹2,000 + 1 × ₹1,000", "₹4,167")
    assert [o["portal"] for o in result["offers"]][-1] == "iShop"  # its fee makes it dearest
    again = brain.voucher_compare("Swiggy", 5000)
    assert len(core.executed) == 3 and again["offers"] == result["offers"]  # each store read once a day


def test_a_total_no_store_can_make_offers_the_nearest_totals_as_options(tmp_path):
    # the eval: with the nearest totals only inside each offer, the brain offered the stores, not the totals
    brain, core = voucher_brain(tmp_path, said=["Swiggy 1200"], cards=["Infinia"], reads={
        "SmartBuy": {"amounts": "₹250 ₹500 ₹1,000", "reward": "5X Reward Points"},
        "Woohoo": {"amounts": "₹500 ₹1,000", "discount": "2%"}})
    result = brain.voucher_compare("Swiggy", 1200)
    assert result["status"] == "unmade" and result["options"] == ["₹1,250", "₹1,000", "₹1,500"]
    assert "options" in result["next"]


def test_a_total_one_store_cant_make_lists_its_nearest_after_the_others(tmp_path):
    brain, core = voucher_brain(tmp_path, said=["Swiggy 1500"], cards=["Infinia"], reads={
        "SmartBuy": {"amounts": "₹1,000", "reward": "5X Reward Points"},
        "Woohoo": {"amounts": "₹500 ₹1,000", "discount": "2%"}})
    offers = brain.voucher_compare("Swiggy", 1500)["offers"]
    assert offers[0]["portal"] == "Woohoo" and offers[-1]["vouchers"] is None and offers[-1]["nearest"] == [
        "₹1,000", "₹2,000"]


def test_a_store_that_couldnt_be_read_is_listed_and_still_buyable(tmp_path):
    brain, core = voucher_brain(tmp_path, said=["Swiggy 5000", "ShopWise with Platinum Travel"],
                                cards=["Platinum Travel"],
                                reads={"Amex ShopWise": "Amex ShopWise needs a sign-in to show its prices."})
    result = brain.voucher_compare("Swiggy", 5000)
    assert result["offers"] == [] and result["unread"][0] == {
        "portal": "Amex ShopWise", "why": "Amex ShopWise needs a sign-in to show its prices."}
    brain.buy_voucher("Amex ShopWise", "Swiggy", 5000, "Platinum Travel")
    assert core.executed[-1]["goal"] == ("Buy ₹5,000 of Swiggy vouchers on Amex ShopWise: any vouchers that make "
                                         "₹5,000, paying with Platinum Travel.")


def test_no_store_fits_the_cards_says_so(tmp_path):
    brain, core = voucher_brain(tmp_path, said=["Swiggy 5000"], cards=["Kotak League"], portals_any=False)
    result = brain.voucher_compare("Swiggy", 5000)
    assert result["status"] == "rejected" and "no voucher store" in result["reason"].lower()
    assert core.executed == []


def test_buying_needs_a_compared_offer_and_no_fresh_no(tmp_path):
    brain, core = voucher_brain(tmp_path, said=["Swiggy 5000"], cards=["Infinia"], reads={
        "SmartBuy": {"amounts": "₹1,000 ₹2,000", "reward": "5X Reward Points"}})
    assert brain.buy_voucher("SmartBuy", "Swiggy", 5000, "Infinia")["status"] == "rejected"  # not compared yet
    brain.voucher_compare("Swiggy", 5000)
    brain.items.append({"role": "user", "content": "Not now"})
    assert brain.buy_voucher("SmartBuy", "Swiggy", 5000, "Infinia")["status"] == "rejected"
    brain.items.append({"role": "user", "content": "SmartBuy + Infinia"})
    out = brain.buy_voucher("SmartBuy", "Swiggy", 5000, "Infinia")
    assert core.executed[-1]["goal"] == ("Buy ₹5,000 of Swiggy vouchers on SmartBuy: 2 × ₹2,000 + 1 × ₹1,000, "
                                         "paying with Infinia. Compared: 5X Reward Points.")
    assert core.executed[-1]["skill"] == "vouchers" and out["status"] == "not_approved"


def test_a_brand_the_user_never_named_is_asked_for(tmp_path):
    brain, core = voucher_brain(tmp_path, said=["Get me some vouchers"], cards=["Infinia"])
    result = brain.voucher_compare("Swiggy", 5000)
    assert result["status"] == "rejected" and core.executed == []


def test_a_bought_purchase_says_so_and_never_a_code(tmp_path):
    brain, core = voucher_brain(tmp_path, said=["Swiggy 5000", "SmartBuy + Infinia"], cards=["Infinia"], reads={
        "SmartBuy": {"amounts": "₹1,000 ₹2,000", "reward": "5X Reward Points"}})
    brain.voucher_compare("Swiggy", 5000)
    core.execute = lambda goal, **kw: {**done("Bought ₹5,000 of Swiggy vouchers on SmartBuy (2 × ₹2,000 + 1 × "
                                              "₹1,000). Order GV7Q2M44K1. The codes come by SMS and email.",
                                              outcome="bought"), "facts": {}}
    out = brain.buy_voucher("SmartBuy", "Swiggy", 5000, "Infinia")
    assert out["status"] == "bought" and "GV7Q2M44K1" in out["report"] and "code" in out["next"].lower()


# ---- bills: pay_bill names the bill; the worker's money rules do the rest

def test_a_bill_is_paid_on_the_billers_own_site_with_an_exact_goal(tmp_path):
    core = FakeCore(done("Paid ₹1,240 to BESCOM for account ••7890. Reference BD7H2K99.", outcome="paid"))
    model = Model(("pay_bill", {"biller": "bescom", "amount": None, "method": "UPI", "site": ""}),
                  ("reply", {"text": "Paid.", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("pay my bescom bill by upi")
    goal = "Pay the BESCOM bill for account [profile:bescom_account]: the amount due, by UPI."
    assert core.executed == [{"goal": goal,
                              "url": "https://bescom.co.in/bescom/main/quick-payment", "skill": "bills-recharges",
                              "progress": "Getting your BESCOM bill…"}]
    out = results(brain, "pay_bill")[0]
    assert out["status"] == "paid" and "BD7H2K99" in out["report"]


def test_an_unknown_billers_site_is_asked_for_never_searched(tmp_path):
    core = FakeCore()
    model = Model(("pay_bill", {"biller": "Torrent Power", "amount": None, "method": None, "site": ""}),
                  ("reply", {"text": "What's Torrent Power's website?", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("pay my torrent power bill")
    out = results(brain, "pay_bill")[0]
    assert core.executed == [] and out["status"] == "rejected" and "official website" in out["next"]


def test_a_confirmed_site_for_an_unlisted_biller_is_used_as_given(tmp_path):
    core = FakeCore(done("", outcome="nothing_due"))
    model = Model(("pay_bill", {"biller": "Torrent Power", "amount": 500, "method": None,
                                "site": "connect.torrentpower.com"}),
                  ("reply", {"text": "Nothing due.", "options": []}))
    make(core, model, tmp_path).turn("yes that's their site")
    assert core.executed[0]["url"] == "https://connect.torrentpower.com"
    assert core.executed[0]["goal"] == ("Pay the Torrent Power bill for account [profile:torrent_power_account]: "
                                        "exactly ₹500.")


def test_the_brain_lists_the_billers_it_knows():
    tool = next(t for t in brains.TOOLS if t["name"] == "pay_bill")
    for biller in ("BESCOM", "Adani Electricity", "Tata Power-DDL", "BSES Rajdhani", "MSEDCL"):
        assert biller in tool["description"]
