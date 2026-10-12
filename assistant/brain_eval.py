"""Brain evals: the demo's phrasings against the real brain model, with Core faked so no browser opens and nothing is
cancelled. Paid (a few cents a run) and run by hand before a demo, never by pytest:

    uv run python -m assistant.brain_eval
"""

import re
import sys
import tempfile
import time

from . import skills
from .brain import LIST, Brain

CARDS = [{"name": "OPENAI LLC", "details": "OPENAI LLC ₹ 399 Active on NAVI APP Frequency Custom"},
         {"name": "GOOGLE PLAY", "details": "GOOGLE PLAY ₹ 299 Active on NAVI APP Frequency Monthly"},
         {"name": "AISLE", "details": "AISLE ₹ 499 Active on Google Pay Frequency Monthly"}]
PAYEES = [c["name"] for c in CARDS]
HINGLISH = re.compile(r"\b(?:aap|aapke|kaunsa|karna|karein|hai|kya|nahi)\b", re.I)  # the live run: English in, Hinglish out
CHANGE = re.compile(r"(Cancel|Pause) the UPI AutoPay mandate whose payee is (.+?)(?: until .+)?$")


def done(report, cards=(), outcome=None):
    return {"status": "done", "outcome": outcome, "report": report, "cards": list(cards)}


class Memory:
    def ids(self):
        return [{"key": "phone", "label": "phone number"}]

    def hidden_values(self):
        return []


class Tools:
    """Core, faked: each task answers from these fixtures (a QR is always answered “Not now”); replies are kept."""

    def __init__(self):
        self.memory, self.library = Memory(), {"skills": {}, "sites": skills.load_sites()}
        self.executed, self.replies, self.said = [], [], []

    def hidden(self, call, *args):
        return call(*args)

    def update_typing(self):
        pass

    def say(self, text):
        self.said.append(text)

    def reply(self, text, options):
        self.replies.append((text, options))

    def memory_message(self, words):
        self.said.append(f"(memory) {words}")

    def execute(self, goal, url=None, skill=None, progress=None):
        self.executed.append(goal)
        if goal == LIST:
            return done("Your UPI AutoPay mandates: " + "; ".join(c["details"] for c in CARDS), CARDS)
        change = CHANGE.match(goal)
        if change and change[2] not in PAYEES:
            return done("None of your UPI AutoPay mandates is for that, so nothing was changed.", CARDS, "not_found")
        if change and change[1] == "Cancel":
            return done(f"Nothing was changed: {change[2]}'s UPI AutoPay stays active.", CARDS, "not_approved")
        if change:
            return done(f"Paused {change[2]}'s UPI AutoPay; the user approved it in their UPI app.", CARDS)
        if goal.startswith("Show my Personal Details"):
            return done("Name: Jev User; Date of Birth: 01-01-1990; Address 1: 12 Old Road, Howrah; Address 2: 4 Park "
                        "Street, Kolkata; Address 3: 9 Lake View, Howrah")
        if "credit report" in goal:
            return done("Dispute raised with Experian. Reference EXP-2026-1012.")
        if goal.startswith("Show the Swiggy gift vouchers on SmartBuy"):
            return done("Swiggy gift vouchers on SmartBuy: ₹250, ₹500, ₹1,000, ₹2,000. Rewards shown: ACCELERATED "
                        "REWARDS Upto 5X Reward Points / 5% Cashback. Monthly Limit: ₹10,000. Savings on ₹2,000 by card "
                        "(the site's estimate): Infinia Credit Card ₹325 in reward points; Diners Black / Biz Black "
                        "Credit Card ₹195 in reward points; Regalia Gold Credit Card / Biz Power Credit Card ₹125 in "
                        "reward points; Regalia Credit Card ₹100 in reward points; Other Credit Cards* ₹100 cashback; "
                        "Other Debit Cards* ₹100 cashback.")
        if goal.startswith("Show the Swiggy gift vouchers on iShop"):
            return done("Swiggy Money E-Voucher on iShop: ₹1,000, ₹2,000. Earns 3% iCash.")
        if goal.startswith("Buy a ₹"):  # the demo's No at the approval card
            return done("You chose not to pay. Nothing was charged.", outcome="not_approved")
        return done("Your UPI ID is jev.user@okaxis.")

    def changed(self, verb):
        return [m[2] for m in map(CHANGE.match, self.executed) if m and m[1] == verb]


def option_payee(option):
    return next((p for p in PAYEES if p.lower() in option.lower()), None)


def known(brain):
    brain.learn(done("", CARDS))
    return brain


def last(tools):
    return tools.replies[-1] if tools.replies else ("", [])


CASES = [
    ("A vague cancel reads the list and asks which, in English", ["Cancel my subscriptions"], False,
     lambda t: t.executed == [LIST] and not t.changed("Cancel") and len(last(t)[1]) >= 2
     and not HINGLISH.search(last(t)[0])),
    ("A named payee is cancelled at once", ["Cancel my aisle subscription"], False,
     lambda t: t.changed("Cancel") == ["AISLE"] and CHANGE.match(t.executed[-1])),  # nothing read after “Not now”
    ("A brand billed by a company gets the card", ["cancel chatgpt"], True,
     lambda t: not t.changed("Cancel") and "Cancel OPENAI LLC" in last(t)[1]),
    ("The card's yes runs the cancel", ["cancel chatgpt", "haan kar do"], True,
     lambda t: t.changed("Cancel") == ["OPENAI LLC"]),
    ("Nothing matching never cancels and offers the list", ["cancel my netflix subscription"], True,
     lambda t: not t.changed("Cancel") and len(last(t)[1]) >= 2),
    ("What AutoPays: every payee named", ["What AutoPays do I have?"], False,
     lambda t: LIST in t.executed and all(p.lower() in last(t)[0].lower() for p in PAYEES)),
    ("“The second one” after the list", ["Cancel my subscriptions", "the second one"], False,
     lambda t: len(t.replies) >= 2 and option_payee(t.replies[0][1][1]) in (
         t.changed("Cancel") or [option_payee(o) for o in last(t)[1] if o.lower().startswith("cancel")])),
    ("Pause with a date", ["pause google play till the end of this month"], True,
     lambda t: t.changed("Pause") == ["GOOGLE PLAY"]),
    ("UPI ID goes to MyUPI", ["what's my UPI ID?"], False,
     lambda t: any(g != LIST and not CHANGE.match(g) for g in t.executed) and "jev.user@okaxis" in last(t)[0]),
    ("An Experian dispute starts by asking what's wrong", ["I want to dispute something on my Experian report"],
     False, lambda t: t.executed == [] and bool(last(t)[0])),
    ("A wrong address is read off the report, then asked which", ["I want to dispute something on my Experian report",
                                                                  "Personal details", "my address is wrong"], False,
     lambda t: any(g.startswith("Show my Personal Details") for g in t.executed)
     and not any("credit report" in g for g in t.executed) and len(last(t)[1]) >= 2),
    ("Thanks needs no tool", ["thanks!"], True, lambda t: t.executed == [] and bool(last(t)[0])),
    ("A voucher with no portal asks which portal", ["Buy me a Swiggy voucher"], False,
     lambda t: t.executed == [] and any("smartbuy" in o.lower().replace(" ", "") for o in last(t)[1])
     and any("ishop" in o.lower().replace(" ", "") for o in last(t)[1])),
    ("The portal picked, its amounts are read and offered", ["Buy me a Swiggy voucher", "SmartBuy"], False,
     lambda t: [g for g in t.executed if g.startswith("Show the Swiggy")] and not any(g.startswith("Buy a") for g in
                                                                                       t.executed)
     and any("500" in o for o in last(t)[1])),
    ("An amount asks for the user's HDFC card, never assuming one", ["Buy me a Swiggy voucher from SmartBuy", "₹500"],
     False, lambda t: not any(g.startswith("Buy a") for g in t.executed)
     and any("regalia" in o.lower() for o in last(t)[1])),
    ("The card's saving and effective price, then checkout, and its No ends it",
     ["Buy me a Swiggy voucher from SmartBuy", "₹500", "Regalia Gold", "Buy it"], False,
     lambda t: any("469" in text for text, _ in t.replies)
     and "Buy a ₹500 Swiggy gift voucher, quantity 1, on SmartBuy." in t.executed
     and re.search(r"nothing was charged|not charged|didn't pay|stopped", last(t)[0], re.I)),
]


def main():
    from .server import load_environment

    load_environment()
    traces, failed = tempfile.mkdtemp(prefix="brain-eval-"), 0
    for name, turns, preset, check in CASES:
        tools, started = Tools(), time.perf_counter()
        brain = Brain(tools, traces=traces)
        if preset:
            known(brain)
        for message in turns:
            brain.turn(message)
        ok = bool(check(tools))
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.perf_counter() - started:.1f} s)")
        print(f"      tasks: {tools.executed}")
        for text, options in tools.replies:
            print(f"      reply: {text[:160]!r} {options}")
        for text in tools.said:
            print(f"      said:  {text[:160]!r}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed. Traces: {traces}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
