# Jev Assistant — design

Date: 2026-09-23 · Status: approved in brainstorming, awaiting spec review

## 1. What we are building

A chat assistant that completes real tasks on real websites for its user. The user states a goal ("order Diet Coke on Instamart", "book an Uber to the airport", "raise a dispute for this card charge"). A hidden Chrome performs the task. The assistant asks the user only for what it cannot know or must not decide alone: OTPs, choices between materially different options, missing personal data, and one explicit approval before any irreversible commitment. It then reports the outcome with evidence from the page.

The product shape follows Instinct (a WhatsApp assistant). v1 is a local web chat whose architecture mirrors the WhatsApp Cloud API, so a WhatsApp channel later replaces one module.

Speed and cost come from the jev-ultrafast approach: TypeSafe's **Jev** classifier chooses an operation and its target from an indexed table of observed elements in one request per step. A small LLM (**GPT-4.1 mini**) writes text only when text is needed.

## 2. Terms

| Term | Meaning |
|---|---|
| Jev | TypeSafe `systemone` classifier (`jev-latest`). Answers `choice` questions with a probability per option. Makes every decision. |
| Text model | GPT-4.1 mini via the OpenAI chat-completions API, JSON output. Writes field values, questions, summaries, URLs. Reads images for LOOK and user screenshots. |
| Task | One user goal running in its own browser tab and thread. |
| Commit | An irreversible action: pay, place order, buy, book, request a ride, cancel, delete, submit to an organisation, send a message, change account settings. |
| Channel | The chat transport. v1: local web. Later: WhatsApp. |
| Agent Chrome | A dedicated, hidden, headed Chrome instance with its own profile at `~/.jev/chrome`. |

## 3. Decisions

| Topic | Decision |
|---|---|
| v1 purpose | Local prototype on the user's Mac. Cloud hosting is a later, separate spec. |
| Coverage | Generic. No site-specific scripts, plans, or field values. Every interaction the browser supports is an operation. |
| Done means | Live smoke set completes for real (Section 16), including payment. |
| Payments | Stored card vault in macOS Keychain; bank 3DS OTP always asked. Pay-on-delivery and UPI flows work through the same generic operations. |
| Chrome | Dedicated profile, headed, window hidden. Not headless (headless received HTTP 403s). |
| User surface | Chat only. The user never sees or drives the agent's browser. |
| Commit gate | Exactly one approval before each commit, detected by Jev and by a code backstop. |
| Open choices | Use saved preferences; otherwise ask with options; offer "Remember this". |
| OTP source | Asked in chat. |
| Core loop | Jev decides every step; the text model talks. |
| Mid-task messages | Jev classifies each message. New tasks run in parallel; one question open in chat at a time. |
| Code home | New repo `~/Documents/jev-assistant`. Core copied from jev-ultrafast (MIT, attribution kept) and owned here. |
| Channel shape | WhatsApp Cloud API shape (webhook in, send-message out, media ids, interactive limits). |

## 4. Non-goals for v1

- WhatsApp integration, cloud hosting, multiple end users, persistent tasks across server restarts.
- Solving captchas automatically. Captchas are shown to the user as an image question.
- Device-bound authentication (passkeys, biometrics, app push approvals). The assistant asks the user to act on their own device or reports it cannot continue.
- Anything outside the browser.

## 5. Repository and runtime

```
jev-assistant/
  assistant/
    agent.py          task loop, ask/confirm/help ladder, commit gate, attempt limits, runner threads, question queue
    model.py          Jev requests (step decisions, message intent) and text-model jobs
    questions.py      every Jev rule text and text-model prompt
    browser.py        agent Chrome connection, execution, frames, pop-ups, event pump
    snapshot.js       in-page observation
    profile.py        profile.json, Keychain vault, redaction registry
    server.py         loopback HTTP server, CLI entry points
    channel_web.py    WhatsApp-shaped web channel: webhook handler, send(), media()
    static/           chat UI (index.html, app.js, style.css)
    fixtures/         offline test sites
  tests/
  docs/
  .env.example
  pyproject.toml
  LICENSE             MIT, with jev-ultrafast attribution
```

Dependencies: `browser-harness==0.1.13`, `httpx[http2]`. Dev: `pytest`, `ruff`. Python ≥ 3.12, managed with `uv`. No other runtime dependency; Keychain access uses the built-in `security` CLI.

Environment (`.env`, git-ignored):

```
TYPESAFE_API_KEY=
TYPESAFE_MODEL=jev-latest
OPENAI_API_KEY=
TEXT_MODEL=gpt-4.1-mini
TEXT_MODEL_BASE_URL=https://api.openai.com/v1
BU_CDP_URL=http://127.0.0.1:9333
BH_TELEMETRY=0
JEV_DEBUG=0
```

The copied `field_text` currently sends a `reasoning` request parameter (`jev_ultrafast/model.py:166-168`) that the OpenAI API rejects. The text-model client sends only parameters OpenAI accepts: `model`, `messages`, `response_format={"type":"json_object"}`, `max_tokens`.

Commands:

| Command | Action |
|---|---|
| `jev chrome` | Launch agent Chrome: `open -gj -na "Google Chrome" --args --user-data-dir=$HOME/.jev/chrome --remote-debugging-port=9333 --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-background-timer-throttling` |
| `jev probe [urls…]` | Open each URL in agent Chrome and report `ok`, `http-403`, or `captcha` from the main-document status and page text. Default URLs: the smoke-set sites. |
| `jev` | Start the chat server at `http://127.0.0.1:8766`. |

Browser Harness connects to agent Chrome through `BU_CDP_URL`. Chrome 136+ refuses remote debugging on the default profile, so the dedicated profile is also a requirement. Existing focus emulation from jev-ultrafast stays enabled per tab.

## 6. The step loop

Each task repeats: observe → build action space → one Jev request → execute the chosen operation → log → observe.

### 6.1 Observation (`snapshot.js`)

One `Runtime.evaluate` per frame returns visible text (up to 6,000 characters), candidate elements, freshness guards, and scroll state. Each real DOM node receives a code-owned integer id through a `WeakMap`; the live reference stays in a page-side `Map`. The model never sees or produces selectors or coordinates.

Candidates, in addition to jev-ultrafast's semantic elements and ARIA roles:

- Roles `slider`, `menu`, `menuitem`, `menuitemcheckbox`, `treeitem`, `listbox`, `tab`.
- Clickable non-semantic elements: computed `cursor: pointer` where the parent's cursor is not `pointer`, an `onclick` attribute or property, or `tabindex >= 0`; skipped when inside an already-collected interactive ancestor.
- `type=password` inputs as TYPE_TEXT targets. Their values are never read.
- File inputs, including hidden ones, as UPLOAD targets.
- Elements with `draggable=true` as DRAG sources. Drop handlers cannot be detected from the DOM, so every other candidate element and every scrollable container is a valid DRAG target.
- Elements with `aria-haspopup` or `aria-expanded` as HOVER targets.
- Scrollable containers: `overflow` of `auto`/`scroll` with scroll size larger than client size, per direction.
- OTP groups: adjacent inputs with `maxlength=1` sharing a parent, reported as one TYPE_TEXT target with `group_size`.
- Field constraints: `maxlength`, `inputmode`, `pattern`, `type`.
- Open shadow roots are traversed. Same-origin iframes are traversed through `contentDocument` with coordinates offset by the iframe's rect. Cross-origin iframes are observed through their own CDP sessions (Section 13.2).

Limits stay as in jev-ultrafast: 250 candidates per observation, only in-viewport elements, visible text only.

### 6.2 Action space

As in jev-ultrafast `action_space`: one index per node; each operation has its own set of valid targets; an operation is offered only when it has at least one valid target. Always-available operations are listed in 6.3.

### 6.3 Operations

| Operation | Jev target question(s) | Execution (CDP) | Offered when |
|---|---|---|---|
| CLICK | `click_target` | mouse press/release at current centre after occlusion check | clickable targets exist |
| DOUBLE_CLICK | `double_click_target` | same, `clickCount=2` | clickable targets exist |
| RIGHT_CLICK | `right_click_target` | same, `button=right` | clickable targets exist |
| HOVER | `hover_target` | `mouseMoved` to centre | hover targets exist |
| DRAG | `drag_source`, `drag_target` | press at source, 10 intermediate moves, release at target; for HTML5 drag-and-drop `Input.setInterceptDrags` + `Input.dispatchDragEvent` | draggable sources exist |
| TYPE_TEXT | `type_text_target` | select-all + `Input.insertText`; OTP groups typed one key event per box | editable targets exist |
| PRESS_KEY | `press_key_target`, `press_key_key` ∈ Enter, Escape, Tab, Shift+Tab, ArrowUp, ArrowDown, ArrowLeft, ArrowRight, PageUp, PageDown, Home, End, Space, Backspace | focus target, `Input.dispatchKeyEvent` | focusable targets exist |
| SELECT | `select_target` (element:option) | as jev-ultrafast | native selects exist |
| UPLOAD | `upload_target` | ASK_USER kind `file`, then `DOM.setFileInputFiles` on the input's backend node | file inputs exist |
| SCROLL_UP / SCROLL_DOWN / SCROLL_LEFT / SCROLL_RIGHT | `scroll_<dir>_target` (page or container) | `mouseWheel` at the container's centre | something can scroll that way |
| GO_BACK / GO_FORWARD | — | `Page.navigateToHistoryEntry` | history entry exists |
| RELOAD | — | `Page.reload` | always |
| OPEN_URL | — | text model writes URL; only `http`/`https` accepted; `Page.navigate` | always |
| SWITCH_TAB / CLOSE_TAB | `switch_tab_target` / `close_tab_target` | switch the task's active session / `Target.closeTarget` | task owns more than one tab |
| ACCEPT_DIALOG / DISMISS_DIALOG | — | `Page.handleJavaScriptDialog` | a `confirm`, `prompt`, or `beforeunload` dialog is open |
| SAVE_PDF | — | `Page.printToPDF` → file sent to chat | always |
| WAIT | — | 100 ms, then observe | always |
| LOOK | — | visual fallback (6.6) | always |
| ASK_USER | — | Section 7 | always |
| CONFIRM | — | Section 8 | always |
| NOTE | — | text model extracts facts into task memory | always |
| DONE | — | text model writes the result with verbatim evidence | always |
| BLOCKED | — | help ladder (7.3) | always |

All target questions for the offered operations are asked in the same Jev request; only the chosen operation's target answer is used.

### 6.4 Jev step request

As in jev-ultrafast `choose()`: `state` = page URL, title, redacted text, element table, last 10 redacted actions; `questions` = `operation` plus one question per target head. The operation question's `instructions.goal` is the task context:

```
Goal: <original goal>
User updates: <amendments, oldest first>
Task memory: <user answers (secrets as [secret:<name>]), notes>
Profile: addresses, preferences, cards (label + last 4), available profile keys
Approval: <current approval summary, or none>
```

Validation stays strict: choice must be offered, probabilities finite, summing to 1 within 0.02, and the choice must be the argmax. An invalid answer executes nothing.

### 6.5 Text-model jobs

Every job returns one JSON object; anything else is rejected and nothing is executed.

| Job | Output |
|---|---|
| start | `{"start_url": "https://…", "title": "Swiggy · Diet Coke"}` |
| field value | one of `{"text": "…"}`, `{"profile": "phone"}`, `{"secret": "card.hdfc.cvv"}`, `{"ask": "…", "secret": true\|false}` |
| compose question | `{"kind": "text\|secret\|choice\|file\|image", "text": "…", "options": [{"label": "…", "description": "…", "element": "12"}], "crop_element": "7"}` |
| confirm card | `{"action_label": "Pay ₹79", "amount": "₹79", "lines": ["Diet Coke 300 ml × 1", "Home – Indiranagar", "HDFC ••1234"]}` |
| payment outcome | `{"outcome": "success\|failed\|unclear", "evidence": "…"}` |
| result | `{"text": "…", "evidence": "…"}` |
| note | `{"facts": ["…"]}` |
| user screenshot hint | `{"hint": "…"}` |
| open URL | `{"url": "https://…"}` |
| remember | `{"preference": "Default Instamart address: Home"}` |
| status / other reply | `{"text": "…"}` |
| LOOK | `{"cell": "C4"}` |

Option `element` values and `crop_element` must be indices from the current element table; `profile` and `secret` keys must exist in the profile or vault; `evidence` and `amount` must occur verbatim (whitespace-normalised) in the current page text. Failing any check turns the job into a failure handled like an invalid model response.

### 6.6 LOOK

For canvas, maps, image-only controls, and closed shadow roots. The executor takes a screenshot with secret fields masked (12.4), overlays an 8×6 labelled grid (A1–H6), and asks the text model which cell holds the needed control. A second call on a 4×4 grid inside that cell refines it. The executor clicks the centre of the refined cell. The model returns cell labels only; code converts them to coordinates. DRAG to a free position uses LOOK for the drop point.

### 6.7 Execution invariants (from jev-ultrafast)

- Every target resolves to an observed node; freshness guards and occlusion are rechecked immediately before input.
- Browser mutations are never retried automatically.
- Execution is logged before the next observation.
- A stale page means observe and decide again.
- Budgets: 150 executed actions and 300 Jev step requests per task.

## 7. Talking to the user

### 7.1 ASK_USER

The text model composes the question from the page and task context. Kinds:

| Kind | Example | Reply handling |
|---|---|---|
| text | "Which pickup point?" | stored in task memory |
| secret | "Swiggy sent you an OTP." | stored in memory only for the task; registered for redaction; never persisted |
| choice | "Deliver to which address?" + options tied to elements | chosen option's element is clicked directly after a freshness check; if stale, re-observe and match by label; if no match, Jev decides with the answer in context |
| file | "Upload the statement PDF." | media stored in `~/.jev/uploads/`, then UPLOAD |
| image | captcha or QR crop + question | answer typed or acted on |

Choice replies carry a "Remember this" option. When chosen, the text model writes a preference sentence, it is appended to the profile, and the user sees "Saved: <sentence>".

A question the user has not answered for 15 minutes marks the task `paused`. A reply resumes it after a fresh observation. A paused task is cancelled after 24 hours with a summary.

### 7.2 Format check before typing

User-provided values are checked against field constraints (`maxlength`, `inputmode`, `pattern`, `type`, OTP `group_size`) before typing. A mismatch is re-asked immediately ("The OTP should be 6 digits") and does not count as an attempt.

### 7.3 Help ladder (BLOCKED)

| Rung | Behaviour |
|---|---|
| 1 Show and ask | Masked crop of the blocking region + a question (captcha text, "which of these?"). |
| 2 Ask for guidance | Explain what is blocking; ask for directions or a screenshot from the user's own device. A screenshot is converted to a text hint by the text model and appended to task memory. |
| 3 Device action | Ask the user to act in their own app ("Approve the ₹79 UPI request, then reply done"), then re-observe. |
| 4 Stop cleanly | Report what was done, what blocked, and whether anything was committed. Task ends `failed`. |

Two unsuccessful hints on the same page (host + path) move to rung 4. Passkey or biometric prompts go to rung 4 unless an OTP or password alternative is visible, which is tried first. The first time a user sends an image, the chat states once that images are sent to OpenAI.

### 7.4 DONE

DONE never alone proves success. The result job must quote verbatim evidence from the page (an order id, "Order placed"). Without evidence the message says the task looks complete but could not be confirmed, and suggests where to check.

## 8. Commit gate

Two independent detectors; either one stops before the action:

1. Jev chooses CONFIRM. Its criterion: the next action commits and the user has not approved this exact action.
2. Code backstop. Before executing CLICK, PRESS_KEY Enter (using the enclosing form's submit label), or ACCEPT_DIALOG, the target label is matched (case-insensitive) against:

```
\b(pay|place order|buy|purchase|book now|confirm (order|booking|ride|payment|purchase)|request (ride|uber\w*|trip)|cancel (order|booking|subscription|ride)|delete|submit (complaint|dispute|claim|request))\b
```

A match without a valid approval converts the action to CONFIRM. Messages and emails are detected by Jev only.

CONFIRM flow:

- The confirm-card job builds the card. The `amount` must occur verbatim in page text.
- The chat shows the card with two buttons: "Yes, <action_label>" and "No".
- Approval record: `{task_id, card_id, action_label, amount, host}`.
- An approval is consumed by the first commit action. It is valid only while the approved amount still appears on the page, the host matches, fewer than 20 actions have passed, and no AMEND arrived. Otherwise a new card is shown, including when the total changed.
- A second commit in the same task (for example a retry) needs a new approval.
- Bank-side steps after payment ("Submit" OTP, "Verify") do not match the backstop and proceed.
- "No" asks what to change: payment, items, address, or cancel the task.

## 9. Wrong data and payment outcomes

- Each user-provided or vault value is tagged with the field it was typed into. If the same field appears again after submission, the value is marked rejected, removed from task memory, and never retyped. The next question quotes the site's error text.
- Attempts are counted in code per `(host, field label)`. After the second rejection the question warns that one attempt is left. After the third, the task stops before typing again.
- CVV: at most 2 attempts per card per task. A vault CVV that was rejected is replaced only after a subsequent successful payment using the new value.
- Expired OTP: the agent clicks the site's resend control (not a commit) and asks for the new code.
- After every commit, the payment-outcome job classifies the next page. A retry is offered only on `failed` with verbatim evidence and requires a fresh approval. `unclear` is never retried; the user is asked to check their bank for a debit.
- Wrong profile data or wrong choices are caught at the confirm card; after a fix the agent offers to update the profile.
- CANCEL stops before the next action. If a commit is mid-flight, the user gets its outcome.

## 10. Profile, vault, redaction

### 10.1 Stores

| Store | Contents | Lifetime |
|---|---|---|
| `~/.jev/profile.json` (mode 600) | name, phone, email, UPI ID, addresses `[{label, text, lat, lng}]` (coordinates optional), preferences `[{text, from_task}]`, cards `[{id, label, last4}]` | permanent |
| Keychain service `jev` | `card.<id>.number`, `.expiry`, `.name`, `.cvv`; optional `login.<host>` | permanent |
| process memory | OTPs, one-off secrets | task |
| `~/.jev/chrome/` | agent Chrome profile | permanent |
| `~/.jev/uploads/`, `~/.jev/downloads/<task>/`, `~/.jev/traces/` | user files, downloaded files, redacted traces | permanent |

Cards are added in chat: the first time a card is needed, number, expiry, and CVV are asked as secrets, then "Save to vault for next time?".

### 10.2 Visibility

| Data | Jev | Text model | Chat | Traces |
|---|---|---|---|---|
| Address labels and text, preferences, card label + last 4 | yes | yes | yes | yes |
| Name, phone, email, UPI ID | `[profile:<key>]` | `[profile:<key>]` | yes | `[profile:<key>]` |
| Card number, expiry, CVV, OTPs, passwords | never | never | `••••` | never |
| Page text and elements | redacted | redacted | — | redacted |
| Screenshots | never | masked | masked | masked |
| User-sent images | — | as sent | yes | not stored |

### 10.3 Redaction

A per-task registry holds every profile value, every vault value used, and every secret typed. `redact(obj)` deep-replaces registered values: whole-word matches for values of 3 or more characters; digit sequences of 4 or more compared after removing spaces and dashes. Any field that received a secret reports `[secret]` as its value regardless of formatting. Every outbound path — model requests, trace writes, channel messages — goes through one wrapper that calls `redact`.

### 10.4 Screenshot masking

Before any screenshot that leaves the process, fields that received secrets get `filter: blur(12px)` and `-webkit-text-security: disc` via an injected style; the style is removed after capture.

## 11. Messages and multiple tasks

### 11.1 Intent classification

Every free-text inbound message is classified by one Jev request with two questions: `intent` ∈ {ANSWER, AMEND, NEW_TASK, CANCEL, STATUS, OTHER} and `task` ∈ active tasks. State: active tasks (goal, current step, open question), the last 6 chat messages, the new message. A button reply or a quoted reply (`context.id`) routes directly without classification.

If the top intent probability is below 0.6, or the top two are within 0.2, the assistant asks which was meant. A message that could answer more than one open secret question is never routed by guess.

| Intent | Handling |
|---|---|
| ANSWER | the open question |
| AMEND | appended to that task's user updates; its approval is invalidated; if the commit already happened, offer to modify or cancel |
| NEW_TASK | start a task |
| CANCEL | stop that task before its next action |
| STATUS | text model answers from task state |
| OTHER | short reply |

### 11.2 Runner

- One thread and one tab per task. At most 3 running tasks; at most 1 per site (hostname without leading `www.` or `m.`). Extra tasks wait with a message.
- Questions go into one queue; one question is open in chat at a time. Priority: secret and payment questions first, then oldest first.
- One event-pump thread drains Browser Harness events every 50 ms and routes them by `session_id` to the owning task. This is required because `drain_events` is a single shared buffer and the daemon tracks one dialog.
- `cdp()` calls from task threads are independent sockets and need no lock.
- State lives in memory. Restarting the server ends running tasks; Chrome logins and traces persist.

## 12. Channel and UI

### 12.1 WhatsApp-shaped web channel

| WhatsApp Cloud API | Web channel |
|---|---|
| inbound webhook: `text`, `interactive.button_reply`, `interactive.list_reply`, `image`, `document`, `context.id` | `POST /webhook`, same JSON shape |
| send message: text, image, document, interactive button / list | `channel.send(user, payload)` → outbox; UI polls `GET /outbox?after=<seq>` every 500 ms |
| media upload → id | `POST /media` (raw body, 20 MB cap) → `{id}`; `GET /media/<id>` |
| typing indicator | "typing…" while any task runs |
| sender `wa_id` | `from: "local"` |

`channel_web.py` exposes `send()`, `media()`, and the webhook handler that calls `core.receive(user, event)`. A WhatsApp channel later implements the same three with the Graph API.

Limits enforced by the core for every channel: text ≤ 4,096 characters; at most 3 reply buttons with titles ≤ 20 characters; lists of at most 10 rows with titles ≤ 24 and descriptions ≤ 72 characters; more options become a numbered text list answered by number. Button ids encode `q<question_id>:<option_id>`.

Secret questions carry `x_secret: true`, a web-only extension the web UI renders as a password field. Secret inbound text is dropped from the core's message store as soon as it is consumed.

### 12.2 Server

Loopback only on `127.0.0.1:8766`, standard-library HTTP server, with jev-ultrafast's Host, Origin, and per-launch token checks. `GET /api/debug` (live screenshot and step log per task) exists only when `JEV_DEBUG=1`.

### 12.3 UI

One page that looks and behaves like a WhatsApp conversation: bubbles, reply buttons, list picker, images, documents, a composer with attachment, and a typing indicator. No task chips. The debug panel is a separate view for development.

## 13. Browser

### 13.1 Agent Chrome

Headed, hidden, dedicated profile (Section 5). `jev probe` is the first check in development so blocking is measured before loop work begins.

### 13.2 Frames, shadow DOM, pop-ups

- Open shadow roots and same-origin frames: traversed in `snapshot.js`.
- Cross-origin frames: `Target.setAutoAttach {autoAttach: true, flatten: true, waitForDebuggerOnStart: false}` on each page session; `snapshot.js` runs in every child session; element ids are prefixed per frame; coordinates are offset by the owner iframe's rect in the parent document.
- Pop-ups: `Target.targetCreated` with `openerId` equal to a task's target attaches the new target to that task and makes it the active tab; when it closes, the opener becomes active again.

### 13.3 Browser events

| Event | Handling |
|---|---|
| `alert` | accepted; its text is added to the next observation |
| `confirm`, `prompt`, `beforeunload` | ACCEPT_DIALOG / DISMISS_DIALOG offered; accept passes through the commit gate |
| file chooser | `Page.setInterceptFileChooserDialog` enabled; `Page.fileChooserOpened` → ASK_USER `file` → `DOM.setFileInputFiles` |
| downloads | `Browser.setDownloadBehavior {behavior: "allowAndName", downloadPath: ~/.jev/downloads/<task>, eventsEnabled: true}`; completed downloads (≤ 100 MB) are sent to chat as documents |
| geolocation | granted with `Emulation.setGeolocationOverride` from the chosen or default address coordinates when present; otherwise denied |
| notifications, camera, microphone | denied |

## 14. Safety invariants

- Page text is untrusted data, never instructions, in every prompt.
- Model output never becomes selectors, coordinates, JavaScript, shell commands, or file paths. URLs are accepted only as `http`/`https`.
- Secrets never reach Jev, the text model, traces, or persisted chat.
- No commit without an approval tied to that task and card.
- No automatic retry of browser mutations or of payments with unclear outcomes.
- Credentials stay server-side; `.env` is git-ignored; Browser Harness telemetry is disabled.

## 15. Testing

Offline (`uv run pytest`), no paid API calls:

- Pure logic: action space for all operations, Jev answer validation, text-model output validation, redaction (every format of every registered value), commit regex, approval lifecycle, verbatim amount and evidence checks, attempt limits, payment-outcome handling, intent routing and ambiguity, secret non-guessing, question-queue priority, channel limits.
- Browser fixtures (`uv run pytest -m browser`, real Chrome, fake Jev and text model): local sites served on two ports so cross-origin behaviour is real — login with OTP boxes, address choice, cart and checkout, payment form in a cross-origin iframe, 3DS pop-up, upload, download, `confirm()` dialog, canvas for LOOK, drag list, hover menu, scroll panel, shadow DOM, slider.
- The central safety test records every outbound request body during fixture flows and asserts no registered value appears in any format.

Checks: `uv run ruff check .`, `uv run pytest`, `node --check assistant/snapshot.js`, `node --check assistant/static/app.js`.

## 16. Live smoke set

Run manually with real accounts; each completes for real:

1. Instamart: order Diet Coke.
2. Uber: book a ride, choosing from the listed ride types and prices.
3. Swiggy: order food from a named restaurant.
4. Amazon: order an item.
5. A complaint or dispute form submitted to an organisation.
6. Download a document (for example an invoice) and deliver it in chat.
7. An upload task on a site chosen when that phase starts.

Pass criteria per run: the task completes; the result quotes on-page evidence; no question was unnecessary (question count recorded in the trace).

## 17. Build phases

v1 is complete when every phase passes. Order follows dependencies.

| Phase | Content |
|---|---|
| 0 | Repo, copied core with attribution, OpenAI client fix, `jev chrome`, `jev probe` |
| 1 | Snapshot and executor breadth: clickable non-semantic elements, password fields, OTP groups, scroll containers, sliders, HOVER, DRAG, DOUBLE_CLICK, RIGHT_CLICK, PRESS_KEY, navigation, OPEN_URL, tabs |
| 2 | Frames, shadow DOM, pop-ups, event pump, dialogs, file chooser, downloads, permissions, SAVE_PDF |
| 3 | Profile, Keychain vault, redaction, screenshot masking |
| 4 | ASK_USER, CONFIRM, NOTE, DONE, commit gate, wrong-data rules, help ladder |
| 5 | Web channel, chat UI, runner threads, question queue, intent classification, parallel tasks |
| 6 | LOOK |
| 7 | Live smoke set; fix and repeat |

## 18. Risks

| Risk | Mitigation |
|---|---|
| Sites block the agent Chrome | Headed Chrome with a real profile on a home IP; `jev probe` measures it first. |
| Many operations reduce Jev accuracy | Operations are offered only with valid targets; smoke traces record operation probabilities to find confusions. |
| Commit regex misses a commit | Jev CONFIRM is the primary detector; misses found in traces extend the regex and its tests. |
| Keychain prompt interrupts the first vault read | The first read happens during card setup in chat, where the user expects it. |
| Real money during testing | One approval per commit; confirm card amounts are verbatim; unclear payments are never retried. |
| Terms of service of automated sites | Personal use on the user's own accounts; revisit before any multi-user launch. |
