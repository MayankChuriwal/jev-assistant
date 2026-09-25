# Known issues

Minor findings deferred by the final reviews of each plan. Each keeps its ID for good; when one is scheduled, its
status names the plan and task; when fixed, the plan and task that fixed it. New findings from live runs are added here too.

Status: `open` · `Plan N Task M` (scheduled) · `fixed (Plan N Task M)` · `won't fix: <reason>`

## Plan 1 — browser foundation

| ID | Issue | Where | Status |
|---|---|---|---|
| P1-01 | "Drop-off" and "Drag the map" are read as upload drop zones, because text asking for a drop marks its container as an upload target. | `snapshot.js` | fixed (Plan 4 Task 5) |
| P1-02 | Every option of a native dropdown counts as a candidate, so a long country list can use up the 250-candidate cap and hide real controls. | `snapshot.js` | fixed (Plan 4 Task 5) |
| P1-03 | Recording screenshots (`record_dir`) crashes while a JavaScript dialog is open. | `agent.py` | open |
| P1-04 | One unexpected error inside the event pump's loop stops event routing for every task until restart. | `browser.py` `EventPump` | fixed (Plan 4 Task 5) |
| P1-05 | A dialog that opens while the page is being read costs 5 s per iframe. | `browser.py` | open |
| P1-06 | Back and forward wait the full 15 s on single-page apps that change the URL without loading. | `browser.py` `_navigation` | open |
| P1-07 | Page scripts can tamper with the in-page snapshot helper. | `snapshot.js` | open |
| P1-08 | Remote objects created by iframe lookups are never released. | `browser.py` | open |
| P1-09 | File-picker interception is not enabled inside cross-origin iframes. | `browser.py` | open |
| P1-10 | `jev probe` reports `ok` when a site's DNS lookup fails. | `server.py` | open |
| P1-11 | Some custom menu and listbox controls are not picked up as candidates. | `snapshot.js` | open |
| P1-12 | 1×1 tracker iframes are still observed, which costs time on every read. | `browser.py` | fixed (Plan 4 Task 5) |

## Plan 2 — talking and safety

| ID | Issue | Where | Status |
|---|---|---|---|
| P2-01 | A field's attempt count never resets after the value is accepted, so a later code for the same field starts with used attempts. | `agent.py` | partly fixed (Plan 4): a user's code stops counting once the URL changes and the field is gone; saved values keep counting; a same-URL modal still keeps counting |
| P2-02 | OTP boxes without their own label share one attempt count per site. | `agent.py` | partly fixed (Plan 4): as P2-01 |
| P2-03 | Keychain calls have no timeout, so a Keychain unlock dialog hangs the task. | `profile.py` | fixed (Plan 4 Task 6) |
| P2-04 | Screenshots only blur secret fields (no disc masking), and a field the page re-renders is not blurred again. | `browser.py` | fixed (Plan 4 Task 5) |
| P2-05 | In the terminal, typing "yes" at a confirmation declines; only the option number works. | `server.py` `terminal_asker` | open |
| P2-06 | Temporary image files written by the terminal asker are never deleted. | `server.py` | open |
| P2-07 | If the text model answers a field with a placeholder such as `[profile:phone]`, those literal characters are typed. | `agent.py` | fixed (Plan 4 Task 6) |
| P2-08 | DONE can finish while a payment's outcome is still pending. | `agent.py` | fixed (Plan 4 Task 6) |
| P2-09 | The terminal prints nothing when a task stops for making no progress. | `server.py` | fixed (live run 1): a task that makes no progress asks the user for help, in chat and in the terminal |

## Plan 3 — chat and runner

| ID | Issue | Where | Status |
|---|---|---|---|
| P3-01 | A superscript digit ("²") passes `isdigit()` but fails `int()`, so a numbered reply is refused as "Not delivered"; the server also reports any error inside the core as a bad webhook body. | `core.py`, `messages.py`, `server.py` | open |
| P3-02 | Deeply nested JSON under 64 KB raises `RecursionError`, which drops the connection instead of answering 400 (the server keeps running). | `server.py` | open |
| P3-03 | The 15-minute pause and 24-hour cancel timers start when a question is queued, not when it is shown. | `core.py` | open |
| P3-04 | With no task running, an ambiguous message such as "hi" can start a task instead of asking. | `core.py` | open |
| P3-05 | "Which task?" offers at most 2 tasks (3 for stop or change), and titles cut to 20 characters can look identical. | `core.py` | open |
| P3-06 | Tapping a button of an already-answered question gives no feedback. | `core.py` | open |
| P3-07 | Files the user sends reach websites under a random media-id name (and `.bin` without a MIME type), not the original filename. | `channel_web.py`, `core.py` | fixed (Plan 4 Task 7) |
| P3-08 | `_forget` iterates the tab-owner map while other task threads add to it, which can raise and leave tabs open. | `browser.py` | open |
| P3-09 | Cancelled or interrupted downloads stay in the download maps and keep closed browsers in memory. | `browser.py` | open |
| P3-10 | Parallel tasks share one profile, and concurrent saves use the same temporary file. | `profile.py` | fixed (Plan 4 Task 6) |
| P3-11 | If sending a question fails after it opens, it stays open and blocks every later question. | `core.py` | open |
| P3-12 | After `jev` restarts, an open chat page gets 403 with its old token and shows "offline, retrying" forever instead of asking for a reload. | `static/app.js` | fixed (Plan 4 Task 7) |
| P3-13 | The attach button stays highlighted if a file question is answered another way. | `static/app.js` | open |
| P3-14 | The password-type composer may make Chrome offer to save OTPs, CVVs, or passwords. | `static/app.js` | open |
| P3-15 | Each webhook is handled on its own thread, so a goal and a quick follow-up change can be routed out of order. | `server.py`, `core.py` | open |
| P3-16 | Documents sent to chat carry no task title when several tasks run. | `core.py` | open |
| P3-17 | Nothing confirms when a task that was asked to stop has actually stopped. | `core.py` | fixed (Plan 4 Task 7) |

## Plan 4 — LOOK, traces, smoke kit

| ID | Issue | Where | Status |
|---|---|---|---|
| P4-01 | Trace file names come from the unredacted task title (a phone or email in a `jev run` goal lands in the name); `~/.jev/traces` is created 0755 and each file is readable for an instant before `chmod`. | `agent.py` `save_trace` | open |
| P4-02 | A task the chat stopped, or gave up on after three problems, is traced as `ready` instead of cancelled or failed. | `core.py`, `agent.py` | fixed (live run 3) |
| P4-03 | The LOOK grid is drawn under top-layer content, so it is hidden behind a modal `<dialog>`. | `browser.py` `GRID` | open |
| P4-04 | Event pump: a persistent error prints about 20 lines a second; one malformed event drops the rest of its batch; a stopped pump cannot restart. | `browser.py` `EventPump` | open |
| P4-05 | Keychain waits block the task thread up to 120 s with no cancel check; the unlock prompt stays up after the timeout; a timed-out card save repeats its offer. | `profile.py`, `agent.py` | open |
| P4-06 | With `JEV_DEBUG=1`, a debug-view capture can unmask fields in the middle of a LOOK capture. | `server.py`, `browser.py` | open |
| P4-07 | `jev traces --limit 0` prints every trace. | `server.py` | open |
| P4-08 | A `[secret:<key>]` the field may use is refused (a "Problem" message) instead of being used like `{"secret": key}`. | `agent.py` `resolve` | open |
| P4-09 | URL-encoded emails (`m%40…`) and name parts that are common words escape redaction. | `profile.py` `Secrets` | open |
| P4-10 | An answer the text model marks as not secret reaches task memory and traces as typed. | `agent.py` | open |
| P4-11 | Profile saves are locked within one process only; `jev run` and `jev` running at once can collide. | `profile.py` | open |
| P4-12 | How reliably GPT-4.1 mini reads the 11 px grid labels, and clip scale on a Retina display, are unverified until live runs. | `browser.py`, `questions.py` | open |

## Live runs

| ID | Issue | Where | Status |
|---|---|---|---|
| L1-01 | "Order Diet Coke from Swiggy" starts on swiggy.com's restaurant search instead of Instamart, and wanders through restaurant menus. | `jobs.start`, Jev | fixed (live run 2): grocery items start in the service's grocery section; loops ask for help |
| L1-02 | A site that keeps a "Sign in" banner up after the user signed in keeps the sign-in step pending; the user must say "continue". | `agent.py` `track_login` | open (sign-in via a magic link or a device approval also stays pending) |
| L2-01 | Icon-only tiles carry labels like "IM" and "DO", so Jev cannot tell that "IM" is Instamart. | `snapshot.js` labels | open |
| L2-02 | The user's name shown by a site after sign-in reaches the models unless it is in the profile; set it once with `jev profile --name …`. | `profile.py` | open (mitigated: `jev profile`; phone and email are learned when typed) |
| L3-01 | On Uber, the summary card for "Request Uber Go AC" failed three times, so the ride was never requested; the error was not recorded then. Traces now keep errors, so the next run shows the cause. | `jobs.confirm_card` | open |
| L3-02 | Ola's login form was not seen after "LOG IN" (only a scroll panel and a reCAPTCHA badge), so the phone number had nowhere to go. Repeated questions now turn into help, where the user can say "scroll down". | `snapshot.js`, Jev | open |
| L4-01 | Instamart: the agent picked the saved "HOME" address itself instead of asking which address. | `agent.py` | fixed (live run 4): two or more saved addresses always become a question unless a saved preference names one |
| L4-02 | Uber: "current location" can't work in the agent's hidden Chrome (no location), and Uber's airport suggestion became the pickup. | `agent.py`, Jev | fixed (live run 4): the agent Chrome shares the device's location, and "current location" uses the site's own detect-location option; nothing is stored; suggestions must match the user's words |
| L5-01 | Uber sign-in: the agent asked for the phone number the profile already held, then a dropped connection surfaced as a problem. | `agent.py`, `model.py` | fixed (live run 5): while signing in, profile details are typed, never asked; dropped connections are retried twice |
| L5-02 | Uber's pickup and dropoff boxes share the label "Search for a location", so the pickup value was typed into the dropoff box and the wrong suggestions followed. | `snapshot.js` | fixed (live run 5): fields sharing a label get their placeholder or position added |
| L6-01 | Uber: the page navigated while its iframes were being attached, and Chrome's "No target with given id found" reached chat as a Problem. | `browser.py` `_sync_frames` | fixed (live run 6): a frame that disappears while it is being attached is skipped |
| L6-02 | Uber sign-in: Jev asked the user while the site was still changing screens (after Continue, and while it checked a code), so the phone number was asked once and the code three more times. | `agent.py` | fixed (live run 6): for 3 s after a step, a question waits first; if the page changes, Jev chooses again |
| L6-03 | The text model typed an invented code ("1234") into Uber's OTP field instead of the code the user sent, and the site rejected it. | `agent.py` `field_input` | fixed (live run 6): code and password fields take only a value from the user or the Keychain; model text becomes the user's newest unused code, or a question |
| L6-04 | Retyping a code into boxes a rejected code left filled typed nothing (each box holds one character). | `browser.py` | fixed (live run 6): each box's contents are selected before typing |
| L6-05 | The start page for an Uber ride was www.uber.com/global/en/ride/, which is a 404. Jev recovered by clicking "Go to Uber.com". | `jobs.start` | open |
| L7-01 | Uber: when stuck, help sent a half-cut screenshot and told the user to act on the site ("You can do so by typing your destination in the destination input field"). | `agent.py` `unblock`, `questions.py` | fixed (live run 7): help sends no screenshots and never asks the user to click or type on the site; choices the page shows come as buttons in chat, and the agent clicks the one picked. A captcha still comes as a picture, since only the user can solve it |
| L7-02 | Uber: the agent picked Uber Go AC itself; Go Non AC and Premier AC were never offered. | `agent.py`, `rules.py` | fixed (live run 7): two or more priced options (ride types, sizes, plans) become a choice with prices, unless the goal or an answer names one; a commit that names one ("Request Uber Go AC") asks first too |
| L7-03 | After "Request Uber Go AC" the site showed "Finding drivers nearby"; after 10 checks the agent stopped with "check your bank or account" and never reported the driver. | `agent.py` `check_commit`, `questions.py` | fixed (live run 7): a commit the site is still working on is watched for up to 5 minutes, and the user is told once; the result reports the driver, car, number plate, and PIN when the page shows them; the "can't tell" message no longer mentions a bank |
| L7-04 | Uber: for a "current location" pickup, the agent opened the pickup box 4 times without finding the site's detect-location option, then asked the user to pick a suggestion. | Jev, `snapshot.js` | re-test on the bench (Plan 5: mini decides, loop nudges) |
| L7-05 | Option names keep icon text ("Uber Go AC Person"). | `snapshot.js` labels | re-test on the bench (Plan 5: options are mini's own words) |


## Plan 5 — mini + Jev agent

| ID | Issue | Where | Status |
|---|---|---|---|
| P5-01 | The bench audits commits only; that no secret reaches a model rests on the single redacting outbound path (`model.post_json`). | `bench.py` | open (by design) |
