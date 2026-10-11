# Jev — Brain and hands: a conversation agent over the browser worker

Date: 2026-10-12. Branch: `mini-jev-agent`. Builds on the Phase A and B specs; everything they specify stays unless this
spec changes it. Demo target: today — cancel a UPI AutoPay mandate on NPCI MyUPI, and raise an Experian credit-report
dispute, from chat, with the agent keeping the conversation's context.

## 1. Why

Two runs on 12 Oct show the problem (traces `20261012-020252-…cancel-my-subscriptions` and `…-020329-…aisle…`):

- "Cancel my subscriptions": the planner chose OPENAI LLC by itself; a guard blocked it; the `no_mandate` rule then
  ended the task ("None of your UPI AutoPay mandates is for what you asked") instead of asking which one.
- "Cancel my aisle subscription": answered "not found" 6.7 s in, from a mandate list still drawing (GOOGLE PLAY's card
  half-filled, AISLE's not yet drawn). Jev's done-check confirmed it on a page showing only the header.

Root cause: the layers are inverted. Jev (a 7-label classifier) and regexes interpret the chat; the LLM sees only one
task's goal string and one page, never the conversation. Every message starts a task from zero, so an ambiguous request
can only be guessed or ended. This spec puts an LLM agent in charge of the conversation (the brain) and turns the
existing worker into its tools (the hands). Jev keeps its right job: picking the next action on a page.

## 2. Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Approach | Brain + hands: a tool-calling conversation agent; each tool runs today's worker with an exact goal |
| 2 | Brain model | `gpt-5.6-luna` via the Responses API (function tools with reasoning need it), reasoning effort `low`. `BRAIN_MODEL` and `BRAIN_EFFORT` override |
| 3 | Planner model | The worker's planner stays `gpt-4.1-mini` (tuned; the brain removes the ambiguous goals that tripped it) |
| 4 | Cancel rule | A payee the user named ("cancel aisle") goes straight to the cancel QR: their UPI PIN is the real confirmation. A payee the brain inferred ("cancel chatgpt" → OPENAI LLC) needs the user's yes first. Enforced in code (§5.3) |
| 5 | Experian | Built and tested live: the user has a fresh report's ERN and UTI; the worker asks for them in the hidden field |
| 6 | Old router | Jev's chat intents, the which-task buttons, task amendments, `planner.chat` and `planner.split` are deleted (POC: no legacy paths) |

## 3. Architecture

```
Chat (web today, WhatsApp later)
 └ Core — channel; an open question's answer (captcha, code, QR tap) goes straight to it and never to a model;
          "log out"; "stop" while a tool runs
    └ Brain — one per user; gpt-5.6-luna tool loop
        • keeps the conversation (Responses items) and a snapshot: the user's mandates with read time, saved ID names
        • playbooks for NPCI MyUPI and Experian in its instructions
        • each step: call a tool, or reply (text + tap options), which ends the turn
        └ Hands — Core.execute(goal, …) runs one worker task to its end on the brain's thread
            upi_mandates · upi_cancel · upi_pause · upi_task · experian_dispute · browse · memory
            Inside: deterministic sign-in, captcha/code relay, card guards, QR hand-off; Jev picks the next action
```

## 4. Message flow (Core)

`Core.receive(event)`, in order:

1. Taps: offers (remember/rule/note/guess) and question taps as today; a tap on a brain option goes to the brain as
   the option's words.
2. An open question takes the message as its answer as today (secret, captcha reply, reply-to, file, number). New: any
   other text answers it too, except a stop word, which cancels the task that asked. (Questions are only asked by a
   running tool now, so text arriving while one is open is for it.)
3. "Log out" as today.
4. A stop word while a tool runs cancels that tool's task.
5. Everything else goes to the brain's inbox. If the brain is busy, Core says once: "I'll get to that as soon as this
   finishes."

The brain processes its inbox one message at a time on its own thread. The typing indicator shows while it thinks.

## 5. The brain (`assistant/brain.py`)

### 5.1 Loop

- `hear(text)` queues a message; the brain thread runs `turn(text)` for each.
- `turn`: append the user item; up to `STEPS = 8` model calls. Each call must call exactly one tool
  (`tool_choice: required`, `parallel_tool_calls: false`). `reply` ends the turn; any other tool runs, and its result goes
  back as a `function_call_output`. The last step offers only `reply`.
- A model error: say "I couldn't work that out just now ({error}). Please try again." and end the turn. The items of a
  failed turn are dropped, so the history stays valid.

### 5.2 Context

- **Instructions** (static, cache-friendly): who Jev is; rules (reply is the only way to talk; WhatsApp style; use what
  the chat established; never guess or dead-end — show what the user has and ask with options; never ask for codes, PINs,
  passwords or IDs in chat; never claim a result a tool didn't report); the NPCI MyUPI playbook (mandates, cancel rule,
  brands billed by companies, app-store mandates, pause dates, transactions/complaints/UPI ID/payee check/UPI questions
  via `upi_task`); the Experian playbook (what a dispute needs; ERN and UTI typed by the tool; final submit asks the user's
  yes itself); other sites via `browse`; memory via `memory`.
- **Snapshot** (a developer message appended at each call, never stored): the time; saved ID names (`memory.ids()`
  labels, never values); the mandates last read, with their read time.
- **History**: every item of the conversation, trimmed at a user-message boundary to the last 60 items; reasoning items
  of earlier turns are dropped.
- Every call goes through `Core.hidden`, so the user's IDs are redacted before any model sees them (`post_json` refuses a
  leak).

### 5.3 Tools

Each returns JSON with a `status` and, when useful, a `next` hint, as the companion harness does.

| Tool | Arguments | Runs | Result |
|---|---|---|---|
| `upi_mandates` | — | worker on MyUPI: "List all my UPI AutoPay mandates" | `done` + `mandates` [{payee, details}] |
| `upi_cancel` | `payee` | "Cancel the UPI AutoPay mandate whose payee is {payee}" | `approved` / `not_approved` / `not_found` (+ `mandates`) / `stopped` / `failed` |
| `upi_pause` | `payee`, `until` | "Pause the UPI AutoPay mandate whose payee is {payee} until {until}" | as `upi_cancel` |
| `upi_task` | `goal` | the goal on MyUPI (transactions, complaints, UPI ID, payee check, UPI questions) | `done` + `report` |
| `experian_dispute` | `kind` (Account Details / Personal Details / Credit Enquiry), `account`, `wrong`, `correct` | "Raise a dispute on my Experian credit report: …" on the Experian note's start page | `done` + `report` |
| `browse` | `goal` | today's path: intake, habit, start page | `done` + `report` |
| `memory` | `words` | today's memory chat flow (it replies itself) | `done` |
| `reply` | `text`, `options` (≤ 10 labels) | sends the message; ends the turn. Empty text sends nothing (after `memory`) | — |

Guards (code, returned as `rejected` with `next`, never raised):

- **Cancel/pause naming** (decision 4): `upi_cancel`/`upi_pause` run only when the payee's words appear in this turn's
  user message, or the brain's previous reply named that payee and this message affirms it (yes, ok, sure, go ahead,
  do it, haan, a tap on an option naming it). Otherwise: `next` = ask "Cancel {payee}?" with options naming it.
- **Known list**: when mandates were read, the payee must be one of them (case-insensitive); otherwise `rejected` with
  the list.

Progress: when a tool starts a browser task, Core sends one fixed line (e.g. "Checking your UPI AutoPay mandates on
MyUPI…"). No model writes it.

### 5.4 Replies

`reply` options become WhatsApp reply buttons (≤ 3) or a list (≤ 10) through `messages.render`, with ids
`b{n}:{i}`. A tap comes back to the brain as the option's label; typed answers ("the second one", "aisle") also reach the
brain, which reads them against its own last reply.

### 5.5 Trace

One JSON line per turn in `~/.jev/traces/brain-YYYYMMDD.jsonl`: time, the message (redacted), each step's tool,
arguments, status and milliseconds, the reply and options, the model's latency. The worker's per-task traces stay.

## 6. Hands (worker and Core changes)

- **`Core.execute(goal, url=None, skill=None)`** runs one task to its end on the calling thread and returns
  `{status, outcome, report, mandates}`. With a `url` it skips intake and the start page (`preauth` false); without one,
  `prepare` runs as today. A brain task's result message, its "Stopped …" line, and an escaped error come back in the
  return value instead of the chat; questions, pictures and info lines still reach the chat live.
- **Outcome**: the worker records `state["outcome"]`: `approved` / `not_approved` in `hand_off`, `not_found` in
  `no_mandate`. Mandates come from the final page's `cards` and `card_lines`.
- **The list settles before it is read**: on a `cards: payee` site, an observation on the mandate list (cards present,
  or the note's `list` marker text) is read again every 0.7 s until two reads show the same cards with the same words,
  at most 6 reads. The note gains `list: Manage recurring payments`.
- `no_mandate` stays (nothing is pressed), but its result now goes to the brain, which asks.

## 7. Safety (unchanged rules, new paths)

- Captcha text, codes, IDs and QR taps never reach the brain: Core answers the open question directly; the chat log keeps
  "••••" for secrets; every brain call is redacted and leak-checked.
- The brain never types into pages: tools take exact goals; the worker's gate still requires the user's yes for any
  commit (Experian's final submit, a complaint's submit); a mandate cancel or pause completes only with the user's UPI
  PIN in their UPI app.
- Brain tasks never carry pre-authorisation.

## 8. Testing

- `tests/test_brain.py` (fake model, fake core): ambiguous cancel lists then asks with options; a named payee cancels
  directly; an inferred payee is rejected until confirmed; the known-list guard; a tap's label reaches the next turn; the
  step cap forces a reply; a model error ends the turn cleanly; the snapshot carries ID names, never values; redaction.
- `tests/test_core.py`: routing per §4 (open question takes text; stop words; busy notice; brain inbox); `execute`
  captures the result and doesn't post it; brain option taps. Tests of the deleted router go.
- `tests/test_worker.py`: the list settles (a fake browser whose cards grow, then hold); outcomes.
- `tests/test_model.py`: the Responses payload (model, tools, `tool_choice`, reasoning, `store: false`, encrypted
  reasoning included, redaction).
- Live acceptance (the user, in the web chat): "What AutoPays do I have?"; "Cancel my subscriptions" → pick AISLE → QR →
  Not now; "cancel chatgpt" → confirm → QR → Not now; "cancel netflix" → no mandate, options; "dispute my Experian
  report" → details → ERN/UTI hidden → yes at the final submit.

## 9. Known limits (accepted for now)

- One tool runs at a time per user, and the brain waits for it; a message sent meanwhile waits its turn.
- The conversation lives in memory: a restart forgets it (the worker's sign-ins and traces are unaffected).
- `gpt-5.6-luna`'s price wasn't checked; a demo uses a few cents.

## 10. References

- Rivo companion harness (`rivo-companion/src/harness`): the step loop with a step cap and final reply-only steps; the
  per-user snapshot message; `ask_user` ending the turn with the tapped label as the next message; guards returning
  rejections as tool results with a next-move hint. Its bugs avoided: per-turn state reset, questions code asked that the
  model never sees, process-wide globals.
- OpenInstinct, Open Instinct, OpenBot, OpenDots (research page https://claude.ai/artifact/Ct2MnfYsdjUGdVvt1eaPFE): an
  LLM tool loop at the conversation level; approvals enforced in code, not the prompt; the browser keeps its logins and
  hands codes to the user.
