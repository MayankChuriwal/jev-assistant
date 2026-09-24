# Jev Assistant — general-purpose local agent reliability

Date: 2026-09-24

Status: written spec approved by the user on 2026-09-24; implementation planning.

This replaces the behavioral and orchestration portions of
[the original design](2026-09-23-jev-assistant-design.md). This document
takes precedence wherever the designs differ. The existing browser implementation
remains the foundation; this document does not claim that the changes are implemented.

## 1. Intent and agreed scope

Build a general-purpose assistant that accepts an open-ended natural-language request,
uses a browser when needed, asks for missing information, and completes the request
with observable evidence. The intended audience includes people who want the assistant
to do the browsing for them. Interaction is through the existing local text chat.

The user explicitly chose:

- Local reliability first. Server hosting, isolated customer sessions, and WhatsApp
  follow in separate work.
- General capability, with no supported-service whitelist or fixed list of supported
  tasks. Uber and shopping examples are evaluation cases, not product boundaries.
- Every user interaction stays in chat. No browser takeover, visiting another website,
  banking-app approval, device authentication, or instructions to click on the site.
- Text input for now. The assistant can send CAPTCHA images, choices, and documents
  through chat; speech recognition and voice messages are outside this milestone.
- Informative, conversational updates explain useful findings, options, trade-offs,
  and interruptions. Review the complete cart or booking breakdown before approval,
  and offer evidence-backed savings suggestions without silently changing the request.
- GPT-4.1 mini coordinates the task; Jev selects browser operations and targets;
  application code enforces execution, information handling, and approval rules.
- Final confirmation before purchases, bookings, messages, form submissions,
  cancellations, deletions, and account changes.
- Card details, CVVs, and OTPs are temporary. Reusable login credentials can be saved
  in the vault only with explicit user consent.
- Reuse information already supplied. Ask again only for a real correction,
  expiration, changed request, or unresolved ambiguity.
- Use the requested provider or an explicitly saved default. When neither is known,
  ask for a preferred provider or offer comparison; explain that comparison may
  require separate logins and multiple OTPs. Do not compare automatically.
- Offer another service when the requested one is unusable, but switch only after
  the user agrees. Preserve progress and report the blocker when no path exists.

Generic does not mean claiming completion when a website offers no usable path. A
request that requires an outside-chat action ends with an accurate blocker after
available browser-compatible alternatives have been considered.

### Existing scope retained

Keep the dedicated headed Chrome profile, local web channel, upload/download support,
and one-user deployment. Keep the existing limit of three concurrent tasks and serialize
tasks sharing an account/site. Browser logins and saved non-secret profile information
remain available between tasks. Active tasks are not automatically resumed after an
application restart in this milestone; restart handling is specified in Section 10.

No new website adapters, service-specific scripts, prepared navigation plans, or
hardcoded field values. Do not add a stronger fallback model or a new hosted browser
dependency as part of this design.

## 2. Evidence and choice of approach

The inspected baseline is assistant commit `22c4e45` and Ultrafast commit `1231850`.
Ultrafast sends the original goal, visible page, indexed elements, and recent history
directly to Jev. Jev chooses the operation and corresponding target in one request.
Mercury supplies a field value only for `TYPE_TEXT`; it does not plan or supervise.
The assistant already substitutes GPT-4.1 mini and adds conversation and browser breadth.

The chosen approach preserves the browser layer and restructures coordination. Starting
again from Ultrafast would discard working frame, event, dialog, file, and chat support.
More prompt patches alone would leave the underlying state and evidence problems intact.

Baseline validation: 325 offline tests and 83 browser-fixture tests passed, as did Ruff
and both JavaScript syntax checks. Models in those tests are scripted. Passing them
does not measure live-model navigation reliability.

Read-only offline probes reproduced these gaps in the current implementation:

| Gap | Replacement rule |
|---|---|
| Login can remain pending when a sign-in banner persists despite account evidence | Explicit authentication state, tied to current origin and evidence |
| Selecting a filled field again implies rejection | Count rejection only after submission and observed rejection evidence |
| An OTP fallback can use an unrelated unused secret | Bind every secret to purpose, origin, challenge, and permitted field |
| An old amount elsewhere on the page keeps an approval valid after the total changes | Bind approval to a full transaction proposal and its current evidence |
| A numeric text CAPTCHA is parsed as tile numbers | Explicit challenge type and typed response schema |
| A non-commerce task can be marked done without evidence or the expected file | Verify all completion requirements before `done` |

Historical traces additionally show repeated questions, a false completion citing a
ride-request screen, and three consecutive invalid confirmation cards. They span older
revisions and are diagnostic material, not a current success-rate benchmark. The
confirmation traces omit rejected model output, so their precise validation failures
cannot be reconstructed from those traces alone.

## 3. Responsibilities and boundaries

| Component | Owns | Must not do |
|---|---|---|
| GPT-4.1 mini coordinator | Interpret user intent, maintain the current objective, extract evidence-backed facts, identify missing information and material choices, assess progress, propose semantic recovery | Select executable browser operations/targets, emit selectors/code/coordinates, invent user data, grant approval |
| Jev decision policy | Choose one offered operation and its operation-specific target(s) from the current observation | Execute unused target heads, invent targets, bypass approval or secret binding |
| Application state and gates | Validate records, route replies, reuse facts, manage challenges, determine allowed execution, record results and enforce budgets | Silently substitute another browser action when a model decision is rejected |
| Browser adapter | Observe, expose code-owned references, validate freshness/occlusion, execute once, collect browser events and files | Interpret the user's goal or retry an uncertain mutation |
| Chat channel | Present questions, masked secret input, confirmations, CAPTCHA images, status and results | Require the user to operate a browser or another app |

Keep the existing browser adapter and snapshot reader, with targeted changes where
observation quality or execution correctness requires them. Split policy responsibilities
out of the large agent module using these focused boundaries:

- `state.py`: task contract, facts, requirements, challenge records, observation references,
  and validated state transitions. No browser or network calls.
- `coordinator.py`: GPT coordination events, request/response schemas, evidence validation,
  and application of permitted state updates. No browser mutations.
- `interaction.py`: information requirements, question identity, answer matching,
  challenge-scoped temporary secrets, and question lifecycle.
- `commit.py`: proposals, approvals, execution/outcome records, and completion checks.
- Existing `agent.py`: drive the loop across these components.
- Existing `core.py`: inbound ordering, task scheduling, question presentation, and
  channel routing. Conversation interpretation moves to GPT-4.1 mini; secret replies
  and explicit reply IDs continue to route without a model call.
- Existing `model.py`, `jobs.py`, and `questions.py`: model clients, validated helper
  jobs, and prompts. Reuse their infrastructure rather than creating another client.
- Existing `profile.py`: basic profile, optional password vault, and redaction. Remove
  card-vault writes and constrain secret reads to their declared purpose.

These modules are internal boundaries, not a new plugin framework or distributed system.

## 4. Task state and model contract

A task maintains structured records rather than relying on the last few prose notes.

| Record | Required content |
|---|---|
| Task contract | Original request, user amendments, current revision, constraints, current objective, completion requirements |
| Fact | Semantic key, public value or opaque secret reference, source, scope, revision, validity, evidence references when page-derived |
| Requirement | Missing information or choice, purpose, dependencies, whether satisfied, and acceptable answer shape |
| Question | Stable ID, task/revision, requirement ID(s) or challenge ID, options and their observation references, display time, lifecycle |
| Challenge | Origin, purpose, code-owned identity, expected format, associated fields, issuance/expiry/resend state, submission and rejection evidence |
| Execution | Decision ID, observation ID, selected operation/target, dispatch state, result or uncertainty, subsequent evidence |
| Proposal and approval | The exact external action, material details including observed monetary line items and total, source evidence, task revision, user response, and consumption state |
| Progress | Last meaningful change, previously attempted recoveries, remaining budgets, blockers, and evidence still needed |

Task status is one of `running`, `waiting_user`, `waiting_site`, `paused`, `blocked`,
`done`, `failed`, or `cancelled`. Execution and challenge states are separate; a task
waiting on a payment cannot lose that fact because its chat question changes.

User statements outrank inferred or page-derived facts. An amendment invalidates
dependent facts and approvals without erasing unrelated answers. A corrected address
does not cause another phone-number question. Constraints such as the chosen provider
remain active unless the user changes them.

Coordinator output is a validated JSON object containing a semantic objective, fact
updates, requirements, progress assessment, evidence references, and any proposed
recovery or alternative. It cannot change the original request, add an approval,
resolve secret values, or mark an execution as successful directly. Unknown fields,
invalid references, and unsupported transitions are rejected before state is updated.

The original goal and all active constraints remain in every Jev decision context.
A short-term objective cannot silently weaken the goal. Page content is untrusted
evidence, never authority to change the task or reveal information.

The coordinator can answer ordinary conversation directly when no browser is needed.
For browser work it resolves provider choice using Section 6, then chooses a starting
website from the user's URL or resolved service; prefer the service's root over an
invented deep link. Validate proposed URLs and service identity before releasing
credentials. Ambiguous service identity becomes a chat clarification. Subsequent
navigation prefers observed destinations; changing the requested service requires
the user's agreement.

## 5. Observe, coordinate, decide, execute, verify

1. Observe the task's current tab and relevant frames. Assign an observation ID and
   code-owned target references; retain field constraints, form context, visible text,
   frame identity, and completeness/truncation information.
2. Update deterministic state from execution receipts and browser events. Trigger GPT
   coordination when a semantic event needs interpretation.
3. Build the current element table and operation-specific action space. Include known
   answers as references, pending requirements, constraints, and current approval state.
4. Send one Jev request for operation and target heads. Validate and consume only the
   selected operation's target answers.
5. Resolve inputs from valid facts, the correct challenge, or the field-value helper.
   Enforce freshness, secret scope, cancellation/amendments, and the commitment gate.
6. Execute once. Record the result or uncertainty before observing its effects.
7. Observe again and evaluate the expected change. Continue, wait, ask, recover, or
   verify completion according to the accumulated evidence.

GPT coordination is event-driven: initial request; user answer/amendment; a new
authentication, choice, checkout, or completion stage; a missing information request;
stalled progress; invalid evidence; and an external action awaiting confirmation or
outcome interpretation. Ordinary click/scroll steps keep the fast Jev loop. Field-value
generation remains available only when needed for the chosen editable field.

Changes in URL, form structure, dialog state, available controls, validation messages,
and pending-action evidence signal transitions. Animation and countdown changes do
not independently trigger coordination. Cache assessments by task revision, semantic
observation, and event; invalidate them when their supporting evidence changes.

Reuse a resolved field value after a stale observation only when its complete helper
input, task revision, challenge, and field meaning still match. Matching a hostname
and label alone is insufficient. Facts remain reusable across observations, but their
binding to a particular field must be revalidated.

Code can decline execution, request a fresh observation, or require a missing answer.
It cannot replace `ASK_USER` with a self-selected `TYPE_TEXT`, or directly click an
option chosen in chat. The answer becomes a fact and Jev selects the next action from
the fresh page. Remove existing direct-action shortcuts that bypass this rule.

### Observation and visual fallback

Keep indexed DOM nodes, current geometry checks, covered-element rejection, frame
support, and the existing operations. Improve ambiguous field labels using observed
form/section/placeholder context. Surface omitted controls instead of treating a
truncated observation as proof that a control does not exist.

Visual fallback follows the same selection authority. Code creates screenshot/grid
references; GPT may annotate only supplied references with visible labels and semantic
descriptions. Jev chooses any eventual browser target. GPT's image interpretation
cannot itself cause a click. Re-observation and refinement remain bounded, and a
visual candidate is invalidated when the supporting screenshot state changes. The
executor alone converts a selected code-owned reference into geometry. Uncertain
visual targets cannot authorize a commitment.

## 6. Questions, answers, and choices

Ask for information only after checking the goal, amendments, valid task facts, profile,
and current website evidence. Questions name the actual missing detail. Never ask the
user which page to open or how to operate the site.

A question is identified by its underlying requirement(s), not its wording. Rephrasing
does not create a new question. An answered requirement is reopened only with a recorded
reason: expiration, observed validation failure, changed request, invalid format, or
genuine ambiguity. If Jev requests an already-satisfied fact, refresh its context and
redecide; repeated requests invoke recovery rather than another question to the user.

Keep one active question in chat. Do not silently redirect an answer because a different
task has a more urgent question. Incoming reply IDs bind directly to the displayed
question. Secret input carries that question/challenge identity and never goes through
language-model intent classification. A normal text reply that could satisfy several
requirements gets one concise clarification rather than a guessed assignment.

Serialize inbound events per user and deduplicate message IDs before routing or starting
tasks. Old buttons and duplicate replies produce a brief acknowledgement rather than
another browser action. Start the 15-minute pause and 24-hour cancellation timers when
the question is first displayed, not when it is queued.

Material choices use observed options, including price, quantity, timing, location,
and payment differences. Use an explicit request or saved preference when it resolves
the choice unambiguously; otherwise ask. Do not infer a choice from a shared word such
as a service name or an overlapping label. Store the selected option's meaning and
evidence, not only its temporary element number. Revalidate after the user replies.

Basic profile details and website sessions retain existing reuse behavior; lasting
preferences have an explicit remember action. Every fact retains its origin so a
one-off answer cannot silently become a global preference.

### Informative conversation and useful suggestions

Use natural, informative chat messages that explain what matters to the user's goal.
At the start, state the understood task and any applicable saved defaults being used.
A saved address is not automatically a default address. When only previous-order
history is available, offer its provider/address as a suggested choice and ask which
to use. Closely related non-secret requirements can share one question, for example:
"Use the same provider and delivery address as last time, or change either?" Record
each answer separately; a partial answer leaves only the missing requirement open.
Offer comparison with the Section 6 login notice when provider choice is unresolved.
Do not make the user reconfirm explicit current instructions or saved defaults.

Send useful updates at meaningful stages: starting work, finding options, encountering
a relevant delay or login requirement, preparing the final review, and verifying the
outcome. Include observed details and explain what happens next. Deduplicate updates
for the same state; avoid a message for every click, repeated "working on it" messages,
or speculative claims of progress. Reuse coordinator assessments and verified task
facts for these messages rather than adding a separate model call per browser step.

Options should include decision-relevant differences, such as brand/type, pack size,
quantity, price, stock, and delivery estimate for groceries, or vehicle class, capacity,
fare and pickup estimate for rides. Present a manageable selection and offer more when
useful; distinguish a shortlist from an exhaustive search. Explain relevant trade-offs
and ask one clear next question. Never substitute a different variant or increase
quantity merely because it appears cheaper or more convenient.

Proactively surface observed discounts, delivery thresholds, bundles, or other savings
opportunities when relevant. Preserve the site's actual eligibility terms: threshold
operator, qualifying subtotal, exclusions, membership/payment requirements, and validity
when shown. Free delivery is not a free order and does not imply that every fee is zero.
Compute eligibility using verified amounts and quantities in application code. A pair
of items priced at 95 each totals 190 and does not meet a 199 threshold.

For a proposed larger basket, explain both the fee reduction and the additional total
spend using comparable, verified totals where available. Do not describe buying more
as saving money overall when the final payable amount increases. If a total or benefit
cannot be verified yet, describe it as a possible benefit to check, not a guaranteed
discount. Ask before adding unrequested quantities/items, substituting products, opting
into paid memberships, or changing payment choices. A request to evaluate an alternative
basket permits reversible preparation and inspection; it does not approve its purchase.
Recheck any selected benefit on the final cart/checkout page.

### Provider selection and comparison

Resolve provider choice before beginning a provider-dependent task:

1. Follow the provider or comparison scope explicitly requested in the current task.
2. Otherwise use an explicitly saved default that applies to the request. A previous
   one-off choice or an available login does not establish a default.
3. When neither resolves the choice, ask for a preferred provider or offer comparison.
   Include the login implications in that question. For example: "Do you have a
   preferred cab service, or would you like me to compare? Comparing may require
   separate logins and more than one OTP."

These are generic rules, not fixed service menus. Accept a provider named in free
text. Discover comparison candidates relevant to the goal and location, validate
their identity, and report which providers were actually checked. Do not claim that
every possible provider was compared. If location or another fact is needed to find
relevant candidates, reuse it or ask for it through the normal requirement flow.

Choosing a provider proceeds with that provider. Choosing comparison authorizes
checking relevant providers and presents the separate-login/multiple-OTP notice
before any new login challenge. If the original request already asks to compare,
give that notice and proceed without asking the same choice again. No additional
confirmation is needed merely to start the requested comparison.

Reuse valid sessions and public information where available. New logins are requested
only when needed, with one active question at a time. Every OTP prompt names its
provider and purpose, and every answer remains bound to that provider's authentication
flow and challenge. Do not promise a particular OTP count. The user can skip a provider;
report its quote as unavailable rather than repeatedly requesting login or inventing
a price. Comparison consent does not authorize account creation or changed account
terms; those remain subject to the commitment rules.

Compare offers on matching task details: for example, the same route, time, and
vehicle requirements, or the same product, pack size, quantity, and delivery address.
Show differences when offers are not equivalent. Include observed total charges and
arrival/delivery estimates; label estimates, missing fees, and unavailable quotes.
Do not call an incomplete price the cheapest final total. Let the user select an
offer unless an explicit instruction or saved preference resolves that choice.

Comparison and offer selection are separate from final booking/order approval.
Recheck the chosen offer's material details before commitment under Section 8.
Neither comparing nor choosing a provider creates a lasting preference unless the
user explicitly asks to remember it.

## 7. Authentication, temporary secrets, and CAPTCHA

### Authentication

Authenticate when the request requires account access or the site blocks progress on
authentication. A sign-in link alone does not require logging in to perform a public
search. Prefer a visible browser-compatible path; inspect available methods instead
of assuming phone and OTP are always supported.

Track authentication as `unknown`, `required`, `challenge`, `submitted`, `authenticated`,
`rejected`, `expired`, or `unsupported`. A typed OTP is not proof of login. A persistent
sign-in banner is not proof of failure. Require evidence relevant to the requested
account access, such as the completed challenge together with an authenticated account
view or an accessible account-dependent page. Host transitions must be associated with
the actual authentication flow, not treated as unrelated challenges or universal trust.

After submission, wait for relevant evidence before asking again. Count attempts only
after an observed rejection of that submission. Keep the existing three-rejection
limit for an authentication flow and two for CVV verification, stopping sooner if
the website shows a stricter limit. Resending creates a new challenge and invalidates
the prior code without resetting the flow's rejection count. Format errors are
explained before typing and do not consume attempts.

### Secret handling

The models see opaque references and metadata, never secret values. The resolver may
release a reference only for its bound task, challenge/purpose, permitted origin and
field role. There is no “newest unused secret” fallback and no model-generated OTP,
password, CVV, or card number.

Temporary card details, CVVs, and OTPs stay in a dedicated in-memory store. A dispatched
submission consumes its secret references; they cannot be replayed while its outcome
is pending. Expiration, challenge replacement, confirmed use, or task termination
invalidates and removes the values. Use the website's observed expiry when available,
capped at ten minutes after receipt for codes and fifteen minutes for payment-entry
details; these caps are also the defaults when the website supplies no expiry.
Completed task objects and redaction registries must not retain raw temporary values.
Sanitize retained task records before removing redaction entries. Redact records before
persistence, including failed model responses and errors.
Do not serialize secrets into traces, retained chat history, screenshots, file names,
or diagnostic dumps. Clear the chat secret composer after submission.

Do not save new cards or CVVs in the local vault. Existing merchant-saved cards are
selected through their displayed labels; new card entry is temporary for that payment.
The application must reject legacy card-vault references so previous saved CVVs are
not reused. Provide an explicit cleanup action for legacy local card secrets instead
of deleting unrelated Keychain entries automatically. Optional password saving uses
a separate explicit consent, scoped to the login origin. Vault access must not trigger
an interactive system prompt during a task. If noninteractive access is unavailable,
fall back to temporary masked chat entry or report the blocker.

PCI prohibits storing card verification values after authorization, even encrypted:
[PCI SSC FAQ 1280](https://www.pcisecuritystandards.org/faqs/1280/).

### CAPTCHA

The assistant presents a masked challenge image in chat and asks the user to solve it.
The model must not solve the CAPTCHA. Challenge kinds are explicit: `text`, `tiles`,
or `unsupported`. A text response consisting of digits remains text. Tile responses
use code-owned labels bound to the displayed challenge revision. If the challenge
changes while the user answers, reject stale coordinates and show the current image.

The human answer constrains Jev's next action; code does not silently select or click
tiles outside Jev's decision loop. Confirmations or verification controls are handled
as observed controls. Preserve the existing eight-round challenge budget. A challenge
requiring an unsupported gesture is a blocker, not a request for browser takeover.

### Location and outside-chat requirements

Collect a pickup/delivery location through chat or reuse an explicitly selected saved
address. Do not treat the server's or Mac's location as the customer's location. If
“current location” is requested without a reliable user-provided location, ask for an
address or landmark in chat. No task should depend on a native permission dialog.

For app approval, passkeys, biometrics, or other outside-chat requirements, inspect
visible alternative authentication/payment methods. If none works through chat and the
agent browser, explain the blocker and offer a permitted alternative service. Do not
ask the user to leave chat. Do not create a second order or switch services while the
outcome of an earlier commitment is unresolved.

## 8. Confirmation and external effects

Navigation, searching, comparison, ordinary form preparation, and authentication for
the requested account proceed without a separate approval for each step. Actions that
purchase, book, send, submit externally, cancel, delete, or change an account require
an explicit confirmation. If editing itself commits an account change, gate the edit.

Detect commitments using action/form context, coordinator risk assessment, Jev's
`CONFIRM` choice, and code backstops. Do not rely on button-label regexes alone. A
submission-capable action with unresolved effects must be assessed before execution.

Construct a proposal containing the action type, service/account, recipient or
destination, item/quantity or message/form content, selected option, timing, payment
method, and current total/currency where applicable. Include evidence references for
material page-derived details. Missing material details cause further observation or
a specific question, not a fabricated summary.

### Cart, fees, and final review

Inspect the actual cart or booking review and relevant charge details before asking
for final approval. A product listing price, promotional banner, or offer selection is
not evidence of the final payable amount. Jev selects any operations needed to reveal
collapsed fee details and reach the review page through the normal loop.

Retain structured monetary lines with their displayed labels, signed amounts, currency,
whether they are included in another amount, and supporting observation references.
Keep item prices and quantities, subtotal, applied discounts, delivery/shipping, platform,
handling/packing, small-order, surge, separately charged tax, tip, other charges, and
rounding when present. This is an illustrative list, not a fixed fee schema: preserve
unfamiliar charges too. Do not double-count tax already included in an item price or
claim an absent/unread fee is zero. Show conditional or excluded later charges, such
as an estimated toll, separately from the currently payable total.

Application code checks monetary arithmetic in exact currency units against the
site's displayed total. If the breakdown is incomplete or does not reconcile, inspect
further and explain what is unresolved. Do not invent a balancing fee or present an
unverified exact total for approval. A site that explicitly quotes an estimate can
be summarized as an estimate with its observed variable-charge terms; the proposal
and user approval must retain that uncertainty.

Present a readable chat review containing the full basket or booking, provider,
delivery address or route, timing/estimate, each observed charge and discount, final
payable total, and selected payment method. Include all items already in the cart;
surface unrelated items and ask how to handle them before proceeding. Do not silently
remove them or include them in the requested purchase. Identify optional additions
such as tips, donations or paid memberships; do not accept defaults as user consent.
Use masked merchant-saved payment labels and never show secrets in this review.

An offer-selection reply or agreement to add another pack is not purchase approval.
After the chosen cart is ready, one clear confirmation of its full proposal authorizes
the commitment. A previously approved proposal is revalidated using the same review
requirements if the site changes a discount, charge, quantity, destination or method.

### Approval binding and execution

Show the proposal in chat. Record approval against task revision and a hash of its
material details, not against the presence of an amount anywhere on the page. After
the reply, read the page again and verify those details. An item price of ₹79 cannot
validate a previously approved ₹79 total if the new total is ₹95. A changed address,
recipient, quantity, payment method, service, or materially changed option also
invalidates approval even when the amount is unchanged.

A cosmetic re-render with the same verified proposal does not require another question.
A changed proposal or separate commitment does. Approval can authorize only one
commitment dispatch and is consumed before dispatch can be attempted again. If dispatch
fails ambiguously, it remains consumed until outcome reconciliation establishes what
happened; the mutation is never retried automatically.

OTP verification following a payment remains bound to that authorized transaction.
It cannot authorize a new purchase, changed total, or unrelated account operation.
Declining asks only what remains unclear about the requested change; a clear “cancel”
stops without an extra questionnaire.

## 9. Progress, recovery, and completion

Progress means an observed change relevant to the objective: a field accepted, an
autocomplete option selected, authentication completed, a requested result appearing,
a proposal prepared, or an external action confirmed. A new screenshot fingerprint,
elapsed time, or an executed click alone does not establish progress.

After three consecutive non-wait actions without relevant change, or a repeated cycle
through the same semantic state/action, request a coordinator assessment. Read-only
waiting uses elapsed-time bounds and useful browser events; an unchanged WAIT loop
must not spend the decision budget as fast as the model can respond.

Recovery can propose a new semantic objective, inspect missing regions, revisit an
observed route, or resolve a factual ambiguity. Jev still selects each executable
operation. Permit at most two distinct unsuccessful recovery approaches for the same
objective and semantic state before explaining the blocker or offering alternatives.
Do not ask the user to troubleshoot navigation. Retain the 150-action/300-Jev-decision
task bounds; add a 60-assessment coordinator budget, separate from field/question jobs.
Budget exhaustion produces a progress summary and a blocked state, never completion.

Invalid model output executes nothing. Allow one repair request with a precise,
redacted validation error and fresh evidence as necessary. Do not repeat an identical
invalid confirmation job three times without explaining or recording its failed
validation rule. Provider/network retries are bounded read-only request retries;
they never retry browser mutations.

After an external action, classify evidence as `pending_user`, `processing`, `success`,
`failed`, or `unknown`. Pending user steps must still satisfy the chat-only constraint.
Watch processing for up to five minutes with bounded observations and one meaningful
status update. If no conclusive result appears, report an unknown outcome. Never
describe “finding a driver” as driver confirmation or an uncertain payment as failed.

At task creation, derive completion requirements from the request, not a verb list
such as “book/order/pay.” Update those requirements only when the user changes the
request. A shopping comparison can finish without purchasing; a booking task cannot
finish at its quote screen. Required evidence is goal-specific and generic in schema:
page quotes with provenance, relevant observed state, confirmed action outcomes, or
an actual downloaded/uploaded artifact and delivery receipt.

Jev's `DONE` initiates verification. Code checks every requirement against validated
evidence and the execution ledger. GPT may interpret evidence but its boolean alone
does not establish success. Missing proof produces further observation or an explicit
unverified result with `blocked` status, never `done`. Historical account records
must not be mistaken for the result of the current task.

Implementation decision (2026-09-25): a site may provide a confirmation page without
a structured transaction receipt. For an approved, single-dispatch action, a new
terminal reference and goal material may establish success if they appear in two
consecutive post-dispatch observations of the same page, were absent before dispatch,
and any exact payable total matches the approved proposal with no conflicting total.
The coordinator must cite the current page lines; code checks their provenance,
terminal meaning, material details, amount, stability, and execution ownership before
recording success. Processing text, old history, unrelated confirmations, or ambiguous
amounts remain unknown. A model success claim alone never creates this proof. A
structured provider receipt remains an alternative when available.

## 10. Cancellation, restart, and records

Check cancellations and amendments before model work, after user replies, and immediately
before dispatch. Amendments invalidate affected decisions and approvals. Cancellation
stops further mutations; if an external action is already in flight, allow bounded
read-only observation to report its outcome accurately.

Preserve progress for blocked tasks in the current process and keep a sanitized record
of the outstanding objective, answers, and outcome uncertainty. A user's resume starts
with fresh observation and revalidation, never replay of executed actions. Unknown
external outcomes must be reconciled before a new commitment can be prepared.

Keep task-owned tabs while waiting for the user, paused, or resumably blocked, closing
them after 24 hours without user activity or on explicit cancellation. Blocked tasks
release execution slots; an unresolved external commitment still reserves the affected
account against conflicting new work. A resumed task reacquires its slot and rereads
the site because another completed task may have changed its account state.

Record dispatch intent and its result or uncertainty in a private append-only task
journal around each mutation; record execution before post-action observation. Use
atomic/private file creation and opaque file names. This supports diagnosis and marks
an interrupted dispatch as unknown after a crash. It does not add automatic task
resumption: on restart, interrupted tasks are reported as interrupted, old approvals
cannot execute, and expired or lost secrets are requested only if a resumed task needs
them. Never infer failure from a missing post-action record.

Diagnostics include model/operation counts, latency, selected target, observation
references, question requirement IDs, validation failure reasons, recovery triggers,
and outcome evidence. Redacted rejected model responses can be retained for diagnosis;
screenshots require the same masking as model-visible images. Maintain separate counts
for completed, blocked, cancelled, failed, and outcome-unknown runs.

## 11. Verification and release acceptance

Testing measures transferable capabilities, not a product service whitelist. Reuse
existing fixtures and add regression cases for every reproduced gap. Tests never call
paid APIs and browser tests use a throwaway headless Chrome, never the user's Chrome.

Required deterministic coverage:

- One Jev operation/target selection per step, including user choices and visual
  fallback; unused heads and Python shortcuts cannot execute.
- Multi-step login, persistent login banners, redirects, slow transitions, expired
  codes, resend, and explicit rejection versus fields merely remaining visible.
- Questions reused across rewording, updated observations, and amendments; duplicate
  webhook delivery, late answers, stale buttons, and concurrent-task reply isolation.
- Explicit providers, saved defaults, and omitted-provider questions; comparison
  requires a user request/choice and gives an upfront separate-login/multiple-OTP
  notice. An explicit comparison request is not asked again. Reuse valid sessions,
  serialize provider-specific OTP questions, handle skipped or incomplete quotes,
  and require separate approval for the final commitment.
- Wrong-purpose/origin/challenge secrets rejected; temporary values absent from every
  model request, saved record, screenshot, and completed-task object.
- Text and tile CAPTCHAs, numeric text answers, and challenge replacement during a reply.
- Choices with similar labels and changed prices; selected options rebound from fresh
  observations without interpreting a shared word as consent.
- Conversation updates grounded in current evidence and deduplicated across unchanged
  observations; explicit defaults versus prior-order suggestions; combined provider/
  address questions and partial replies without reopening answered requirements.
- Promotion thresholds with strict/inclusive bounds, ineligible products, quantity
  arithmetic, membership/payment conditions, and expired discounts. A larger basket
  may reduce delivery fees while increasing total spend; never add it without a choice.
- Cart breakdowns with unfamiliar fees, included taxes, discounts, rounding, missing
  lines, changed charges, and estimated later charges; arithmetic must reconcile without
  fabricated values. Existing unrelated cart items and optional extras require a choice.
- Changed totals while the previous amount remains visible; changed recipients,
  quantities, addresses, payment methods and terms; cosmetic re-renders retaining a
  valid proposal; unknown dispatch results never retried.
- Stalls, repeated WAIT, missing elements, cross-origin frames, dialogs, and unavailable
  routes; bounded recovery without asking the user to operate the website.
- Goal-derived completion for read-only, transactional, form, upload, and download
  tasks; no false success from model assertions or irrelevant page text.
- Cancellation before dispatch, interruption after dispatch, journal recovery, provider
  failures, and invalid model responses.

Regression and metamorphic fixtures vary labels, DOM order, page structure, timing,
and URLs while preserving task meaning. No fixture-specific behavior enters prompts
or production policy. Real browser tests use deterministic model responses to check
mechanics; replayed, sanitized observations check schemas and state transitions.
Neither is presented as a live-model success benchmark.

Live evaluation is separate and explicitly launched. It uses real Jev/GPT-4.1 mini
calls and a declared set of user-approved goals spanning multiple sites and capability
families, including previously unseen goals after the implementation is fixed. Keep
the existing smoke examples as reusable checks and add user-selected holdouts; do not
limit requests to those examples. Each purchase, booking, message or submission still
waits for its own in-chat approval. A design or evaluation approval is not transaction
approval.

Before describing the milestone as complete:

1. All required repository checks and new deterministic regressions pass.
2. The declared live evaluation records every attempted run, user question, model call,
   latency, blocker, and independently checked outcome.
3. Every declared feasible acceptance goal has a verified successful run; unresolved
   product failures remain open work. External blockers require recorded evidence and
   a correct chat-only response, and are reported separately rather than counted as
   completed goals.
4. No evaluated run contains an unauthorized commitment, duplicate uncertain mutation,
   secret disclosure, false success, or unnecessary repeated question. Any occurrence
   becomes a release-blocking regression with a fresh passing evaluation after its fix.
5. No service-specific policy is introduced to make the examples pass.

Report the sample size and coverage with results. No finite evaluation establishes
that every possible website and request will succeed.

Required checks:

```sh
uv run ruff check .
uv run pytest
uv run pytest -m browser
node --check assistant/snapshot.js
node --check assistant/static/app.js
```

## 12. Handoff to implementation planning

After written-spec approval, produce a separate implementation plan with commits for
each task. Order work by dependency: structured state and evidence; coordinator;
question/challenge handling; commitment and completion verification; loop integration;
then regression and live evaluation. Preserve working browser capabilities throughout.

The plan must replace conflicting documentation and prompts, update the repository
instructions to point to this approved design, and account for existing local card-vault
data without silently using or deleting it. Cloud/WhatsApp/voice work remains separate.
Do not push changes unless the user asks.
