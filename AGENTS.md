# Jev Assistant

Read `docs/superpowers/specs/2026-10-01-system-one-agent-design.md` and
`docs/superpowers/specs/2026-10-01-phase-b-learning-design.md` before editing. Loop: Jev chooses each browser
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
- Memory lives in Postgres (`memory.py`). An ID is encrypted with `JEV_MEMORY_KEY`, which never goes into the database;
  models see IDs only as `[profile:<key>]`. Never store card numbers, CVVs, OTPs, passwords, PINs, Aadhaar, or PAN
  (`privacy.storable`).
- Learned notes and habits are hints; standing approvals are the one stored thing the gate reads, and only under its own
  checks. Replay only chooses among Jev's picks.
- No macOS-only code: the agent must stay deployable.
- Never write a skill, site note, or rule for a task or a site in `bench/unseen.toml`.
- Commit per plan task. Never push unless asked.

Checks (Postgres running: `brew services start postgresql@18`): `uv run ruff check . && uv run pytest && uv run pytest -m browser && node --check assistant/snapshot.js`
