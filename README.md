# Jev Assistant

A chat assistant that does real web tasks in its own Chrome and asks only for what it needs. **Jev**, TypeSafe's
System One model, chooses every browser step: the operation and its element, whether the subgoal is done, what
blocks the page, and whether the last step worked. **GPT-4.1 mini** plans the subgoals, resolves the steps Jev is
unsure about, and writes every word you read. Code guards the rest: nothing is paid, booked, or sent without your
yes in chat (or, once you switch pre-approval on, your own "don't ask, under ₹N" or a standing approval), and codes,
passwords, and your IDs never reach a model. One known limit: an ID typed into plain chat is seen once by the router and
the memory job before it is saved; `jev memory set <key>` (typed hidden) avoids that.

It remembers you, in Postgres: your IDs (encrypted), addresses, default apps and preferences, standing approvals, the
site notes it learned and you saved, and habits: verified runs that a repeat errand follows, with Jev checking each
step.

Task and site know-how lives in markdown skills (`skills/`), not in code. The browser layer comes from
[jev-ultrafast](https://github.com/browser-use/jev-ultrafast).

Design: `docs/superpowers/specs/2026-10-01-system-one-agent-design.md` (Phase A) and
`docs/superpowers/specs/2026-10-01-phase-b-learning-design.md` (Phase B: memory, learning, replay). Known issues:
`docs/known-issues.md`.

## Setup

    brew install postgresql@18 && brew services start postgresql@18   # or any Postgres ≥ 16 with pg_trgm: JEV_DATABASE_URL
    uv sync
    cp .env.example .env      # add TYPESAFE_API_KEY and OPENAI_API_KEY
    uv run jev memory set phone "phone number"              # optional: typed hidden; no model ever sees it
    uv run jev probe          # optional: check sites for 403s and captchas
    uv run jev forget swiggy.com uber.com                   # sign the agent Chrome out of sites

The first start makes the `jev` database if it is missing, and, while no ID is stored yet, writes a new
`JEV_MEMORY_KEY` into `.env` (an empty `JEV_MEMORY_KEY=` line there is filled in, not duplicated). Keep that key: your
saved IDs are encrypted with it. If IDs are stored and the key is missing, the chat and `jev run` refuse to start
(a new key would lose them); put the key back, or `uv run jev memory forget id:<key>` (with the key lost, `jev memory`
hides IDs, so you need the key name, e.g. `id:phone`). A wrong key instead makes those IDs unreadable, and they are asked
for again. A `~/.jev/profile.json` from before is imported once. Memory needs the `pg_trgm` extension (in Postgres's
contrib set): the first start runs `CREATE EXTENSION`, so the database user needs the right to create it.

## Chat

    uv run jev                # starts the hidden agent Chrome if needed, then serves the chat
                              # open http://127.0.0.1:8766/

Ask for anything, several things at once if you like ("milk from Zepto and a cab home"). The agent works in its own
hidden Chrome and asks only for what it needs, in plain messages:

- **Choices** come as a numbered list with prices.
- **Hidden field:** codes, CVVs, and any ID a task needs that memory lacks (a phone number, an email, a bill's
  account number) go in a hidden field; an ID is kept, encrypted, for next time. A question there that lists options
  takes the option's number or its words too.
- **Before paying, booking, or sending:** you see a summary read from the page, and it waits for your yes. The
  exception is a message of your own that said not to ask and set a cap ("order it, don't ask, under ₹300"), and
  only when the page's payable total is within it. Without a cap, "don't ask" covers only a goal that buys nothing
  (a form, a message) on a page that shows no money. Pre-approval is off by default: until you set
  `JEV_PREAUTH=on` in `.env`, the agent still asks and says why.
- **Standing approvals:** after an order you approved, whose cart and rupee total were read from the page, the agent
  offers *Don't ask next time* (or *Keep asking*): the same site and cart, up to the total plus 20% (rounded up to
  the next ₹10), for 30 days. Or, once you have placed that order with the agent, say "always order my usual Zepto
  cart without asking, up to ₹300". A standing approval:
  - needs `JEV_PREAUTH=on` too (until then it only adds a line saying it would have covered the order);
  - is never used when your own message for that task set a cap: your cap wins;
  - applies only when the page's cart is fully readable and matches the rule's items and quantities, with no priced
    line it doesn't recognise, and the total and every money line are within the cap; otherwise the agent asks;
  - is announced each time it is used, and "stop that rule" ends it.
- **Which app:** a goal that names no app is asked once per kind of task and remembered.
- **What it learned:** after a task the agent may offer site notes (*Save* / *Skip*) and guesses about you
  (*Remember* / *No*). A learned note is kept only if you tap Save. Saved notes help the next run on that site;
  nothing in a note changes what needs your yes. `uv run jev run` asks the same in the terminal ("Save these notes?
  [y/N]").
- **Repeats:** an errand you've done before starts where it did and follows the steps that worked ("Same as last
  time: …"). Jev still checks every step, a step the page no longer offers or that Jev picks differently goes back to
  the normal loop, and the approval works as always. `jev run` records habits but doesn't replay them.
- **Several tasks:** up to three run at once, one per site.
- **Messages mid-task** count as answers, changes, new tasks, cancellations, or status questions.
- **Files** the agent saves arrive in chat as documents.

The chat uses WhatsApp Cloud API shapes: a webhook comes in, send-message payloads go out, and files travel as media
ids, so a WhatsApp channel can replace `assistant/channel_web.py` without touching the conversation core
(`assistant/core.py`). The server listens on 127.0.0.1 only and checks a per-launch token. `JEV_DEBUG=1` adds
`/api/debug` with each task's recent steps.

## Memory

Tell it in chat: "remember my office is 80 Residency Road", "what do you know about me?", "forget my office address",
"stop the Instamart rule", "undo that". An ID in a message (an account number, an email) is cut out by code, never by
a model, and kept encrypted. Or use the terminal:

    uv run jev memory                       # what it keeps, with refs; IDs by name only, never their values
    uv run jev memory set bescom_account "BESCOM account number"   # typed hidden
    uv run jev memory forget 12 id:phone rule:3 habit:5   # refs as `jev memory` lists them: a fact's number, id:, rule:, habit:
    uv run jev memory undo                  # the latest change; a forgotten item can be undone for a day
    uv run jev memory notes zeptonow.com    # saved learned notes: one host, or every host without one

`set` needs a key that starts with a letter and has 2–40 lowercase letters, digits, or underscores (`bescom_account`) and an optional label; its value is
typed at a hidden prompt. `forget` takes one or more refs, and a ref that fails doesn't stop the others. Without the
key, `jev memory` still lists and forgets, but IDs are hidden and `set` is refused.

Never stored: card numbers, CVVs, OTPs, passwords, PINs, Aadhaar, and PAN. Memory lives in the Postgres database at
`JEV_DATABASE_URL` (default `postgresql:///jev`). The key that encrypts IDs, `JEV_MEMORY_KEY`, is never in the database.

## Skills

Kind skills: `skills/<kind>.md` holds one kind of task (groceries, rides, travel, …). Each lists:

- what to ask up front;
- the usual plan and its rules;
- what the approval names;
- what the final report names.

Situation skills: `skills/sign-in.md`, `payment.md`, and `location.md` join a task when Jev sees that the page needs
them.

Site notes: `skills/sites/<host>.md` holds one site's start pages, search address, and quirks.

A new kind of task or a new site is one markdown file. Never write one for a task or a site in `bench/unseen.toml`.

## Traces and the bench

Every task writes a redacted trace to `~/.jev/traces/` when it ends. `uv run jev traces` lists the latest ones.

    uv run jev --bench                              # 24 known tasks × 2, plus 8 unseen tasks per run
    uv run jev --bench --only uber,instamart --repeat 1
    uv run jev --bench --seed 7                     # the same unseen sample again
    uv run jev --bench --learn                      # each task twice: pass 2 uses what pass 1 taught
    uv run jev --bench --learn --only instamart --keep

The bench runs scripted goals on real sites through the chat:

- Questions get the answers in `bench/tasks.toml`, then the bench defaults.
- Codes, captchas, and questions scripted as `@user` come to your chat page; keep http://127.0.0.1:8766/ open.
- Every final approval is answered **no**, so nothing is bought or booked.
- Every run starts from an empty memory of its own (a `bench_<run>` schema, dropped when `jev` stops; `--keep` keeps
  it). A bench run never reads or writes your memory. `--learn` and `--keep` work only with `--bench`.

The report lands in `~/.jev/bench/<run>/report.html`. It shows:

- the done bar: ≥ 80% known, ≥ 60% unseen, 0 unsafe, ≤ 2 s per step, ≤ $0.02 per task;
- results per category;
- Jev's margins and the escalations;
- regressions.

`--learn` runs each task as a pair in its own schema (`bench_<run>_<n>`, dropped after the pair unless `--keep`) and
writes `learn.html`, `pairs.json`, and `learn_summary.json` beside the report. For each task `learn.html` shows:

- both verdicts;
- the questions, and the repeats memory could have answered (a pass-2 question that is not a code, CVV, captcha, or
  approval and resembles a pass-1 one; an ID asked again counts);
- busy time, spend and steps;
- the habit match, matched steps and fallbacks;
- notes · facts/IDs saved, and the standing approvals that would have fired (memory hits are in `pairs.json`);
- the secret audit.

Its bar:

- pass 2 asks nothing memory could answer;
- over at least 10 tasks that passed both times, pass 2 takes ≤ 0.50× the busy time and ≤ 0.30× the spend of pass 1;
- pass 2 passes at least as many tasks as pass 1;
- at least 30% of the pass-1 failures pass in pass 2;
- 0 unsafe runs and 0 hidden values in clear (the audit reads the traces and every stored text).

Pass 1's passed checkout is kept as a standing approval in the pair's schema, so the report can say whether one would
have fired in pass 2; the gate only logs it on a bench run.

A Jev step from any trace can be kept as a replay case and asked again (this uses the paid API):

    uv run jev bench-save ~/.jev/traces/<trace>.json 7 "op=CLICK, target~search"
    uv run jev replay

## One task in the terminal

    uv run jev chrome         # hidden agent Chrome with its own profile
    uv run jev run "Order a Diet Coke on Instamart"

Questions are asked in the terminal; type `cancel` to stop.

## Use the worker

    from assistant.worker import Worker

    with Worker("https://en.wikipedia.org/wiki/Main_Page", "Open the article about Gödel's incompleteness theorems") as worker:
        for state in worker.steps():
            print(state["elapsed_ms"], state["status"], state["history"][-1:])

Run it with `uv run --env-file .env python script.py` after `uv run jev chrome`, with Postgres running: the worker
opens your memory. Without an `asker`, goals that need
you (codes, files, approvals) stop at BLOCKED.

## Checks

    uv run ruff check .
    uv run pytest             # offline logic; needs Postgres (JEV_TEST_DATABASE_URL, default postgresql:///postgres)
    uv run pytest -m browser  # real headless Chrome on local fixtures
    node --check assistant/snapshot.js
    node --check assistant/static/app.js
