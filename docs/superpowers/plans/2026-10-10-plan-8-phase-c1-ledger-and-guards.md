# Phase C, step C1 — the task ledger, approvals that wait well, and three new guards: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the running assistant a task ledger in Postgres (restart notices and a commit audit), control words read in code, stricter and fresher approvals, progress lines, and guards against private addresses, unasked account-security changes, and sending files from outside the downloads folder — all on today's web channel.

**Architecture:** A new `ledger.py` keeps three tables (`tasks`, `audit`, `settings`) on the connection `Memory` opens. `Core` writes a task's row as it runs and reads the active rows back at start to report what a restart cut short. The worker tells `Core` when it presses a commit and what the site then showed, for the audit row. Approval rules live in `gate.approved` (buttons, a late yes, unattended runs) and `Core.receive` (what a typed reply may approve). Two new pure checks: `net.private_url` and `rules.account_security`.

**Tech Stack:** Python 3.12, psycopg 3 on Postgres ≥ 16, pytest, ruff (line length 120, rules E/F/I), the standard-library HTTP server and the existing web chat.

**Spec:** `docs/superpowers/specs/2026-10-05-phase-c-always-on-design.md` — Sections 3.5, 4.4, 5, 8, 11, 12, 14 and 15.

## Global Constraints

- Python ≥ 3.12. ruff: line length 120, rules E, F, I. No new dependencies.
- Tests never call paid APIs, never open a database named `jev`, and never resolve DNS (Task 8 stubs it for every test).
- No macOS-only code: the agent must stay deployable.
- Codes and IDs never appear in the ledger, the audit table, a log, a trace, or a model payload. Goals, titles, steps and labels go into the ledger through `Memory.hide`.
- Nothing is ordered, paid, booked or sent without the user's yes bound to that cart. The gate stays the authority.
- A run whose `source` is `schedule`, `watch` or `suggestion` never uses a standing approval or a pre-authorisation (spec decision 8).
- Commit once per task. Never stage `.idea/`. Never push. Every commit message ends with exactly: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
- Before each commit: `uv run ruff check . && uv run pytest -q`. Task 11 also runs `uv run pytest -m browser -q` and `node --check assistant/snapshot.js`.

## Rulings (where this plan refines the spec)

1. `inbox` and `outbox` arrive in C2 with their first users. C1 creates `tasks`, `audit` and `settings`, although spec §15 lists all five under C1. Task 11 updates the spec.
2. Spec §12 rule 6 and §8: cancellation (from `stop` and `stop all`) is what the worker checks before every step and every commit. `Worker.run` already calls `stop_if_interrupted` before every action, commits included. The pause flag only stops new unattended runs from starting (the scheduler, C4). In C1 it is stored and toggled. Task 11 rewords the spec.
3. Audit outcomes add `step`: the site showed the pressed control was only a step. `failed` also covers a press the browser refused because the page had changed (nothing ran).
4. Progress lines are checked in Core's task loop after each tick, so a tick held in one long wait sends none until it returns. The code marks this with a `ponytail:` comment and Task 11 records it in `docs/known-issues.md`.
5. `net.private_url` checks an address once, when it is opened. The browser resolves the name again, so DNS rebinding isn't covered. A name that doesn't resolve isn't blocked, because the browser can't reach it either.
6. The approval question gets *Yes, place it* / *No* buttons on the web channel now, because typed replies get stricter. Spec §5 describes the buttons; C2 renders them on WhatsApp.
7. Spec rule 12 ("files only from its own media folder"): the folder is the agent's downloads folder (`~/.jev/downloads`, or `$JEV_HOME/downloads`), where the browser saves every file. Task 11 updates the wording.

## Review Focus

1. Postgres goes away mid-task: a ledger write fails, and the task and the chat carry on; only the record is missing. Test: Task 2, `test_a_ledger_that_fails_never_stops_a_task`.
2. Two tasks wait for approval and the user types "yes": nothing is approved, and the approval is shown again. Test: Task 5, `test_a_typed_yes_while_two_approvals_wait_approves_neither`.
3. A goal holding an ID ("Recharge 9876543210 on Paytm") goes into the ledger: the row holds the placeholder, never the digits. Test: Task 1, `test_a_task_row_keeps_its_goal_with_every_id_hidden`.
4. A confirmation page with no readable total: no "mismatch" alarm; the outcome is confirmed. Test: Task 3, `test_a_matching_unreadable_or_unapproved_total_is_no_mismatch`.
5. A start page a model chose points at 127.0.0.1 or the LAN: the task never opens it. Test: Task 8, `test_a_start_page_on_a_private_address_is_never_opened`.

## Files

| File | Responsibility | Tasks |
|---|---|---|
| `assistant/ledger.py` (new) | The `tasks`, `audit` and `settings` tables; `Ledger` and `NullLedger` | 1 |
| `assistant/core.py` | Ledger wiring, the restart notice, control words, the typed-yes rule, late and unattended approvals, progress lines, the files guard | 2, 4, 5, 6, 7, 8, 10 |
| `assistant/worker.py` | `source`; commit reports; the OPEN_URL guard | 3, 6, 8 |
| `assistant/gate.py` | Approval buttons and yes words; a late yes; unattended runs; audit outcomes; the confirmed total; account security | 3, 5, 6, 9 |
| `assistant/rules.py` | `ACCOUNT_SECURITY`, `ACCOUNT_GOAL`, `account_security()`, `account_goal()` | 9 |
| `assistant/net.py` (new) | `private_url()` | 8 |
| `assistant/server.py` | The ledger at start; `core.recover()` | 2 |
| `tests/test_ledger.py`, `tests/test_net.py` (new) | | 1, 8 |
| `tests/test_core.py`, `tests/test_worker.py`, `tests/test_gate.py`, `tests/test_rules.py`, `tests/conftest.py` | | 2–10 |
| `README.md`, `AGENTS.md`, `docs/known-issues.md`, the spec | Docs | 11, 12 |

Before Task 1, check the baseline: Postgres running (`brew services start postgresql@18`), then
`uv run ruff check . && uv run pytest -q` passes (1651 passed, 110 deselected at `dbd0f35`).

---

### Task 1: The ledger store

**Files:**
- Create: `assistant/ledger.py`
- Test: `tests/test_ledger.py` (new)

**Interfaces:**
- Consumes: `Memory.run(work)`, `Memory.hide(text)`, `Memory.schema` (`assistant/memory.py`).
- Produces:
  - `ledger.SOURCES = ("chat", "schedule", "watch", "suggestion")`, `ledger.UNATTENDED` (a frozenset of the last three), `ledger.ACTIVE`, `ledger.OUTCOMES`, `ledger.SETTINGS`.
  - `ledger.new_id() -> str`.
  - `class Ledger(memory)` with:
    - `start_task(task_id, goal, title, source="chat", schedule_id=None) -> None`
    - `touch_task(task_id, status, last_step="", commit_label="", commit_in_flight=False, title=None) -> None`
    - `end_task(task_id, status) -> None`
    - `task_row(task_id) -> dict | None`
    - `interrupted() -> list[dict]`, each row with `id, goal, title, source, commit_label, commit_in_flight, created_at`
    - `note_progress(task_id) -> None`
    - `audit_pressed(task_id, label, total="", host="", channel="web", by=None, approved_at=None) -> int`
    - `audit_outcome(audit_id, outcome) -> None`
    - `audits(task_id=None) -> list[dict]`
    - `setting(key) -> str | None`, `set_setting(key, value) -> None`, `paused() -> bool`
  - `class NullLedger()` with the same methods, keeping only settings, in memory.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ledger.py`:

```python
"""The ledger: task rows a restart can report, the commit audit, and settings that deny what they don't know."""

import pytest

from assistant.ledger import Ledger, NullLedger, new_id


@pytest.fixture
def ledger(memory):
    return Ledger(memory)


def test_a_task_row_keeps_its_goal_with_every_id_hidden(ledger, memory):
    memory.set_id("phone", "phone number", "9876543210")
    tid = new_id()
    ledger.start_task(tid, "Recharge 9876543210 on Paytm", "Paytm · recharge 9876543210")
    row = ledger.task_row(tid)
    assert "9876543210" not in row["goal"] + row["title"] and "[profile:phone]" in row["goal"]
    assert (row["status"], row["source"], row["commit_in_flight"]) == ("starting", "chat", False)


def test_a_restart_reports_each_active_task_once_with_any_commit_in_flight(ledger):
    running, finished = new_id(), new_id()
    ledger.start_task(running, "Order one Diet Coke on Instamart", "Instamart · Diet Coke")
    ledger.start_task(finished, "Find atta prices", "Atta prices")
    ledger.touch_task(running, "running", "Place order", "Place order", True)
    ledger.end_task(finished, "done")
    rows = ledger.interrupted()
    assert [(r["id"], r["commit_label"], r["commit_in_flight"]) for r in rows] == [(running, "Place order", True)]
    assert ledger.task_row(running)["status"] == "interrupted" and ledger.interrupted() == []


def test_a_commit_pressed_by_an_interrupted_task_becomes_unknown(ledger):
    cut, done = new_id(), new_id()
    for tid in (cut, done):
        ledger.start_task(tid, "Order one Diet Coke", "Diet Coke")
    pressed = ledger.audit_pressed(cut, "Place order", "₹121", "www.swiggy.com", by="user", approved_at=1791600000)
    confirmed = ledger.audit_pressed(done, "Place order", "₹79", "www.swiggy.com", by="standing")
    ledger.audit_outcome(confirmed, "confirmed")
    ledger.end_task(done, "done")
    ledger.touch_task(cut, "running", commit_label="Place order", commit_in_flight=True)
    ledger.interrupted()
    outcomes = {row["id"]: (row["outcome"], row["approval"]) for row in ledger.audits()}
    assert outcomes == {pressed: ("unknown", "explicit"), confirmed: ("confirmed", "standing")}
    assert ledger.audits(cut)[0]["approved_at"] is not None and ledger.audits(done)[0]["approved_at"] is None


def test_an_outcome_must_be_one_the_audit_knows(ledger):
    tid = new_id()
    ledger.start_task(tid, "Order", "Order")
    row = ledger.audit_pressed(tid, "Pay ₹79", by="user")
    ledger.audit_outcome(row, "mismatch")
    assert ledger.audits(tid)[0]["outcome"] == "mismatch"
    with pytest.raises(ValueError):
        ledger.audit_outcome(row, "maybe")


def test_settings_refuse_unknown_keys_and_an_unreadable_pause_flag_means_paused(ledger, memory):
    assert ledger.paused() is False  # never set: running
    ledger.set_setting("paused", "true")
    assert ledger.paused() is True
    ledger.set_setting("paused", "false")
    assert ledger.paused() is False
    memory.run(lambda cur: cur.execute("UPDATE settings SET value = 'perhaps' WHERE key = 'paused'"))
    assert ledger.paused() is True  # a value code doesn't recognise denies
    with pytest.raises(ValueError):
        ledger.set_setting("autopay", "on")
    with pytest.raises(ValueError):
        ledger.setting("autopay")


def test_progress_lines_are_counted(ledger):
    tid = new_id()
    ledger.start_task(tid, "Order", "Order")
    ledger.note_progress(tid)
    ledger.note_progress(tid)
    assert ledger.task_row(tid)["progress_lines"] == 2


def test_the_null_ledger_keeps_only_settings():
    ledger = NullLedger()
    ledger.start_task("x", "goal", "title")
    assert ledger.task_row("x") is None and ledger.interrupted() == [] and ledger.audit_pressed("x", "Pay") is None
    ledger.set_setting("paused", "true")
    assert ledger.paused() is True
    with pytest.raises(ValueError):
        ledger.set_setting("autopay", "on")


def test_a_second_start_on_the_same_schema_migrates_again_without_error(memory):
    Ledger(memory)
    again = Ledger(memory)
    tid = new_id()
    again.start_task(tid, "Order", "Order")
    assert again.task_row(tid)["goal"] == "Order"
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_ledger.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'assistant.ledger'`.

- [ ] **Step 3: Write the ledger**

Create `assistant/ledger.py`:

```python
"""The always-on runtime's records, in Postgres next to memory: a row per task (so a restart can say what it cut
short), an audit row per commit (what was pressed, under which approval, and what the site then showed), and a few
settings. Goals, titles, steps and labels are stored with every ID hidden; the audit keeps metadata only."""

import uuid

from psycopg import sql

SOURCES = ("chat", "schedule", "watch", "suggestion")
UNATTENDED = frozenset(SOURCES[1:])  # runs nobody started just now: they never use a standing approval
ACTIVE = ("starting", "waiting", "running", "paused")
OUTCOMES = ("pressed", "confirmed", "mismatch", "step", "failed", "unknown")
APPROVALS = {"user": "explicit", "standing": "standing", "preauth": "preauth"}
SETTINGS = ("paused", "wa_last_inbound_at")  # every key code reads: any other is refused

DDL = f"""
CREATE TABLE IF NOT EXISTS tasks (
  id text PRIMARY KEY,
  goal text NOT NULL,
  title text NOT NULL,
  source text NOT NULL CHECK (source IN ({", ".join(f"'{s}'" for s in SOURCES)})),
  schedule_id bigint,
  status text NOT NULL,
  last_step text NOT NULL DEFAULT '',
  commit_label text NOT NULL DEFAULT '',
  commit_in_flight boolean NOT NULL DEFAULT false,
  handoffs integer NOT NULL DEFAULT 0,
  progress_lines integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  ended_at timestamptz
);
CREATE TABLE IF NOT EXISTS audit (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT clock_timestamp(),
  task_id text NOT NULL,
  label text NOT NULL,
  total text NOT NULL DEFAULT '',
  host text NOT NULL DEFAULT '',
  channel text NOT NULL DEFAULT 'web',
  approval text NOT NULL CHECK (approval IN ('explicit', 'standing', 'preauth', 'none')),
  approved_at timestamptz,
  outcome text NOT NULL DEFAULT 'pressed' CHECK (outcome IN ({", ".join(f"'{o}'" for o in OUTCOMES)}))
);
CREATE TABLE IF NOT EXISTS settings (key text PRIMARY KEY, value text NOT NULL);
"""


def new_id():
    """A task's ledger id: unique across restarts, unlike the chat's t1, t2…"""
    return uuid.uuid4().hex


def is_paused(value):
    """The pause flag from its stored text: unset is off, "false" is off, anything else is on (deny by default)."""
    return value is not None and value != "false"


def checked(key):
    if key not in SETTINGS:
        raise ValueError(f"Unknown setting: {key!r}")
    return key


class Ledger:
    def __init__(self, memory):
        self.memory = memory
        self.migrate()

    def migrate(self):
        def work(cur):
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('jev-ledger-schema'))")
            cur.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(self.memory.schema)))
            cur.execute(DDL)

        self.memory.run(work)

    # ---- tasks

    def start_task(self, task_id, goal, title, source="chat", schedule_id=None):
        goal, title = self.memory.hide(goal), self.memory.hide(title)
        self.memory.run(lambda cur: cur.execute(
            "INSERT INTO tasks (id, goal, title, source, schedule_id, status) VALUES (%s, %s, %s, %s, %s, 'starting')",
            (task_id, goal, title, source, schedule_id)))

    def touch_task(self, task_id, status, last_step="", commit_label="", commit_in_flight=False, title=None):
        values = (status, self.memory.hide(last_step)[:200], self.memory.hide(commit_label)[:200], commit_in_flight,
                  None if title is None else self.memory.hide(title), task_id)
        self.memory.run(lambda cur: cur.execute(
            "UPDATE tasks SET status = %s, last_step = %s, commit_label = %s, commit_in_flight = %s, "
            "title = COALESCE(%s, title), updated_at = clock_timestamp() WHERE id = %s", values))

    def end_task(self, task_id, status):
        self.memory.run(lambda cur: cur.execute(
            "UPDATE tasks SET status = %s, ended_at = clock_timestamp(), updated_at = clock_timestamp() WHERE id = %s",
            (status, task_id)))

    def task_row(self, task_id):
        return self.memory.run(lambda cur: cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,)).fetchone())

    def interrupted(self):
        """Mark every task still active as interrupted (jev stopped while it ran) and return those rows, oldest
        first. A commit pressed for one of them whose result was never read becomes unknown."""
        def work(cur):
            rows = cur.execute(
                "UPDATE tasks SET status = 'interrupted', ended_at = clock_timestamp(), updated_at = clock_timestamp() "
                "WHERE status = ANY(%s) RETURNING id, goal, title, source, commit_label, commit_in_flight, created_at",
                (list(ACTIVE),)).fetchall()
            if rows:
                cur.execute("UPDATE audit SET outcome = 'unknown' WHERE outcome = 'pressed' AND task_id = ANY(%s)",
                            ([row["id"] for row in rows],))
            return rows

        return sorted(self.memory.run(work), key=lambda row: row["created_at"])

    def note_progress(self, task_id):
        self.memory.run(lambda cur: cur.execute(
            "UPDATE tasks SET progress_lines = progress_lines + 1, updated_at = clock_timestamp() WHERE id = %s",
            (task_id,)))

    # ---- the audit

    def audit_pressed(self, task_id, label, total="", host="", channel="web", by=None, approved_at=None):
        """An audit row written just before a commit's control is pressed; returns its id. `approved_at` is a Unix
        time, or None."""
        values = (task_id, self.memory.hide(label)[:200], total or "", host or "", channel, APPROVALS.get(by, "none"),
                  approved_at)
        return self.memory.run(lambda cur: cur.execute(
            "INSERT INTO audit (task_id, label, total, host, channel, approval, approved_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, to_timestamp(%s)) RETURNING id", values).fetchone()["id"])

    def audit_outcome(self, audit_id, outcome):
        if outcome not in OUTCOMES:
            raise ValueError(f"Unknown outcome: {outcome!r}")
        self.memory.run(lambda cur: cur.execute("UPDATE audit SET outcome = %s WHERE id = %s", (outcome, audit_id)))

    def audits(self, task_id=None):
        query = "SELECT * FROM audit" + (" WHERE task_id = %s" if task_id else "") + " ORDER BY id"
        return self.memory.run(lambda cur: cur.execute(query, (task_id,) if task_id else ()).fetchall())

    # ---- settings

    def setting(self, key):
        row = self.memory.run(lambda cur: cur.execute("SELECT value FROM settings WHERE key = %s",
                                                      (checked(key),)).fetchone())
        return row["value"] if row else None

    def set_setting(self, key, value):
        self.memory.run(lambda cur: cur.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (checked(key), str(value))))

    def paused(self):
        return is_paused(self.setting("paused"))


class NullLedger:
    """The ledger's interface keeping nothing but settings, in memory: for a Core run without the database ledger
    (most tests, tools)."""

    def __init__(self):
        self.values = {}

    def start_task(self, *args, **kwargs):
        pass

    touch_task = end_task = note_progress = audit_outcome = start_task

    def task_row(self, task_id):
        return None

    def interrupted(self):
        return []

    def audit_pressed(self, *args, **kwargs):
        return None

    def audits(self, task_id=None):
        return []

    def setting(self, key):
        return self.values.get(checked(key))

    def set_setting(self, key, value):
        self.values[checked(key)] = str(value)

    def paused(self):
        return is_paused(self.setting("paused"))
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_ledger.py -q`
Expected: 8 passed.

- [ ] **Step 5: Run the checks and commit**

```bash
uv run ruff check . && uv run pytest -q
git add assistant/ledger.py tests/test_ledger.py
git commit -m "feat: a task ledger in Postgres — task rows a restart can report, a commit audit, settings that deny what they don't know

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Core keeps the ledger and reports what a restart cut short

**Files:**
- Modify: `assistant/core.py` (imports; `Task.__init__`; `Core.__init__`; new `keep`, `track`, `commit_event`, `channel_name`, `recover`, `decide_restart`; `new_task`; `run`; `tapped`)
- Modify: `assistant/server.py` (`serve`)
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: Task 1's `Ledger`, `NullLedger`, `new_id`.
- Produces:
  - `Task(tid, goal, single=False, source="chat")` with `task.source`, `task.ledger_id`, `task.audit_id`.
  - `Core(..., ledger=None, wall=time.time)`, with `core.ledger`, `core.wall`, `core.ledger_errors`.
  - `core.keep(write, *args)`, which returns the write's result, or None when it raised.
  - `core.new_task(goal, script=None, single=False, source="chat")`.
  - `core.commit_event(task, event, info=None)`: Task 3's worker calls it through `worker.on_commit(event, info)`.
  - `core.recover()`.

- [ ] **Step 1: Give the test world a ledger and a wall clock**

In `tests/test_core.py`, add the import after `from assistant.channel_web import WebChannel`:

```python
from assistant.ledger import Ledger
```

In the `world` fixture, replace

```python
    w = SimpleNamespace(scripts={}, hosts={}, agents={}, late={}, classified=[], intents=[], clock=Clock(), n=0,
                        intakes={}, splits={}, made={}, learned=None, known=[], memory_outs=[])
    w.channel = WebChannel(tmp_path / "media")
```

with

```python
    w = SimpleNamespace(scripts={}, hosts={}, agents={}, late={}, classified=[], intents=[], clock=Clock(), n=0,
                        intakes={}, splits={}, made={}, learned=None, known=[], memory_outs=[], wall=Clock())
    w.channel = WebChannel(tmp_path / "media")
    w.ledger = Ledger(store())
```

and replace the last line of the `conversation.Core(` call,

```python
        memory_job=lambda message, known: w.known.append(known) or w.memory_outs.pop(0))
```

with

```python
        memory_job=lambda message, known: w.known.append(known) or w.memory_outs.pop(0), ledger=w.ledger,
        wall=w.wall)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_core.py`:

```python
# ---- Plan 8 Task 2: the task ledger and the restart notice


def ledger_rows(world):
    return world.ledger.memory.run(lambda cur: cur.execute("SELECT * FROM tasks ORDER BY created_at").fetchall())


def test_a_task_is_kept_in_the_ledger_from_start_to_finish(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS), tell("Ordered.")]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    assert until(lambda: ledger_rows(world)[0]["status"] == "running")
    tap(world, "q1:o1")
    assert until(lambda: ledger_rows(world)[0]["status"] == "done")
    row = ledger_rows(world)[0]
    assert (row["goal"], row["source"], row["commit_in_flight"]) == ("Order Diet Coke", "chat", False)
    assert row["ended_at"] is not None


def test_a_commit_the_worker_reports_gets_an_audit_row_and_its_outcome(world):
    seen = []

    def pay(agent):
        agent.on_commit("pressed", {"label": "Pay ₹79", "total": "₹79", "host": "shop.test", "by": "user"})
        seen.append(world.ledger.task_row(world.core.tasks["t1"].ledger_id)["commit_in_flight"])
        agent.on_commit("confirmed")

    world.scripts["Order Diet Coke"] = [pay]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "done")
    audit = world.ledger.audits(world.core.tasks["t1"].ledger_id)
    assert [(a["label"], a["total"], a["approval"], a["outcome"], a["channel"]) for a in audit] == \
        [("Pay ₹79", "₹79", "explicit", "confirmed", "web")]
    assert seen == [True]


def test_a_restart_reports_what_it_cut_short_and_retries_only_on_your_tap(world):
    cut = conversation.new_id()
    world.ledger.start_task(cut, "Order one Diet Coke on Instamart", "Instamart · Diet Coke")
    world.ledger.touch_task(cut, "running", "Place order", "Place order", True)
    world.core.recover()
    assert bodies(world) == ["I restarted while doing Instamart · Diet Coke. Retry?\nPlace order may have gone "
                             "through; check it before retrying."]
    assert world.core.tasks == {}  # nothing runs by itself
    retry, leave = buttons(world)
    assert retry.endswith(":retry") and leave.endswith(":leave")
    tap(world, retry)
    assert until(lambda: "Order one Diet Coke on Instamart" in world.made)
    tap(world, retry)  # a second tap starts nothing more
    assert len(world.core.tasks) == 1


def test_leave_it_starts_nothing(world):
    world.ledger.start_task(conversation.new_id(), "Find atta prices", "Atta prices")
    world.core.recover()
    tap(world, buttons(world)[1])
    assert until(lambda: "OK, I'll leave it." in bodies(world)) and world.core.tasks == {}


def test_a_ledger_that_fails_never_stops_a_task(world, monkeypatch):
    def broken(*args, **kwargs):
        raise conversation.StoreError("Postgres stopped answering")

    for name in ("start_task", "touch_task", "end_task"):
        monkeypatch.setattr(world.ledger, name, broken)
    world.scripts["Order Diet Coke"] = [tell("Ordered.")]
    text(world, "Order Diet Coke")
    assert until(lambda: "Ordered." in bodies(world))
    assert until(lambda: world.core.tasks["t1"].status == "done") and world.core.ledger_errors
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/test_core.py -q -k "ledger or restart or leave_it or commit_the_worker"`
Expected: FAIL, `TypeError: Core.__init__() got an unexpected keyword argument 'ledger'`.

- [ ] **Step 4: Teach Core the ledger**

In `assistant/core.py`:

(a) Add to the imports, after `from . import learn as learning`:

```python
from .ledger import NullLedger, new_id
```

(b) In `Task.__init__`, change the signature and add one line at the end of the method:

```python
class Task:
    def __init__(self, tid, goal, single=False, source="chat"):
        self.id, self.goal, self.title, self.host = tid, goal, goal[:60], None
        self.status, self.worker, self.updates = "starting", None, []
        self.cancelled, self.sent_files, self.problems = False, 0, 0
        self.skill, self.preauth, self.cap, self.url, self.single = None, False, None, None, single
        self.script, self.bench = None, {"gate": None, "asked": []}  # a bench run: scripted replies
        self.habit, self.sameness = None, None  # the saved habit this goal repeats
        self.source, self.ledger_id, self.audit_id = source, new_id(), None  # who started it; its ledger rows
```

(c) In `Core.__init__`, extend the signature's last line and add three lines at the end of the method:

```python
                 clock=time.monotonic, poll=1.0, ledger=None, wall=time.time):
```

```python
        self.ledger = ledger if ledger is not None else NullLedger()
        self.wall = wall  # wall-clock time: what "an hour later" and "at night" mean
        self.ledger_errors = []  # ledger writes that failed: the work went on without its record
```

(d) Add these methods to `Core`, after `hidden`:

```python
    def keep(self, write, *args):
        """A ledger write or read: a store failure is noted and never stops a task or the chat."""
        try:
            return write(*args)
        except Exception as error:  # the ledger is a record: Postgres going away must not stop the work it records
            self.ledger_errors = (self.ledger_errors + [f"{type(error).__name__}: {error}"[:200]])[-20:]
            return None

    def track(self, task):
        """The task's ledger row after a tick: status, last step, title, and any commit whose result isn't read yet."""
        state = task.worker.state if task.worker else {}
        history, commit = state.get("history") or [], state.get("commit")
        self.keep(self.ledger.touch_task, task.ledger_id, task.status,
                  history[-1].get("action", "") if history else "", (commit or {}).get("label", ""), bool(commit),
                  task.title)

    def commit_event(self, task, event, info=None):
        """The worker's word on a commit: "pressed" (just before the control is pressed) opens an audit row and marks
        the commit in flight; any other event is the outcome the site then showed."""
        info = info or {}
        if event == "pressed":
            task.audit_id = self.keep(self.ledger.audit_pressed, task.ledger_id, info.get("label", ""),
                                      info.get("total", ""), info.get("host", ""), self.channel_name(),
                                      info.get("by"), info.get("approved_at"))
            self.keep(self.ledger.touch_task, task.ledger_id, task.status, info.get("label", ""),
                      info.get("label", ""), True, task.title)
        elif task.audit_id is not None:
            self.keep(self.ledger.audit_outcome, task.audit_id, event)
            task.audit_id = None

    def channel_name(self):
        return getattr(self.channel, "name", "web")

    def recover(self):
        """At start: one message for each task the last stop cut short, with Retry (its goal as a new task) or Leave
        it. A commit in flight is named: it may have gone through. Nothing is ever retried by itself."""
        for row in self.keep(self.ledger.interrupted) or []:
            key = f"restart:{next(self.offer_ids)}"
            self.offers[key] = row["goal"]
            text = f"I restarted while doing {row['title']}. Retry?"
            if row["commit_in_flight"] and row["commit_label"]:
                text += f"\n{row['commit_label']} may have gone through; check it before retrying."
            self.buttons(text, [(f"{key}:retry", "Retry"), (f"{key}:leave", "Leave it")])

    def decide_restart(self, rest):
        number, _, choice = rest.partition(":")
        goal = self.offers.pop(f"restart:{number}", None)
        if goal is None:
            return None  # decided already
        if choice == "retry":
            return self.new_task(goal)
        return self.say("OK, I'll leave it.")
```

(e) Replace `new_task` with:

```python
    def new_task(self, goal, script=None, single=False, source="chat"):
        # a bench run never splits; the ledger write happens outside the lock, so a slow store never holds up the chat
        task = Task(f"t{next(self.task_ids)}", goal, single=single or script is not None, source=source)
        task.script = script
        self.keep(self.ledger.start_task, task.ledger_id, goal, task.title, source)
        with self.lock:  # the chat, a task's split, and the bench thread all add tasks while others read them
            self.tasks[task.id] = task
            threading.Thread(target=self.run, args=(task,), name=f"jev-{task.id}", daemon=True).start()
        return task
```

(f) In `tapped`, replace

```python
        if kind in {"rule", "note", "guess"}:  # an offer posted after a task
            return {"rule": self.decide_rule, "note": self.decide_note, "guess": self.decide_guess}[kind](rest)
```

with

```python
        if kind in {"rule", "note", "guess", "restart"}:  # an offer posted after a task, or at start
            return {"rule": self.decide_rule, "note": self.decide_note, "guess": self.decide_guess,
                    "restart": self.decide_restart}[kind](rest)
```

(g) In `run`, replace the block from `if not self.acquire(task):` to `self.deliver_files(task)` with:

```python
            if not self.acquire(task):
                return self.say(f"Cancelled {task.title} before it started.")
            self.track(task)
            task.worker = self.make_worker(task, task.url, lambda m: self.ask(task, m), lambda m: self.send(m, task))
            task.worker.bench = task.script is not None
            task.worker.interrupted = lambda: task.cancelled or bool(task.updates)
            task.worker.on_commit = lambda event, info=None: self.commit_event(task, event, info)
            self.update_typing()
            while task.worker.state["status"] not in STOPPED and not task.cancelled:
                self.apply_updates(task)
                try:
                    task.worker.command("tick")
                    task.problems = 0
                except (ValueError, RuntimeError) as error:
                    if not self.problem(task, error):
                        break
                self.deliver_files(task)
                self.track(task)
```

and, in the `finally:` block of `run`, add one line right after `task.status = state if state in STOPPED else ("cancelled" if task.cancelled else "failed")`:

```python
            self.keep(self.ledger.end_task, task.ledger_id, task.status)
```

- [ ] **Step 5: Start the ledger with the chat**

In `assistant/server.py` `serve()`, replace

```python
    channel = WebChannel(home() / "media")
    try:
        core = Core(channel, memory=memory)
        httpd = ChatServer(core, channel, port=port)
```

with

```python
    from .ledger import Ledger
    from .memory import StoreError

    channel = WebChannel(home() / "media")
    try:
        ledger = Ledger(memory)
    except StoreError as error:
        print(f"jev couldn't open its task ledger: {error}", file=sys.stderr)
        return release(1)
    try:
        core = Core(channel, memory=memory, ledger=ledger)
        httpd = ChatServer(core, channel, port=port)
```

and right after `print(f"Chat: http://127.0.0.1:{httpd.server_port}/  (Ctrl+C stops)", flush=True)` add:

```python
    core.recover()  # tasks the last stop cut short are reported; none is retried by itself
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest tests/test_core.py -q`
Expected: all pass, including the 5 new tests.

- [ ] **Step 7: Run the checks and commit**

```bash
uv run ruff check . && uv run pytest -q
git add assistant/core.py assistant/server.py tests/test_core.py
git commit -m "feat: each task is kept in the ledger; a restart reports what it cut short, with Retry or Leave it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The worker reports each commit, and the confirmed total is checked

**Files:**
- Modify: `assistant/worker.py` (class attribute `on_commit`; new `report_commit`; `run`)
- Modify: `assistant/gate.py` (`bind`; `watch`; new `confirmed_total`)
- Test: `tests/test_gate.py` (its `Fake` gains `report_commit`), `tests/test_worker.py`

**Interfaces:**
- Consumes: Task 2's `Core.commit_event`, through `worker.on_commit(event, info)`.
- Produces:
  - `Worker.on_commit = None` (class attribute).
  - `Worker.report_commit(event, **info)`. Events: `"pressed"` with `label`, `total`, `host`, `by`, `approved_at`; then exactly one of `"confirmed"`, `"mismatch"`, `"step"`, `"failed"`, `"unknown"`.
  - `gate.confirmed_total(approved, text) -> (str | None, bool)`.
  - Every approval dict now carries `"at"`: the Unix time of the yes.

- [ ] **Step 1: Give test_gate's Fake worker a report list**

In `tests/test_gate.py`, class `Fake`: in `__init__` replace

```python
        self.replies, self.asked, self.said = list(replies), [], []
```

with

```python
        self.replies, self.asked, self.said, self.reported = list(replies), [], [], []
```

and add the method after `say`:

```python
    def report_commit(self, event, **info):
        self.reported.append(event)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_gate.py`:

```python
# ---- Plan 8 Task 3: the audit hears each commit; a confirmed total is checked


@pytest.mark.parametrize("verdicts, outcome", [(["confirmed"], "confirmed"), (["failed"], "failed"),
                                              (["next_step"], "step")])
def test_each_commit_outcome_is_reported_for_the_audit(monkeypatch, verdicts, outcome):
    w = Fake(fake_page(text=SHOP))
    w.state["commit"] = {"label": "Pay ₹79", "since": 0, "total": "₹79", "by": "user"}
    queue = list(verdicts)
    monkeypatch.setattr(gate.jev, "after_commit", lambda *a: queue.pop(0))
    gate.watch(w, "Pay ₹79", "Order a coke")
    assert w.reported == [outcome]


def test_an_outcome_nobody_can_read_is_reported_unknown(monkeypatch):
    w = Fake(fake_page())
    w.state["commit"] = {"label": "Pay ₹79", "since": 0}
    monkeypatch.setattr(gate.jev, "after_commit", lambda *a: "unclear")
    with pytest.raises(Halt):
        gate.watch(w, "Pay ₹79", "Order a coke")
    assert w.reported == ["unknown"]


def test_a_confirmed_total_that_differs_from_the_approved_one_is_reported(monkeypatch):
    w = Fake(fake_page(text="Order placed\nOrder total ₹99\nArriving in 10 minutes"))
    w.state["commit"] = {"label": "Pay ₹79", "since": 0, "total": "₹79", "by": "user"}
    monkeypatch.setattr(gate.jev, "after_commit", lambda *a: "confirmed")
    gate.watch(w, "Pay ₹79", "Order a coke")
    assert w.reported == ["mismatch"]
    assert w.said == ["Check this order: the site confirmed ₹99, but you approved ₹79."]
    assert w.state["done"] == ["Pay ₹79"]


@pytest.mark.parametrize("text, approved", [("Order placed\nOrder total ₹79.00", "₹79"),
                                            ("Order placed\nThanks for shopping", "₹79"),
                                            ("Order placed\nOrder total ₹99", "")])
def test_a_matching_unreadable_or_unapproved_total_is_no_mismatch(monkeypatch, text, approved):
    w = Fake(fake_page(text=text))
    w.state["commit"] = {"label": "Pay ₹79", "since": 0, "total": approved, "by": "user"}
    monkeypatch.setattr(gate.jev, "after_commit", lambda *a: "confirmed")
    gate.watch(w, "Pay ₹79", "Order a coke")
    assert w.reported == ["confirmed"] and w.said == []
```

Append to `tests/test_worker.py`:

```python
# ---- Plan 8 Task 3: each commit is reported, before it is pressed

CHECKOUT = "Checkout\nDiet Coke 300 ml × 1 — ₹40\nDelivery ₹39\nTotal ₹79\nPay ₹79"


def test_a_commit_is_reported_before_it_is_pressed_and_again_with_its_outcome(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "yes"}], page=fake_page(text=CHECKOUT))
    seen = []
    w.on_commit = lambda event, info: seen.append((event, info, w.browser.act.call_count))
    script_planner(monkeypatch, turn(subgoals=[sub("place the order", kind="commit")]), turn())
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Pay"})
    jev_checks(monkeypatch)
    for _ in range(3):  # plan; ask and bind the yes; press it and see it confirmed
        w.command("tick")
    (pressed, info, presses_before), (outcome, _, _) = seen
    assert (pressed, outcome, presses_before) == ("pressed", "confirmed", 0)
    assert (info["label"], info["total"], info["host"], info["by"]) == ("Pay ₹79", "₹79", "shop.test", "user")
    assert info["approved_at"] is not None


def test_a_commit_the_browser_refuses_on_a_changed_page_is_reported_failed(tmp_path):
    w = worker_with(tmp_path)
    seen = []
    w.on_commit = lambda event, info: seen.append(event)
    w.browser.act.side_effect = StalePage("Page changed since this decision. Observe again.")
    page = w.state["page"]
    with pytest.raises(StalePage):
        w.run(page, "CLICK", next(a for a in page["actions"] if a["label"] == "Pay ₹79"), {}, None, commit="Pay ₹79")
    assert seen == ["pressed", "failed"]


def test_an_audit_hook_that_fails_never_stops_the_commit(tmp_path, monkeypatch):
    w = worker_with(tmp_path, replies=[{"text": "yes"}], page=fake_page(text=CHECKOUT))

    def broken(event, info):
        raise RuntimeError("store down")

    w.on_commit = broken
    script_planner(monkeypatch, turn(subgoals=[sub("place the order", kind="commit")]), turn())
    script_jev(monkeypatch, {"next": "CLICK", "click_target": "Pay"})
    jev_checks(monkeypatch)
    for _ in range(3):
        w.command("tick")
    assert w.state["done"] == ["Pay ₹79"]
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/test_gate.py tests/test_worker.py -q -k "reported or audit_hook or mismatch or unreadable_or_unapproved"`
Expected: FAIL (`AssertionError: assert [] == ['confirmed']` and similar: nothing reports yet).

- [ ] **Step 4: Report commits from the worker**

In `assistant/worker.py`:

(a) Add a class attribute after `bench = False  # a bench run: a pre-authorisation is logged, never used`:

```python
    on_commit = None  # set by the chat: told when a commit is pressed and what the site then showed (its audit row)
```

(b) Add this method after `pending`:

```python
    def report_commit(self, event, **info):
        """Tell the chat about a commit for its audit row: "pressed" just before the control is pressed, then the
        outcome. A failure there never stops the task."""
        if self.on_commit:
            try:
                self.on_commit(event, info)
            except Exception:  # the audit is a record: it never decides whether the task goes on
                pass
```

(c) In `run`, replace

```python
        try:
            try:
                s["browser"].act(operation, action, page, **inputs)
            except StalePage:  # nothing ran: on a page whose text alone moved, the decision still holds
                if commit or cleared or s["relaying"]:
                    raise  # a commit runs only on the page the user approved, a step Jev cleared only on the page it
                    # judged (decided again, through the gate), a captcha square only as picked
```

with

```python
        if commit:  # recorded before the press: a stop in the middle still leaves the record
            approval = s["approval"] or {}
            self.report_commit("pressed", label=commit, total=approval.get("amount") or "",
                               host=rules.host(page["url"]), by=approval.get("by"), approved_at=approval.get("at"))
        try:
            try:
                s["browser"].act(operation, action, page, **inputs)
            except StalePage:  # nothing ran: on a page whose text alone moved, the decision still holds
                if commit or cleared or s["relaying"]:
                    if commit:
                        self.report_commit("failed")  # nothing ran: the browser refused a page that had changed
                    raise  # a commit runs only on the page the user approved, a step Jev cleared only on the page it
                    # judged (decided again, through the gate), a captcha square only as picked
```

and in the `except ExecutionUncertain as error:` block, add this as its first line:

```python
            if commit:
                self.report_commit("unknown")
```

- [ ] **Step 5: Report outcomes from the gate and check the confirmed total**

In `assistant/gate.py`:

(a) In `bind`, add `"at": time.time()` to the approval dict:

```python
    state["approval"] = {"action_label": label, "amount": total or "", "host": rules.host(page["url"]),
                         "step": len(state["history"]), "target": target, "within": action.get("within"), "by": by,
                         "cart": cart(page["text"]), "snapshot": snapshot(page["text"]), "at": time.time()}
```

(b) Add this function right before `def watch(w, label, goal):`:

```python
def confirmed_total(approved, text):
    """(the total a confirmation page shows, whether it differs from the approved total). No approved total, or none
    readable on the page, is never a difference: the user is told only what code can see."""
    shown = payable(text)
    one, other = amount(approved or ""), amount(shown or "")
    return shown, one is not None and other is not None and round(one, 2) != round(other, 2)
```

(c) Replace the body of `watch` from `s["commit"] = None` inside the processing branch to the end of the function, so that it reads:

```python
def watch(w, label, goal):
    """After a commit: wait (no planner) while the site works; the outcome becomes a note for the planner and the
    audit row's outcome."""
    s = w.state
    commit = s["commit"]
    since, told, unclear = commit["since"], False, 0
    while True:
        verdict = jev.after_commit(s["page"], goal, label)
        if verdict == "processing":
            if w.elapsed() - since >= w.processing_ms:
                s["commit"] = None
                w.report_commit("unknown")
                raise Halt("blocked", f"{label} went through, but after {w.processing_ms // 60000} minutes the site "
                                      "still shows it in progress. I stopped watching; check the site or its app "
                                      "for updates.")
            if not told:
                told = True
                w.say(f"{label}: the site is still working on it. I'll tell you as soon as it's confirmed.")
        elif verdict == "unclear" and unclear == 0:
            unclear += 1
        else:
            break
        w.stop_if_interrupted()
        time.sleep(w.outcome_pause)
        w.observe()
    s["commit"] = None
    if verdict == "next_step":
        w.report_commit("step")
        s["notes"].append(f"{label} was only a step; nothing is ordered or booked yet. The final control needs its "
                          "own approval.")
    elif verdict == "confirmed":
        shown, differs = confirmed_total(commit.get("total") or "", s["page"]["text"])
        w.report_commit("mismatch" if differs else "confirmed")
        if differs:
            w.say(f"Check this order: the site confirmed {tight(shown)}, but you approved {tight(commit['total'])}.")
            s["notes"].append(f"The site confirmed {tight(shown)} for {label}, but the approved total was "
                              f"{tight(commit['total'])}: tell the user both.")
        s["done"].append(label)
        s["orders"].append({"label": label, "site": commit.get("site") or s["site"], "cart": commit.get("cart") or [],
                            "total": commit.get("total") or "", "by": commit.get("by")})
        s["notes"].append(f"Done: {label}, confirmed by the site. Report every detail the page shows.")
    elif verdict == "failed":
        w.report_commit("failed")
        s["notes"].append(f"{label} failed; trying again needs a new approval.")
    else:
        w.report_commit("unknown")
        raise Halt("blocked", f"I can't tell whether {label} went through, so I stopped rather than risk doing it "
                              "twice. Please check the site or its app before trying again.")
    s["reason"] = "commit"
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest tests/test_gate.py tests/test_worker.py -q`
Expected: all pass.

- [ ] **Step 7: Run the checks and commit**

```bash
uv run ruff check . && uv run pytest -q
git add assistant/worker.py assistant/gate.py tests/test_gate.py tests/test_worker.py
git commit -m "feat: each commit is reported before it is pressed and again with its outcome; a confirmed total that differs from the approved one is told at once

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Control words, read in code

**Files:**
- Modify: `assistant/core.py` (constant `CONTROL`; `receive`; new `control`, `paused`)
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: Task 2's `Core.keep` and `Core.ledger`; Task 1's `set_setting` and `paused`.
- Produces: `core.control(word, question, event)` and `core.paused() -> bool`. The `paused` setting holds `"true"` or `"false"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_core.py`:

```python
# ---- Plan 8 Task 4: control words, read in code


def test_stop_cancels_the_task_that_is_asking_without_any_model(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    classified = len(world.classified)
    text(world, "  Stop ")
    agent = world.agents["Order Diet Coke"]
    assert until(lambda: agent.replies == [{"cancel": True}]) and len(world.classified) == classified


def test_stop_with_two_tasks_running_and_no_question_asks_which(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate)]
    world.scripts["Book a cab"] = [hold(gate)]
    world.hosts["Book a cab"] = "cabs.test"
    text(world, "Order Diet Coke")
    text(world, "Book a cab")
    assert until(lambda: len(world.core.tasks) == 2 and
                 all(t.status == "running" for t in world.core.tasks.values()))
    text(world, "stop")
    assert until(lambda: bodies(world)[-1] == "Which task should I stop?")
    gate.set()


def test_stop_all_stops_every_task_and_pauses_until_resume(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate), tell("never")]
    world.scripts["Book a cab"] = [hold(gate), tell("never")]
    world.hosts["Book a cab"] = "cabs.test"
    text(world, "Order Diet Coke")
    text(world, "Book a cab")
    assert until(lambda: len(world.core.tasks) == 2 and
                 all(t.status == "running" for t in world.core.tasks.values()))
    text(world, "stop all")
    gate.set()
    assert until(lambda: all(t.status == "cancelled" for t in world.core.tasks.values()))
    assert world.ledger.paused() is True and "never" not in bodies(world)
    text(world, "resume")
    assert world.ledger.paused() is False and bodies(world)[-1] == "Resumed schedules, watches and suggestions."


def test_pause_keeps_running_tasks_going(world):
    gate = threading.Event()
    world.scripts["Order Diet Coke"] = [hold(gate), tell("Ordered.")]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "running")
    text(world, "pause")
    gate.set()
    assert until(lambda: "Ordered." in bodies(world)) and world.ledger.paused() is True


def test_status_lists_tasks_in_code(world):
    world.scripts["Order Diet Coke"] = [ask(ADDRESS)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    classified = len(world.classified)
    text(world, "status")
    assert until(lambda: bodies(world)[-1].startswith("• Order Diet Coke: ")) and len(world.classified) == classified


def test_a_word_typed_at_a_hidden_prompt_is_the_answer_not_a_control_word(world):
    world.scripts["Log in"] = [ask(OTP)]
    text(world, "Log in")
    assert until(lambda: world.core.open is not None)
    text(world, "status")
    assert until(lambda: world.agents["Log in"].replies == [{"text": "status"}])
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_core.py -q -k "stop_cancels or stop_with_two or stop_all or pause_keeps or status_lists or hidden_prompt_is_the_answer"`
Expected: FAIL. "stop all", "pause", "resume" and "status" reach the router (`len(world.classified)` grows), and `world.ledger.paused()` stays False.

- [ ] **Step 3: Read control words in code**

In `assistant/core.py`:

(a) Add after `STOP_WORDS = {...}`:

```python
CONTROL = STOP_WORDS | {"stop all", "pause", "resume", "status"}  # read in code, never by a model
```

(b) In `receive`, replace

```python
            if secret and text.lower() in STOP_WORDS:
                return self.cancel(question.task)
```

with

```python
            word = " ".join(text.lower().split())
            if secret and word in STOP_WORDS:
                return self.cancel(question.task)
            if word in CONTROL and not event.get("media_id") and (not secret or word == "stop all"):
                return self.control(word, question, event)
```

(c) Add these methods to `Core`, after `which`:

```python
    def control(self, word, question, event):
        """A control word, read in code with no model: stop (the task asking, else the only one running, else which?),
        stop all (every task, and schedules, watches and suggestions pause), pause, resume, status."""
        active = [t for t in self.tasks.values() if t.status in ACTIVE]
        if word in STOP_WORDS:
            if question is not None:
                return self.cancel(question.task)
            if len(active) == 1:
                return self.cancel(active[0])
            if not active:
                return self.say("Nothing is running right now.")
            return self.which(event, word, active, "CANCEL", question)
        if word == "stop all":
            for task in active:
                self.cancel(task)
            self.keep(self.ledger.set_setting, "paused", "true")
            return self.say(("Stopping everything" if active else "Nothing was running") +
                            ". Schedules, watches and suggestions are paused; say “resume” to start them again.")
        if word in {"pause", "resume"}:
            self.keep(self.ledger.set_setting, "paused", "true" if word == "pause" else "false")
            return self.say("Paused schedules, watches and suggestions. Running tasks carry on." if word == "pause"
                            else "Resumed schedules, watches and suggestions.")
        lines = [f"• {t.title}: {t.view()['step']}" for t in active] or ["Nothing is running right now."]
        if self.paused():
            lines.append("Schedules, watches and suggestions are paused.")
        return self.say("\n".join(lines))

    def paused(self):
        """Whether schedules, watches and suggestions are paused. A flag the ledger can't read counts as paused."""
        value = self.keep(self.ledger.paused)
        return True if value is None else value
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_core.py -q`
Expected: all pass. The older tests that queue a CANCEL intent and type "stop" still pass: the word now cancels in code, and the queued intent is simply never read.

- [ ] **Step 5: Run the checks and commit**

```bash
uv run ruff check . && uv run pytest -q
git add assistant/core.py tests/test_core.py
git commit -m "feat: stop, stop all, pause, resume and status are read in code, never by a model

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Approvals come with buttons, and a typed reply approves only when it is clear

**Files:**
- Modify: `assistant/gate.py` (`YES` becomes `APPROVE`; new `APPROVE_OPTIONS`; `approved`)
- Modify: `assistant/core.py` (imports; constant `UNCLEAR_YES`; `Core.__init__`; `send`; `buttons`; `deliver_files`; `receive`; new `typed_yes`)
- Test: `tests/test_gate.py`, `tests/test_core.py`

**Interfaces:**
- Consumes: the approval message from `gate.approved`: `{"kind": "text", "text": …, "approval": True, "options": APPROVE_OPTIONS}`.
- Produces:
  - `gate.APPROVE`, a regex of explicit yes words; "ok", "okay" and "sure" are no longer in it.
  - `gate.APPROVE_OPTIONS = [{"id": "yes", "label": "Yes, place it"}, {"id": "no", "label": "No"}]`.
  - `core.last_sent`: the id of the question whose message went out last, else None.
  - `core.typed_yes(question, event)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gate.py`:

```python
# ---- Plan 8 Task 5: approvals come with buttons; "ok" never approves


def test_the_approval_asks_with_yes_and_no_buttons_and_a_tap_on_yes_approves(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke", "Total ₹79"])
    checks(monkeypatch, approval="no")  # a tap needs no model's reading
    w = Fake(page, replies=[{"option": "yes"}])
    with pytest.raises(StalePage, match="Approved"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert w.asked[0]["options"] == [{"id": "yes", "label": "Yes, place it"}, {"id": "no", "label": "No"}]
    assert "Tap Yes to go ahead" in w.asked[0]["text"] and w.state["approval"]["by"] == "user"


@pytest.mark.parametrize("reply", [{"option": "no"}, {"text": "ok"}, {"text": "sure"}, {"text": "okay"}])
def test_a_no_ok_or_sure_never_approves(monkeypatch, reply):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[reply])
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert w.state["approval"] is None
```

Append to `tests/test_core.py`:

```python
# ---- Plan 8 Task 5: what a typed reply to an approval may do

APPROVAL = {"kind": "text", "text": "Diet Coke 300 ml × 1\n• Total ₹79\n\nI'll press “Pay ₹79” on shop.test. Tap Yes "
            "to go ahead, or tell me what to change.", "approval": True,
            "options": [{"id": "yes", "label": "Yes, place it"}, {"id": "no", "label": "No"}]}


def test_an_approval_comes_with_yes_and_no_buttons(world):
    world.scripts["Order Diet Coke"] = [ask(APPROVAL)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world)) and buttons(world) == ["q1:yes", "q1:no"]
    tap(world, "q1:yes")
    assert until(lambda: world.agents["Order Diet Coke"].replies == [{"option": "yes"}])


def test_a_typed_yes_right_after_the_approval_is_passed_on(world):
    world.scripts["Order Diet Coke"] = [ask(APPROVAL)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    classified = len(world.classified)
    text(world, "Yes, place it")
    assert until(lambda: world.agents["Order Diet Coke"].replies == [{"text": "Yes, place it"}])
    assert len(world.classified) == classified  # read in code


@pytest.mark.parametrize("reply", ["ok", "Sure", "👍", "k"])
def test_ok_sure_or_an_emoji_never_approve(world, reply):
    world.scripts["Order Diet Coke"] = [ask(APPROVAL)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    text(world, reply)
    assert until(lambda: bodies(world)[-1].startswith("To approve, tap “Yes, place it”, or reply yes."))
    assert world.agents["Order Diet Coke"].replies == [] and world.core.open is not None


def test_a_typed_yes_after_something_else_was_sent_shows_the_approval_again(world):
    world.scripts["Order Diet Coke"] = [ask(APPROVAL)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    world.core.say("Your Zepto order is out for delivery.")  # something else came after the approval
    text(world, "yes")
    assert until(lambda: len(interactive(world)) == 2)  # the approval again, now last
    assert world.agents["Order Diet Coke"].replies == []
    text(world, "yes")
    assert until(lambda: world.agents["Order Diet Coke"].replies == [{"text": "yes"}])


CAB = {**APPROVAL, "text": "Uber Go · ₹412\n\nI'll press “Request Uber Go” on m.uber.com. Tap Yes to go ahead."}


def test_a_typed_yes_while_two_approvals_wait_approves_neither(world):
    world.scripts["Order Diet Coke"] = [ask(APPROVAL)]
    world.scripts["Book a cab"] = [ask(CAB)]
    world.hosts["Book a cab"] = "cabs.test"
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    text(world, "Book a cab")
    assert until(lambda: len(world.core.queue) == 2)
    text(world, "yes")
    assert until(lambda: bodies(world)[-2].startswith("To approve, tap “Yes, place it” on this question"))
    assert world.agents["Order Diet Coke"].replies == [] and world.agents["Book a cab"].replies == []


def test_a_change_typed_at_the_approval_goes_to_the_task(world):
    world.scripts["Order Diet Coke"] = [ask(APPROVAL)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    world.intents.append(("ANSWER", "t1", {"ANSWER": 1.0}))
    text(world, "no, make it two")
    assert until(lambda: world.agents["Order Diet Coke"].replies == [{"text": "no, make it two"}])
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_gate.py tests/test_core.py -q -k "buttons or no_ok_or_sure or typed_yes or emoji or change_typed"`
Expected: FAIL. The gate asks without `options`, "ok" approves, and typed replies reach the router.

- [ ] **Step 3: Buttons and explicit yes words in the gate**

In `assistant/gate.py`:

(a) Replace

```python
YES = re.compile(r"\b(yes|yeah|yep|yup|ok|okay|sure|go ahead|book it|confirm|do it|proceed|haan)\b", re.IGNORECASE)
```

with

```python
# Explicit yes words only: "ok", "okay" or "sure" never approve.
APPROVE = re.compile(r"\b(yes|yeah|yep|yup|go ahead|place it|book it|confirm|do it|proceed|haan)\b", re.IGNORECASE)
APPROVE_OPTIONS = [{"id": "yes", "label": "Yes, place it"}, {"id": "no", "label": "No"}]
```

(b) In `approved`, replace

```python
        f"\n\nI'll press “{label}” on {host}. Reply yes to go ahead, or tell me what to change."
```

with

```python
        f"\n\nI'll press “{label}” on {host}. Tap Yes to go ahead, or tell me what to change."
```

(c) In `approved`, replace

```python
    reply = w.ask({"kind": "text", "text": question, "approval": True})
    answer = str(reply.get("text", "")).strip()
    s["chat"] += [{"from": "agent", "text": question, "approval": True},  # marked: the final yes teaches nothing
                  {"from": "user", "text": f"“{answer}”", "approval": True}]
    if YES.search(answer) and not NOT_YES.search(answer) and jev.approval(question, answer) == "yes":
        bind(s, action, label, total, page, "user")
        raise StalePage("Approved. Reading the page again before acting.")
```

with

```python
    reply = w.ask({"kind": "text", "text": question, "approval": True, "options": APPROVE_OPTIONS})
    option = reply.get("option")
    answer = {"yes": "Yes, place it", "no": "No"}.get(option) or str(reply.get("text", "")).strip()
    s["chat"] += [{"from": "agent", "text": question, "approval": True},  # marked: the final yes teaches nothing
                  {"from": "user", "text": f"“{answer}”", "approval": True}]
    yes = option == "yes" or (option is None and APPROVE.search(answer) and not NOT_YES.search(answer)
                              and jev.approval(question, answer) == "yes")
    if yes:
        bind(s, action, label, total, page, "user")
        raise StalePage("Approved. Reading the page again before acting.")
```

- [ ] **Step 4: What a typed reply may approve, in Core**

In `assistant/core.py`:

(a) Change the gate import to:

```python
from .gate import APPROVE, NOT_YES, cap, rule_cap, same_cart
```

(b) Add after the `CONTROL = …` line:

```python
UNCLEAR_YES = re.compile(r"(?:ok(?:ay)?|k+|sure|fine|alright|cool|hm+|[\W_]+)", re.IGNORECASE)  # never approves
```

(c) In `Core.__init__`, add at the end:

```python
        self.last_sent = None  # the id of the question whose message went out last, else None
```

(d) In `send`, add as the last line of the method:

```python
        self.last_sent = question.id if question else None
```

(e) In `buttons`, add as the last line of the method:

```python
        self.last_sent = None
```

(f) In `deliver_files`, right after `self.channel.send(self.user, {"type": "document", "document": {"id": media_id, "filename": path.name}})`, add:

```python
                self.last_sent = None
```

(g) In `receive`, right after the control-word lines from Task 4, add:

```python
            approval = question is not None and question.message.get("approval") and not event.get("media_id")
            if approval and APPROVE.search(text) and not NOT_YES.search(text):
                return self.typed_yes(question, event)
            if approval and UNCLEAR_YES.fullmatch(text):
                return self.say("To approve, tap “Yes, place it”, or reply yes. To change something, tell me what.")
```

(h) Add this method after `answer`:

```python
    def typed_yes(self, question, event):
        """A typed yes to the final approval counts only when it is the one approval waiting and was the last thing
        sent (or the reply quotes it); otherwise the approval is shown again so a yes can follow it. The gate reads
        the yes again before anything is pressed."""
        quoted = bool(event.get("context")) and event["context"] in question.sent
        waiting = [q for q in self.queue if q is not question and q.message.get("approval")]
        if waiting or not (quoted or self.last_sent == question.id):
            self.say("To approve, tap “Yes, place it” on this question, or reply yes right after it.")
            self.send(question.message, question.task, question)  # shown again: now the last thing sent
            return None
        return self.answer(question, event)
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/test_gate.py tests/test_core.py tests/test_chat_e2e.py -q`
Expected: all pass. The end-to-end chat test's typed "yes" now counts in code, because the approval was the last thing sent.

- [ ] **Step 6: Run the checks and commit**

```bash
uv run ruff check . && uv run pytest -q
git add assistant/gate.py assistant/core.py tests/test_gate.py tests/test_core.py
git commit -m "feat: approvals come with Yes and No buttons; a typed yes counts only when it is clear, \"ok\" never does

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: A late yes is asked afresh, and runs nobody started never skip the question

**Files:**
- Modify: `assistant/core.py` (imports; constants; `Question.__init__`; `worker_for`; `ask`)
- Modify: `assistant/worker.py` (`Worker.__init__`)
- Modify: `assistant/gate.py` (imports; `approved`)
- Test: `tests/test_core.py`, `tests/test_gate.py`, `tests/test_worker.py`

**Interfaces:**
- Consumes: Task 1's `UNATTENDED`; Task 2's `task.source`; Task 5's approval message and `option` replies.
- Produces:
  - `Worker(..., source="chat")`, with `worker.state["source"]`.
  - `Core.ask` marks an approval reply that arrives more than `APPROVAL_FRESH = 3600` s after the question with `"late": True`.
  - An approval that a run with source in `UNATTENDED` asked is cancelled after `UNATTENDED_WAIT = 3600` s.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_core.py`:

```python
# ---- Plan 8 Task 6: a late yes, and approvals nobody is waiting on


def test_a_yes_more_than_an_hour_after_the_question_is_marked_late(world):
    world.scripts["Order Diet Coke"] = [ask(APPROVAL)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    world.wall.now += 3601
    tap(world, "q1:yes")
    assert until(lambda: world.agents["Order Diet Coke"].replies == [{"option": "yes", "late": True}])


def test_a_yes_within_the_hour_is_not_late(world):
    world.scripts["Order Diet Coke"] = [ask(APPROVAL)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    world.wall.now += 3599
    tap(world, "q1:yes")
    assert until(lambda: world.agents["Order Diet Coke"].replies == [{"option": "yes"}])


def test_an_unattended_approval_left_for_an_hour_stops_its_run(world):
    world.scripts["Pay my BESCOM bill"] = [ask(APPROVAL)]
    world.core.new_task("Pay my BESCOM bill", source="schedule")
    assert until(lambda: interactive(world))
    world.clock.now += 3600
    assert until(lambda: world.agents["Pay my BESCOM bill"].replies == [{"cancel": True}])
    assert "No answer, so I stopped Pay my BESCOM bill." in bodies(world)


def test_your_own_task_waits_past_the_hour(world):
    world.scripts["Order Diet Coke"] = [ask(APPROVAL)]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    world.clock.now += 3600
    time.sleep(0.05)
    assert world.agents["Order Diet Coke"].replies == [] and world.core.open is not None
```

Append to `tests/test_gate.py`:

```python
# ---- Plan 8 Task 6: a late yes; runs nobody started


def test_a_late_yes_reads_the_page_again_and_asks_afresh(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"option": "yes", "late": True}])
    with pytest.raises(StalePage, match="late yes"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert w.state["approval"] is None and w.said[-1].startswith("That yes came more than an hour after I asked")


def test_a_late_no_is_still_a_no(monkeypatch):
    page = fake_page()
    summary(monkeypatch, ["Diet Coke", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"option": "no", "late": True}])
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)


@pytest.mark.parametrize("source", ["schedule", "watch", "suggestion"])
def test_an_unattended_run_never_uses_a_standing_approval(monkeypatch, memory, source):
    w, page, _ = with_rule(monkeypatch, memory)
    w.state["source"] = source
    with pytest.raises(Retry, match="did not approve"):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert len(w.asked) == 1 and memory.live_rules()[0]["uses"] == 0


def test_an_unattended_run_never_uses_your_words_pre_approval(monkeypatch):
    monkeypatch.setenv("JEV_PREAUTH", "on")
    page = fake_page()
    summary(monkeypatch, ["Diet Coke", "Total ₹79"])
    checks(monkeypatch)
    w = Fake(page, replies=[{"text": "no"}], preauth=True, cap=100.0)
    w.state["source"] = "schedule"
    with pytest.raises(Retry):
        gate.approved(w, page, PAY, "Pay ₹79", None)
    assert len(w.asked) == 1
```

Append to `tests/test_worker.py`:

```python
# ---- Plan 8 Task 6: who started a run


def test_a_worker_knows_who_started_it(tmp_path):
    assert worker_with(tmp_path).state["source"] == "chat"
    assert worker_with(tmp_path, source="schedule").state["source"] == "schedule"
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_core.py tests/test_gate.py tests/test_worker.py -q -k "late or unattended or waits_past or knows_who"`
Expected: FAIL (no `late` mark; `Worker.__init__() got an unexpected keyword argument 'source'`; standing approvals still used).

- [ ] **Step 3: Core marks a late yes and stops an unattended wait**

In `assistant/core.py`:

(a) Change the ledger import to:

```python
from .ledger import UNATTENDED, NullLedger, new_id
```

(b) Add after `CANCEL_AFTER = 24 * 3600`:

```python
APPROVAL_FRESH = 3600  # a yes older than this is asked afresh: prices and stock move
UNATTENDED_WAIT = 3600  # how long an approval a schedule, watch, or suggestion asked waits; then its run stops
```

(c) In `Question.__init__`, add a third line:

```python
        self.asked_wall = None  # wall-clock time it was asked: an approval's yes may come too late
```

(d) In `worker_for`, add `source=task.source` to the `Worker(...)` call:

```python
        return Worker(url, task.goal, title=task.title, asker=asker, notify=notify, memory=self.memory,
                      habit=task.habit, sameness=task.sameness, skill=task.skill, preauth=task.preauth, cap=task.cap,
                      library=library, source=task.source)
```

(e) In `ask`, right after `question = Question(next(self.question_ids), task, message, self.clock())`, add:

```python
        question.asked_wall = self.wall()
```

Inside the `while not question.answered.wait(self.poll):` loop, add this block right after `waited = self.clock() - question.asked_at`:

```python
            if task.source in UNATTENDED and message.get("approval") and waited >= UNATTENDED_WAIT:
                with self.lock:
                    if question in self.queue:
                        self.queue.remove(question)
                    if self.open is question:
                        self.open = None
                        self.show_next()
                self.say(f"No answer, so I stopped {task.title}.", task)
                return {"cancel": True}
```

Replace the method's last three lines,

```python
        task.status = "running"
        self.update_typing()
        return question.reply
```

with

```python
        task.status = "running"
        self.update_typing()
        reply = question.reply
        if message.get("approval") and not reply.get("cancel") and self.wall() - question.asked_wall > APPROVAL_FRESH:
            reply = {**reply, "late": True}  # the gate reads the page again and asks afresh
        return reply
```

- [ ] **Step 4: The worker knows who started it**

In `assistant/worker.py` `Worker.__init__`, change the signature to

```python
    def __init__(self, url, goal, *, asker=None, notify=None, memory=None, habit=None, sameness=None, browser=None,
                 title=None, skill=None, preauth=False, cap=None, library=None, trace_dir=None, provide_file=None,
                 source="chat"):
```

and the `self.state.update(...)` line to

```python
        self.state.update(skill=skill, preauth=preauth, cap=cap, site=skills.bare(urlparse(url).netloc.lower()),
                          start=url, source=source)
```

- [ ] **Step 5: The gate asks afresh after a late yes and never skips the question for an unattended run**

In `assistant/gate.py`:

(a) Add to the imports, after `from .errors import Halt, Retry`:

```python
from .ledger import UNATTENDED
```

(b) In `approved`, replace

```python
    shadow = covered = False
    if matched and preauthorised(s, found, page):
```

with

```python
    shadow = covered = False
    unattended = s.get("source", "chat") in UNATTENDED  # a run nobody started just now: always a fresh yes
    if matched and not unattended and preauthorised(s, found, page):
```

and

```python
    elif matched and s["cap"] is None and (rule := standing(w, found, page)):  # the user's own cap always wins
```

with

```python
    elif matched and not unattended and s["cap"] is None and (rule := standing(w, found, page)):  # your cap wins
```

(c) In `approved`, replace

```python
    if yes:
        bind(s, action, label, total, page, "user")
        raise StalePage("Approved. Reading the page again before acting.")
```

with

```python
    if yes and reply.get("late"):
        s["approval"] = None
        w.say("That yes came more than an hour after I asked, so I'm checking the page again and will ask afresh.")
        raise StalePage("A late yes: reading the page again to ask afresh.")
    if yes:
        bind(s, action, label, total, page, "user")
        raise StalePage("Approved. Reading the page again before acting.")
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest tests/test_core.py tests/test_gate.py tests/test_worker.py -q`
Expected: all pass.

- [ ] **Step 7: Run the checks and commit**

```bash
uv run ruff check . && uv run pytest -q
git add assistant/core.py assistant/worker.py assistant/gate.py tests/test_core.py tests/test_gate.py tests/test_worker.py
git commit -m "feat: a yes more than an hour late is asked afresh; runs nobody started never use a standing approval and stop after an hour unanswered

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: A quiet task says what it is doing

**Files:**
- Modify: `assistant/core.py` (imports; constants; new `quiet_hours`; `Task.__init__`; `new_task`; `send`; `run`; new `progress_line`)
- Test: `tests/test_core.py` (`FakeAgent` gains `subgoal`)

**Interfaces:**
- Consumes: `Worker.subgoal()` (a dict with `"do"`, or None); Task 1's `note_progress`; Task 6's `UNATTENDED`; Task 2's `Core.wall`.
- Produces: `core.PROGRESS_AFTER = 180`, `core.MAX_PROGRESS = 10`, `core.IST`, `core.quiet_hours(now) -> bool`, `task.last_heard`, `task.progress`.

- [ ] **Step 1: Give FakeAgent a subgoal and write the failing tests**

In `tests/test_core.py`, add to the imports:

```python
from datetime import datetime
```

and add this method to `FakeAgent`, after `update`:

```python
    def subgoal(self):
        return self.state.get("subgoal")
```

Append:

```python
# ---- Plan 8 Task 7: progress lines


def busy(world, seconds, doing="find Diet Coke in the search results"):
    def step(agent):
        agent.state["subgoal"] = {"do": doing}
        world.wall.now += seconds
    return step


def progress_lines(world):
    return [b for b in bodies(world) if b.startswith("Still on")]


def test_a_quiet_task_says_what_it_is_doing_every_three_minutes(world):
    world.scripts["Order Diet Coke"] = [busy(world, 181), busy(world, 181), busy(world, 60)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "done")
    assert progress_lines(world) == ["Still on Order Diet Coke: find Diet Coke in the search results"] * 2
    assert world.ledger.task_row(world.core.tasks["t1"].ledger_id)["progress_lines"] == 2


def test_a_question_counts_as_hearing_from_the_task(world):
    def wait_then_ask(agent):
        world.wall.now += 200
        ask(ADDRESS)(agent)

    world.scripts["Order Diet Coke"] = [wait_then_ask]
    text(world, "Order Diet Coke")
    assert until(lambda: interactive(world))
    tap(world, "q1:o1")
    assert until(lambda: world.core.tasks["t1"].status == "done") and progress_lines(world) == []


def test_a_scheduled_run_stays_quiet_at_night_and_yours_does_not(world):
    world.wall.now = datetime(2026, 10, 10, 23, 30, tzinfo=conversation.IST).timestamp()
    world.scripts["Reorder atta"] = [busy(world, 181), busy(world, 1)]
    world.scripts["Order Diet Coke"] = [busy(world, 181), busy(world, 1)]
    world.hosts["Order Diet Coke"] = "other.test"
    world.core.new_task("Reorder atta", source="schedule")
    assert until(lambda: world.core.tasks["t1"].status == "done")
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t2"].status == "done")
    assert [line.split(":")[0] for line in progress_lines(world)] == ["Still on Order Diet Coke"]


def test_at_most_ten_progress_lines(world):
    world.scripts["Order Diet Coke"] = [busy(world, 181) for _ in range(12)]
    text(world, "Order Diet Coke")
    assert until(lambda: world.core.tasks["t1"].status == "done")
    assert len(progress_lines(world)) == 10
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_core.py -q -k "progress or hearing_from or quiet_at_night"`
Expected: FAIL. No progress line is sent, and `conversation.IST` doesn't exist.

- [ ] **Step 3: Send progress lines**

In `assistant/core.py`:

(a) Add to the imports:

```python
from datetime import datetime
from zoneinfo import ZoneInfo
```

(b) Add after `UNATTENDED_WAIT = 3600 …`:

```python
PROGRESS_AFTER = 180  # a task silent this long says what it is doing
MAX_PROGRESS = 10  # progress lines per task
IST = ZoneInfo("Asia/Kolkata")
QUIET = (22, 8)  # 22:00–08:00: a run nobody started just now stays quiet


def quiet_hours(now):
    hour = datetime.fromtimestamp(now, IST).hour
    return hour >= QUIET[0] or hour < QUIET[1]
```

(c) In `Task.__init__`, add a last line:

```python
        self.last_heard, self.progress = None, 0  # when the user last heard from it; progress lines it has sent
```

(d) In `new_task`, right after `task.script = script`, add:

```python
        task.last_heard = self.wall()
```

(e) In `send`, add as the first line of the method body:

```python
        if task is not None:
            task.last_heard = self.wall()
```

(f) In `run`, right after the `self.track(task)` that ends the loop body (from Task 2), add:

```python
                self.progress_line(task)
```

(g) Add this method after `track`:

```python
    def progress_line(self, task):
        """One line when a task has said nothing for PROGRESS_AFTER seconds: what it is doing, in the planner's own
        words. Never while its question waits, never more than MAX_PROGRESS, and never at night for a run nobody
        started just now."""
        # ponytail: checked after each tick, so a tick held in one long wait sends nothing until it returns
        now = self.wall()
        if task.progress >= MAX_PROGRESS or now - (task.last_heard or now) < PROGRESS_AFTER:
            return
        with self.lock:
            if any(q.task is task for q in self.queue):
                return
        if task.source in UNATTENDED and quiet_hours(now):
            return
        subgoal = task.worker.subgoal() if hasattr(task.worker, "subgoal") else None
        doing = (subgoal or {}).get("do")
        task.progress += 1
        self.say(f"Still on {task.title}: {doing}" if doing else f"Still on {task.title}.", task)
        self.keep(self.ledger.note_progress, task.ledger_id)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_core.py -q`
Expected: all pass.

- [ ] **Step 5: Run the checks and commit**

```bash
uv run ruff check . && uv run pytest -q
git add assistant/core.py tests/test_core.py
git commit -m "feat: a task quiet for three minutes says what it is doing, at most ten times, and runs nobody started stay quiet at night

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Never open a local or private address

**Files:**
- Create: `assistant/net.py`
- Modify: `assistant/worker.py` (import; `do`, the OPEN_URL branch)
- Modify: `assistant/core.py` (import; `prepare`)
- Modify: `tests/conftest.py` (no real DNS)
- Test: `tests/test_net.py` (new), `tests/test_worker.py`, `tests/test_core.py`

**Interfaces:**
- Produces: `net.resolve(host) -> list[str]`, `net.blocked_address(text) -> bool`, `net.private_url(url) -> str | None` (the reason, or None).

- [ ] **Step 1: No test resolves a name**

In `tests/conftest.py`, add after the `no_real_database` fixture:

```python
@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):
    """No test resolves a name: every name the address guard looks up resolves to a public address, unless a test
    says otherwise."""
    from assistant import net

    monkeypatch.setattr(net, "resolve", lambda host: ["93.184.216.34"])
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_net.py`:

```python
"""Where the agent must never go: this machine, the local network, or a cloud's metadata service."""

import pytest

from assistant import net


@pytest.mark.parametrize("url", ["http://127.0.0.1:9333/json", "http://localhost:8766/", "http://[::1]/",
                                 "http://192.168.1.1/admin", "http://10.0.0.5/", "http://172.16.3.4/",
                                 "http://169.254.169.254/latest/meta-data/", "http://100.64.0.1/",
                                 "http://0.0.0.0:8080/", "http://printer.local/", "http://router.lan/",
                                 "http://[::ffff:127.0.0.1]/"])
def test_a_local_or_private_address_is_refused(url):
    assert net.private_url(url)


def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch):
    monkeypatch.setattr(net, "resolve", lambda host: ["10.1.2.3"] if host == "intranet.example.com" else
                        ["93.184.216.34"])
    assert net.private_url("https://intranet.example.com/") == \
        "intranet.example.com points to a local or private address"
    assert net.private_url("https://www.swiggy.com/instamart") is None


def test_a_name_that_doesnt_resolve_is_left_to_the_browser(monkeypatch):
    monkeypatch.setattr(net, "resolve", lambda host: [])
    assert net.private_url("https://shop.test/") is None
```

Append to `tests/test_worker.py`:

```python
# ---- Plan 8 Task 8: never a private address


def test_the_planner_can_never_open_a_private_address(tmp_path, monkeypatch):
    w = worker_with(tmp_path)
    script_planner(monkeypatch, turn("OPEN_URL", text="http://192.168.1.1/admin"))
    jev_checks(monkeypatch)
    w.command("tick")
    assert w.browser.act.call_count == 0
    assert any("Never open http://192.168.1.1/admin" in line for line in w.state["feedback"])
```

Append to `tests/test_core.py`:

```python
# ---- Plan 8 Task 8: a start page on a private address


def test_a_start_page_on_a_private_address_is_never_opened(world):
    world.hosts["Open my router"] = "192.168.1.1"
    world.scripts["Open my router"] = [tell("opened")]
    text(world, "Open my router")
    assert until(lambda: any(b.startswith("I couldn't carry on with") and "192.168.1.1" in b for b in bodies(world)))
    assert "Open my router" not in world.agents
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/test_net.py tests/test_worker.py tests/test_core.py -q -k "private or resolve"`
Expected: collection error in `tests/conftest.py` and `tests/test_net.py`: `ImportError: cannot import name 'net' from 'assistant'`.

- [ ] **Step 4: Write the guard**

Create `assistant/net.py`:

```python
"""Whether an address the agent is asked to open is somewhere it must never go: this machine, the local network, or
a cloud's metadata service."""

import ipaddress
import socket
from urllib.parse import urlparse

LOCAL_NAMES = (".local", ".localhost", ".internal", ".home.arpa", ".lan")


def resolve(host):
    """The addresses a name resolves to; none when it doesn't resolve."""
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None)]
    except (OSError, UnicodeError):
        return []


def blocked_address(text):
    """Whether a literal address is anything but a public one (loopback, private, link-local, shared, reserved,
    multicast). A name is not an address: False."""
    try:
        address = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return not address.is_global or address.is_multicast


def private_url(url):
    """The reason an http(s) address must not be opened, or None: a literal address that isn't public, a local
    name, or a name that resolves to one. A name that doesn't resolve isn't blocked: the browser can't reach it
    either."""
    # ponytail: checked once when opening; the browser resolves the name again, so DNS rebinding isn't covered
    host = (urlparse(url).hostname or "").rstrip(".").lower()
    if not host:
        return "it has no host"
    if host == "localhost" or host.endswith(LOCAL_NAMES):
        return f"{host} is a local name"
    if blocked_address(host):
        return f"{host} is a local or private address"
    if any(blocked_address(address) for address in resolve(host)):
        return f"{host} points to a local or private address"
    return None
```

- [ ] **Step 5: Use it where a model can choose an address**

In `assistant/worker.py`:

(a) Change the package import to:

```python
from . import executor, gate, jev, learn, net, planner, replay, rules, skills, views
```

(b) In `do`, in the `if op == "OPEN_URL":` branch, right after

```python
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise Retry("OPEN_URL needs a full http(s) address in text.")
```

add

```python
            reason = net.private_url(url)
            if reason:
                raise Retry(f"Never open {url}: {reason}. Open the site's public address instead, or ASK the user.")
```

In `assistant/core.py`:

(a) Change the package import to:

```python
from . import jev, messages, net, planner, replay, rules, skills
```

(b) In `prepare`, add after its last line, `task.host = site(task.url)`:

```python
        reason = net.private_url(task.url)
        if reason:  # a start page a model chose: never this machine or the local network
            raise ValueError(f"I won't open {task.url}: {reason}.")
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `uv run pytest tests/test_net.py tests/test_worker.py tests/test_core.py -q`
Expected: all pass.

- [ ] **Step 7: Run the checks and commit**

```bash
uv run ruff check . && uv run pytest -q
git add assistant/net.py assistant/worker.py assistant/core.py tests/conftest.py tests/test_net.py tests/test_worker.py tests/test_core.py
git commit -m "feat: never open this machine, the local network or a metadata service, whether the planner or the start job chose it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Account-security changes only when the goal asks, and then with a yes

**Files:**
- Modify: `assistant/rules.py` (new `ACCOUNT_SECURITY`, `ACCOUNT_GOAL`, `account_security`, `account_goal`)
- Modify: `assistant/gate.py` (`commit_label`)
- Test: `tests/test_rules.py`, `tests/test_gate.py`

**Interfaces:**
- Consumes: `rules.control_words`; `gate.lookup(w, label, sure)`.
- Produces: `rules.account_security(operation, action, page, key=None, focused="") -> str | None` and `rules.account_goal(goal) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rules.py`:

```python
# ---- Plan 8 Task 9: account security


def click(label, shown=""):
    return {"label": label, "name": label, "shown": shown, "node": 1}


@pytest.mark.parametrize("label", ["Forgot password?", "Change password", "Reset your password",
                                   "Update mobile number", "Change email address", "Turn off 2FA",
                                   "Enable two-factor authentication", "Delete account", "Deactivate my profile"])
def test_controls_that_change_an_accounts_security_are_named(label):
    assert rules.account_security("CLICK", click(label), {"text": ""}) == label


@pytest.mark.parametrize("label", ["Password", "Sign in with password", "Login", "Continue", "Add to cart",
                                   "Mobile number", "Email", "New password"])
def test_ordinary_controls_are_not(label):
    assert rules.account_security("CLICK", click(label), {"text": ""}) is None


def test_a_dialog_is_never_read_as_one():
    assert rules.account_security("ACCEPT_DIALOG", {}, {"text": "Forgot password? Delete account"}) is None


@pytest.mark.parametrize("goal, asks", [("Change my Swiggy password", True), ("reset my Amazon password", True),
                                       ("Update my email on Flipkart", True), ("Delete my Zomato account", True),
                                       ("turn off 2FA on my Paytm", True), ("Order one Diet Coke", False),
                                       ("Sign in to Swiggy and reorder atta", False)])
def test_a_goal_asks_for_an_account_change_only_in_its_own_words(goal, asks):
    assert rules.account_goal(goal) is asks
```

Append to `tests/test_gate.py`:

```python
# ---- Plan 8 Task 9: account security only on the goal's word


def test_an_account_security_control_is_refused_when_the_goal_didnt_ask():
    page = fake_page()
    w = Fake(page)
    with pytest.raises(Retry, match="didn't ask for that"):
        gate.commit_label(w, "CLICK", control("Forgot password?"), page)


def test_an_account_security_control_the_goal_asked_for_still_needs_a_yes():
    page = fake_page()
    w = Fake(page)
    w.state["goal"] = "Reset my Swiggy password"
    assert gate.commit_label(w, "CLICK", control("Forgot password?"), page) == "Forgot password?"
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_rules.py tests/test_gate.py -q -k "account"`
Expected: FAIL, `AttributeError: module 'assistant.rules' has no attribute 'account_security'`.

- [ ] **Step 3: The rule**

In `assistant/rules.py`, add after `maybe_commit_label`:

```python
# Changes to how an account is secured or kept: its password, the phone or email it is tied to, its 2FA, or the
# account itself. Instinct reset passwords unasked; Jev presses none of these unless the goal asks, and then asks.
ACCOUNT_SECURITY = re.compile(
    r"\b(?:(?:change|reset|update|forgot(?:ten)?|recover)\s+(?:your\s+|my\s+)?password"
    r"|(?:change|update|edit|add|remove)\s+(?:your\s+|my\s+)?(?:phone|mobile)(?:\s+number)?"
    r"|(?:change|update|edit|add|remove)\s+(?:your\s+|my\s+)?e-?mail(?:\s+(?:address|id))?"
    r"|(?:turn\s+(?:on|off)|enable|disable|set\s+up|remove|change)\s+(?:two[- ]factor|2fa|two[- ]step|authenticator)"
    r"|(?:delete|close|deactivate)\s+(?:your\s+|my\s+)?(?:account|profile))\b", re.IGNORECASE)
ACCOUNT_GOAL = re.compile(  # a goal that asks for one of those, in the user's own words
    r"\b(?:change|reset|update|recover)\b.{0,40}\bpassword\b|\b(?:change|update)\b.{0,40}\b(?:phone|mobile|e-?mail)\b"
    r"|\b(?:delete|close|deactivate)\b.{0,40}\baccount\b|\b(?:2fa|two[- ]factor|two[- ]step)\b", re.IGNORECASE)


def account_security(operation, action, page, key=None, focused=""):
    """The words of a control that changes how the account is secured, or deletes it, else None. A dialog's text is
    the whole page's, so a dialog is never read as one (deleting through a dialog is an outright commit anyway)."""
    if operation == "ACCEPT_DIALOG":
        return None
    words = control_words(operation, action, page, key, focused)
    if words is None:
        return None
    label, shown = words
    return (label or shown) if ACCOUNT_SECURITY.search(f"{label} {shown}") else None


def account_goal(goal):
    """Whether the user's goal asks to change how an account is secured, or to delete it."""
    return bool(ACCOUNT_GOAL.search(goal or ""))
```

- [ ] **Step 4: The gate refuses it, or asks**

In `assistant/gate.py` `commit_label`, replace

```python
    page_key = operation == "PRESS_KEY" and action.get("node") is None
    focused = w.state["browser"].focused_label() if page_key else ""
    label = rules.commit_label(operation, action, page, key, focused)
```

with

```python
    page_key = operation == "PRESS_KEY" and action.get("node") is None
    focused = w.state["browser"].focused_label() if page_key else ""
    security = rules.account_security(operation, action, page, key, focused)
    if security:
        if not rules.account_goal(w.state["goal"]):
            raise Retry(f"“{security}” changes how the account is secured (password, phone, email, 2FA) or deletes "
                        "it. The user didn't ask for that, so never press it: go on another way, or ASK the user.")
        return lookup(w, security, sure=True)  # asked for: still the user's yes first
    label = rules.commit_label(operation, action, page, key, focused)
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/test_rules.py tests/test_gate.py -q`
Expected: all pass.

- [ ] **Step 6: Run the checks and commit**

```bash
uv run ruff check . && uv run pytest -q
git add assistant/rules.py assistant/gate.py tests/test_rules.py tests/test_gate.py
git commit -m "feat: a password, phone, email, 2FA or account-deletion control is pressed only when the goal asks, and then only after a yes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Files go to the user only from the downloads folder

**Files:**
- Modify: `assistant/core.py` (import; `Core.__init__`; new `sendable`; `deliver_files`)
- Test: `tests/test_core.py` (the `world` fixture passes `files_root`)

**Interfaces:**
- Produces: `Core(..., files_root=None)`, where None means `HOME / "downloads"` (`HOME` from `assistant/memory.py`), and `core.sendable(path) -> bool`.

- [ ] **Step 1: Point the test world at its own folder and write the failing test**

In the `world` fixture of `tests/test_core.py`, change the ending of the `conversation.Core(` call from Task 2 to:

```python
        memory_job=lambda message, known: w.known.append(known) or w.memory_outs.pop(0), ledger=w.ledger,
        wall=w.wall, files_root=tmp_path)
```

Append:

```python
# ---- Plan 8 Task 10: files only from the downloads folder


def test_a_file_outside_the_downloads_folder_is_never_sent(world, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "id_rsa"
    elsewhere.write_bytes(b"secret")
    world.scripts["Get my invoice"] = [lambda agent: agent.state["files"].append(str(elsewhere))]
    text(world, "Get my invoice")
    assert until(lambda: "I didn't send id_rsa: I only send files from my downloads folder." in bodies(world))
    assert not [m for m in sent(world) if m["type"] == "document"]
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run pytest tests/test_core.py -q -k "downloads_folder"`
Expected: FAIL, `TypeError: Core.__init__() got an unexpected keyword argument 'files_root'`.

- [ ] **Step 3: The guard**

In `assistant/core.py`:

(a) Change the memory import to:

```python
from .memory import HOME, LABELS, RULE_DAYS, UNSTORABLE, Memory, StoreError, Unstorable, rule_line, rupees
```

(b) Extend the `Core.__init__` signature's last line:

```python
                 clock=time.monotonic, poll=1.0, ledger=None, wall=time.time, files_root=None):
```

and add at the end of `__init__`:

```python
        self.files_root = Path(files_root) if files_root is not None else HOME / "downloads"  # browser saves here
```

(c) Add this method before `deliver_files`:

```python
    def sendable(self, path):
        """Whether a file may go to the user: only from the agent's own downloads folder, symlinks resolved."""
        try:
            return path.resolve().is_relative_to(self.files_root.resolve())
        except OSError:
            return False
```

(d) Replace `deliver_files` with:

```python
    def deliver_files(self, task):
        files = task.worker.state["files"]
        for name in files[task.sent_files:]:
            path = Path(name)
            if not self.sendable(path):
                self.say(f"I didn't send {path.name}: I only send files from my downloads folder.", task)
            elif path.is_file() and path.stat().st_size <= MAX_DOWNLOAD:
                media_id = self.channel.register(path)
                self.channel.send(self.user, {"type": "document", "document": {"id": media_id, "filename": path.name}})
                self.last_sent = None
            else:
                self.say(f"{path.name} is too large to send here; it's saved at {path}.", task)
        task.sent_files = len(files)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_core.py -q`
Expected: all pass. The older invoice tests keep their files in `tmp_path`, which is now the world's downloads folder.

- [ ] **Step 5: Run the checks and commit**

```bash
uv run ruff check . && uv run pytest -q
git add assistant/core.py tests/test_core.py
git commit -m "feat: files reach the user only from the agent's downloads folder

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Docs, the spec's rulings, and the full checks

**Files:**
- Modify: `README.md` (Chat section), `AGENTS.md`, `docs/known-issues.md`
- Modify: `docs/superpowers/specs/2026-10-05-phase-c-always-on-design.md` (§8, §11, §12, §15)

- [ ] **Step 1: README**

In `README.md`, replace

```markdown
- **Files** the agent saves arrive in chat as documents.
```

with

```markdown
- **Files** the agent saves arrive in chat as documents, and only files from its downloads folder (`~/.jev/downloads`).
- **Approvals** come with *Yes, place it* and *No*. A typed reply approves only when it is an explicit yes ("yes",
  "go ahead", "confirm"), the approval is the only one waiting, and it was the last thing sent; otherwise the approval
  is shown again. "ok", "sure" or an emoji never approve. A yes that comes more than an hour after the question makes
  the agent read the page again and ask afresh.
- **Control words** are read in code, never by a model: `stop` (the task asking, or the only one running), `stop all`
  (every task, and schedules pause), `pause`, `resume`, and `status`.
- **Progress:** a task that has said nothing for three minutes sends "Still on …: what it is doing", at most ten times.
- **After a restart** the agent lists each task the stop cut short ("I restarted while doing …") with *Retry* and
  *Leave it*, and names any payment or order that may already have gone through. Nothing is retried by itself.
- **Never** opened: this computer, the local network, or a cloud metadata address. **Never** pressed without your
  instruction in the goal and then your yes: password, phone, email, 2FA, or account-deletion controls.
```

- [ ] **Step 2: AGENTS.md**

In `AGENTS.md`, add a bullet right after the three-line bullet that begins ``- Memory lives in Postgres (`memory.py`).``
and ends ``(`privacy.storable`).``:

```markdown
- The task ledger (`ledger.py`) keeps a row per task, an audit row per commit, and settings, in memory's Postgres
  schema. Goals, titles, steps and labels go in through `Memory.hide`; the audit holds metadata only, never typed
  values. An unknown setting is refused, and a pause flag code can't read means paused.
```

- [ ] **Step 3: Known issues**

In `docs/known-issues.md`, add at the end:

```markdown

## Plan 8 — Phase C step 1: ledger and guards

| ID | Issue | Where | Status |
|---|---|---|---|
| P8-01 | Progress lines are checked after each tick, so a tick held in one long wait (a commit the site is still processing) sends none until it returns. | `core.py` `progress_line` | open |
| P8-02 | The private-address check runs once, when a page is opened; the browser resolves the name again, so a DNS-rebinding site isn't caught. Links clicked on a page aren't checked. | `net.py`, `worker.py` `do`, `core.py` `prepare` | open |
| P8-03 | `pause` and `resume` set the flag that schedules, watches and suggestions will read (C4, C5). In C1 nothing reads it yet. | `core.py` `control` | open (until C4) |
| P8-04 | After a restart the old task's browser tab stays open; Retry starts a new task from its goal rather than resuming. | `core.py` `recover` | open (by design: spec §3.5) |
```

- [ ] **Step 4: The spec's rulings**

In `docs/superpowers/specs/2026-10-05-phase-c-always-on-design.md`:

(a) §8: replace

```markdown
The pause flag lives in `settings` and survives restarts. The worker already checks for cancellation before each step;
the gate also checks it immediately before pressing a commit.
```

with

```markdown
The pause flag lives in `settings` and survives restarts; schedules, watches and suggestions read it before starting a
run. Cancellation (from `stop` and `stop all`) is checked before every step and immediately before pressing a commit.
```

(b) §11, the `audit` row: replace

```markdown
outcome (`pressed`, `confirmed`, `mismatch`, `failed`, `unknown`)
```

with

```markdown
outcome (`pressed`, `confirmed`, `mismatch`, `step`, `failed`, `unknown`; `step` when the site showed it was only a step, `failed` also when the browser refused a page that had changed)
```

(c) §12: replace rule 6,

```markdown
6. `stop all` and the pause flag are checked before every step and immediately before every commit.
```

with

```markdown
6. Cancellation from `stop` and `stop all` is checked before every step and immediately before every commit. The pause
   flag stops schedules, watches and suggestions from starting new runs.
```

and rule 12,

```markdown
12. Jev sends files only from its own media folder.
```

with

```markdown
12. Jev sends files only from its own downloads folder, where the browser saves them.
```

(d) §15, the C1 row: replace `ledger.py` (tasks, outbox, inbox, audit, settings) with `ledger.py` (tasks, audit, settings) in that row, and in the C2 row replace `inbox statuses and \`uncertain\`` with `` `inbox` and `outbox` in `ledger.py`, inbox statuses and `uncertain` ``.

- [ ] **Step 5: The full checks**

Run each, and expect each to pass:

```bash
uv run ruff check .
uv run pytest -q
uv run pytest -m browser -q
node --check assistant/snapshot.js && node --check assistant/static/app.js
```

- [ ] **Step 6: Commit**

```bash
git add README.md AGENTS.md docs/known-issues.md docs/superpowers/specs/2026-10-05-phase-c-always-on-design.md
git commit -m "docs: the task ledger, approval rules, control words, progress lines and guards; the spec's C1 rulings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The bench round (live, run by the controller with the user)

Spec §15: after each build step, a bench round must pass at least 6 of 22 known tasks with 0 unsafe. The
reliability work picked from its traces goes in before C2 starts. This task spends model money and uses the agent
Chrome, so the controller runs it, never a subagent.

- [ ] **Step 1: Ask the user to go ahead**

Tell the user: the round takes about 40–60 minutes and $1–2; nothing is bought (every final approval is answered no);
keep http://127.0.0.1:8766/ open in case a task asks for a code; don't use the agent Chrome meanwhile. Wait for their
yes.

- [ ] **Step 2: Run the same 22 tasks as round 5**

```bash
uv run jev --bench --repeat 1 --unseen 0 --only zepto,instamart,blinkit,swiggy-food,zomato,amazon,flipkart,uber,ola,rapido,bookmyshow,flight,hotel,electricity,invoice,cancel-prime,form,web-form,compare,myntra,restaurant,atta
```

When it prints `Bench report: …`, stop the server (Ctrl+C).

- [ ] **Step 3: Check the bar**

```bash
python3 -c "import json,glob,os; d=sorted(glob.glob(os.path.expanduser('~/.jev/bench/2026*')))[-1]; s=json.load(open(d+'/summary.json')); print(d, s['known']['passed'], '/', s['known']['runs'], 'unsafe', s.get('unsafe'))"
```

Expected: at least 6 passed and `unsafe` 0 (or an empty list). If the bar isn't met, triage the traces in
`~/.jev/traces/` and fix the causes before C2.

- [ ] **Step 4: Record it**

Add a row under `## Live runs` in `docs/known-issues.md` with the bench stamp, passed/runs, unsafe, seconds per step
and dollars per task (from `summary.json`), then:

```bash
git add docs/known-issues.md
git commit -m "docs: bench round after Phase C step 1

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
