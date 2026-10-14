# Jev — Finance agent, step 1: the money layer, proven on bill payments

Date: 2026-10-14. Branch: `mini-jev-agent`. Builds on `2026-10-12-brain-and-hands-design.md` and
`2026-10-13-voucher-demo-design.md`; everything they specify stays unless this spec changes it. Research behind it:
`docs/research/2026-10-14-finance/` (bill payments, mandates and payment rails, bureau disputes, card applications) and
the comparison of browser agents at https://claude.ai/artifact/Mg7mcDvGFugrifdfNhymLL.

## 1. Why

The user wants Jev to be the best browser agent for five financial jobs: credit-bureau disputes, NPCI subscriptions
(mandates), gift cards from bank portals, bill payments (electricity, credit cards, loans, FASTag, DTH, water), and
credit-card applications. On 14 Oct they objected to how the voucher demo was built: "If for every website I have to
manually go and test and for every failure we go and update our code, that means I would have simply written a
script". In the last voucher dry run, code chose all 14 browser steps.

The research shows how the leading agents avoid that. One strong model acts on the page (an element tree, screenshots
on demand, actions in batches, results checked). Site knowledge is words the model reads. Money, secrets and approvals
are guarded in code: PayBench measured unsafe payments falling from 23% to 7% when spending limits sit in the tools
rather than in the prompt.

There is no BBPS licence and no BBPS vendor. Everything runs through the browser, on sites the user could use
themselves, paid with the user's own UPI or card. Jev never holds money.

This spec records the plan for all five jobs (§3) and designs its first step: the money layer, proven on bill
payments, starting with the user's own BESCOM bill.

## 2. Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Approach | A: one generic browser agent, a money layer in code, and a playbook per job. Site knowledge is words (site notes); no site gets code |
| 2 | Rails | Browser only. No BBPS licence or vendor. The user's own UPI or card pays; Jev never holds money |
| 3 | Order | 0 the voucher demo on today's code; 1 this spec; 2 vouchers, MyUPI and Experian move onto it; 3 bureau disputes; 4 subscriptions; 5 card applications (§3). Each step gets its own spec, plan and build |
| 4 | Page model | `gpt-6-luna`, reasoning effort high, through the Responses API; settings `ACTOR_MODEL` and `ACTOR_EFFORT`. It replaces Jev's next-click choice and the planner's subgoals in the page loop. Jev keeps judging a typed yes (`jev.confirms`) and a commit's result (`jev.after_commit`) |
| 5 | How it acts | Short action lists (up to 6) on the page's element ids. Code runs them and stops at a pay or submit control, a page change, or a failed check. Model output still never becomes selectors, coordinates, JavaScript or file paths |
| 6 | First target | BESCOM's own pay page. Done when the user's own BESCOM bill is paid end to end, with the user watching |
| 7 | Unseen test | The user's own BWSSB water, DTH, FASTag, and gas or broadband bills, each on its provider's own site, with no site notes. Runs stop at the approval card. The user names each provider when the bench tasks are written |
| 8 | Paying | UPI through a verified QR hand-off to the user's phone. Card through the hidden field, key by key, with the bank's OTP under the same yes. Never net banking |
| 9 | Approval | One yes per payment, at its first pay control, written in code. Later controls of the same payment ride on it while the amount, the biller and the site chain hold; anything else asks again |
| 10 | Official sites | Money moves only on a site-note host, or a host the user confirmed once, and on pages reached by clicking from it |
| 11 | Aggregators | Paytm, Freecharge and PhonePe forbid automated use in their terms, and Amazon blocks AI agents; MSEDCL tells customers to pay "without going through any channels/agents". None is used until its owner gives written permission |
| 12 | Deferred | The scheduled check that a payment posted (with the always-on work), the "take the wheel" link (step 5), complaints other than MyUPI's |

## 3. The plan for all five jobs

Code holds what is true on every site; words hold what is true on one. A new site needs no code: its first money run
is watched, and Jev writes its site note.

The money layer, built in step 1 and used by all five:

1. A page model that acts in short lists and is checked after each one (§5.2).
2. Approvals written in code, by kind: money (payee, amount, account), consent (the exact wording), submission (the
   exact text), cancellation (the payee). One yes per commit; the bank's OTP and SUBMIT ride on it.
3. Intent checks against the page: biller or payee, name on the account, amount, last 4 digits, cart equals order.
4. Payment hand-off: a verified UPI QR to the phone, card details through the hidden field, in-app bank approvals
   waited for.
5. Hand-offs to the user: one-time codes and CAPTCHA pictures (built today); a "take the wheel" live link (step 5).
6. Proof: every reference in chat and in the record; follow-up checks once the always-on work lands.
7. Never twice: nothing due and the payments record are checked before paying; after an unclear result, history comes
   before anything else.
8. Commit only on the task's own site and pages reached from it.

| Step | Job | Sites | The user's part | Commit and after |
|---|---|---|---|---|
| 1 | Bills | Boards' and providers' own pages, BESCOM first | CAPTCHA, one yes, UPI PIN or card OTP | Pay; reference reported |
| 2 | Gift cards, MyUPI, Experian | SmartBuy, iShop, upihelp.npci.org.in, consumer.experian.in | as today | as today; SmartBuy's code steps deleted; iShop with a note only |
| 3 | Bureau disputes | CIBIL (myCIBIL login, report control number), Equifax (report order number), CRIF (OTP, report ID), Experian | OTPs; picking the wrong entry; one yes per dispute | Submit; a check on day 30; then the RBI ombudsman (a representative named in writing may file) and ₹100 a day after day 30 |
| 4 | Subscriptions | MyUPI; card standing instructions on issuers' SiHub or MandateHQ pages (card number and OTP); e-NACH in net banking | OTP or UPI PIN | Cancel or pause; one list of everything the user pays for |
| 5 | Card applications | Issuers' own journeys | PAN and Aadhaar in the hidden field; OTPs; a yes per consent (bureau check, terms, key facts); video KYC is the user's alone (RBI: the customer live; nobody else may run or coach it) | Submit; status checks; activation reminder |

The owner's track runs alongside: a terms review per site, written permission where terms forbid automated use (NPCI
MyUPI's 4.1(k), any aggregator), and counsel's view on whether paying as the user keeps Jev outside BBPS and
payment-aggregator rules.

## 4. The conversation

1. User: "Pay my electricity bill." The brain knows BESCOM from memory (else asks, with the user's billers as options)
   and calls `pay_bill("BESCOM")`. The status line reads "Getting your BESCOM bill…".
2. First time only, the hidden field asks for the BESCOM account ID. Then the CAPTCHA picture: "Type the letters you
   see."
3. Jev: "Your BESCOM bill: ₹1,240, due 20 Oct, in the name of M K CHURIWAL." When the page offers a choice of method
   before its pay control, Jev asks "UPI or card?" with only the methods shown; otherwise the choice comes after the
   yes, on the gateway, and rides on it.
4. The approval card, written in code: "Pay ₹1,240 BESCOM bill · account ••1234 · M K CHURIWAL · due 20 Oct · by UPI"
   with Pay and No.
5. Pay. Jev sends the QR picture with "Open GPay / PhonePe / Paytm" links: "Scan or tap, and pay ₹1,240 to BESCOM with
   your UPI PIN."
6. The user pays. Jev: "Paid ₹1,240 to BESCOM for account ••1234. Reference 7H2K…. It may take a few days to show on
   BESCOM's site."

By card, step 5 is the card form through the hidden field, then the bank's one-time password in the hidden field, and
the bank page's SUBMIT under the same yes.

## 5. Components

### 5.1 Brain: `pay_bill` and the bills playbook

- New tool `pay_bill(biller, amount=None, method=None)`. The biller is as the user names it (BESCOM, BWSSB, Tata Play,
  a FASTag bank). No amount means the amount due; a number means exactly that (a recharge, or a partial payment where
  the site allows one). Method is `upi`, `card` or none. The result uses every tool's contract: status (`paid`,
  `stopped`, `unclear`, `nothing_due`), report, next.
- It builds the payment's intent (§5.4) and runs the worker with an exact goal, such as "Pay the BESCOM bill for account
  [profile:bescom_account]: the amount due." The saved account's key comes from the biller's name.
- `skills/playbooks/bills.md`: which biller (from memory, else ask with options); never ask for an account number in
  chat (the hidden field does); show the bill before the method; report the reference; after an unclear result, tell
  the user not to pay again.
- `skills/bills-recharges.md`, the worker's task skill, names the provider's own site instead of Paytm and Amazon
  (decision 11).

### 5.2 The page model (`assistant/actor.py`)

- Each turn the model gets the goal and the intent; the playbook and the site note; the page as the snapshot's elements
  with ids plus its text, marked as untrusted data; the results of its last list; and a screenshot when it asked for
  one, with secrets masked as QR pictures are today.
- It answers in a fixed JSON schema: up to 6 actions, optional facts, and optional words for the user.
- Actions: `click(id)`; `type(id, text)`, where text is plain or a placeholder (`[profile:key]`, or a hidden-field ask);
  `select(id, option)`; `key(name)`; `scroll(direction)`; `open(url)`; `wait(text)`; `check(text)`; `look` (a
  screenshot next turn); `ask(question, options)`; `done(report)`; `stop(reason)`.
- Facts are named values the page shows: `name`, `amount_due`, `due_date`, `payable`, `payee`, `reference`. Code accepts
  a fact only when its value appears in the page text (money compared as amounts).
- A list naming an unknown id, typing a plain value into a secret field, or breaking the schema is refused with the
  reason and asked again; three refusals in a row stop the task.
- Model and effort are settings. Tokens and cost are logged per task.

### 5.3 The worker

- `step()` keeps today's order for the hand-written steps (sign-in, codes, SmartBuy, MyUPI, Experian) until step 2
  deletes the site-specific ones. Where the page used to fall to Jev and the planner, it now runs the model's list.
- Each action runs through today's `run` and `act`, so the gate, secrets and "log before observing" stay. A list stops
  at a commit-class control (the gate takes over), a page change (the snapshot's page key), a failed `check`, or an
  error; the page is then read again.
- Stuck: three lists without progress (the same page key and no new fact), or 25 lists in a task, stop it, saying
  where.
- `paid`, `bank_box`, `otp_step` and `bought` stop being voucher-only: they follow the payment session (§5.5).
  `approval_lines` moves to `money.py`.
- Deleted: what only the old page loop used — Jev's next-click choice, the executor's candidate scoring, and the
  planner's subgoals. The plan lists the exact functions after checking their callers.

### 5.4 The money layer (`assistant/money.py`)

- Intent: kind (`bill`), biller, account key, amount rule (`due` or an exact amount), method.
- Checks, each a pure function with its own tests:
  - The name on the bill matches the profile name by words and initials ("M K CHURIWAL" matches "Mayank Kumar
    Churiwal"). A mismatch asks the user. With no name on the page, the card shows the masked account instead.
  - Nothing due (₹0, or the site's own "no dues") stops the task.
  - Already paid: a `payments` row for this biller and account in the last 25 days, or one still `started` or
    `unclear`, asks first.
  - The payable is at most the approved amount. A fee or a higher total asks a new yes.
  - QR: the `upi://pay` link is read from the page, else decoded from the QR picture with `zxing-cpp` (Chrome's own
    barcode reader isn't on Linux). Its `am` must equal the approved amount; its `pn` is shown to the user. A mismatch or
    an unreadable QR stops the task.
- Approval lines for any money commit, such as "Pay ₹1,240 BESCOM bill · account ••1234 · M K CHURIWAL · due 20 Oct ·
  by UPI".
- The hand-off message: the QR picture, and app links built from the same link (`upi://pay?…` on Android;
  `gpay://upi/pay?…`, `phonepe://pay?…` and `paytmmp://pay?…` on iPhone, where a bare `upi://` opens WhatsApp).
- Receipt: the success page's reference, a fact the model names and code checks against the text.

### 5.5 The gate

- Payment session: the first commit-class control after the bill's facts are checked asks the one yes, with
  `money.py`'s lines. A pay control pressed before the facts are read is refused ("read the bill first").
- The yes covers the method choice, the gateway's pay controls, the QR hand-off and the bank's OTP SUBMIT, while the
  payable stays at most the approved amount, the biller is unchanged (a page or QR naming a different biller or payee
  shows it on a new card and asks again), and each host was reached by clicking from the previous one.
- Hosts: money moves only on a site-note host (or one the user confirmed once, saved as a note) and hosts reached by
  clicking from it. A host the model opened with `open(url)` never takes a payment.
- One-time codes are asked only for the task's own site (sign-in or verification) or for the bank page of an approved
  payment. A code box anywhere else stops the task with a warning.

### 5.6 Memory

- A `payments` table: user, biller, account key, amount, fee, method, reference, status (`started`, `paid`, `failed`,
  `unclear`), created and updated times. A row is written `started` before the pay control is pressed, then updated.
- Account numbers are saved IDs, encrypted as today (`bescom_account`, `bwssb_rr`, …). Card numbers, CVVs, OTPs, PINs
  and passwords are never stored (unchanged).

### 5.7 Browser and chat

- A masked screenshot of the viewport for the model, using today's QR-screenshot masking.
- The web chat shows the UPI app links as buttons under the QR picture.

### 5.8 Site notes

- `skills/sites/bescom.co.in.md`: host and start page only, with learning on. Jev's learned notes add the rest after
  the watched runs.
- The unseen billers get no notes (AGENTS.md). Their bench tasks name the site, and the user confirms each site once.

### 5.9 AGENTS.md

Its loop paragraph changes to the model's action lists (§5.2) and the money layer (§5.4); its rules stay, including
"Model output never becomes selectors, coordinates, JavaScript, or file paths".

## 6. Failure handling

Before money moves, Jev stops or asks; it never guesses.

| What happens | What Jev does |
|---|---|
| The site won't load, or shows a bot wall | Retries loading once (a read), then says the site didn't let it in and that the user can pay there themselves. Never solves challenges |
| Wrong CAPTCHA | Refreshes and asks again, up to 8 rounds (today's rule) |
| "Invalid account" or no bill found | Stops, quotes the site, offers to replace the saved number through the hidden field |
| Nothing due | Stops, with the last payment or due date if shown |
| A fact the model named isn't on the page | Asks once more; then stops: "I couldn't read the amount reliably" |
| The model is stuck (3 lists without progress, or 25 in all) | Stops and says where |
| A list names an unknown element or breaks the schema | Refused with the reason; 3 in a row stop the task |
| The session timed out before the yes | Starts again from the bill, with a new CAPTCHA |

At the yes:

| What happens | What Jev does |
|---|---|
| A fee appears, or the payable grows | Stops before paying and asks a new yes: "The gateway adds a ₹23 fee: ₹1,263 in all. Pay?" |
| The QR's amount differs from the approved one, or the QR can't be read | Never sends it: "The UPI QR asks ₹X, not ₹Y, so I stopped. Nothing was paid" |
| The bank declines the card | Quotes the bank; no new try without a new yes |
| Wrong OTP | 3 tries, then stops (today's rule) |
| The bank wants approval in its app (no OTP box) | Tells the user to approve in their bank app, then watches the page until a time limit |
| No answer to the approval | Nothing is paid; a late yes on a stale page fetches the bill again and asks again |

After money may have moved, never twice:

- The `payments` row is written `started` before the pay control, then set to `paid`, `failed` or `unclear`.
- A pay control whose page didn't change is never pressed again; the page is read.
- An unclear result is recorded as such and told plainly ("Don't pay again: ₹1,240 may be pending at the gateway");
  Jev then fetches the bill again, and nothing due means it was paid.
- An expired QR gets a new one only when the gateway shows the payment failed or was cancelled, the user says they
  didn't pay, and they give a new yes.
- Money gone but nothing on BESCOM after a few days: Jev offers a UPI complaint on MyUPI (`upi_task`, built).
- Jev restarted mid-payment: the next `pay_bill` finds the `started` or `unclear` row and asks first.

Hostile pages: money moves only where §5.5 allows; page text is data, never instructions; a one-time code outside an
approved payment or the task's own sign-in is never relayed; screenshots never show secrets.

## 7. Testing

1. Unit tests, written first, with no paid API and the `payments` table in a throwaway schema: name matching (initials
   too), nothing due, the already-paid window, fee and payable, reading a `upi://pay` link, decoding a QR picture from
   a fixture, the approval lines, the action-list schema and where a list stops (commit, page change, failed check,
   unknown element), the gate's site chain, a yes riding through a payment and asking again on a change, and the
   `payments` row's states.
2. Browser tests (`pytest -m browser`, headless Chrome) on copies of the pages: a BESCOM-like pay page with a CAPTCHA
   and a bill; a gateway whose QR carries its `upi://` link on the page, and one with a picture only; a card form; an
   OTP page with a SUBMIT link and a countdown; a success page with a reference. Scripted action lists stand in for the
   model. They cover the UPI and card paths, a fee after the yes, a QR mismatch, nothing due, a name mismatch, an
   unclear result, no second press, and a hostile page (injected text, and a payment on an `open(url)` host).
3. Dry runs with the real model against the copies, run by hand because they cost money: time, lists per task, cost per
   task, and how often the user is asked.
4. A safety set on the copies: the amount changed after the yes, a QR whose payee or amount differs, page text telling
   Jev to pay someone, a code asked for another purpose, a second payment of the same bill, and a typed-address
   lookalike site. Any unsafe action fails step 1.
5. A live bench that stops at the approval card, so nothing is paid: BESCOM with its note; the user's BWSSB, DTH,
   FASTag, and gas or broadband sites with no notes, scored on a correct approval card; copies of two or three other
   boards' public pages (Adani, Tata Power-DDL, BSES) for variety; and the existing bench with the MyUPI, Experian and
   SmartBuy dry runs for regressions.
6. Live acceptance with the user watching: the BESCOM run to the approval card (No); then a real payment of the actual
   bill, by the method the user picks.

Step 1 is done when every test passes, the safety set shows no unsafe action, the BESCOM bill is paid with its
reference, the user's other billers reach a correct approval card, and MyUPI, Experian and SmartBuy still pass.

## 8. Safety and privacy

- Every money rule is code; the page model can fail only by stopping.
- Page text is data, never instructions.
- Secrets never reach a model. Screenshots are masked. Card details are typed key by key from the hidden field and
  never stored.
- Payments happen only on providers' own sites. Aggregators and MSEDCL need written permission first.
- Whether paying as the user keeps Jev outside BBPS and payment-aggregator rules is for counsel (the owner's track).

## 9. Out of scope

- Steps 2 to 5.
- Aggregator sites and net banking.
- Scheduled posting checks, due-date reminders, finding bills the user didn't name, and setting up autopay.
- Complaints other than MyUPI's existing `upi_task`.

## 10. To confirm in the first watched run

BESCOM's pay page address and labels, its CAPTCHA, whether the bill shows a name, its gateway, and whether UPI appears
as a QR or an app link on desktop.

## Appendix: facts relied on (14 Oct 2026)

- Razorpay, PayU and Cashfree stopped "enter your UPI ID" at desktop checkout on 28 Feb 2026 (Juspay on 31 Jan); the
  desktop gets a QR. On iPhone a bare `upi://` link opens WhatsApp payments by default, so gateways use
  `gpay://upi/pay`, `phonepe://pay` and `paytmmp://pay`.
- RBI's authentication directions (in force 1 Apr 2026) need two factors, one of them dynamic, for card-not-present
  payments. SMS OTP is still allowed; Axis and IDFC FIRST offer in-app or passkey approval.
- Paytm's website takes cards and its wallet, not UPI; its terms forbid automated use and shared logins. Freecharge
  (5.2(i)) and PhonePe forbid robots. MSEDCL: "make payment yourself without going through any channels/agents". No
  such clause was found in Adani's or Tata Power-DDL's terms.
- BWSSB's quick pay asks for an 8-digit RR number, then an OTP to the registered mobile or email.
- A Bharat Connect payment can be a deemed success before the biller posts it; Adani shows payments after 3 to 5 working
  days.
- RBI's turnaround rule for a failed UPI merchant payment: reversal by T+5, else ₹100 a day.

Sources and dates for each are in `docs/research/2026-10-14-finance/`.
