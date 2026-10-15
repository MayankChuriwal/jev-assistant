# Jev Agent: an end-to-end personal finance assistant on browser-use's architecture

Date: 16 Oct 2026 (v3: adds the per-user vault, the card vault behind a launch gate, the ECS deployment plan and the
rivo-companion API; v2 and v1 were 15–16 Oct). Status: design, for review.

## 1. Goal

Jev is the user's end-to-end personal finance assistant. The user asks for a task. Jev does it in the background on the
real websites, from start to finish, and comes back to the user only for what a person must give.

The browser agent underneath is built in-house, from scratch, on the architecture of browser-use
(github.com/browser-use/browser-use), with Jev's guardrails on top.

### 1.1 What only the user gives (the ask list)

Jev asks the user for these, and only these:

1. **Secrets not in the user's vault:**
   - Always asked: OTPs, CVVs, net-banking and wallet logins.
   - Asked only until saved in the vault: site passwords; card number and expiry, once the card vault is switched on
     (§7).
   - A UPI PIN is never typed by Jev at all: the user approves in their own UPI app.
2. **Captchas.**
3. **Consent:** every payment, and every irreversible submission made for the user (§6.1).
4. **A personal detail Jev doesn't have yet.** It is asked once, then saved in the user's vault (§7).
5. **A clarification,** only when the request matches more than one of the user's saved targets, or none (two
   electricity accounts, an unknown biller).

Everything else Jev decides and does by itself:
- which store, card or way to pay gives the most savings;
- every page, form, banner and pop-up;
- every retry and recovery;
- switching to the next-best option when one fails before the payment.

### 1.2 Guardrails

- **Consent.** No payment and no irreversible submission without the user's yes. The yes is asked in code and bound to
  exactly what is paid or submitted.
- **Kept only in the user's vault.** The vault is encrypted per user (§7). It may hold:
  - personal info;
  - site logins (never banks);
  - card label and last 4;
  - card number and expiry, once the card vault's launch gate is passed.
- **Never kept anywhere:** OTPs, CVVs, UPI PINs, net-banking and wallet logins, Aadhaar numbers. Not in the database,
  traces, logs or model payloads.
- **Models never see a secret or a vault value.** Models see only placeholders. Values are decrypted only inside the
  browser worker, at the moment they are typed.

### 1.3 Clean room

- No browser-use code is copied, forked, vendored or installed.
  - Its architecture and ideas are the only input, taken from our own study notes (§15).
  - Its prompts are not copied either; ours are written in our own words.
  - Nothing comes from workflow-use (AGPL-3.0).
- Jev's own code is ours, and is reused or rewritten freely.
- No third-party agent SDKs and no browser extensions.

### 1.4 Targets

| What | Target |
|---|---|
| Tasks finished | ≥ 95 % of started tasks end done. Not counted against this: the user's no, a declined card, or a task the user abandons. |
| Asks | Only from the ask list. Every ask is counted by kind in the trace, and tests fail on any other ask. |
| Purchase on a store with a learned route | ≤ 60 s agent time (excluding time waiting on the user), ≤ $0.02 model spend |
| First run on an unseen store | ≤ 3 min agent time |
| Comparing all stores for a brand | ≤ 60 s (stores read in parallel) |
| Unseen-site bench, read-only | ≥ 90 % success |
| Concurrency at launch | 20 tasks at once, queued beyond that |

## 2. Decisions

| Area | Decision |
|---|---|
| Choosing | Jev picks the best option (most savings) by itself. The payment consent names the choice and the saving, with "use another option" offered. |
| Consent scope | Every payment, every submission that can't be undone or speaks for the user (bureau dispute, card application, mandate cancel), and every consent given on the user's behalf (a bureau pull, account terms). |
| Sessions | Throwaway: every task starts signed out, in its own browser context, deleted at the end. Sign-in OTPs are asked every time. |
| Vault | Per user, encrypted, Instinct-style (§7). It holds Logins (sites, never banks), Cards (label and last 4; number and expiry behind the launch gate) and Personal info (name, phone, email, addresses, PAN, date of birth, UPI ID, biller account numbers), with an Activity log of every use. Values are masked when shown and never revealed. |
| Card vault | Built now as a separate, isolated service. Switched off in production until a legal opinion on RBI's card-on-file rules and PCI DSS controls are in place. Until then cards are label and last 4, and each store saves the card itself as an RBI token. CVVs are never stored. |
| Vault use | Scoped and logged. A login is used only on its own site; a card only inside a payment the user said yes to; PAN and date of birth only on bureau, bank and card-issuer hosts. Every use goes to the vault's Activity, and any item can be deleted. |
| Sign-ups | Jev never creates accounts. A sign-up form ends with stop, and the user is told to create the account. |
| Identity | No sign-in in Jev for now. Jev will merge into rivo-companion, which will own users and sign-in. Jev exposes an internal API keyed by user id (§14). |
| Deployment | Planned now, deployed later: AWS ECS on Fargate in ap-south-1 (Mumbai), Postgres (RDS) as the only queue and state, KMS for the vault, Terraform and GitHub Actions (§13). |
| Card on file | Opt-in per store. The payment consent offers "Yes, and save this card with \<store\>". The token lives with the store or bank, never with Jev. Afterwards only the CVV and OTP are asked. |
| Failure before the yes | Retry with backoff and a fresh browser context and IP, then switch to the next-best option and carry on. The switch is named in the consent. |
| Failure after the yes | Never retried. Jev tells the user and offers the next option, which needs a fresh consent. |
| Reach | In-app chat only. A task waiting on the user shows in the chat. |
| Expiring asks | A bank OTP not answered in time: the site's Resend OTP is pressed once and the user asked again. If still unanswered, the task pauses with nothing paid, and resumes when the user says continue. |
| Proactive | Bill-due watches with reminders, and scheduled tasks. Each run asks a fresh consent. No deal alerts. |
| Site changes | No nightly checks. A failed user task drops its route and drafts a site-note fix for our review. |
| Banners | Our own handler rejects optional cookies on common consent banners before the model reads the page. No extensions. |
| Browsers | Our own Chrome pool on an Indian server. One context per task. An Indian residential proxy per context. |
| CDP | Our own thin async client. browser_harness is dropped. |
| Models | gpt-6-luna for every step, at high reasoning as chosen on 14 Oct. It is lowered only if the bench shows no loss. A stuck step goes to gpt-6-astra for up to 3 steps. Routes skip the model where they can. |
| Page reading | browser-use's design: DOMSnapshot, DOM and AX trees, all frames, paint-order occlusion, backendNodeId element numbers, `*` on new elements. Jev's masking stays. |
| Screenshots | On request, or after an action whose effect the page text can't confirm. Masked. Never on a card, OTP or password page, and never after the yes. |
| Tools | browser-use's set plus ask_user. evaluate is read-only and fenced (§6.4). |
| Dialogs | Never auto-accepted. A dialog that commits goes to consent. |
| Captchas | Basic stealth only. Every captcha goes to the user. No solver services, no fingerprint spoofing. |
| Net banking and wallets | Supported. Login ID, password and OTP are asked through the hidden field every time, typed only on the bank's or wallet's own host, and never kept, not even in the vault. |
| UPI | A QR with app links, whose amount and payee are checked against the consent first. Or a collect request to the vault's UPI ID, approved by the user in their app. |
| Analytics | None. Only Jev's own masked traces, kept 30 days. |

## 3. Architecture

```
in-app chat / rivo-companion (later) ── api/ (internal API, chat, asks, vault pages)
                 │
brain (conversation; compare, buy, pay bill, dispute …; picks the best option)
                 │ task
                 ▼
runtime/   tasks (durable) · per-user queue · asks · consent · schedules · watches · pause/resume · recovery
                 │ run
                 ▼
agent/     loop · step · message manager · loop detector · compaction · routes · judge
                 │ actions                     ▲ page state + effects
                 ▼                             │
tools/     registry · actions · ask_user · done(job schema) ── execute(): security → consent → secrets/vault → handler → mask
vault/     per-user encrypted items (KMS envelope) · scopes · activity · card vault service (gated, isolated)
                 │ CDP                         ▲ events
                 ▼                             │
browser/   session = one browser context · event bus · watchdogs (dom, settle, banners, dialogs, downloads,
           security, crash, popups, permissions, screenshot)
dom/       capture (snapshot + DOM + AX, all frames) · visibility · paint order · serializer · hashes · masking
cdp/       async websocket client
pool/      Chrome processes · contexts · proxies · health
```

The money layer (money.py, gate.py, payment records, the 24-hour check, voucher-code masking) sits inside `execute()`
and the job's checks. It is the only code that decides whether money moves.

## 4. Components

### 4.1 cdp/: client

- One websocket per Chrome. Each command has an id and a future; events are routed by sessionId.
- Flattened target sessions, including cross-origin iframes. Each command type has its own timeout.
- Reconnects 3 times (1, 2 and 4 s) before the session is declared lost.
- No daemon, no IPC hop, no control tab.

### 4.2 browser/: session, settle and watchdogs

**`BrowserSession`** is one task:
- It creates a browser context (with the task's proxy) and disposes of it at the end.
- It owns the context's tabs and the focused tab, its download folder and permissions, and an event bus.

**Settle.** This is done better than browser-use, which only sleeps 0.3 s while requests are in flight and has no
DOM-quiet wait. It runs after every action and before every page read:
- **After a navigation:** wait for that navigation's load event, up to 8 s. A timeout is told to the model as "still
  loading after 8 s". (browser-use computes this and never shows it.)
- **Network quiet:** no request in flight for 500 ms, counted from Network events for the context. Ads, analytics,
  media, websockets, long-polls and anything older than 10 s don't count.
- **DOM quiet:** no mutations for 300 ms (MutationObserver). Capped at 3 s.
- **Suggestions:** an autocomplete box waits until its options show (Jev's current rule).
- **Empty page after load:** wait 3 s, read again, reload once, then tell the model the page is empty.

**Watchdogs:**

| Watchdog | What it does |
|---|---|
| dom | Builds the page state on request, always from a fresh read. |
| banners | Recognises common consent managers (OneTrust, Cookiebot, Didomi, Quantcast, TrustArc) and in-house banners by their buttons. It presses reject or "necessary only", or accept only when that is the only choice, before the model reads the page. |
| dialogs | Reads every JS dialog. An alert is dismissed and noted for the model. A confirm or prompt waits for the model's answer. A confirm that pays or submits goes to consent. |
| downloads | Event-driven, with no fixed sleep: CDP download events. The file is attached to the task, and PDFs are kept. |
| security | Checks allowed and blocked hosts before navigation, on redirects and on new tabs. URLs are parsed strictly: userinfo is removed and hostnames normalised. A page opened by typing its address never takes a payment. |
| crash | Notices a crashed target. The tab is reopened once, then recovery takes over (§9). |
| popups | Adopts tabs the page opens. The agent switches to them and says so. |
| permissions | Denies notifications, camera and microphone. Grants geolocation. |
| screenshot | Masked captures (§6.3). |

### 4.3 dom/: page reader

- **Capture:** DOMSnapshot (layout, paint order, styles), the DOM (pierce) and the AX tree, for every frame. That
  includes cross-origin iframes through their own sessions (visible, at least 10 px, depth ≤ 5, ≤ 100 frames) and
  shadow roots, open and closed.
- **Visibility:** an element is kept if its bounds, intersected through every parent frame, fall within the viewport
  plus 1000 px, and it isn't hidden.
- **Occlusion:** a paint-order sweep drops elements fully covered by opaque ones above.
- **Interactive:** decided by tag, role, listeners, tabindex, contenteditable and cursor, with the AX name.
- **For the model:**
  - one line per element, `*[backendNodeId]<role name=… value=… placeholder=… aria-* />`, with text children below;
  - shadow, iframe and scroll containers marked; `*` for new elements;
  - "N pages above, M below"; capped at 40,000 characters.
- **Masking (Jev):**
  - Secret fields show `[secret]`, and password values are never shown.
  - The user's kept details show as `[profile:…]`.
  - After the yes, codes are masked, except rupee amounts and the order id.
- **Hashes for replay:** element hash, stable hash and xpath.

### 4.4 agent/: loop

**Per step:**
1. Prepare the context: page state, the previous actions' effects, nudges (budget at 75 %, replan after 3 failures,
   loop warnings).
2. Run a learned route step if one matches (§4.6).
3. Otherwise make one model call returning
   `{evaluation_previous_goal, memory, next_goal, plan_update, current_plan_item, facts, actions[≤5]}`.
4. Run the actions (`multi_act`). The list ends at:
   - a navigation, search, back or tab switch;
   - any change of URL or focused tab, or new controls appearing;
   - an error, an ask or a consent.
5. Post-process:
   - facts are grounded on the page;
   - history and effects recorded;
   - the loop detector runs;
   - the judge runs on `done`.

**Effects as ground truth.** Every action returns what it changed: controls new or gone, URL, values, dialogs, tabs
and downloads. This is Jev's existing diff. It replaces browser-use's "the screenshot is ground truth" at a fraction of
the tokens. A screenshot is added only when the effects can't confirm the action.

**Our prompt** gives this guidance, in our words:
- clear banners and pop-ups first;
- for autocomplete, type, then pick the suggestion on the next step;
- redo actions cut short by a page change;
- filters before browsing;
- a 403 or bot page goes to recovery, never a blind retry;
- note what was tried, to avoid loops;
- plan by how complex the task is;
- before `done`, check every requirement against the page, with values grounded and never invented.

**Limits.**
- Steps: 40 per task, plus 10 after the yes.
- Time: 180 s per step. Per-action caps: click 15 s, type 60 s, navigate 30 s, select 8 s.
- Model calls: retried up to 5 times with backoff and jitter. On a rate limit or 5xx, astra takes the step.
- An empty answer is retried once, then counts as a failure.
- 5 failures in a row hand over to recovery (§9), not to a forced `done`.

**Loop detector.** Normalised actions are hashed over a window of 20, and pages fingerprinted. Nudges come at 5, 8
and 12 repeats, and after 5 steps on an unchanged page. A third nudge hands over to recovery.

**Message manager.**
- One system prompt plus one rebuilt state message: request, history, plan, todo, secret placeholders, browser state.
- History above 40,000 characters is summarised into 6,000 characters or fewer, keeping the last 6 steps, with secrets
  filtered out.

**done** returns the job's schema with an explicit `status` (done, failed, blocked or needs_user) and is never
defaulted. (browser-use hides `success` in structured mode, so it always reads true.)

**Judge.**
- After `done`, the deterministic proof comes first: an order id or reference on a page after the yes, or an
  acknowledgement number for a submission.
- Then a strict judge call, on text history only, that must agree. It doubts self-reported success, and a captcha,
  invented values or an early done make it fail.
- If it disagrees, the loop gets one more round with the judge's reason. If it still disagrees, the result is
  "unclear", and the user is told what is known.
- (browser-use's judge is advice only.)

### 4.5 tools/: registry and actions

**Registry.** `@action(description, params=Model, domains=…, ends_list=bool, commits=bool)`. The model's action union
is generated per page domain. Parameters are validated, and dependencies injected by name.

**Mechanics** (browser-use's, with our fixes):

- **click**
  - `<select>` is refused and its options returned instead. A file input is refused; use upload.
  - Scroll into view, then geometry: quads, then box model, then rect.
  - Hit-test at the point: it passes on the target, an ancestor or descendant, or its label.
  - CDP mouse move, press and release, with short human delays.
  - A checkbox or radio is checked to have toggled, with a script click as the fallback.
  - A covered control gets a script click, **except a commit control**: a covered pay or submit button is never
    script-clicked. The model is told what covers it.
  - A new tab is switched to and announced. A download is tracked.
- **input**
  - Focus (DOM.focus, else a click), then clear: select, the native value setter, then input and change events, with
    fallbacks.
  - Key events per character with real key codes.
  - Date, time, range and jQuery or Bootstrap datepickers are set through the native setter with their events.
  - In a contenteditable, a dropped first character is retyped.
  - Non-secret values are read back, and a reformatted value is reported. Secrets are checked as non-empty only.
  - An autocomplete box gets a hint to wait for its suggestions.
- **dropdown_options / select_dropdown**
  - Native select, ARIA listbox (including options elsewhere, through aria-controls or aria-owns, which browser-use
    can't do), menus.
  - Exact, case-insensitive match. On a miss, the available options are listed.
- **navigate, search, go_back** (all end the list); **scroll, send_keys, upload, wait**.
- **switch_tab, close_tab:** tabs shown as "Tab ab12: url — title".
- **extract:**
  - The fresh DOM is converted to markdown in our own code, chunked at 100,000 characters with overlap and table
    headers carried over.
  - Facts come out in the job's schema, through a cheap model, with the output masked.
- **search_page, find_elements:** free and instant. They also search inside iframes, which browser-use doesn't.
- **file area:** write, read, replace, with todo.md for long tasks.
- **evaluate:** read-only and fenced (§6.4).
- **ask_user:** §5.
- **done.**

**Every action runs through `execute(action)`:**
1. the security re-check;
2. consent, if the action commits;
3. secret substitution;
4. the handler;
5. masking of the result.

**Error texts** go back to the model as information. Examples:
- "Element N is no longer on the page; read it again." (A stale element doesn't count as a failure.)
- "Couldn't click N: covered by \<label\>."
- "Site unavailable: \<url\>."
- "The value shows as '…', not '…'."

### 4.6 Routes: record and replay

**Record.** On success, the steps up to the yes are saved per store or biller (memory `routes`). Each step keeps its
action, its element's lookups (element hash, stable hash, xpath, role and accessible name, unique name/id/aria-label)
and its text as a placeholder or variable (`{brand}`, `{amount}`, `{account}`, `[profile:phone]`, `[ask]`).

These steps are marked `think` and never replayed:
- an amount;
- a step on a page showing the cart, payable, fee or bill;
- the yes, and everything after it.

**Replay.**
- Before each model call, the route's next step (or one of the 2 after it) is looked up through the five lookups in
  turn.
- A single match runs through `execute` with no model call. Otherwise the model takes the step, and the route resumes
  after the step the model actually took.

**Learning.**
- A newer success replaces the route.
- A failure before the yes in a run that used the route drops it.

### 4.7 runtime/: tasks, asks and schedules

**Task record** (Postgres, per user):
- goal and job;
- state: queued, running, waiting_user, paused, done, failed, unclear or cancelled;
- checkpoint: plan, facts, memory summary, route position, the chosen option and its alternatives, consents, and the
  payment row;
- timestamps.

No secrets are stored.

**Concurrency:** one money task per user at a time, with that user's other tasks queued; 20 tasks at once overall.

**Waiting on the user:**
- An ask puts the task in waiting_user. The browser context stays alive until the ask expires (§5).
- An expired ask pauses the task: the context is disposed of and nothing is paid.
- "Continue" restarts it from the start, reusing the checkpoint (the choice, facts and route), so it goes quickly.
- A consent is always asked again.

**Restart or crash:**
- A task running before the yes is re-queued and starts again.
- A task after the yes is marked unclear, and the user is told. It is never repeated. Jev offers "check my order",
  which is a read-only task on the store's order history.

**Progress.** A task silent for 3 minutes posts one line in the chat about what it is doing.

**Schedules.**
- The user creates them ("pay my electricity bill on the 5th every month", "buy ₹2,000 of Amazon vouchers monthly").
- Each run is an ordinary task with a fresh consent, waiting in the chat if the user isn't there.
- A run missed while the server was down runs once if less than 12 hours late.

**Bill-due watches.**
- Each saved biller's bill is read, read-only, weekly and daily in the 5 days before it is due.
- A new bill or a nearing due date posts one chat message with "Pay now". Alerts are de-duplicated.

### 4.8 pool/: Chrome pool

- N Chrome processes on an Indian server, up to 10 live contexts each.
- Indian residential proxy per context. A blocked context gets a new IP on retry.
- A health check restarts a crashed Chrome, and its tasks go to recovery.
- Locally: one Chrome, same interface.

### 4.9 vault/: the user's vault

§7 covers the items, encryption, scopes, saving and the card vault. The component:
- `vault_items` and `vault_activity` tables;
- the KMS envelope;
- `use(item, task, host)`, which checks the scope, decrypts, returns the value to the typing code only, and logs;
- the vault pages: Logins, Cards, Personal info, Activity.

### 4.10 Learning from failures

A task that fails or ends unclear:
- drops the route it used;
- runs one reflection call on the masked trace, drafting site-note lines (learn.py's checks).

Drafts go to a review queue for us, never straight into the notes, because a page could plant instructions. Routes
learn from successes by themselves.

## 5. Asking the user

`ask_user(kind, why, field?, host?)`. Only these kinds exist. The agent and the code can't ask anything else.

| Kind | When | What the user sees | Expires |
|---|---|---|---|
| `secret` | OTP, CVV, net-banking or wallet login, a site password not in the vault, a card number or expiry not in the vault | Hidden field in the in-app chat ("Your bank sent an OTP for ₹250 Swiggy on SmartBuy"). A site password, and a card when the card vault is on, have "Save to vault", ticked by default | OTP: the site's limit (about 3 min), Resend pressed once, then pause. Others: 10 min, then pause |
| `captcha` | A captcha on the page | The captcha's picture, sized to read on a phone | Image refreshed once, then pause |
| `consent` | §6.1 | The code-written summary, with Yes, "Yes, and save card with \<store\>", "Use another option" and No | 10 min, then pause (nothing paid) |
| `qr` | UPI QR after the yes | The QR with app links, plus I paid / I didn't pay | The QR's own expiry |
| `detail` | A personal detail not in the user's vault | A plain question. The answer is saved to the vault's Personal info, shown there, and deletable | 30 min, then pause |
| `clarify` | The request matches several saved targets, or none | A choice | 30 min, then pause |

**Secrets** (answers to `secret` asks, not yet in the vault):
- Held in a per-task store in memory only, scoped to the host they were asked for.
- An answer saved to the vault goes in encrypted (§7). Every other answer is wiped after typing.
- A gateway's bank page counts as that host only when the payment redirected there, with the host parsed strictly.
- Typed once, then wiped, and wiped in any case when the task ends.
- The model sees only `<secret>otp1</secret>`-style placeholders.

**Code-level backstops:**
- Typing a literal into a secret field is refused.
- A secret field still empty triggers its ask by itself.
- Card details never go into a code box.
- Codes have their own try limits, with fresh tries for a bank OTP.
- The expiry is asked as MM/YY and typed in the form its box takes.

## 6. Money and consent safety

### 6.1 Consent

**Classes:**
- **pay:** payments, including the card-on-file option.
- **submit:** a bureau dispute, a card application.
- **cancel:** a mandate cancellation.
- **accept:** a consent or terms on the user's behalf beyond the store's own checkout terms, such as a bureau pull or
  account terms.

**Before a commit.** `execute()` asks the gate whether an action commits (gate.commit_label, extended to the four
classes). If it does, code writes the summary from the job and the page:
- what is bought, paid or submitted, and to whom;
- the amount, including any fee, and how it is paid;
- the saving against the next option;
- the terms ticked to complete it;
- any switch made ("SmartBuy was down, so this is Woohoo").

The job's checks run first:
- the cart is exactly the order;
- the payable is no more than the amount plus any fee shown;
- no unsettled purchase or bill in the last 24 hours;
- the page wasn't reached by typing its address.

**Binding.**
- A consent is bound to a hash of class, payee, amount, items and method. Any change asks again.
- The bank OTP page and the gateway steps after the yes ride on it.
- Money jobs never use standing or pre-approval.
- Each consent is audited: user, time, summary hash, answer. No secrets.

### 6.2 Secrets

- Placeholders only, put in at execution time on their own host.
- Literal values are redacted from prompts, history, summaries, the judge, traces, `extract` output and tool results.

### 6.3 Screenshots

- Taken on request, or when an action's effects can't confirm it.
- Every filled field and every kept detail is masked.
- Refused on a page with any card, OTP or password box, and after the yes.

### 6.4 evaluate

- Runs in an isolated world, and its result is masked.
- Refused on a page with a secret box, on payment and bank hosts, in a money job from the cart onwards, and after the
  yes.
- A script that clicks, submits, navigates or sets values is refused. This is checked before it runs and by comparing
  the page before and after; a change stops a money job.

## 7. The vault and data

### 7.1 What the vault holds

| Section | Items | Typed where | Shown as |
|---|---|---|---|
| Logins | Username and password for a site: voucher stores, billers, bureaus, card issuers' portals. Never net banking or wallets | Only on that login's own host (strict host match) | "smartbuy.hdfc.bank.in · 98•••••953" |
| Cards | Label, network, issuer, last 4, and the stores where the card is saved by the store. Number and expiry only behind the card vault's launch gate (§7.4) | Card boxes inside a payment the user said yes to | "HDFC Mastercard ending 0185" |
| Personal info | Name, phone, email, addresses, PAN, date of birth, UPI ID, biller account numbers (per biller) | Matching fields. PAN and date of birth only on bureau, bank and card-issuer hosts. A biller's number only on that biller's host | Masked: "PAN ABC••••4F", "BESCOM ••7890" |
| Activity | Every use: item, task, host, time, outcome | n/a | A list the user can read |

**Never in the vault, nor anywhere else:** OTPs, CVVs, UPI PINs, net-banking and wallet logins, Aadhaar numbers.

**No agent items:** Jev never creates accounts (§2).

### 7.2 Encryption

- **Envelope encryption** with AWS KMS. Each user has one data key, generated by KMS under the vault's customer-managed
  key and stored only in its wrapped form.
- **Item values** are encrypted with AES-256-GCM under that data key. The item id and user id are the associated data,
  so a value can't be moved to another user or item.
- **Who can decrypt:**
  - Only the browser worker's role may decrypt a data key, and only for the user whose task it is running. KMS
    encryption context: `user_id`.
  - The API's role can only encrypt (to save items).
- **Plaintext lives only in the worker's memory** for the moment it is typed, then is wiped. It is never sent to a
  model, logged or traced. The model sees `[vault:login.smartbuy]`-style placeholders.
- **Deleting an item** destroys its ciphertext. Deleting a user's vault schedules their data key for deletion
  (crypto-shredding).
- **Locally**, KMS is replaced by a key from the environment (today's JEV_MEMORY_KEY), with the same interface.
- Today's `ids` table (one Fernet key for the whole app) moves into the vault.

### 7.3 How items get in and are used

**Getting in:**
- **Personal info** comes from `detail` asks, saved automatically and shown in the vault.
- **Logins** come from `secret` asks for a site password, with "Save to vault" ticked by default. Also from the vault
  page's "+".
- **Cards:**
  - With the gate off, a card is added by label and last 4 on the vault page, or after a purchase.
  - With the gate on, the card's number and expiry are also saved through "Save to vault" on the card ask.

**Use** (`vault.use(item, task, host)`):
- The item's scope (§7.1) is checked against the task and the page's strictly parsed host.
- A card needs the task's consent id for the payment.
- The value is decrypted, typed, and logged to Activity.
- Use outside its scope is refused and logged.

**On the vault page** the user can add, replace or delete items. Values are never revealed, only masked.

### 7.4 The card vault (built now, switched on later)

- **A separate service and database schema:** its own KMS key, its own task role, and network isolation (only the
  browser workers can reach it).
- **The only call:** `card(user, card_id, consent_id)`. It returns the number and expiry only for a live payment
  consent of that user, once per consent.
- **Feature flag `CARD_VAULT`:** off in production until both are in place:
  1. A legal opinion that Jev may store card data under RBI's card-on-file rules (since Oct 2022, only issuers and
     networks may store card data).
  2. PCI DSS controls for the service: scope assessment, key management, logging, audits.
- **CVVs are never stored,** under PCI DSS 3.3.1.
- **With the flag off,** stores save cards as RBI tokens (the opt-in in the consent). After that, only CVV and OTP are
  asked.

### 7.5 Other data

- Traces are masked and kept 30 days.
- Browser contexts and their cookies are deleted at the end of each task.
- Card-on-file tokens live with the store or bank.
- Routes hold no user data.
- Audit rows (consents, vault uses) hold ids, hashes and times, never values.

## 8. Data flows

**"Get me ₹250 of Swiggy vouchers":**
1. The brain compares stores in parallel (≤ 60 s) and picks SmartBuy + Regalia Gold (₹234.50 effective).
2. A task starts: route for the brand page and sign-in, `[profile:phone]`, sign-in OTP asked (`secret`).
3. The model picks ₹250 and reads the cart; the cart check passes.
4. The card form. If SmartBuy has the card saved, only the CVV is asked. Otherwise card number, expiry and CVV are asked in one turn (`secret` ×3; number and expiry come from the card vault once it is switched on), typed key by key.
5. PROCEED is a commit, so a `consent` is asked: "Buy ₹250 Swiggy on SmartBuy with Regalia Gold · ₹15.50 back in points
   (best; next Woohoo UPI ₹242.50) · Yes / Yes and save card / Use another option / No".
6. On yes: click, then the bank OTP (`secret`), then SUBMIT riding on the yes.
7. Order page: proof plus judge, then "Bought … Order HSB…". The record is marked paid and the route saved.

**SmartBuy down at step 2:** retry twice, then a new context and IP. Still down, so switch to Woohoo + UPI. The consent
says "SmartBuy wasn't loading, so this is Woohoo with UPI (3% off, code WOOHOO)".

**Scheduled bill:**
1. On the 5th, a task reads the BESCOM bill. Its captcha is asked, and the task waits in the chat.
2. The user opens the app and types it. Consent "Pay ₹1,240 to BESCOM · UPI"; on yes, the QR is shown; I paid, then the
   receipt and reference.

## 9. Recovery

| Failure | What Jev does by itself |
|---|---|
| Transient: timeout, 5xx, network error, crash, empty page | Retry the step twice, reload, then a new context and IP, restarting from the route (≤ 2 restarts). |
| Blocked: 403, bot page, 3 captchas in a row | A new context and IP once. Then the next-best option (vouchers), or a retry in 1 hour with a chat line (bills). |
| Unavailable: out of stock, limit reached, brand not sold, store down for maintenance | The next-best option, named in the consent. |
| Bad user detail: invalid account number, login refused | `detail` ask once. The kept value is corrected. |
| Agent stuck: loop detector or 5 failures | gpt-6-astra for 3 steps. Then one fresh restart with the failure noted. Then the next-best option. Then tell the user what was tried. |
| After the yes: declined | Recorded as failed. "Not paid", with the next option offered as one tap and a fresh consent. |
| After the yes: unclear | Recorded as unclear. Told to check SMS and email, with "check my order" offered. Never repeated. |

A switch happens only before the yes, and never changes what the user asked for (brand, amount, biller).

## 10. Errors the user sees

Only:
- the asks (§5);
- the result;
- "paused, say continue";
- recovery outcomes that need them (§9).

No internal errors, stack traces or "I'm stuck" questions.

## 11. Stages

Each stage gets its own spec, plan and build. Every gate includes all unit tests and headless-Chrome replica tests.

| # | Stage | Delivers | Gate |
|---|---|---|---|
| 1 | Browser core | cdp/, browser/ (contexts, settle, watchdogs incl. banners and dialogs), dom/ reader with masking and hashes. A CLI that prints a page's state. | Every replica fixture and 10 live store and biller pages read correctly. No secret in any output. Settle ends the wait and look turns on the replica flows. |
| 2 | Agent loop and tools | agent/ loop, effects, message manager, loop detector, judge, done schema; tools/ with the mechanics in §4.5; `execute()` with consent and secrets; ask_user. | Replica purchase and bill flows, plus the safety set: fee, leftover cart, injected page, declined card, covered pay button, confirm dialog. Ask-count tests pass. |
| 3 | Money jobs switch | Vouchers (auto-pick, parallel compare, every way to pay, card-on-file opt-in), bills (incl. net banking through hidden asks) on the new agent. The actor loop is deleted. | Replica targets met. One real ₹50–₹250 purchase per store changed, and one real bill. Live read bench ≥ 90 %. |
| 4 | Runtime and vault | Durable tasks, in-app asks with expiry and resend, pause and continue, restart rules, recovery (§9), schedules and bill-due watches, progress lines. The vault: Logins, Cards (label and last 4), Personal info, Activity; envelope encryption with a local key; scoped use; vault pages; today's `ids` moved in. | Pause and continue, restart before and after the yes, recovery switches, and schedules all tested on replicas. Vault scope tests pass. No vault value in any payload, log or trace. A live scheduled bill read. |
| 5 | Replay | Five-lookup routes with variables, think steps and resume. | A second replica purchase uses ≤ 50 % of the first's model turns. Live: SmartBuy with a route ≤ 60 s agent time. |
| 6 | ECS and many users | The §13 plan built and deployed: Terraform, images, Fargate services, RDS, KMS envelope, Postgres queue with leases, CI/CD, observability. The Chrome pool (one Chrome per worker, contexts, proxies), a per-user data partition, and the internal API for rivo-companion (§14). | Staging: 20 concurrent replica purchases with no cross-user leakage; live read bench from staging ≥ 90 %; a rolling deploy in the middle of tasks loses none; a restore drill. |
| 7 | Other use cases | Bureau disputes, mandates, card applications on the new agent, with submit, cancel and accept consents. The planner and picker loop, snapshot.js and browser_harness are deleted. | Each use case's bench passes. One real run each, with the user. |
| 8 | Card vault (gated) | The isolated card-vault service, behind the `CARD_VAULT` flag (§7.4). | Built and tested. Switched on in production only after the legal opinion and the PCI DSS assessment. |

## 12. Testing

- **Units:**
  - the CDP client against a fake socket;
  - settle against scripted network and DOM activity;
  - the serialiser against recorded snapshots;
  - lookups against fixture pages;
  - `execute()` guards;
  - consent binding;
  - redaction everywhere a string goes;
  - the ask whitelist.
- **Replica tests:** headless Chrome on the voucher, bill, OTP and gateway fixtures, with the model scripted. New
  fixtures:
  - cross-origin card iframe;
  - shadow DOM;
  - covered pay button;
  - confirm dialog;
  - consent banner;
  - slow XHR page;
  - 500 and 403 pages;
  - out-of-stock page;
  - net-banking login.
- **Ask-count assertions:** every replica flow asserts its exact ask sequence (for example: sign-in OTP, card ×3,
  consent, bank OTP) and nothing more.
- **Data scans:** no secret or vault value in the database (outside vault ciphertext), traces, logs or model payloads
  after any test, using the same matcher the model calls use.
- **Vault tests:**
  - A login is used only on its own host.
  - A card is used only with a live consent.
  - PAN is used only on bureau, bank and issuer hosts.
  - Ciphertext moved to another user or item fails to decrypt.
  - A deleted item is gone.
  - Every use is in Activity.
- **Deploy drills** (stage 6):
  - a rolling deploy in the middle of tasks;
  - a worker killed while a task waits on the user;
  - an RDS failover;
  - a restore from point-in-time.
- **Live bench:** read-only, every store × 5 brands, and 5 billers' bill reads.
- **Live purchases:** one per store changed, by the user, from stage 3.

## 13. Deployment on AWS ECS (planned now, deployed later)

### 13.1 Shape

- **Region and data:** AWS ap-south-1 (Mumbai), two availability zones. Data stays in India.
- **No Redis and no SQS:** Postgres is the only queue and state.

| Service (ECS Fargate) | Runs | Size per task | Scaling |
|---|---|---|---|
| `api` | Internal API, the in-app chat and vault pages, the brain (conversation and its tools), ask and consent endpoints | 1 vCPU, 2 GB | 2 or more, on CPU |
| `scheduler` | Schedules, bill-due watches, recovery timers, missed runs | 0.5 vCPU, 1 GB | 1 active (Postgres advisory lock), 1 standby |
| `worker` | The agent and one headful Chrome (in a virtual display) with up to 5 task contexts. Claims tasks | 4 vCPU, 16 GB | 4 at launch (20 tasks); scales on queued tasks per worker |
| `cardvault` | The card vault (§7.4), flag off in production | 0.5 vCPU, 1 GB | 2, isolated security group |

- **Data:**
  - RDS PostgreSQL, Multi-AZ, encrypted, backups with point-in-time restore.
  - S3 for masked traces and downloaded files: SSE-KMS, 30-day lifecycle, no public access.
- **Keys:**
  - KMS customer-managed keys for the vault, the card vault, and data at rest (RDS, S3, logs).
  - Secrets Manager for the model API key, proxy credentials and database credentials, with rotation.
- **Network:**
  - VPC with public subnets (ALB, NAT) and private subnets (services, RDS).
  - VPC endpoints for ECR, S3, KMS, Secrets Manager and CloudWatch Logs.
  - Model calls go out through NAT. Browser traffic goes through the residential proxy provider.
  - Workers may only make outbound 443 connections.
  - Only `api` sits behind the ALB, with ACM TLS and WAF (managed rules, rate limits). Its internal routes are open
    only to rivo-companion's service token.

### 13.2 Coordination through Postgres

- **Claiming:** `tasks` are claimed by workers with `SELECT … FOR UPDATE SKIP LOCKED`.
- **Lease:** the worker's id and a heartbeat every 10 s. Three missed heartbeats end the lease, and the restart rules
  apply (§4.7): before the yes, re-queued; after the yes, unclear and the user told.
- **Waking:** `LISTEN/NOTIFY` wakes services (`task_ready`, `answer:<worker>`), with polling as a backstop.
- **Answers to secret asks:**
  - Written to `answers` encrypted under the user's data key, with a 10-minute TTL.
  - The worker holding the task's lease is notified, reads the row, deletes it, and holds the value only in memory.
- **Per-user order:** one money task per user at a time (§4.7), enforced in the claim query.

### 13.3 Deploys

- **Rolling:** minimum 100 % healthy, maximum 200 %. New workers start before old ones stop.
- **On SIGTERM a worker:**
  - stops claiming;
  - checkpoints and re-queues tasks before the yes, which restart quickly elsewhere with their routes;
  - gives tasks after the yes the remaining stop timeout (Fargate allows up to 120 s) to finish, after which they are
    marked unclear and the user is told.
- **Migrations** run as a one-off ECS task before the services roll, and stay backward-compatible for one release.

### 13.4 Images

- **`api`, `scheduler`, `cardvault`:** Python slim.
- **`worker`:** Debian, Google Chrome stable, Xvfb, Indic fonts and the app.
- **All of them:** run as non-root, with a read-only root filesystem except /tmp, `--disable-dev-shm-usage` for
  Chrome, and ECR image scanning.

### 13.5 Observability and security

- **Logs:** structured JSON in CloudWatch Logs, masked: no secret, vault value or OTP is ever logged.
- **Metrics:**
  - tasks started, finished and failed, by job and failure class;
  - asks by kind;
  - agent time; model spend;
  - queue depth and wait; worker load;
  - vault uses and refusals.
- **Alarms:** completion below 95 % over an hour, queue wait over 2 min, worker crashes, KMS decrypt errors, error
  rates.
- **Traces:** kept in S3 and linked from the task record.
- **Security:**
  - Least-privilege IAM per service: only `worker` may decrypt vault keys, and only with the task's `user_id` as the
    encryption context; `api` may only encrypt.
  - CloudTrail, including every KMS use, plus GuardDuty and Security Hub.
  - No SSH. ECS Exec is off in production.

### 13.6 Environments and delivery

- **Environments:** staging and production in separate AWS accounts, built from the same Terraform modules: network,
  ecs, services, rds, kms, s3, alb-waf, iam, observability, ecr.
- **GitHub Actions:**
  - On a PR: unit and browser tests.
  - On main:
    1. build images and push them to ECR, tagged with the commit;
    2. deploy to staging, then run the replica suite and a live read-bench smoke test;
    3. manual approval;
    4. production rolling deploy.
- **Recovery:** RDS point-in-time restore (RPO 5 min), Terraform rebuild (RTO 1 h), S3 versioning.
- **Cost drivers to watch:** worker Fargate hours, residential proxy traffic, model tokens, NAT data, RDS.

## 14. rivo-companion integration (later)

Jev will run as a service. rivo-companion owns users, sign-in and the main app UI, and calls Jev's internal API with a
service token and the user's id.

| Endpoint | Does |
|---|---|
| `POST /v1/tasks` | `{user_id, request}` → a task id |
| `GET /v1/tasks/{id}` | State, result and progress |
| `GET /v1/users/{id}/events` | Server-sent events: progress lines, asks, results |
| `POST /v1/asks/{id}/answer` | Answers to non-secret asks (consent, clarify, detail) |
| `POST /v1/secure/asks/{id}` | Secret answers, posted by the client straight to Jev with the ask's one-time token. They never pass through rivo-companion's backend |
| `GET/POST/DELETE /v1/users/{id}/vault/items` | Vault items, masked on read |
| `GET/POST/DELETE /v1/users/{id}/schedules`, `…/watches` | Schedules and bill-due watches |

Until then, Jev's own web chat runs with one development user, no sign-in, and is never exposed publicly.

## 15. Reference: browser-use (architecture only, no code)

From two code studies of commit c75e847 (7 Oct 2026) and the web research.

**Taken:**
- event bus and watchdogs; CDP only;
- DOMSnapshot, DOM and AX capture with paint-order occlusion; backendNodeId element numbers with `*`;
- the step output (memory, goals, plan, ≤ 5 actions); `multi_act`'s list endings;
- the click chain with hit-test and script fallback; per-character typing with clearing, read-back and the datepicker
  setter;
- the dropdown tools; new-tab auto-switch; the error texts;
- model-call retries and fallback;
- loop detector; compaction; the action registry;
- domain-scoped secret placeholders;
- extract with chunking; search_page and find_elements;
- the five lookups for replay; the planning and pre-done guidance (in our words).

**Done better than browser-use:**

| browser-use | Jev |
|---|---|
| Sleeps 0.3 s while requests are in flight; its network-idle settings are unused | Settle waits for network and DOM quiet |
| Navigation timeouts never shown to the model | Shown |
| Structured `done` hides `success`, so it always reads true | Explicit status |
| Judge is advice only | Judge decides (with proof) |
| Dialogs auto-accepted | Never auto-accepted |
| Screenshots unmasked every step | Masked, and only when needed |
| Secrets leak through extract, tool results and TOTP | Redacted |
| evaluate unrestricted | Fenced |
| Can't select ARIA options outside the combobox | Can |
| search_page and find_elements top document only | Also inside iframes |
| extract can read a stale DOM | Always a fresh read |
| Crash watchdog disabled | Enabled |
| One Chrome process per session | One context per task |
| Covered buttons always script-clicked | Never for a commit control |
| Allow-list bypass from loose URL parsing (CVE-2025-47241) | Strict parsing |
| PostHog analytics on by default | None |
| workflow-use's fallback to the agent disabled | Routes resume with the model |
| Fixed 0.5 s wait for a download after every click | Event-driven |

**Not taken:**
- LLM adapters, MCP, cloud skills, sandbox, the beta Rust agent, GIF and HAR recording;
- the about:blank keep-alive tab (the pool keeps Chrome alive);
- third-party extensions;
- coordinate clicks keyed to model names.

**Measured caution:** human-graded, early-2025 browser-use with GPT-4o succeeded on about 30 % of live tasks
(Online-Mind2Web). Jev's reliability has to come from settle, effects, recovery, consent and routes, and is proven on
our own bench, not on browser-use's numbers.
