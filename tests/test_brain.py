"""The brain: one user's conversation agent over the worker's tasks. A scripted model and a fake Core: no paid APIs,
no browser."""

import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from helpers import until

from assistant import brain as brains
from assistant import rules
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
        self.forgotten = []

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
                                      "bureau_dispute", "voucher_options", "voucher_savings", "voucher_checkout",
                                      "browse", "memory", "reply"]
    assert "UPI AutoPay mandates" in model.seen[0]["instructions"]
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


# ---- gift vouchers on a bank's rewards portal: the amounts are read first, then the checkout goes up to the yes

SWIGGY = done("Swiggy Money Voucher on SmartBuy: ₹250, ₹500, ₹1,000, ₹2,000. Up to 5X reward points or 5% "
              "cashback. Monthly limit ₹10,000.")


def test_a_brands_vouchers_are_read_on_the_portal_the_user_named(tmp_path):
    core = FakeCore(SWIGGY)
    model = Model(("voucher_options", {"portal": "SmartBuy", "brand": "Swiggy"}),
                  ("reply", {"text": "Which amount?", "options": ["₹250", "₹500", "₹1,000", "₹2,000"]}))
    brain = make(core, model, tmp_path)
    brain.turn("Buy me a Swiggy voucher from SmartBuy")
    assert core.executed == [{  # no word of buying: the read is a lookup, never an order (live, it was refused)
        "goal": "Show the Swiggy gift vouchers on SmartBuy: list the amounts its page offers and the reward rate it "
                "states, reading the page as it is. Press nothing.",
        "url": "https://www.gyftr.com/instantvouchers/swiggy-gv-gift-vouchers", "skill": "lookup",  # its page, no search
        "progress": "Reading Swiggy vouchers on SmartBuy…"}]
    assert rules.lookup_goal(core.executed[0]["goal"], "lookup")
    assert results(brain, "voucher_options")[0]["report"].startswith("Swiggy Money Voucher on SmartBuy")


@pytest.mark.parametrize("said, missing", [("Buy me a Swiggy voucher", "portal"), ("Buy me a voucher on SmartBuy",
                                                                                     "brand")])
def test_a_portal_or_brand_the_user_never_named_is_asked_for_not_guessed(tmp_path, said, missing):
    core = FakeCore()
    model = Model(("voucher_options", {"portal": "SmartBuy", "brand": "Swiggy"}),
                  ("reply", {"text": "Which one?", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn(said)
    out = results(brain, "voucher_options")[0]
    assert core.executed == [] and out["status"] == "rejected" and f"which {missing}" in out["next"].lower()


def test_a_brands_vouchers_read_once_are_not_read_again(tmp_path):
    core = FakeCore(SWIGGY)
    model = Model(("voucher_options", {"portal": "SmartBuy", "brand": "Swiggy"}),
                  ("reply", {"text": "Which amount?", "options": ["₹250", "₹500"]}),
                  ("voucher_options", {"portal": "SmartBuy", "brand": "Swiggy"}),
                  ("reply", {"text": "Which amount?", "options": ["₹250", "₹500"]}))
    brain = make(core, model, tmp_path)
    brain.turn("Buy me a Swiggy voucher from SmartBuy")
    brain.turn("What were the amounts again?")
    again = results(brain, "voucher_options")[1]
    assert len(core.executed) == 1 and again["status"] == "already_read" and again["report"] == SWIGGY["report"]


def test_a_checkout_needs_the_read_and_an_amount_it_showed(tmp_path):
    core = FakeCore(SWIGGY, done("Ready to pay ₹500.", outcome="not_approved"))
    model = Model(("voucher_checkout", {"portal": "SmartBuy", "brand": "Swiggy", "amount": 500, "quantity": 1}),
                  ("voucher_options", {"portal": "SmartBuy", "brand": "Swiggy"}),
                  ("reply", {"text": "Which amount?", "options": ["₹250", "₹500"]}),
                  ("voucher_checkout", {"portal": "SmartBuy", "brand": "Swiggy", "amount": 300, "quantity": 1}),
                  ("voucher_checkout", {"portal": "SmartBuy", "brand": "Swiggy", "amount": 500, "quantity": 1}),
                  ("reply", {"text": "Stopped before paying. Nothing was charged.", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("Buy me a Swiggy voucher from SmartBuy")
    brain.turn("₹500")
    first, odd, bought = results(brain, "voucher_checkout")
    assert first["status"] == "rejected" and "voucher_options" in first["next"]
    assert odd["status"] == "rejected" and "₹500" in odd["next"]  # the amounts it sells, offered instead
    assert core.executed[-1] == {"goal": "Buy a ₹500 Swiggy gift voucher, quantity 1, on SmartBuy.",
                                 "url": "https://www.gyftr.com/instantvouchers/swiggy-gv-gift-vouchers",
                                 "skill": "shopping",
                                 "progress": "Getting your ₹500 Swiggy voucher ready on SmartBuy…"}
    assert bought["status"] == "not_approved" and "nothing was charged" in bought["next"].lower()
    assert "call no other tool" in bought["next"].lower()


# ---- the user's card: SmartBuy's savings differ by HDFC card, and its calculator assumed Infinia (live, the user has
# another card): the read lists every card's saving on ₹2,000, the user picks theirs, and code scales it to the amount

CARDS_READ = done("Swiggy gift vouchers on SmartBuy: ₹250, ₹500, ₹1,000, ₹2,000. Rewards shown: ACCELERATED REWARDS "
                  "Upto 5X Reward Points / 5% Cashback. Savings on ₹2,000 by card (the site's estimate): Infinia Credit "
                  "Card ₹325 in reward points; Regalia Gold Credit Card / Biz Power Credit Card ₹125 in reward points; "
                  "Regalia Credit Card ₹100 in reward points; Other Credit Cards* ₹100 cashback; Other Debit Cards* ₹100 "
                  "cashback.")


def test_the_users_card_gets_its_own_saving_and_effective_price_in_code(tmp_path):
    core = FakeCore(CARDS_READ)
    model = Model(("voucher_options", {"portal": "SmartBuy", "brand": "Swiggy"}),
                  ("reply", {"text": "Which amount?", "options": ["₹500", "₹2,000"]}),
                  ("voucher_savings", {"portal": "SmartBuy", "brand": "Swiggy", "amount": 500, "card": "Regalia Gold"}),
                  ("reply", {"text": "Get it?", "options": ["Buy it", "Not now"]}))
    brain = make(core, model, tmp_path)
    brain.turn("Buy me a Swiggy voucher from SmartBuy")
    brain.turn("₹500 with my Regalia Gold")
    out = results(brain, "voucher_savings")[0]
    assert out["card"] == "Regalia Gold Credit Card / Biz Power Credit Card" and out["earns"] == "₹31 in reward points"
    assert out["effective_price"] == "₹469" and len(core.executed) == 1  # code, no second read


def test_with_cards_on_the_read_a_checkout_waits_for_the_users_card(tmp_path):
    core = FakeCore(CARDS_READ)
    model = Model(("voucher_options", {"portal": "SmartBuy", "brand": "Swiggy"}),
                  ("voucher_checkout", {"portal": "SmartBuy", "brand": "Swiggy", "amount": 500, "quantity": 1}),
                  ("reply", {"text": "Which card?", "options": ["Regalia Gold", "Infinia"]}))
    brain = make(core, model, tmp_path)
    brain.turn("Buy me a ₹500 Swiggy voucher from SmartBuy")
    out = results(brain, "voucher_checkout")[0]
    assert out["status"] == "rejected" and "which hdfc card" in out["next"].lower() and len(core.executed) == 1


def test_a_card_the_read_doesnt_list_is_offered_the_ones_it_does(tmp_path):
    core = FakeCore(CARDS_READ)
    model = Model(("voucher_options", {"portal": "SmartBuy", "brand": "Swiggy"}),
                  ("voucher_savings", {"portal": "SmartBuy", "brand": "Swiggy", "amount": 500, "card": "Amex Platinum"}),
                  ("reply", {"text": "Which card?", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("Buy me a ₹500 Swiggy voucher from SmartBuy")
    out = results(brain, "voucher_savings")[0]
    assert out["status"] == "rejected" and "Regalia Gold" in out["next"]


def test_the_portal_named_messages_ago_still_counts_after_tool_calls(tmp_path):
    # eval: "from SmartBuy" in the first message fell out of the last 12 conversation items (tool calls fill them),
    # so the card's savings were refused and the portal asked again
    core = FakeCore(CARDS_READ)
    model = Model(("voucher_options", {"portal": "SmartBuy", "brand": "Swiggy"}),
                  ("reply", {"text": "Which amount?", "options": ["₹500"]}),
                  ("reply", {"text": "Which card?", "options": ["Regalia Gold"]}),
                  ("voucher_savings", {"portal": "SmartBuy", "brand": "Swiggy", "amount": 500, "card": "Regalia Gold"}),
                  ("voucher_savings", {"portal": "SmartBuy", "brand": "Swiggy", "amount": 500, "card": "Regalia"}),
                  ("voucher_savings", {"portal": "SmartBuy", "brand": "Swiggy", "amount": 500, "card": "Infinia"}),
                  ("reply", {"text": "Get it?", "options": ["Buy it"]}))
    brain = make(core, model, tmp_path)
    brain.turn("Buy me a Swiggy voucher from SmartBuy")
    brain.turn("₹500")
    for n in range(8):  # a longer conversation's tool calls in between
        brain.items += [{"type": "function_call", "call_id": f"pad{n}", "name": "memory", "arguments": "{}"},
                        {"type": "function_call_output", "call_id": f"pad{n}", "output": "{}"}]
    brain.turn("Regalia Gold")
    assert results(brain, "voucher_savings")[0]["status"] == "done"


@pytest.mark.parametrize("said", ["Not now", "no", "later", "nahi"])
def test_a_checkout_after_a_decline_is_refused(tmp_path, said):
    # dry run: "Not now" to "Get it?" started the checkout in one of two runs with the real model
    core = FakeCore(CARDS_READ)
    model = Model(("voucher_options", {"portal": "SmartBuy", "brand": "Swiggy"}),
                  ("voucher_savings", {"portal": "SmartBuy", "brand": "Swiggy", "amount": 500, "card": "Regalia Gold"}),
                  ("reply", {"text": "Get it?", "options": ["Buy it", "Not now"]}),
                  ("voucher_checkout", {"portal": "SmartBuy", "brand": "Swiggy", "amount": 500, "quantity": 1}),
                  ("reply", {"text": "OK, not bought.", "options": []}))
    brain = make(core, model, tmp_path)
    brain.turn("Buy me a ₹500 Swiggy voucher from SmartBuy with my Regalia Gold")
    brain.turn(said)
    out = results(brain, "voucher_checkout")[0]
    assert out["status"] == "rejected" and "nothing" in out["next"].lower() and len(core.executed) == 1
