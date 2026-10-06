# Jev Assistant — Phase C: the agent reaches out (WhatsApp, background runs, schedules, watches)

Date: 2026-10-05. Branch: `mini-jev-agent`. Builds on the Phase A spec
(`docs/superpowers/specs/2026-10-01-system-one-agent-design.md`) and the Phase B spec
(`docs/superpowers/specs/2026-10-01-phase-b-learning-design.md`): everything they specify stays unless this spec changes
it. Phase A §14 listed Phase C as "reaches out"; this spec is its first milestone.

Research basis: a code-level teardown of CopilotKit/OpenDots, Anil-matcha/open-dots and mariagorskikh/open-instinct,
with CopilotKit/OpenBot and OpenMuse as further references (published privately at
https://claude.ai/artifact/Ct2MnfYsdjUGdVvt1eaPFE). We borrow patterns, not code (Section 17).

## 1. Goal and decisions

**Goal.** Jev becomes an always-on personal assistant for its owner. You reach it on WhatsApp. A task keeps running
after you look away and asks you there for a yes, a code or help. Schedules and watches start tasks on their own, and Jev
offers tasks you usually do at the time you usually do them. No Phase A or B safety rule gets weaker, and nothing ties the
code to macOS.

| # | Topic | Decision |
|---|---|---|
| 1 | Audience | The owner only. One browser with your logins, one channel, no sign-up. Tables keep room for more users later |
| 2 | v1 behaviours | WhatsApp reach, background tasks, schedules and watches, proactive suggestions. Voice notes in are transcribed (decision 13); no voice calls or spoken replies |
| 3 | Channel | WhatsApp Cloud API, dogfooded on Meta's test number. Approved templates for messages outside the 24-hour window |
| 4 | Hosting | Everything on your Mac. A tunnel with a stable HTTPS hostname reaches one tunnel-facing port. Schedules and watches fire only while the Mac is awake. The code stays portable |
| 5 | Payment | Jev presses the final control after your yes, bound to that exact cart (Phase A's gate). UPI still needs your PIN in your UPI app; cash on delivery moves no money; card details never |
| 6 | Codes and IDs | Every code and ID goes through a one-time secure link with one hidden field. Nothing secret is ever typed into WhatsApp |
| 7 | Take the wheel | In v1: a one-time link shows Jev's live tab on your phone; your taps and typing go straight to the browser |
| 8 | Unattended orders | A run started by a schedule, a watch or a suggestion always asks for a fresh yes. Standing approvals cover only tasks you start yourself |
| 9 | Suggestions | In the moment, for habits seen at least 3 times; at most 2 a day; none between 22:00 and 08:00; Yes / Not now / Stop suggesting this |
| 10 | Missed runs | A schedule or watch that fell due while the Mac slept runs once on wake if it is less than 12 hours late; older ones are skipped and listed in one note. Missed suggestions are dropped |
| 11 | Sequencing | Interleaved with task-reliability work: a bench round after each build step (Section 15). The bench never falls below 6/22 with 0 unsafe |
| 12 | Done | Scripted offline scenarios plus a two-week dogfood with fixed thresholds (Section 2) |
| 13 | Voice notes | WhatsApp voice notes are transcribed and handled as text. The transcript is kept, the audio is not. A voice note never approves a commit |
| 14 | Progress | A task that has said nothing for 3 minutes sends one line on what it is doing, at most every 3 minutes |
| 15 | Safety additions | From the Open Instinct review: Section 12, rules 9–15 |
| — | Approach | Grow today's server: one `jev` process, no new frameworks. The scheduler and the channel reach the task runner only through Core and Postgres, so splitting them out later is a deployment change |

## 2. Done bar

**Offline.** Every scenario in Section 14 passes under `uv run pytest` and `uv run pytest -m browser`, with a fake Graph
API, signed fake webhooks and an injected clock. No test calls a paid API or opens a database named `jev`.

**Dogfood.** Two weeks of your real tasks through WhatsApp. `jev report --days 14` reads the Postgres tables and prints
each line below with pass or fail:

- 0 unsafe actions: no commit without an explicit yes bound to its cart; no standing approval used by a run that a
  schedule, watch or suggestion started; no secret refused at send time.
- Every commit has an audit row naming the WhatsApp yes that allowed it.
- No hidden value in clear in the outbox, the inbox, traces, the audit table or any model payload. `jev report` scans them
  with the same `Secrets` matcher the model calls use.
- At least 70% of started tasks finish. A task finishes when its final approval is answered (yes and confirmed, or no) or,
  for a lookup or download, when its DONE is accepted. One hand-off is allowed: one take-the-wheel session or one answered
  "I'm stuck" question. Ordinary questions (which address, how to pay) don't count as hand-offs.
- While the Mac is awake, a schedule starts within 5 minutes of its due time.
- After every build step, `jev --bench --repeat 1 --unseen 0` passes at least 6 of 22 known tasks with 0 unsafe.

Phase A's and Phase B's bars are not part of this bar and are not changed by it.

## 3. Architecture

### 3.1 Process and ports

One `jev` process on the Mac, as today, with Chrome running separately (`jev chrome`). Two listeners:

| Listener | Binds | Routes |
|---|---|---|
| Chat (today) | 127.0.0.1:8766 | The chat page, `/api/*`, `/media/*`, the web channel's `/webhook`. Never reachable through the tunnel |
| Public (new) | 127.0.0.1:`JEV_PUBLIC_PORT` (default 8767), reached only through the tunnel | `GET /webhook` (Meta's verify check), `POST /webhook` (signed), `GET/POST /s/<token>` (code or ID), `/wheel/<token>` and its `frame`, `input` and `done` sub-routes. Every other path is 404 |

When WhatsApp is configured (Section 3.4), the conversation goes to WhatsApp and the web chat is idle. The web channel stays
for development and the bench. Showing the conversation in both places is out of scope (Section 16).

### 3.2 Modules

| Module | Role |
|---|---|
| `channel_whatsapp.py` (new) | `WhatsAppChannel`, with `WebChannel`'s interface: `send`, `events`, `store`, `media`, `set_typing`. Sends through the outbox; uploads and downloads media through the Graph API; switches to a template outside the 24-hour window; strips the web channel's `x_*` keys; `hidden_input = False` |
| `public.py` (new) | The tunnel-facing HTTP server of Section 3.1, on the standard library like `server.py`. Request bodies of `/s/` and `/wheel/` are never logged |
| `ledger.py` (new) | The always-on runtime's Postgres tables (Section 11): tasks, inbox, outbox, links, schedules, habit runs, suggestions, audit, settings. Uses the connection and schema `Memory` opens |
| `scheduler.py` (new) | One thread. Starts due schedules and watches, applies the missed-run rule, compares watch results, offers suggestions |
| `wheel.py` (new) | Take the wheel: a token-guarded session that streams a task's tab and passes your taps, keys and text to it |
| `core.py` | Control words; the SCHEDULE intent; secret questions become links when the channel has no hidden input; wheel offers; task `source`; ledger writes; the restart notice; the one-hour approval refresh; the one-hour limit on unattended approvals |
| `worker.py` | Pauses while you drive; writes "commit in flight" to the ledger; a watch task's typed result |
| `gate.py` | Runs started by a schedule, watch or suggestion never use standing approvals; an audit row for every commit |
| `planner.py` | New job `schedule()`: one sentence to a rule. DONE gains `result` for watch tasks |
| `jev.py` | New intent SCHEDULE |
| `browser.py` | Screencast start and stop, and input dispatch, for the wheel |
| `messages.py` | Template payloads and the link-button (`cta_url`) payload |
| `server.py` | Starts the public listener, the scheduler, the outbox sender and the inbox reader when WhatsApp is configured; checks the setup at start; adds `jev schedules` and `jev report` |
| `bench.py` | Unchanged. Bench runs use the web channel and never create schedules or touch WhatsApp |

### 3.3 One message, end to end

1. Meta POSTs a webhook to `JEV_PUBLIC_URL/webhook`. `public.py` checks `X-Hub-Signature-256` (HMAC-SHA256 of the raw body
   with `WHATSAPP_APP_SECRET`); a bad or missing signature is 403.
2. Each message in the body is inserted into `inbox` by its WhatsApp message id, with status `queued`; a repeat id is
   ignored. The handler answers 200 before any model runs. Status updates (sent, delivered, read) are dropped.
3. The inbox reader thread takes rows in order, marks each `running`, and calls `Core.receive_webhook`. Only
   `JEV_OWNER_WA` is heard (Phase A already ignores other senders), and a message that carries group context is dropped.
   The row then becomes `done` and its body is wiped; the id stays 7 days for de-duplication. A voice note is
   transcribed first (Section 4.1).
4. Control words are handled in code (Section 8). Everything else goes to Jev's router as today, with SCHEDULE added.
5. Agent messages leave through `Core.send` → `messages.render` → `WhatsAppChannel.send`, which writes the payload to
   `outbox`. The sender thread posts it to the Graph API and records the WhatsApp id.

### 3.4 Configuration

| Variable | Meaning |
|---|---|
| `WHATSAPP_PHONE_NUMBER_ID` | The sending number's id (Meta's test number in v1) |
| `WHATSAPP_TOKEN` | A permanent system-user token |
| `WHATSAPP_APP_SECRET` | Signs webhook bodies |
| `WHATSAPP_VERIFY_TOKEN` | Meta's subscription check |
| `JEV_OWNER_WA` | Your number as WhatsApp writes it in `from` (country code, digits only) |
| `JEV_PUBLIC_URL` | The tunnel's stable HTTPS origin; links are built from it |
| `JEV_PUBLIC_PORT` | The public listener's port, default 8767 |
| `JEV_STT_MODEL` | The speech-to-text model for voice notes |
| `WHATSAPP_GRAPH_VERSION` | The pinned Graph API version: the one Meta's dashboard shows when the app is created. Upgrading it is a deliberate change |

WhatsApp is configured when the first five are set. A partial set stops `jev` at start with a message naming what is
missing. At start, `jev` also checks the token (one Graph call) and that the three templates of Section 4.3 exist and are
approved; either failure stops it with the fix.

### 3.5 Restarts

Task threads and their open questions stay in memory, as in Phase A, so a restart ends them. Chrome keeps its tabs. On
start, every `tasks` row still active becomes `interrupted` and you get one message per task:

- "I restarted while doing {title}. Retry?" with **Retry** and **Leave it**. Retry starts the same goal as a new task.
- When the row says a commit was in flight (pressed, result not yet read): "{label} may have gone through; check it
  before retrying." The task is never retried automatically.
- An `inbox` row still `running` becomes `uncertain`, and you get one line: "I may have missed your message from {time}.
  Please send it again if it still matters." It is never re-run.

Automatic resume is out of scope (Section 16).

## 4. WhatsApp channel

### 4.1 Inbound

- Message types read: text, button and list replies, images and documents (downloaded through the Graph media URL into
  the channel's private media folder, as the web channel stores uploads).
- Voice notes (`audio`) are downloaded, transcribed by `JEV_STT_MODEL` (an OpenAI transcription model; English, Hindi
  or a mix), and handled as text. The audio is deleted once transcribed, and the transcript goes through the `Secrets`
  matcher before any model sees it. A note longer than 5 minutes, or an empty or failed transcription, gets "I couldn't
  make that out; could you type it?" The speech model only turns your voice into text: it never chooses a step or writes
  a reply.
- Any other type (sticker, reaction, location, contact) gets one reply: "I can read text, voice notes, buttons, images
  and documents for now." It is never an error, because a rejected webhook makes Meta resend it.
- `GET /webhook` answers Meta's `hub.challenge` only when `hub.verify_token` matches.

### 4.2 Outbound

- `messages.render` stays the single renderer: text split at 4,096 characters, up to 3 reply buttons, lists of up to
  10 rows, images and documents by media id. The channel removes `x_question`, `x_secret` and `x_expects` before
  sending.
- Images (screenshots, captcha pictures) and files (invoices) are uploaded to the Graph API first and sent by id.
- The link button uses WhatsApp's `cta_url` interactive message: a body, a display text and an HTTPS URL on
  `JEV_PUBLIC_URL`.
- Every payload passes the Phase A secret scan before it is queued, skipping only the `to` field. A payload holding a
  hidden value is refused and recorded, as `post_json` does for model calls.
- The sender retries a failed post 3 times with backoff (2, 8, 30 s), then marks the row failed and writes one line to
  the local log. Provider error text never goes into a WhatsApp message.

### 4.3 The 24-hour window

`settings.wa_last_inbound_at` holds when you last messaged. If a message is due and that is more than 24 hours ago, the
channel sends a template instead and keeps the real message until you reply. Your reply opens the window and the kept
messages go out in order.

| Template | Category | Body |
|---|---|---|
| `jev_needs_you` | Utility | "Jev needs you for: {{1}}. Reply here to continue." |
| `jev_update` | Utility | "{{1}}: {{2}}. Reply here for details." |
| `jev_suggestion` | Utility (Meta may class it as marketing) | "Want me to {{1}}? Reply here to answer." |

Variables carry only task titles and one-word states ("done", "stopped", "needs you"), never amounts, addresses or IDs.

### 4.4 Progress

A task that has sent you nothing for 3 minutes sends one line, "Still on {title}: {its current subgoal}", and then at
most one line every 3 minutes, up to 10 per task. The subgoal text is the planner's own words. No progress line is sent
while the task's question is open, or between 22:00 and 08:00 for a run that a schedule, watch or suggestion started.
The web channel shows the same lines.

## 5. Approvals over WhatsApp

- The gate's approval question is unchanged in content: shop, items, total, address and payment method, from
  `gate.summary`. It arrives with **Yes, place it** and **No**. Each button's id carries its question id
  (`q{id}:{option}`, as today), so a tap cannot answer another task.
- **One-hour refresh.** A yes that arrives more than 60 minutes after its approval question was asked is not used. Jev
  reloads the page and asks again with a fresh summary. The gate's existing check at the commit (same control, same
  total, same money lines) still runs on every yes.
- **Unattended runs.** For a task whose `source` is `schedule`, `watch` or `suggestion`, standing approvals are skipped
  (decision 8) and an unanswered approval cancels the task after 60 minutes: "No answer, so I stopped {title}." The next
  run of that schedule happens as normal.
- **What counts as a yes.** A tap on **Yes, place it**. A typed reply counts only when exactly one approval is open,
  that approval was the last message Jev sent, and the text is an explicit yes ("yes", "yes, place it", "go ahead",
  "confirm"). "ok", "sure", an emoji or a voice note never approve; Jev asks you to tap the button.
- **The confirmed total.** When the order or payment confirmation loads, code compares its total with the approved total.
  A difference marks the audit row `mismatch` and tells you at once.
- Every commit writes an `audit` row (Section 11) before the control is pressed and updates it when the result is read.

## 6. Codes and IDs by link

- When a question's kind is `secret` and the channel has no hidden input, Core sends a link message instead of the
  question: "{question text} Enter it here: {link} (expires in 10 minutes)". IDs (phone, consumer and account numbers)
  get 30 minutes.
- The token is 32 random bytes, URL-safe. `links` stores only its SHA-256, its kind, task, question, expiry and use
  time. A token works once.
- `GET /s/<token>` serves one small page with no external resources: the question text, one `type=password` field
  (`autocomplete=one-time-code` and `inputmode=numeric` for codes) and a Send button. Headers: `Cache-Control: no-store`,
  `Referrer-Policy: no-referrer`, a strict CSP, `X-Robots-Tag: noindex`.
- `POST /s/<token>` checks the token (unused, unexpired), marks it used, and hands the value to `Core.answer` through the
  same path the web chat's hidden field uses: never logged, never stored in clear, never shown to a model. IDs are then
  saved encrypted as Phase B saves them. The page then says "Got it. You can close this page."
- A code or a known ID typed into WhatsApp anyway is caught in code before any model sees it: a reply of 4–8 digits
  while a code link is open, or any text the `Secrets` matcher recognises. Jev wipes it from the inbox, doesn't use it,
  and answers "Please use the link; I haven't used what you typed." with the link again.
- Typing `stop` in WhatsApp still cancels the task while its link is open. An expired link gets a new one only if you ask
  ("send the link again").

## 7. Take the wheel

- **When Jev offers it.** Alongside an "I'm stuck" question (`worker.STUCK`), a captcha relay, or a sign-in the agent
  can't do itself (QR, passkey, a login wall); and whenever you send "show me" while a task runs. The offer is a `cta_url`
  button **Take the wheel**, plus **Stop task**.
- **Session.** A `links` row of kind `wheel`, one per task at a time. The link lasts 15 minutes; each input extends it by
  15 minutes, up to 60 in all.
- **Page.** `GET /wheel/<token>` serves the live tab. `frame` returns the latest JPEG from CDP `Page.startScreencast`
  (about 3 frames a second, quality 60, at most 1280 px wide). `input` takes one of: a tap (x and y as fractions of the
  frame), text (CDP `Input.insertText` into the focused field), a key (Enter, Backspace, Tab, Escape), or a scroll.
  **Done** and **Failed** end the session. The page shows why Jev asked for help.
- **While you drive.** The worker does not act: each tick waits, and the gate refuses any commit Jev would press. A
  control you press yourself is your action, not Jev's; Jev reports what the page shows afterwards. No page observation
  goes to any model until you finish. Typed text is never stored, logged or put in a trace; the trace records only that
  you typed and how many characters. Frames are never stored.
- **After.** On **Done**, or when the link expires, the worker marks its page view stale, takes a fresh observation,
  and carries on from what the page now shows. On **Failed**, the step fails and the planner is told you marked it
  failed; it stops the task or asks you what next. It never retries that step on its own.

## 8. Control words and the kill switch

Handled in code before any model, case-insensitive, as the whole message:

| Message | Effect |
|---|---|
| `stop` | Cancels the task whose question is open, else the only running task, else asks which (buttons) |
| `stop all` | Cancels every task and pauses schedules, watches and suggestions |
| `pause` / `resume` | Pauses or resumes schedules, watches and suggestions; running tasks carry on |
| `status` | Lists tasks with their step, and live schedules and watches |
| `show me` | Offers the wheel for the running task |

The pause flag lives in `settings` and survives restarts. The worker already checks for cancellation before each step;
the gate also checks it immediately before pressing a commit.

## 9. Schedules and watches

### 9.1 Setting one up

- A message such as "pay my BESCOM bill on the 5th of every month", "reorder atta every other Saturday at 9" or "tell me
  when the iPhone 16 128 GB drops below ₹70,000 on Flipkart" routes to SCHEDULE.
- `planner.schedule(message, now)` returns, under a strict schema:
  `{kind: "schedule"|"watch", goal, rule: {freq: "daily"|"weekly"|"monthly"|"every_days"|"every_minutes", at, days,
  day, every}, condition: {type: "price_below"|"price_above"|"in_stock"|"changed"|"due", value}|null, readback}`.
- Code validates it: nothing repeats more often than every 30 minutes; at most 10 schedules and watches are live;
  `at` is a time of day in Asia/Kolkata; `day` is 1–31, and a day the month lacks means its last day. The goal passes
  `privacy.storable`, and any ID in it becomes a placeholder, as Phase B stores memory.
- Jev replies with `readback` in plain words and **Set it** / **Cancel**. Nothing is saved without the tap.
- SCHEDULE also covers "my schedules" (a list with stop buttons), "stop watching the iPhone" and "pause the BESCOM
  schedule".

### 9.2 The scheduler

- One thread wakes every 30 seconds and reads live rows whose `next_at` has passed, skipping all of them while the pause
  flag is set. Times are wall-clock (`zoneinfo` Asia/Kolkata), so a Mac that slept sees its missed runs on wake.
- For each due row: if it is less than 12 hours late, start one task; otherwise skip it and add it to one note sent after
  the pass ("Skipped while your Mac was asleep: …"). Either way, `next_at` moves to the next time after now, so missed
  runs never pile up.
- `next_at` and `last_run_at` are written in the same transaction that claims the row, before the task starts, so a run
  happens at most once even if `jev` stops mid-start.
- A run is `Core.new_task(goal, source=…, schedule_id=…)`: the same thread, gate, skills, memory and approval path as a
  task you start. Runs queue behind others under the existing rules (at most 3 at once, never two on one site).
- Three failed runs in a row pause the row and tell you why, with **Resume** and **Stop**. A failure is a task that ends
  failed, blocked or stuck, or a watch check with no valid result.

### 9.3 Watch checks

- A watch check is a lookup task whose worker carries `expects = {"result": "amount"|"yes_no"|"text"}`. The planner's
  DONE then includes `result`.
- Code checks the result before using it: an amount must appear on the page the task ended on (Phase A's
  `rules.amount_shown`); a yes or no must be literally `yes` or `no`. Anything else is a failed check.
- Code, not a model, compares the result with the condition. `changed` compares a hash of the text result with the last
  one.
- You hear when the condition turns true, and again only after it has turned false and true again. Alerts and suggestions
  wait until 08:00; schedules and your own tasks message at any hour.
- An alert carries **Prepare it**, **Not now** and **Stop watching**. Prepare it starts the purchase or payment task,
  which ends at the usual approval (Section 5).

## 10. Suggestions

- **Recording.** `habit_runs` gets a row when a task that matched a habit (Phase B's `task.habit`) finishes, and when
  Phase B records a new habit from a successful run.
- **Usual slot.** With at least 3 runs:
  - if at least 2 of the last 3 fall on the same weekday within 2 hours of each other, the slot is that weekday at their
    median hour;
  - otherwise, if the gaps between the last runs vary by no more than half their median, the slot is "every {median gap}
    days" at the median hour;
  - otherwise there is no slot and no suggestion.
- **Offering.** From the slot's hour until one hour after it, if the habit hasn't run in the last 24 hours, isn't snoozed or muted, and
  isn't running: "You usually {title} around now. Want me to?" with **Yes**, **Not now** (snoozed for 24 hours) and
  **Stop suggesting this** (mutes the habit). At most 2 offers a day, none between 22:00 and 08:00.
- **Yes** starts the habit as a task with `source = "suggestion"`: Phase B's replay guides it, and it ends at a fresh
  approval (decision 8).

## 11. Data

All tables live in the schema `Memory` opens, created by the same migration path (`CREATE TABLE IF NOT EXISTS`, then
`ALTER … ADD COLUMN IF NOT EXISTS`). Bench schemas get them too; tests keep using throwaway schemas.

| Table | Columns |
|---|---|
| `tasks` | id, goal (placeholders only), title, source (`chat`, `schedule`, `watch`, `suggestion`), schedule_id, status, last_step, commit_label, commit_in_flight, handoffs, progress_lines, created_at, updated_at, ended_at |
| `inbox` | wamid (primary key), body (wiped once handled), status (`queued`, `running`, `done`, `uncertain`), received_at, handled_at |
| `outbox` | id, payload, status (`queued`, `sent`, `failed`, `held` for the 24-hour window), attempts, next_at, wamid, created_at |
| `links` | token_hash (primary key), kind (`secret`, `wheel`), task_id, question_id, created_at, expires_at, used_at |
| `schedules` | id, kind, goal (placeholders only), rule, condition, readback, status (`live`, `paused`, `stopped`), next_at, last_run_at, last_result_hash, alert_on, failures, created_at |
| `habit_runs` | habit_id, at |
| `suggestions` | habit_id, offered_at, answer (`yes`, `not_now`, `stop`) |
| `audit` | id, at, task_id, label, total, host, channel, approval (`explicit`, `standing`), approved_at, outcome (`pressed`, `confirmed`, `mismatch`, `failed`, `unknown`) |
| `settings` | key, value: `paused`, `wa_last_inbound_at` |

`habits` gains `suggest boolean NOT NULL DEFAULT true` and `snoozed_until timestamptz`.

## 12. Safety rules

1. Nothing is ordered, paid, booked or sent without your yes bound to that cart (Phase A's gate, unchanged).
2. A run started by a schedule, a watch or a suggestion never uses a standing approval.
3. Codes and IDs never appear in a WhatsApp message, the inbox or outbox, a log, a trace, the audit table or a model
   payload. They travel only through `/s/` links.
4. The public port serves only the routes of Section 3.1. Webhooks must be signed. Link tokens are random, hashed at rest,
   single-use (a wheel token lasts one session) and short-lived. A `/s/` token takes at most 10 requests a minute; a
   wheel session at most 5 frames a second and 60 inputs a minute. Only `JEV_OWNER_WA` is heard.
5. While you drive, the agent does not act, and nothing you type is stored.
6. `stop all` and the pause flag are checked before every step and immediately before every commit.
7. A commit that was pressed and then interrupted is marked `unknown` in `audit` and reported to you. It is never
   retried automatically.
8. Inbound WhatsApp text is your words only when it comes from `JEV_OWNER_WA`. Page text stays untrusted, as in Phase A.
9. Opening a page never reaches a loopback, private, link-local or carrier-grade NAT address, or a `.local` host,
   checked on the resolved address. Today's `browser.py` checks only the scheme; C1 closes this.
10. Changing a password, an account's phone number or email, or its 2FA, or deleting an account, is a commit that needs
    your explicit instruction in the goal and a yes. Without the instruction, the gate refuses it.
11. A rule value or setting that code doesn't recognise means deny.
12. Jev sends files only from its own media folder.
13. A confirmed total that differs from the approved total is reported at once (Section 5).
14. A voice note, "ok" or an emoji never approves a commit (Section 5).
15. An inbox message interrupted by a restart is never re-run (Section 3.5).

## 13. Failures

| Failure | Behaviour |
|---|---|
| Graph API down or rate-limited | The outbox keeps the message and retries; after 3 failures the row is marked failed and logged locally |
| Tunnel down or Mac asleep | Meta resends webhooks later; the inbox drops repeats. Due schedules follow the missed-run rule |
| Bad token, missing or unapproved template | `jev` stops at start with the fix (Section 3.4) |
| Webhook with a bad signature | 403, logged with no body |
| Unknown message type | One reply (Section 4.1), then 200 |
| Expired or used link | A page saying so; "send the link again" in WhatsApp gets a new one |
| Watch check with no valid result | Counts as a failure; 3 in a row pause the watch |
| Restart mid-task | Section 3.5 |

## 14. Tests

All offline: a fake Graph API (an object that records payloads and can fail on demand), signed fake webhook bodies, an
injected clock, and throwaway Postgres schemas. Each scenario is one test, failing first:

1. A background task asks for approval by buttons; **Yes** presses the commit; the audit row names the yes.
2. A yes 61 minutes after the question makes Jev reload and ask again.
3. An unattended run's approval goes unanswered for 60 minutes; the task stops and says so.
4. A scheduled run with a matching standing approval still asks.
5. A code question becomes a link; the value reaches the worker; the link fails the second time; nothing secret is in
   the outbox, the trace or any model payload.
6. An ID question becomes a 30-minute link and the ID is stored encrypted.
7. Take the wheel (browser fixture): frames arrive; a tap clicks; typed text fills the field and is absent from the
   trace; the worker waits while you drive and re-observes after `done`.
8. `stop`, `stop all`, `pause`, `resume`, `status` and `show me` act without any model call.
9. A message due more than 24 hours after your last one goes as a template and is held; your reply releases it.
10. A schedule fires within one tick of its due time on the fake clock.
11. A watch alerts once when the price drops below the threshold, stays quiet while it stays below, and alerts again
    after it goes above and drops back.
12. A watch result whose amount is not on the page is a failed check; three pause the watch.
13. Missed runs: 3 hours late runs once; 13 hours late is skipped and listed in one note.
14. Suggestions: offered at the usual slot; never more than 2 a day; none at 23:00; **Not now** snoozes; **Stop
    suggesting this** mutes.
15. Restart: active rows become `interrupted` with a Retry message; a commit in flight adds "may have gone through".
16. Voice notes get the one-line reply; a bad signature is 403; a repeated message id is handled once.
17. `jev report` flags a planted hidden value in a trace and a commit without an explicit yes.
18. A voice note is transcribed and routed as text; its audio is deleted; a failed transcription asks you to type.
19. A voice note saying "yes" while an approval is open does not approve; neither does a typed "ok"; a typed "yes"
    approves only when the approval was Jev's last message.
20. A 6-digit reply while a code link is open never reaches a model, is wiped, and gets the link again.
21. An inbox row left `running` by a crash becomes `uncertain` and is reported, never re-run.
22. Take the wheel's **Failed** fails the step and the planner hears it; no observation reaches a model while you drive.
23. OPEN_URL to 127.0.0.1:9333, 192.168.1.1, 169.254.169.254, or a name that resolves to a private address, is refused.
24. A goal without the instruction cannot change a password; with it, the change still asks.
25. Progress: one line after 3 minutes of silence on the fake clock; none while a question is open; none at 23:00 for a
    scheduled run; at most 10 per task.
26. A confirmed total that differs from the approved one marks the audit row `mismatch` and messages you.

`uv run ruff check . && uv run pytest && uv run pytest -m browser && node --check assistant/snapshot.js` stays green.

## 15. Build order

One plan per step in `docs/superpowers/plans/`, each landing fully before the next. After each step, a bench round:
`jev --bench --repeat 1 --unseen 0` must pass at least 6 of 22 with 0 unsafe, and the reliability work picked from its
traces goes in before the next step starts.

| Step | Delivers |
|---|---|
| C1 | `ledger.py` (tasks, outbox, inbox, audit, settings), task `source`, the restart notice, control words, the pause flag, the one-hour refresh, the unattended-approval rules, the typed-yes rule, the confirmed-total check, progress lines, the private-address guard for OPEN_URL, the account-security rule, deny by default. All on today's web channel |
| C2 | `channel_whatsapp.py`, `public.py` (webhook, signatures, inbox), templates and the 24-hour window, button approvals, `/s/` links, setup checks, inbox statuses and `uncertain`, voice notes, typed codes caught |
| C3 | `wheel.py`, the browser screencast and input, wheel offers, **Done** and **Failed** |
| C4 | `scheduler.py`, the SCHEDULE intent, `planner.schedule`, watch results, alerts, the missed-run rule, `next_at` written before each run, `jev schedules` |
| C5 | `habit_runs`, suggestions, `jev report`, then the two-week dogfood |

## 16. Out of scope for v1

Voice calls and spoken replies; iMessage (it needs a Mac, which breaks "deployable"); a cloud browser and a browser per user; automatic resume
after a restart; reading codes from SMS or an inbox, and TOTP; showing the conversation in both web chat and WhatsApp;
Telegram; more than one user; event triggers other than watches (webhooks, email); using OpenBot's or OpenDots' code.

## 17. Research basis

Patterns borrowed, no code copied:

| Pattern | Seen in |
|---|---|
| A background or scheduled run is an ordinary task in the same pipeline | OpenDots `index.ts`, `headless.ts` |
| Approvals answered later from a channel, each tied to one question and used once | OpenDots `pages.ts`; open-dots `action_gateway.py` |
| "Outcome unknown" after an interrupted commit, never retried | open-dots `action_gateway.py` |
| A channel allowlist, tools limited per channel, clean error text | OpenDots `slack-channel.ts` |
| Taking the wheel, with typing that skips the models | OpenBot; OpenDots `ComputerPanel.tsx` |
| Routine guardrails: a floor, a cap, a pause after failures, missed runs skipped | OpenBot routines |
| Watches with de-duplicated alerts | OpenMuse goals and tracking |
| Signed webhooks, hashed single-use tokens, metadata-only audit | OpenDots, open-dots, OpenBot |
| Webhooks saved before the acknowledgement; interrupted work marked uncertain, never replayed | open-instinct `inbox.ts`, `runtime.ts` |
| Approvals bound to the exact request and used once; a typed yes only under strict rules | open-instinct `approvals.ts` |
| Takeover with the agent blind, ending in Done or Failed | open-instinct `desktopd.ts` |
| A guard against loopback and private addresses | open-instinct `net-guard.ts` |
| Voice notes in, transcript kept and audio not; progress every 3 minutes | open-instinct requirements US-M-5 and B-8 |
| Account-security changes only on instruction | Instinct incidents, as reported in open-instinct's research notes |

Avoided: purchase guards written only in a prompt (OpenDots), approving every keystroke (open-dots), a keyword-only
payment hand-off that also blocks OTP entry (OpenBot), a hosted thread store (CopilotKit Intelligence), and card details
in the model's context and logs with a checkout stop written only in the prompt (open-instinct).

## 18. Setup checklist (you)

1. In Meta for Developers, create a Business app and add WhatsApp. Use the test number and add your own number as a
   recipient.
2. In Business Manager, create a system user with `whatsapp_business_messaging` and `whatsapp_business_management`, and
   generate a permanent token.
3. Note the phone-number id and the app secret, and choose a verify token.
4. Submit the three templates of Section 4.3 (English) and wait for approval.
5. Run a tunnel with a stable HTTPS hostname to `JEV_PUBLIC_PORT`: Cloudflare Tunnel with your domain, or ngrok's free
   static domain. Set `JEV_PUBLIC_URL`.
6. In the app's WhatsApp configuration, set the callback URL to `JEV_PUBLIC_URL/webhook` with your verify token, and
   subscribe to `messages`.
7. Put the values in `.env` and start `jev`. It checks the token and the templates before serving.

## 19. Risks

| Risk | Mitigation |
|---|---|
| Task reliability (6/22 on the last bench) makes unwatched runs fail | Stalls become "needs you" messages with the wheel; a bench round after every build step |
| The Mac sleeps, so v1 is not truly always-on | The missed-run rule; portable code, so a cloud machine later is a configuration change |
| The tunnel exposes the Mac | Only the public port's routes; signatures; hashed single-use tokens; rate limits; the chat port stays loopback |
| Meta classes `jev_suggestion` as marketing | Send suggestions only inside the 24-hour window if the template is refused |
| A message arrives for the wrong task | Button ids carry question ids; one question open at a time; messages name the task when several run |
| Inbound messages carry prompt injection | Only the owner's number is heard; the gate, not a model, decides commits |
| A voice note is misheard (English, Hindi or a mix) | The approval shows exactly what will be bought; a voice note never approves; "I couldn't make that out" when unsure |
