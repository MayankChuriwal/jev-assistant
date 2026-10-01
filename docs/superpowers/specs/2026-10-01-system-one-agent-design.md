# Jev Assistant — System One agent (Phase A)

Date: 2026-10-01. Status: design approved in chat, section by section; this spec awaits the user's review.

This replaces the decision loop of [the mini + Jev design](2026-09-26-mini-jev-agent-design.md) and the legacy loop of
[the original design](2026-09-23-jev-assistant-design.md). The browser, snapshot, profile, secrets, channel, and
trace layers of those designs stay. The repo is a proof of concept: superseded code is deleted in the same plan, with
no flag that keeps the old loop runnable. The old loop's recorded bench results are the baseline.

## 1. Goal and decisions

The user asks for anything in chat. The agent does it in its own Chrome and asks only for what it cannot know or must
not decide alone. The product shape follows Instinct (a personal agent you text); the difference is the decider:
where Instinct runs a frontier model over the page every step, this agent lets Jev, a fast calibrated classifier,
choose every step, and calls GPT-4.1 mini only to plan, to resolve what Jev is unsure about, and to write.

Decided with the user on 2026-10-01:

| Topic | Decision |
|---|---|
| Model split | Jev chooses every browser step (operation + element) and judges done, blocked, and worked. Mini writes plan subgoals, typed values, and every word the user reads, and steps in only on low-margin or blocked steps. Code guards the invariants. |
| Know-how | Markdown skills (task kinds + situations) with per-site notes. Code keeps only universal invariants. The commerce regex rails of `driver.py` move into skill text or are deleted. |
| Autonomy | One approval per commit, with a summary whose facts come from code, bound to site and total, asked again if the total changes. Skipped only when the user's own message explicitly authorised the final step and code confirms items, site, and total ≤ the stated cap. Pre-authorisation ships in shadow mode: until the owner sets `JEV_PREAUTH=on`, a pre-authorised commit is asked about like any other (the question adds a line saying so); bench runs always behave this way. |
| Done bar | 24 known bench tasks over 10 categories, run twice, plus 8 unseen tasks per run from a pool no skill or site note is written for. Pass ≥ 80% known, ≥ 60% unseen, 0 unsafe actions, median ≤ 2 s per step, ≤ $0.02 per task. |
| Parallel | Up to 3 tasks at once, one per site. The concierge is the only voice to the user. Jev routes every message; mini splits multi-part requests. |
| Tools | Browser only. Skills may carry URL templates for search, filter, and sort. |
| Phone | Typed from the profile as `[profile:phone]`; asked through the hidden input when missing. OTP and CVV stay hidden-input answers. |
| Phases | A (this spec): System One core, local Mac. B: memory, learned site notes, replay. C: WhatsApp/iMessage, background tasks, TOTP and inbox codes, cloud browser (Section 14). |
| Build | Clean restructure in this repo: keep the proven layers, write new modules, delete the old loop and its rails. |

## 2. Evidence

**Our loop today** (127 traces and 72 bench runs, 2026-09-26 to 09-28):

- Bench 40/72 (56%), median 56 s and $0.044 per task. Lookups pass (Flipkart 7/7, flights 6/7, Myntra 5/6); sign-in
  sinks unattended runs (Zepto 0/5, Amazon 0/2, BookMyShow 0/2); Uber 9/15, Instamart 8/16.
- Mini is 66% of wall time: 4.0 s per executed action and 1.67 mini calls per action (rails refuse its steps).
- 85% of mini's decisions are plain browser operations (65% clicks). Jev alone was ≥ 0.9 confident on 57% of the
  elements it grounded and ≥ 0.4 on 82%.
- The earlier Jev-only loop (no planner) finished 1 of 15 traced tasks; its step decision took 479 ms against mini's
  1.67 s.
- `driver.py` holds about 40 commerce rules (fees, bill, UPI, ride options, delivery address, payment method) added
  one live failure at a time; none of them helps a new kind of task.

**Jev** (TypeSafe System One, released 2026-09-15; [flaviocopes.com/jev](https://flaviocopes.com/jev/)): question types
choice (≤ 255 options), score, and yes/no; text-only state up to about 32k tokens per question; 70–500 ms, about
100 ms typical; $0.042 per million input tokens, output free; 1,200 requests per minute; calibrated probabilities;
questions in one request are answered independently. Weak at math, counting, dates, double negatives, and large
irrelevant state. Option names outweigh their definitions ([arxiv 2609.26758](https://arxiv.org/html/2609.26758)).
Optimised page text flipped 61% of its decisions ([arxiv 2609.30243](https://arxiv.org/html/2609.30243)), so Jev is
never the only safety check.

**Planner plus grounder beats either alone:** an executor went from 9.85% to 44.24% on WebArena-Lite once given a
planner's step plans ([Plan-and-Act](https://arxiv.org/html/2503.09572v3)); grounding with a filtered candidate list
rose from 46.8% to 88.3% ([Prune4Web](https://arxiv.org/abs/2511.21398)); Jev driving the DOM alone solved 25/49
real-site tasks ([WindTunnel](https://webmcp.com/benchmark)). Extra compute gated on the top-1/top-2 margin beat
majority voting, and overriding high-consensus steps cut success from 46.9% to 35.0%
([CATTS](https://arxiv.org/html/2602.12276)). GPT-4.1 mini as policy with a separate grounder and validator retries
reached 88.8% on WebVoyager ([Surfer-H](https://arxiv.org/html/2506.02865v1)).

**Personal agents** (research 2026-10-01):

- **Instinct** (X post by @RMladek, 2026-09-29, and its replies; Yogev Kr's in-sandbox teardown; supermemory's memory
  teardown):
  - Loop and tools:
    - a backend main agent owns the conversation and delegates to task agents;
    - an E2B sandbox runs tools through a CLI, with no model or credentials inside;
    - a separate leased cloud-Chrome pool keeps cookies;
    - `browser_guidance` gives per-site notes and past outcomes before each site.
  - Secrets and payment:
    - vault fill by reference, with a secure link for a missing secret;
    - an OTP ladder: TOTP, then the inbox, then asking;
    - single-use cards for an approved amount.
  - Memory: git markdown, grep retrieval, a daily background writer, and a profile with "autonomy calibration".
  - About 30 markdown skills; wake subscriptions; small detector models that can block a step before it runs.
- **Meta Muse:**
  - a permission gate outside the agent (allow / deny / ask);
  - a browser sub-agent that reads the accessibility tree only.
- **OpenAI dots:**
  - a private sign-in form, so the model never sees credentials;
  - four policies per action class: act / act if pre-approved / ask / hand off;
  - a second-model review.
  - The same launch added a Decisions API: an option scorer for "an agent's next move".
- **OpenClaw and Poke:**
  - one interaction agent plus per-task workers;
  - skills listed by name and description until needed.
- **Grok Bot, Mariner, Stagehand, Skyvern:** do a task once, replay it, and fall back to the live agent when the page
  differs.

Source list: [RMladek post](https://x.com/RMladek/status/2105057398566367666),
[Yogev teardown](https://x.com/yogevkr/status/2102169003967783129),
[platforms teardown](https://rohanadwankar.github.io/posts/platforms.html),
[Instinct memory](https://supermemory.ai/blog/reverse-engineering-instinct-memory/),
[Muse security](https://research.meta.ai/blog/security-and-safety-for-ai-agents-our-approach-with-muse),
[dots](https://flaviocopes.com/openai-dots/), [OpenClaw docs](https://docs.openclaw.ai).

## 3. Architecture

### 3.1 Roles

- **Jev chooses.** Every choice among known options: message routing, the task's skill, each browser step, whether a
  subgoal is done, what blocks the page, whether the last step worked, what a reply means, and the gate's checks.
- **Mini writes.** Plans (subgoals), text to type, URLs, and every word the user reads. It resolves steps Jev is
  unsure about by choosing among Jev's candidates, never by naming new elements.
- **Code guards.** Approval, secrets, attempt limits, budgets, captcha relay, and the facts shown for approval.

### 3.2 Modules

| Module | Role | Origin |
|---|---|---|
| `core.py` | Concierge: routing, new-task intake, parallel tasks, question queue, per-task tags | reworked |
| `worker.py` | One task's runtime: tab, state, ask/say, secrets, LOOK, captcha relay, uploads, downloads, traces, the tick loop | new, from `agent.py`'s base |
| `executor.py` | System One: the Jev step request, gates, TYPE values, loop control | new |
| `planner.py` | System Two: mini turns, split, start, LOOK prompts, message lint | new; replaces `mind.py`, `jobs.py` |
| `gate.py` | Commits: detect, facts, summary, approve or pre-authorise, bind, watch | new, from `driver.py` and `agent.py` |
| `skills.py` | Parse `skills/*.md` and `skills/sites/*.md`, list them for Jev, render them for mini | new |
| `skills/` | Skill and site-note files (Section 6) | new |
| `jev.py` | Every Jev question text and the ask helpers | reworked |
| `model.py` | Transports only: TypeSafe post and validation, OpenAI `text_json`, `action_space`, `criteria` | trimmed |
| `rules.py` | Universal checks only: commit regex, verbatim, amounts, secret fields, formats, captcha, error page | trimmed |
| `browser.py`, `snapshot.js`, `views.py`, `profile.py`, `channel_web.py`, `messages.py`, `static/`, `server.py` | Browser, page views, profile and secrets, channel, UI, CLI | kept; `server.py` loses `JEV_DRIVER` |
| `bench.py`, `bench/tasks.toml`, `bench/unseen.toml` | Live bench | extended |

Deleted: `agent.py`, `driver.py`, `mind.py`, `jobs.py`, `questions.py`, and the tests of the deleted code (rewritten
for the new modules). `README.md` and `AGENTS.md` are rewritten for this loop.

### 3.3 One request, end to end

"Order 2 Diet Coke on Instamart, don't ask, under ₹100":

1. **Concierge:**
   - Jev routes the message as a new task.
   - Jev says it is one errand, picks the `groceries` skill, and reads it as pre-authorised; code parses the cap
     ₹100.
   - The start URL comes from `skills/sites/swiggy.com.md`, and a worker starts.
2. **Planner, first turn:** subgoals
   1. set the delivery location;
   2. search "Diet Coke 300 ml";
   3. add 2 to the cart;
   4. open the cart and its bill;
   5. choose the payment method (`ask`);
   6. place the order (`commit`).
3. **Executor, each step:** one Jev request → CLICK or TYPE with the subgoal's value → execute → read the page.
   - A code field appears → hidden question → TYPE `[secret:answer1]`, with no mini call.
   - Subgoal 5 → planner turn → "How should I pay? 1. UPI 2. Cash on delivery" → Jev reads the reply.
4. **Gate:**
   - "Place order ₹76" is a commit.
   - Code facts: payable ₹76 ≤ ₹100. Jev says the order matches the request.
   - Tell "Placing: Diet Coke 300 ml × 2 · ₹76 · Home · UPI" → click → watch until the site confirms.
5. **Planner, report turn:** "Order placed, arriving in 12 minutes, ₹76", with a quote from the page.

About 5 mini calls (plan, payment question, report, about 2 escalations) against about 30 today.

## 4. Executor (System One)

### 4.1 The step request

One TypeSafe request per step. State, kept small because irrelevant state costs Jev accuracy:

- `goal`: the user's goal; `subgoal`: `{do, done_when}`;
- `values`: the subgoal's values (`field: text`), with secrets and profile values as placeholders only;
- `answers`: the user's non-secret answers so far;
- `page`: URL, title, and the first 2,500 characters of redacted text;
- `elements`: the indexed element list from `action_space`, visible first, at most 200, new since the last step
  marked `*`, each named by its own label and role;
- `recent`: the last 6 steps (operation, element label, and the change line from `views.change`).

Questions in the request:

| Name | Options |
|---|---|
| `next` | Operations that have valid targets on this page: CLICK, TYPE, SELECT, KEY, SCROLL_UP, SCROLL_DOWN, HOVER, UPLOAD, SWITCH_TAB, ACCEPT_DIALOG and DISMISS_DIALOG (a browser dialog is open), BACK (history exists), WAIT; always DONE ("the page already shows the subgoal complete") and THINK ("no option clearly advances the subgoal: it needs a decision, text, or the user") |
| one target question per offered operation | The compatible elements only (today's `action_space` heads); only the chosen operation's answer is used |
| `key` | Enter, Tab, Escape, ArrowDown, ArrowUp, Backspace (when KEY is offered) |
| `blocker` | none · sign_in (a sign-in wall or form) · code (a one-time code or password is asked) · captcha · error (an error or not-found page) · unavailable (out of stock, closed, not serviceable here) · choice (the page offers options that neither the goal nor the user's answers pick) · payment (the page asks how to pay) |
| `effect` | worked · failed · unclear — about the previous step; asked only when one exists |

DRAG, DOUBLE_CLICK, and RIGHT_CLICK stay in `browser.py` but are not offered in Phase A. OPEN_URL is mini's (Jev
cannot write a URL).

### 4.2 Gates

Constants in `executor.py`, tuned from bench logs (Section 11 reports the margins):

| Constant | Start value | Meaning |
|---|---|---|
| `ACT_P` | 0.55 | minimum probability of the chosen operation |
| `TARGET_P` | 0.6 | a target is accepted at this probability… |
| `TARGET_MARGIN` | 0.25 | …or at this lead over the next target (same-label duplicates merged first) |
| `DONE_P` | 0.6 | DONE at this probability completes the subgoal |
| `BLOCKER_P` | 0.6 | a blocker other than none at this probability is handled before any action |

Order of handling:

1. A blocker ≥ `BLOCKER_P` is handled first (4.4).
2. DONE ≥ `DONE_P`: the next subgoal starts; a plain `act` subgoal starts with no mini call.
3. An operation that passes `ACT_P` and whose target passes `TARGET_P` or `TARGET_MARGIN` runs through the gate
   (Section 8) and executes.
4. Anything else (THINK, low probability, low margin) is a planner turn with Jev's top 5 candidates (operation,
   element, probability). Mini sees only these escalated steps; a confident Jev step is never sent to mini.

A Jev answer that fails validation executes nothing; the request is asked once more, then goes to the planner.

### 4.3 What TYPE types

1. A field whose label shares most of its words with one of the subgoal's value names gets that value.
2. A subgoal with exactly one value gives it to any field.
3. Several values and no clear match: a Jev choice among the value names.
4. No value: a planner turn writes it.

Placeholders are replaced after every model call (today's mechanism). Code, password, and CVV fields
(`rules.secret_field`) accept only `[secret:answerN]` from the user's hidden answers, within the attempt limits
(3 codes per field, 2 CVVs). Dates are computed by code from the goal ("next Friday") and passed to mini as text,
because Jev cannot compare dates. UPLOAD takes the files the user sent in this task's chat; with none, code asks for
them with a file question naming the field (today's upload path).

### 4.4 Blockers

| Blocker | Handling |
|---|---|
| code | Only when `rules.code_field` finds a code, OTP, or password field on the page (otherwise the step goes to the planner, which may first press "Send code"). Code asks the hidden question "<site> is asking for a code or password. Type it here; it stays hidden." and types the answer into the field Jev picks, as `[secret:answerN]`. No mini call. After 3 codes for one sign-in the task stops, as today. |
| captcha | The relay (`worker.relay_captcha`): a numbered screenshot to the user, who replies with square numbers or the text. |
| sign_in | Planner turn with the `sign-in` situation skill. |
| payment | Planner turn with the `payment` situation skill. |
| choice | Planner turn: ASK with the options and their prices, then act on the answer. |
| unavailable | Planner turn with the `location` situation skill when the site says it doesn't serve the place, else the kind skill's rule (ask for an alternative or STOP). |
| error | Planner turn: press the page's retry once, else BACK or the site's home page. |

### 4.5 Loop control

- The same operation and element on the same URL path with no page change: at 2 the executor takes Jev's next-best
  target once; at 3 it goes to the planner with the last 3 steps; at 8 it asks the user the stuck question (today's
  `stuck`).
- 12 executed steps in one subgoal: planner turn to rewrite the plan.
- Before any action the page is checked fresh (`browser.fresh`); a stale page is read again and Jev asked again.

## 5. Planner (System Two)

### 5.1 When mini is called

| Reason | When |
|---|---|
| `start` | The task starts: plan the subgoals; ask in one message for any skill input the goal, answers, and profile don't give |
| `next` | The next subgoal is `ask`, `commit`, or `report`, or the plan is used up |
| `unsure` | Section 4.2 step 4, and loop control at 3 |
| `blocker` | Section 4.4 |
| `reply` | The user answered a planner question, or sent a change for this task |
| `commit` | The gate reports a commit's outcome (confirmed, failed, or only a step) |
| `replan` | 12 steps in one subgoal, or 3 refusals of the same planner action |

### 5.2 Input

Stable parts first so that OpenAI's automatic prompt caching applies: the system prompt, then the kind skill, the
situation skills it uses or a blocker attached, and the site notes; then the goal, today's date, resolved dates, the
plan with the current index, the chat for this task (secret answers as placeholders), the profile keys, and the last
8 steps; then the page view (`views.view`: numbered elements, text, change line), Jev's top 5 candidates when
escalated, the reason, and code feedback.

### 5.3 Output

One strict `json_schema`, all fields required:

```json
{
  "note": "one sentence worth keeping, or \"\"",
  "subgoals": [{"do": "search for Diet Coke 300 ml", "done_when": "results for Diet Coke are shown",
                "kind": "act|ask|commit|report",
                "values": [{"field": "search", "text": "Diet Coke 300 ml"}]}],
  "action": {"op": "CONTINUE|PICK|TYPE|ASK|TELL|DONE|STOP|OPEN_URL|WAIT|BACK|LOOK",
             "pick": "1-5, one of Jev's candidates, or \"\"",
             "text": "text to type, URL, what LOOK should find, or DONE's page quote"},
  "message": "words for the user: ASK, TELL, DONE, STOP",
  "options": ["Uber Go · ₹212 · 4 min"]
}
```

- `subgoals: []` keeps the plan; a non-empty list replaces the rest of it.
- CONTINUE hands back to the executor. PICK runs Jev's candidate n; TYPE types `text` into candidate n.
- ASK sends `message` with `options` as a numbered list; Jev reads the reply (`jev.read_reply`: option n or
  other). "You choose" leaves the choice to mini, except a delivery or pickup place.
- DONE is checked by code: the quote must appear verbatim on the page or one seen just before it, and Jev's
  `achieved` must say yes (today's `finish_step`).
- OPEN_URL accepts only http(s); a URL built from a site note's template must keep the template's host.
- LOOK runs today's visual finder (grid screenshot, mini names a cell, code clicks its centre); a LOOK click that
  commits goes through the gate.
- A reply that is cut off, refused, or invalid is asked once more; a second failure is feedback to the next turn.

### 5.4 Message lint (code, universal)

Kept from `driver.py` without its commerce rules. A message is refused with feedback when it:

- talks about the page ("click", "tap", "element", `[12]`);
- speaks of "the user" instead of "you";
- repeats a TELL;
- asks what the chat already answers (today's `not_asked_before`);
- asks the user to confirm (the gate asks);
- asks for a UPI PIN (the user approves in their UPI app);
- asks for a code, password, or CVV other than through the hidden input.

## 6. Skills

### 6.1 Files

`skills/<name>.md`: a frontmatter block of `key: value` lines (lists comma-separated), then markdown.

```
---
name: rides
description: Book a cab, auto or bike ride (Uber, Ola, Rapido)
inputs: pickup, drop, when?
sites: uber.com, olacabs.com, rapido.bike
uses: location, payment, sign-in
summary: ride type, pickup → drop, fare, payment
report: driver, car, plate, PIN, arrival time, fare
---
## Plan
1. ...
## Rules
- ...
```

| Key | Meaning |
|---|---|
| `name`, `description` | The skill's id, and the option text Jev chooses by |
| `inputs` | Details to ask for at the start when the goal, answers, and profile lack them; `?` marks optional ones |
| `sites` | Usual services, by host, in suggestion order |
| `uses` | Situation skills always attached to the planner's input |
| `summary` | Fields the approval names |
| `report` | Fields the final message names |

Situation skills (`sign-in`, `payment`, `location`) have only `name`, `description`, and a body.

`skills/sites/<host>.md`: frontmatter `host`, `name` (as the user says it), `start` (URL), optional `search` (a URL
template with `{query}`), then quirks. Site notes are loaded when the task's host matches. Parsing uses the standard
library only.

### 6.2 Selection

At task start Jev picks one kind skill from the descriptions, or `general` (no skill: the planner's system prompt
alone). A blocker attaches its situation skill for the rest of the task.

### 6.3 The Phase A set

- **Kinds (10):**
  - groceries
  - food-delivery
  - shopping
  - rides
  - tickets (movies, events)
  - travel (flights, hotels, trains: search and book)
  - bills-recharges
  - account (orders, invoices, subscriptions, returns)
  - forms
  - lookup (find, compare, report)
- **Situations (3):** sign-in, payment, location.
- **Site notes:** only the known bench sites (Section 11.1); never a host in the unseen pool.

Where today's rails go:

| Rail in `driver.py` | New home |
|---|---|
| Open the detailed bill before paying; name fees the user could avoid | groceries and food-delivery plans |
| The user picks the payment method from every method listed; card details only after they pick a card | payment skill |
| Priced options (ride types, sizes, slots) are asked with their prices before the final step | rides and shopping rules, and the `choice` blocker |
| "Current location" through the site's detect option; a site that doesn't serve the place gets its location fixed first | location skill |
| Never remove items the user already had in the cart; Buy Now for a single item | shopping and groceries rules |
| Travel is one-way for one traveller unless said; a round-trip price is never reported for a one-way question | travel rules; dates resolved by code |
| Sign in only when the task needs an account; phone as `[profile:phone]`; never Google, Apple, or Facebook sign-in | sign-in skill |
| UPI PIN never in chat; codes only through the hidden input | code (5.4, 4.3) |
| Close pop-ups and banners that block the page; decline optional cookies | planner system prompt |

Every other rail (the summary suffixes, the priced-option name matching, the payment-name matching, the bill-opening
rule, `UNSERVED`, the round-trip regex) is deleted with `driver.py`.

## 7. Concierge

For each incoming message, in order:

1. **No model:** stop words, answers to an open hidden question, button taps, and numbered replies are handled as
   today.
2. **Routing:** Jev reads the intent (ANSWER, AMEND, NEW_TASK, CANCEL, STATUS, OTHER) and the task (today's
   classifier, moved into `jev.py`). Ambiguous readings ask which task (today's `which`).
3. **New task.** One Jev request with three questions over the user's current message (never earlier chat: a 'don't ask' for an earlier errand must not pre-authorise a later one):
   - `several`: yes/no, the message asks for more than one separate errand;
   - `skill`: the kind skill, or general;
   - `preauth`: yes/no, the user explicitly says to go ahead without confirming.

   Then:
   - **Split:** `several` = yes → mini splits the message into self-contained goals, and each goal gets its own
     `skill` and `preauth` request.
   - **Cap:** code reads a cap from the user's words: an amount after "under", "below", "max", "up to", "upto",
     "within", "budget", "less than", or "not more than", in ₹, Rs, or INR.
   - **App:** a site named in the goal; else the saved default for the skill (a preference "Default app for rides:
     Uber", today's mechanism); else one question listing the skill's sites by name (the site note's `name`, else
     the host), saved as the default.
   - **Start URL:** the site note's `start`, else mini's start job (validated http(s), as today).
   - **Worker:** starts in its own thread. Its tab loads while the planner's first turn asks for any missing inputs,
     in one message.
4. **Parallel:** at most 3 running tasks, at most 1 per site; extra tasks wait with a message (today's `acquire`).
   The "drop the running task?" switch is removed.
5. **Questions:** one open at a time, codes and payments first (today's queue). Messages carry the task's title when
   more than one task is active.
6. **Changes and status:** AMEND joins the task's updates; the worker's next tick gives it to the planner (reason
   `reply`) and voids any approval. STATUS and OTHER get a mini reply from the tasks' views.
7. **One messenger:** workers speak only through the concierge's `ask` and `send`.

## 8. Gate

1. **Detect.** A CLICK, KEY Enter, LOOK click, or dialog accept commits when:
   - `rules.commit_label` matches the element's label, the enclosing form's submit label (KEY Enter), or the dialog;
     or
   - it happens during a `commit` subgoal and Jev's `final` check on the control says "final" (≥ 0.5).

   Commit detection has two tiers: an outright tier (the commit regex: pay, place order, buy, book ride/cab/bike/auto/table, confirm order/payment, make payment, submit order, end/cancel membership, order now, recharge now, delete, …) that Jev can never clear, and a maybe tier (book, confirm, request, reserve, submit, send, transfer, recharge, subscribe, complete, place, proceed to pay/checkout, any control whose words contain a redaction placeholder) that Jev's `final` check may clear only with 'step' at probability ≥ 0.8.

   Jev's `final` check ("completes the order, booking, payment, submission, or message now" against "a step on the
   way") also runs for ambiguous regex hits: a checkout step such as "Proceed to Pay", or a payment method whose name
   contains "Pay". It clears only those (at "step" ≥ 0.8); a label the commit regex matches outright is never cleared
   by Jev.
2. **Facts (code)** from the current page and the last 5 pages seen:
   - the host and the control's label;
   - the payable total (`PAYABLE` before `TOTAL`, never an item total or saving);
   - fee lines, and the delivery or pickup address line;
   - the page's item count, and the values typed into the form.
3. **Summary.** Mini writes one line per `summary` field of the skill. Every amount in it must appear verbatim on the
   page (`rules.amount_shown`), or mini is asked again. Code appends facts the summary lacks and shows one fact per
   line, then: "I'll press “<control>” on <site>. Reply yes to go ahead, or tell me what to change."
4. **Match.** Jev `matches`: the items, quantities, and places are what the user asked for, with nothing extra. On
   "no", the planner fixes the order (at most twice); then the user is asked anyway.
5. **Decide.**
   - **Pre-authorised run:** the task was pre-authorised, the page shows a payable total, code parses it as ≤ the
     cap, the match check says yes, and the host is the task's site. The summary is sent as "Placing: …" and the
     action runs.
   - **Pre-authorised, no cap:** runs only when the goal is not a commerce goal (no order/buy/book/pay/purchase/reserve words) and the page shows no money at all; a commerce goal with no cap always asks.
   - **Shadow mode (default):** unless `JEV_PREAUTH=on`, neither pre-authorised route runs; the commit is asked about, and the question ends with a line saying pre-approval is switched off.
   - **Everything else asks.** A yes needs both today's clear-yes words (no "no", "wait", "but", "?") and Jev's
     `approval` = yes. "Change" and "no" go to the planner; nothing is committed.
6. **Bind.** The approval holds the element, host, and amount, and lapses when:
   - the amount leaves the page, or
   - 20 steps pass, or
   - the user sends a change (`rules.approval_valid`).
7. **Watch.** After a commit, Jev's after-commit check drives what happens (today's `watch`):
   - processing: read the page again every 5 s for up to 5 minutes, and tell the user once;
   - confirmed or failed: back to the planner;
   - only a step: the next commit needs its own approval;
   - unclear twice: stop with "check the site before trying again".

   A commit is never retried automatically, and one task has at most one commit in flight.
8. **Bench mode.** The gate records what a pre-authorisation would have done, always asks, and the bench answers NO.

## 9. Safety invariants

- **Outbound:**
  - One outbound path (`model.post_json`) redacts every request to either model.
  - It then scans the serialised body for every registered secret value, with the redaction's own matching rules
    (whole words of 3 or more characters; digit runs compared without spaces and dashes). A hit is not sent: it
    raises an error and is recorded as an unsafe event.
- **Secrets:**
  - Secret fields take only the user's hidden answers.
  - Profile values are typed from placeholders substituted after the model call.
  - Card details are never stored.
- **Untrusted input:**
  - Page text is data in every prompt and every Jev question.
  - Answers come only from the user's channel.
  - Model output never becomes a selector, JavaScript, a file path, or a coordinate (LOOK cells are converted by
    code).
- **Jev is never the only safety check:** the commit regex, the code facts, and the pre-authorisation checks do not
  depend on Jev's reading of page text.
- **The user alone:** only the user solves captchas; a UPI PIN is entered only in the user's UPI app.

## 10. Budgets, errors, traces

- **Caps per task:** 150 executed actions, 30 minutes, and $0.25 of model spend. Spend counts mini (input, cached
  input, and output), Jev (input tokens at $0.042 per million), and LOOK.
- **Model and network errors:** they become feedback to the next turn. 5 in a row stop the task with what happened
  and any commit that may have gone through.
- **Browser:** an `ExecutionUncertain` stops the task without repeating the action (today's rule). A stale page is
  read again and decided again.
- **Traces** (same file, redaction, and `jev traces` as today), per step:
  - Jev's questions, the answers with probabilities, and the gate outcome;
  - any escalation reason;
  - planner turns (reason and output), gate facts and decisions;
  - spend per model.

## 11. Bench and acceptance

### 11.1 Known tasks

`bench/tasks.toml`, one entry per task: `id`, `category`, `goal`, scripted answers (question pattern → reply), `end`
(`gate`: reach the final approval, which is answered NO; `result`: a reported answer; `file`: a delivered file), and
a pass rule (required words plus one sentence for the Jev judge).

| Category | id | Goal | End |
|---|---|---|---|
| groceries | zepto | Order one 500 ml pack of Amul Taaza milk on Zepto | gate |
| groceries | instamart | Order one Diet Coke 300 ml can on Swiggy Instamart | gate |
| groceries | blinkit | Order one pack of Britannia white bread on Blinkit | gate |
| food | swiggy-food | Order one Margherita pizza from Domino's on Swiggy | gate |
| food | zomato | Order one masala dosa from a restaurant near me on Zomato | gate |
| shopping | amazon | Buy one boAt Airdopes 141 on Amazon.in | gate |
| shopping | flipkart | What is the price of the Apple iPhone 16 128 GB on Flipkart? | result |
| rides | uber | Book an Uber from my current location to Kempegowda International Airport Terminal 1 | gate |
| rides | ola | Book an Ola cab from my current location to Kempegowda International Airport Terminal 1 | gate |
| rides | rapido | Book a Rapido bike from my current location to Indiranagar Metro Station | gate |
| tickets | bookmyshow | Book 2 tickets on BookMyShow for the most popular Hindi movie tomorrow evening at any PVR in Bengaluru | gate |
| travel | flight | Find the cheapest flight from Bengaluru to Delhi next Friday on Google Flights and tell me the airline, time and price | result |
| travel | hotel | Find the cheapest hotel in Goa rated above 4 for 2 adults next Saturday to Sunday on MakeMyTrip | result |
| bills | recharge | Recharge my Jio number with the ₹299 plan on Paytm | gate |
| bills | electricity | Pay my BESCOM electricity bill on Amazon.in | gate |
| account | invoice | Download the invoice for my latest Amazon.in order | file |
| account | order-status | Where is my latest Flipkart order? | result |
| account | cancel-prime | Cancel my Amazon Prime membership | gate (or a result saying there is no active membership, quoted from the page) |
| forms | form | Fill the pizza order form at https://httpbin.org/forms/post for a medium pizza with bacon, customer name Test User, delivery at 19:00, and submit it | gate |
| forms | web-form | Fill the web form at https://www.selenium.dev/selenium/web/web-form.html with the text Hello, the dropdown option Two, and the date 15 October 2026, and submit it | gate |
| lookup | compare | Compare the price of the Apple iPhone 16 128 GB on Flipkart and Amazon.in and tell me which is cheaper | result |
| lookup | myntra | Find the price of Nike Air Max shoes on Myntra and tell me the cheapest one | result |
| lookup | restaurant | Find the best-rated South Indian restaurant in Koramangala on Zomato that is open now | result |
| lookup | atta | Find the cheaper 1 kg pack of Aashirvaad atta between Zepto and Blinkit | result |

Scripted answers cover choices a user would make (the payment method, a ride type). They never hold personal data:
a question that needs it (the BESCOM account number) is forwarded to the user's web chat, like a code. The sites are
signed in once in the agent Chrome before a run; code questions that still come up reach the user's web chat, as
today.

### 11.2 Unseen tasks

`bench/unseen.toml` holds about 50 tasks written once:

- they span at least 8 of the categories;
- at least half use sites with no site note;
- each ends as `gate` or `result` and carries a one-sentence pass rule for the Jev judge.

Examples: the opening hours of Lalbagh Botanical Garden; Myntra's return policy for shoes; the cheapest 1 kg of
basmati rice on BigBasket; a table for 2 tonight in Koramangala on EazyDiner (gate); the next Vande Bharat from
Bengaluru to Chennai (result).

Each run samples 8 with a logged seed. No skill, site note, or rule may be written for these tasks or their sites.

### 11.3 Running and grading

- **Run:** `jev bench [--only …] [--repeat 2] [--unseen 8] [--seed N]` runs inside the jev server; every final
  approval is answered NO.
- **Grading:** required words (code), then the Jev judge on the pass rule with the final message and page text.
- **Unsafe:** a commit executed without a yes (a would-be pre-authorisation is logged, never executed), or a
  secret-scan hit (Section 9). Either fails the run.
- **Report** (`~/.jev/bench/<run>/report.html`):
  - pass rate for known tasks, unseen tasks, and per category;
  - regressions against the previous run and against the recorded baseline run `20260926-143539` and the aggregate
    of 2026-09-26 (40/72);
  - median seconds per step and per task, and $ per task by model;
  - mini and Jev calls per task, and the escalation rate by reason;
  - a histogram of Jev margins for the gate constants;
  - questions asked, and links to traces.

Seconds per executed step = (task wall time − time waiting on the user) ÷ executed actions, per task run.

**Done means:** across two runs, ≥ 80% of known task runs pass; ≥ 60% of unseen task runs pass; 0 unsafe actions;
median ≤ 2 s per executed step; median ≤ $0.02 per task.

## 12. Testing

- **Offline** (`uv run pytest`), no paid API: scripted Jev (answers by question name) and scripted mini turns, and the
  existing `no_real_models` guard. Covered:
  - executor: gates, blocker routing, TYPE value rules, loop control, stale pages;
  - planner: schema handling, lint, PICK and TYPE limited to Jev's candidates, DONE evidence;
  - gate: detection, Jev never clearing a regex match, facts, amounts verbatim, pre-authorisation cap and host,
    binding, watch, bench mode;
  - skills: parsing, selection, situation attachment, site notes, and URL template hosts;
  - concierge: split, parallel start, one per site, routing, cap parsing, default app, and the removed switch;
  - redaction and the secret scan in `post_json`.
- **Browser fixtures** (`uv run pytest -m browser`): the worker on the local fixture pages (shop, pay, frames, popups,
  upload, events, covered) with scripted models.
- **Replay cases:** `jev bench-save` / `jev replay` run the executor and planner on saved pages without a browser.
- **Checks:** `uv run ruff check . && uv run pytest && uv run pytest -m browser && node --check assistant/snapshot.js
  && node --check assistant/static/app.js`.

## 13. What stays as it is

- `browser.py` and `snapshot.js`: observation, frames, pop-ups, events, uploads, downloads, and dialogs.
- The web channel in WhatsApp Cloud API shapes, and the chat UI with its hidden input.
- Profile and Keychain vault, redaction, captcha relay, LOOK.
- `jev chrome`, `jev probe`, `jev run`, `jev traces`, `jev profile`, `jev forget`.

## 14. Later phases (not in this spec)

- **B, learns:**
  - a git-markdown memory folder (profile, phone, addresses, preferences, standing approvals such as "usual
    Instamart orders under ₹300");
  - site notes the agent drafts after runs, which the user approves;
  - habit replay: record verified runs per site, replay them with Jev checking each step, and fall back to the live
    loop on a mismatch.
- **C, reaches out:**
  - WhatsApp and iMessage, with secrets through a secure one-time web link, because a chat app has no hidden input;
  - wake subscriptions and scheduled or watching tasks;
  - TOTP codes and codes read from an inbox;
  - a cloud browser behind the same browser interface.

## 15. Risks

| Risk | Mitigation |
|---|---|
| Jev steps are weaker than mini's on unfamiliar pages | Explicit subgoals, margin gates, planner escalation, the unseen set; the gate constants are tuned from bench margins |
| Page text manipulates Jev | Gate facts and commit detection in code; Jev clears only ambiguous labels; pre-authorisation checked by code |
| Skills overfit the bench | Unseen tasks never get skills or notes; their pass rate is part of done |
| Mini fails the schema | Small schema, one retry, then feedback; the same strict-schema path as today |
| A pre-authorised order goes wrong | Cap, match, and host checks; bench never executes pre-authorisations; the summary is always shown |
| Sign-in sessions expire | Codes through the hidden input; sites signed in before bench runs |
| Sites block automated checkouts | Headed agent Chrome with the user's own accounts, for personal use |
| Latency and cost targets are estimates | The bench measures both and they are part of done |
