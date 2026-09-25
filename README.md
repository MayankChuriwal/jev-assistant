# Jev Assistant

A chat assistant that completes real web tasks in its own Chrome. Each step, GPT-4.1 mini decides what to do and
writes every word the user reads (`assistant/mind.py`); TypeSafe's Jev picks the exact element mini described,
checks what each step did, and reads the user's replies (`assistant/jev.py`); code keeps the safety rails: nothing
is paid or booked without a yes in chat, and codes, passwords, and profile details never reach a model
(`assistant/driver.py`). The browser layer comes from [jev-ultrafast](https://github.com/browser-use/jev-ultrafast).

Design: `docs/superpowers/specs/2026-09-26-mini-jev-agent-design.md` (loop) and
`docs/superpowers/specs/2026-09-23-jev-assistant-design.md` (browser, chat, profile). `JEV_DRIVER=legacy` runs the
earlier loop, where Jev chose every operation.

## Setup

    uv sync
    cp .env.example .env      # add TYPESAFE_API_KEY and OPENAI_API_KEY
    uv run jev probe          # optional: check the smoke-set sites for 403s and captchas

## Chat

    uv run jev                # starts the hidden agent Chrome if needed, then serves the chat
                              # open http://127.0.0.1:8766/

Type a goal, such as "Order a Diet Coke on Instamart". The agent works in its own hidden Chrome and asks only for
what it needs, one question at a time, in plain messages: choices come as a numbered list with prices and you
reply in your own words; OTPs and CVVs go in a hidden field; a summary comes before anything is paid for or booked,
and only a clear yes goes ahead. A goal that names no app is asked once per kind of task ("Which app should I use
for groceries?") and remembered. Up to three tasks run at once (one per site). Messages sent
mid-task count as answers, changes, new tasks, cancellations, or status questions. Files the agent saves, such as
receipts or PDFs, arrive in chat as documents. When a control can't be named from the page (a canvas, a map,
an image-only button), the agent finds it on a masked screenshot.

The chat uses WhatsApp Cloud API shapes: a webhook comes in, send-message payloads go out, and files travel as
media ids. A WhatsApp channel can therefore replace `assistant/channel_web.py` without touching the conversation core
(`assistant/core.py`). The server listens on 127.0.0.1 only and checks a per-launch token. `JEV_DEBUG=1` adds
`/api/debug` with each task's recent steps.

## Traces and the smoke set

Every task writes a redacted trace to `~/.jev/traces/` when it ends; `uv run jev traces` lists the latest ones with
their outcome, question count, and quoted evidence. The live smoke set that defines v1 as done is in
`docs/smoke.md`. Known issues and their status are in `docs/known-issues.md`.

## The live bench

    uv run jev --bench                           # all 12 tasks in bench/tasks.toml, twice
    uv run jev --bench --only uber,instamart --repeat 1

The bench runs scripted goals on real sites through the chat: questions get the answers in `bench/tasks.toml`,
sign-in codes and captchas come to your chat page (keep http://127.0.0.1:8766/ open), and every final approval is
answered **no**, so nothing is bought or booked. The report lands in `~/.jev/bench/<run>/report.html`, with runs that
passed before and fail now marked. A bad step in any trace can be kept as a replay case and re-run without a browser:

    uv run jev bench-save ~/.jev/traces/<trace>.json 7 "op=ASK"
    uv run jev replay

## One task in the terminal

    uv run jev chrome         # hidden agent Chrome with its own profile
    uv run jev run "Order a Diet Coke on Instamart"

Questions are asked in the terminal; type `cancel` to stop.

## Use the loop

    from assistant.agent import Agent

    with Agent("https://en.wikipedia.org/wiki/Main_Page", "Open the article about Gödel's incompleteness theorems") as agent:
        for state in agent.run():
            print(state["elapsed_ms"], state["status"], state["history"][-1:])

Run with `uv run --env-file .env python script.py` after `uv run jev chrome`. Without an `asker`, goals that need
the user (logins, OTPs, files, approvals) stop at BLOCKED.

## Checks

    uv run ruff check .
    uv run pytest             # offline logic
    uv run pytest -m browser  # real headless Chrome on local fixtures
    node --check assistant/snapshot.js
    node --check assistant/static/app.js
