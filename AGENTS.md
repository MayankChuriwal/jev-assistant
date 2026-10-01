# Jev Assistant

Read `docs/superpowers/specs/2026-10-01-system-one-agent-design.md` before editing. Loop: Jev chooses each browser
step inside a subgoal; GPT-4.1 mini plans the subgoals, resolves what Jev is unsure about, and writes every word; the
gate guards every commit.

- Input is a natural-language goal. Task and site know-how lives in `skills/*.md` and `skills/sites/*.md`, never in
  code.
- Jev answers choice questions only (`jev.py`, `executor.py`); mini writes text (`planner.py`); code guards (`gate.py`,
  `rules.py`, `worker.py`).
- Targets map to observed elements. Model output never becomes selectors, coordinates, JavaScript, or file paths.
- Never retry a browser mutation. Log execution before observing its result.
- The agent drives only its own Chrome: `BU_NAME=jev`, `BU_CDP_URL` → port 9333. Never the user's everyday Chrome.
- Credentials live in `.env` (ignored). Tests never call paid APIs; browser tests use a throwaway headless Chrome.
- A DONE choice is not proof of success.
- Never write a skill, site note, or rule for a task or a site in `bench/unseen.toml`.
- Commit per plan task. Never push unless asked.

Checks: `uv run ruff check . && uv run pytest && uv run pytest -m browser && node --check assistant/snapshot.js`
