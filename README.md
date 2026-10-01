# Jev Assistant

A chat assistant that does real web tasks in its own Chrome and asks only for what it needs. **Jev**, TypeSafe's
System One model, chooses every browser step: the operation and its element, whether the subgoal is done, what
blocks the page, and whether the last step worked. **GPT-4.1 mini** plans the subgoals, resolves the steps Jev is
unsure about, and writes every word you read. Code guards the rest: nothing is paid, booked, or sent without your
yes in chat (or your own "don't ask, under ₹N"), and codes, passwords, and profile details never reach a model.

Task and site know-how lives in markdown skills (`skills/`), not in code. The browser layer comes from
[jev-ultrafast](https://github.com/browser-use/jev-ultrafast).

Design: `docs/superpowers/specs/2026-10-01-system-one-agent-design.md`. Known issues: `docs/known-issues.md`.

## Setup

    uv sync
    cp .env.example .env      # add TYPESAFE_API_KEY and OPENAI_API_KEY
    uv run jev probe          # optional: check sites for 403s and captchas

## Chat

    uv run jev                # starts the hidden agent Chrome if needed, then serves the chat
                              # open http://127.0.0.1:8766/

Ask for anything, several things at once if you like ("milk from Zepto and a cab home"). The agent works in its own
hidden Chrome and asks only for what it needs, in plain messages:

- **Choices** come as a numbered list with prices.
- **Hidden field:** codes, CVVs, and a phone number the profile lacks go in a hidden field.
- **Before paying, booking, or sending:** you see a summary read from the page, and it waits for your yes. The
  exception is a message of your own that said not to ask and set a cap ("order it, don't ask, under ₹300").
- **Which app:** a goal that names no app is asked once per kind of task and remembered.
- **Several tasks:** up to three run at once, one per site.
- **Messages mid-task** count as answers, changes, new tasks, cancellations, or status questions.
- **Files** the agent saves arrive in chat as documents.

The chat uses WhatsApp Cloud API shapes: a webhook comes in, send-message payloads go out, and files travel as media
ids, so a WhatsApp channel can replace `assistant/channel_web.py` without touching the conversation core
(`assistant/core.py`). The server listens on 127.0.0.1 only and checks a per-launch token. `JEV_DEBUG=1` adds
`/api/debug` with each task's recent steps.

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

The bench runs scripted goals on real sites through the chat:

- Questions get the answers in `bench/tasks.toml`, then the bench defaults.
- Codes, captchas, and questions scripted as `@user` come to your chat page; keep http://127.0.0.1:8766/ open.
- Every final approval is answered **no**, so nothing is bought or booked.

The report lands in `~/.jev/bench/<run>/report.html`. It shows:

- the done bar: ≥ 80% known, ≥ 60% unseen, 0 unsafe, ≤ 2 s per step, ≤ $0.02 per task;
- results per category;
- Jev's margins and the escalations;
- regressions.

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

Run it with `uv run --env-file .env python script.py` after `uv run jev chrome`. Without an `asker`, goals that need
you (codes, files, approvals) stop at BLOCKED.

## Checks

    uv run ruff check .
    uv run pytest             # offline logic
    uv run pytest -m browser  # real headless Chrome on local fixtures
    node --check assistant/snapshot.js
    node --check assistant/static/app.js
