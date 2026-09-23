# Jev Assistant

A chat assistant that completes real web tasks. TypeSafe's Jev chooses every browser operation; GPT-4.1 mini writes text only when needed. The core comes from [jev-ultrafast](https://github.com/browser-use/jev-ultrafast).

Design: `docs/superpowers/specs/2026-09-23-jev-assistant-design.md`

## Setup

    uv sync
    cp .env.example .env      # add TYPESAFE_API_KEY and OPENAI_API_KEY
    uv run jev chrome         # hidden agent Chrome with its own profile
    uv run jev probe          # check the smoke-set sites for 403s and captchas

## Use the loop

    from assistant.agent import Agent

    with Agent("https://en.wikipedia.org/wiki/Main_Page", "Open the article about Gödel's incompleteness theorems") as agent:
        for state in agent.run():
            print(state["elapsed_ms"], state["status"], state["history"][-1:])

Run with `uv run --env-file .env python script.py` after `uv run jev chrome`. Plan 1 has no chat: goals that need
the user (logins, OTPs, files, approvals) stop at BLOCKED until Plan 2.

## Checks

    uv run ruff check .
    uv run pytest             # offline logic
    uv run pytest -m browser  # real headless Chrome on local fixtures
    node --check assistant/snapshot.js
