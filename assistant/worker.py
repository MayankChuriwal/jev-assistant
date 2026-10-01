"""One task's runtime: its tab, its state, and the loop that joins System One (executor.py: Jev chooses each step)
and System Two (planner.py: mini plans, resolves what Jev is unsure about, and writes). The gate (gate.py) guards
every commit. The user is asked only for what they alone can give, and no model ever sees a secret."""

import html
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from . import executor, gate, jev, learn, planner, replay, rules, skills, views
from .browser import Browser, ExecutionUncertain, StalePage, unique
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
IDLE_HALT = 16  # ticks in a row with no browser step and no question to the user: ask at STUCK_AT, stop here
CODE_ASKS = 3
COMMIT_DROPS = 3  # an order's replan that drops its commit is refused this many times in a row, then taken
CAPTCHA_ROUNDS = 8  # a captcha can take several rounds of new images
QUIET = {"WAIT", "SCROLL_UP", "SCROLL_DOWN"}  # repeating these is not going in circles
TALKING = {"ASK", "TELL", "DONE", "STOP"}
TRACE_STEP = ("step", "subgoal", "operation", "action", "title", "text", "key", "files", "page_changed", "url",
              "changed", "effect", "committed", "approved", "elapsed_ms", "unconfirmed")
PLACEHOLDER = re.compile(r"\[(profile|secret):([\w.]+)\]")
CODE_WORDS = re.compile(r"\b(otp|passcode|password|cvv|pin(?![\s-]?code)|"  # a PIN code or promo code is no secret
                        r"(?<!pin )(?<!pin-)(?<!promo )(?<!zip )(?<!postal )code)\b", re.IGNORECASE)
DELEGATE = re.compile(r"\b(you (choose|decide|pick)|whatever (you|fits|works|is)|any (one|of them|is fine|will do)|"
                      r"up to you|your (choice|call)|doesn'?t matter)\b", re.IGNORECASE)
PLACE = re.compile(r"\b(address|deliver(y)? (to|location|address)|where (should|do you want|to deliver|are you)|"
                   r"pick ?-?up (location|point|address|from)|drop(-| )?off|drop location)\b", re.IGNORECASE)
PROFILE_ASKS = {"phone": re.compile(r"\b(mobile|phone|contact) number\b", re.IGNORECASE),
                "email": re.compile(r"\b(your e-?mail|e-?mail (address|id))\b", re.IGNORECASE),
                "upi_id": re.compile(r"\bupi id\b", re.IGNORECASE)}


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


def picked(answer, options):
    """The option a hidden reply names by code alone (its number, or its words exactly), else None."""
    text = answer.strip().rstrip(".").casefold()
    if re.fullmatch(r"[0-9]{1,2}", text) and 1 <= int(text) <= len(options):
        return int(text)
    return next((n for n, option in enumerate(options, 1) if option.strip().casefold() == text), None)


def has_commit(plan):
    return any(g["kind"] == "commit" for g in plan)


def plain(text):
    """Text as the user should read it: entities unescaped until none are left (some sites escape twice)."""
    while (unescaped := html.unescape(text)) != text:
        text = unescaped
    return text


class Worker:
    interrupted = None  # set by the chat: true once the user cancelled or changed the task during a step
    trace_dir = HOME / "traces"
    steady_ms = 3000  # a page still changing this soon after a step is read again before the user is asked
    processing_ms = 300000  # how long to watch a commit the site is still working on
    outcome_pause = 5.0  # seconds between looks at that commit
    quiet_wait = 1.5  # seconds to keep looking when a step seems to have changed nothing
    bench = False  # a bench run: a pre-authorisation is logged, never used

    def __init__(self, url, goal, *, asker=None, notify=None, memory=None, habit=None, sameness=None, browser=None,
                 title=None, skill=None, preauth=False, cap=None, library=None, trace_dir=None, provide_file=None):
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
            self.browser = browser or Browser(url, geolocation=self.memory.geolocation())
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
            commit_drops=0,
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
        self.release()
        message = {"from": "agent", **message}
        self.state["messages"].append(message)
        started = time.perf_counter()
        reply = self.asker(message) or {}
        self.state["waited_ms"] += round((time.perf_counter() - started) * 1000)
        if reply.get("cancel"):
            raise Halt("cancelled", "Cancelled. Nothing more will be done.")
        logged = {**reply, "text": "••••"} if message["kind"] == "secret" and "text" in reply else reply
        self.state["messages"].append({"from": "user", **logged})
        return reply

    def elapsed(self):
        started = self.state["started_at"]
        return round((time.perf_counter() - started) * 1000) if started else 0

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
        page = self.clean(self.state["browser"].observe())
        self.state["page"] = page
        self.state["files"].extend(page.get("downloads", []))
        self.forget_accepted(page)
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
            self.state["status"] = halt.status
            self.say(str(halt))
            return self.snapshot()
        finally:
            SECRETS.reset(secrets)
            METER.reset(meter)

    def tick(self):
        s = self.state
        if s["started_at"] is None:
            s["started_at"] = time.perf_counter()
        before, replies = len(s["history"]), self.replies()
        try:
            self.step()
            s["model_errors"] = 0
        except StalePage:
            s["status"] = "ready"
            self.observe()
        except Retry as why:
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
        s["elapsed_ms"] = self.elapsed()
        self.checkpoint()
        return self.snapshot()

    def replies(self):
        return sum(m["from"] == "user" for m in self.state["messages"])

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
            raise Halt("blocked", f"I'm going in circles on {rules.host(s['page']['url'])} and stopped. Tell me "
                                  f"more about what you want, or check the site yourself.{self.pending()}")
        if s["idle"] == STUCK_AT:
            self.stuck()

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
        page = s["page"] if s["browser"].fresh(s["page"]) else self.observe()
        if not s["recent"] or s["recent"][-1] != page["text"]:
            s["recent"] = (s["recent"] + [page["text"]])[-10:]  # facts may come from a page seen a moment ago
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
        label = step["action"].get("label") or step["operation"]
        count = executor.repeats(s["history"][s["loop_from"]:], step["operation"], label, page["url"])
        if count >= 2 and step["operation"] not in QUIET:  # changed nothing twice: Jev's next-best once, then mini
            key = [step["operation"], label, urlparse(page["url"]).path]
            other = next((c for c in step["candidates"] if c["action"].get("label") != label), None)
            if count >= 3 or key in s["switched"] or other is None:
                s["feedback"].append(f"“{label}” changed nothing {count} times, and Jev's next-best choice didn't "
                                     "help: try another way.")
                return self.escalate("unsure", page)
            s["switched"].append(key)
            return self.act(other["operation"], other["action"], page, sub, key=other.get("key"))
        return self.act(step["operation"], step["action"], page, sub, key=step.get("key"))

    # ---- habit replay

    def guided(self, step, answers, page, controls):
        """Replay's say on Jev's answers: (operation, action, key, text) to act on when Jev's top pick is the step the
        habit expects (passing the gates, or the only control on the page with that label unless it commits), else
        None to go on as in Phase A. Another pick that passes the gates, or none, takes this subgoal out of replay. A
        wait, a scroll, a secret field, an expected control not on the page yet, or one that changed nothing twice
        (the loop ladder decides) changes nothing."""
        s = self.state
        record = s["replay"]
        expected = replay.expected(self.habit, record, s["current"]) if record else None
        if not expected or expected["secret"]:
            return None
        commit = (self.subgoal() or {}).get("kind") == "commit"  # a commit is never forced past the gates
        verdict, chosen = replay.judge(expected, step, answers, page, controls, commit=commit)
        if verdict in {"absent", "quiet"}:
            return None
        if verdict == "matched" and chosen[0] not in QUIET and executor.repeats(
                s["history"][s["loop_from"]:], chosen[0], chosen[1].get("label") or chosen[0], page["url"]) >= 2:
            return None  # it changed nothing twice: the loop ladder decides, as in Phase A
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
        if not card and s["code_asks"] >= CODE_ASKS:  # each code usually means a new one sent to the user's phone
            raise Halt("blocked", f"Signing in to {rules.host(page['url'])} isn't working after {CODE_ASKS} codes, "
                                  "so I stopped rather than send you more. Please sign in once in the agent's "
                                  "Chrome window, then ask me again.")
        site = page["title"] or rules.host(page["url"])
        question = (f"{site} is asking for your card's {field['label']}. Type it here; it stays hidden." if card else
                    f"{site} is asking for a code or password. Type it here; it stays hidden.")
        self.steady(page)  # a page still switching screens gets read again first
        reply = self.ask({"kind": "secret", "text": question})
        if not card:
            s["code_asks"] += 1  # a code counts once it was asked: a question dropped for a changed page doesn't
        name = self.keep_secret(str(reply.get("text", "")).strip())
        s["chat"] += [{"from": "agent", "text": question}, {"from": "user", "text": f"[secret:{name}] (hidden answer)"}]
        inputs, shown = self.value(field, f"[secret:{name}]", page)
        return self.run(page, "TYPE_TEXT", field, inputs, shown, goal="type what the user sent")

    def relay_captcha(self, page):
        """A captcha only the user may solve: they see it with its squares numbered, and the worker clicks exactly
        the squares they name, or hands the text they read to the planner. Nothing is solved automatically."""
        s = self.state
        s["captcha_rounds"] += 1
        if s["captcha_rounds"] > CAPTCHA_ROUNDS:
            raise Halt("blocked", f"The site kept showing new checks after {CAPTCHA_ROUNDS} rounds, so I stopped. "
                                  "Please try this step on your own device.")
        clicks = [a for a in page["actions"] if a["kind"] == "click" and a.get("node") is not None]
        marks = ([a for a in clicks if a.get("frame")] or clicks)[:40]  # a captcha lives in its own frame
        image = s["browser"].marked_screenshot(marks, masked=self.secrets.nodes, hide=self.hidden_details())
        reply = self.ask({"kind": "image", "image": image, "text": "The site wants a check (captcha) solved. Reply "
                          "with the numbers of the squares to pick, like 2 5 8; or type the text it shows; or say "
                          "none if there is nothing to pick."})
        text = str(reply.get("text", "")).strip()
        joined = re.sub(r"\band\b", " ", text, flags=re.IGNORECASE)  # "2 and 5", "2, 5 & 7": squares; "4829": text
        numbers = [int(n) for n in re.findall(r"\d+", joined)] if re.fullmatch(r"[\d\s,&]+", joined) else []
        picked = list(dict.fromkeys(numbers)) if numbers and all(1 <= n <= len(marks) for n in numbers) else None
        if picked is None and text.lower() in {"none", ""}:
            s["notes"].append("The user says the captcha has nothing to pick: press its verify or skip button.")
        elif picked is None:
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
            self.stuck()

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
        s["reason"], s["feedback"], s["seen"] = None, [], page
        if reason == "replan":
            s["subgoal_steps"] = 0
        if out["note"].strip():
            s["notes"].append(out["note"].strip())
        subgoals = [{"do": g["do"].strip(), "done_when": g["done_when"].strip(), "kind": g["kind"],
                     "values": [v for v in g["values"] if v["field"].strip()]}
                    for g in out["subgoals"] if g["do"].strip()][:10]
        if subgoals:
            self.keeps_commit(subgoals)
            s["plan"] = s["plan"][:s["current"]] + subgoals
            s["subgoal_steps"] = 0
        if s["replay"] and (reason == "start" or subgoals):  # a plan of another shape, now or later: no replay
            s["replay"]["on"] = s["replay"]["on"] and replay.same_shape(s["plan"], self.habit["plan"])
        act = out["action"]
        if act["op"] not in {"DONE", "TELL"}:
            self.release()  # a TELL waits one step: when the next move is DONE with the same words, they go once
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
        """A replan of an order or booking that drops the commit subgoal the plan still had is refused, and the plan
        kept. After COMMIT_DROPS refusals in a row it is taken: the gate still guards every commit, and a plan that
        runs out is refused at CONTINUE until the user is asked."""
        s = self.state
        dropped = (self.unordered() and has_commit(s["plan"][s["current"]:])
                   and not has_commit(s["plan"][:s["current"]] + subgoals))
        if not dropped or s["commit_drops"] >= COMMIT_DROPS:
            s["commit_drops"] = 0
            return
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
            "plan": [{"n": n, **g, "current": n - 1 == s["current"]} for n, g in enumerate(s["plan"], 1)],
            "notes": s["notes"][-15:], "chat": s["chat"][-20:],
            "memory": self.memory.recall(s["skill"], skills.bare(rules.host(page["url"]).lower()), s["goal"]),
            "steps": self.recent_steps(8),
            "committed_and_waiting": s["commit"]["label"] if s["commit"] else None,
            "page": views.view(page, previous),
            "candidates": [{"n": c["n"], "op": c["op"], "element": c["element"], "p": round(c["p"], 2)}
                           for c in s["candidates"]],
            "reason": s["reason"], "feedback": s["feedback"],
            **({"habit": {"title": self.habit["title"], "goal": self.habit["goal"], "plan": self.habit["plan"]}}
               if s["replay"] and s["reason"] == "start" else {}),
        }

    def do(self, act, page):
        """The planner's browser actions: one of Jev's candidates, an address, WAIT, BACK, or a look."""
        s, op = self.state, act["op"]
        if op in {"PICK", "TYPE"}:
            chosen = next((c for c in s["candidates"] if str(c["n"]) == act["pick"].strip()), None)
            if chosen is None:
                listed = ", ".join(f"{c['n']} {c['element']}" for c in s["candidates"]) or "none"
                raise Retry(f"{op} names one of Jev's candidates by its number; there are {len(s['candidates'])} "
                            f"(candidates: {listed}).")
            if op == "TYPE" and chosen["operation"] != "TYPE_TEXT":
                raise Retry(f"Candidate {chosen['n']} is not a field to type into.")
            return self.act(chosen["operation"], chosen["action"], page, self.subgoal(), key=chosen.get("key"),
                            text=act["text"] if op == "TYPE" else None)
        if op == "OPEN_URL":
            url = act["text"].strip()
            parts = urlparse(url)
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise Retry("OPEN_URL needs a full http(s) address in text.")
            if not skills.template_host(url, skills.site_for(page["url"], self.library["sites"])):
                raise Retry("A search address built from the site's template must stay on that site.")
            return self.run(page, "OPEN_URL", self.control(page, "OPEN_URL"), {"url": url}, url)
        if op == "WAIT":
            return self.run(page, "WAIT", self.control(page, "WAIT"), {}, None)
        if op == "BACK":
            _, heads, controls = action_space(page["actions"])
            current = next((a for a in heads.get("tab_target", {}).values() if a.get("current")), None)
            if "GO_BACK" not in controls and current and len(heads["tab_target"]) > 1:
                return self.run(page, "CLOSE_TAB", current, {}, None)  # a new tab: back is its opener
            return self.run(page, "GO_BACK", self.control(page, "GO_BACK"), {}, None)
        what = act["text"].strip() or (self.subgoal() or {}).get("do") or "the control the task needs next"
        spot = self.look(page, what)  # LOOK
        if spot is None:
            raise Retry("Looking at the screen found nothing that fits. Try another way.")
        return self.act("LOOK", {**self.control(page, "LOOK"), **spot}, page, self.subgoal())

    def talk(self, out, page):
        """ASK, TELL, DONE, or STOP from the planner, its words checked against the universal rules first."""
        s = self.state
        act = out["action"]
        message = plain(out["message"].strip() or (act["text"].strip() if act["op"] != "DONE" else ""))
        op = "ASK" if act["op"] == "TELL" and message.endswith("?") else act["op"]  # a question waits for its answer
        options = [re.sub(r"^\d{1,2}[.)]\s+", "", plain(o.strip())) for o in out["options"] if o.strip()][:10]
        if op in {"ASK", "STOP"} and rules.captcha_challenge(page):
            return self.relay_captcha(page)  # only the user solves a captcha
        problem = planner.lint(op, message, options, s["chat"])
        if problem:
            raise Retry(problem)
        if op == "TELL":
            if not message:
                raise Retry("TELL needs a message.")
            if s["told_at"] == len(s["history"]):
                raise Retry("You just told the user something. Do an action next, or finish with DONE: the answer "
                            "the user asked for goes in DONE's message, never in another TELL.")
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
        if counts:
            s["code_asks"] += 1  # a code counts once it was asked: a question dropped for a changed page doesn't
        answer = str(reply.get("text", "")).strip()
        s["chat"].append({"from": "agent", "text": text})
        pick = picked(answer, options) if secret else None
        if pick:  # one of the offered options: not a secret, and the planner needs to read it
            s["chat"].append({"from": "user", "text": f"“{answer}” → option {pick}: {options[pick - 1]}"})
        elif secret:
            name = self.keep_secret(answer)
            s["chat"].append({"from": "user", "text": f"[secret:{name}] (hidden answer; type it as this placeholder)"})
        else:
            s["chat"].append({"from": "user", "text": self.read(message, options, answer)})
        s["reason"] = "reply"
        return None

    def read(self, message, options, answer):
        """The user's reply as the chat keeps it: their words, and the option Jev reads them as."""
        s = self.state
        line = f"“{answer}”"
        if options and answer:
            pick = jev.read_reply(message, options, answer)
            if pick != "other":
                return line + f" → option {pick}: {options[int(pick) - 1]}"
        if DELEGATE.search(answer) and PLACE.search(message):
            return line + (" → a delivery or pickup place is never chosen for them: use the site's detect-location "
                           "option, or ASK which saved address (use it only if exactly one exists); if neither works, "
                           "STOP and say you need their address")
        if DELEGATE.search(answer):
            s["delegated_at"] = len(s["history"])
            return line + (" → the user leaves this choice to you: pick the option that best fits the goal, TELL "
                           "them which, and carry on")
        return line + (" → none of the options" if options else "")

    def finish(self, act, message, page):
        """DONE: a quote from the page backs the report, and Jev must see the goal done on the page."""
        s = self.state
        if s["commit"]:
            raise Retry("A committed action is still being checked; WAIT first.")
        quote = self.evidence(act["text"].strip(), message, [page["text"], *reversed(s["recent"])])
        report = message or "Done."
        if jev.achieved(page, s["goal"], report) != "yes":
            s["done_refusals"] += 1
            committed = any(h.get("committed") for h in s["history"])
            if s["done_refusals"] < 3 or committed or rules.commerce_goal(s["goal"]):
                raise Retry("The page doesn't show the goal done yet. Keep going until it does; the answer goes in "
                            "DONE, never in a TELL.")
            report += " (I couldn't fully confirm this on the page, so please double-check it.)"
        held = set(re.findall(r"[a-z0-9]+", (s["held"] or "").lower()))
        said = set(re.findall(r"[a-z0-9]+", report.lower()))
        if held and said and len(held & said) / min(len(held), len(said)) >= 0.6:
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
                    f"that look relevant: {hints}.")

    # ---- acting

    def act(self, operation, action, page, sub, key=None, text=None):
        """One operation through the gate: a commit runs only with an approval or a valid pre-authorisation."""
        inputs, shown = self.inputs(operation, action, page, sub, key, text)
        self.state["cleared"] = None
        label = gate.commit_label(self, operation, action, page, inputs.get("key"), sub)
        cleared = self.state.pop("cleared", None)  # a maybe-commit Jev was sure is a step: the bench audits it
        if label:
            gate.approved(self, page, action, label, sub)  # returns True, or raises StalePage or Retry
        return self.run(page, operation, action, inputs, shown, commit=label, goal=(sub or {}).get("do", ""),
                        cleared=cleared)

    def inputs(self, operation, action, page, sub, key=None, text=None):
        """(browser inputs, what the history shows) for one operation."""
        if operation == "TYPE_TEXT":
            return self.value(action, text if text is not None else self.value_for(action, sub), page)
        if operation == "PRESS_KEY":
            return {"key": key or "Enter"}, None
        if operation == "LOOK":
            return {"point": action["point"], **({"hover": True} if action.get("hover") else {})}, action.get("label")
        if operation == "UPLOAD":
            return {"files": self.files_for(action)}, None
        return {}, None

    def value_for(self, action, sub):
        """The subgoal's text for the field Jev chose: by the field's name, the only value, or Jev's pick among the
        names. With none, the planner writes it (the field is its candidate 1)."""
        values = (sub or {}).get("values") or []
        text = executor.value(action, values)
        if text is None and len(values) > 1:
            name = jev.value_for(action["label"], [v["field"] for v in values])
            text = next(v["text"] for v in values if v["field"] == name)
        if text is None:
            self.state["candidates"] = [{"n": 1, "op": "TYPE", "operation": "TYPE_TEXT", "element": action["label"],
                                         "p": 1.0, "action": action}]
            raise Retry(f"Jev chose to type into “{action['label']}”, but the subgoal has no value for it: TYPE "
                        "pick 1 with the text, or decide otherwise.")
        return text

    def value(self, action, text, page):
        """({"text": what to type}, what logs show). Placeholders become the user's values here, after every model
        call. A code, password, or CVV field takes only the user's hidden answer, once, within the attempt limit."""
        s = self.state
        if not text:
            raise Retry("TYPE needs the text to type.")
        if secret_field(action):
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

        return {"text": PLACEHOLDER.sub(real, text)}, text

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
        values = (self.subgoal() or {}).get("values") or []
        label = (LABELS.get(key) or next((v["field"].strip() for v in values if f"[profile:{key}]" in v["text"]
                                          and v["field"].strip()), None)
                 or (action or {}).get("label") or key.replace("_", " "))
        label = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", label).split())[:60] or key.replace("_", " ")
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
                 "approved": (s["approval"] or {}).get("by") if commit else None}
        try:
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
        s["candidates"], s["refusals"], s["idle"] = [], {}, 0
        typing = s.pop("typing", None)
        if typing:
            s["typed"][typing[0]] = typing[1]
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
        after = self.observe()
        for _ in range(3):  # a dropdown or dialog can render a moment after the click: look again before "nothing"
            if operation == "WAIT" or views.change(page, after) != "nothing changed" or self.expected_on(after):
                break
            time.sleep(self.quiet_wait / 3)
            after = self.observe()
        last = s["history"][-1]
        last.update(page_changed=after["fingerprint"] != page["fingerprint"], url=after["url"],
                    changed=views.change(page, after))
        s["status"] = "ready"
        if s["commit"]:
            return gate.watch(self, s["commit"]["label"], s["commit"].get("goal") or s["goal"])
        return self.track_loop(last)

    def track_loop(self, last):
        """The same step on the same page changing nothing, again and again: the user is asked at STUCK_AT."""
        s = self.state
        if last["page_changed"] or last["operation"] in QUIET or s["relaying"]:
            return None
        if executor.repeats(s["history"][s["loop_from"]:], last["operation"], last["action"], last["url"]) >= STUCK_AT:
            self.steady(s["page"])
            self.stuck()
            s["loop_from"] = len(s["history"])  # the user's help starts a fresh count
        return None

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

    def stuck(self):
        s = self.state
        sub = self.subgoal()
        title = s["page"]["title"] or rules.host(s["page"]["url"])
        question = f"I'm stuck on {title}, trying to {sub['do'] if sub else s['goal']}. What should I do?"
        coded = any(secret_field(a) for a in s["page"]["actions"])  # the reply may be a code or card detail: hide it
        reply = self.ask({"kind": "secret" if coded else "text", "text": question})
        answer = str(reply.get("text", "")).strip()
        s["chat"].append({"from": "agent", "text": question})
        if coded:
            s["chat"].append({"from": "user", "text": f"[secret:{self.keep_secret(answer)}] (hidden answer)"})
        else:
            s["chat"].append({"from": "user", "text": f"“{answer}”"})
        s["reason"] = "reply"

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
            "errors": s["errors"],
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
            self.browser.close()

    def steps(self):
        """Tick until the task stops, yielding the state after each step (for scripts)."""
        while self.state["status"] not in STOPPED:
            yield self.command("tick")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
