"""One task's runtime: its tab, its state, and the loop that joins System One (executor.py: Jev chooses each step)
and System Two (planner.py: mini plans, resolves what Jev is unsure about, and writes). The gate (gate.py) guards
every commit. The user is asked only for what they alone can give, and no model ever sees a secret."""

import html
import itertools
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from . import executor, gate, jev, learn, planner, replay, rules, skills, views
from .browser import Browser, ExecutionUncertain, StalePage, TabsClosed, unique
from .errors import Halt, Retry
from .memory import HOME, KEY, LABELS, Memory, StoreError, Unstorable
from .model import METER, action_space, new_meter, spent
from .privacy import SECRETS, Secrets, storable, variants

STOPPED = {"done", "blocked", "failed", "cancelled"}
PRIVATE = {"browser", "answers", "ids"}  # hold secret values
MAX_ACTIONS = 150
MAX_MINUTES = 30
MAX_SPEND = 0.25
SUBGOAL_STEPS = 12
STUCK_AT = 8
STUCK = "I'm stuck on"  # how stuck() begins its question: a bench run stops there rather than answer it
IDLE_HALT = 16  # ticks in a row with no browser step and no question to the user: ask at STUCK_AT, stop here
CODE_ASKS = 3
COMMIT_DROPS = 3  # an order's replan with no commit (none left in the old plan either): refused this often, then taken
CAPTCHA_ROUNDS = 8  # a captcha can take several rounds of new images
QR_TRIES = 5  # seconds a mandate's QR may take to render after its Cancel
LIST_READS, LIST_GAP = 6, 0.7  # MyUPI draws its mandate list a card at a time, over a few seconds
CART_WAIT = 1.5  # seconds for a cart button's answer (SmartBuy's Add to cart, from its server) to show
CART_READS = 6  # reads, CART_WAIT apart, for a cart line's count to change after its stepper (live: over 1.5 s)
CHOSEN = re.compile(r"(Cancel|Pause) the UPI AutoPay mandate whose payee is (.+?)(?: until .+)?$")  # the brain's words
VOUCHER_READ = re.compile(r"^Show the (.+?) gift vouchers on (.+?): list the amounts")  # the brain's voucher read
REWARD_LINE = re.compile(r"reward points|cashback|icash|instant discount", re.IGNORECASE)
VOUCHER_BUY = re.compile(r"^Buy ₹([\d,]+) of .+? gift vouchers on .+?: (\d+ × ₹[\d,]+(?: \+ \d+ × ₹[\d,]+)*)")  # its
# checkout goal: "Buy ₹5,000 of Swiggy gift vouchers on SmartBuy: 2 × ₹2,000 + 1 × ₹1,000, paying with …"
PAYABLE_AMOUNT = re.compile(r"payable amount\s*:?\s*₹\s?([\d,]+(?:\.\d+)?)", re.IGNORECASE)  # GyFTR's pay page
ORDER_ID = re.compile(r"\border\s*(?:id|no\.?|number)\s*:?\s*([A-Z0-9][A-Z0-9-]{5,})", re.IGNORECASE)
CART_LINE = re.compile(r"Voucher Value ([\d,]+).{0,20}?You Pay ₹ ?([\d,]+)")  # a SmartBuy cart line: value, its cost
VOUCHER_CARD = re.compile(r", paying with (.+?)\.$")  # the card the user named, on a portal that lists savings by card
ROW_FIX = re.compile(r"Dispute type: Personal Details\. The report shows: ([^:]+): .+?\. It should be: (.+?)\.?\s*$",
                     re.DOTALL)  # the brain's dispute: the row it names, and that row's correct value
MANDATE_CANCEL = re.compile(r"(?:cancel|revoke)\b", re.IGNORECASE)  # a card's Cancel: the UPI app does the rest
APPROVED = re.compile(r"\s*(?:yes|done|approved|i approved|i did|ok)\b", re.IGNORECASE)
AGREE = re.compile(r"\b(?:i (?:have read and )?agree|i accept)\b", re.IGNORECASE)  # a terms box
LOADING = re.compile(r"\bloading\b", re.IGNORECASE)  # a part of the page not drawn yet
PROOF = re.compile(r"\b(?:complaint|dispute|proof|screenshot|receipt|document|attach)", re.IGNORECASE)
PHONE_BOX = re.compile(r"mobile|phone|\b9876543210\b", re.IGNORECASE)  # a sign-in form's phone number box
SUBMIT = re.compile(r"\s*(?:continue|proceed|log ?in|sign ?in|get otp|verify(?: (?:&|and) continue)?)\s*$",
                    re.IGNORECASE)  # a sign-in form's own button
QUESTION = re.compile(r"\s*(?:what|how|why|when|where|which|who|whom|is|are|can|could|do|does|did|should|will)\b|"
                      r".*\?\s*$", re.IGNORECASE)
POLICY = re.compile(r"\s*(?:terms(?: of use| (?:&|and) conditions)?|privacy(?: policy)?|disclaimer|faqs?)\b",
                    re.IGNORECASE)  # a site's own policy or help pages
QUIET = executor.QUIET  # waits and scrolls: repeating these is not going in circles…
SCROLLS = 8  # …until this many scrolls in a row on one page: then scrolling has stopped finding anything
CIRCLE_AT = 3  # one action run this often from the same page (its path and controls) is not run from it again…
CIRCLE_ASK = 5  # …and once its runs and the planner's refused tries reach this, the user decides
LOOP_ASKS = 2  # questions about one loop: the next time it comes round, the task stops
VISITS = 6  # steps from one page state (views.signature), however far apart: the user is asked
UNDOS = 3  # a step undone and done again (A→B→A) this often: the user is asked
PATH_ASK = 30  # steps on one page address (host and path): the user is asked once…
PATH_HALT = 45  # …and here the task stops; an answer in between never resets the count
FRUITLESS = 6  # steps in a row Jev judged failed on one page: the planner hears it, once a streak
BLANK_WAITS = 3  # reads of a blank page before the planner hears it is blank
FILE_REFUSALS = 3  # a download's DONE refused this often with no file saved: the next one stops the task
REFUSALS_KEPT = 60  # refusals the trace keeps (the latest)
DOWNLOAD_WAIT = 5.0  # seconds a DONE waits for downloads still in flight
TYPED = {"TYPE_TEXT", "OPEN_URL"}  # what these typed or opened is part of the action
CONTAINERS = {"", "generic", "group", "region", "main", "document", "presentation", "none", "section", "article",
              "application"}
TALKING = {"ASK", "TELL", "DONE", "STOP"}
TRACE_STEP = ("step", "subgoal", "operation", "action", "title", "text", "key", "files", "page_changed", "url",
              "changed", "effect", "committed", "approved", "elapsed_ms", "unconfirmed", "spot", "typed_ok")
PLACEHOLDER = re.compile(r"\[(profile|secret):([\w.]+)\]")
CODE_WORDS = re.compile(r"\b(otp|passcode|password|cvv|pin(?![\s-]?code)|"  # a PIN code or promo code is no secret
                        r"(?<!pin )(?<!pin-)(?<!promo )(?<!zip )(?<!postal )code)\b", re.IGNORECASE)
DELEGATE = re.compile(r"\b(you (choose|decide|pick)|whatever (you|fits|works|is)|any (one|of them|is fine|will do)|"
                      r"up to you|your (choice|call)|doesn'?t matter)\b", re.IGNORECASE)
PLACE = re.compile(r"\b(address|deliver(y)? (to|location|address)|where (should|do you want|to deliver|are you)|"
                   r"pick ?-?up (location|point|address|from)|drop(-| )?off|drop location)\b", re.IGNORECASE)
FILLER = {"a", "an", "the", "and", "or", "to", "of", "for", "in", "on", "at", "by", "with", "from", "into", "as", "is",
          "it", "its", "if", "then", "that", "this", "s", "your", "my", "user", "users"}
PROFILE_ASKS = {"phone": re.compile(r"\b(mobile|phone|contact) number\b", re.IGNORECASE),
                "email": re.compile(r"\b(your e-?mail|e-?mail (address|id))\b", re.IGNORECASE),
                "name": re.compile(r"\b(your (full )?name|full name)\b", re.IGNORECASE),
                "upi_id": re.compile(r"\bupi id\b", re.IGNORECASE),
                # live: “Please provide a fresh report's ERN and UTI” went out in the open
                "experian_ern": re.compile(r"\b(ERN|experian reference number)\b", re.IGNORECASE),
                "experian_uti": re.compile(r"\b(UTI|unique transaction id)\b", re.IGNORECASE),
                # iShop signs in through ICICI net banking: its User ID is an ID, never asked in the open
                "icici_user_id": re.compile(r"\b(icici\b.{0,30}\buser ?id|net ?banking user ?id)\b", re.IGNORECASE)}
# A question that asks how to pay: "How would you like to pay?", "Which payment method…?", "What will you pay with?".
# A yes/no about going on to pay ("Shall I proceed to payment?") or "how much" asks no such thing.
HOW_TO_PAY = re.compile(r"\bhow\b(?!\s+(?:much|many|long|soon|often)\b)(?:\s+[\w']+){0,6}?\s+pay(?:ing|ment)?\b|"
                        r"\b(?:which|what)\b(?:\s+[\w']+){0,2}?\s+(?:payment|pay(?:ing)?\s+(?:method|option|mode)s?)\b|"
                        r"\b(?:which|what)\b(?:\s+[\w']+){0,4}?\s+pay\s+(?:with|by|using|through|via)\b",
                        re.IGNORECASE)
# A whole reply that leaves an offered choice to the agent: it opens with "any", "either", "whichever", or "both are
# fine" ("Any two seats together", "either is fine"); a reply that only mentions one ("I'd take Prime over any other")
# names its own pick, and one with a negation ("not any of these", "I don't like either") turns the options down.
ANY_OF = re.compile(r"^\W*(?:(?:ok(?:ay)?|sure|yes|yeah|fine|just|hmm+|um+|uh+)\W+)*"
                    r"(?:any|either|whichever|both (?:are )?(?:fine|ok(?:ay)?|good))\b", re.IGNORECASE)
NEGATION = re.compile(r"\b(?:no|not|none|neither|nor|never|nothing|nope|nah|dont|doesnt|didnt|cant|wont|isnt|"
                      r"arent)\b|\b\w+n['’]t\b", re.IGNORECASE)  # "front" or "want" is none
HERE = re.compile(r"\b(current|present|my) location\b|\bwhere i am\b|\blocate me\b", re.IGNORECASE)
SEARCHY = re.compile(r"\bsearch", re.IGNORECASE)  # a search control or box
SLOT_ROW = re.compile(r"\(in “([^”]+)”\)")  # a time slot's row title, as the snapshot adds it
CHOOSES = re.compile(r"\b(colou?rs?|sizes?|variants?|storage|flavou?rs?|shades?|styles?|models?)\b",
                     re.IGNORECASE)  # a step that picks one version of an item
STRIP = re.compile(r"(?<!\d)(?:\d{1,2}\s+){3,}\d{1,2}(?!\d)")  # a strip of choices: “1 2 3 4 5 6 7 8 9 10”
RAISES = re.compile(r"\b(increase|increment)\b", re.IGNORECASE)  # a stepper's +: "Increase quantity"
PLACE_STEP = re.compile(r"\b(location|address|pin ?code|deliver(y|ing)? (to|area|place)|pick ?-?up (point|place)|"
                        r"drop(-| )?off)\b", re.IGNORECASE)  # a plan step that sets the delivery or pickup place
DOWNLOADS = re.compile(r"download|invoice|receipt|statement|\bpdf\b|\.pdf\b|\bprint\b|\bexport\b",
                       re.IGNORECASE)  # a step that may have opened the document
PICK_METHOD = ("ASK which payment method first, listing every method the page shows; a pre-selected saved card is "
               "not the user's pick.")
CLEARING = ("TYPE needs the text to type. A field is never cleared first: typing replaces what it holds. PICK the "
            "field to only focus it.")


def delegates(answer):
    """Whether a reply leaves the choice to the agent ("you choose", "doesn't matter") and turns nothing down: "any
    one, but not the window" names what the user won't take."""
    return bool(DELEGATE.search(answer)) and not NEGATION.search(DELEGATE.sub(" ", answer))


def secret_field(action):
    """Whether only the user's hidden answer may go into this field: a code or password, or a card detail. A button
    that merely mentions card details is not a field."""
    return rules.secret_field(action) or (action.get("kind") == "fill"
                                          and bool(rules.CARD_DETAIL.search(action.get("label") or "")))


def trivial(value):
    """A hidden reply too plain to redact: letters and spaces only, or under 4 characters that are not all digits.
    A CVV or code (digits) is always registered; the registry never matches it inside a larger number."""
    value = str(value or "").strip()
    return (len(value) < 4 and not value.isdigit()) or bool(re.fullmatch(r"[^\W\d_]+(?:[\s'-]+[^\W\d_]+)*", value))


def code_like(answer):
    """Whether a hidden reply is a code the site sent (letters and digits, at least one digit, spaces and dashes
    aside), not words such as "it never came"."""
    return bool(re.fullmatch(r"(?=\D*\d)[^\W_]{3,12}", re.sub(r"[\s-]", "", str(answer or ""))))


def picked(answer, options):
    """The option a hidden reply names by code alone (its number, or its words exactly), else None."""
    text = answer.strip().rstrip(".").casefold()
    if re.fullmatch(r"[0-9]{1,2}", text) and 1 <= int(text) <= len(options):
        return int(text)
    return next((n for n, option in enumerate(options, 1) if option.strip().casefold() == text), None)


def has_commit(plan):
    return any(g["kind"] == "commit" for g in plan)


def step_words(subgoal):
    return " ".join(subgoal["do"].lower().split())


def gist(text):
    return set(re.findall(r"[a-z0-9]+", text.lower())) - FILLER


def shares(a, b, by=min):
    """Whether two subgoals' words overlap by at least half: of the shorter one's (by=min, "one is mostly the other"),
    or of the longer one's (by=max, "they are the same step")."""
    x, y = gist(a), gist(b)
    return bool(x and y) and len(x & y) / by(len(x), len(y)) >= 0.5


def blank(page):
    """Whether a page shows nothing to decide on: no text, and no control but at most one empty container (an app
    still loading, a passkey prompt drawn outside the page)."""
    if page.get("text", "").strip() or page.get("dialog"):
        return False
    elements, _, _ = action_space(page["actions"])
    return len(elements) <= 1 and all(e.get("role", "") in CONTAINERS or not e["label"].strip() for e in elements)


def unchanged(subgoals, rest):
    """Whether a replan is the rest of the plan sent again: the same steps (their words and kind) with the same
    values, in order. A new value (an address the user just gave) is a change."""
    def shape(plan):
        return [(step_words(g), g["kind"], [(v["field"].strip(), str(v["text"]).strip()) for v in g["values"]])
                for g in plan]

    return bool(subgoals) and shape(subgoals) == shape(rest)


def with_commit(subgoals, rest):
    """A replan that left the commit out, repaired: the new subgoals, then the old plan's `rest` from its first commit
    subgoal on (the commit, the report). The new plan's closing reports move after the commit, or go when the old tail
    has its own; a step written in both is kept once, and as the commit when the old tail has it as one. Each act or
    ask step the old plan still had before the commit is judged on its own: one that no new subgoal rewrites (sharing
    at least half the words of the longer of the two) was dropped rather than replaced, and comes back, in its old
    order, before the commit. A new question that shares a word or two with one old step never takes the others. Steps
    before the last old one the new subgoals rewrite are ones the planner moved past: they never come back after the
    new ones (a search restored after "open the product" sends Jev round)."""
    first = next(i for i, g in enumerate(rest) if g["kind"] == "commit")
    tail = rest[first:]
    rewritten = [i for i, g in enumerate(rest[:first])
                 if any(shares(new["do"], g["do"], by=max) for new in subgoals)]
    start = rewritten[-1] + 1 if rewritten else 0
    middle = [g for g in rest[start:first] if g["kind"] in {"act", "ask"}
              and not any(shares(new["do"], g["do"], by=max) for new in subgoals)]
    end = len(subgoals)
    while end and subgoals[end - 1]["kind"] == "report":
        end -= 1
    if not any(g["kind"] == "report" for g in tail):
        tail = tail + subgoals[end:]
    commits = {step_words(g) for g in tail if g["kind"] == "commit"}
    head = [g for g in subgoals[:end] if step_words(g) not in commits] + middle
    have = {step_words(g) for g in head}
    return head + [g for g in tail if g["kind"] == "commit" or step_words(g) not in have]


def asks(goal):
    """Whether the goal is a question ("What did I order last on Swiggy?"): a lookup, even when it names an order."""
    return bool(planner.OPEN_Q.match(goal or "")) or (goal or "").rstrip().endswith("?")


def voucher_order(goal):
    """The vouchers the brain's checkout goal names, by value, largest first ({2000: 2, 1000: 1}), else None."""
    wanted = VOUCHER_BUY.match(goal)
    return {int(value.replace(",", "")): int(n) for n, value in re.findall(r"(\d+) × ₹([\d,]+)", wanted[2])} \
        if wanted else None


def cart_lines(text):
    """A voucher cart's lines as (value, count), in page order: "Voucher Value 2,000 … You Pay ₹ 4,000" is two."""
    found = [(int(value.replace(",", "")), int(paid.replace(",", "")))
             for value, paid in CART_LINE.findall(re.sub(r"\s+", " ", text))]
    return [(value, round(paid / value)) for value, paid in found if value]


def plain(text):
    """Text as the user should read it: entities unescaped until none are left (some sites escape twice)."""
    while (unescaped := html.unescape(text)) != text:
        text = unescaped
    return text


def repeats(message, earlier):
    """Whether `message` says again what `earlier` said: most of the shorter one's words are in both."""
    said, before = (set(re.findall(r"[a-z0-9]+", t.lower())) for t in (message, earlier))
    return bool(said and before) and len(said & before) / min(len(said), len(before)) >= 0.6


class Worker:
    interrupted = None  # set by the chat: true once the user cancelled or changed the task during a step
    trace_dir = HOME / "traces"
    steady_ms = 3000  # a page still changing this soon after a step is read again before the user is asked
    processing_ms = 300000  # how long to watch a commit the site is still working on
    outcome_pause = 5.0  # seconds between looks at that commit
    quiet_wait = 1.5  # seconds to keep looking when a step seems to have changed nothing
    blank_pause = 1.0  # seconds before a blank page is read again
    suggest_wait = 2.5  # seconds a typed place or search waits for its suggestions to come
    bench = False  # a bench run: a pre-authorisation is logged, never used

    def __init__(self, url, goal, *, asker=None, notify=None, memory=None, habit=None, sameness=None, browser=None,
                 title=None, skill=None, preauth=False, cap=None, library=None, trace_dir=None, provide_file=None,
                 owner=None):
        goal = goal.strip() if isinstance(goal, str) else ""
        if not goal:
            raise ValueError("Supply a task")
        self.asker, self.notify, self.provide_file = asker, notify, provide_file
        self.memory = memory if memory is not None else Memory.open()
        self.secrets = Secrets(self.memory.hidden_values())
        self.library = library if library is not None else {"skills": skills.load(), "sites": skills.load_sites()}
        if trace_dir:
            self.trace_dir = Path(trace_dir)
        token = SECRETS.set(self.secrets)
        try:
            keep = (skills.site_for(url, self.library["sites"]) or {}).get("keep")
            self.browser = browser or Browser(url, geolocation=self.memory.geolocation(), owner=owner,
                                              stay=keep == "page")  # Experian: its sign-in lives in its page
            try:
                page = self.clean(self.browser.observe())
                home = urlparse(url)._replace(path="/", params="", query="", fragment="").geturl()
                if url != home and rules.error_page(page):  # a guessed deep link: start from the home page
                    self.browser.navigate(home)
                    page = self.clean(self.browser.observe())
            except Exception:
                self.browser.close()
                raise
        finally:
            SECRETS.reset(token)
        self.controls_seen = {}  # (host, path) → every control label read there: a dialog closing brings none new
        self.state = self.new_state(self.browser, goal, title or goal[:60], page)
        self.state.update(skill=skill, preauth=preauth, cap=cap, site=skills.bare(urlparse(url).netloc.lower()),
                          start=url)
        self.habit = habit
        if habit:
            self.state["replay"] = replay.start(habit, sameness)
            if sameness == "same":  # the same errand: its plan, and no planner start turn
                self.state["plan"], self.state["reason"] = [dict(g) for g in habit["plan"]], None
                self.say(f"Same as last time: {habit['title']}.")

    @staticmethod
    def new_state(browser, goal, title, page):
        return dict(
            browser=browser, goal=goal, title=title, page=page, status="ready", started_at=None, elapsed_ms=0,
            waited_ms=0, history=[], messages=[], files=list(page.get("downloads", [])), answers={}, ids={}, typed={},
            approval=None, commit=None, done=[], errors=[], plan=[], current=0, notes=[], chat=[], feedback=[],
            reason="start", candidates=[], seen=None, recent=[], jev=[], turns=[], refusals={}, switched=[],
            subgoal_steps=0, loop_from=0, code_asks=0, model_errors=0, mismatches=0, done_refusals=0,
            captcha_rounds=0, meter=new_meter(), escalations={}, margins=[], situations=[], uploaded=[], held=None,
            told_at=None, delegated_at=None, relaying=False, skill=None, preauth=False, cap=None, site="",
            cleared_maybe=[], idle=0, idle_ticks=0, orders=[], checkout=None, replay=None, closed=False,
            commit_drops=0, idle_asked=0, circled={}, loops={}, fruitless=None, blank_waits=0, coded=[],
            file_refusals=0, path_steps={}, path_asked=[], refusals_log=[], stuck_why=None, acting=None,
            recent_at=None, last_by_site={}, wrong_cards=0, list_refusals=0, captcha_text=None, outcome=None,
        )

    # ---- what the user and the models see

    def snapshot(self):
        return {k: v for k, v in self.state.items() if k not in PRIVATE}

    def send(self, text, kind="info", **extra):
        message = {"from": "agent", "kind": kind, "text": text, **extra}
        self.state["messages"].append(message)
        if self.notify:
            self.notify(message)

    def release(self):
        """An update held back one step (an answer told just before DONE goes out once) goes out now."""
        held, self.state["held"] = self.state["held"], None
        if held:
            self.send(held)

    def say(self, text, kind="info", **extra):
        self.release()
        self.send(text, kind, **extra)

    def ask(self, message):
        """One question to the user; returns the reply. Secret replies never enter the log."""
        if self.asker is None:
            raise Halt("blocked", "This step needs you, but no one is attached to answer.")
        if self.state["held"] and repeats(message["text"], self.state["held"]):
            self.state["held"] = None  # the update held a step was this question: it goes once, as the question
        self.release()
        message = {"from": "agent", **message}
        self.state["messages"].append(message)
        if self.state["started_at"] is None:  # the wait is part of the task's time: busy = elapsed − waited ≥ 0
            self.state["started_at"] = time.perf_counter()
        started = time.perf_counter()
        reply = self.asker(message) or {}
        self.state["waited_ms"] += round((time.perf_counter() - started) * 1000)
        self.clock()  # recorded now: a cancel or a stop right after the wait must not leave elapsed behind waited
        if reply.get("cancel"):
            raise Halt("cancelled", "Cancelled. Nothing more will be done.")
        logged = {**reply, "text": "••••"} if message["kind"] == "secret" and "text" in reply else reply
        self.state["messages"].append({"from": "user", **logged})
        return reply

    def elapsed(self):
        started = self.state["started_at"]
        return round((time.perf_counter() - started) * 1000) if started else 0

    def clock(self):
        """Record the task's time so far. Every wait for the user falls inside it, so the agent's busy time (elapsed
        less waited) is never below zero."""
        self.state["elapsed_ms"] = max(self.elapsed(), self.state["waited_ms"])

    def clean(self, page):
        """The page as the worker keeps it: hidden values replaced before anything reads or trims the text, and
        fields that hold a secret masked."""
        page["text"] = self.secrets.text(page["text"])
        page["title"] = self.secrets.text(page["title"])
        page["actions"] = self.secrets.redact(page["actions"])
        for action in page["actions"]:
            if action.get("value") and (action.get("frame"), action.get("node")) in self.secrets.nodes:
                action["value"] = "[secret]"  # an emptied field (a rejected code) still reads as empty
        return page

    def observe(self):
        page = self.settled(self.clean(self.state["browser"].observe()))
        self.state["page"] = page
        self.state["files"].extend(page.get("downloads", []))
        self.forget_accepted(page)
        return page

    def settled(self, page):
        """On a site that names its cards by payee (its note's `cards: payee`), a read of its list (cards on the page,
        or the note's `list` words) is read again until two reads show the same cards in the same words: MyUPI draws
        them a card at a time (“cancel my aisle” was answered “no mandate” 6.7 s in, AISLE's card not yet drawn)."""
        note = skills.site_for(page["url"], self.library["sites"]) if page.get("url") else None
        if (note or {}).get("cards") != "payee":
            return page
        marker = (note.get("list") or "").lower()
        for _ in range(LIST_READS - 1):
            if not page.get("cards") and not (marker and marker in page["text"].lower()):
                return page
            time.sleep(LIST_GAP)
            again = self.clean(self.state["browser"].observe())
            if again.get("cards") and (again["cards"], again.get("card_lines")) == (page.get("cards"),
                                                                                    page.get("card_lines")):
                return again
            page = again
        return page

    def forget_accepted(self, page):
        """A code the site took no longer counts against its field: the page moved on and the field is gone."""
        labels = {a["label"] for a in page["actions"]}
        for where, typed in list(self.state["typed"].items()):
            moved = typed.get("url") and urlparse(typed["url"])[1:3] != urlparse(page["url"])[1:3]
            if moved and where[1] not in labels:
                del self.state["typed"][where]

    def file_name(self, path):
        """A file's name as logs and models may see it: IDs hidden, even inside names like Jane_Doe_CV."""
        name = Path(path).name
        spaced = re.sub(r"[_-]+", " ", name)
        return self.secrets.text(spaced) if self.secrets.text(spaced) != spaced else name

    def skill(self):
        return self.library["skills"].get(self.state["skill"]) if self.state["skill"] else None

    def subgoal(self):
        s = self.state
        return s["plan"][s["current"]] if s["current"] < len(s["plan"]) else None

    def user_words(self):
        return [c["text"] for c in self.state["chat"] if c["from"] == "user"]

    def recent_steps(self, n=executor.RECENT):
        keys = ("step", "operation", "action", "text", "effect", "changed")
        return [{k: h.get(k) for k in keys} for h in self.state["history"][-n:]]

    # ---- the loop

    def command(self, name):
        if name != "tick":
            raise ValueError("Unknown command")
        if self.state["status"] in STOPPED:
            return self.snapshot()  # a stopped task is never driven again
        for value, placeholder in self.memory.hidden_values():  # a value another task learned is hidden here too
            self.secrets.add(value, placeholder)
        secrets, meter = SECRETS.set(self.secrets), METER.set(self.state["meter"])
        try:
            return self.tick()
        except Halt as halt:
            self.log_refusal("halt", halt)
            self.state["status"] = halt.status
            self.clock()
            self.say(str(halt), kind="result")  # how it ended: a brain task's report (Experian refused the ERN)
            return self.snapshot()
        finally:
            SECRETS.reset(secrets)
            METER.reset(meter)

    def tick(self):
        s = self.state
        if s["started_at"] is None:
            s["started_at"] = time.perf_counter()
        before, replies = len(s["history"]), self.replies()
        s["acting"] = None
        try:
            self.step()
            s["model_errors"] = 0
        except StalePage as why:
            self.log_refusal("stale", why)
            s["status"] = "ready"
            self.observe()
        except TabsClosed as error:  # no page left to work on: never a hiccup to retry
            raise Halt("blocked", f"The browser tab closed, so I stopped.{self.pending()}") from error
        except Retry as why:
            self.log_refusal("retry", why)
            s["feedback"].append(str(why))
            s["reason"] = s["reason"] or "unsure"  # the planner hears it next
            self.refused()
        except (ValueError, RuntimeError, TimeoutError) as error:  # a hiccup: feedback, never the end by itself
            s["errors"].append({"step": len(s["history"]), "error": str(error)})
            s["model_errors"] += 1
            if s["model_errors"] >= 5:
                raise Halt("failed", f"The models stopped answering, so I stopped: {error}.{self.pending()}") \
                    from error
            if len(s["history"]) > before:  # the action itself ran; only reading its result failed
                s["feedback"].append(f"The last action already ran, but checking its result failed ({error}). "
                                     "Look at the page before repeating it.")
            else:
                s["feedback"].append(f"The last step failed ({error}); nothing was done. Decide again.")
            s["reason"] = s["reason"] or "unsure"
        self.count_idle(before, replies)
        self.clock()
        self.checkpoint()
        return self.snapshot()

    def replies(self):
        return sum(m["from"] == "user" for m in self.state["messages"])

    def log_refusal(self, error, message):
        """One action refused or cut short (a Retry, a stale page, a stop, a question to the user about a loop), for
        the trace: who chose it (the planner or Jev), what it was, and why. The latest REFUSALS_KEPT are kept."""
        s = self.state
        acting = s["acting"] or {}
        entry = {"step": len(s["history"]) + 1, "by": acting.get("by", "worker"), "error": error,
                 "op": acting.get("op"), "pick": str(acting.get("pick") or "")[:60],
                 "text": str(acting.get("text") or "")[:60], "message": str(message)[:160]}  # text: redacted alone
        s["refusals_log"] = (s["refusals_log"] + [entry])[-REFUSALS_KEPT:]

    def count_idle(self, before, replies):
        """A tick that ran no browser step and asked the user nothing (the planner and Jev handing the step back and
        forth): the user is asked at STUCK_AT such ticks in a row, and the task stops at IDLE_HALT."""
        s = self.state
        if len(s["history"]) > before or self.replies() > replies or s["status"] in STOPPED:
            return
        s["idle"] += 1
        if s["idle"] > 1:  # the planning tick of every run runs no step; only a second one in a row is idle
            s["idle_ticks"] += 1  # never reset: learning reads it
        if s["idle"] >= IDLE_HALT:
            raise self.circling(why=f"idle halt {s['idle']} ticks")
        if s["idle"] == STUCK_AT and not s["idle_asked"]:  # asked already since the last step: no second question
            self.stuck(f"idle {s['idle']} ticks")

    def circling(self, after_asking=False, why="circling"):
        """The stop for a task going round in circles; `why` (the guard that stopped it) goes into the trace."""
        self.state["stuck_why"] = why
        asked = " even after asking you," if after_asking else ""
        return Halt("blocked", f"I'm going in circles on {rules.host(self.state['page']['url'])}{asked} and stopped. "
                               f"Tell me more about what you want, or check the site yourself.{self.pending()}")

    def pending(self):
        """The warning a stop carries while a commit's outcome is unknown."""
        commit = self.state["commit"]
        return f" {commit['label']} may already have gone through; check it before trying again." if commit else ""

    def checkpoint(self):
        """Write the trace after every step, to the same file, so a stop at any moment keeps it."""
        try:
            self.save_trace()
        except OSError:
            pass

    def check_caps(self):
        """The task's limits: actions, the agent's own time (time the user took to answer doesn't count), and spend."""
        s = self.state
        reason = None
        if len(s["history"]) >= MAX_ACTIONS:
            reason = f"Stopped at the {MAX_ACTIONS}-action limit."
        elif self.elapsed() - s["waited_ms"] > MAX_MINUTES * 60000:
            reason = f"Stopped after {MAX_MINUTES} minutes."
        elif spent(s["meter"]) > MAX_SPEND:
            reason = f"Stopped at the ${MAX_SPEND:.2f} model-spend limit."
        if reason:
            raise Halt("blocked", reason + self.pending())

    def step(self):
        s = self.state
        if s["commit"]:  # a watch cut short: its outcome is known before any cap, Jev, or the planner acts
            gate.watch(self, s["commit"]["label"], s["commit"].get("goal") or s["goal"])
            return None
        self.check_caps()
        s.pop("typing", None)  # a code counts as an attempt only once it is typed
        try:
            page = s["page"] if s["browser"].fresh(s["page"]) else self.observe()
        except KeyError as error:  # Browser.session with its tab gone
            raise TabsClosed("The task's browser tab has closed.") from error
        page = self.unblank(page)
        if page is None:
            return None
        self.remember_page(page)
        pressed = self.mandate_cancel_pressed()
        if pressed:  # its hand-off was cut short (reading the click's result failed): the QR still goes next
            return self.hand_off(pressed)
        if self.captcha_unasked(page):  # MyUPI: the planner wandered the sign-in page and never sent it
            return self.relay_captcha(page)
        box = self.captcha_box(page) if self.captcha_pending() else None
        if box:  # the text the user read, typed now: the planner left it, and Continue stayed disabled
            inputs, shown = self.value(box, self.state["captcha_text"][0], page)
            return self.run(page, "TYPE_TEXT", box, inputs, shown, goal="type the captcha text the user read")
        signing = self.sign_in_step(page)
        if signing:  # MyUPI: the planner skipped the phone, so Continue never lit up
            operation, action, text = signing
            return self.act(operation, action, page, None, text=text)
        sending = self.code_submit(page)
        if sending:  # live: Experian's OTP was typed and never sent; the planner waited eleven times
            operation, action, key = sending
            return self.act(operation, action, page, None, key=key, paid=self.paid())
        if self.bought(page):  # the payment confirmed: the end, in code, so no voucher's code reaches a model
            return None
        code = self.otp_step(page)
        if code:  # the bank's one-time password for the payment the user said yes to
            return self.ask_code(page, {"targets": {"type_text_target": code}})
        form = self.card_form_step(page)
        if form:  # live: the planner made up a placeholder for the CVV, and the card form was never filled
            if form[0] == "ask":
                return self.ask_code(page, {"targets": {"type_text_target": form[1]}})
            _, operation, action, text = form
            return self.act(operation, action, page, None, text=text)
        chosen = self.mandate_step(page)
        if chosen:  # the demo: the planner never scrolled to AISLE's card and reached for GOOGLE PLAY's until stuck
            action, page = chosen
            return self.no_mandate(page) if action is None else self.act("CLICK", action, page, None)
        notice = self.notice_step(page)
        if notice:  # live: Experian's “… wait for 30 days …” was read as an instruction, and waited on twenty times
            return self.act("CLICK", notice, page, None)
        if self.card_step(page):  # live: the cart's calculator kept Infinia, and the planner asked to pay with it
            return None
        fewer = self.quantity_step(page)
        if fewer:  # live: the cart held 2 × ₹2,000 from an earlier run, and Checkout was offered at ₹4,000
            action, page = fewer
            return self.act("CLICK", action, page, None)
        if self.agree_step(page):  # live SmartBuy cart: Checkout did nothing until its two required boxes were ticked
            return None
        checkout = self.checkout_step(page)
        if checkout:  # live: with the boxes ticked the planner pressed the cart count "1" as the purchase
            action, page = checkout
            return self.act("CLICK", action, page, None)
        if self.voucher_read(page):  # live: the planner noted every amount, then said CONTINUE where DONE was due
            return None
        buying = self.voucher_step(page)
        if buying:  # dry run: the chosen amount left the controls offered, and the planner toggled two amounts
            action, page = buying
            return self.act("CLICK", action, page, None)
        fixing = self.row_step(page)
        if fixing:  # live: the planner opened other rows, cancelled the address it typed, and left the page
            operation, action, text, page = fixing
            return self.act(operation, action, page, None, text=text)
        if s["reason"]:
            return self.think(page)
        sub = self.subgoal()
        if sub is None or sub["kind"] in {"ask", "report"}:
            s["reason"] = "next"
            return self.think(page)
        if s["subgoal_steps"] >= SUBGOAL_STEPS:
            s["feedback"].append(f"{SUBGOAL_STEPS} steps on “{sub['do']}” without finishing it: rewrite the plan.")
            return self.escalate("replan", page)
        return self.system_one(page, sub)

    def remember_page(self, page):
        """Keep the page's text among the recent ones: facts may come from a page seen a moment ago. A read of the same
        page state (its address and controls) whose text alone moved (a rotating placeholder) replaces the last one,
        so it never pushes older pages out; the last page read on each site is kept too, for a comparison."""
        s = self.state
        at = (page["url"], views.signature(page))
        if s["recent"] and s["recent_at"] == at:
            s["recent"][-1] = page["text"]
        elif not s["recent"] or s["recent"][-1] != page["text"]:
            s["recent"] = (s["recent"] + [page["text"]])[-10:]
        s["recent_at"] = at
        s["last_by_site"][skills.bare(rules.host(page["url"]).lower())] = page["text"]
        self.controls_seen.setdefault(self.place_of(page), set()).update(a.get("label") for a in page["actions"])

    @staticmethod
    def place_of(page):
        """A page's address without its query: where controls read once stay known."""
        parts = urlparse(page["url"])
        return parts.netloc, parts.path

    def pages_before(self, page):
        """The pages a lookup's answer may come from besides this one: the last page read on every other site (a
        comparison's other side, however many reads ago), then the last two read."""
        s = self.state
        here = skills.bare(rules.host(page["url"]).lower())
        others = [text for site, text in s["last_by_site"].items() if site != here and text != page["text"]]
        return list(dict.fromkeys(others + [t for t in s["recent"] if t != page["text"]][-2:]))

    def unblank(self, page):
        """A blank page (no text, no controls) is no state for Jev or the planner to decide on: it is read again after
        a short pause, up to BLANK_WAITS times. One that stays blank goes to the planner as blank, and after three such
        turns the user is asked. Returns the page to decide on, or None when the user was asked."""
        s = self.state
        while blank(page) and s["blank_waits"] < BLANK_WAITS:
            s["blank_waits"] += 1
            time.sleep(self.blank_pause)
            page = self.observe()
        if not blank(page):
            s["blank_waits"] = 0
            return page
        s["blank_waits"] += 1
        if s["blank_waits"] > BLANK_WAITS + 3:
            s["blank_waits"] = BLANK_WAITS + 1
            self.loop_stuck(f"blank|{urlparse(page['url']).path}", f"blank {s['blank_waits']} reads")
            return None
        s["feedback"].append(f"The page has stayed blank (no text and no controls) after {BLANK_WAITS} waits: "
                             "OPEN_URL the address you need, go BACK, or ASK the user.")
        if not s["reason"]:
            s["escalations"]["blank"] = s["escalations"].get("blank", 0) + 1
            s["reason"] = "unsure"
        return page

    def system_one(self, page, sub):
        """One Jev request for this step, and what its answers mean: act, finish the subgoal, a blocker, or the
        planner."""
        s = self.state
        task = {"goal": s["goal"], "answers": self.user_words(), "recent": self.recent_steps()}
        state, questions, heads, controls = executor.request(page, sub, task, s["seen"])
        try:
            answers = jev.request(state, questions)
        except ValueError:
            answers = jev.request(state, questions)  # an invalid answer acts on nothing: asked once more
        step = executor.decide(answers, questions, heads, controls)
        if step["kind"] == "blocker" and step["blocker"] == "captcha" and not rules.captcha_challenge(page):
            # a captcha the page doesn't show (a "protected by reCAPTCHA" badge): the step goes on as if none blocked
            unblocked = {"choice": "none", "probabilities": {"none": 1.0}, "confidence": 1.0}
            step = executor.decide({**answers, "blocker": unblocked}, questions, heads, controls)
        s["jev"].append({"step": len(s["history"]) + 1, "subgoal": s["current"], "kind": step["kind"],
                         "op": step["op"], "p": round(step["p"], 3), "blocker": step["blocker"],
                         "margin": step["margin"], "state": state, "questions": questions,
                         "answers": {name: [a["choice"], round(a["probabilities"][a["choice"]], 3)]
                                     for name, a in answers.items()}})
        if step["margin"] is not None:
            s["margins"].append(round(step["margin"], 3))
        if s["history"] and step["effect"]:
            s["history"][-1].setdefault("effect", step["effect"])
        s["seen"], s["candidates"] = page, step["candidates"]
        s["acting"] = {"by": "jev", "op": step["op"], "pick": (step.get("action") or {}).get("label") or step["kind"]}
        if self.fruitless():
            return self.escalate("replan", page)
        if step["kind"] == "blocker":
            return self.blocked(step, page)
        if step["kind"] == "done":
            return self.advance()
        guided = self.guided(step, answers, page, controls)
        if guided:
            operation, action, key, text = guided
            return self.act(operation, action, page, sub, key=key, text=text)
        if step["kind"] == "think":
            return self.escalate("unsure", page)
        if step["operation"] in {"CLICK", "DOUBLE_CLICK"} and STRIP.search(step["action"].get("label") or "") \
                and not step["action"].get("within"):
            s["feedback"].append(f"Jev chose “{step['action'].get('label')}”: clicking it lands on its middle. PICK it "
                                 "with the words of the choice to click, like PICK [k] with text 2.")
            return self.escalate("unsure", page)
        if step["operation"] in {"CLICK", "DOUBLE_CLICK"} and self.recloses(step["action"]):
            s["feedback"].append(f"“{step['action'].get('label')}” is open now, its text on the page: read it and "
                                 "go on; pressing it again closes it.")
            return self.escalate("unsure", page)
        if step["operation"] in {"CLICK", "DOUBLE_CLICK"} and self.raises_quantity(step["action"]):
            s["feedback"].append(f"Jev chose “{step['action'].get('label')}”, which raises a quantity: press it only "
                                 "while the item's count is below what the user asked for; at their count, go on.")
            return self.escalate("unsure", page)
        label = step["action"].get("label") or step["operation"]
        problem = self.forbidden(step["operation"], label, page)  # ran 3 times from this page, or a 9th scroll
        count = executor.repeats(s["history"][s["loop_from"]:], step["operation"], label, page["url"])
        if problem or (count >= 2 and step["operation"] not in QUIET):  # Jev's next-best once, then mini
            key = [step["operation"], label, urlparse(page["url"]).path]
            other = next((c for c in step["candidates"] if c["action"].get("label") != label
                          and not self.forbidden(c["operation"], c["action"].get("label") or c["operation"], page)),
                         None)
            if count >= 3 or key in s["switched"] or other is None:
                s["feedback"].append(problem or f"“{label}” changed nothing {count} times, and Jev's next-best choice "
                                                "didn't help: try another way.")
                return self.escalate("unsure", page)
            s["switched"].append(key)
            return self.act(other["operation"], other["action"], page, sub, key=other.get("key"))
        return self.act(step["operation"], step["action"], page, sub, key=step.get("key"))

    # ---- habit replay

    def guided(self, step, answers, page, controls):
        """Replay's say on Jev's answers: (operation, action, key, text) to act on when Jev's top pick is the step the
        habit expects (passing the gates, or the only control on the page with that label unless it commits), else
        None to go on as in Phase A. Another pick that passes the gates, or none, takes this subgoal out of replay. A
        wait, a scroll, a secret field, an expected control not on the page yet, or one that changed nothing twice or
        already ran three times from this page (the loop ladder decides) changes nothing."""
        s = self.state
        record = s["replay"]
        expected = replay.expected(self.habit, record, s["current"]) if record else None
        if not expected or expected["secret"]:
            return None
        commit = (self.subgoal() or {}).get("kind") == "commit"  # a commit is never forced past the gates
        verdict, chosen = replay.judge(expected, step, answers, page, controls, commit=commit)
        if verdict in {"absent", "quiet"}:
            return None
        if verdict == "matched":
            name = chosen[1].get("label") or chosen[0]
            unchanged = executor.repeats(s["history"][s["loop_from"]:], chosen[0], name, page["url"])
            if self.forbidden(chosen[0], name, page) or (chosen[0] not in QUIET and unchanged >= 2):
                return None  # it changed nothing twice, or ran from this page 3 times: the loop ladder decides
        record["expected"] += 1
        if verdict != "matched":
            record["left"].append(s["current"])
            record["fallbacks"].append({"subgoal": s["current"], "step": len(s["history"]) + 1,
                                        "reason": f"Jev chose {chosen}" if verdict == "other" else "unsure"})
            return None
        record["matched"] += 1  # the cursor moves once the step has run (replay_moved): a page that moved runs nothing
        operation, action, key = chosen
        values = (self.subgoal() or {}).get("values") or []
        text = next((v["text"] for v in values if v["field"] == expected["value"]), None) if expected["value"] else None
        return operation, action, key, text

    def replay_moved(self, operation, action):
        """A step ran: when it was the habit's expected step (a code typed as in Phase A included), replay moves past
        it. A captcha square the user picked never does."""
        s = self.state
        expected = replay.expected(self.habit, s["replay"], s["current"]) if s["replay"] and not s["relaying"] else None
        if expected and operation == expected["op"] and learn.control_name(action.get("label")) == expected["name"]:
            s["replay"]["used"][str(s["current"])] = s["replay"]["used"].get(str(s["current"]), 0) + 1

    def expected_on(self, page):
        """Whether the habit's next expected control is already on this page: no need to look again."""
        s = self.state
        expected = replay.expected(self.habit, s["replay"], s["current"]) if s["replay"] else None
        return bool(expected) and replay.present(expected, page) > 0

    def blocked(self, step, page):
        """A blocker Jev sees. A captcha goes to the user as a picture; a code field is asked through the hidden
        field and typed without the planner; anything else brings its situation skill to the planner."""
        if step["blocker"] == "captcha":
            return self.relay_captcha(page)
        if step["blocker"] == "code" and any(secret_field(a) for a in page["actions"]):
            return self.ask_code(page, step)
        situation = {"sign_in": "sign-in", "payment": "payment", "unavailable": "location"}.get(step["blocker"])
        if situation and situation not in self.state["situations"]:
            self.state["situations"].append(situation)
        return self.escalate(f"blocker:{step['blocker']}", page)

    def ask_code(self, page, step):
        """A code, OTP, password, or CVV field: the user answers through the hidden field and the answer is typed as
        its placeholder. No model sees it, and the planner isn't called."""
        s = self.state
        chosen = step["targets"].get("type_text_target")
        fields = [a for a in action_space(page["actions"])[1].get("type_text_target", {}).values()
                  if secret_field(a)]
        empty = [a for a in fields if a.get("value") != "[secret]"]  # fields still waiting for their value
        if chosen is not None and secret_field(chosen) and chosen.get("value") != "[secret]":
            field = chosen
        else:  # Jev chose a field already typed into: the first one still empty, else that one
            field = empty[0] if empty else chosen if chosen is not None and secret_field(chosen) else \
                (fields[0] if fields else None)
        if field is None or field.get("value") == "[secret]":  # none, or a code typed that the site hasn't rejected
            return self.escalate("blocker:code", page)
        card = bool(rules.CARD_DETAIL.search(field["label"]))
        if card and self.unchosen_card(field, page):  # a pre-selected card's CVV box: the user picks the method first
            s["feedback"].append(PICK_METHOD)
            if "payment" not in s["situations"]:
                s["situations"].append("payment")
            return self.escalate("blocker:payment", page)
        fresh = None if card else self.untyped_code()
        if fresh:  # sent a moment ago, to the planner's own question or before the page moved on: typed, not asked
            inputs, shown = self.value(field, f"[secret:{fresh}]", page)
            return self.run(page, "TYPE_TEXT", field, inputs, shown, goal="type what the user sent")
        if not card and s["code_asks"] >= CODE_ASKS and s.get("bank_code"):
            raise Halt("blocked", f"Your bank didn't take the one-time password after {CODE_ASKS} tries, so I "
                                  "stopped. Your bank's SMS will say whether anything was charged.")
        if not card and s["code_asks"] >= CODE_ASKS:  # each code usually means a new one sent to the user's phone
            raise Halt("blocked", f"Signing in to {rules.host(page['url'])} isn't working after {CODE_ASKS} codes, "
                                  "so I stopped rather than send you more. Please sign in once in the agent's "
                                  "Chrome window, then ask me again.")
        site, label = self.site_name(page), field["label"].rstrip(" *")
        question = (f"{site} is asking for your card's {label.rstrip('.')}. Type it here; it stays hidden." if card else
                    f"{site} is asking for the one-time password your bank sent for this payment. Type it here; it "
                    "stays hidden." if s.get("bank_code") else
                    f"{site} is asking for a code or password. Type it here; it stays hidden.")
        self.steady(page)  # a page still switching screens gets read again first
        reply = self.ask({"kind": "secret", "text": question})
        answer = str(reply.get("text", "")).strip()
        name = self.keep_secret(answer)
        if card:  # a card's detail is never a code to type elsewhere (dry run: the CVV went into the bank's OTP box)
            s.setdefault("card_answers", []).append(name)
        if not card and code_like(answer):
            self.count_code(name)  # a code the user got: a question dropped for a changed page, or "none", isn't one
        s["chat"] += [{"from": "agent", "text": question}, {"from": "user", "text": f"[secret:{name}] (hidden answer)"}]
        inputs, shown = self.value(field, f"[secret:{name}]", page)
        return self.run(page, "TYPE_TEXT", field, inputs, shown, goal="type what the user sent")

    def untyped_code(self):
        """The user's latest hidden answer when it is a code no field has had yet. MyUPI asked for its SMS code three
        times: the planner asked, then this rule; and an answer dropped when the page moved on (its resend countdown
        ticking) was asked for again."""
        s = self.state
        name = next(reversed(s["answers"]), None)
        typed = {entry.get("key") for entry in s["typed"].values()} | set(s.get("typed_keys", ()))
        return name if name and name not in typed and name not in s.get("card_answers", ()) \
            and code_like(s["answers"][name]) else None

    def count_code(self, name):
        """One more code toward CODE_ASKS, once per answer: when the user's reply is a code, or a hidden answer is
        typed into a code or password field."""
        s = self.state
        if name not in s["coded"]:
            s["coded"].append(name)
            s["code_asks"] += 1

    def captcha_pending(self):
        """The text the user read off a captcha, given and not typed yet."""
        pending = self.state["captcha_text"]
        return bool(pending) and not any(h.get("operation") == "TYPE_TEXT" and h.get("text") == pending[0]
                                         for h in self.state["history"][pending[1]:])

    def sign_in_step(self, page):
        """On a sign-in form its site note says the worker fills (MyUPI's `sign-in: auto`): (operation, control, text)
        for the next step, else None. The phone first, then the agree box, then Continue or Proceed once no box is
        empty; the captcha and the code have their own rules, which ask the user."""
        note = skills.site_for(page["url"], self.library["sites"])
        if (note or {}).get("sign-in") != "auto":
            return None
        fills = [a for a in page["actions"] if a["kind"] == "fill"]
        code = (note.get("otp") or "").lower()  # live SmartBuy: a registered number is asked its password, or a code
        last = self.state["history"][-1].get("action") if self.state["history"] else None
        switch = next((a for a in page["actions"] if code and a["kind"] == "click" and a.get("label") != last
                       and (a.get("label") or "").strip().lower() == code), None)
        if switch and any((a.get("constraints") or {}).get("type") == "password" for a in fills):
            return "CLICK", switch, None  # the code comes to the user's phone; their password never comes from us
        phones = [a for a in fills if (a.get("constraints") or {}).get("type") == "tel"
                  or PHONE_BOX.search(a.get("label") or "")]
        if not phones and not any(a.get("group") for a in fills):
            return None  # not the sign-in form: neither its phone box nor its code's boxes
        empty = next((a for a in phones if not a.get("value")), None)
        if empty:
            return "TYPE_TEXT", empty, "[profile:phone]"
        agree = next((a for a in page["actions"] if a.get("role") == "checkbox" and a.get("checked") != "true"
                      and AGREE.search(a.get("label") or "")), None)
        if agree:
            return "CLICK", agree, None
        if any(not a.get("value") for a in fills):
            return None  # the captcha or the code, still to come from the user
        submit = next((a for a in page["actions"] if a["kind"] == "click" and SUBMIT.match(a.get("label") or "")),
                      None)
        return ("CLICK", submit, None) if submit else None

    def code_submit(self, page):
        """A one-time code the last step typed, still in its field: (operation, control, key) that sends it, the
        field's own submit button or Enter, else None. Never a card's detail: those are never typed."""
        last = self.state["history"][-1] if self.state["history"] else None
        if not last or last.get("operation") != "TYPE_TEXT" or not str(last.get("text") or "").startswith("[secret:"):
            return None
        field = next((a for a in page["actions"] if a["kind"] == "fill" and a.get("label") == last.get("action")
                      and a.get("value")), None)
        if field is None or rules.CARD_DETAIL.search(field.get("label") or ""):
            return None  # the site took it (the field is gone), or it isn't a code's
        button = next((a for a in page["actions"] if a["kind"] == "click" and field.get("submit")
                       and a.get("label") == field["submit"]), None)
        return ("CLICK", button, None) if button else ("PRESS_KEY", field, "Enter")

    def mandate_step(self, page):
        """A mandate the user chose (the brain's goal “Cancel the UPI AutoPay mandate whose payee is AISLE”) on a site
        whose cards go by payee: (its control to press, the page it is on), (None, page) when the whole list has no
        such card, else None for the planner. The list is opened from its note's `list-link`, the card scrolled into
        view, and its Cancel or Pause pressed: MyUPI lists its cards in a new order on each load, and only those on
        screen are offered."""
        chosen = CHOSEN.match(self.state["goal"])
        note = skills.site_for(page["url"], self.library["sites"]) if page.get("url") else None
        if not chosen or (note or {}).get("cards") != "payee":
            return None
        word, payee = chosen[1], chosen[2].strip()
        label = f"{word} (in “{payee}”)".lower()
        pressable = next((a for a in page["actions"] if (a.get("label") or "").lower() == label), None)
        if pressable:
            return pressable, page
        cards = [c.lower() for c in page.get("cards") or []]
        if payee.lower() in cards:
            if self.state["browser"].reveal(payee, word):
                page = self.observe()
                pressable = next((a for a in page["actions"] if (a.get("label") or "").lower() == label), None)
            return (pressable, page) if pressable else None
        marker = (note.get("list") or "").lower()
        if marker and marker in page["text"].lower():  # the whole list (one mandate's own panel says nothing of it)
            return None, page  # read whole, and settled: no card is this payee's
        link = (note.get("list-link") or "").lower()
        opener = next((a for a in page["actions"] if a["kind"] == "click" and link
                       and (a.get("label") or "").strip().lower() == link), None)
        return (opener, page) if opener else None

    def notice_step(self, page):
        """A notice its site note says to close (`close`: Experian's “If you have made a recent payment, …”, a pop-up
        with no button over the dispute page): the control showing it, else None. Never twice in a row: a notice that
        stays is the planner's."""
        words = ((skills.site_for(page["url"], self.library["sites"]) or {}).get("close") or "").lower()
        last = self.state["history"][-1].get("action") if self.state["history"] else None
        return next((a for a in page["actions"] if words and a["kind"] == "click" and a.get("label") != last
                     and words in (a.get("label") or "").lower()), None)

    def card_step(self, page):
        """The card the brain's checkout names (“…, paying with Regalia Gold Credit Card.”) picked in its portal's
        savings calculator (the note's `cards:` list, SmartBuy's closed menu, Infinia by default), once a page: the
        cart's savings are that card's then, and no one asks how to pay. True when it was picked."""
        s = self.state
        card = VOUCHER_CARD.search(s["goal"]) if VOUCHER_BUY.match(s["goal"]) else None
        note = (skills.site_for(page["url"], self.library["sites"]) if page.get("url") else None) or {}
        done = s.setdefault("cards_picked", [])
        if not card or not note.get("cards") or self.place_of(page) in done:
            return False
        try:  # live, the first look came before the calculator's cards: tried again each step until picked
            picked = s["browser"].pick(note["cards"], card[1])
        except (StalePage, RuntimeError, TimeoutError):
            return False  # the planner goes on; the approval still shows the cart's own figures
        if picked:
            done.append(self.place_of(page))
        return picked

    def quantity_step(self, page):
        """On a cart of the brain's vouchers (SmartBuy's lines: "Voucher Value 2,000 … You Pay ₹ 4,000" is two), each
        line brought to the order's count by its own stepper, first line first: (its "Decrease quantity" or "Increase
        quantity", page). A value the order doesn't name goes down to none (live, an earlier run's 2 × ₹2,000 sat in
        the cart and Checkout was offered at ₹4,000). Only while the page shows one stepper a line, at most 20 presses;
        else None. A cart whose lines are the order is marked for `checkout_step`: on screen alone a line's value shows
        (live, it sits in a disabled box the whole page's words leave out)."""
        s = self.state
        wanted = voucher_order(s["goal"])
        if not wanted or "voucher value" not in page["text"].lower().replace("\n", " "):
            return None
        steps = ("decrease quantity", "increase quantity")
        last = (s["history"][-1].get("action") or "") if s["history"] else ""
        if last.lower().startswith(steps):  # its answer comes from the site's server: read until it shows (live: a
            for _ in range(CART_READS):  # second + before it did made ₹6,000 of ₹4,000)
                time.sleep(CART_WAIT)
                page = self.observe()
                if cart_lines(page["text"]) != s.get("cart_seen"):
                    break
        if sum((h.get("action") or "").lower().startswith(steps) for h in s["history"]) >= 20:
            return None
        lines = cart_lines(page["text"])
        s["cart_seen"] = lines
        s["cart_ok"] = self.place_of(page) if sorted(lines) == sorted(wanted.items()) else None
        fewer, more = ([a for a in page["actions"] if a["kind"] == "click"
                        and (a.get("label") or "").lower().startswith(word)] for word in steps)
        if not lines or not len(lines) == len(fewer) == len(more):
            return None  # a line's stepper off screen, or a line with none: which is whose isn't known
        for (value, count), down, up in zip(lines, fewer, more):
            if count != wanted.get(value, 0):
                return (down if count > wanted.get(value, 0) else up), page
        return None

    def agree_step(self, page):
        """The boxes its site note's `agree:` names (words their labels start with, comma-separated: SmartBuy's cart
        "Read T&C and important instructions …", "I agree to the Terms and Conditions") ticked on a purchase, each only
        while unticked: they sit below the cart's first screen and a click on a ticked one unticks it (live, the planner
        toggled one seven times). The final control does nothing until they are ticked, and the user's yes there covers
        them; a box the note doesn't name (Receive Gift Voucher on WhatsApp) is never ticked. True when it ticked one;
        with every named box ticked, the page is marked ready for its final control."""
        s = self.state
        note = (skills.site_for(page["url"], self.library["sites"]) if page.get("url") else None) or {}
        words = [w.strip().lower() for w in str(note.get("agree") or "").split(",") if w.strip()]
        if not words or not VOUCHER_BUY.match(s["goal"]) or s.get("agree_tries", 0) >= 3:
            return False
        try:
            boxes = s["browser"].tick(words)
        except (StalePage, RuntimeError, TimeoutError):
            return False
        if boxes["ticked"]:
            s["agree_tries"] = s.get("agree_tries", 0) + 1  # a box that never stays ticked is the planner's
            return True
        if boxes["found"]:
            s["agreed_at"] = self.place_of(page)
        return False

    def checkout_step(self, page):
        """On the page whose named boxes are all ticked (`agree_step`), its note's `checkout:` control (SmartBuy's
        Checkout, below the cart's first screen, which opens the payment page), scrolled into view: (control, page);
        the gate asks the user before it only when it pays. Once only; else None."""
        s = self.state
        note = (skills.site_for(page["url"], self.library["sites"]) if page.get("url") else None) or {}
        finals = [w.strip() for w in str(note.get("checkout") or "").split(",") if w.strip()]
        if not finals or s.get("agreed_at") != self.place_of(page) or not VOUCHER_BUY.match(s["goal"]):
            return None

        def final(p):
            return next((a for name in finals for a in p["actions"] if a["kind"] == "click"
                         and (a.get("label") or "").strip().lower().startswith(name.lower())), None)

        if any(final({"actions": [{"kind": "click", "label": h.get("action")}]}) for h in s["history"]):
            return None
        if s.get("cart_ok") != self.place_of(page):
            return None  # its lines, read on screen (`quantity_step`), are the order: nothing more, nothing missing
        total = re.search(r"total cart value\s*₹\s?([\d,]+)", self.page_words(), re.IGNORECASE)
        if total and total[1].replace(",", "") != VOUCHER_BUY.match(s["goal"])[1].replace(",", ""):
            return None  # and its total, anywhere on the page
        control = final(page)
        if not control and any(s["browser"].into_view(name) for name in finals):
            page = self.observe()
            control = final(page)
        return (control, page) if control else None

    def card_form_step(self, page):
        """On a payment page whose note names its card form (`pay-form:`, the words its boxes' labels start with, in
        order: GyFTR's pay.gyftr.com), in a voucher purchase whose goal names the card: the next step, ("ask", box) for
        a card detail (number, expiry, CVV: through the hidden field, each time, never kept), ("act", operation,
        control, text) otherwise, else None. Its Card method when the boxes aren't shown, the name on the card from the
        profile, a ticked "Save card" box unticked, then its `final:` control (PROCEED), once: the gate asks the user
        there, their one yes for the payment. A payable amount above the order's total stops the task first."""
        s = self.state
        note = (skills.site_for(page["url"], self.library["sites"]) if page.get("url") else None) or {}
        words = [w.strip().lower() for w in str(note.get("pay-form") or "").split(",") if w.strip()]
        wanted = VOUCHER_BUY.match(s["goal"])
        if not words or not wanted or not VOUCHER_CARD.search(s["goal"]):
            return None
        finals = tuple(w.strip().lower() for w in str(note.get("final") or "").split(",") if w.strip())
        pressed = [(h.get("action") or "").strip().lower() for h in s["history"]]
        if any(label.startswith(finals) for label in pressed if finals):
            return None  # paid: the bank's page is next
        clicks = [a for a in page["actions"] if a["kind"] == "click"]
        boxes = [next((a for a in page["actions"] if a["kind"] == "fill"
                       and (a.get("label") or "").strip().lower().startswith(word)), None) for word in words]
        if not any(boxes):
            card = next((a for a in clicks if (a.get("label") or "").strip().lower() == "card"), None)
            return ("act", "CLICK", card, None) if card and "card" not in pressed else None
        if None in boxes:
            return None  # a box off screen or missing: the planner's
        for box in boxes:
            if not box.get("value"):
                return ("ask", box) if rules.CARD_DETAIL.search(box.get("label") or "") else \
                    ("act", "TYPE_TEXT", box, "[profile:name]")
        saved = next((a for a in clicks if a.get("role") == "checkbox" and a.get("checked") == "true"
                      and (a.get("label") or "").lower().startswith("save card")), None)
        if saved:
            return "act", "CLICK", saved, None  # never kept on the site either
        total, due = int(wanted[1].replace(",", "")), PAYABLE_AMOUNT.search(page["text"])
        if due and float(due[1].replace(",", "")) > total:
            raise Halt("blocked", f"The payment page asks ₹{due[1]} for your ₹{total:,} of vouchers, so I stopped "
                                  "before paying. Nothing was charged.")
        control = next((a for a in clicks if (a.get("label") or "").strip().lower().startswith(finals)), None) \
            if finals else None
        return ("act", "CLICK", control, None) if control else None

    def approval_lines(self, page, found):
        """A voucher purchase's approval, written in code from its goal and the page's payable total, else None: the
        cart was checked against the goal before Checkout (`checkout_step`) and the payable total against its total
        (`card_form_step`). Dry run: the summary model cut the vouchers' amounts the payment page doesn't show (“2 × +
        1 ×”) and wrote "Seller not specified"."""
        s = self.state
        if not VOUCHER_BUY.match(s["goal"]):
            return None
        card = VOUCHER_CARD.search(s["goal"])
        return [s["goal"].removeprefix("Buy ").split(", paying with")[0].rstrip("."),
                *([f"Card: {card[1]}"] if card else []),
                *([f"Payable: {gate.tight(found['total'])}"] if found["total"] else [])]

    def paid(self):
        """Whether this voucher purchase pressed its payment's final control with the user's yes (GyFTR's PROCEED):
        the bank's one-time password that follows is part of that payment, and their yes covers sending it."""
        return bool(VOUCHER_BUY.match(self.state["goal"])) and any(
            h.get("committed") and h.get("approved") == "user" for h in self.state["history"])

    def bank_box(self, page):
        """After the payment the user said yes to (`paid`), the bank's one-time password box still empty, else None."""
        return next((a for a in page["actions"] if rules.secret_field(a) and a.get("value") != "[secret]"
                     and not rules.CARD_DETAIL.search(a.get("label") or "")), None) if self.paid() else None

    def otp_step(self, page):
        """The bank's one-time password box (`bank_box`), for `ask_code` to fill through the hidden field; its tries
        are counted afresh, apart from the sign-in's code. Else None."""
        s = self.state
        box = self.bank_box(page)
        if box and not s.get("bank_code"):
            s["bank_code"], s["code_asks"] = True, 0
        return box

    def bought(self, page):
        """A voucher purchase whose payment the site confirmed (`paid`, then a commit it confirmed, with no code box
        left to fill): the task ends with its report written in code, from the goal and the order's id (on this page,
        else the payment page before it), never the page's words: a voucher's code or PIN never reaches a model or the
        chat. True when it ended."""
        s = self.state
        if not s["done"] or not self.paid() or any(rules.secret_field(a) for a in page["actions"]):
            return False
        order = next((m[1] for text in [page["text"], *reversed(s["recent"])] if (m := ORDER_ID.search(text))), None)
        bought = "Bought " + s["goal"].removeprefix("Buy ").split(", paying with")[0].rstrip(".")
        s["held"] = None
        self.say(f"{bought}." + (f" Order {order}." if order else "") + " The site sends the codes by SMS and email.",
                 kind="result")
        s["status"], s["outcome"] = "done", "bought"
        return True

    def voucher_read(self, page):
        """The brain's voucher read (“Show the Swiggy gift vouchers on SmartBuy: list the amounts …”) on a portal whose
        note names its cart button: done here, from the page as it is: its amount tiles (₹ lines and controls) and the
        lines that state a reward (points, cashback, iCash, an instant discount). True when it ended the task; False
        when the page shows no amounts yet (a pop-up over it, still loading): the planner goes on."""
        s = self.state
        wanted = VOUCHER_READ.match(s["goal"])
        note = (skills.site_for(page["url"], self.library["sites"]) if page.get("url") else None) or {}
        if not wanted or not note.get("cart"):
            return False
        tile = re.compile(r"^₹\s?([\d,]+)$")
        lines = [line.strip() for line in page["text"].splitlines() if line.strip()]
        first = next((n for n, line in enumerate(lines) if tile.match(line)), len(lines))
        # its tiles are controls on both portals; else the first run of ₹ lines (a calculator's "₹325" comes later)
        found = [tile.match(a.get("label") or "")[1] for a in page["actions"] if tile.match(a.get("label") or "")] or \
            [tile.match(line)[1] for line in itertools.takewhile(tile.match, lines[first:])]
        amounts = sorted({int(a.replace(",", "")) for a in found})
        if not amounts:
            return False
        # the whole page's words when its note says how its offers are headed (SmartBuy): its calculator and offers sit
        # below the first screen, and on screen they come in pieces ("Reward Points", "Cashback")
        body = self.page_words() if note.get("offers") or note.get("cards") else ""
        rewards = self.offers(note, body) or list(dict.fromkeys(
            line[:120] for line in lines[:first] if REWARD_LINE.search(line)
            and not re.search(r"value|calculat|effective", line, re.IGNORECASE)))[:4]
        # above its amounts: below them iShop suggests other brands, and SmartBuy's calculator estimates
        report = f"{wanted[1]} gift vouchers on {wanted[2]}: " + ", ".join(f"₹{a:,}" for a in amounts) + "."
        if rewards:
            report += " Rewards shown: " + "; ".join(rewards) + "."
        limit = re.search(r"monthly limit:?\s*₹\s?[\d,]+", body, re.IGNORECASE)
        if limit:
            report += f" {limit[0]}."
        report += self.card_savings(note, body)
        s["held"] = None
        self.say(report, kind="result", evidence=rewards[0] if rewards else lines[0])
        s["status"] = "done"
        return True

    def page_words(self):
        """The whole page's words, on screen or not, in one line; empty when the page can't be read."""
        try:
            return " ".join(self.state["browser"].texts("body"))
        except (StalePage, RuntimeError, TimeoutError):
            return ""

    @staticmethod
    def offers(note, body):
        """The offers a voucher page states under its note's `offers:` headings, each with its figures ("ACCELERATED
        REWARDS Upto 5X Reward Points / 5% Cashback"); a heading's banner with no figure is no offer."""
        heads = [h.strip() for h in str(note.get("offers") or "").split(",") if h.strip()]
        if not heads or not body:
            return []
        unit = r"(?:Cashback|Reward Points|iCash|Instant Discount|Discount)\*?"
        found = re.finditer(r"(" + "|".join(map(re.escape, heads)) + r")\s+(.{3,90}?" + unit +
                            r"(?:\s*[/+]\s*[\d.]+\s*(?:X|%)\s*" + unit + r")*)", body)
        return list(dict.fromkeys(f"{m[1]} {m[2]}" for m in found if re.search(r"\d\s*(?:X|%)", m[2])))[:3]

    def card_savings(self, note, body):
        """Each card's saving from the portal's own calculator (its note's `cards:` names the list, SmartBuy's closed
        dropdown), on the amount it quotes ("Effective Price ₹2,000 ₹1,675"): live, it showed Infinia's alone, and the
        user has another card. Empty when the note names none or the page shows none."""
        if not note.get("cards"):
            return ""
        kinds = {"RP": "in reward points", "CB": "cashback", "CP": "in cash points"}
        try:
            texts = self.state["browser"].texts(note["cards"])
        except (StalePage, RuntimeError, TimeoutError):
            return ""  # the amounts still go out; the cards are asked about without their savings
        found = [re.match(r"(.+?)\s*Save\s*₹\s*([\d,]+)\s*via\s*(RP|CB|CP)\b", text) for text in texts]
        found = [m for m in found if m]
        basis = re.search(r"effective price\s*₹\s?([\d,]+)", body, re.IGNORECASE)  # the amount it quotes them on
        if not found or not basis:
            return ""
        return (f" Savings on ₹{basis[1]} by card (the site's estimate): "
                + "; ".join(f"{m[1].strip()} ₹{m[2]} {kinds[m[3]]}" for m in found) + ".")

    def voucher_step(self, page):
        """The vouchers the brain's checkout names (“Buy ₹5,000 of Swiggy gift vouchers on SmartBuy: 2 × ₹2,000 + 1 ×
        ₹1,000.”) put in the cart on a portal whose note names its cart buttons (`cart:`, in order: SmartBuy's "Add to
        cart, View Cart & Buy"): (control, page) to press, else None for the planner. Each value in turn, largest
        first: its tile until the page's Total shows it, then Add to cart, unless the page shows it in the cart already
        (live, signed in with that amount in the cart: a quantity stepper where Add to cart was); then the last button.
        Each is scrolled into view when off screen; a tile at most twice, Add to cart never twice for a value, and
        nothing once the last button was pressed (the counts are the cart page's: `quantity_step`). A chosen tile leaves
        the controls offered, so the Total is the proof. A portal with one button (iShop's Buy now) takes one
        voucher."""
        s = self.state
        wanted = voucher_order(s["goal"])
        note = (skills.site_for(page["url"], self.library["sites"]) if page.get("url") else None) or {}
        carts = [c.strip() for c in str(note.get("cart") or "").split(",") if c.strip()]
        if not wanted or not carts or (len(carts) == 1 and sum(wanted.values()) != 1):
            return None

        def flat(text):  # "₹ 1,000" and "₹1000" alike
            return re.sub(r"[\s,]", "", text or "").lower()

        def control(p, label):
            return next((a for a in p["actions"] if a["kind"] == "click" and flat(a.get("label")) == flat(label)), None)

        def chosen(p, amount):
            return re.search(r"total(?:value)?:?" + re.escape(amount) + r"(?!\d)", flat(p["text"]))

        pressed = [h.get("action") for h in s["history"]]
        if any(flat(label) == flat(carts[-1]) for label in pressed):
            return None  # its last button pressed: the cart is the next page's
        added = s.setdefault("vouchers_added", [])
        for value in (v for v in wanted if v not in added):
            amount = f"₹{value}"
            presses = [n for n, label in enumerate(pressed) if flat(label) == amount]
            if not chosen(page, amount):
                tile = control(page, amount)
                if tile and len(presses) < 2:
                    return tile, page
                # off screen: the tile, else (pressed already) its Total, beside the first cart button the page has
                # (live, with that amount in the cart there was no Add to cart, only View Cart & Buy)
                if not (any(s["browser"].into_view(c) for c in carts) if presses else s["browser"].into_view(amount)):
                    return None
                page = self.observe()
                if not chosen(page, amount):
                    tile = control(page, amount)
                    return (tile, page) if tile and len(presses) < 2 else None
            if len(carts) > 1:  # its Add to cart, unless this value is in the cart already
                add, since = carts[0], pressed[presses[-1] + 1:] if presses else pressed
                button = control(page, add)
                if not button and s["browser"].into_view(add):
                    page = self.observe()
                    button = control(page, add)
                if button and since and flat(since[-1]) == flat(add):  # its answer comes from the site's server: a
                    time.sleep(CART_WAIT)  # second press before it shows would add two
                    page = self.observe()
                    if control(page, add):
                        return None  # pressed, and still offered once its answer had time to come: the planner's
                elif button:
                    return None if any(flat(label) == flat(add) for label in since) else (button, page)
            added.append(value)
        button = control(page, carts[-1])
        if not button and s["browser"].into_view(carts[-1]):
            page = self.observe()
            button = control(page, carts[-1])
        return (button, page) if button else None

    def row_step(self, page):
        """A personal detail the brain's dispute names (“The report shows: Address 1: …. It should be: …”) on a site
        whose rows each have their own edit control (Experian's pencil, its note's `rows`): (operation, control, text,
        page) for the next step, else None for the planner. The Personal details tab, then that row's control (scrolled
        into view; never another row's), the value into the box it opens, then Submit, once, which needs the user's
        yes like any final step."""
        s = self.state
        fix = ROW_FIX.search(s["goal"])
        note = skills.site_for(page["url"], self.library["sites"]) if page.get("url") else None
        control = (note or {}).get("rows")
        if not fix or not control or any(h.get("committed") or h.get("action") == "Submit" for h in s["history"]):
            return None
        row, value = fix[1].strip(), fix[2].strip()

        def labelled(p, label):
            return next((a for a in p["actions"] if a["kind"] == "click"
                         and (a.get("label") or "").lower() == label.lower()), None)

        tab = next((a for a in page["actions"] if a.get("role") == "tab"
                    and (a.get("label") or "").lower().startswith("personal details")), None)
        if tab and tab.get("selected") == "false":
            return "CLICK", tab, None, page
        box = next((a for a in page["actions"] if a["kind"] == "fill"
                    and f"“{row.lower()}" in (a.get("label") or "").lower()), None)
        if box and rules.normal(box.get("value") or "").lower() != rules.normal(value).lower():
            typed = sum(h.get("operation") == "TYPE_TEXT" and h.get("action") == box.get("label") for h in s["history"])
            return ("TYPE_TEXT", box, value, page) if typed < 2 else None  # twice and still not shown: the planner's
        if box:
            submit = labelled(page, "Submit")
            if not submit and s["browser"].into_view("Submit"):  # Experian's sits below the first screen
                page = self.observe()
                submit = labelled(page, "Submit")
            return ("CLICK", submit, None, page) if submit else None
        pencil = labelled(page, f"{control} (in “{row}”)")
        if not pencil and any(f"{control} (in “".lower() in (a.get("label") or "").lower() for a in page["actions"]):
            if s["browser"].reveal(row, control):  # other rows' pencils in view: its own is further down
                page = self.observe()
                pencil = labelled(page, f"{control} (in “{row}”)")
        return ("CLICK", pencil, None, page) if pencil else None

    def captcha_box(self, page):
        """The captcha's text box when it is empty, else None."""
        return next((a for a in page["actions"] if a["kind"] == "fill" and not a.get("value")
                     and rules.CAPTCHA_BOX.search(a.get("label") or "")), None)

    def captcha_unasked(self, page):
        """A captcha on screen with its box empty and none of the user's text for it still to type: it goes to them
        first, before anything else on the page."""
        return bool(self.captcha_box(page)) and rules.captcha_challenge(page) and not self.captcha_pending() and \
            not LOADING.search(page["text"])  # MyUPI: its picture still said “Loading...”: sent once it shows

    def relay_captcha(self, page):
        """A captcha only the user may solve: they see it with its squares numbered, and the worker clicks exactly
        the squares they name, or hands the text they read to the planner. Nothing is solved automatically."""
        s = self.state
        if self.captcha_pending():
            s["reason"] = "reply"  # the text the user read is in the chat, not typed yet: typed next, never asked again
            return None
        s["captcha_rounds"] += 1
        if s["captcha_rounds"] > CAPTCHA_ROUNDS:
            raise Halt("blocked", f"The site kept showing new checks after {CAPTCHA_ROUNDS} rounds, so I stopped. "
                                  "Please try this step on your own device.")
        clicks = [a for a in page["actions"] if a["kind"] == "click" and a.get("node") is not None]
        typed = any(a["kind"] == "fill" and rules.CAPTCHA_BOX.search(a.get("label") or "") for a in page["actions"])
        marks = [] if typed else ([a for a in clicks if a.get("frame")] or clicks)[:40]  # squares: in their frame
        image = (typed and s["browser"].captcha_screenshot(masked=self.secrets.nodes, hide=self.hidden_details())) or \
            s["browser"].marked_screenshot(marks, masked=self.secrets.nodes, hide=self.hidden_details())  # its picture alone
        reply = self.ask({"kind": "image", "image": image, "captcha": True, "text": "The site wants a check (captcha) "
                          "solved. " + ("Type the text it shows." if typed else "Reply with the numbers of the squares "
                          "to pick, like 2 5 8; or type the text it shows; or say none if there is nothing to pick.")})
        text = str(reply.get("text", "")).strip()
        joined = re.sub(r"\band\b", " ", text, flags=re.IGNORECASE)  # "2 and 5", "2, 5 & 7": squares; "4829": text
        numbers = [int(n) for n in re.findall(r"\d+", joined)] if re.fullmatch(r"[\d\s,&]+", joined) else []
        picked = list(dict.fromkeys(numbers)) if numbers and all(1 <= n <= len(marks) for n in numbers) else None
        if picked is None and text.lower() in {"none", ""}:
            s["notes"].append("The user says the captcha has nothing to pick: press its verify or skip button.")
        elif picked is None:
            s["captcha_text"] = (text, len(s["history"]))
            s["chat"] += [{"from": "agent", "text": "(captcha)"}, {"from": "user", "text": f"“{text}” (the text "
                                                                                            "the captcha shows)"}]
            s["reason"] = "reply"  # the planner types it into the captcha's field
        else:
            s["relaying"] = True  # these clicks are the user's: no loop counting in between
            try:
                done = [n for n in picked if self.click_same(marks[n - 1])]
            finally:
                s["relaying"] = False
            s["notes"].append(f"Clicked the captcha squares the user picked ({', '.join(map(str, done))}). "
                              "Press its verify or next button now." if done else
                              "None of the squares the user picked could be pressed.")
        s["status"] = "ready"
        return None

    def click_same(self, target):
        """Click the element the user named, on a fresh read of the page; never a commit. True only when it was
        pressed."""
        page = self.observe()  # the user may have taken a while
        clicks = [a for a in page["actions"] if a["kind"] == "click"]
        same = (target.get("frame"), target.get("node"))
        match = next((a for a in clicks if (a.get("frame"), a.get("node")) == same), None)
        if match is None:
            named = [a for a in clicks if a["label"] == target["label"]]
            match = named[0] if len(named) == 1 else None  # two "Deliver here" buttons: never guess
        if match is None:
            return False  # the page moved on
        if rules.commit_label("CLICK", match, page) or rules.maybe_commit_label("CLICK", match, page):
            self.state["notes"].append(f"The user's pick “{match['label']}” would commit, so it wasn't pressed.")
            return False
        try:
            self.run(page, "CLICK", match, {}, None, goal="the user's pick")
        except StalePage:
            self.observe()
            return False
        return True

    def escalate(self, reason, page):
        s = self.state
        s["escalations"][reason] = s["escalations"].get(reason, 0) + 1
        s["reason"] = reason
        return self.think(page)

    def advance(self):
        """The page shows the subgoal done: the next one starts; the planner hears only when it must act."""
        s = self.state
        s["current"] += 1
        s["subgoal_steps"] = 0
        after = self.subgoal()
        if after is None or after["kind"] in {"ask", "report"}:
            s["reason"] = "next"
        return None

    def refused(self):
        """The same planner action refused again and again: replan at 3, ask the user at 6. A CONTINUE refused (the
        plan has nothing left Jev can do) hears that the goal isn't finished."""
        s = self.state
        act = s["turns"][-1]["action"] if s["turns"] else {}
        key = f"{act.get('op')}:{act.get('pick')}:{str(act.get('text', ''))[:40]}"
        count = s["refusals"][key] = s["refusals"].get(key, 0) + 1
        if count == 3 and act.get("op") == "CONTINUE":
            s["feedback"].append(f"The goal is not finished: write the remaining subgoals{self.ending()}, or finish "
                                 "with DONE or STOP. Never CONTINUE again here.")
            s["reason"] = "replan"
        elif count == 3:
            s["feedback"].append("This was refused 3 times: stop trying it. Change the plan, pick another candidate, "
                                 "or ASK the user.")
            s["reason"] = "replan"
        if count >= 6:
            s["refusals"][key] = 0
            if s["idle_asked"]:  # the user was asked since the last step, and still nothing runs
                raise self.circling(after_asking=True, why=f"refused {count}x after asking: {key}")
            self.stuck(f"refused {count}x: {key}")

    # ---- System Two

    def think(self, page):
        """One planner turn: a note, the rest of the plan, and one action."""
        s = self.state
        before = s["seen"]  # the planner still reads what changed since the last decision
        self.refresh(page)
        payload = self.payload(page, before)
        try:
            out, meta = planner.turn(payload)
        except ValueError:
            out, meta = planner.turn(payload)  # a cut-off or refused reply is asked once more
        reason = s["reason"]
        s["turns"].append({"step": len(s["history"]) + 1, "reason": reason,
                           "latency_ms": meta.get("latency_ms", 0), **out,
                           "candidates": payload["candidates"][:5], "feedback": payload["feedback"][-5:]})
        act = out["action"]
        s["acting"] = {"by": "planner", "op": act["op"], "pick": act["pick"], "text": act["text"]}
        s["reason"], s["feedback"], s["seen"] = None, [], page
        if reason == "replan":
            s["subgoal_steps"] = 0
        if out["note"].strip():
            s["notes"].append(out["note"].strip())
        subgoals = [{"do": g["do"].strip(), "done_when": g["done_when"].strip(), "kind": g["kind"],
                     "values": [v for v in g["values"] if v["field"].strip() and str(v["text"]).strip()]}
                    for g in out["subgoals"] if g["do"].strip()][:10]
        if unchanged(subgoals, s["plan"][s["current"]:]):
            subgoals = []  # the rest of the plan sent again as it was: the plan is kept, as for []
        moved_on = False  # whether this turn gave Jev another current subgoal
        if subgoals:
            was = self.subgoal()
            s["plan"] = s["plan"][:s["current"]] + self.keeps_commit(subgoals)
            now = self.subgoal()
            moved_on = not (was and now and shares(was["do"], now["do"], by=max))
            if moved_on:  # the same subgoal rewritten keeps counting toward SUBGOAL_STEPS
                s["subgoal_steps"] = 0
        if any(PLACE_STEP.search(g["do"]) for g in subgoals) and "location" not in s["situations"]:
            s["situations"].append("location")  # a step that sets the place: the planner reads the location rules
        if s["replay"] and (reason == "start" or subgoals):  # a plan of another shape, now or later: no replay
            s["replay"]["on"] = s["replay"]["on"] and replay.same_shape(s["plan"], self.habit["plan"])
        if act["op"] not in {"DONE", "TELL", "ASK"}:
            self.release()  # a TELL waits one step: when the next move is DONE or ASK with the same words, they go once
        if act["op"] in TALKING:
            return self.talk(out, page)
        if act["op"] == "CONTINUE":
            sub = self.subgoal()
            if sub is None:
                raise Retry(f"The plan has nothing left: give subgoals{self.ending()}, or finish with DONE or STOP. "
                            "CONTINUE does nothing here.")
            if sub["kind"] in {"ask", "report"}:
                yours = "ASK the question" if sub["kind"] == "ask" else "finish with DONE (the result and a quote)"
                raise Retry(f"The current subgoal is yours, not Jev's: {yours}, or give the remaining subgoals"
                            f"{self.ending()}. CONTINUE does nothing here.")
            if (reason == "unsure" or (reason or "").startswith("blocker:")) and not moved_on:
                raise Retry("Jev couldn't decide: PICK a candidate, TYPE, ASK, OPEN_URL, LOOK, WAIT or BACK; CONTINUE "
                            "does nothing here.")
            return None
        return self.do(act, page)

    def unordered(self):
        """Whether the goal is an order or booking that hasn't gone through yet."""
        s = self.state
        return rules.commerce_goal(s["goal"]) and not s["done"] and not s["orders"]

    def ending(self):
        """What the rest of the plan must end with, for feedback: the commit, while an order hasn't gone through."""
        return " (ending with the commit step: place the order, pay, or book)" if self.unordered() else ""

    def keeps_commit(self, subgoals):
        """The new rest of the plan, still holding the commit while an order or booking hasn't gone through. A replan
        that leaves out the commit subgoal the old plan still has gets it back (`with_commit`), with no planner call.
        When neither has one, the replan is refused and the old plan kept; after COMMIT_DROPS refusals in a row it is
        taken, and so is every later plan without a commit until one has a commit again: the gate still guards every
        commit, and a plan that runs out is refused at CONTINUE until the user is asked. The first plan, and the plan
        for a goal that asks a question, are never refused."""
        s = self.state
        rest = s["plan"][s["current"]:]
        if not self.unordered() or has_commit(subgoals):
            s["commit_drops"] = 0
            return subgoals
        if has_commit(rest):
            s["commit_drops"] = 0
            return with_commit(subgoals, rest)
        if not s["plan"] or asks(s["goal"]) or s["commit_drops"] >= COMMIT_DROPS:
            return subgoals
        s["commit_drops"] += 1
        s["reason"] = "replan"
        raise Retry("This goal ends in an order or booking: the plan must include the final commit subgoal (kind "
                    "commit: place the order, pay, or book) after the cart and payment steps; write the remaining "
                    "subgoals.")

    def refresh(self, page):
        """Jev's candidates for the page the planner is about to read, when they came from another one (the page
        changed since Jev last decided, or Jev has read none yet): one Jev request on this page. Nothing is acted on
        and the plan stays; a failed request keeps the old candidates, and the planner can still ASK, OPEN_URL, or
        WAIT."""
        s = self.state
        if s["seen"] is not None and s["seen"].get("fingerprint") == page.get("fingerprint"):
            return
        sub = self.subgoal() or {"do": s["goal"], "done_when": "", "kind": "act", "values": []}
        task = {"goal": s["goal"], "answers": self.user_words(), "recent": self.recent_steps()}
        try:
            state, questions, heads, controls = executor.request(page, sub, task, s["seen"])
            try:
                answers = jev.request(state, questions)
            except ValueError:
                answers = jev.request(state, questions)  # an invalid answer is asked once more
            step = executor.decide(answers, questions, heads, controls)
        except (ValueError, RuntimeError, TimeoutError):
            return
        s["jev"].append({"step": len(s["history"]) + 1, "subgoal": s["current"], "kind": "refresh",
                         "op": step["op"], "p": round(step["p"], 3), "blocker": step["blocker"],
                         "margin": step["margin"], "state": state, "questions": questions,
                         "answers": {name: [a["choice"], round(a["probabilities"][a["choice"]], 3)]
                                     for name, a in answers.items()}})
        s["seen"], s["candidates"] = page, step["candidates"]

    def payload(self, page, previous):
        """What the planner reads, stable parts first so the provider's prompt cache holds them. `previous` is the
        page the last decision was made on: what changed since then is marked."""
        s = self.state
        skill = self.skill()
        names = dict.fromkeys([*(skill["uses"] if skill else []), *s["situations"]])
        situations = [self.library["skills"][n] for n in names if n in self.library["skills"]]
        return {
            "skill": skills.render(skill, situations, skills.site_for(page["url"], self.library["sites"])),
            "goal": s["goal"], "today": time.strftime("%A, %d %B %Y"), "dates": planner.dates(s["goal"]),
            "sites": [{"name": site["name"], "start": site.get("start"), "search": site.get("search")}
                      for site in skills.named_all(s["goal"], self.library["sites"])],  # a comparison opens each
            "plan": [{"n": n, **g, "current": n - 1 == s["current"]} for n, g in enumerate(s["plan"], 1)],
            "notes": list(reversed(dict.fromkeys(reversed(s["notes"]))))[-15:], "chat": s["chat"][-20:],
            "memory": self.memory.recall(s["skill"], skills.bare(rules.host(page["url"]).lower()), s["goal"]),
            "steps": self.recent_steps(8),
            "committed_and_waiting": s["commit"]["label"] if s["commit"] else None,
            "files_saved": [self.file_name(f) for f in s["files"]],
            "page": views.view(page, previous),
            "candidates": [{"n": c["n"], "op": c["op"], "element": c["element"], "p": round(c["p"], 2)}
                           for c in s["candidates"]],
            "reason": s["reason"], "feedback": s["feedback"],
            **({"habit": {"title": self.habit["title"], "goal": self.habit["goal"], "plan": self.habit["plan"]}}
               if s["replay"] and s["reason"] == "start" else {}),
        }

    def do(self, act, page):
        """The planner's browser actions: one of Jev's candidates or a page element by its number, an address,
        WAIT, BACK, or a look."""
        s, op = self.state, act["op"]
        if op in {"PICK", "TYPE"}:
            number = re.search(r"\[(\d+)\]", act["pick"]) or self.past_candidates(act["pick"], page)
            opener = self.search_opener(op, number[1], page) if number else None
            if opener is not None:
                return self.type_through(opener, act["text"], page)
            if number:  # a control Jev didn't rank, by the number the page view gives it
                operation, action, text = self.element(op, number[1], act["text"], page)
                key = None
            else:
                chosen = next((c for c in s["candidates"] if str(c["n"]) == act["pick"].strip()), None)
                if chosen is None:
                    raise Retry(f"{op} names one of Jev's candidates by its number; there are "
                                f"{len(s['candidates'])} ({self.listed()}). To act on another control, use its "
                                "element number in brackets, like [12].")
                spot = self.on_words(chosen["action"], act["text"], typed=op == "TYPE") \
                    if chosen["operation"] == "CLICK" else None
                if spot is None and op == "TYPE" and chosen["operation"] == "CLICK" and \
                        SEARCHY.search(chosen["action"].get("label") or ""):
                    return self.type_through(chosen["action"], act["text"], page)
                if spot is not None:  # a candidate clicked on the planner's words: “2” in “1 2 3 … 10”
                    chosen = {**chosen, "operation": "CLICK", "action": spot}
                    op = "PICK"
                if op == "TYPE" and chosen["operation"] != "TYPE_TEXT":
                    raise Retry(f"Candidate {chosen['n']} {self.not_a_field(chosen['action'])}")
                operation, action, key = chosen["operation"], chosen["action"], chosen.get("key")
                text = act["text"] if op == "TYPE" else None
                if op == "PICK" and operation == "TYPE_TEXT":  # a field picked with no text: the subgoal's, else focus
                    text = self.subgoal_value(action, self.subgoal())
                    operation = "TYPE_TEXT" if text is not None else "CLICK"  # focused, the site may offer options
            if operation == "TYPE_TEXT":
                self.misplaced(action, text, page)
            if self.guard(operation, action.get("label") or operation, page, text):
                return None
            return self.act(operation, action, page, self.subgoal(), key=key, text=text)
        if op == "OPEN_URL":
            url = act["text"].strip()
            parts = urlparse(url)
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise Retry("OPEN_URL needs a full http(s) address in text.")
            if not skills.template_host(url, skills.site_for(page["url"], self.library["sites"]),
                                        here=urlparse(page["url"]).netloc):
                raise Retry("A search address built from the site's template must stay on that site.")
            control = self.control(page, "OPEN_URL")
            if self.guard("OPEN_URL", control.get("label") or "OPEN_URL", page, url):
                return None
            return self.run(page, "OPEN_URL", control, {"url": url}, url)
        if op == "WAIT":
            return self.run(page, "WAIT", self.control(page, "WAIT"), {}, None)
        if op == "BACK":
            _, heads, controls = action_space(page["actions"])
            current = next((a for a in heads.get("tab_target", {}).values() if a.get("current")), None)
            if "GO_BACK" not in controls and current and len(heads["tab_target"]) > 1:
                operation, control = "CLOSE_TAB", current  # a new tab: back is its opener
            else:
                operation, control = "GO_BACK", self.control(page, "GO_BACK")
            if self.guard(operation, control.get("label") or operation, page):
                return None
            return self.run(page, operation, control, {}, None)
        what = act["text"].strip() or (self.subgoal() or {}).get("do") or "the control the task needs next"
        spot = self.look(page, what)  # LOOK
        if spot is None:
            raise Retry("Looking at the screen found nothing that fits. Try another way.")
        return self.act("LOOK", {**self.control(page, "LOOK"), **spot}, page, self.subgoal())

    def listed(self):
        """Jev's candidates as a refusal names them: "candidates: 1 [2] Continue, 2 …"."""
        return "candidates: " + (", ".join(f"{c['n']} {c['element']}" for c in self.state["candidates"]) or "none")

    def element(self, op, index, text, page):
        """(operation, action, text) for the page element the planner names by its number in the page view ("[12]")
        when the control isn't one of Jev's candidates: a field to type into (PICK types the subgoal's value, else
        only focuses it), a control to click, or a dropdown's option (TYPE with the option's words). The gates that
        hold every action still hold it. Refused when the page has no such element or it can't take the step."""
        elements, heads, _ = action_space(page["actions"])
        fill = heads.get("type_text_target", {}).get(index)
        click = heads.get("click_target", {}).get(index)
        options = [a for i, a in heads.get("select_target", {}).items() if i.split(":")[0] == index]
        names = ", ".join(f"“{a['label'].split(' → ', 1)[-1]}”" for a in options)
        if int(index) > views.ELEMENTS or not any(e["index"] == index for e in elements):
            raise Retry(f"The page has no element [{index}]. Use the number of an element the page lists, or one of "
                        f"Jev's candidates ({self.listed()}).")
        spot = self.on_words(click, text, typed=op == "TYPE") if not fill else None
        if spot is not None:  # "2" in a row of numbers, a day in a strip of dates: the click lands on those words
            return "CLICK", spot, None
        if op == "TYPE" and fill:
            return "TYPE_TEXT", fill, text
        if op == "TYPE" and options:
            wanted = " ".join(str(text).split()).casefold()
            option = next((a for a in options if wanted in {a["label"].split(" → ", 1)[-1].strip().casefold(),
                                                            str(a.get("value", "")).casefold()}), None)
            if option is None:
                raise Retry(f"[{index}] is a dropdown: TYPE [{index}] with the words of one of its options ({names}).")
            return "SELECT", option, None
        if op == "TYPE":
            raise Retry(f"[{index}] {self.not_a_field(click)}")
        if fill:  # picked with no text: the subgoal's, else only focused (the site may offer its own options)
            value = self.subgoal_value(fill, self.subgoal())
            return ("TYPE_TEXT" if value is not None else "CLICK"), fill, value
        if click:
            return "CLICK", click, None
        if options:
            raise Retry(f"[{index}] is a dropdown: TYPE [{index}] with the words of the option to choose ({names}).")
        raise Retry(f"[{index}] can't be clicked, typed into, or chosen from. Use another element, or one of Jev's "
                    f"candidates ({self.listed()}).")

    def past_candidates(self, pick, page):
        """A bare number above Jev's candidates' that numbers a page element ("14" with five candidates): that element,
        as if written [14]. A candidate's own number always stays the candidate."""
        bare = re.fullmatch(r"\s*(\d+)\s*", pick or "")
        if not bare or int(bare[1]) <= len(self.state["candidates"]):
            return None
        elements, _, _ = action_space(page["actions"])
        return bare if any(e["index"] == bare[1] for e in elements) else None

    def misplaced(self, action, text, page):
        """Refuse the plan's value for one field typed into another ("Indiranagar Metro Station", the drop, into the
        pickup box) when the page has the field that value is named for: Retry naming it by its number."""
        values = (self.subgoal() or {}).get("values") or []
        meant = next((v for v in values if str(v["text"]).strip() == str(text or "").strip()), None)
        name = executor.words(meant["field"]) - executor.GENERIC if meant else set()
        if not name or name & executor.words(action.get("label", "")):
            return
        _, heads, _ = action_space(page["actions"])
        right = next(((i, a) for i, a in heads.get("type_text_target", {}).items()
                      if name & executor.words(a.get("label", ""))), None)
        if right:
            raise Retry(f"“{text}” is the plan's value for “{meant['field']}”: type it into [{right[0]}] "
                        f"“{right[1]['label']}”, not “{action.get('label')}”.")

    def other_item(self, action, page):
        """Why a time slot is another item's, or None. A page reached by clicking a venue's name ("PVR: Nexus") on an
        item's own page (a movie's show times) lists every item's slots there, each carrying its row's title (“(in
        “Doraemon…”)”): a slot whose row names none of the words the item's page title had (and this page's title
        doesn't) belongs to another item."""
        row = SLOT_ROW.search(action.get("label") or "")
        here = urlparse(page["url"]).path
        came = next((h for h in reversed(self.state["history"]) if urlparse(h.get("url") or "").path != here), None)
        if not row or not came or came.get("operation") not in {"CLICK", "DOUBLE_CLICK"}:
            return None
        title, link = rules.words(page.get("title") or ""), rules.words(came.get("action") or "")
        if not link or len(link & title) * 2 < len(link):
            return None  # not opened by clicking this page's own name: the rows may be venues, not items
        item = {w for w in rules.words(came.get("title") or "") - title if not w.isdigit()}
        if not item or rules.words(row[1]) & item:
            return None
        return (f"“{action.get('label')}” is a show of “{row[1]}”, but this page (“{page.get('title')}”) lists every "
                f"show there and was opened from “{came.get('title')}”: choose a show of that one (its own show times "
                "list them), or ASK the user before switching to another.")

    def narrows(self, action):
        """The words a click on a search suggestion adds to what was just typed into a search box, that neither the
        goal nor the user's answers say ("anc" in “boat airdopes 141 anc” after “boAt Airdopes 141”): another product
        than the one asked for. Empty for a place box's suggestions, which are picked to set the place."""
        s = self.state
        last = s["history"][-1] if s["history"] else {}
        field = executor.words(last.get("action", ""))
        if last.get("operation") != "TYPE_TEXT" or not SEARCHY.search(last.get("action", "")) or \
                field & (executor.PLACE | executor.ROLES | {"landmark", "destination", "street"}):
            return set()
        typed, label = executor.words(last.get("text") or ""), executor.words(action.get("label") or "")
        if not typed & label:
            return set()  # not a suggestion of what was typed: a search button, another control
        said = typed | executor.words(s["goal"]) | executor.words(" ".join(self.user_words()))
        return label - said - FILLER

    @staticmethod
    def card_add(action, sub):
        """A card (a clickable container, not a button) whose words hold its ADD, clicked for a step that adds to the
        cart: the click lands on the ADD inside it, not on the card's middle."""
        add = re.search(r"\bADD\b|\bAdd to cart\b", action.get("label") or "")
        wants = re.search(r"\b(add|cart)\b", (sub or {}).get("do") or "", re.IGNORECASE)
        if not add or not wants or action.get("within") or action.get("role") != "clickable" or \
                (action.get("label") or "").strip() == add[0]:
            return action
        return {**action, "within": add[0], "label": f"{action['label']} (at “{add[0]}”)"}

    @staticmethod
    def on_words(click, text, typed=False):
        """A click control whose name shows these words among others ("2" in “1 2 3 … 10”): the control, clicked on
        those words (`within`, found and hit-tested by the browser), labelled with them so each is its own step. TYPE
        gets this only for a number, or a slider or group of choices: words typed at a link are meant for a field.
        None when there is no such control or no such words."""
        words = " ".join(str(text or "").split())
        name = (click or {}).get("label") or ""
        if typed and not words.isdigit() and (click or {}).get("role") not in {"slider", "radiogroup", "tablist",
                                                                                  "listbox", "spinbutton"}:
            return None
        if not words or not click or re.fullmatch(r"\W*" + re.escape(words) + r"\W*", name, re.IGNORECASE):
            return None
        if not re.search(r"(?<![\w])" + re.escape(words) + r"(?![\w])", name, re.IGNORECASE):
            return None
        return {**click, "within": words, "label": f"{name} (at “{words}”)"}

    def recloses(self, action):
        """Whether a click would close the panel the last step opened: the same control, now expanded."""
        last = self.state["history"][-1] if self.state["history"] else {}
        return str(action.get("expanded")).lower() == "true" and last.get("operation") in {"CLICK", "DOUBLE_CLICK"} \
            and last.get("action") == action.get("label")

    @staticmethod
    def raises_quantity(action):
        """Whether a control is a quantity stepper's + (it shows only "+", or its name says increase): how many the
        user wants is the planner's to read, never Jev's alone."""
        plus = {"+", "＋"}
        return (action.get("shown") or "").strip() in plus or (action.get("name") or "").strip() in plus or \
            bool(RAISES.search(action.get("name") or ""))

    @staticmethod
    def search_opener(op, index, page):
        """The search control drawn as a link or button (no field of its own) that TYPE names by its number, else
        None."""
        if op != "TYPE":
            return None
        _, heads, _ = action_space(page["actions"])
        click = heads.get("click_target", {}).get(index)
        if heads.get("type_text_target", {}).get(index) or click is None:
            return None
        return click if SEARCHY.search(click.get("label") or "") else None

    def type_through(self, control, text, page):
        """TYPE into a search control drawn as a link or button: press it, then type into the one search box it
        opened, each through the loop guards and the gate. When none (or several) opened, the planner hears it."""
        s = self.state
        if not str(text or "").strip():
            raise Retry(CLEARING)
        sub, label = self.subgoal(), control.get("label") or "CLICK"
        if self.guard("CLICK", label, page):
            return None
        self.act("CLICK", control, page, sub)
        if s["status"] in STOPPED or s["commit"] or s["reason"]:  # stopped, committing, or the user was asked
            return None
        after = s["page"]
        boxes = [a for a in after["actions"] if a.get("kind") == "fill"
                 and (a.get("role") == "searchbox" or SEARCHY.search(a.get("label") or ""))]
        if len(boxes) > 1:
            focused = s["browser"].focused_label()
            boxes = [a for a in boxes if a["label"] == focused]
        if len(boxes) != 1:
            s["feedback"].append(f"Pressed “{label}”, but no search box opened (or several did): TYPE into the search "
                                 "field the page shows now, by its number.")
            return None
        if self.guard("TYPE_TEXT", boxes[0]["label"], after, text):
            return None
        return self.act("TYPE_TEXT", boxes[0], after, sub, text=text)

    @staticmethod
    def not_a_field(action):
        """Why TYPE can't go into this control, and what to do instead: a link or button that may open a field (a
        search bar drawn as a link) is pressed first."""
        if action and action.get("kind") == "click":
            return (f"is a {action.get('role') or 'button'}, not a field: PICK it to open its field, then TYPE there.")
        return "is not a field to type into."

    def talk(self, out, page):
        """ASK, TELL, DONE, or STOP from the planner, its words checked against the universal rules first."""
        s = self.state
        act = out["action"]
        message = plain(out["message"].strip() or (act["text"].strip() if act["op"] != "DONE" else ""))
        op = "ASK" if act["op"] == "TELL" and message.endswith("?") else act["op"]  # a question waits for its answer
        options = [re.sub(r"^\d{1,2}[.)]\s+", "", plain(o.strip())) for o in out["options"] if o.strip()][:10]
        if op in {"ASK", "STOP"} and rules.captcha_challenge(page):
            return self.relay_captcha(page)  # only the user solves a captcha
        if op in {"ASK", "STOP", "DONE"} and self.no_card_named(page):
            return self.no_mandate(page)  # “No Netflix mandate here. What would you like to do?”: say so, and end
        problem = planner.lint(op, message, options, s["chat"])
        if problem:
            raise Retry(problem)
        if op == "TELL":
            if not message:
                raise Retry("TELL needs a message.")
            if s["told_at"] == len(s["history"]):
                raise Retry("You just told the user something. Do an action next, or finish with DONE: the answer "
                            "the user asked for goes in DONE's message, never in another TELL.")
            if any(repeats(message, c["text"]) for c in s["chat"][-8:] if c["from"] == "agent"):
                raise Retry("You told the user this already: don't repeat it. WAIT for the page, act, or ASK something "
                            "new.")
            qr = s["browser"].qr_screenshot(masked=self.secrets.nodes, hide=self.hidden_details()) \
                if rules.QR.search(page["text"]) or rules.QR.search(message) else None  # its words may scroll off
            if qr:  # a QR for the user's phone (a UPI app's mandate or payment) goes out now, as its picture
                self.say(message, image=qr)
            else:
                self.release()
                s["held"] = message
            s["chat"].append({"from": "agent", "text": message})
            s["told_at"] = len(s["history"])
            return None
        if op == "STOP":
            raise Halt("blocked", message or "I stopped: I can't find a way to finish this on the site.")
        if op == "DONE":
            return self.finish(act, message, page)
        if not message:
            raise Retry("ASK needs the question in message.")
        wants_code = bool(CODE_WORDS.search(message) or rules.asks_for_secret(message))
        if wants_code and not rules.code_field(page) and not rules.CARD_DETAIL.search(message) \
                and not rules.TO_RECEIVE.search(message):
            raise Retry("The system asks for codes only when a code field is on the page: don't ask the user for a "
                        "code the site hasn't asked for. Do the step that makes the site send one, then wait for its "
                        "field.")
        if rules.CARD_DETAIL.search(message) and not self.method_question(message, options) and \
                not self.method_chosen():
            raise Retry(PICK_METHOD)
        # A question that asks for (or mentions) a code, password, or card detail goes through the hidden field,
        # options included: a reply typed in the open would reach the models. A picked option is read by code below.
        secret = (rules.asks_for_secret(message) or (rules.code_field(page) and bool(CODE_WORDS.search(message)))
                  or bool(rules.CARD_DETAIL.search(message)))
        saved = {i["key"]: i["label"] for i in self.memory.ids()}
        held = [k for k, pattern in PROFILE_ASKS.items() if k in saved and pattern.search(message)]
        held += [k for k, label in saved.items() if k not in PROFILE_ASKS and rules.words(label)
                 and rules.words(label) <= rules.words(message)]
        if not secret and held:
            raise Retry(f"Memory has it: use [profile:{held[0]}] as the value instead of asking.")
        unknown = [k for k, pattern in PROFILE_ASKS.items() if k not in saved and k not in s["ids"]
                   and pattern.search(message)]
        if not secret and unknown:
            raise Retry(f"TYPE [profile:{unknown[0]}] into the field instead: the system asks the user for it hidden "
                        "and remembers it.")
        if s["delegated_at"] == len(s["history"]):
            raise Retry("The user just left this choice to you: pick the option that best fits the goal, TELL them "
                        "which one, and act on it.")
        counts = bool(secret and CODE_WORDS.search(message) and not rules.CARD_DETAIL.search(message))
        if counts and s["code_asks"] >= CODE_ASKS:
            raise Halt("blocked", f"Signing in to {rules.host(page['url'])} isn't working after {CODE_ASKS} codes, "
                                  "so I stopped rather than send you more. Please sign in once in the agent's "
                                  "Chrome window, then ask me again.")
        if self.moved(page):
            raise StalePage("The page changed while the planner decided. Decide again.")
        text = message + "".join(f"\n{n}. {o}" for n, o in enumerate(options, 1))
        self.steady(page)  # a page still switching screens gets read again first
        reply = self.ask({"kind": "secret" if secret else "text", "text": text})
        answer = str(reply.get("text", "")).strip()
        s["chat"].append({"from": "agent", "text": text})
        pick = picked(answer, options) if secret else None
        if pick:  # one of the offered options: not a secret, and the planner needs to read it
            s["chat"].append({"from": "user", "text": f"“{answer}” → option {pick}: {options[pick - 1]}"})
        elif secret:
            name = self.keep_secret(answer)
            if counts and code_like(answer):
                self.count_code(name)  # a code the user got; a dropped question or "it never came" is none
            s["chat"].append({"from": "user", "text": f"[secret:{name}] (hidden answer; type it as this placeholder)"})
        else:
            s["chat"].append({"from": "user", "text": self.read(message, options, answer)})
        if (self.subgoal() or {}).get("kind") == "ask":
            self.advance()  # the plan's question is answered: the next subgoal is current, so CONTINUE can carry on
        s["reason"] = "reply"
        return None

    @staticmethod
    def method_question(message, options=()):
        """Whether a question asks how to pay: it lists two or more payment methods, or asks which payment or how to
        pay (`HOW_TO_PAY`) without asking for a card detail. A yes to "Shall I proceed to payment?" picks nothing."""
        text = "\n".join([message, *options])
        methods = {m.casefold() for m in rules.METHOD.findall(text)}
        return len(methods) >= 2 or bool(HOW_TO_PAY.search(text) and not rules.CARD_DETAIL.search(text))

    def method_chosen(self):
        """Whether the chat holds the user's answer to a question about how to pay (never an approval's yes)."""
        chat = self.state["chat"]
        return any(q["from"] == "agent" and r["from"] == "user" and not q.get("approval")
                   and self.method_question(q["text"]) for q, r in zip(chat, chat[1:]))

    def unpicked_method(self, page):
        """Whether the page offers a choice of payment methods (controls naming two or more) while the user hasn't said
        how to pay, in the chat or the goal: a saved card or method shown pre-selected is not their pick."""
        methods = {m.casefold() for a in page["actions"] if a.get("kind") == "click"
                   for m in rules.METHOD.findall(a.get("label") or "")}
        return len(methods) >= 2 and not self.method_chosen() and not rules.METHOD.search(self.state["goal"])

    def unchosen_card(self, field, page):
        """Whether a card-detail box (card number, CVV, expiry) sits on a page that offers other ways to pay too, while
        the user hasn't said how to pay, in the chat or the goal: a saved card shown pre-selected is not their pick."""
        offered = " ".join([page["text"], *(a.get("label") or "" for a in page["actions"])])
        methods = {m.casefold() for m in rules.METHOD.findall(offered)}
        return bool(rules.CARD_DETAIL.search(field.get("label") or "")) and len(methods) >= 2 and \
            not self.method_chosen() and not rules.METHOD.search(self.state["goal"])

    def read(self, message, options, answer):
        """The user's reply as the chat keeps it: their words, and the option Jev reads them as."""
        s = self.state
        line = f"“{answer}”"
        if options and answer:
            pick = jev.read_reply(message, options, answer)
            if pick != "other":
                return line + f" → option {pick}: {options[int(pick) - 1]}"
        if HERE.search(answer) and PLACE.search(message):
            return line + (" → the user's own location: click the empty location box and pick the suggestion marked as "
                           "their own location (“Your current location”, “Your location”); a detect button already "
                           "pressed isn't pressed again")
        if DELEGATE.search(answer) and PLACE.search(message):  # "you choose, just not the office" leaves it too
            return line + (" → a delivery or pickup place is never chosen for them: use the site's detect-location "
                           "option, or ASK which saved address (use it only if exactly one exists); if neither works, "
                           "STOP and say you need their address")
        if delegates(answer) or (options and ANY_OF.match(answer) and not NEGATION.search(answer)):
            s["delegated_at"] = len(s["history"])
            return line + (" → the user leaves this choice to you: pick the option that best fits the goal, TELL "
                           "them which, and carry on")
        return line + (" → none of the options" if options else "")

    def finish(self, act, message, page):
        """DONE: a quote from the page backs the report, and Jev must see the goal done on the page."""
        s = self.state
        if s["commit"]:
            raise Retry("A committed action is still being checked; WAIT first.")
        missing = ""  # a download that never came, said in the report of a task that committed something first
        if rules.download_goal(s["goal"]) and not s["files"]:
            self.settle(DOWNLOAD_WAIT)  # a download still in flight arrives first
        if rules.download_goal(s["goal"]) and not s["files"]:
            s["file_refusals"] += 1
            opened = any(DOWNLOADS.search(f"{h.get('action') or ''} {h.get('text') or ''}") for h in s["history"]
                         if h.get("operation") in {"CLICK", "DOUBLE_CLICK", "LOOK", "PRESS_KEY", "OPEN_URL"})
            nothing = ("it may have opened in the browser, but no file was saved, so nothing reached you" if opened
                       else "no file was saved, because the document's download link was never opened")
            if s["file_refusals"] <= FILE_REFUSALS:
                raise Retry("No file has been saved yet, and this goal needs the file itself: "
                            + ("press the document's download link or button" if opened else
                               "open the document's download link first")
                            + " (one shown in the browser is saved by itself), then DONE once files_saved lists it. "
                              "If the page shows there is no such document, STOP and say so.")
            if not any(h.get("committed") for h in s["history"]):
                raise Halt("blocked", f"The document didn't download from {rules.host(page['url'])}: {nothing}. "
                                      "Please save it from the agent's Chrome window, or ask me again.")
            missing = f" The document didn't download, though: {nothing}."
        texts, quote = [page["text"], *reversed(s["recent"]), *s["last_by_site"].values()], act["text"].strip()
        if not message and quote and not any(rules.verbatim(quote, text) for text in texts):
            message, quote = quote, ""  # the report written where the quote goes: it is the report
        quote = self.evidence(quote, message, texts)
        report = (message or "Done.") + missing
        cards, lines = page.get("cards") or [], page.get("card_lines") or []
        if lines and rules.lists_mandates(s["goal"], cards):  # the cards' own words: the planner made one up
            report = "Your UPI AutoPay mandates:\n" + "\n".join(f"• {line}" for line in lines)
        named = [line for card, line in zip(cards, lines)
                 if not rules.wrong_card(s["goal"], {"label": f"Cancel (in “{card}”)"}, self.payee_cards(page))]
        if len(named) == 1 and (rules.MANDATE_GOAL.search(s["goal"]) or self.payee_cards(page)) \
                and not rules.MANDATE_ACTION.search(s["goal"]):
            report = f"Your UPI AutoPay mandate for it: {named[0]}"  # its card's words (“every month” was Custom)
        missing = [card for card in cards if card.lower() not in report.lower()]
        if missing and rules.lists_mandates(s["goal"], cards):  # MyUPI: “two active …” with three on the list
            s["list_refusals"] += 1
            if s["list_refusals"] <= 1:
                raise Retry(f"Your answer leaves out {', '.join(missing)}: the list has {len(cards)} mandates "
                            f"({', '.join(cards)}). Scroll to their cards and name each one.")
            report += f" All your UPI AutoPay mandates: {', '.join(cards)}."
        # a lookup may read its answer across pages (a comparison of two sites): Jev also sees those pages
        before = self.pages_before(page) if rules.lookup_goal(s["goal"], s["skill"]) else []
        if jev.achieved(page, s["goal"], report, before=before) != "yes":
            s["done_refusals"] += 1
            committed = any(h.get("committed") for h in s["history"])
            if s["done_refusals"] < 3 or committed or rules.commerce_goal(s["goal"]):
                raise Retry("The page doesn't show the goal done yet. Keep going until it does; the answer goes in "
                            "DONE, never in a TELL.")
            report += " (I couldn't fully confirm this on the page, so please double-check it.)"
        if s["held"] and repeats(report, s["held"]):
            s["held"] = None  # the answer, told a step early: it goes out once, here
        self.say(report, kind="result", evidence=quote)
        s["status"] = "done"
        return None

    def evidence(self, quote, report, texts):
        """A quote proving the report, from the current page or one seen just before: the planner's own when it is
        there, else the line that shares the most prices and numbers with the report."""
        if quote and any(rules.verbatim(quote, text) for text in texts):
            return quote
        wanted = set(rules.PRICE.findall(report)) | set(re.findall(r"\d[\d:,.]*\d|\d", report))
        lines = [line.strip() for text in texts for line in text.splitlines() if line.strip()]
        scored = sorted(((sum(w in line for w in wanted), line) for line in lines), key=lambda x: -x[0])
        if scored and scored[0][0] and len(scored[0][1]) <= 200:
            return scored[0][1]
        hints = "; ".join(f"“{line[:80]}”" for n, line in scored[:3] if n) or "none found"
        raise Retry("DONE needs a quote in text: a short piece of the page text copied exactly, proving it. Lines "
                    f"that look relevant: {hints}. If the site refused the task (an error it showed), STOP with why "
                    "instead.")

    # ---- acting

    def act(self, operation, action, page, sub, key=None, text=None, paid=False):
        """One operation through the gate: a commit runs only with an approval or a valid pre-authorisation. `paid`:
        the bank's one-time password sent for the payment the user said yes to, a commit their yes covers."""
        if operation == "CLICK":
            action = self.card_add(action, sub)
            if action.get("picture") and action.get("role") != "link" and CHOOSES.search((sub or {}).get("do") or ""):
                raise Retry(f"“{action.get('label')}” is a photo in the item's picture gallery: clicking it only shows "
                            "that photo and chooses nothing. Choose with the page's own control for it (the colour or "
                            "size swatches, or the control naming the current choice), or LOOK for it.")
            label, goal = action.get("label") or "", self.state["goal"]
            if POLICY.match(label) and not POLICY.search(goal) and not (
                    label.lower().startswith("faq") and QUESTION.match(goal)):
                raise Retry(f"“{label}” only opens the site's policy or help text, which isn't needed for this task: "
                            "go on with the task itself.")  # MyUPI: its Terms, Privacy and FAQs opened again and again
            buying = voucher_order(goal)
            if buying and max(buying.values()) == 1 and label.lower().startswith("increase quantity"):
                raise Retry("Each voucher in the goal is one: never raise a quantity. Go on with the task itself.")
            note = (skills.site_for(page["url"], self.library["sites"]) if page.get("url") else None) or {}
            never = [w.strip().lower() for w in str(note.get("never") or "").split(",") if w.strip()]
            if any(label.strip().lower().startswith(w) for w in never) and not (  # unticking one is no step on
                    action.get("role") == "checkbox" and action.get("checked") == "true"):  # live: Redeem Now
                raise Retry(f"“{label}” is never part of a task on this site (its note says so): go on with the task "
                            "itself.")
            if action.get("role") == "checkbox" and action.get("checked") == "true" and \
                    AGREE.search(action.get("label") or ""):  # MyUPI: ticked and unticked five times over
                raise Retry(f"“{action['label']}” is already ticked: a second click unticks it. Go on to the next "
                            "step.")
            if STRIP.search(action.get("label") or "") and not action.get("within"):
                raise Retry(f"“{action.get('label')}” is a strip of choices: clicking it lands on its middle. PICK it "
                            "with the words of the one to click, like PICK [k] with text 2.")
            drift = self.other_item(action, page)
            if drift:
                raise Retry(drift)
            payee = rules.wrong_card(self.state["goal"], action, self.payee_cards(page))
            if payee:
                self.state["wrong_cards"] += 1
                if self.state["wrong_cards"] >= 2 and self.no_card_named(page):
                    return self.no_mandate(page)
                raise Retry(f"“{action.get('label')}” is on {payee}'s card, and the goal doesn't name {payee}: press "
                            "only the card whose payee is the merchant the goal names, or the company that bills for "
                            "it. When no card's is, DONE saying so and list every payee; press nothing.")
            extra = self.narrows(action)
            if extra:
                raise Retry(f"“{action.get('label')}” adds {', '.join(sorted(extra))} to the search, which the user "
                            "never said: press Enter in the search box or its search button to search their own "
                            "words.")
        inputs, shown = self.inputs(operation, action, page, sub, key, text)
        self.state["cleared"] = None
        label = gate.commit_label(self, operation, action, page, inputs.get("key"), sub)
        cleared = self.state.pop("cleared", None)  # a maybe-commit Jev was sure is a step: the bench audits it
        if label and self.unpicked_method(page):
            raise Retry(PICK_METHOD)  # the final control beside a payment selector: the user picks how to pay first
        if paid:  # watched like the payment it completes, and named so to the user
            label = "Payment"
            gate.bind(self.state, action, label, "", page, "user")
        elif label:
            gate.approved(self, page, action, label, sub)  # returns True, or raises StalePage or Retry
        ran = self.run(page, operation, action, inputs, shown, commit=label, goal=(sub or {}).get("do", ""),
                       cleared=cleared)
        card = rules.CARD_ACTION.match(action.get("label") or "") if operation == "CLICK" else None
        if card and MANDATE_CANCEL.match(action["label"]) and (rules.MANDATE_GOAL.search(self.state["goal"])
                                                                or self.payee_cards(page)):
            return self.hand_off(card[1])  # the rest is the user's, in their UPI app
        return ran

    def payee_cards(self, page):
        """Whether the page's site names a card by its payee and every action on one must be the user's (its note's
        `cards: payee`: MyUPI's mandates), however the goal is worded."""
        note = skills.site_for(page.get("url") or "", self.library["sites"]) if page.get("url") else None
        return (note or {}).get("cards") == "payee"

    def no_card_named(self, page):
        """A cancel, pause, or resume of a mandate that none of the page's cards is for (MyUPI: Netflix), judged on the
        whole list only: two cards or more, read off the page whether on screen or not. One mandate's own panel says
        nothing of the rest (“cancel my aisle” was answered “no mandate” with GOOGLE PLAY's card alone read)."""
        cards, goal = page.get("cards") or [], self.state["goal"]
        return len(cards) >= 2 and bool(rules.MANDATE_ACTION.search(goal)) and all(
            rules.wrong_card(goal, {"label": f"Cancel (in “{card}”)"}, self.payee_cards(page)) for card in cards)

    def no_mandate(self, page):
        """The goal's merchant has none of the user's mandates: the task ends saying so, with every payee on the list
        and nothing pressed (MyUPI: the planner insisted “Netflix billed by OPENAI LLC”, or listed only the cards on
        screen)."""
        s = self.state
        s["held"] = None  # the planner's own word on it, held a step: this answer says it
        self.say(f"None of your UPI AutoPay mandates is for what you asked (“{s['goal']}”), so nothing was changed. "
                 f"Your mandates: {', '.join(page['cards'])}.", kind="result")
        s["status"], s["outcome"] = "done", "not_found"

    def mandate_cancel_pressed(self):
        """The payee whose card's Cancel this task pressed, on a goal about the user's mandates, else None."""
        if not (rules.MANDATE_GOAL.search(self.state["goal"]) or self.payee_cards(self.state["page"] or {})):
            return None
        for step in reversed(self.state["history"]):
            card = rules.CARD_ACTION.match(step.get("action") or "") if step.get("operation") == "CLICK" else None
            if card and MANDATE_CANCEL.match(step["action"]):
                return card[1]
        return None

    def hand_off(self, payee):
        """After a mandate's Cancel, MyUPI shows a QR that only the user's UPI app approves, with their UPI PIN: the
        worker sends it with two buttons and ends the task on the answer. Left to the planner, it hunted past the QR
        for a final button and pressed the chat box's Send."""
        s = self.state
        qr = None
        for _ in range(QR_TRIES):  # the QR renders a moment after the panel
            qr = s["browser"].qr_screenshot(masked=self.secrets.nodes, hide=self.hidden_details())
            if qr:
                break
            time.sleep(1)
        where = "scan this QR with your UPI app" if qr else "scan the QR in the agent's MyUPI window with your UPI app"
        reply = self.ask({"kind": "choice", "image": qr,
                          "text": f"To cancel {payee}'s UPI AutoPay, {where} and approve it there with your UPI PIN. "
                                  "Nothing changes until you do.",
                          "options": [{"id": "o1", "label": "I approved it"}, {"id": "o2", "label": "Not now"}]})
        approved = reply.get("option") == "o1" or bool(APPROVED.match(str(reply.get("text") or "")))
        self.say(f"Done: you approved cancelling {payee}'s UPI AutoPay in your UPI app, so its future payments stop. "
                 "NPCI's list can take a few minutes to show it, and the account with the merchant itself isn't "
                 "closed." if approved else f"Nothing was changed: {payee}'s UPI AutoPay stays active.", kind="result")
        s["status"], s["outcome"] = "done", "approved" if approved else "not_approved"

    def inputs(self, operation, action, page, sub, key=None, text=None):
        """(browser inputs, what the history shows) for one operation."""
        if operation == "TYPE_TEXT":
            return self.value(action, text if text is not None else self.value_for(action, sub), page)
        if operation == "PRESS_KEY":
            return {"key": key or "Enter"}, None
        if operation == "LOOK":
            return {"point": action["point"], **({"hover": True} if action.get("hover") else {})}, action.get("label")
        if operation == "UPLOAD":
            if self.payee_cards(page) and not PROOF.search(self.state["goal"]):  # MyUPI: asked for a file to cancel
                raise Retry("This attach-file belongs to the site's chat box, which this task never uses: on this "
                            "site a file is only a complaint's proof the user gave.")
            return {"files": self.files_for(action)}, None
        return {}, None

    def subgoal_value(self, action, sub):
        """The subgoal's text for a field: by the field's name, the only value, or Jev's pick among the names; else
        None."""
        values = (sub or {}).get("values") or []
        text = executor.value(action, values)
        if text is None and len(values) > 1:
            name = jev.value_for(action["label"], [v["field"] for v in values])
            text = next((v["text"] for v in values if v["field"] == name), None)
        return text

    def value_for(self, action, sub):
        """The subgoal's text for the field Jev chose. With none, the planner writes it: the field becomes its
        candidate 1, and Jev's other candidates stay listed after it."""
        text = self.subgoal_value(action, sub)
        if text is None:
            s, field = self.state, (action.get("frame"), action.get("node"))
            others = [c for c in s["candidates"] if not (c["operation"] == "TYPE_TEXT" and
                                                         (c["action"].get("frame"), c["action"].get("node")) == field)]
            s["candidates"] = [{"n": 1, "op": "TYPE", "operation": "TYPE_TEXT", "element": action["label"], "p": 1.0,
                                "action": action}] + [{**c, "n": n} for n, c in enumerate(others, 2)]
            raise Retry(f"Jev chose to type into “{action['label']}”, but the subgoal has no value for it: TYPE "
                        "pick 1 with the text, PICK 1 to only focus it (the site may then offer its own options), or "
                        "decide otherwise.")
        return text

    def value(self, action, text, page):
        """({"text": what to type}, what logs show). Placeholders become the user's values here, after every model
        call. A code, password, or CVV field takes only the user's hidden answer, once, within the attempt limit."""
        s = self.state
        if not str(text or "").strip():
            raise Retry(CLEARING)
        if secret_field(action):
            if self.unchosen_card(action, page):
                raise Retry(PICK_METHOD)
            found = re.fullmatch(r"\s*\[secret:(answer\d+)\]\s*", text)
            if not found or found[1] not in s["answers"]:
                raise Retry(f"“{action['label']}” takes a code or password: the system asks the user for it; type "
                            "only the placeholder of their answer ([secret:answerN]).")
            where = (rules.host(page["url"]), action["label"])
            typed = s["typed"].get(where) or {}
            if typed.get("key") == found[1]:
                raise Retry(f"That code ({text}) was already typed into “{action['label']}”. If the site rejected "
                            "it, the system asks the user for a new one.")
            count = typed.get("count", 0)
            if count >= (2 if rules.CARD_DETAIL.search(action["label"]) else 3):  # a CVV gets two tries, a code three
                raise Halt("blocked", f"The site rejected {count} codes for “{action['label']}”, so I stopped before "
                                      "your account gets locked.")
            code = rules.clean(s["answers"][found[1]], action)
            problem = rules.format_error(code, action)
            if problem:
                raise Retry(f"The user's answer doesn't fit “{action['label']}”: {problem} Ask them again.")
            s["typing"] = (where, {"key": found[1], "count": count + 1, "url": page["url"]})
            return {"text": code}, f"[secret:{found[1]}]"
        known = re.search(r"\[secret:(answer\d+)\]", text)
        if known and known[1] in s["answers"]:
            raise Retry(f"A code or password goes only into its code field; “{action['label']}” isn't one.")

        def real(match):
            kind, key = match[1], match[2]
            if kind == "profile":
                return self.profile_value(key, action)
            if key in s["answers"]:
                return s["answers"][key]
            raise Retry(f"[secret:{key}] isn't one of the user's answers.")

        typed = rules.fit_phone(PLACEHOLDER.sub(real, text), action)  # +91 98765 43210 in a 10-digit box: 9876543210
        limit = (action.get("constraints") or {}).get("maxlength")
        if limit and not action.get("group") and len(typed) > int(limit):  # the field would cut it short
            raise Retry(f"“{action['label']}” takes at most {limit} characters, so “{text}” would be cut short: type "
                        "a shorter text that still names what the site needs (the area or a landmark), or use another "
                        "field.")
        problem = None if PLACEHOLDER.search(text) else self.misfit(typed, action)  # the user's own values stand
        if problem:
            raise Retry(f"“{action['label']}” can't take “{text}”: {problem} Type what this field asks for, or use "
                        "another field.")
        return {"text": typed}, text

    @staticmethod
    def misfit(text, action):
        """Why text the planner wrote can't go into this field, or None: its format (a phone, email, date, number,
        or pattern field), or a field named for a phone number that the text is no phone number for."""
        problem = rules.format_error(text, action)
        label = action.get("label") or ""
        if problem is None and HERE.search(text) and (executor.words(label) & (executor.PLACE | executor.ROLES)
                                                       or SEARCHY.search(label)):
            problem = ("Typing words for the user's own location finds places named so. Press the site's detect "
                       "option, or click the empty box and pick the suggestion marked as their own location.")
        if problem is None and PROFILE_ASKS["phone"].search(label) and not re.search(r"e-?mail", label, re.I) \
                and not re.fullmatch(r"[\d\s+()-]{6,}", text):
            problem = "That doesn't look like a phone number."
        return problem

    def profile_value(self, key, action=None):
        """An ID for a placeholder: from this task or memory, else asked once through the hidden field and kept,
        encrypted. Its name comes from the known labels, the subgoal value holding the placeholder, or the field."""
        if key in self.state["ids"]:
            return self.state["ids"][key]
        try:
            return self.memory.id_value(key)
        except KeyError:
            pass
        if not KEY.match(key):
            raise Retry(f"[profile:{key}] isn't an ID name: use lowercase letters, digits, and underscores.")
        if set(key.split("_")) & executor.PLACE:  # addresses are memory's facts, never hidden IDs
            raise Retry(f"[profile:{key}]: a place is not an ID, and is never asked hidden. Type the place in words "
                        "(an address memory lists, or the user's own words), or use the site's detect-location "
                        "option; ASK where only when neither gives one.")
        values = (self.subgoal() or {}).get("values") or []
        label = (LABELS.get(key) or next((v["field"].strip() for v in values if f"[profile:{key}]" in v["text"]
                                          and v["field"].strip()), None)
                 or (action or {}).get("label") or key.replace("_", " "))
        label = " ".join(re.sub(r"[\x00-\x1f\x7f_]", " ", label).split())[:60] or key.replace("_", " ")
        if not storable(label):
            raise Retry(f"“{label}” is never kept or asked for this way: the system asks for codes and card details "
                        "itself.")
        reply = self.ask({"kind": "secret", "text": f"What's your {label}? It stays hidden from the models, and I'll "
                                                    "remember it for next time."})
        value = str(reply.get("text", "")).strip()
        if not value:
            raise Halt("blocked", f"I need your {label} to go on.")
        for hidden, placeholder in variants(key, value):
            self.secrets.add(hidden, placeholder)  # hidden in this task even when it can't be kept
        try:
            self.memory.set_id(key, label, value)
        except (Unstorable, StoreError, ValueError) as error:
            self.state["ids"][key] = value
            self.say(f"I'll use your {label} for this task only: {error}")
            return value
        self.say(f"Saved your {label}. The models never see it.")
        return value

    def files_for(self, action):
        """Files for an upload: ones the user sent in this task that no upload used yet, else asked for."""
        s = self.state
        if self.provide_file:
            return self.provide_file(action)
        sent = [m["file"] for m in s["messages"] if m.get("from") == "user" and m.get("file")]
        unused = [f for f in sent if f not in s["uploaded"] and Path(f).is_file()]
        if not unused:
            name = action["label"].removeprefix("File input: ").removeprefix("Drop zone: ")
            reply = self.ask({"kind": "file", "text": f"This step needs a file ({name}). Send it here."})
            if not reply.get("file") or not Path(reply["file"]).is_file():
                raise Halt("blocked", "No file arrived, so nothing was attached.")
            unused = [reply["file"]]
        s["uploaded"] += unused
        return unused

    def control(self, page, operation):
        _, _, controls = action_space(page["actions"])
        if operation in controls:
            return controls[operation]
        if operation == "GO_BACK":
            raise Retry("There is no earlier page to go back to.")
        if operation in {"ACCEPT_DIALOG", "DISMISS_DIALOG"}:
            raise Retry("No browser dialog is open.")
        return {"id": operation.lower(), "kind": operation.lower(), "node": None, "label": operation.title()}

    def run(self, page, operation, action, inputs, shown, commit=None, goal="", cleared=None):
        """Execute one operation, record it, and read the page again; a commit is then watched. `cleared` is a
        maybe-commit Jev cleared as a step: it is recorded once the action has run (or may have)."""
        s = self.state
        self.stop_if_interrupted()
        label = action.get("label") or operation
        entry = {"step": len(s["history"]) + 1, "subgoal": s["current"], "operation": operation, "action": label,
                 "title": page.get("title", ""), "relayed": s["relaying"],
                 "kind": action.get("kind"), "text": shown, "key": inputs.get("key"),
                 "files": [self.file_name(f) for f in inputs["files"]] if "files" in inputs else None,
                 "page_changed": None, "url": page["url"], "committed": bool(commit),
                 "approved": (s["approval"] or {}).get("by") if commit else None, "spot": views.signature(page),
                 "own": gate.pressed(operation, action, inputs.get("key")), "cleared": cleared}  # for the audit
        try:
            try:
                s["browser"].act(operation, action, page, **inputs)
            except StalePage:  # nothing ran: on a page whose text alone moved, the decision still holds
                if commit or cleared or s["relaying"]:
                    raise  # a commit runs only on the page the user approved, a step Jev cleared only on the page it
                    # judged (decided again, through the gate), a captcha square only as picked
                again = self.observe()
                same = self.still(page, again, operation, action)
                if same is None or self.may_commit(operation, same, again, inputs.get("key")):
                    raise  # a control whose words may commit is decided again, through the gate
                page, action = again, same
                entry.update(title=page.get("title", ""), url=page["url"])
                s["browser"].act(operation, action, page, **inputs)
        except ExecutionUncertain as error:
            s["history"].append({**entry, "unconfirmed": str(error), "elapsed_ms": self.elapsed()})
            if cleared:
                s["cleared_maybe"].append(cleared)
            raise Halt("blocked", f"I may already have done “{label}” but can't confirm it, so I stopped instead of "
                                  "repeating it. Please check before trying again.") from error
        s["elapsed_ms"] = self.elapsed()
        s["history"].append({**entry, "executed_ms": s["elapsed_ms"], "elapsed_ms": s["elapsed_ms"]})
        if cleared:
            s["cleared_maybe"].append(cleared)
        s["subgoal_steps"] += 1
        self.replay_moved(operation, action)  # the step ran: replay moves past it
        s["candidates"], s["refusals"], s["idle"], s["idle_asked"] = [], {}, 0, 0
        typing = s.pop("typing", None)
        if typing:
            s["typed"][typing[0]] = typing[1]
            s.setdefault("typed_keys", []).append(typing[1]["key"])  # had by a field, though the field may go
            if not rules.CARD_DETAIL.search(typing[0][1]):
                self.count_code(typing[1]["key"])  # a code or password typed counts, once, however it was asked
        if commit:
            approval = s["approval"] or {}
            s["approval"] = None
            s["commit"] = {"label": commit, "since": s["elapsed_ms"], "goal": goal or s["goal"],
                           "site": skills.bare(approval.get("host", rules.host(page["url"])).lower()),
                           "cart": approval.get("cart") or [], "total": approval.get("amount") or "",
                           "by": approval.get("by")}
        if shown and str(shown).startswith("[secret:"):
            for node in action.get("group") or [action.get("node")]:
                self.secrets.nodes.add((action.get("frame"), node))
        saved = len(s["files"])
        after = self.observe()
        for _ in range(3):  # a dropdown or dialog can render a moment after the click: look again before "nothing"
            if operation == "WAIT" or views.change(page, after) != "nothing changed" or self.expected_on(after):
                break
            time.sleep(self.quiet_wait / 3)
            after = self.observe()
        if operation == "TYPE_TEXT" and self.suggests(action):
            after = self.suggestions(page, after)
        last = s["history"][-1]
        changed = views.change(page, after)
        if operation == "OPEN_URL" and inputs.get("url") and not self.landed(inputs["url"], after["url"]):
            moved = f"OPEN_URL to {inputs['url']} ended at {after['url']} (redirected)"
            changed = f"{moved}; {changed}"
            s["notes"].append(moved)
            if self.landed(page["url"], after["url"]) and urlparse(page["url"]).path.strip("/"):
                # sent back to the page it was opened from (not the site's home, a fallback for a missing location
                # or sign-in): the site has nothing at that address for this account
                s["feedback"].append(f"OPEN_URL to {inputs['url']} led back to the page it was opened from "
                                     f"({after['url']}): that address has nothing for this account. Don't open it "
                                     "again; go on from what this page shows, or DONE saying what isn't there.")
                s["reason"] = s["reason"] or "unsure"
        last.update(page_changed=after["fingerprint"] != page["fingerprint"], url=after["url"], changed=changed,
                    after_spot=views.signature(after))
        if operation == "TYPE_TEXT" and self.reads_back(action, after, inputs.get("text"), shown):
            last.update(effect="worked", typed_ok=True)  # the field shows it: Jev's later look can't undo that
        if len(s["files"]) > saved and rules.download_goal(s["goal"]):  # the document the goal asks for came
            s["feedback"].append(f"“{self.file_name(s['files'][-1])}” was saved for the user (files_saved): the "
                                 "download is done; finish with DONE, naming the file.")
            s["reason"] = s["reason"] or "next"
        finals = [] if commit else self.revealed(page, after, self.controls_seen.get(self.place_of(page), ()))
        if finals:  # seats picked on a canvas, a cart filled: the page may now be ready for its last step
            s["feedback"].append(f"“{finals[0]}” came up after this step: the choices on this page may be done; "
                                 "check them, then go on to it.")
        s["status"] = "ready"
        if s["commit"]:
            return gate.watch(self, s["commit"]["label"], s["commit"].get("goal") or s["goal"])
        return self.track_loop(last)

    def may_commit(self, operation, action, page, key=None):
        """Whether pressing this control on this page may commit, as the gate reads its words before Jev's check: the
        control's own words, or for a key pressed on the page, the focused control or the form an Enter submits."""
        page_key = operation == "PRESS_KEY" and action.get("node") is None
        focused = self.state["browser"].focused_label() if page_key else ""
        if rules.commit_label(operation, action, page, key, focused) or \
                rules.maybe_commit_label(operation, action, page, key, focused):
            return True
        return page_key and key == "Enter" and gate.form_submit(
            page, focused, lambda words: bool(rules.COMMIT.search(words)) or rules.maybe_commits(words)) is not None

    @staticmethod
    def still(page, again, operation, action):
        """The action decided on `page`, on a fresh read `again`: the same control when only the text moved (a
        rotating placeholder, a carousel, a timer) and the controls are as they were (views.signature), else None. A
        control with a node must still be there under the same name, own name, and shown words (the quoted words the
        signature drops: “View cart” turned “Place order”); a spot found by looking is never carried over."""
        if operation in {"OPEN_URL", "WAIT"} and action.get("node") is None:
            return action  # an address to open, or a wait, never depends on what the page shows
        if operation == "LOOK" or views.signature(page) != views.signature(again):
            return None
        if action.get("node") is None:
            return action
        name = views.calm(action.get("label"))
        return next((a for a in again["actions"] if (a.get("frame"), a.get("node"), a.get("kind")) ==
                     (action.get("frame"), action.get("node"), action.get("kind"))
                     and views.calm(a.get("label")) == name and a.get("value") == action.get("value")
                     and a.get("name") == action.get("name") and a.get("shown") == action.get("shown")), None)

    @staticmethod
    def suggests(action):
        """Whether a field offers suggestions as it is typed into: a place, an address, or a search."""
        words = executor.words(action.get("label", ""))
        return action.get("role") in {"combobox", "searchbox"} or bool(
            words & (executor.PLACE | executor.ROLES | {"search", "landmark", "destination"}))

    def suggestions(self, page, after):
        """The page read again until the controls a typed place or search brings (its suggestions) appear, up to
        suggest_wait seconds: they often come from the network a second or two after the text."""
        clicks = lambda p: {a.get("label") for a in p["actions"] if a.get("kind") == "click"}  # noqa: E731
        before, deadline = clicks(page), time.monotonic() + self.suggest_wait
        while not (clicks(after) - before) and time.monotonic() < deadline:
            time.sleep(min(0.5, self.suggest_wait / 3))
            after = self.observe()
        return after

    @staticmethod
    def revealed(before, after, seen=()):
        """The final controls (ones the commit rule names outright) a step brought onto the same page, never read on
        it before (`seen`): a dialog closing shows the page's own "Book tickets" again, which is no news."""
        if urlparse(before["url"]).path != urlparse(after["url"]).path:
            return []  # a new page: its controls are all new, and Jev reads it afresh
        old = {a.get("label") for a in before["actions"]} | set(seen)
        return [a["label"] for a in after["actions"] if a.get("kind") == "click" and a.get("label") not in old
                and not a["label"].rstrip().endswith("?")  # a question ("How do I buy …?") is help, no last step
                and rules.commit_label("CLICK", a, after)][:2]

    @staticmethod
    def landed(wanted, url):
        """Whether an address opened ended where it was meant to: the same host and path (a "www." or a closing
        slash aside)."""
        def place(address):
            parts = urlparse(address)
            return skills.bare(parts.netloc.lower()), parts.path.rstrip("/")

        return place(wanted) == place(url)

    @staticmethod
    def reads_back(action, after, typed, shown):
        """Whether the field typed into (the same frame and node, read again) now holds what was typed."""
        same = (action.get("frame"), action.get("node"))
        field = next((a for a in after["actions"] if a.get("kind") == "fill" and a.get("node") is not None
                      and (a.get("frame"), a.get("node")) == same), None)
        value = str((field or {}).get("value") or "").strip()
        return bool(value) and value in {str(typed or "").strip(), str(shown or "").strip()}

    def track_loop(self, last):
        """The same step going round: run CIRCLE_ASK times from the same page (whatever each run changed), STUCK_AT
        times on one path changing nothing, STUCK_AT times on one path whatever each run changed (a menu toggling),
        VISITS steps from one page state however far apart, or a step undone and done again UNDOS times (A→B→A). The
        user is asked (loop_stuck). Every step also spends its page address's budget (path_budget), waits and scrolls
        included."""
        s = self.state
        if s["relaying"]:
            return None
        self.path_budget(last)
        if last["operation"] in QUIET:
            return None
        history = s["history"][s["loop_from"]:]
        text = last.get("text") if last["operation"] in TYPED else None
        spot = last.get("spot")
        circles = executor.circles(history, last["operation"], last["action"], spot, text)
        repeats = 0 if last["page_changed"] else executor.repeats(history, last["operation"], last["action"],
                                                                  last["url"])
        visits = executor.visits(history, spot) if spot else 0
        undos = executor.undos(history) if spot else 0
        if circles >= CIRCLE_ASK:
            key = f"{last['operation']}|{last['action']}|{text or ''}|{spot}"
            self.loop_stuck(key, f"circles {circles}: {key}")
        elif repeats >= STUCK_AT:
            key = f"{last['operation']}|{last['action']}|{urlparse(last['url']).path}"
            self.loop_stuck(key, f"repeats {repeats}: {key}")
        elif (pressed := executor.presses(history, last["operation"], last["action"], last["url"])) >= STUCK_AT:
            key = f"{last['operation']}|{last['action']}|{urlparse(last['url']).path}"
            self.loop_stuck(key, f"presses {pressed}: {key}")  # the same press, whatever the page did in between
        elif visits >= VISITS:
            self.loop_stuck(f"visits|{spot}", f"visits {visits}: {spot}")
        elif undos >= UNDOS:
            self.loop_stuck(f"undos|{spot}", f"undos {undos}: {spot}")
        return None

    def path_budget(self, last):
        """Steps on one page address (host and path), counted over the whole task: the user is asked once at
        PATH_ASK, and the task stops at PATH_HALT. Asking never resets the count, so a site that keeps its address
        while going round can't go round for ever."""
        s = self.state
        parts = urlparse(last.get("url") or "")
        key = f"{skills.bare(parts.netloc.lower())}{parts.path or '/'}"
        count = s["path_steps"][key] = s["path_steps"].get(key, 0) + 1
        if count >= PATH_HALT:
            raise self.circling(after_asking=key in s["path_asked"], why=f"path halt {count}: {key}")
        if count >= PATH_ASK and key not in s["path_asked"]:
            self.loop_stuck(f"path|{key}", f"path budget {count}: {key}")  # a page that moved while asking: asked
            # at the next step instead
            s["path_asked"].append(key)

    def forbidden(self, operation, label, page, text=None):
        """Why this action may not run from this page now, or None: it already ran CIRCLE_AT times from the same page
        (its URL path and its controls, views.signature), whatever each run changed; or it would be one more scroll
        after SCROLLS in a row on this page. Waits are never refused. `text` (typed, or the address opened) is part of
        the action when given."""
        s = self.state
        if s["relaying"]:
            return None
        history = s["history"][s["loop_from"]:]
        if operation in executor.SCROLLS:
            run = executor.scrolls(history, page["url"])
            return None if run < SCROLLS else (f"{run} scrolls in a row on this page found nothing to act on: stop "
                                               "scrolling. LOOK for the control, OPEN_URL the address you need, or "
                                               "ASK the user.")
        if operation in QUIET:
            return None
        count = executor.circles(history, operation, label, views.signature(page),
                                 text if operation in TYPED else None)
        inside = (" To press words inside it (its ADD, a size), PICK it with those words as text."
                  if operation == "CLICK" and len(label.split()) > 4 else "")  # a card's centre isn't its ADD
        return None if count < CIRCLE_AT else (
            f"“{label}” already ran {count} times from this same page without getting anywhere: don't repeat it. "
            f"Pick another candidate, LOOK, OPEN_URL the address you need, ASK the user, or rewrite the plan.{inside}")

    def guard(self, operation, label, page, text=None):
        """The loop guard on one of the planner's actions: a Retry (counted) when it may not run from this page again;
        True when its runs and refused tries reached CIRCLE_ASK and the user was asked instead (the turn is over)."""
        problem = self.forbidden(operation, label, page, text)
        if problem is None:
            return False
        if operation not in QUIET:
            s, text = self.state, text if operation in TYPED else None
            spot = views.signature(page)
            key = f"{operation}|{label}|{text or ''}|{spot}"
            s["circled"][key] = tries = s["circled"].get(key, 0) + 1
            runs = executor.circles(s["history"][s["loop_from"]:], operation, label, spot, text)
            if runs + tries >= CIRCLE_ASK:
                self.loop_stuck(key, f"guarded {runs} runs + {tries} refused: {key}")
                return True
        raise Retry(problem)

    def loop_stuck(self, key, why=None):
        """Ask the user about a loop the guards caught. A reply that helps starts a fresh count; a second reply that
        leaves it to the agent ("you choose") doesn't, so the loop can't go on for ever: once one loop was asked about
        LOOP_ASKS times, the next time it comes round the task stops. `why` (which guard, at what count) goes into the
        trace."""
        s = self.state
        why = why or key
        asked = s["loops"].get(key, 0)
        if asked >= LOOP_ASKS:
            raise self.circling(after_asking=True, why=f"asked {asked}x already: {why}")
        s["stuck_why"] = why  # recorded even when the page moves before the question
        self.steady(s["page"])
        s["loops"][key] = asked + 1
        answer = self.stuck(why)
        if not asked or not DELEGATE.search(answer):
            s["loop_from"], s["circled"] = len(s["history"]), {}

    def fruitless(self):
        """Whether FRUITLESS steps in a row, all on one page (host and path), were judged failed by Jev and left the
        page's controls as they were (views.signature): the planner hears it once a streak, with what to try
        instead. A scroll that shows new controls each time is still finding things."""
        s = self.state
        streak = []
        for h in reversed(s["history"]):
            if h.get("effect") != "failed" or h.get("spot") != h.get("after_spot") or (
                    streak and urlparse(h.get("url", ""))[1:3] != urlparse(streak[0].get("url", ""))[1:3]):
                break
            streak.append(h)
        start = len(s["history"]) - len(streak)  # where this streak began
        if len(streak) < FRUITLESS or s["fruitless"] == start:
            return False
        s["fruitless"] = start
        s["feedback"].append("Six steps in a row changed nothing useful here: try LOOK for the control, OPEN_URL the "
                             "target address, or ASK the user.")
        return True

    def look(self, page, purpose):
        """Find by sight what the element list can't name: one grid question, then a closer one inside the chosen
        cell. Mini answers with cell names only; code turns the cell into a point. None when nothing fits."""
        s = self.state
        hide = self.hidden_details()
        image, cells = s["browser"].grid_screenshot(masked=self.secrets.nodes, hide=hide)
        context = f"Goal: {s['goal']}\nStep: {(self.subgoal() or {}).get('do', '')}"
        found, _ = planner.look(context, page, purpose, image, cells)
        if found is None:
            return None
        close, finer = s["browser"].grid_screenshot(region=cells[found["cell"]], columns=4, rows=4,
                                                    masked=self.secrets.nodes, hide=hide, scale=2)
        cell, _ = planner.look_closer(found["label"], close, finer)
        return {"point": list(rules.centre(finer[cell])), "label": found["label"], "text": found.get("text", ""),
                "commits": found.get("commits", False)}

    # ---- the user

    def site_name(self, page):
        """The site as the user knows it: its note's name (“NPCI UPI Help”), else the page's title, else its host."""
        note = skills.site_for(page["url"], self.library["sites"])
        return note["name"] if note else page["title"] or rules.host(page["url"])

    def stuck(self, why="asked"):
        """Ask the user what to do; `why` (the guard that asked) goes into the trace."""
        s = self.state
        s["stuck_why"] = why
        self.log_refusal("stuck", why)
        sub = self.subgoal()
        title = self.site_name(s["page"])
        question = f"{STUCK} {title}, trying to {sub['do'] if sub else s['goal']}. What should I do?"
        coded = any(secret_field(a) for a in s["page"]["actions"])  # the reply may be a code or card detail: hide it
        reply = self.ask({"kind": "secret" if coded else "text", "text": question})
        answer = str(reply.get("text", "")).strip()
        s["idle_asked"] += 1
        s["chat"].append({"from": "agent", "text": question})
        if coded:
            s["chat"].append({"from": "user", "text": f"[secret:{self.keep_secret(answer)}] (hidden answer)"})
        else:
            s["chat"].append({"from": "user", "text": f"“{answer}”"})
        s["reason"] = "reply"
        return answer

    def steady(self, page):
        """Asking costs the user's time: right after a step, wait until steady_ms for the page to settle; a page
        that still changes is read again first."""
        last = self.state["history"][-1] if self.state["history"] else None
        while last and self.elapsed() - last.get("executed_ms", 0) < self.steady_ms:
            time.sleep(0.25)
            if self.moved(page):
                self.state["status"] = "ready"
                raise StalePage("The page changed while I was about to ask. Decide again.")

    def moved(self, page):
        """Whether the page's elements changed since it was read. Text that ticks (arrival times) doesn't count."""
        if self.state["browser"].fresh(page):
            return False
        return views.lines(self.observe()) != views.lines(page)

    def keep_secret(self, value):
        """Hold a secret answer for this task: usable by its name, never logged, and registered for redaction unless
        it is a word or a short reply ("yes", "pay", "use UPI"), which would hide those words on every page."""
        name = f"answer{len(self.state['answers']) + 1}"
        self.state["answers"][name] = value
        if not trivial(value):
            self.secrets.add(value, f"[secret:{name}]")
        return name

    def update(self, text):
        """A change the user sent for this task: the planner hears it next, and any approval is void."""
        s = self.state
        s["chat"].append({"from": "user", "text": f"(change) {text}"})
        s["approval"] = None
        s["reason"] = "reply"

    def stop_if_interrupted(self):
        """A cancel or a change from the user arrived while this step was decided: decide again with it."""
        if self.interrupted and self.interrupted():
            self.state["status"] = "ready"
            raise StalePage("The user cancelled or changed the task. Decide again.")

    def hidden_details(self):
        """Every ID value this task knows, for screenshots to blur where a page shows it: what memory holds now, and
        what this task's registry still hides (an ID kept for this task only, or forgotten while it ran)."""
        held = [value for value, _ in self.memory.hidden_values()]
        return held + [v for v in self.secrets.values_for("[profile:") if v not in held]

    # ---- records

    def trace(self):
        """The task as the bench and `jev traces` read it, redacted like everything that leaves the task."""
        s = self.state
        result = next((m for m in reversed(s["messages"]) if m.get("kind") == "result"), None)
        return self.secrets.redact({
            "goal": s["goal"], "title": s["title"], "status": s["status"], "skill": s["skill"],
            "preauth": s["preauth"], "elapsed_ms": s["elapsed_ms"], "waited_ms": s["waited_ms"],
            "result": {"text": result["text"], "evidence": result.get("evidence", "")} if result else None,
            "questions": sum(m["from"] == "agent" and m.get("kind") not in {"info", "result"} for m in s["messages"]),
            "messages": [{k: v for k, v in m.items() if k != "image"} for m in s["messages"]],
            "plan": s["plan"], "notes": s["notes"], "chat": s["chat"],
            "steps": [{k: h.get(k) for k in TRACE_STEP} for h in s["history"]],
            "jev": s["jev"], "turns": s["turns"], "meter": s["meter"], "escalations": s["escalations"],
            "margins": s["margins"], "replay": s["replay"], "files": [self.file_name(f) for f in s["files"]],
            "errors": s["errors"], "refusals": s["refusals_log"][-REFUSALS_KEPT:], "stuck_why": s["stuck_why"],
            "standing_error": s.get("standing_error"),
        })

    def save_trace(self):
        """Write the trace to ~/.jev/traces/<time>-<title>.json, readable only by the user."""
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        path = self.state.get("trace_path")
        if not path:
            slug = re.sub(r"[^a-z0-9]+", "-", self.state["title"].lower()).strip("-")[:40] or "task"
            path = unique(self.trace_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}.json")
            self.state["trace_path"] = str(path)
        path = Path(path)
        temporary = path.with_suffix(".tmp")
        temporary.touch(mode=0o600)
        temporary.write_text(json.dumps(self.trace(), indent=2, ensure_ascii=False, default=str))
        temporary.replace(path)  # a stop mid-write never leaves an empty or half-written trace
        return path

    def settle(self, timeout=10.0):
        """Before the tabs close: wait (bounded) for downloads still in flight and keep every one that arrived."""
        browser = self.state["browser"]
        deadline = time.monotonic() + timeout
        while browser.pending_downloads and time.monotonic() < deadline:
            time.sleep(0.1)
        arrived = browser.downloads[:]
        del browser.downloads[:len(arrived)]
        self.state["files"].extend(arrived)

    def close(self):
        """Write the final trace, then close the tabs. `closed` tells the bench the final trace is on disk."""
        try:
            self.state["trace_path"] = str(self.save_trace())
            self.state["closed"] = True
        except OSError as error:  # a trace is a record: never a reason to leave the tabs open
            print(f"jev: trace not saved: {error}", file=sys.stderr)
        finally:
            url = (self.state.get("page") or {}).get("url") or ""
            note = skills.site_for(url, self.library["sites"]) if url else None
            self.browser.close(keep=(note or {}).get("keep") in ("tab", "page"))  # MyUPI, Experian: signed in there

    def steps(self):
        """Tick until the task stops, yielding the state after each step (for scripts)."""
        while self.state["status"] not in STOPPED:
            yield self.command("tick")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
