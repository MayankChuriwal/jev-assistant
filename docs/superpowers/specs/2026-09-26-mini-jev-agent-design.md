# Jev Assistant — mini + Jev agent

Date: 2026-09-26. Status: design approved in chat; this spec awaits the user's review.

This replaces the decision loop of [the original design](2026-09-23-jev-assistant-design.md). The browser,
chat, profile, secret, and trace layers of that design stay. The GPT-built reliability design
(`2026-09-24-local-agent-reliability-design.md`, saved on branch `gpt-reliability`) is not used: its live runs
failed at step 0 because GPT-4.1 mini could not fill its per-turn state schema.

## 1. Goal and decisions

The user states any goal in chat. The agent opens its own Chrome, signs in as the user, and completes the goal
on the real site, asking in chat only for what it cannot know. It must work on any site, with no per-site code.
Uber and Swiggy are test cases, not the product.

Decided with the user on 2026-09-26:

| Topic | Decision |
|---|---|
| Models | GPT-4.1 mini is the strongest model allowed. Jev (TypeSafe) is part of the decision layer. No frontier model. |
| Base | Rebuild on commit `22c4e45` (branch `mini-jev-agent`). Current master is saved as branch `gpt-reliability`. GPT fixes are copied only when they clearly help, each with a test. |
| Split | Mini decides the next step and writes all text. Jev picks the exact element, judges each result, and reads the user's replies. Code enforces the safety rails. |
| Chat | Plain messages. Options as a numbered list with prices; the user replies in their own words. No buttons, no screenshots (captcha excepted), never "click/type this on the site". Open questions ("Where to?") come before site suggestions. |
| Sign-in | The agent signs in itself. OTPs are asked in the jev web chat (hidden input) and never pass through a model. |
| No site named | Ask once per kind of task ("Swiggy Instamart, Zepto or Blinkit?"), save the answer as that kind's default, and say which app is used next time. |
| Payment | Always list the site's payment options and let the user pick. Card CVV/OTP via hidden input, never stored. UPI in-app approval: tell the user the amount and wait for "done". |
| Live testing | Claude may run live tasks on real sites in the agent Chrome, up to the final approval, always answering NO there. |
| Done means | Section 9. |

Evidence behind the split (research, 2026-09-26): Surfer-H on WebVoyager with GPT-4.1-mini as the policy and a
separate grounding model picking elements scored 69.2% on one attempt and 88.8% with validator retries
(arxiv.org/html/2506.02865). Validators (+17–20 points), explicit plans (+2.5–24), a distilled element list
instead of raw HTML (+10), and change reports after each action (+4) are the largest gains found; several actions
per step hurts (arxiv.org/html/2506.08136, skyvern.com blog, browser-use system prompt).

## 2. The step loop

Each step, in order:

1. **Read the page** (existing `Browser.observe`, `snapshot.js`, redaction). Code builds:
   - an element list: `[n] role "label" value/state`, visible elements first, at most 120, new elements since
     the last step prefixed `*`;
   - page text, trimmed to about 3,000 characters;
   - a change line: URL change, elements added and removed (top 8 labels each), new text lines (top 5), or
     "nothing changed".
2. **Mini decides.** One call, OpenAI strict `json_schema` (Section 3). Input: goal, the chat so far for this
   task, saved preferences, the plan, the last 10 steps (action, target label, Jev's verdict, change line),
   feedback from code, and the page from step 1. A screenshot is not sent by default (a switch exists for the
   bench to compare).
3. **Jev picks the element** (Section 4) when the action needs one. If Jev is not confident, mini gets
   feedback naming the closest candidates and decides again (once per step); after that the step uses LOOK
   (existing visual finder, GPT-4.1 mini on a grid screenshot) with mini's description.
4. **Code rails** (Section 5) may hold the action: approval, secrets, captcha, one page-changing action per step.
5. **Execute** through `Browser.act`.
6. **Jev checks the result** (Section 4): success, failed, or uncertain, from the before/after change line and
   mini's `next_goal`. The verdict goes into the next step's input.
7. **Loops.** Code tracks (operation, element, page URL path). The same one three times with nothing changed
   adds the feedback "this did nothing three times; try something else"; five times adds "rewrite your plan";
   eight times makes the agent ask the user "I'm stuck at <page title>: <next_goal>. What should I do?".

A task ends only when mini says DONE (and the done check passes), mini says STOP, the user cancels, or a cap is
reached: 150 actions, 300 model decisions, 30 minutes, or $0.50 of model spend. Model or network errors, invalid
elements, and rejected values are feedback to the next step, never a task-ending "Problem".

## 3. Mini's output

Strict `json_schema`, all fields required, reasoning first:

```json
{
  "evaluation": {"status": "success|failed|uncertain|none", "why": "one sentence"},
  "memory": "at most 3 sentences worth keeping",
  "plan": ["3-10 short items, or [] to keep the current plan"],
  "next_goal": "one sentence",
  "action": {
    "op": "CLICK|TYPE|SELECT|KEY|SCROLL|HOVER|UPLOAD|WAIT|BACK|OPEN_URL|ASK|TELL|DONE|STOP",
    "target": "the element in words, may include its [n], or \"\"",
    "text": "text to type, URL, option to select, scroll direction, or DONE's page quote",
    "key": "Enter|Tab|Escape|ArrowDown|ArrowUp|Backspace|\"\"",
    "commits": true
  },
  "message": "words for the user (ASK, TELL, DONE, and every committing action)",
  "options": ["numbered options for an ASK, or []"]
}
```

- `TYPE` text may contain placeholders: `[profile:phone]`, `[profile:email]`, `[profile:name]`,
  `[secret:answerN]`. Code substitutes the real value after the model call; the model never sees it.
- `ASK` sends `message` and, when `options` is not empty, a numbered list. The reply joins the chat.
- `TELL` sends `message` without waiting (progress, findings).
- `commits: true` marks an irreversible action (pay, place order, book, request, submit, send, cancel, delete).
  `message` must then summarise it for approval: items, route or address, option, payment method, amount.
- `DONE`: `message` is the final report; `text` is a short quote copied from the page proving it.
- A refusal or a cut-off reply is retried once; a second failure is feedback, not a crash.

One system prompt holds the general behaviour, with no site names:
sign in first when the site offers it and the task needs an account (phone/email via profile placeholders,
codes asked from the user); ask open questions before offering suggestions; to fill a place or search box,
TYPE the user's words, then pick the matching `*` suggestion (never press Enter on an autocomplete when
suggestions appear); for "current location" use the site's detect-location option; when the goal leaves a choice
open (ride type, size, variant, slot), ASK with the options and their prices; at payment, ASK which of the listed
payment methods to use; never choose for the user what the goal did not state; use memory and the chat before
asking; one page-changing action per step; after a commit, WAIT while the site works, then report every detail the
user needs (driver, car, plate, PIN, order number, ETA, amount).

## 4. Jev's three jobs

All three use the existing TypeSafe `systemone` choice API (`model.choose` style: a question with
`criteria` and `instructions`).

1. **Grounding.** Candidates: the elements that support the operation, from `action_space` heads
   (`click_target`, `type_text_target`, `select_target`, `press_key_target`, `upload_target`, scroll heads).
   Instructions: the goal, mini's `next_goal`, and "choose the element that is: <target>". Confidence below 0.4
   is "not confident". KEY without a target presses on the focused element; SCROLL without a target scrolls the
   page.
2. **Result check.** After every executed action: criteria `success` ("the page now shows what the step
   wanted"), `failed` ("nothing changed, or an error appeared"), `uncertain`. After a commit the criteria are
   `confirmed`, `failed`, `processing`, `unclear` (Section 6).
3. **Reading replies.** After an ASK with options: which option, or `other`. After an approval question: `yes`,
   `no`, or `change`. After a stuck question: passed to mini as text. Anything unclear re-asks once.

## 5. Safety rails in code

Kept from `22c4e45` unless noted:

- **Approval.** A committing action (mini's `commits`, or `rules.commit_label` on the element) never runs
  without an explicit yes. The approval question is mini's `message` plus "Reply yes to go ahead.". Every amount
  in the message must appear on the page (`rules.amount_shown`), otherwise mini gets feedback and nothing is
  asked. A yes binds to that element, host, and amount and expires as today (`rules.approval_valid`). Jev reads
  the reply (Section 4); `change` goes to mini as the user's words.
- **Secrets.** Every page and model request is redacted (`Secrets`, `SECRETS`). Code, OTP, password, and CVV
  fields (`rules.secret_field`) accept only `[secret:…]` placeholders from the user's hidden answers; anything else
  is refused with feedback. A question that asks for a code uses the hidden input (`rules.asks_for_secret`).
  Attempt limits stay (3 per code, 2 per CVV). Card details are never stored.
- **Captcha.** `rules.captcha_challenge` routes to the existing relay: a numbered screenshot to the user, who
  answers with square numbers or the text.
- **Wait before asking.** The 3-second settle rule before any question.
- **One commit at a time.** No second committing action while an earlier one is unresolved.

Removed from the decision path: Jev choosing among all operations, the text jobs `field_value`, `compose`,
`confirm_card`, `outcome`, `result`, `note`, `unblock`, `hint`, and the site-shaped rules (saved-address and
priced-option questions, sign-in tracking, profile-field guard, current-location rewrite). The old loop stays
runnable with `JEV_DRIVER=legacy` until the bench passes (Section 9), then a cleanup plan deletes it.

## 6. After a commit

The site may still be working (finding a driver, confirming payment). While Jev's check says `processing`, code
waits 5 s and reads the page again without calling mini, for up to 5 minutes, and tells the user once
("Uber Go AC requested: finding a driver. I'll tell you when it's confirmed."). `confirmed` or `failed` returns
control to mini, which reports. After 5 minutes the task ends with what the page shows. `unclear` twice ends the
task with "I can't tell whether <action> went through; check the site or its app before trying again."

## 7. Talking and preferences

- Chat routing (`core`, Jev classify), parallel tasks, cancel and change requests stay as they are.
- **No site named.** The start job returns the start URL, the task title, a kind (groceries, food, cab, flights,
  shopping, …), whether the goal names a service, and up to four usual services for that kind. If it names none
  and no default is saved for the kind, the agent asks once and saves the answer as a preference
  ("Default for groceries: Zepto"); later tasks say which app they use.
- **Options** are a numbered list with prices, the user replies in words, and Jev maps the reply.
- **Payment** method is always the user's pick (Section 3 prompt); the approval message names it.
- **Address** is never stored; "current location" means the site's detect option.

## 8. Traces

Same file and redaction as today, plus per step: mini's evaluation, memory, plan, next_goal, action, message;
Jev's grounding (candidate count, choice, confidence) and verdict; the change line; code feedback given.

## 9. Bench and acceptance

- `bench/tasks.toml`: per task an id, goal, scripted answers (question pattern → reply), end type (`gate`: reach
  the final approval and answer NO; `result`: report or file), and a pass rule (required words in the approval
  message or result, plus one sentence for the judge).
- `jev bench [--only …] [--repeat 2] [--driver mini|legacy]` runs inside the running jev server so that OTP and
  other hidden questions reach the user's web chat; all other questions get scripted replies; the final approval
  always gets NO.
- Grading: required words (code), then Jev pass/fail on the pass rule with the final message and page text.
  Unsafe-action audit: any commit executed without a yes, or any known secret value inside a model request, fails
  the run.
- Report `~/.jev/bench/<run>/report.html`: task × run table, pass rate, per-site results, steps, seconds, cost,
  questions asked, links to traces; regressions against the previous run highlighted.
- Replay cases: `jev bench save <trace> <step> "<expectation>"` stores the page and an expectation
  (must ASK / must not act on [n] / must TYPE into <target>); `jev bench --replay` runs mini + Jev on them without
  a browser.

The 12 tasks: Uber ride; Ola ride; Swiggy Instamart Diet Coke; Swiggy restaurant dish; Zepto or Blinkit item;
Amazon.in product; BookMyShow tickets; cheapest Bengaluru→Delhi flight (report); Flipkart price lookup (report);
a public web form (submit gate); latest Amazon invoice download (file); a popup-heavy site.

**Done means:** across two bench runs, at least 80% of task runs pass; Uber and Swiggy Instamart pass both runs;
zero unsafe actions. Then two real completions with the user approving in chat: an Uber booked with driver, car,
plate, and PIN reported, and a Diet Coke order placed.

## 10. Testing

- Offline (`uv run pytest -q`): the loop with scripted mini and Jev: schema handling, grounding retry and LOOK
  fallback, verdict feedback, loop nudges, approval binding and reply reading, placeholders, secret-field rule,
  after-commit watching, caps. No test reaches a paid model (existing `no_real_models` fixture).
- Browser fixtures (`uv run pytest -m browser`): the loop on local pages (typeahead, modal, checkout with payment
  options, code boxes) with scripted models.
- Live: the bench (Section 9), run by Claude.

## 11. Risks

- Mini may still misread complex pages; Jev's check and loop nudges bound the damage, and the bench measures it.
- Jev's grounding threshold (0.4) and the element-list size (120) are first guesses, tuned on bench results.
- Sites change daily; each task runs twice and failures become replay cases.
- Sign-in sessions can expire; an expired session costs one OTP in the web chat.
