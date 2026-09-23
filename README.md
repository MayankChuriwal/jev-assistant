# Jev Assistant

A chat assistant that completes real web tasks. TypeSafe's Jev chooses every browser operation; GPT-4.1 mini writes text only when needed. The core comes from [jev-ultrafast](https://github.com/browser-use/jev-ultrafast).

Design: `docs/superpowers/specs/2026-09-23-jev-assistant-design.md`

## Setup

    uv sync
    cp .env.example .env      # add TYPESAFE_API_KEY and OPENAI_API_KEY
    uv run jev probe          # optional: check the smoke-set sites for 403s and captchas

## Chat

    uv run jev                # starts the hidden agent Chrome if needed, then serves the chat
                              # open http://127.0.0.1:8766/

Type a goal, such as "Order a Diet Coke on Instamart". The agent works in its own hidden Chrome and asks only for
what it needs, one question at a time: buttons and lists for choices, a hidden field for OTPs and CVVs, and a
summary card before anything is paid for or booked. Up to three tasks run at once (one per site). Messages sent
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
