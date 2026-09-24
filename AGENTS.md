# Jev Assistant

Read `docs/superpowers/specs/2026-09-24-local-agent-reliability-design.md` before editing. Loop: page → indexed elements → one Jev operation + target → execution.

- Input is a natural-language goal. No site-specific scripts, plans, or field values.
- Jev chooses every operation and target in one request per step. Use only the chosen operation's target answer.
- Targets map to observed elements. Model output never becomes selectors, coordinates, JavaScript, or file paths.
- Never retry a browser mutation. Log execution before observing its result.
- The agent drives only its own Chrome: `BU_NAME=jev`, `BU_CDP_URL` → port 9333. Never the user's everyday Chrome.
- Credentials live in `.env` (ignored). Tests never call paid APIs; browser tests use a throwaway headless Chrome.
- A DONE choice is not proof of success.
- Commit per plan task. Never push unless asked.

Checks: `uv run ruff check . && uv run pytest && uv run pytest -m browser && node --check assistant/snapshot.js`

- Chat answers become structured facts; only a fresh Jev decision selects an action.
- Dispatch through `dispatch_once`; completion through `completion_report`. Unknown outcomes never retry.
- Secrets use task/origin/challenge/field scope. No card vault, device takeover, or inferred geolocation.
