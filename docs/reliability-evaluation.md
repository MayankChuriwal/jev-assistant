# Local reliability evaluation

Status on 2026-09-25: locally tested implementation; **live acceptance pending**. No Task 14 live run,
paid model call, purchase, booking or external submission was performed. Attempted live sample size: **0**.
Successful live goals, live blockers, and unknown live outcomes: **0 observed**, not evidence of reliability.
Live latency and model-call statistics: unavailable. The milestone is not complete.

## Record schema

Each attempted run keeps a separate record. The following is a schema example, not a result:

```json
{
  "mode": "live",
  "commit": "actual tested git SHA",
  "goal": "user-authorized natural-language request, with personal data redacted",
  "expected_outcome": "observable requirement agreed before the run",
  "providers_checked": [],
  "trace_id": "opaque local trace identifier",
  "status": "not_run",
  "external_outcome": "not_applicable",
  "questions": {"asked": 0, "necessary": 0, "duplicate": 0},
  "calls": {"jev": 0, "coordinator": 0, "helper": 0},
  "elapsed_ms": null,
  "verification": [],
  "blocker": null
}
```

`mode` is `fixture` or `live`. Once a run starts, record the actual tested SHA and opaque trace identifier;
never use an example SHA/trace. Unstarted declarations below use null for both because no commit has been
live-tested and no trace exists. `candidate_id` is an additional declaration key. Missing goal parameters
must be resolved before the run. Zero counts in `not_run` declarations mean nothing has been attempted.

After a run, `status` is `completed`, `blocked`, `cancelled` or `failed`; external outcome is tracked separately
as `confirmed_success`, `confirmed_failure`, `unknown` or `not_applicable`. A preparation-only task may complete
with no external commitment. Cancellation after dispatch can coexist with an unknown external outcome.
Verification entries cite the actual observation/receipt, material details, amount and delivery ID as applicable,
using opaque local references and redacted descriptions. Never store credentials or raw secret inputs here.

Record every attempt, including blocked sites and product failures. A developer intervention is a failed run;
a fix and rerun creates a new record. Supported user corrections remain normal chat input. Review necessary
and duplicate questions manually against previously supplied facts; do not infer necessity from a count.
All attempted model calls, including errors and schema repairs, count. Do not substitute fixture calls for live data.

## Declared live candidates

The exact goals/holdouts are not finalized: user accounts, locations and unrelated holdout goals are still needed.
[Smoke instructions](smoke.md) classify S1–S5 as external commitments, S6 as file retrieval, S7 as comparison
preparation and S8 as recovery. H1/H2 require user selection and classification. S1 exercises fresh login;
S3 exercises session reuse where available; S7 starts without a chosen provider and requires opt-in comparison
with the separate-login/multiple-OTP notice. These are evaluation cases, never a product service whitelist.

No execution is authorized by this declaration. Each transaction still requires a complete material/fee review
and concrete approval in chat. A requested confirmed driver cannot be replaced by a finding-driver screen.
User approval for implementation did not authorize live evaluation. Step 3 remains open until inputs and
holdouts are supplied; launch/outcome/live acceptance steps remain unchecked.

```json
[
  {
    "candidate_id": "S1",
    "mode": "live",
    "commit": null,
    "goal": "Order one Diet Coke 300 ml on Swiggy Instamart at [address]",
    "expected_outcome": "Confirmed correct order, quantity, address and approved final total",
    "providers_checked": [],
    "trace_id": null,
    "status": "not_run",
    "external_outcome": "not_applicable",
    "questions": {
      "asked": 0,
      "necessary": 0,
      "duplicate": 0
    },
    "calls": {
      "jev": 0,
      "coordinator": 0,
      "helper": 0
    },
    "elapsed_ms": null,
    "verification": [],
    "blocker": "Live evaluation not authorized; goal parameters and accounts/holdouts pending"
  },
  {
    "candidate_id": "S2",
    "mode": "live",
    "commit": null,
    "goal": "Book an Uber from [pickup] to [destination], with a confirmed driver",
    "expected_outcome": "Confirmed driver/car/plate, correct route/class and agreed fare evidence",
    "providers_checked": [],
    "trace_id": null,
    "status": "not_run",
    "external_outcome": "not_applicable",
    "questions": {
      "asked": 0,
      "necessary": 0,
      "duplicate": 0
    },
    "calls": {
      "jev": 0,
      "coordinator": 0,
      "helper": 0
    },
    "elapsed_ms": null,
    "verification": [],
    "blocker": "Live evaluation not authorized; goal parameters and accounts/holdouts pending"
  },
  {
    "candidate_id": "S3",
    "mode": "live",
    "commit": null,
    "goal": "Order [dish] from [restaurant] on Swiggy",
    "expected_outcome": "Confirmed correct dish/options/address and approved final total",
    "providers_checked": [],
    "trace_id": null,
    "status": "not_run",
    "external_outcome": "not_applicable",
    "questions": {
      "asked": 0,
      "necessary": 0,
      "duplicate": 0
    },
    "calls": {
      "jev": 0,
      "coordinator": 0,
      "helper": 0
    },
    "elapsed_ms": null,
    "verification": [],
    "blocker": "Live evaluation not authorized; goal parameters and accounts/holdouts pending"
  },
  {
    "candidate_id": "S4",
    "mode": "live",
    "commit": null,
    "goal": "Order [item/variant/quantity] on Amazon.in",
    "expected_outcome": "New order/reference for exact listing, quantity, destination and approved charge",
    "providers_checked": [],
    "trace_id": null,
    "status": "not_run",
    "external_outcome": "not_applicable",
    "questions": {
      "asked": 0,
      "necessary": 0,
      "duplicate": 0
    },
    "calls": {
      "jev": 0,
      "coordinator": 0,
      "helper": 0
    },
    "elapsed_ms": null,
    "verification": [],
    "blocker": "Live evaluation not authorized; goal parameters and accounts/holdouts pending"
  },
  {
    "candidate_id": "S5",
    "mode": "live",
    "commit": null,
    "goal": "Submit a complaint about [issue] using [official organisation form]",
    "expected_outcome": "New submission reference for correct details and recipient",
    "providers_checked": [],
    "trace_id": null,
    "status": "not_run",
    "external_outcome": "not_applicable",
    "questions": {
      "asked": 0,
      "necessary": 0,
      "duplicate": 0
    },
    "calls": {
      "jev": 0,
      "coordinator": 0,
      "helper": 0
    },
    "elapsed_ms": null,
    "verification": [],
    "blocker": "Live evaluation not authorized; goal parameters and accounts/holdouts pending"
  },
  {
    "candidate_id": "S6",
    "mode": "live",
    "commit": null,
    "goal": "Download my latest invoice from [provider/account] and send it here",
    "expected_outcome": "Correct invoice identity/period/account, execution binding and actual chat document",
    "providers_checked": [],
    "trace_id": null,
    "status": "not_run",
    "external_outcome": "not_applicable",
    "questions": {
      "asked": 0,
      "necessary": 0,
      "duplicate": 0
    },
    "calls": {
      "jev": 0,
      "coordinator": 0,
      "helper": 0
    },
    "elapsed_ms": null,
    "verification": [],
    "blocker": "Live evaluation not authorized; goal parameters and accounts/holdouts pending"
  },
  {
    "candidate_id": "S7",
    "mode": "live",
    "commit": null,
    "goal": "Compare [item] prices, including all fees, and let me choose providers",
    "expected_outcome": "Explicit comparison consent and evidence-backed comparable quotes, no purchase",
    "providers_checked": [],
    "trace_id": null,
    "status": "not_run",
    "external_outcome": "not_applicable",
    "questions": {
      "asked": 0,
      "necessary": 0,
      "duplicate": 0
    },
    "calls": {
      "jev": 0,
      "coordinator": 0,
      "helper": 0
    },
    "elapsed_ms": null,
    "verification": [],
    "blocker": "Live evaluation not authorized; goal parameters and accounts/holdouts pending"
  },
  {
    "candidate_id": "S8",
    "mode": "live",
    "commit": null,
    "goal": "Resume [blocked task] after [supported correction]",
    "expected_outcome": "Fresh evidence for remaining goal with no duplicate mutation",
    "providers_checked": [],
    "trace_id": null,
    "status": "not_run",
    "external_outcome": "not_applicable",
    "questions": {
      "asked": 0,
      "necessary": 0,
      "duplicate": 0
    },
    "calls": {
      "jev": 0,
      "coordinator": 0,
      "helper": 0
    },
    "elapsed_ms": null,
    "verification": [],
    "blocker": "Live evaluation not authorized; goal parameters and accounts/holdouts pending"
  },
  {
    "candidate_id": "H1",
    "mode": "live",
    "commit": null,
    "goal": "User-selected unrelated holdout pending",
    "expected_outcome": "User to define before run",
    "providers_checked": [],
    "trace_id": null,
    "status": "not_run",
    "external_outcome": "not_applicable",
    "questions": {
      "asked": 0,
      "necessary": 0,
      "duplicate": 0
    },
    "calls": {
      "jev": 0,
      "coordinator": 0,
      "helper": 0
    },
    "elapsed_ms": null,
    "verification": [],
    "blocker": "Live evaluation not authorized; goal parameters and accounts/holdouts pending"
  },
  {
    "candidate_id": "H2",
    "mode": "live",
    "commit": null,
    "goal": "User-selected unrelated holdout pending",
    "expected_outcome": "User to define before run",
    "providers_checked": [],
    "trace_id": null,
    "status": "not_run",
    "external_outcome": "not_applicable",
    "questions": {
      "asked": 0,
      "necessary": 0,
      "duplicate": 0
    },
    "calls": {
      "jev": 0,
      "coordinator": 0,
      "helper": 0
    },
    "elapsed_ms": null,
    "verification": [],
    "blocker": "Live evaluation not authorized; goal parameters and accounts/holdouts pending"
  }
]
```

## Fixture evidence

The Task 13 baseline `e8c72d9f775fb486393008f047c3a4125f9296be` passed 718 offline and 163 real headless-Chrome
fixture tests, Ruff and both JavaScript syntax checks. These figures are deterministic scripted-model checks,
not a live sample. Task 14 runtime commit `0951818ab776610796fc17e3afde9a3db3bbc5b5` adds eight production
PDF export boundary tests. Final checks on that source state: `uv run ruff check .` passed;
`uv run pytest -q` **718 passed / 171 deselected** (9.60s); `uv run pytest -m browser -q`
**171 passed / 718 deselected** (97.99s); both `node --check assistant/snapshot.js` and
`node --check assistant/static/app.js` passed; `git diff --check` passed.

The first full browser attempt had 169 passes and two failures. New PDF tests polluted the shared download
folder, breaking an older exact filename assertion; per-test download directories fixed that test isolation
error. The dispute upload flow also intermittently ended blocked despite its fixture result. No unrelated
runtime gate was weakened: the focused export/chat/upload rerun passed 10/10 and the final full suite passed.
The intermittent upload result remains a test-stability observation, not a discarded successful-run denominator.

Existing staged journeys cover provider choice/comparison/skip, separate OTPs, quantities and promotion
tradeoffs, changed fees, final approval, one dispatch, processing/unknown outcomes and conservative page
confirmation. `tests/test_chat_e2e.py` checks real PDF bytes and chat delivery using a **fixture-owned** receipt
adapter; it must not be cited as proof of a generic production artifact adapter. `tests/test_e2e.py` checks
fixture file contents and submission. Lower-level Core tests cover retained blocked resume and cancellation;
complete outside-chat-auth alternatives and every blocked-resume permutation remain unevaluated whole flows.

## Production artifact scope and open gaps

The narrow production export path supports an explicit request such as **“Save this page as PDF.”** Jev must
select SAVE_PDF from the observed page. Chrome's print response produces the bytes; the browser keeps a private
UUID/hash receipt and only exposes a public event when the source page remains fresh. The executed journal
record precedes the post-dispatch observation. Exactly one artifact requirement must match current goal-material
evidence. Core supplies the actual opaque media ID and records successful channel delivery; changed bytes,
stale task revision, mismatched material, absent delivery or an invoice-retrieval goal cannot attest completion.
This proves a PDF export of the current page, not that a service-issued invoice was found or that every print
stylesheet preserves the same visual layout. Ambiguous or multiple artifact requirements remain unverified.

General downloads still arrive through real Chrome download events, but the existing ownership map binds a
GUID to a browser/task, not unambiguously to a particular selected operation and goal material. Timing alone,
a suggested filename, a matching word or an arbitrary page label cannot safely fill that gap. No generic
invoice/download success adapter was fabricated. Requested invoice identity/period/account need stronger evidence.

`DOM.setFileInputFiles` and drag/drop show browser attachment, not server acceptance. An upload control can accept
bytes while a later request fails or submits a different document. General server upload completion therefore
remains unverified until an actual transfer receipt is tied to the execution, supplied media and requirement.
Final form submission is a separate action requirement. No model path, selector or website-specific script
is allowed to bridge either gap. These are open implementation prerequisites, not external site blockers.

## Trace accounting and acceptance gate

`uv run jev traces` preserves its existing summary (status, questions, steps, elapsed time, quoted evidence).
The full trace also includes structured task evidence, diagnostics and progress counters for coordinator/Jev
budget reservations, including failed attempts. Those counters are not guaranteed transport call totals.
Field `text_calls` includes cached field resolutions and omits some helper jobs: its length is **not** a helper
API-call count. Historical traces cannot reliably reconstruct all helper attempts or every question reopen.
Call/reask accounting requires additional instrumentation before a live acceptance report can claim exact totals;
use null for unavailable actual-run counts, not invented zeroes. No trace-summary change was made in Task 14.

Every declared feasible goal needs a verified successful live run. Any unauthorized/duplicate action, secret
leak, false success or unnecessary reask prevents acceptance until fixed and freshly evaluated. Keep unsupported
wording, history/contradictory confirmation pages, missing material and ambiguous fees unknown rather than
claiming success. Unresolved product failures and missing runtime adapters remain work. A finite successful
sample would still not establish that every website works.
