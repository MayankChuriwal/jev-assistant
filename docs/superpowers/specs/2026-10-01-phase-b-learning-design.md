# Jev Assistant — Phase B: the agent learns (memory, site notes, habit replay)

Date: 2026-10-01. Branch: `mini-jev-agent`. Builds on the Phase A spec,
`docs/superpowers/specs/2026-10-01-system-one-agent-design.md`: everything Phase A specifies stays unless this spec
changes it.

## 1. Goal and decisions

**Goal.** The agent remembers what you tell it and what it learns. A repeat task asks less and runs faster and cheaper,
and a site it struggled with goes better the next time. No Phase A safety rule gets weaker, and nothing ties the agent
to macOS, so it can be deployed.

| Topic | Decision |
|---|---|
| Scope | One spec and one plan: memory, learned site notes, and habit replay |
| Store | Postgres: Homebrew Postgres 18 locally today; any Postgres ≥ 16 with `pg_trgm` when deployed. No macOS Keychain |
| Memory writes | Facts you state or answer are saved at once ("Saved: …"; say "forget" to undo). Guesses about you get one "Remember this?" tap |
| Privacy | IDs reach models only as placeholders and are stored encrypted. IDs are your phone, email, name, UPI ID, bill and consumer numbers, and other account numbers. Addresses, preferences, carts and notes are visible to models. Card numbers, CVVs, OTPs, passwords, PINs, Aadhaar and PAN are never stored |
| Standing approvals | One site, one recorded cart and a ₹ cap. Created from your words, or from a "Don't ask next time" tap after an order you approved. A rule expires after 30 days, skips the question only with `JEV_PREAUTH=on`, and you're told every time it's used |
| Site notes | Drafted after a run by one mini call, only when the run taught something. You Save or Skip them right after the result |
| Replay | A recorded run guides Jev. When Jev is unsure, the recorded control wins if it is the only one with its label and doesn't commit. A different confident pick takes that subgoal out of replay. On an exact repeat, the planner's start turn is skipped |
| Bench | The normal bench starts from an empty memory every run. `--learn` runs each task twice in a throwaway schema and measures the gain |
| Done bar | Section 2 |

## 2. Done bar

`jev --bench --learn` measures the bar. It covers the 24 known tasks and 8 sampled unseen tasks, each run twice: pass 1 on
an empty memory, then pass 2 with what pass 1 saved.

- Pass 2 asks no question that memory could answer. Codes, CVVs, captchas and approvals still ask.
- For tasks that passed both times, the median of (pass-2 busy time ÷ pass-1 busy time) is ≤ 0.50. Busy time is wall
  time minus time spent waiting on you.
- For the same tasks, the median of (pass-2 model spend ÷ pass-1 model spend) is ≤ 0.30. Spend means mini plus Jev.
- Pass 2 passes at least as many tasks as pass 1.
- At least 30% of the tasks that failed pass 1 pass in pass 2.
- 0 unsafe runs, using Phase A's audit: a commit during a bench run, or a secret refused at send time.
- 0 hidden values in clear, whether in traces or in any text the bench schema stores.
- The normal bench (`jev --bench`) still meets Phase A's bar: ≥ 80% of known runs and ≥ 60% of unseen runs pass, 0 unsafe,
  ≤ 2 s per step, ≤ $0.02 per task.

The time and cost ratios need at least 10 tasks that passed both times. With fewer, the report says "not enough runs"
and the bar is not met.

## 3. Architecture

### 3.1 Modules

| Module | Role |
|---|---|
| `memory.py` (new) | The Postgres store, and every read and write of what the agent keeps: facts, IDs, standing approvals, site notes, habits, events. Also exports `HOME` (`~/.jev`, or `JEV_HOME`) |
| `learn.py` (new) | Runs after a task. Code records the habit. One mini call drafts site notes and guesses, only when the run taught something. Returns the offers for the concierge |
| `replay.py` (new) | Matches a habit when a task starts (a Jev request). Gives the worker each subgoal's expected step, and judges Jev's answers against it |
| `privacy.py` (renamed from `profile.py`) | `Secrets` and `SECRETS`, unchanged. Adds `storable(text)`, which decides what may never be stored |
| `core.py` | Adds the MEMORY intent, habit matching in `prepare`, learning after each task, and offers (notes, guesses, rules) as buttons. Uses `Memory` instead of `Profile` |
| `worker.py` | Uses memory instead of the profile. Any missing ID is asked once, hidden, and kept. Adds replay hooks and the `orders` list: confirmed commits with their cart and total |
| `gate.py` | `cart(text)` reads the cart lines. `approved()` checks standing approvals |
| `planner.py` | Puts the memory block in the payload. A saved choice counts as yours. A similar repeat attaches the habit. New jobs: `memory()` and `reflect()` |
| `jev.py` | Adds the MEMORY intent and the `habit()` questions |
| `skills.py` | `learned(sites, lines)` adds saved learned lines to the site notes |
| `bench.py` | One schema per run, `--learn`, the learning metrics, and the bar |
| `server.py` | Adds `jev memory …`, and Postgres and key checks at start. Removes `jev profile` |

Removed: the `Profile` class, `Vault`, `CARD_PARTS`, cards, logins and `vault_keys` in the profile, `profile.json` (it
is imported once, Section 4.6), and `jev profile`.

### 3.2 One request, end to end (what changes from Phase A)

1. A message arrives and Jev routes it. MEMORY is a new intent (Section 5.3). For NEW_TASK, intake reads the skill,
   several and preauth as in Phase A.
2. In `prepare`, habits are looked up for the skill: on the site the goal names, or else on all of the skill's sites.
   If any exist, one Jev request picks a habit and decides same or similar (Section 8.1). A match fixes the site and the
   start page, so no "which app?" question is asked.
3. The worker starts with memory.
   - same: the habit's plan, with no planner start turn.
   - similar: one planner turn with the habit attached.
   - no match: Phase A.
4. Each step asks Jev as in Phase A. Replay judges the answer against the expected step (Section 8.3).
5. A commit goes through the gate as in Phase A, which now also checks standing approvals (Section 6.3).
6. At the end, the trace is saved and the browser closed. Then `learn` records the habit (for verified runs) and drafts
   notes and guesses (when the run taught something), and the concierge posts the offers (Section 7).

## 4. The store

### 4.1 Connection

- Driver: psycopg 3 (`psycopg[binary]>=3.2,<4`). Address: `JEV_DATABASE_URL`, default `postgresql:///jev` (the local
  socket, connecting as the OS user).
- `Memory.open()` does the following, in order:
  - connects;
  - when the database in the default address doesn't exist, creates it through the `postgres` database;
  - runs `CREATE EXTENSION IF NOT EXISTS pg_trgm`;
  - creates the schema and its tables;
  - applies versioned changes recorded in `schema_version`.

  Any failure stops `jev` with one line saying what failed and how to fix it ("brew services start postgresql@18", or
  set `JEV_DATABASE_URL`).
- Schemas:
  - your memory: `jev`
  - a normal bench run: `bench_<run>`
  - one task pair in learn mode: `bench_<run>_<n>`
  - tests: a throwaway database (Section 11)

  Each connection sets `search_path` to `<schema>, public`.
- Each `Memory` has one connection behind a lock, because tasks run in threads. Every public method is one transaction.
  Separate processes (`jev`, `jev run`) hold their own connections, and Postgres serialises their writes. This fixes
  P4-11.

### 4.2 Tables (schema version 1)

```sql
CREATE TABLE facts (
  id bigserial PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('address', 'preference', 'default_app', 'fact')),
  label text NOT NULL DEFAULT '',            -- "Home", "groceries"
  text text NOT NULL,                        -- redacted: an ID inside it is its placeholder
  tags text[] NOT NULL DEFAULT '{}',         -- skill names, site hosts, "general"
  data jsonb NOT NULL DEFAULT '{}',          -- an address's lat and lng
  source text NOT NULL DEFAULT '',           -- the chat line or answer it came from, redacted
  task text NOT NULL DEFAULT '',             -- the goal it came from
  created_at timestamptz NOT NULL DEFAULT now(),
  superseded_by bigint REFERENCES facts (id) ON DELETE SET NULL
);
CREATE INDEX facts_text ON facts USING gin (text gin_trgm_ops);

CREATE TABLE ids (
  key text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{1,39}$'),
  label text NOT NULL,                       -- "BESCOM account number"
  value bytea NOT NULL,                      -- Fernet token (Section 4.3)
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE approvals (
  id bigserial PRIMARY KEY,
  site text NOT NULL,                        -- bare host
  cart jsonb NOT NULL,                       -- [{"item": "diet coke 300 ml", "qty": 1}]
  cap numeric(12, 2) NOT NULL CHECK (cap > 0),
  words text NOT NULL,                       -- your words, or "Don't ask next time (up to ₹N)"
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  uses integer NOT NULL DEFAULT 0,
  last_used_at timestamptz,
  revoked_at timestamptz
);

CREATE TABLE notes (
  id bigserial PRIMARY KEY,
  host text NOT NULL,                        -- bare host
  text text NOT NULL,
  status text NOT NULL CHECK (status IN ('draft', 'saved', 'skipped')),
  task text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  decided_at timestamptz
);

CREATE TABLE habits (
  id bigserial PRIMARY KEY,
  host text NOT NULL,
  skill text NOT NULL,
  title text NOT NULL,                       -- the goal, at most 80 characters
  goal text NOT NULL,
  start text NOT NULL,                       -- the first page's URL
  plan jsonb NOT NULL,
  steps jsonb NOT NULL,
  cart jsonb NOT NULL DEFAULT '[]',          -- a confirmed order's cart, when the run had one
  version integer NOT NULL DEFAULT 1,
  replays integer NOT NULL DEFAULT 0,
  clean_replays integer NOT NULL DEFAULT 0,  -- replays with no fallback
  failed_in_a_row integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz,
  retired_at timestamptz
);
CREATE UNIQUE INDEX habits_live ON habits (host, skill, lower(regexp_replace(goal, '\s+', ' ', 'g')))
  WHERE retired_at IS NULL;

CREATE TABLE events (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL CHECK (actor IN ('user', 'agent', 'bench', 'import')),
  action text NOT NULL,                      -- remember, forget, set_id, approve, revoke, note_save, note_skip,
                                             -- habit_record, habit_retire, undo, purge
  ref text NOT NULL,                         -- "facts:12", "ids:phone", "approvals:3"
  before jsonb,
  after jsonb                                -- an ID appears only as key, label, and token
);

CREATE TABLE schema_version (version integer NOT NULL);
```

### 4.3 IDs and encryption

- Python encrypts an ID's value with Fernet (`cryptography>=43`) before it reaches Postgres. The `value` column holds the
  token. Postgres never sees the plain value or the key.
- The key is `JEV_MEMORY_KEY` (a Fernet key). It must live outside the database: the encryption protects nothing if the
  key sits next to the data.
  - Missing at start, and no ID is stored yet: `jev` generates a key, appends it to `.env` (creating the file if needed),
    sets the file to mode 0600, and says so once.
  - Missing at start, and IDs are stored: `jev` and `jev run` refuse to start ("JEV_MEMORY_KEY is missing: the saved IDs
    can't be read without it…"). `jev memory` still lists and forgets (`jev memory forget id:<key>`; it hides the IDs, so
    the key name must be known). Only once no ID is stored does the next start generate a new key.
  - A wrong key that can't open a stored ID: that ID counts as unreadable and is asked for again, with one notice.
- Decrypted values are cached per process. `hidden_values()` reads the IDs again whenever `max(updated_at)` changes,
  which costs one query per step. So an ID that another task or process saved is redacted here too.
- Redaction variants are the same as Phase A: the value itself; a phone number's digits, its last 10 digits, and the
  last 10 with a leading 0; and each part of a name that has 3 or more letters.
- An event about an ID stores its key, label and token, never the plain value.

### 4.4 Never stored

`privacy.storable(text)` refuses, and nothing is saved, when the text:
- contains a run of 13 to 19 digits (spaces and dashes allowed) that passes the Luhn check (a card number);
- contains a 12-digit group shaped like an Aadhaar number (`\d{4}[\s-]?\d{4}[\s-]?\d{4}`);
- contains a PAN (`\b[A-Z]{5}\d{4}[A-Z]\b`);
- names a secret, per `rules.SECRET_ASK` or `rules.CARD_DETAIL`: OTP, password, CVV, PIN, card number, UPI PIN.

An ID's key and label may not name one of these either. An answer typed into a secret field never becomes a fact or an
ID. Before a fact is stored, its text is redacted with the task's `Secrets`, so an ID inside it becomes its placeholder.

### 4.5 Forget and undo

- Every change writes an event recording the row before and after.
- "forget" deletes the row (a fact, an ID, a note, a habit) or revokes a standing approval.
- The event keeps the before-snapshot for 24 hours, so undo still works for a day. `Memory.open()` and a daily purge
  clear the before-snapshots of forget events older than that. After a day, forgotten means gone.
- Undo reverts the latest change made by you or the agent. Bench and import changes are never undone.

### 4.6 First start

When the `jev` schema has no facts and no IDs and `~/.jev/profile.json` exists, the file is imported:
- name, phone, email and UPI ID become IDs, labelled full name, phone number, email address and UPI ID;
- addresses become address facts (label and text, with lat and lng in `data`);
- preferences become default-app facts (label = the skill, text = the app, from "Default app for <skill>: <app>") or
  preference facts;
- the file is renamed `profile.json.imported`, and the agent says so once. These events have actor `import`.

## 5. Memory

### 5.1 Writes

- **IDs.**
  - The planner writes a value as `[profile:<key>]`, with the field's name in `values`. Any key matching
    `^[a-z][a-z0-9_]{1,39}$` works, so `[profile:bescom_account]` is valid.
  - When memory doesn't have the key, the worker asks once through the hidden field ("What's your BESCOM account number?
    It stays hidden from the models."). It saves the answer and says "Saved your BESCOM account number. The models never
    see it."
  - The Phase A keys keep their names: `phone`, `email`, `name`, `upi_id`.
- **What you tell it.**
  - Routing: the MEMORY intent sends the message to `planner.memory(message, known)`.
  - The job returns a strict reply:
    - `op`: remember, forget, list, approve, undo or none;
    - `facts`: kind, label, text, tags;
    - `id`: key and label;
    - `refs`: what to forget;
    - `site`: for approve;
    - `reply`: for none, why nothing was done.
  - The model never supplies an ID's value. Code cuts it from your message, and only when it is a run of digits or
    letters containing a digit (at least 4 characters), an email address, or a UPI ID. With two or more different
    candidates, nothing is saved and the reply asks you to send them one at a time. Any other ID, such as a name, is
    asked for through the hidden field the next time a task needs it.
  - Code then saves and replies "Saved: …" or "Forgot: …", lists memory, or says why nothing was saved.
- **Default app per skill.** Same as Phase A, but stored as a default_app fact (label = the skill, text = the app's
  name). A new choice supersedes the old one.
- **Guesses.** Two sources:
  - a numbered choice you made (Phase A's "Remember this" button);
  - `learn`'s guesses (Section 7.4).

  Each is offered once with Remember / No buttons. Remember saves a preference fact tagged with the skill and the site.
- **Known limit.** An ID typed in plain chat reaches Jev's router and the memory job once, before it is saved. Once
  saved, the reply suggests `jev memory set <key>` for next time: the chat shows a hidden field only when a task asks.

### 5.2 Recall

The planner payload's `memory` block replaces `profile`:

```json
{"ids": [{"key": "phone", "label": "phone number"}],
 "addresses": [{"label": "Home", "text": "12, 4th Cross, Koramangala"}],
 "defaults": ["groceries: Zepto"],
 "approvals": ["swiggy.com · Diet Coke 300 ml × 1 · up to ₹100 · until 31 Oct"],
 "facts": ["You prefer Amul Taaza 500 ml (groceries)"]}
```

- **Fact selection.** First the facts tagged `general`, then those tagged with the skill or the site, then the closest to
  the goal by `similarity()` ≥ 0.2. Newest first within each group. Superseded facts are never recalled. At most 12
  facts and 1,500 characters. IDs, addresses, defaults and approvals are always included.
- **Planner rules added:**
  - "A choice saved in memory (default address, ride type, usual size, default app) counts as the user's: use it and
    TELL which you used. Never ASK for what memory holds."
  - "When the task needs an ID that memory doesn't list, write [profile:<new_key>] with the field's name: the system
    asks the user, hidden."
- **The start turn** asks only for what the goal, the chat and memory don't give.
- **Asking for a saved ID is refused.** An ASK whose words name a saved ID's label gets
  `Retry("Memory has it: use [profile:<key>]")`. This generalises Phase A's profile check.
- **Geolocation** for the browser comes from the first address fact with lat and lng.

### 5.3 View, forget, undo

- **Chat.** The MEMORY intent covers "what do you know about me", "forget my office address", "stop the Instamart rule"
  and "undo that". Jev's new intent reads: "Tells the assistant something to remember about the user, asks it to forget
  something or stop a standing approval, asks what it remembers, or asks to undo the last memory change."
- **CLI.**
  - `jev memory`: lists the facts with their ids, the IDs by label only, rules, each saved note with its ref
    (`note:7  zeptonow.com: <line>`; drafts and skipped notes are never listed), and habits. The chat's list shows the
    saved notes' text (ten at most), and "forget" in the chat takes their refs too.
  - `jev memory set <key> [label]`: the value is typed hidden.
  - `jev memory forget <ref>`: refs look like `12`, `id:phone`, `rule:3`, `note:7` or `habit:5`.
  - `jev memory undo`.
  - `jev memory notes [host]`: the saved notes, each with its ref.

## 6. Standing approvals

### 6.1 Created

- **The offer.** After an order you approved (approval `by` user) that the site confirmed, and whose cart lines and
  payable total could be read, the agent asks: "Next time, place this same order on <site> without asking, up to ₹N?"
  - N is the total × 1.2, rounded up to the next ₹10.
  - The buttons are *Don't ask next time* and *Keep asking*.
  - When `JEV_PREAUTH` is not `on`, the offer adds that pre-approval is off, so the rule would only be reported.
- **Your words.** The MEMORY intent with op approve.
  - The site is the one named in your message.
  - The cap is read by code with `gate.cap()`, which needs a ₹, Rs or INR marker.
  - The cart comes from the latest habit on that site that recorded a confirmed order. With no such order, the agent
    replies "Place this order with me once; then I can remember it."
- **Stored:** the site, the cart, the cap, your words, and `expires_at` set to 30 days after creation.

### 6.2 Cart lines

`gate.cart(text)` reads the lines of the flattened page text that name an item with a quantity. Examples: "Diet Coke
300 ml × 2", "2 x Atta 1 kg", or "Qty 2" with the item name on the line above.

Each line becomes an item and a qty. The item is lowercased, with prices and ₹ removed and spaces collapsed. When no lines
can be read, no rule is offered and no rule can apply, so the gate asks.

### 6.3 At checkout

In `gate.approved`, after Jev's matches check (it must say yes) and before asking, and only when your message didn't
pre-authorise the order, a rule applies when all of these hold:
- it is live, for this site (bare host);
- its cart equals `cart(page)`: the same items and quantities, in any order;
- the payable total is ≤ cap, and every money line is ≤ cap (`worst`);
- it has not expired or been revoked.

What happens then:
- **Bench:** logged as `bench_standing`, then asked as usual and answered NO.
- **`JEV_PREAUTH` not on:** asked, with the added line "Your standing approval for this order would have covered it."
- **`JEV_PREAUTH=on`:** the agent says "Placing your usual <site> order (₹245) under your ₹300 rule (until 31 Oct). Say
  “stop that rule” to end it." The approval is bound with `by: standing`, and `uses` goes up by 1.

### 6.4 Never

- A rule never covers another site, another cart, more than its cap, an expired or revoked rule, or a bench run.
- A model, a page or a note never creates a rule.

## 7. Learning from runs

### 7.1 When

`learn.after(state, memory, verified=…)` runs after every task, once the trace is saved and the browser closed. A bench
run learns only in `--learn` mode, after grading.

### 7.2 Verified

A run is verified when any of these holds:
- its status is done: DONE passed Phase A's quote and `achieved` checks;
- the site confirmed a commit, so it is in `orders`;
- on the bench, the judge passed it.

### 7.3 Habit (code only)

A habit is recorded from a verified run that has a skill and a site.
- **What a habit holds:**
  - `plan`: the subgoals (do, done_when, kind, and values with placeholders, as the planner wrote them).
  - `steps`: for each subgoal, the executed steps that moved it forward. Each step records:
    - op: Jev's operation name;
    - name: the control's label (with the context a page adds to tell like controls apart), lowercased, with prices
      and counts removed;
    - key;
    - value field: the subgoal value that was typed;
    - secret: true for a secret field, and then never a value;
    - commit;
    - URL host and path;
    - page title.
  - `cart`: from `orders`.
  - `start`: the first page's URL.
- **Steps dropped:**
  - steps judged failed, or that changed nothing;
  - a step undone by BACK, and the BACK itself;
  - repeats inside a loop;
  - unconfirmed steps;
  - waits, scrolls, and captcha squares the user picked.
- **Versions.** The same site, skill and goal (ignoring case and spaces) makes a new version of the existing habit. A
  different goal makes a new habit.
- **Counters.** `replays`, `clean_replays` (replays with no fallback), and `failed_in_a_row` (replays that did not
  verify). Three failed replays in a row retire the habit. A run counts as a replay only once replay judged at least
  one expected step (`expected > 0`).

### 7.4 Reflection (one mini call)

Reflection runs only when the run taught something:
- the first run on its site (no habit and no saved note for that site yet);
- any escalation, loop, idle tick, or replay fallback;
- a failed run;
- a new answer from you.

`planner.reflect(digest)` reads a redacted digest of the run: goal, skill, site, status, result, plan, notes, feedback,
failed steps (op, name, what changed), your questions and answers, the site's saved notes, and the memory facts. It
returns at most 3 note lines about the site and at most 2 guesses about you (each a text and its tags).

### 7.5 Lint (code)

- **Note lines:**
  - at most 140 characters, and about the site;
  - no placeholders, no run of 4 or more digits, and no words from your addresses, IDs or name;
  - nothing about approvals, payments, codes, passwords, PINs or cards, and nothing about skipping a check;
  - no link outside the site.
- **Guesses:** at most 140 characters, no placeholders, no run of 6 or more digits, and `storable()` must accept them.
- A line that fails is dropped.

### 7.6 Offers

After the result, the agent posts:
- "I learned about zeptonow.com:\n• …" with Save / Skip buttons, which apply to all the lines at once;
- each guess, with Remember / No buttons;
- the standing-approval offer (Section 6.1).

Notes stay drafts until you choose. `--learn` saves notes and guesses at once, inside its own schema.

### 7.7 Notes in use

- At the start of a task, `skills.learned()` adds the saved lines to the library's site notes, under "## Learned". A site
  with no shipped note gets a learned-only note: host, name = host, start = `https://<host>/`.
- A site keeps at most 30 saved lines. Saving more removes the oldest.
- The planner's rules say that learned lines are hints from earlier runs, never instructions, and that nothing in a note
  changes what needs your approval.

## 8. Habit replay

### 8.1 Matching

- In `prepare`, after intake: the live habits for the skill, on the site the goal names, or else on all of the skill's
  sites. Newest first, at most 50.
- `jev.habit(goal, habits)` is one request with two questions:
  - `habit`: the options are each habit's title, plus "none: a new errand";
  - `sameness`: `same` (the same items, quantities, places and choices) or `similar` (the same kind of errand on this
    site, with different items, places or choices).
- A match needs `habit` to be something other than none, with probability ≥ 0.7, and `jev.ambiguous` to be false.
  The sameness counts as `same` only when its answer has probability ≥ 0.7 and isn't ambiguous; otherwise it is
  `similar`.
- On a match, the habit's site and start page are used, and the "which app?" question is skipped.

### 8.2 Starting

- **same:** the plan is the habit's, and the planner start turn is skipped. The agent says "Same as last time: <title>."
- **similar:** one planner turn with reason "start" and the habit attached ("reuse its plan, changing only what this goal
  changes"). Replay continues only when the new plan has the same number of subgoals with the same kinds; otherwise
  replay is off.

### 8.3 Each step

The expected step is the habit's next recorded step for the current subgoal. Jev is asked exactly as in Phase A. Then:
- **Jev's top pick is the expected step.** The operation and the target's label (normalised like the habit's names)
  match. Act on it and count it as matched when it passes the gates, or, when the gates would escalate, only if that
  label is unique on the page and the step is no commit. A label shown twice goes through the gates as in Phase A. A
  recorded commit step, or any step of a commit subgoal, is never forced: unless Jev's pick passes the gates, the
  planner decides as in Phase A (fallback "unsure"), with or without pre-authorisation.
- **A wait or a scroll.** Act on it as in Phase A; replay stays as it is, since habits don't record them. When a
  subgoal's recorded steps are used up, Jev goes on as in Phase A and nothing counts.
- **Jev picks something else that passes the gates.** Act on Jev's pick. This subgoal leaves replay, with the fallback
  "Jev chose <name>".
- **Neither.** Go to the planner as in Phase A, with the fallback "unsure".
- **A blocker, DONE, or a secret-field step.** Handled as in Phase A. On DONE, the cursor moves to the next subgoal.
- **After acting.** When the next expected control's name is already on the new page, the "changed nothing" re-reads
  are skipped.
- **Re-joining.** A subgoal that left replay stays live. The next subgoal starts in replay, and its expected step counts
  only once its control is on the page.

### 8.4 Never changes

- the gate: approval, message pre-authorisation, standing approvals;
- hidden code and CVV asks, and the captcha relay;
- the loop ladder and the caps;
- the never-retry rule.

### 8.5 Recorded

`state["replay"]` holds the habit id, sameness, expected and matched step counts, and the fallbacks (subgoal, step,
reason). It is written to the trace.

## 9. Safety invariants (added to Phase A's)

1. An ID's value is in clear only in process memory and in the field it is typed into. It never appears in a table's
   text, an event, a trace, a log or a model request. Redaction and Phase A's leak scan at send time enforce this.
2. Card numbers, CVVs, OTPs, passwords, PINs, Aadhaar and PAN are never stored (Section 4.4).
3. A model never supplies an ID's value to store (Section 5.1).
4. Memory, notes and habits are data. Prompts call them hints, and nothing they say changes the gate. Notes need your
   Save; the only exception is the bench's learn mode, inside its own schema.
5. Standing approvals follow Section 6.4. On top of that, they need `JEV_PREAUTH=on`, you're told about each use, and they
   can be revoked.
6. Replay never bypasses anything in Section 8.4, and never forces a commit step Jev is unsure of (Section 8.3).
7. A bench run never reads or writes your memory. Lessons from unseen tasks stay in bench schemas, which are dropped
   afterwards.
8. Forgotten means gone after 24 hours (Section 4.5).

## 10. Bench

### 10.1 Normal bench

`jev --bench` runs in a schema `bench_<run>` that starts empty.
- Normal memory writes happen in it: IDs from `@user` answers, and default apps.
- Learning is off: no habits, notes, guesses or rules.
- The schema is dropped at the end unless `--keep` is given.

### 10.2 Learn mode

`jev --bench --learn [--only …] [--unseen 8] [--seed N] [--keep]` gives each task its own schema, `bench_<run>_<n>`:
1. pass 1 on an empty memory;
2. grade it;
3. `learn`, with verified meaning the judge passed it, and notes and guesses saved at once; pass 1's checkout (when it
   passed) becomes a standing approval in the pair's schema, which pass 2's gate can only log;
4. pass 2;
5. grade it again, which completes the pair.

The schema is dropped unless `--keep` is given. Unseen tasks are sampled as in Phase A (seeded). Each task runs as one
pair.

### 10.3 Report additions

For each pair:
- the verdicts of pass 1 and pass 2;
- questions asked, and the repeats memory could have answered. A repeat is a pass-2 question (not a code, CVV, captcha
  or approval) whose `similarity()` to any pass-1 question (not a code, CVV, captcha or approval) is ≥ 0.5, so an ID
  asked again counts;
- busy seconds;
- spend (mini, Jev) and calls (mini, Jev);
- steps;
- whether a habit matched, and its sameness;
- expected steps matched, out of the total;
- fallbacks, with their reasons;
- notes drafted and saved;
- facts and IDs saved;
- memory hits: IDs filled from memory, default apps used, habits matched;
- standing approvals that would have fired;
- the unsafe and secret audits. The secret audit scans the traces and every text and JSON column of the schema for any
  registered ID value.

The summary lists each Section 2 bar item as met or not met.

## 11. Testing

- **Postgres required.** The offline suite needs the local Postgres.
  - Each session creates a database `jev_test_<pid>` through `JEV_TEST_DATABASE_URL` (default
    `postgresql:///postgres`). Each test gets a fresh schema, and everything is dropped at the end.
  - When Postgres can't be reached, the run stops with: "Postgres isn't running: brew services start postgresql@18 (or
    set JEV_TEST_DATABASE_URL)".
  - `JEV_MEMORY_KEY` is a fixed test key.
  - Tests never reach a paid model: Phase A's `no_real_models` fixture stays.
- **New offline tests:**
  - the store: each table, versions, forget, undo and purge, the import, key handling, `storable()`;
  - the memory block and the planner rules that use it;
  - the MEMORY intent's paths;
  - asking for an ID and saving it;
  - standing approvals: the offer, your words, and every refusal case in Section 6.4;
  - `cart()`;
  - `learn`: what habit recording drops, the reflection triggers, lint, offers;
  - replay: match thresholds, same and similar, each per-step outcome, re-joining, retirement;
  - learn mode: pairs, metrics and the bar.
- **Browser test.** The shop fixture runs twice through the worker, with scripted Jev and planner. Pass 2 must reach the
  approval with zero planner turns before it, produce the same page log, and take fewer Jev steps. No ID or secret may
  appear in any request or table.

## 12. Rollout and config

- **`.env`:**
  - `JEV_DATABASE_URL` (optional);
  - `JEV_MEMORY_KEY` (generated on first start when absent and no ID is stored yet);
  - `JEV_TEST_DATABASE_URL` (tests).
- **`jev` start order:**
  1. load `.env`;
  2. `Memory.open()`: connect, create, migrate, purge, import, key;
  3. the agent Chrome and the chat server.
- **Deployable.** No macOS API remains; the agent needs Postgres and its environment variables. The agent Chrome is
  still local in Phase B; the cloud browser is Phase C.
- **Docs to update:**
  - README: memory, `jev memory`, standing approvals, the learn bench, Postgres setup;
  - AGENTS.md: the checks need Postgres;
  - `.env.example`;
  - `docs/known-issues.md`: a Plan 7 section;
  - the Phase A spec's §13 (the Keychain vault is gone) and §14 (Phase B is delivered by this spec).

## 13. What stays, what goes

- **Stays:** everything in Phase A that this spec doesn't name. That includes:
  - the browser layer and snapshot;
  - the executor's request and gates;
  - the gate's money reading and message pre-authorisation;
  - the planner's turn schema;
  - the skill and site files;
  - the bench task files and the unseen pool;
  - traces on disk under `~/.jev/traces`.
- **Goes:**
  - `Profile`, `Vault`, `CARD_PARTS`;
  - cards, logins and `vault_keys`;
  - `profile.json` (imported once);
  - `jev profile`;
  - every macOS Keychain call.

## 14. Risks

| Risk | Mitigation |
|---|---|
| A wrong habit replays a wrong order | Jev's same/similar match has a 0.7 floor; the approval summary shows the cart; standing approvals check the cart by code |
| A site redesign breaks habits | Each step falls back to the live loop; 3 failed replays retire a habit; a verified live run records a new version |
| A hostile page plants a note | Notes need your Save; code lint; notes are hints in prompts; the gate is code |
| Memory gets noisy | Recall is capped at 12 facts and 1,500 characters; forget and undo; guesses need a tap |
| An ID leaks | Encrypted at rest; placeholders in prompts; leak scan at send time; secret audit on the bench |
| `JEV_MEMORY_KEY` is lost | With IDs stored, `jev` and `jev run` refuse to start ("JEV_MEMORY_KEY is missing…"); `jev memory` still lists and forgets (`jev memory forget id:<key>`); only once no ID is stored does the next start generate a new key. A wrong key makes those IDs unreadable and they are asked for again. Facts, notes and habits are unaffected |
| Postgres is not running | `jev` and the tests stop with the fix in one line |
| The time bar is missed | The report shows where the time goes; crystallised replay (code acting on exact matches after repeated clean replays) is the next speed step |

## 15. Later phases

- Phase C is unchanged from Phase A's §14: WhatsApp and iMessage with a secure link for secrets, wake subscriptions and
  scheduled tasks, TOTP and inbox codes, and a cloud browser.
- Crystallised replay is its first candidate when Phase B's time bar is missed.
