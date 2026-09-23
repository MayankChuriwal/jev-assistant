# Known issues

Minor findings deferred by the final reviews of each plan. Each keeps its ID for good; when one is scheduled, its
status names the plan and task; when fixed, the commit. New findings from live runs are added here too.

Status: `open` · `Plan N Task M` (scheduled) · `fixed <commit>` · `won't fix: <reason>`

## Plan 1 — browser foundation

| ID | Issue | Where | Status |
|---|---|---|---|
| P1-01 | "Drop-off" and "Drag the map" are read as upload drop zones, because text asking for a drop marks its container as an upload target. | `snapshot.js` | open |
| P1-02 | Every option of a native dropdown counts as a candidate, so a long country list can use up the 250-candidate cap and hide real controls. | `snapshot.js` | open |
| P1-03 | Recording screenshots (`record_dir`) crashes while a JavaScript dialog is open. | `agent.py` | open |
| P1-04 | One unexpected error inside the event pump's loop stops event routing for every task until restart. | `browser.py` `EventPump` | open |
| P1-05 | A dialog that opens while the page is being read costs 5 s per iframe. | `browser.py` | open |
| P1-06 | Back and forward wait the full 15 s on single-page apps that change the URL without loading. | `browser.py` `_navigation` | open |
| P1-07 | Page scripts can tamper with the in-page snapshot helper. | `snapshot.js` | open |
| P1-08 | Remote objects created by iframe lookups are never released. | `browser.py` | open |
| P1-09 | File-picker interception is not enabled inside cross-origin iframes. | `browser.py` | open |
| P1-10 | `jev probe` reports `ok` when a site's DNS lookup fails. | `server.py` | open |
| P1-11 | Some custom menu and listbox controls are not picked up as candidates. | `snapshot.js` | open |
| P1-12 | 1×1 tracker iframes are still observed, which costs time on every read. | `browser.py` | open |

## Plan 2 — talking and safety

| ID | Issue | Where | Status |
|---|---|---|---|
| P2-01 | A field's attempt count never resets after the value is accepted, so a later code for the same field starts with used attempts. | `agent.py` | open |
| P2-02 | OTP boxes without their own label share one attempt count per site. | `agent.py` | open |
| P2-03 | Keychain calls have no timeout, so a Keychain unlock dialog hangs the task. | `profile.py` | open |
| P2-04 | Screenshots only blur secret fields (no disc masking), and a field the page re-renders is not blurred again. | `browser.py` | open |
| P2-05 | In the terminal, typing "yes" at a confirmation declines; only the option number works. | `server.py` `terminal_asker` | open |
| P2-06 | Temporary image files written by the terminal asker are never deleted. | `server.py` | open |
| P2-07 | If the text model answers a field with a placeholder such as `[profile:phone]`, those literal characters are typed. | `agent.py` | open |
| P2-08 | DONE can finish while a payment's outcome is still pending. | `agent.py` | open |
| P2-09 | The terminal prints nothing when a task stops for making no progress. | `server.py` | open |

## Plan 3 — chat and runner

| ID | Issue | Where | Status |
|---|---|---|---|
| P3-01 | A superscript digit ("²") passes `isdigit()` but fails `int()`, so a numbered reply is refused as "Not delivered"; the server also reports any error inside the core as a bad webhook body. | `core.py`, `messages.py`, `server.py` | open |
| P3-02 | Deeply nested JSON under 64 KB raises `RecursionError`, which drops the connection instead of answering 400 (the server keeps running). | `server.py` | open |
| P3-03 | The 15-minute pause and 24-hour cancel timers start when a question is queued, not when it is shown. | `core.py` | open |
| P3-04 | With no task running, an ambiguous message such as "hi" can start a task instead of asking. | `core.py` | open |
| P3-05 | "Which task?" offers at most 2 tasks (3 for stop or change), and titles cut to 20 characters can look identical. | `core.py` | open |
| P3-06 | Tapping a button of an already-answered question gives no feedback. | `core.py` | open |
| P3-07 | Files the user sends reach websites under a random media-id name (and `.bin` without a MIME type), not the original filename. | `channel_web.py`, `core.py` | open |
| P3-08 | `_forget` iterates the tab-owner map while other task threads add to it, which can raise and leave tabs open. | `browser.py` | open |
| P3-09 | Cancelled or interrupted downloads stay in the download maps and keep closed browsers in memory. | `browser.py` | open |
| P3-10 | Parallel tasks share one profile, and concurrent saves use the same temporary file. | `profile.py` | open |
| P3-11 | If sending a question fails after it opens, it stays open and blocks every later question. | `core.py` | open |
| P3-12 | After `jev` restarts, an open chat page gets 403 with its old token and shows "offline, retrying" forever instead of asking for a reload. | `static/app.js` | open |
| P3-13 | The attach button stays highlighted if a file question is answered another way. | `static/app.js` | open |
| P3-14 | The password-type composer may make Chrome offer to save OTPs, CVVs, or passwords. | `static/app.js` | open |
| P3-15 | Each webhook is handled on its own thread, so a goal and a quick follow-up change can be routed out of order. | `server.py`, `core.py` | open |
| P3-16 | Documents sent to chat carry no task title when several tasks run. | `core.py` | open |
| P3-17 | Nothing confirms when a task that was asked to stop has actually stopped. | `core.py` | open |
