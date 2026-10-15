# Jev Agent: an in-house browser agent on browser-use's architecture

Date: 15 Oct 2026. Status: design, for review.

## 1. Goal

Build Jev's own browser agent, written from scratch in this repo, with the architecture of browser-use
(github.com/browser-use/browser-use). It should be faster, cheaper and more reliable than Jev's current loops, work on
sites it has never seen, and serve many users at once.

Jev adds two things on top of that architecture:

1. **The user gives what Jev doesn't have.** A captcha, an OTP, a card number, a CVV, an expiry, a password, or any
   detail Jev lacks is asked for in Jev's chat. Secrets go through the hidden field and are never shown to a model.
2. **The user confirms the payment.** Nothing that pays, buys or commits money happens without the user's yes, asked
   in code on the payment screen.

Everything else follows browser-use's architecture.

### 1.1 Clean room

- No browser-use code is copied, forked, vendored or installed. The project's ideas and architecture (MIT-licensed
  code; ideas aren't copyrightable) are the only input, taken from our own study notes (§11). Nothing comes from
  workflow-use (AGPL-3.0).
- Jev's own code (browser.py, snapshot.js masking, gate.py, money.py, skills, memory) is ours, and is reused or
  rewritten freely.
- No third-party agent SDKs. Model calls go through Jev's own model client.

### 1.2 Targets

| What | Target |
|---|---|
| Purchase on a store with a learned route | ≤ 60 s agent time (excluding time waiting on the user), ≤ $0.02 model spend |
| First run on an unseen store | ≤ 3 min agent time |
| Unseen-site bench, read-only | ≥ 90 % success |
| Concurrency at launch | 20 tasks at once, queued beyond that |

## 2. Decisions

| Area | Decision |
|---|---|
| Scope | One loop for every task. Vouchers and bills move first; the planner and picker loop and the page-model (actor) loop are retired last. |
| Browsers | Our own Chrome pool on an Indian server. Each task runs in its own browser context, with an Indian residential proxy per context. Nothing is kept after the task ends. |
| CDP | Our own thin async CDP client. browser_harness is dropped. |
| Models | gpt-6-luna for every step; one step goes to gpt-6-astra on a stall, then back. Routes skip the model where they can. |
| Page reading | Chrome's DOMSnapshot (paint order, bounds), the DOM tree and the accessibility tree, across all frames, including cross-origin iframes and shadow roots. Elements hidden behind others are dropped. Elements are numbered by backendNodeId, and new ones are marked `*`. |
| Step | One model call per step returning memory, an evaluation of the last goal, the next goal, a plan checklist, the facts and up to 5 actions. The list stops early when the page moves, a question comes up, or the gate steps in. |
| Screenshots | Sent only when the model asks, with fields masked. Never sent on a page with a card, OTP or password box, and never after the yes. |
| Tools | The browser-use set: navigate, search, go_back, click, input, select, upload, scroll, send_keys, tabs, wait, extract, find_text / search_page, file area with todo.md, evaluate, done. Jev adds ask_user. |
| evaluate | Read-only and fenced (§6.4). |
| Dialogs | Never auto-accepted. A dialog that commits money goes through the payment confirmation. |
| Captchas | Basic stealth only: real Chrome, AutomationControlled off, human-like typing, Indian proxy. Every captcha goes to the user as a picture. No solver services, no fingerprint spoofing. |
| Memory | The recent steps plus a summary of the rest, written by the model, with secrets filtered out. Site notes and the route go in as guidance. |
| Replay | Routes find elements again through five lookups in turn: element hash, stable hash, xpath, role and accessible name, then a unique name, id or aria-label. Amounts, brand and phone are variables. A missed step goes to the model, and the route resumes after it. |
| Hand-off on a server | Hidden field, captcha picture and UPI QR only. Net banking and wallets aren't offered on a server. |
| Analytics | None. Only Jev's own traces, kept on our servers. |
| Staging | Six stages, each with its own spec, plan and build (§9). |
| Acceptance | Replica-store tests in headless Chrome, a live read-only bench (≥ 90 %), and from stage 3 one real ₹50–₹250 purchase by the user on each store that changed. |

## 3. Architecture

```
brain (chat; compare, buy, pay bill)        ← unchanged: Jev's chat brain and its tools
   │ task: goal, start URL, job (money rules), guide
   ▼
agent/      loop · step · message manager · loop detector · compaction · routes (record/replay)
   │ actions                      ▲ page state
   ▼                              │
tools/      registry · actions · ask_user · done(job schema) ── guard: gate (payment confirmation, money rules)
   │ CDP commands                 ▲ events
   ▼                              │
browser/    session (one task = one browser context) · event bus · watchdogs
dom/        capture (snapshot + DOM + AX, all frames) · visibility · paint order · serializer · hashes · masking
cdp/        async websocket client · sessions · event routing
pool/       Chrome processes · contexts · proxies · queue (stage 5)
```

The money layer (money.py, gate.py, payment records, the 24-hour check, voucher-code masking) wraps the tools.
It is the only code that decides whether a payment may happen.

## 4. Components

### 4.1 cdp/: client

- One websocket per Chrome. Each command carries an id, and each reply resolves its future.
- Events are routed by sessionId, and flattened target sessions are attached. Commands time out.
- No daemon, no IPC hop and no control tab.
- API: `send(method, params, session=None)`, `on(event, handler, session=None)`, `attach(target) -> session`.

### 4.2 browser/: session and watchdogs

**`BrowserSession`** is one task:
- It creates a browser context on start (`Target.createBrowserContext`, with a proxy when it runs on the pool) and
  disposes of it on close.
- It owns its tabs (targets) and the focused tab, sets download and permission settings for its context, and owns an
  event bus.

**Watchdogs**, each a small class listening on the bus, as in browser-use:

| Watchdog | What it does |
|---|---|
| dom | Builds the page state on request. |
| downloads | Tracks a download to completion, gives the file to the task, and keeps PDFs. |
| dialogs | Reads every JS dialog. An alert is dismissed and noted for the model. A confirm or prompt is held for the model to answer through an action. A confirm whose text pays or commits goes to the payment confirmation. Nothing is auto-accepted. |
| security | Checks the allowed and blocked domains before navigation, on redirects and on new tabs. URLs are parsed strictly: userinfo is removed and the hostname normalised (the CVE-2025-47241 lesson). A page opened by typing its address never takes a payment, as now. |
| crash | Notices a crashed target and reports it to the loop. The loop reopens the tab or stops the task, saying why. |
| popups | Adopts a tab the page opens, which then becomes the focused tab. |
| permissions | Denies notifications, camera and microphone. Grants geolocation to the context. |
| screenshot | Takes masked captures (§6.3). |

### 4.3 dom/: page reader

**Capture.** `DOMSnapshot.captureSnapshot` (with layout, paint order and computed styles), `DOM.getDocument`
(pierce) and `Accessibility.getFullAXTree`. This is done for every frame, including cross-origin iframes through
their own targets (depth ≤ 5, ≤ 100 frames), and shadow roots both open and closed.

**Visibility.** An element is kept if its bounds, intersected through every parent frame, lie inside the viewport plus
1000 px, it has a non-zero size, and CSS doesn't hide it.

**Occlusion.** Elements are walked from the highest paint order down, keeping a union of the opaque rectangles above.
An element fully under them is dropped; transparent layers don't count as covering. At click time the target is
hit-tested again.

**Interactive.** By tag, role, listeners, tabindex, contenteditable and cursor, with the accessible name from the AX tree.

**Serialised for the model.** One line per element:
```
*[backendNodeId]<role-or-tag name=… value=… placeholder=… aria-*… />
```
Text children go on the lines that follow, and shadow, iframe and scroll containers are marked. A `*` marks an element
that is new since the last step. The text is capped at 40,000 characters, along with the counts of pages above and
below.

**Masking (Jev's rules carried over).**
- A field holding a secret shows `[secret]`, and a password value is never shown.
- The user's saved details show as `[profile:…]` placeholders.
- After the yes, code-like tokens are masked, except rupee amounts and the order id (money.mask_codes).

**Hashes for replay.** For each element:
- an element hash: the parent tag path, the static attributes and the accessible name;
- a stable hash: the same, without dynamic class names;
- its xpath.

### 4.4 agent/: loop

**`run(task)`** loops `step()` until done, a stop, or the limits below.

**`step()`**, in order:
1. Prepare the context: the page state from the dom watchdog, any queued screenshot request (§6.3), and nudges for
   budget, replan, loop and forced done.
2. The route: if a learned step's element is on the page, run it with no model call (§4.6).
3. Otherwise one structured-output call: `{memory, evaluation_previous_goal, next_goal, plan_update, facts, actions[≤5]}`.
   The plan is a checklist, and facts follow the job's schema (money.FACTS).
4. Run the actions through the tools (`multi_act`). Each one is validated against the current page, and the list
   stops at a page change, a navigation, a tab switch, an error, a question to the user, or the gate.
5. Post-process: store the facts (each grounded on the page, as `take_facts` does now), record the history, run the
   loop detector, and save the route on success.

**Limits.**
- Steps: 40 per task, plus 10 after the payment's yes, counted from the yes.
- Failures: 5 in a row trigger a forced `done`.
- Time: 180 s per step.
- Escalation: a stall, a refused action or a failure gives the next step to gpt-6-astra.

**Loop detector.** Normalised actions are hashed over a window of 20, and each page is fingerprinted by URL, element
count and DOM text hash. Nudges come at 5, 8 and 12 repeats. A money job stops at the third nudge, saying nothing was
paid.

**Message manager.**
- The prompt is one system prompt plus one rebuilt state message: the user request, the history, the agent state
  (plan, todo, secret placeholder names) and the browser state.
- Above about 40,000 characters, the history is summarised by the model into 6,000 characters or fewer, keeping the
  last 6 steps.
- Literal secret values are replaced with their placeholders before anything is stored or sent.

### 4.5 tools/: registry and actions

**Registry.** `@action(description, params=Model, domains=…, ends_list=bool)`. The model's action union is generated
from the registry for the current page's domain. Parameters are validated, and named dependencies (session, page,
secrets, job) are injected.

**Actions.**

| Action | Notes |
|---|---|
| navigate, search, go_back | End the action list. |
| click | By element number; coordinates as a fallback. |
| input | Human-like key-by-key typing for secrets and card details. |
| select, upload, scroll, send_keys | |
| switch_tab, close_tab, wait | |
| extract | A cheap model reads the page into facts; its output is masked. |
| find_text, search_page | |
| file area: write, read, replace, todo.md | |
| evaluate | Read-only (§6.4). |
| ask_user | §5. |
| done | The job's result schema. |

**Every action runs through `execute(action)`:**
1. the security re-check of the domain;
2. the payment confirmation (§6.1);
3. secret substitution (§6.2);
4. the handler;
5. masking of the result.

### 4.6 Routes: record and replay

**Record.** On success, the history up to the yes is saved under the store or biller's key (memory `routes`). Each step
keeps its action, the five lookups for its element, and the text it typed as a placeholder or variable (`{brand}`,
`{amount}`, `[profile:phone]`, `[ask]`).

These steps are marked `think` and are never replayed:
- an amount;
- a step taken on a page that showed the cart, payable, fee or bill;
- the yes, and everything after it.

**Replay.** Before each model call, the next route step (or one of the 2 after it) is looked up through the five
lookups in turn. If exactly one element matches, it runs through `execute` with no model call. Otherwise the model
takes the step, and the route resumes after the step the model actually took.

**Learning.**
- A newer success replaces the route.
- A run that used the route and failed before the yes drops it.

### 4.7 pool/: Chrome pool (stage 5)

- N Chrome processes on an Indian server, each with up to 10 live contexts.
- A task queue with a cap of 20 concurrent tasks.
- A residential Indian proxy per context.
- A health check restarts a crashed Chrome, and its tasks stop, saying so.
- Locally, there is one Chrome and the same interface.

## 5. Asking the user

`ask_user(kind, field?, question)` is called by the model, or by code when a step needs it:

| kind | When | How it reaches the user | What the model sees |
|---|---|---|---|
| `secret` | OTP, CVV, card number, expiry, password, PIN, or any value Jev lacks that it shouldn't see | Hidden field in Jev's chat | Only `<secret>answerN</secret>` |
| `captcha` | A captcha on the page | A picture of the captcha, sized to read on a phone; the user's answer | Only `[captcha]` |
| `qr` | A UPI QR after the yes | The QR with app links, plus "I paid" and "I didn't pay" buttons | Whether the user says they paid |
| `choice` / `text` | A detail the user must decide or supply that isn't a secret (which bank, which address) | A normal chat question | The answer |

**Code-level backstops** (as now):
- Typing a literal into a field that holds a secret is refused.
- A secret box with no answer yet triggers a `secret` ask by itself.
- A card's details are never typed into a code box.
- Codes have their own try limits, and a bank OTP gets fresh tries.

The expiry is asked "as MM/YY" and typed in the form its box takes.

## 6. Money safety

### 6.1 Payment confirmation

Before any click, key press or dialog accept, `execute` asks the gate whether the action commits: pays, buys, places,
books or submits money (gate.commit_label, as now).

If it does:
- The approval is written in code from the job and the page: what is bought or paid, from whom, how it is paid, and
  the payable amount, including any fee.
- It is asked once, as a yes or no.
- Only a yes runs the action.
- The bank's OTP page and the gateway steps that follow belong to that payment, and ride on that yes.

Money jobs never use standing or pre-approval. The job's checks run before the question:
- the cart is exactly the order;
- the payable is no more than the amount plus any fee shown;
- no unsettled purchase or bill exists in the last 24 hours;
- the page wasn't opened by typing its address.

### 6.2 Secrets

- The model only ever sees placeholder names.
- Values are put in at execution time, only on hosts where they were asked for.
- A literal value is redacted everywhere: prompts, history, the summary, traces, `extract` output and tool results.
- Card numbers and CVVs are never stored. OTPs are used once.

### 6.3 Screenshots

- A screenshot is sent only when the model asks, with every filled field and the user's details masked (browser.py's
  MASK, as now).
- It is refused on a page with any card, OTP or password box, and after the yes.

### 6.4 evaluate

- It runs in an isolated world, and its result passes the masking.
- It is refused:
  - on a page with any secret box;
  - on a payment or bank host;
  - in a money job from the cart onwards;
  - after the yes.
- A script that clicks, submits, navigates or sets values is refused. This is checked statically before it runs, and
  by comparing the page before and after; a change is reported and the money job stops.

## 7. Data flow: buying a voucher

1. The brain compares stores and the user picks "SmartBuy + Regalia Gold".
2. The task starts with its goal, the brand page URL and the job (vouchers).
3. A session opens a new context.
4. The route runs the known steps: brand page and sign-in with `[profile:phone]`. The OTP goes through `ask_user(secret)`.
5. The model adds the amounts and reads the cart into facts. The gate's cart check passes.
6. Checkout opens the card form. The model plans every box in one turn, and each `[ask]` goes through
   `ask_user(secret)`.
7. The model presses PROCEED. `execute` asks the gate, which asks the user to confirm with the written approval. On
   yes, the click runs and the payment record is written as "started".
8. The bank's OTP page appears; the OTP goes through `ask_user(secret)`. SUBMIT rides on the yes.
9. The order page shows. The model sets `order_id`, the proof check passes, and Jev reports "Bought …". The record is
   marked paid and the route is saved.
10. The context is disposed of.

## 8. Errors

| Case | What happens |
|---|---|
| Model gives no valid action | One retry, then a forced `done(failure)`. |
| Action on a missing element | "Element N not on the page" goes back to the model. |
| Page changes mid-list | The list stops and the page is read again. |
| Crash | The tab is reopened once, then the task stops. |
| Context or proxy failure | One new context, then the task stops. |
| After the yes, anything unclear | Never repeated. The user is told to check SMS and email, and the record is marked unclear. |
| Declined | The record is marked failed and Jev reports "Not bought". |
| A loop in a money job | The job stops, saying nothing was paid. |

## 9. Stages

Each stage gets its own spec, plan and build. Each gate includes all unit tests and headless-Chrome replica tests.

| # | Stage | Delivers | Gate |
|---|---|---|---|
| 1 | Browser core | cdp/ client; browser/ session, contexts, watchdogs; dom/ reader with masking and hashes. A CLI that prints the serialised page. | The page reader reads every replica fixture and 10 live store and biller pages read-only. No secret value appears in any output. |
| 2 | Agent loop and tools | agent/ loop, message manager, loop detector, compaction; tools/ registry and actions; ask_user; the gate in `execute`. | Replica purchase and bill flows pass, including the safety set: fee, leftover cart, injected page, declined card. Unseen-site read bench ≥ 90 %. |
| 3 | Money jobs switch | Vouchers, voucher reads and bills run on the new agent. The actor loop is deleted. | Targets met on the replicas. One real ₹50–₹250 purchase per store changed. All live reads pass. |
| 4 | Replay | Five-lookup routes with variables, think steps and resume. | A second replica purchase uses ≤ 50 % of the first's model turns. Live: a SmartBuy purchase with a learned route takes ≤ 60 s agent time. |
| 5 | Pool | Mumbai server, Chrome pool, queue, proxies. | 20 concurrent replica purchases with no cross-session leakage. Live read bench from the server ≥ 90 %. |
| 6 | Everything else | General tasks (Experian, MyUPI, shopping) move over. The planner and picker loop, snapshot.js and browser_harness are deleted. | Each task's bench passes on the new agent. |

## 10. Testing

- **Units:** each module on its own.
  - The CDP client against a fake socket.
  - The serialiser against recorded snapshots.
  - Hashes and lookups against fixture pages.
  - The registry and `execute` guards.
  - Secret redaction everywhere a string goes.
- **Replica tests:** headless Chrome on the voucher, bill, OTP and gateway fixtures, with the model scripted. These are
  extended with cross-origin iframe, shadow DOM, covered-button and confirm-dialog fixtures.
- **Live bench:** read-only. Every store × 5 brands, and 5 billers' bill reads.
- **Live purchase:** one per store changed, by the user, from stage 3.
- **Speed:** agent time and spend come from traces and are checked against §1.2 at stages 3 and 4.

## 11. Reference: browser-use study (architecture only, no code)

From the code study of commit c75e847 (7 Oct 2026) and the web research.

**Taken:**
- the event bus with watchdogs;
- CDP only;
- the DOMSnapshot, DOM and AX capture with paint-order occlusion;
- backendNodeId element numbers with `*` on new elements;
- the step output (memory, goals, plan, actions ≤ 5) and the `multi_act` page-change guards;
- the loop detector;
- history compaction;
- the action registry;
- domain-scoped secret placeholders;
- the replay with five lookups.

**Changed for Jev:**

| browser-use | Jev |
|---|---|
| Dialogs auto-accepted | Never auto-accepted |
| Screenshots unmasked every step | Masked, on request only |
| No ask-user action and no hook that can block an action | ask_user, and the gate in `execute` |
| TOTP and tool outputs unredacted | Redacted |
| PostHog analytics on by default | None |
| One Chrome process per session | One context per task |
| CrashWatchdog disabled | Enabled |
| Allow-list bug from loose URL parsing | Strict URL parsing |

**Not taken:**
- the LLM adapters (we use Jev's model client);
- MCP, cloud skills, sandbox, the beta Rust agent, the judge, GIF and HAR recording;
- workflow-use (AGPL).

**Measured caution:** human-graded, early-2025 browser-use with GPT-4o succeeded on about 30 % of live tasks
(Online-Mind2Web), so Jev's reliability comes from its guards, routes and bench, not from the architecture alone.
